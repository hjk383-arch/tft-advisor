"""MetaTFT 변환 규칙(R1~R15, src/tft_advisor/stats/metatft_convert.py) 단위 테스트.

합성 입력만 쓴다(네트워크·원본 캐시 불필요). 정적 데이터는 data/static/18을 읽는다.
원본 캐시가 있으면 마지막 테스트가 57개 클러스터 전체 변환을 확인한다.
"""
from __future__ import annotations

import pytest

from tft_advisor.contracts import CompStats, StatSource
from tft_advisor.stats import metatft_convert as mc
from tft_advisor.stats.collectors.metatft import cluster_id_of, latest_raw_dir
from tft_advisor.static_data import load_static

PROV = {"source": StatSource.METATFT, "patch": "18.2b"}


@pytest.fixture(scope="module")
def static():
    return load_static(18)


# R1 ------------------------------------------------------------------------


def test_trait_suffix_is_tier_index_not_count(static):
    # DA_Juggernaut18 breakpoints [2,4,6]: _2 -> 4명
    out = mc.key_traits("DA_Juggernaut18_2, DA_18_Lunar_1, ", static)
    assert {(t.id, t.count) for t in out} == {("DA_Juggernaut18", 4), ("DA_18_Lunar", 2)}


def test_trait_unit_less_and_out_of_range_skipped(static):
    unmapped: set[str] = set()
    out = mc.key_traits("DA_18_Eclipse_1, DA_Juggernaut18_9, DA_NoSuchTrait_1", static, unmapped)
    assert out == []
    assert unmapped == {"DA_NoSuchTrait"}
    assert mc.trait_count({"breakpoints": [1], "unit_less": True}, 1) is None


# R2 ------------------------------------------------------------------------


def test_level_timing_skips_empty_stage_and_keeps_max_count():
    rows = [{"stage": "", "round": "", "count": 105, "level": 4},
            {"stage": "2", "round": "5", "count": 10, "level": 5},
            {"stage": "2", "round": "6", "count": 99, "level": 5},
            {"stage": "4", "round": "2", "count": 50, "level": 8.0}]
    assert mc.level_timing(rows) == {5: "2-6", 8: "4-2"}


# R4~R6 ---------------------------------------------------------------------


def test_board_keys_unit_list_vs_units_list_and_summons():
    assert mc.board_units({"unit_list": "DA_18_Zyra&DA_Elderwood18_Lifeblossom&DA_Amumu18"}) == ["DA_18_Zyra", "DA_Amumu18"]
    assert mc.board_units({"units_list": "DA_18_Zyra&DA_Amumu18"}) == ["DA_18_Zyra", "DA_Amumu18"]
    assert mc.to_buildup_board(4, {"unit_list": "DA_Elderwood18_Lifeblossom"}) is None


def test_buildup_level_keys_from_dict_key_not_float_level():
    early = {"4": [{"unit_list": "DA_18_Zyra", "level": 4.49, "count": 5, "avg": 4.1, "win": 0.6},
                   {"unit_list": "DA_Amumu18", "level": 4.51, "count": 9, "avg": 4.3, "win": 0.5}],
             "6": []}
    options = {"7": [{"units_list": "DA_Vi18", "count": 3, "avg": 5.0, "score": 1.0}],
               "8": [{"units_list": "DA_Vi18&DA_18_Zyra", "count": 7, "avg": 4.0, "score": 2.0}],
               "11": [{"units_list": "DA_Vi18", "count": 1, "avg": 1.0}]}
    b = mc.buildup(early, options)
    assert sorted(b) == [4, 7, 8]                      # 빈 6 제거, 11(계약 범위 밖) 제거, 7은 early 없어 options
    assert [x.units for x in b[4]] == [["DA_Amumu18"], ["DA_18_Zyra"]]   # count 내림차순, 배열 순서 무시
    assert b[4][0].top4 == 0.5 and b[4][0].win_rate is None             # R6: early win = top4
    assert b[8][0].top4 is None


# R7~R11 --------------------------------------------------------------------

