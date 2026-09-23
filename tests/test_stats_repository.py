"""stats 조회 API(repository) + SQLite 적재 테스트. 네트워크 없음: data/raw/metatft 최신 캐시를 변환해 tmp DB에 쓴다."""
from __future__ import annotations

import json
import time

import pytest

from tft_advisor.contracts import AugmentTier, CompStats, PlacementStats, UnitItemStats, UnitStats
from tft_advisor.static_data import load_static
from tft_advisor.stats import db as statsdb
from tft_advisor.stats import refresh as rf
from tft_advisor.stats.collectors.metatft import latest_raw_dir
from tft_advisor.stats.repository import (
    InMemoryStatsRepository,
    StatsNotFound,
    StatsRepository,
    open_repository,
)

RAW = latest_raw_dir()
pytestmark = pytest.mark.skipif(RAW is None, reason="MetaTFT 원본 캐시 없음(data/raw/metatft)")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("stats")
    r = rf.refresh(fetch=False, raw_dir=RAW, db_path=d / "stats.sqlite", json_dir=d, diff_out=None)
    return r


@pytest.fixture(scope="module")
def repo(built):
    return open_repository(db_path=built.db_path, allow_json_fallback=False)


@pytest.fixture(scope="module")
def doc(built):
    return json.loads(built.json_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- 로드·메타


def test_open_from_sqlite_meta(repo, built):
    assert isinstance(repo, StatsRepository)
    m = repo.meta
    assert m.snapshot_id == built.snapshot_id and m.patch == built.report["patch"]
    assert m.rank_filter_units == "CHALLENGER,DIAMOND,GRANDMASTER,MASTER" and m.rank_filter_comps is None
    assert m.counts["comps"] == built.report["clusters"] == 57
    assert m.counts["unit_item_stats"] > 0 and m.counts["unit_build_stats"] > 0 and m.counts["item_stats"] > 0


def test_sqlite_roundtrip_equals_json(built, doc):
    back = statsdb.read_snapshot(built.db_path, built.snapshot_id)
    assert sorted(back["comps"], key=lambda c: c["comp_id"]) == sorted(doc["comps"], key=lambda c: c["comp_id"])
    for k in ("augment_tiers", "unit_stats", "unit_item_stats", "unit_build_stats"):
        assert len(back[k]) == len(doc[k]), k
    assert back["comp_ids"] == doc["comp_ids"]


def test_load_is_fast(built):
    t = time.perf_counter()
    open_repository(db_path=built.db_path, allow_json_fallback=False)
    assert time.perf_counter() - t < 5.0


def test_json_fallback_and_not_found(built, tmp_path):
    r = open_repository(db_path=built.json_path.parent / "missing.sqlite")   # 같은 폴더의 metatft_*.json
    assert len(r.comps()) == 57 and r.meta.snapshot_id is None
    with pytest.raises(StatsNotFound):
        open_repository(db_path=tmp_path / "none.sqlite", allow_json_fallback=False)


def test_from_doc_minimal_for_fakes(doc):
    """advisor 테스트용: comps만 있는 dict로도 저장소를 만들 수 있다."""
    r = InMemoryStatsRepository.from_doc({"comps": doc["comps"][:3]})
    assert len(r.comps()) == 3 and r.augment_tiers() == {} and r.unit_item_stat("DA_18_Zyra", "DA_ArchangelsStaff") is None
    assert r.shop_odds(4) == [55, 30, 15, 0, 0]


# --------------------------------------------------------------------------- 덱


def test_comps_sorted_and_min_games(repo):
    cs = repo.comps()
    assert all(isinstance(c, CompStats) for c in cs)
    avg = [c.avg_place for c in cs]
    assert avg == sorted(avg)
    small = [c for c in cs if (c.games or 0) < 1000]
    assert len(repo.comps(min_games=1000)) == len(cs) - len(small)


def test_comp_lookup_by_id_and_cluster(repo, doc):
    for cluster, cid in doc["comp_ids"].items():
        assert repo.comp_by_cluster(cluster) is repo.comp(cid)
    assert repo.comp("nope") is None and repo.comp_by_cluster("0") is None


def test_buildup_boards_only_shop_champions(repo):
    """QA recheck 03 N6(WARN 2): 나무정령 수호자·허수아비 제거."""
    bad = {u for c in repo.comps() for bs in c.buildup.values() for b in bs for u in b.units if not repo.is_champion(u)}
    assert not bad, bad
    for x in ("DA_Elderwood18_Protector", "DA_TheTower_TrainingDummy", "DA_TrainingDummy", "DA_Elderwood18_Lifeblossom"):
        assert not repo.is_champion(x)


# --------------------------------------------------------------------------- 증강 등급


def test_augment_tier_scopes(repo):
    overall = repo.augment_tiers()
    assert len(overall) == 258 and all(isinstance(t, AugmentTier) and t.comp_id is None for t in overall.values())
    scoped = repo.comps_with_augment_tiers()
    assert len(scoped) == 32 and scoped <= {c.comp_id for c in repo.comps()}
    cid = sorted(scoped)[0]
    per = repo.augment_tiers(cid)
    aid, t = next(iter(per.items()))
    assert repo.augment_tier(aid, cid) is t and t.comp_id == cid
    # 덱별 등급에 없는 증강 → augment_tier(exact)는 None, augment_tier_for는 전체 등급으로 폴백
    only_overall = next(a for a in overall if a not in per)
    assert repo.augment_tier(only_overall, cid) is None
    assert repo.augment_tier_for(only_overall, cid) is overall[only_overall]
    assert repo.augment_tier_for(aid, cid) is t
    assert repo.augment_tier_for("DA_NotAnAugment", cid) is None


# --------------------------------------------------------------------------- 유닛 / 유닛+아이템 / 아이템


def test_unit_stats(repo, built):
    """데이터 독립 불변식(18.2b 69개 → 18.3 65개로 바뀌어 고정 개수를 뺐다): 원본 units.json 행 수와 같고,
    모든 unit_id는 정적 챔피언이거나 report.unmapped_ids에 기록돼 있다."""
    us = repo.all_unit_stats()
    raw_units = {r["unit"] for r in json.loads((RAW / "units.json").read_text(encoding="utf-8"))["results"]}
    assert set(us) == raw_units and len(us) >= 50
    assert all(isinstance(u, UnitStats) and u.games for u in us.values())
    static = load_static(18)
    unknown = {u for u in us if static.get("champions", u) is None}
    assert unknown <= set(built.report["unmapped_ids"]), unknown
    assert repo.unit_stats("DA_18_Zyra") is us["DA_18_Zyra"]


def test_unit_item_comp_scoped_for_every_carry(repo):
    """설계 §7 holder place_change: 모든 덱의 carry × BIS 아이템 덱 한정 행이 있다."""
    missing = []
    for c in repo.comps():
        rows = repo.unit_item_stats(c.carry, c.comp_id)
        assert rows and all(isinstance(r, UnitItemStats) and r.comp_id == c.comp_id for r in rows)
        for item in set(c.carry_bis_items):
            if repo.unit_item_stat(c.carry, item, c.comp_id) is None:
                missing.append((c.comp_id, item))
    assert not missing, missing


def test_unit_item_overall_is_games_weighted(repo, doc):
    unit, item = "DA_18_Zyra", "DA_ArchangelsStaff"
    rows = [r for r in doc["unit_item_stats"] if r["unit_id"] == unit and r["item_ids"] == [item]]
    best = {}
    for r in rows:   # 같은 (덱) 중복이 있으면 games 최대 행
        if r["comp_id"] not in best or r["games"] > best[r["comp_id"]]["games"]:
            best[r["comp_id"]] = r
    o = repo.unit_item_stat(unit, item)
    assert o.comp_id is None and o.games == sum(r["games"] for r in best.values())
    w = sum(r["games"] * r["place_change"] for r in best.values()) / o.games
    assert o.place_change == pytest.approx(w, abs=1e-3)
    # fallback_overall
    other = next(c.comp_id for c in repo.comps() if repo.unit_item_stat(unit, item, c.comp_id) is None)
    assert repo.unit_item_stat(unit, item, other) is None
    assert repo.unit_item_stat(unit, item, other, fallback_overall=True) is o


def test_unit_builds_exact(repo):
    c = repo.comp_by_cluster("424000")
    builds = repo.unit_builds(c.carry, c.comp_id)
    assert builds and [b.games for b in builds] == sorted((b.games for b in builds), reverse=True)
    assert any(b.item_ids == c.carry_bis_items for b in builds)
    assert repo.unit_builds(c.carry, "nope") == []


def test_item_stats(repo):
    p = repo.item_stats("DA_ArchangelsStaff")
    assert isinstance(p, PlacementStats) and p.games and 1 <= p.avg_place <= 8 and 0 <= p.top4 <= 1
    assert repo.item_stats("DA_Nope") is None


# --------------------------------------------------------------------------- 정적 데이터


def test_item_categories_and_craftability(repo):
    """QA WARN 1: 유물·찬란한 BIS는 데이터에 남기고 저장소가 조합 불가로 알려준다."""
    assert repo.item_category("DA_Artifact_Dawncore") == "artifact" and not repo.is_craftable("DA_Artifact_Dawncore")
    assert repo.item_category("DA_ArchangelsStaffRadiant") == "radiant" and not repo.is_craftable("DA_ArchangelsStaffRadiant")
    assert repo.item_category("DA_Artifact_Hullcrusher") is None and not repo.is_craftable("DA_Artifact_Hullcrusher")
    assert repo.is_craftable("DA_ArchangelsStaff")
    a, b = repo.recipe("DA_ArchangelsStaff")
    assert repo.is_component(a) and repo.is_component(b)
    assert repo.craft(a, b) == repo.craft(b, a) == "DA_ArchangelsStaff"
    non_craft = {i for c in repo.comps() for i in c.carry_bis_items if not repo.is_craftable(i)}
    assert non_craft and all(repo.item_category(i) in {"artifact", "radiant", "emblem", None} for i in non_craft)


def test_recipe_table_roundtrip(repo):
    static = load_static(18)
    comps = repo.components()
    assert len(comps) == 10 and all(c.startswith("DA_Component_") for c in comps)
    n = 0
    for r in static.tables["items"]:
        if repo.is_craftable(r["apiName"]) and r["apiName"].startswith("DA_") and r.get("set_native"):
            assert repo.craft(*repo.recipe(r["apiName"])) == r["apiName"]
            n += 1
    assert n >= 50   # 완성템 36 + 상징 16 + 전략가 3
    assert repo.craft("DA_Component_Spatula", "DA_Component_Spatula") == "DA_TacticiansCrown"
    # QA 04 W4: recipe/is_craftable은 craft와 같은 범위(DA_*·set_native). 레거시 TFT_Item_*는 조합 불가
    assert repo.recipe("TFT_Item_ArchangelsStaff") is None and not repo.is_craftable("TFT_Item_ArchangelsStaff")
    for r in static.tables["items"]:
        if repo.recipe(r["apiName"]) is not None:
            assert r["apiName"].startswith("DA_") and r.get("set_native")
            assert repo.craft(*repo.recipe(r["apiName"])) == r["apiName"]


def test_emblem_traits(repo):
    static = load_static(18)
    emblems = [r["apiName"] for r in static.tables["items"] if r["category"] == "emblem"]
    assert all(repo.emblem_trait(e) and repo.trait(repo.emblem_trait(e)) for e in emblems)
    assert repo.emblem_trait("DA_18_EmblemSlayer") == "DA_18_Slayer"   # 표시 이름 Ravager
    assert repo.emblem_trait("DA_ArchangelsStaff") is None


def test_shop_odds(repo):
    for lv in range(1, 11):
        o = repo.shop_odds(lv)
        assert len(o) == 5 and sum(o) == 100
    assert repo.shop_odds(7) == [16, 30, 43, 10, 1]
    with pytest.raises(ValueError):
        repo.shop_odds(11)


def test_static_lookups(repo):
    assert repo.champion_cost("DA_18_Zyra") and repo.champion_traits("DA_18_Zyra")
    assert repo.champion_cost("nope") is None and repo.champion_traits("nope") == []
    c = repo.comp_by_cluster("424000")
    for u in c.final_board:
        assert repo.is_champion(u.id) and repo.champion_cost(u.id) >= 1 and repo.champion_traits(u.id)
    for t in c.key_traits:
        assert repo.trait_breakpoints(t.id)
    assert repo.name("DA_18_Zyra", "en") and repo.name("DA_18_Zyra") and repo.name("nope") is None
    assert repo.augment("DA_PandorasBench") and repo.augment_traits("DA_PandorasBench") == []
    assert any(repo.augment_traits(a["apiName"]) for a in load_static(18).tables["augments"])
    assert repo.shop_special("DA_ThreeMe18") is not None
