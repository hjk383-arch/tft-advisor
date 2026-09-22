"""contracts 정합성 테스트.

1) 스크린샷 정답 파일(expected.json) 7개가 GameState 필드명의 부분집합이고 GameState로 검증된다.
2) 계약 모델이 JSON 왕복 직렬화에서 값이 보존된다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from tft_advisor.config import load_settings, load_weights
from tft_advisor.contracts import (
    GAME_STATE_FIELDS,
    AugmentAdvice,
    AugmentChoice,
    AugmentRef,
    AugmentTier,
    BuildupBoard,
    CompStats,
    CompUnit,
    FallbackReason,
    GameState,
    ItemAdvice,
    ItemReadiness,
    ItemRef,
    ItemState,
    ItemSuggestion,
    ReasonTag,
    Recommendation,
    ScreenMode,
    ShopAdvice,
    ShopSlot,
    ShopSlotKind,
    StatSource,
    TargetComp,
    TraitReq,
    UnitItemStats,
    UnitOnBoard,
    UnitStats,
)
from tft_advisor.fixtures import load_expected
from tft_advisor.static_data import load_static

SCREENS = Path(__file__).parent / "fixtures" / "screens"
EXPECTED = sorted(SCREENS.glob("*.expected.json"))


def test_fixture_count():
    assert len(EXPECTED) == 7


@pytest.mark.parametrize("path", EXPECTED, ids=lambda p: p.name)
def test_expected_json_is_game_state_subset(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    keys = {k for k in raw if not k.startswith(("_", "note"))}
    assert keys <= set(GAME_STATE_FIELDS), f"GameState에 없는 필드: {keys - set(GAME_STATE_FIELDS)}"

    exp = load_expected(path)
    st = exp.state
    assert exp.fields == keys
    assert st.screen_mode in ScreenMode
    if st.shop is not None:
        assert len(st.shop) == 5
        for slot, raw_slot in zip(st.shop, raw["shop"]):
            if raw_slot is None:
                assert slot.kind is ShopSlotKind.EMPTY
            else:
                assert slot.id is not None and slot.cost == raw_slot["cost"]
    if st.augment_offer is not None:
        assert all(a.id.startswith("DA_") for a in st.augment_offer)
    # 정답 파일은 직렬화 왕복에도 동일해야 한다
    assert GameState.model_validate_json(st.model_dump_json()) == st


def test_fixture_name_to_id_mapping():
    by_name = {p.name: load_expected(p).state for p in EXPECTED}
    s33 = by_name["라운드 3-3.expected.json"]
    assert s33.shop[1].id == "DA_Nidalee18_AP"
    assert s33.shop[4].kind is ShopSlotKind.SPECIAL and s33.shop[4].id == "DA_ThreeMe18"
    assert by_name["라운드 3-5.expected.json"].shop[3].id == "DA_Scuttlecrab18"  # 크립 동명 배제
    aug = [a.id for a in by_name["라운드 2-1 증강선택.expected.json"].augment_offer]
    assert aug == ["DA_SeraphimsStaff", "DA_Warpath", "DA_Hustler"]


def _sample_state() -> GameState:
    return GameState(
        screen_mode=ScreenMode.PLANNING,
        stage="3-2",
        level=6,
        xp=(4, 36),
        gold=34,
        streak=-2,
        hp=62,
        shop_odds=[30, 40, 25, 5, 0],
        shop=[
            ShopSlot(kind=ShopSlotKind.CHAMPION, id="DA_18_Zyra", name_ko="자이라", cost=3, confidence=0.93),
            ShopSlot(kind=ShopSlotKind.SPECIAL, id="DA_ThreeMe18", cost=9),
            ShopSlot(kind=ShopSlotKind.EMPTY),
            ShopSlot(kind=ShopSlotKind.UNKNOWN, confidence=0.2),
            ShopSlot(kind=ShopSlotKind.CHAMPION, id="DA_18_Sentry", cost=1),
        ],
        board=[UnitOnBoard(id="DA_18_Zyra", star=2, items=["DA_ArchangelsStaff"], hex=(0, 3))],
        bench=[UnitOnBoard(id="DA_18_Sentry", star=1, bench_slot=0)],
        items=ItemState(
            components=[ItemRef(id="DA_Component_FryingPan", category="component")],
            completed=[ItemRef(id="DA_ArchangelsStaff", category="completed")],
        ),
        augments_owned=[AugmentRef(id="DA_Hustler", rarity=2, picked_stage="2-1")],
        confidence={"gold": 0.99, "board": 0.3},
        set_number=18,
        captured_at=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )


def test_game_state_roundtrip_and_confidence():
    st = _sample_state()
    assert GameState.model_validate_json(st.model_dump_json()) == st
    assert st.confidence_of("gold") == 0.99
    assert st.confidence_of("level") == 1.0      # 값 있음 + 미기재 → 1.0
    assert st.confidence_of("active_traits") == 0.0  # 값 없음
    assert not st.is_reliable("board", 0.6)
    assert st.items.all_ids() == ["DA_Component_FryingPan", "DA_ArchangelsStaff"]


def test_stats_roundtrip():
    comp = CompStats(
        comp_id="zyra_juggernaut",
        name="자이라 거대괴수",
        source=StatSource.METATFT,
        patch="18.2b",
        final_board=[CompUnit(id="DA_18_Zyra", items=["DA_ArchangelsStaff"], is_core=True, role="carry")],
        carry="DA_18_Zyra",
        carry_bis_items=["DA_ArchangelsStaff"],
        key_traits=[TraitReq(id="DA_18_Elderwood", count=5)],
        buildup={5: [BuildupBoard(level=5, units=["DA_18_Zyra", "DA_18_Sentry"], avg_place=4.2, games=900)]},
        level_timing={5: "2-5", 8: "4-2"},
        item_usage={"DA_ArchangelsStaff": 1.38},  # 덱당 평균 개수: 1 초과 허용
        avg_place=4.1,
        top4=0.55,
        games=12000,
    )
    back = CompStats.model_validate_json(comp.model_dump_json())
    assert back == comp and 5 in back.buildup  # JSON 문자열 키 "5" → int 복원
    with pytest.raises(ValidationError):
        CompStats.model_validate({**comp.model_dump(), "buildup": {3: []}})

    tier = AugmentTier(augment_id="DA_Hustler", tier="A", source_kind="editorial", source=StatSource.METATFT)
    assert tier.games is None
    for m in (
        tier,
        UnitStats(unit_id="DA_18_Zyra", source=StatSource.TACTICS_TOOLS, avg_place=4.3, games=5000),
        UnitItemStats(unit_id="DA_18_Zyra", item_ids=["DA_ArchangelsStaff"], source=StatSource.METATFT, place_change=-0.3),
        UnitItemStats(unit_id="DA_18_Zyra", item_ids=["DA_ArchangelsStaff"], source=StatSource.METATFT,
                      comp_id="zyra_juggernaut", place_change=-0.5),
    ):
        assert type(m).model_validate_json(m.model_dump_json()) == m


def test_recommendation_roundtrip():
    rec = Recommendation(
        target_comps=[
            TargetComp(
                comp_id="zyra_juggernaut",
                name="자이라 거대괴수",
                score=0.82,
                levelling="Fast 8",
                reasons=["아크엔젤 보유"],
                owned_units=["DA_18_Zyra"],
                missing_units=["DA_Nidalee18_AP"],
                items_ready=[ItemReadiness(item_id="DA_ArchangelsStaff", status="owned", holder_unit_id="DA_18_Zyra")],
                next_buildup_board=BuildupBoard(level=7, units=["DA_18_Zyra"]),
            )
        ],
        shop=[ShopAdvice(slot=0, kind=ShopSlotKind.CHAMPION, offer_id="DA_18_Zyra", buy=True, score=0.9,
                         reason_tag=ReasonTag.FINAL_COMP)],
        augment=AugmentAdvice(choices=[AugmentChoice(augment_id="DA_Hustler", score=0.6, editorial_tier="A")],
                              pick="DA_Hustler"),
        item=ItemAdvice(suggestions=[ItemSuggestion(item_id="DA_ArchangelsStaff", score=0.7)], hold=False),
        component_priority=["DA_Component_FryingPan"],
        jev_used=False,
        fallback_reason=FallbackReason.TIMEOUT,
        latency_ms=812.5,
        debug={"jev": None, "comp_scores": {"zyra_juggernaut": 0.82}},
    )
    assert Recommendation.model_validate_json(rec.model_dump_json()) == rec
    assert ReasonTag.FINAL_COMP.label == "최종 덱"


def test_contract_rejects_unknown_fields_and_bad_values():
    with pytest.raises(ValidationError):
        GameState.model_validate({"golds": 3})
    with pytest.raises(ValidationError):
        GameState(stage="3/2")
    with pytest.raises(ValidationError):
        ShopSlot(kind=ShopSlotKind.CHAMPION)  # id 필수
    with pytest.raises(ValidationError):
        GameState(confidence={"not_a_field": 0.5})


def test_config_loads_and_static_data():
    s, w = load_settings(), load_weights()
    assert s.stats.primary is StatSource.METATFT
    assert w.comp.wt == pytest.approx(0.2) and w.comp.show_ratio == pytest.approx(0.75)
    assert w.shop.for_stage(6).wp == pytest.approx(0.8)
    static = load_static(s.app.set_number)
    assert static.name_ko("DA_Hustler") == "수완가"
    assert static.champion_by_name("바위 게")["apiName"] == "DA_Scuttlecrab18"


# --------------------------------------------------------------------------- 0.2.0 계약 / §10a 설정 validator


def test_fallback_reason_closed_set_and_consistency():
    assert {r.value for r in FallbackReason} == {
        "jev_disabled", "circuit_open", "auth", "rate_limited", "overloaded",
        "server_error", "timeout", "connection", "bad_request",
    }
    assert Recommendation(jev_used=True).fallback_reason is None
    assert Recommendation(jev_used=False, fallback_reason="timeout").fallback_reason is FallbackReason.TIMEOUT
    with pytest.raises(ValidationError):
        Recommendation(jev_used=False, fallback_reason="jev timeout")   # 닫힌 집합 밖
    with pytest.raises(ValidationError):
        Recommendation(jev_used=False)                                   # 폴백인데 사유 없음
    with pytest.raises(ValidationError):
        Recommendation(jev_used=True, fallback_reason=FallbackReason.AUTH)


def test_contract_020_field_constraints():
    with pytest.raises(ValidationError):
        Recommendation(jev_used=True, component_priority=["DA_Component_FryingPan"] * 11)
    with pytest.raises(ValidationError):
        CompUnit(id="DA_18_Zyra", role="mage")
    with pytest.raises(ValidationError):
        CompStats(comp_id="x", name="x", source=StatSource.METATFT, item_usage={"DA_ArchangelsStaff": -0.1})
    assert TargetComp(comp_id="x", name="x", score=0.5).levelling is None
    assert UnitItemStats(unit_id="DA_18_Zyra", item_ids=["DA_ArchangelsStaff"], source=StatSource.METATFT).comp_id is None


def test_rank_filter_normalization():
    from tft_advisor.contracts import Provenance, normalize_rank_filter, rank_filter_set, same_rank_filter

    metatft = "CHALLENGER,GRANDMASTER,MASTER,DIAMOND"
    ours = "CHALLENGER,DIAMOND,GRANDMASTER,MASTER"
    assert rank_filter_set(metatft) == rank_filter_set(ours)
    assert same_rank_filter(metatft, ours) and same_rank_filter(None, None) and not same_rank_filter(ours, None)
    assert not same_rank_filter(ours, "CHALLENGER,MASTER")
    assert normalize_rank_filter(" master, challenger,MASTER ") == "CHALLENGER,MASTER"
    assert Provenance(source=StatSource.METATFT, rank_filter=metatft).rank_filter == ours
    assert load_settings().stats.rank_filter == ours


def test_shrinkage_adjust_prior():
    from tft_advisor.config import ShrinkageWeights

    s = ShrinkageWeights(k=200, prior_avg_place=4.5)
    assert s.adjust(4.0, 200) == pytest.approx(4.25)                   # prior=None → prior_avg_place (하위 호환)
    assert s.adjust(4.0, None) == pytest.approx(4.5)
    assert s.adjust(4.0, 0, prior=3.9) == pytest.approx(3.9)           # games 0 → prior
    comp_adj = s.adjust(4.2, 800)
    assert s.adjust(3.8, 200, prior=comp_adj) == pytest.approx((200 * 3.8 + 200 * comp_adj) / 400)
    assert s.adjust(-0.6, 200, prior=0.0) == pytest.approx(-0.3)       # place_change: 범위 검증 없이 0 쪽 수축
    assert ShrinkageWeights(k=0).adjust(3.3, 0, prior=9.0) == pytest.approx(3.3)   # g + k == 0 → x


def test_stage_lookups():
    w = load_weights()
    a, sh = w.augment, w.shop
    assert [a.commit_for_stage(s) for s in (None, 1, 2, 3, 4, 5, 9)] == pytest.approx([0.3, 0.3, 0.3, 0.6, 0.9, 0.9, 0.9])
    assert sh.for_stage(None).ws == pytest.approx(0.8) and sh.for_stage(7).wp == pytest.approx(0.8)
    # commit_for_stage와 for_stage는 같은 조회 규칙(아래 가장 가까운 키, 없으면 최소 키)
    from tft_advisor.config import AugmentWeights, ShopWeights, StageWeight

    keys = {3: 0.4, 5: 0.8}
    aw = AugmentWeights(commit_by_stage=keys)
    sw = ShopWeights(stage_weights={k: StageWeight(ws=v, wp=1 - v) for k, v in keys.items()})
    for s in (None, 1, 3, 4, 5, 8):
        assert aw.commit_for_stage(s) == pytest.approx(sw.for_stage(s).ws)


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        # 합 = 1
        ("CompWeights", {"wi": 0.5}),
        ("PrefilterWeights", {"w_item": 0.5}),
        ("ItemWeights", {"w_bis": 0.6}),
        ("AugmentWeights", {"w_jev": 0.8}),
        ("StageWeight", {"ws": 0.6, "wp": 0.6}),
        # 비증가 사다리 / 순서
        ("PrefilterWeights", {"unit_w_buildup": 0.7}),
        ("ItemFitWeights", {"usage": 0.8}),
        ("ItemFitWeights", {"emblem_other": 0.3, "emblem_key_trait": 0.2}),
        ("ShopWeights", {"mu_cur_buildup": 0.6}),
        ("CompWeights", {"stat_avg_best": 5.2, "stat_avg_worst": 5.2}),
        ("CompWeights", {"show_ratio_undecided": 0.8}),
        ("AdvisorCfg", {"jev_timeout_s": 1.6}),                       # jev_timeout_s > jev_retry_budget_s
        ("AdvisorCfg", {"jev_retry_budget_s": 2.0}),                  # retry_budget == timeout_s
        # 범위·패턴·비어 있음
        ("AdvisorCfg", {"jev_model": "gpt-4"}),
        ("AdvisorCfg", {"jev_model": "jev-1.13"}),
        ("AdvisorCfg", {"jev_max_retries": 4}),
        ("AdvisorCfg", {"max_candidate_comps": 21}),
        ("AugmentWeights", {"commit_by_stage": {}}),
        ("AugmentWeights", {"commit_by_stage": {0: 0.3}}),
        ("AugmentWeights", {"editorial_tier_score": {"S": 1.5}}),
        ("PrefilterWeights", {"dedupe_jaccard": 0}),
        ("ItemFitWeights", {"usage_min_pcnt": 3.5}),
        ("ItemWeights", {"hold_until_stage": 0}),
        ("ShopWeights", {"hp_danger_shift": 0.6}),
        ("CompWeights", {"hysteresis_bonus": 1.2}),
    ],
)
def test_config_validators_reject(model, kwargs):
    from tft_advisor import config

    with pytest.raises(ValidationError):
        getattr(config, model).model_validate(kwargs)


def test_config_accepts_valid_variants():
    from tft_advisor.config import AdvisorCfg, AugmentWeights, Weights

    assert AdvisorCfg(jev_model="jev-1.13.0").jev_model == "jev-1.13.0"
    assert AdvisorCfg(jev_timeout_s=1.5, jev_retry_budget_s=1.5).jev_timeout_s == 1.5   # <= 경계 허용
    assert AugmentWeights(editorial_tier_score={"S": 0.9}).editorial_tier_score["D"] == 0.0   # 누락 등급 채움
    w = Weights.model_validate({"augment": {"commit_by_stage": {"2": 0.3, "4": 1}}, "comp": {"wi": 1, "wa": 0, "wb": 0}})
    assert w.augment.commit_by_stage == {2: 0.3, 4: 1.0}
