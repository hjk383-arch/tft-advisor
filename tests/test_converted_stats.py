"""경계면 회귀 테스트 (qa-validator, Phase 2 재검증 2026-09-22).

- 변환 산출물 data/stats/metatft_*.json ↔ contracts 0.2.0 (로드·왕복, 필드 채움, comp_id 일관성)
- 변환 산출물 ID ↔ data/static/18
- 설계 문서(§8.1 FallbackReason, 설정 키 참조) ↔ contracts/config
- meta.json 상점 확률 ↔ fixture 라벨, 7레벨 출처 충돌의 영향(Jev state 라벨 동일)

네트워크 호출 없음. 변환 산출물·설계 문서가 없으면 해당 테스트는 skip.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tft_advisor.contracts import AugmentTier, CompStats, FallbackReason, UnitItemStats, UnitStats
from tft_advisor.static_data import PROJECT_ROOT, load_static

STATS = max((PROJECT_ROOT / "data" / "stats").glob("metatft_*.json"), default=None)
DESIGN = PROJECT_ROOT / "_workspace" / "02_jev-strategist_design.md"
SCREENS = Path(__file__).parent / "fixtures" / "screens"
needs_stats = pytest.mark.skipif(STATS is None, reason="변환 산출물 data/stats/metatft_*.json 없음")
needs_design = pytest.mark.skipif(not DESIGN.is_file(), reason="설계 문서 없음")


@pytest.fixture(scope="module")
def static():
    return load_static(18)


@pytest.fixture(scope="module")
def doc():
    return json.loads(STATS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def comps(doc):
    return [CompStats.model_validate(c) for c in doc["comps"]]


# --------------------------------------------------------------------------- 로드·왕복


@needs_stats
def test_converted_file_loads_into_contracts(doc, comps):
    tiers = [AugmentTier.model_validate(t) for t in doc["augment_tiers"]]
    uis = [UnitItemStats.model_validate(r) for r in doc["unit_item_stats"]]
    us = [UnitStats.model_validate(r) for r in doc["unit_stats"]]
    assert len(comps) == doc["report"]["clusters"] == doc["report"]["with_details"]
    assert tiers and uis and us
    for c in comps:
        assert CompStats.model_validate_json(c.model_dump_json()) == c
    for m in tiers[:200] + uis[:500] + us:
        assert type(m).model_validate_json(m.model_dump_json()) == m


@needs_stats
def test_converted_fields_populated(comps):
    """설계 2.2/5.2/5.4가 읽는 필드가 57덱 모두 채워져 있다."""
    for c in comps:
        board = {u.id: u for u in c.final_board}
        assert c.carry in board, c.comp_id
        assert board[c.carry].role == "carry", c.comp_id
        assert c.carry_bis_items and board[c.carry].items == c.carry_bis_items, c.comp_id
        assert any(u.is_core for u in c.final_board), c.comp_id
        assert c.item_usage and all(v >= 0 for v in c.item_usage.values()), c.comp_id
        assert c.buildup and c.level_timing and c.item_conditional and c.key_traits, c.comp_id
        assert c.levelling, c.comp_id
        assert c.top4 is None and c.win_rate is None  # 소스에 없음(R6: top4는 early 빌드업 보드에만)
    fb = [u for c in comps for u in c.final_board]
    assert sum(u.role is None for u in fb) / len(fb) < 0.05
    assert max(v for c in comps for v in c.item_usage.values()) > 1  # 비율이 아님(덱당 평균 개수)


@needs_stats
def test_comp_id_consistent_across_collections(doc, comps):
    ids = [c.comp_id for c in comps]
    assert len(ids) == len(set(ids))
    assert {c.source_cluster_id: c.comp_id for c in comps} == doc["comp_ids"]
    tier_ids = {t["comp_id"] for t in doc["augment_tiers"] if t.get("comp_id")}
    assert tier_ids and tier_ids <= set(ids)
    uis_ids = {r.get("comp_id") for r in doc["unit_item_stats"]}
    assert None not in uis_ids and uis_ids <= set(ids)
    # 설계 6.3: 1위 덱 holder(carry)의 덱 한정 place_change 조회가 가능해야 한다
    carry_rows = {(r["comp_id"], r["unit_id"]) for r in doc["unit_item_stats"]}
    assert all((c.comp_id, c.carry) in carry_rows for c in comps)


# --------------------------------------------------------------------------- ID ↔ static


@needs_stats
def test_converted_comp_ids_resolve_in_static(comps, static):
    """CompStats 안의 챔피언/아이템/특성 ID가 모두 정적 데이터에 있다(오버레이 이름·코스트 조회)."""
    bad = set()
    for c in comps:
        for u in c.final_board:
            if static.get("champions", u.id) is None:
                bad.add(("final_board", u.id))
            bad |= {("item", i) for i in u.items if static.get("items", i) is None}
        bad |= {("bis", i) for i in c.carry_bis_items if static.get("items", i) is None}
        bad |= {("trait", t.id) for t in c.key_traits if static.get("traits", t.id) is None}
        bad |= {("item_usage", i) for i in c.item_usage if static.get("items", i) is None}
        bad |= {("item_conditional", i) for i in c.item_conditional if static.get("items", i) is None}
    assert not bad, sorted(bad)


@needs_stats
@pytest.mark.xfail(strict=True, reason="QA recheck 03: buildup 보드에 비챔피언 유닛 8건"
                   "(DA_Elderwood18_Protector, DA_TheTower_TrainingDummy, DA_TrainingDummy). "
                   "stats-researcher가 SUMMON_IDS 확장 또는 champions 존재 필터를 넣으면 XPASS -> 마커 제거")
def test_converted_buildup_units_are_champions(comps, static):
    bad = sorted({x for c in comps for bs in c.buildup.values() for b in bs for x in b.units
                  if static.get("champions", x) is None})
    assert not bad, bad


# --------------------------------------------------------------------------- 설계 ↔ 계약/설정


@needs_design
def test_design_fallback_reasons_equal_enum():
    text = DESIGN.read_text(encoding="utf-8")
    sec81 = text[text.index("### 8.1"):text.index("### 8.2")]
    design = set(re.findall(r"^\| `([a-z_]+)` \|", sec81, re.M))
    assert design == {e.value for e in FallbackReason}


@needs_design
def test_design_config_and_contract_refs_exist():
    r = subprocess.run([sys.executable, str(PROJECT_ROOT / "_workspace/qa_scripts/design_refs_check.py")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["config_refs_missing"] == []
    # GameState.item_offer는 설계 §10-1에서 보류(거부)된 제안이라 참조만 남아 있다
    assert set(out["contract_refs_missing"]) <= {"GameState.item_offer"}
    assert out["fallback_equal"] is True


# --------------------------------------------------------------------------- 상점 확률


def _odds_label(p: int) -> str:
    """설계 §4.1 shop_odds 라벨 규칙."""
    return "none" if p == 0 else "rare" if p < 10 else "uncommon" if p < 30 else "common"


def test_shop_odds_table_sums_and_matches_fixtures():
    meta = json.loads((PROJECT_ROOT / "data/static/18/meta.json").read_text(encoding="utf-8"))
    odds = meta["shop_odds_pct"]
    assert all(sum(v) == 100 for v in odds.values())
    for p in SCREENS.glob("*.expected.json"):
        e = json.loads(p.read_text(encoding="utf-8"))
        if e.get("shop_odds") and e.get("level") and "note_level" not in e:
            assert odds[str(e["level"])] == e["shop_odds"], p.name


def test_level7_odds_conflict_does_not_change_jev_labels():
    """7레벨 출처 충돌(16/30/43 vs 19/30/40)은 Jev state 라벨이 같아 추천 입력에 영향이 없다."""
    meta = json.loads((PROJECT_ROOT / "data/static/18/meta.json").read_text(encoding="utf-8"))
    chosen = meta["shop_odds_pct"]["7"]
    for alt in meta.get("shop_odds_conflicts", {}).get("7", {}).values():
        assert [_odds_label(x) for x in chosen] == [_odds_label(x) for x in alt]
