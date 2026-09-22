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
        final_board=[CompUnit(id="DA_18_Zyra", items=["DA_ArchangelsStaff"], is_core=True)],
        carry="DA_18_Zyra",
        carry_bis_items=["DA_ArchangelsStaff"],
        key_traits=[TraitReq(id="DA_18_Elderwood", count=5)],
        buildup={5: [BuildupBoard(level=5, units=["DA_18_Zyra", "DA_18_Sentry"], avg_place=4.2, games=900)]},
        level_timing={5: "2-5", 8: "4-2"},
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
    ):
        assert type(m).model_validate_json(m.model_dump_json()) == m


def test_recommendation_roundtrip():
    rec = Recommendation(
        target_comps=[
            TargetComp(
                comp_id="zyra_juggernaut",
                name="자이라 거대괴수",
                score=0.82,
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
        jev_used=False,
        fallback_reason="jev timeout",
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