BUILDS = [
    # 배열 앞쪽이 carry가 아니다: Amumu(탱커 3방어템)가 먼저, count도 크다
    {"unit": "DA_Amumu18", "buildName": ["DA_WarmogsArmor", "DA_GargoyleStoneplate", "DA_DragonsClaw"],
     "count": 900, "score": 0.9},
    {"unit": "DA_18_Zyra", "buildName": ["DA_InfinityEdge", "DA_SpearOfShojin", "DA_SteraksGage"],
     "count": 500, "score": 0.4},
    {"unit": "DA_18_Zyra", "buildName": ["DA_InfinityEdge", "DA_InfinityEdge", "DA_SteraksGage"],
     "count": 60, "score": 0.7},
    {"unit": "DA_18_Zyra", "buildName": ["DA_InfinityEdge", "DA_InfinityEdge", "DA_InfinityEdge"],
     "count": 10, "score": 0.99},   # 표본 < BIS_MIN_COUNT -> 제외
    {"unit": "DA_Vi18", "buildName": ["DA_SteraksGage"], "count": 300, "score": 0.5},
]
US = {
    "DA_Amumu18": {"pcnt": 0.95, "tiers": [{"tier": 2, "pcnt": 0.8}, {"tier": 1, "pcnt": 0.2}],
                   "num_items": [{"num_items": 3, "count": 5000}]},
    "DA_18_Zyra": {"pcnt": 0.99, "tiers": [{"tier": 3, "pcnt": 0.6}, {"tier": 2, "pcnt": 0.4}],
                   "num_items": [{"num_items": 3, "count": 4000}]},
    "DA_Vi18": {"pcnt": 0.5, "tiers": [], "num_items": [{"num_items": 1, "count": 3000}]},
}


def test_bis_is_score_among_min_count_not_array_order():
    assert mc.pick_bis(BUILDS, "DA_18_Zyra") == ["DA_InfinityEdge", "DA_InfinityEdge", "DA_SteraksGage"]
    assert mc.pick_bis(BUILDS, "DA_18_Zyra", min_count=10_000) == [   # 모두 미달 -> count 최대
        "DA_InfinityEdge", "DA_SpearOfShojin", "DA_SteraksGage"]
    assert mc.pick_bis(BUILDS, "DA_Vi18") == []


def test_carry_excludes_defensive_builds(static):
    units = ["DA_Amumu18", "DA_18_Zyra", "DA_Vi18"]
    assert mc.pick_carry(units, US, BUILDS, static) == "DA_18_Zyra"
    assert mc.pick_carry(units, US, BUILDS) == "DA_Amumu18"   # static 없으면 3아이템 판 수만(참고)
    assert mc.pick_carry(["DA_Vi18"], US, BUILDS, static) is None


def test_is_core_star_role(static):
    assert mc.is_core(US["DA_Amumu18"]) and not mc.is_core(US["DA_Vi18"]) and not mc.is_core(None)
    assert mc.star_of(US["DA_18_Zyra"]) == 3 and mc.star_of(US["DA_Vi18"]) is None
    tank_items = mc.representative_items(BUILDS, "DA_Amumu18")
    assert mc.unit_role("DA_Amumu18", "DA_18_Zyra", tank_items, static) == "tank"
    assert mc.unit_role("DA_18_Zyra", "DA_18_Zyra", [], static) == "carry"
    assert mc.unit_role("DA_Vi18", "DA_18_Zyra", mc.representative_items(BUILDS, "DA_Vi18"), static) == "support"
    assert mc.unit_role("DA_Vi18", "DA_18_Zyra", [], static) is None
    assert mc.item_class("DA_SteraksGage", static) == "hybrid"


# R12 -----------------------------------------------------------------------


def test_comp_id_slug_collision_and_reuse():
    clusters = {"1": {"name": [{"name": "DA_Juggernaut18"}, {"name": "DA_18_Zyra"}], "units_string": "DA_18_Zyra, DA_Amumu18"},
                "2": {"name_string": "DA_Juggernaut18, DA_18_Zyra", "units_string": "DA_Vi18, DA_Taric18"},
                "3": {"name_string": "DA_18_Lunar, DA_Nidalee18_AP", "units_string": "DA_18_Aphelios, DA_Nidalee18_AP"}}
    ids = mc.assign_comp_ids(clusters)
    assert ids == {"1": "juggernaut-zyra", "2": "juggernaut-zyra~2", "3": "lunar-nidalee_ap"}
    prev = {"old-aphelios": ["DA_18_Aphelios", "DA_Nidalee18_AP"]}
    assert mc.assign_comp_ids(clusters, prev)["3"] == "old-aphelios"


def test_cluster_id_of():
    assert cluster_id_of("424017", None) == "424"
    assert cluster_id_of("424017", {"cluster_info": {"cluster_id": 425}}) == "425"


# R13 -----------------------------------------------------------------------


