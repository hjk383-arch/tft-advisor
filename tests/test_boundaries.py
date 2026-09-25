"""경계면 회귀 테스트 (qa-validator, Phase 2).

- static_data ID <-> MetaTFT 캐시 ID (unmapped.json 선언분 제외)
- fixture 이름 -> ID -> 정적 코스트
- MetaTFT 원본 -> 계약 모델(CompStats/AugmentTier/UnitStats/UnitItemStats) 변환 가능성
- 설계(02_jev-strategist_design.md §10a) 최종 설정 키의 config 수용 여부

네트워크 호출 없음. 원본 캐시(data/raw/metatft/{최신 날짜})가 없으면 해당 테스트는 skip.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from tft_advisor.config import load_settings
from tft_advisor.contracts import (
    BUILDUP_LEVELS,
    AugmentTier,
    BuildupBoard,
    CompStats,
    CompUnit,
    PlacementStats,
    StatSource,
    TraitReq,
    UnitItemStats,
    UnitStats,
)
from tft_advisor.fixtures import load_expected
from tft_advisor.static_data import PROJECT_ROOT, load_static

RAW = max((PROJECT_ROOT / "data" / "raw" / "metatft").glob("????-??-??"), default=PROJECT_ROOT / "data" / "raw" / "metatft" / "none")  # 최신 날짜 캐시
SCREENS = Path(__file__).parent / "fixtures" / "screens"
needs_raw = pytest.mark.skipif(not (RAW / "comps_data.json").is_file(), reason="MetaTFT 원본 캐시 없음")


def _raw(name: str):
    return json.loads((RAW / name).read_text(encoding="utf-8"))


def _trait_base(t: str) -> str:
    return re.sub(r"_\d+$", "", t.strip())


@pytest.fixture(scope="module")
def static():
    return load_static(18)


@pytest.fixture(scope="module")
def declared_unmapped() -> set[str]:
    u = json.loads((PROJECT_ROOT / "data/static/18/unmapped.json").read_text(encoding="utf-8"))
    return (set(u["units"]["metatft"]) | set(u["units"]["summons_in_boards"])
            | set(u["items"]["tactics_tools"]) | set(u["augments"]["metatft_augments_tiers"]))


# --------------------------------------------------------------------------- ID 체계


@needs_raw
def test_metatft_ids_exist_in_static(static, declared_unmapped):
    comps = _raw("comps_data.json")["results"]["data"]["cluster_details"]
    missing = set()
    for c in comps.values():
        for u in c["units_string"].split(","):
            if not static.get("champions", u.strip()):
                missing.add(u.strip())
        for t in c["traits_string"].split(","):
            if not static.get("traits", _trait_base(t)):
                missing.add(t.strip())
        for b in c["builds"]:
            if not static.get("champions", b["unit"]):
                missing.add(b["unit"])
            missing |= {i for i in b["buildName"] if not static.get("items", i)}
        missing |= {i for i in c["build_items"] if not static.get("items", i)}
    for r in _raw("units.json")["results"]:
        if not static.get("champions", r["unit"]):
            missing.add(r["unit"])
    tiers = _raw("augments_tiers.json")["content"]["content"]["tierList"]
    missing |= {x["id"] for t in tiers for x in t["content"] if not static.get("augments", x["id"])}
    cat = _raw("comp_augment_tiers.json")["results"]
    missing |= {a["id"] for v in cat.values() for a in v["augments"] if not static.get("augments", a["id"])}
    assert missing <= declared_unmapped, f"unmapped.json에 선언되지 않은 미매핑 ID: {sorted(missing - declared_unmapped)}"


@needs_raw
def test_metatft_champions_are_shop_pool(static):
    comps = _raw("comps_data.json")["results"]["data"]["cluster_details"]
    ids = {u.strip() for c in comps.values() for u in c["units_string"].split(",")}
    assert all(static.get("champions", i)["shop_pool"] for i in ids)


@needs_raw
def test_metatft_trait_suffix_maps_to_breakpoint(static):
    """MetaTFT `_N` 접미사는 인원이 아니라 구간 번호 -> TraitReq.count = breakpoints[N-1] 변환이 가능해야 한다."""
    comps = _raw("comps_data.json")["results"]["data"]["cluster_details"]
    for c in comps.values():
        for t in c["traits_string"].split(","):
            base, n = re.match(r"(.+)_(\d+)$", t.strip()).groups()
            bp = static.get("traits", base)["breakpoints"]
            assert int(n) <= len(bp), (t, bp)


def test_trait_breakpoints_have_no_null(static):
    bad = [t["apiName"] for t in static.tables["traits"] if any(b is None for b in t["breakpoints"])]
    assert not bad, bad


def test_static_name_uniqueness_for_jev_english_names(static):
    """jev-strategist 설계 0절 가정: 상점 풀 챔피언 name_en/name_ko 중복 없음, 완성/상징 name_en 중복은 1건."""
    pool = [r for r in static.tables["champions"] if r.get("shop_pool")]
    assert len({r["name_en"] for r in pool}) == len(pool)
    assert len({r["name_ko"] for r in pool}) == len(pool)
    names = [r["name_en"] for r in static.tables["items"]
             if r["category"] in ("completed", "emblem") and r["apiName"].startswith("DA_")]
    dups = [k for k, v in Counter(names).items() if v > 1]
    assert dups == ["Flora Fatalis Emblem"]


@pytest.mark.parametrize("path", sorted(SCREENS.glob("*.expected.json")), ids=lambda p: p.name)
def test_fixture_shop_ids_and_costs(path, static):
    raw = json.loads(path.read_text(encoding="utf-8"))
    st = load_expected(path).state
    for slot, rs in zip(st.shop or [], raw.get("shop") or []):
        if slot.kind == "champion":
            rec = static.get("champions", slot.id)
            assert rec["shop_pool"]
            assert rec["cost"] == rs["cost"], f"{rs['name']}: 정적 코스트 {rec['cost']} != 라벨 {rs['cost']}"
        elif slot.kind == "special":
            assert static.get("shop_specials", slot.id)
    for a in st.augment_offer or []:
        assert static.get("augments", a.id)["set_native"]


# --------------------------------------------------------------------------- MetaTFT -> 계약


@needs_raw
def test_rank_filter_same_set_as_source():
    """settings.rank_filter와 MetaTFT 응답 rank_filter는 순서가 다르다 -> 문자열이 아닌 집합으로 비교해야 한다."""
    ours = load_settings().stats.rank_filter
    theirs = _raw("units.json")["filter_adjustment"]["rank_filter"]
    assert set(ours.split(",")) == set(theirs.split(","))


@needs_raw
def test_comp_details_convertible_to_compstats(static):
    """comp_details 424001 원본으로 CompStats를 만들 수 있다(최소 변환기). 소스 필드 <-> 계약 필드 경계 확인."""
    cd = _raw("comps_data.json")["results"]["data"]["cluster_details"]["424001"]
    det = _raw("comp_details_424001.json")["results"]
    summons = {"DA_Elderwood18_Lifeblossom", "DA_Elderwood18_StonebarkTree"}

    buildup: dict[int, list[BuildupBoard]] = {}
    for key, boards in {**det["early_options"], **det["options"]}.items():
        lv = int(key)
        assert lv in BUILDUP_LEVELS
        buildup[lv] = [
            BuildupBoard(
                level=lv,
                units=[u for u in (b.get("unit_list") or b["units_list"]).split("&") if u not in summons],
                avg_place=b["avg"], games=b["count"], win_rate=b.get("win"),
            )
            for b in boards
        ]
    # levels: 첫 행(level 4)은 stage/round가 빈 문자열 -> 건너뛰어야 Stage 패턴 통과
    level_timing = {l["level"]: f"{l['stage']}-{l['round']}" for l in det["levels"] if l["stage"] and l["round"]}
    assert any(not l["stage"] for l in det["levels"]), "원본 구조 변경: 빈 stage 행이 사라짐(변환기 조건 재확인)"
    item_cond = {i["itemNames"]: PlacementStats(avg_place=i["avg"], games=i["count"]) for i in det["itemNames"]
                 if static.get("items", i["itemNames"])}
    builds = {}
    for b in cd["builds"]:
        builds.setdefault(b["unit"], b["buildName"])
    carry = cd["builds"][0]["unit"]
    key_traits = []
    for t in cd["traits_string"].split(","):
        base, n = re.match(r"(.+)_(\d+)$", t.strip()).groups()
        bp = static.get("traits", base)["breakpoints"]
        if bp[int(n) - 1] is not None:
            key_traits.append(TraitReq(id=base, count=bp[int(n) - 1]))
    comp = CompStats(
        comp_id="lunar_aphelios", name="루나 아펠리오스", source=StatSource.METATFT, patch="18.2b",
        source_cluster_id="424001",
        final_board=[CompUnit(id=u.strip(), items=builds.get(u.strip(), [])[:3]) for u in cd["units_string"].split(",")],
        carry=carry, carry_bis_items=builds[carry][:3], key_traits=key_traits,
        buildup=buildup, level_timing=level_timing, item_conditional=item_cond,
        levelling=cd["levelling"], avg_place=cd["overall"]["avg"], games=cd["overall"]["count"],
    )
    assert CompStats.model_validate_json(comp.model_dump_json()) == comp
    assert comp.top4 is None  # MetaTFT comps/comp_details에는 top4/win_rate가 없다(정상: Optional)


@needs_raw
def test_augment_tiers_convertible(static):
    tiers = _raw("augments_tiers.json")["content"]["content"]["tierList"]
    rows = [AugmentTier(augment_id=x["id"], tier=t["label"], source_kind="editorial", source=StatSource.METATFT)
            for t in tiers for x in t["content"]]
    assert len(rows) == 258
    cat = _raw("comp_augment_tiers.json")["results"]
    rows2 = [AugmentTier(augment_id=a["id"], tier=a["tier"], source_kind="editorial", source=StatSource.METATFT,
                         comp_id=cid, source_title=v["source_title"])
             for cid, v in cat.items() for a in v["augments"]]
    assert rows2


@needs_raw
def test_unit_and_unit_item_stats_convertible():
    for r in _raw("units.json")["results"]:
        p = r["places"]
        g = sum(p)
        UnitStats(unit_id=r["unit"], source=StatSource.METATFT, games=g,
                  avg_place=sum((i + 1) * n for i, n in enumerate(p)) / g, top4=sum(p[:4]) / g, win_rate=p[0] / g)
    det = _raw("comp_details_424001.json")["results"]
    n_without_units = 0
    for it in det["itemNames"]:
        if "units" not in it:  # 11/137 행에 units 키가 없다 -> 수집기는 .get 처리 필요
            n_without_units += 1
            continue
        for u in it["units"]:
            UnitItemStats(unit_id=u["units"], item_ids=[it["itemNames"]], source=StatSource.METATFT,
                          place_change=u["place_change"], avg_place=u["avg"], games=u["count"])
    assert n_without_units > 0


def test_unit_item_stats_has_comp_scope():
    """MetaTFT itemNames/builds는 덱 한정 통계 -> UnitItemStats.comp_id (None = 전체 통계)."""
    assert UnitItemStats.model_fields["comp_id"].default is None
    row = UnitItemStats(unit_id="DA_18_Zyra", item_ids=["DA_ArchangelsStaff"], source=StatSource.METATFT,
                        comp_id="lunar_aphelios", place_change=-0.4, games=120)
    assert UnitItemStats.model_validate_json(row.model_dump_json()).comp_id == "lunar_aphelios"


# --------------------------------------------------------------------------- 설계 <-> config


def test_config_accepts_design_keys():
    """설계 §10a 최종 설정 키 표(기존+신규)를 로더가 받고, config/*.toml 값이 §10a 기본값과 같다."""
    import os
    import subprocess
    import sys

    # 자식 프로세스 출력 인코딩을 고정한다: PYTHONIOENCODING이 없으면 Windows 콘솔 코드페이지(cp949)로 출력해
    # UTF-8 디코드가 깨졌다(환경 문제, 27 QA 게이트).
    r = subprocess.run([sys.executable, str(PROJECT_ROOT / "_workspace/qa_scripts/config_proposal_check.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    assert "rejected keys: 0" in r.stdout, r.stdout
    assert "config files differ from §10a defaults: 0" in r.stdout, r.stdout
    # 스크립트와 별개로 §10a 신규 섹션·키가 모델에 있는지 직접 확인
    from tft_advisor.config import AdvisorCfg, Weights

    for sec in ("prefilter", "item_fit", "item"):
        assert sec in Weights.model_fields
    for k in ("jev_model", "jev_timeout_s", "jev_retry_budget_s", "jev_max_retries", "circuit_fail_threshold",
              "circuit_cooldown_s"):
        assert k in AdvisorCfg.model_fields