def test_comp_augment_tiers_acceptance(static):
    board = ["DA_18_Zyra", "DA_Amumu18"]
    ok = {"distance": 0.3, "source_title": "ZYRA > Juggernaut > Lvl 8 push"}
    assert mc.accept_comp_augment_tiers(ok, board, static) == (True, "ok")
    assert not mc.accept_comp_augment_tiers({**ok, "distance": 0.51}, board, static)[0]
    assert not mc.accept_comp_augment_tiers({**ok, "source_title": "AHRI > x"}, board, static)[0]
    assert not mc.accept_comp_augment_tiers({**ok, "source_title": "??? > x"}, board, static)[0]


# R3 / R15 / R14 --------------------------------------------------------------


def test_unit_item_rows_missing_units_key_and_comp_scope():
    item_names = [{"itemNames": "DA_InfinityEdge", "count": 10, "avg": 4.0,
                   "units": [{"units": "DA_18_Zyra", "count": 5, "avg": 3.9, "place_change": -0.1}]},
                  {"itemNames": "DA_SteraksGage", "count": 3, "avg": 4.5}]   # units 키 없음
    rows = mc.unit_item_rows(item_names, PROV, "juggernaut-zyra", BUILDS[:1] + [{"unit": "DA_Vi18", "buildName": []}])
    assert [(r.unit_id, r.item_ids) for r in rows] == [
        ("DA_18_Zyra", ["DA_InfinityEdge"]),
        ("DA_Amumu18", ["DA_WarmogsArmor", "DA_GargoyleStoneplate", "DA_DragonsClaw"])]
    assert all(r.comp_id == "juggernaut-zyra" for r in rows)


def test_item_conditional_records_unmapped(static):
    un: set[str] = set()
    out = mc.item_conditional([{"itemNames": "DA_InfinityEdge", "count": 10, "avg": 4.0},
                               {"itemNames": "DA_Artifact_Hullcrusher", "count": 1, "avg": 5.0}], static, un)
    assert list(out) == ["DA_InfinityEdge"] and un == {"DA_Artifact_Hullcrusher"}


def test_item_usage_allows_above_one():
    assert mc.item_usage({"DA_InfinityEdge": {"pcnt": 1.38}, "DA_SteraksGage": {}}) == {
        "DA_InfinityEdge": 1.38, "DA_SteraksGage": 0.0}


def test_rank_filter_is_a_set():
    a, b = "CHALLENGER,GRANDMASTER,MASTER,DIAMOND", "challenger, DIAMOND,GRANDMASTER,MASTER"
    assert mc.same_rank_filter(a, b)
    assert mc.normalize_rank_filter(a) == "CHALLENGER,DIAMOND,GRANDMASTER,MASTER"
    assert mc.normalize_rank_filter("") is None and not mc.same_rank_filter(a, "MASTER")


def test_placement_from_places():
    p = mc.placement_from_places([1, 1, 0, 0, 0, 0, 0, 2])
    assert (p.games, p.avg_place, p.top4, p.win_rate) == (4, 4.75, 0.5, 0.25)


# 조립 ----------------------------------------------------------------------


def test_convert_comp_fills_contract_fields(static):
    cd = {"units_string": "DA_Amumu18, DA_18_Zyra, DA_Vi18, ", "traits_string": "DA_Juggernaut18_2, ",
          "name": [{"name": "DA_Juggernaut18"}, {"name": "DA_18_Zyra"}], "levelling": "Fast 8",
          "overall": {"count": 1234, "avg": 4.2}, "build_items": {"DA_InfinityEdge": {"pcnt": 1.2}}}
    det = {"unit_stats": [{"unit": k, **v} for k, v in US.items()], "builds": BUILDS,
           "levels": [{"stage": "4", "round": "2", "level": 8, "count": 1}],
           "early_options": {}, "options": {}, "itemNames": []}
    comp, extra = mc.convert_comp("424000", cd, det, static, "juggernaut-zyra", PROV)
    assert isinstance(comp, CompStats)
    assert comp.carry == "DA_18_Zyra" and comp.levelling == "Fast 8" and comp.item_usage == {"DA_InfinityEdge": 1.2}
    assert {u.id: u.role for u in comp.final_board} == {"DA_Amumu18": "tank", "DA_18_Zyra": "carry", "DA_Vi18": "support"}
    assert all(r.comp_id == "juggernaut-zyra" for r in extra["unit_item"])


@pytest.mark.skipif(latest_raw_dir() is None, reason="MetaTFT 원본 캐시 없음")
def test_build_all_on_cached_snapshot(static):
    res = mc.build_all(latest_raw_dir(), static)
    r = res["report"]
    assert r["clusters"] == r["with_details"] and not r["missing_details"]
    assert r["carry_none"] == []
    assert len(set(res["comp_ids"].values())) == r["clusters"]
    assert all(c.carry in {u.id for u in c.final_board} for c in res["comps"])
