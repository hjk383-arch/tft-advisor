"""보드·벤치 신뢰도를 따로 판정한다(2026-09-23 사용자 결정, `_workspace/21_board_trust.md`).

- 보드 신뢰(≥ 0.6) + 벤치 불신: 보드 유닛 전부 + 벤치 중 이름을 확인한(유닛 신뢰도 ≥ 0.6) 유닛만 쓴다.
- 이름 미상(`UNKNOWN_UNIT_ID`) 칸은 어떤 챔피언으로도 세지 않는다. 부분 확인이면 "부족"·"보유 개수"를 확정하지 않는다.
- 필드 신뢰도가 낮은데 출처가 구매 추적이면(장부가 애매) 아무것도 쓰지 않는다.
"""
from __future__ import annotations

from tft_advisor.advisor.features import build_view
from tft_advisor.contracts import UNKNOWN_UNIT_ID, FieldSource, GameState, UnitOnBoard
from tft_advisor.unit_status import UnitsKnowledge, owned_units, units_knowledge, units_note, units_reason

VISION = {"board": FieldSource.VISION, "bench": FieldSource.VISION}
TRACKED = {"board": FieldSource.TRACKED, "bench": FieldSource.TRACKED}
IMPOLITE = ("한다", "하라", "해요", "이다", "없다")


def U(uid: str, conf: float = 0.9, star: int = 1) -> UnitOnBoard:
    return UnitOnBoard(id=uid, confidence=conf, star=star)


def unknown() -> UnitOnBoard:
    return UnitOnBoard(id=UNKNOWN_UNIT_ID, confidence=0.2, star=1)


def gs(**kw) -> GameState:
    kw.setdefault("screen_mode", "planning")
    kw.setdefault("stage", "2-6")
    kw.setdefault("level", 5)
    kw.setdefault("gold", 30)
    return GameState.model_validate(kw)


def board_ok_bench_low(**kw) -> GameState:
    """test.png와 같은 모양: 보드 0.85(전부 이름 확인), 벤치 0.38(이름 미상 3 + 확인 2 + 낮은 신뢰도 1)."""
    return gs(board=[U("DA_18_Zyra"), U("DA_18_Ashe")],
              bench=[unknown(), U("DA_18_Sejuani", 0.8), unknown(), U("DA_18_Sejuani", 0.7),
                     U("DA_18_Maokai", 0.5), unknown()],
              confidence={"board": 0.85, "bench": 0.38}, field_source=VISION, **kw)


# ---------------------------------------------------------------------------
# 판정(features.build_view / unit_status.owned_units)
# ---------------------------------------------------------------------------


def test_board_reliable_bench_unreliable_uses_board_and_named_bench(stats):
    v = build_view(board_ok_bench_low(), stats, 0.6, None)
    assert v.units_known and v.units_partial and not v.units_complete
    assert v.board_complete                                         # 보드는 전부 안다 → 활성 특성 계산 가능
    assert [u.id for u in v.board] == ["DA_18_Zyra", "DA_18_Ashe"]
    assert [u.id for u in v.bench] == ["DA_18_Sejuani", "DA_18_Sejuani"]   # 이름 미상·신뢰도 0.5는 뺀다
    assert UNKNOWN_UNIT_ID not in {u.id for u in v.units}
    assert v.owned.bench_hidden == 4 and v.owned.board_hidden == 0


def test_both_reliable_is_complete(stats):
    st = gs(board=[U("DA_18_Zyra")], bench=[U("DA_18_Ashe")], confidence={"board": 0.9, "bench": 0.9},
            field_source=VISION)
    v = build_view(st, stats, 0.6, None)
    assert v.units_known and v.units_complete and not v.units_partial
    assert units_knowledge(st, 0.6) is UnitsKnowledge.VISION and units_reason(st, 0.6) is None


def test_both_reliable_but_unknown_slot_is_partial(stats):
    st = gs(board=[U("DA_18_Zyra")], bench=[U("DA_18_Ashe"), unknown()], confidence={"board": 0.9, "bench": 0.7},
            field_source=VISION)
    v = build_view(st, stats, 0.6, None)
    assert v.units_partial and v.board_complete
    assert "이름 미상 1기" in units_reason(st, 0.6)


def test_board_unreliable_vision_keeps_only_named_units(stats):
    st = gs(board=[U("DA_18_Zyra", 0.8), unknown(), unknown()], bench=[U("DA_18_Ashe")],
            confidence={"board": 0.27, "bench": 0.9}, field_source=VISION)
    v = build_view(st, stats, 0.6, None)
    assert [u.id for u in v.board] == ["DA_18_Zyra"] and [u.id for u in v.bench] == ["DA_18_Ashe"]
    assert v.units_partial and not v.board_complete                 # 보드 특성 인원은 계산하지 않는다


def test_unreliable_tracked_side_is_not_used(stats):
    """장부가 애매해 신뢰도가 떨어졌으면 유닛별 신뢰도(장부는 대개 1.0)를 믿지 않는다."""
    st = gs(board=[U("DA_18_Zyra", 1.0)], bench=[U("DA_18_Ashe", 1.0)], confidence={"board": 0.4, "bench": 0.4},
            field_source=TRACKED)
    v = build_view(st, stats, 0.6, None)
    assert not v.units_known and v.units == []
    assert "추천에는 쓰지 않습니다" in units_note(st, 0.6)
    # 보드만 믿을 수 있으면 보드만 쓴다
    st2 = st.model_copy(update={"confidence": {"board": 0.85, "bench": 0.4}})
    v2 = build_view(st2, stats, 0.6, None)
    assert [u.id for u in v2.units] == ["DA_18_Zyra"] and v2.units_partial


def test_partial_view_does_not_take_equipped_from_units(stats):
    """부분 확인이면 미확인 칸의 장착 아이템이 빠지므로 유닛에서 장착분을 모으지 않는다(세션 추적으로 간다)."""
    st = board_ok_bench_low(items={"completed": []})
    st = st.model_copy(update={"board": [UnitOnBoard(id="DA_18_Zyra", items=["DA_Deathblade"], confidence=0.9)]})
    v = build_view(st, stats, 0.6, None)
    assert v.units_partial and v.equipped == []


def test_owned_units_gap_text_and_polite_notes():
    st = board_ok_bench_low()
    o = owned_units(st, 0.6)
    assert o.gap_text() == "이름 미상 4기"
    reason, note = units_reason(st, 0.6), units_note(st, 0.6)
    assert units_knowledge(st, 0.6) is UnitsKnowledge.PARTIAL
    assert "4기 반영" in reason and "미확인 칸" in reason
    assert "4기 반영" in note and "추천에는 쓰지 않습니다" not in note
    assert not any(bad in reason + note for bad in IMPOLITE)
    only_board = gs(board=[U("DA_18_Zyra")], confidence={"board": 0.9}, field_source={"board": FieldSource.VISION})
    assert "벤치 미인식" in units_reason(only_board, 0.6)


# ---------------------------------------------------------------------------
# 엔진 끝까지(mock Jev)
# ---------------------------------------------------------------------------


SHOP = [{"kind": "champion", "id": "DA_18_Sejuani"}, {"kind": "champion", "id": "DA_18_Yorick"}] + [{"kind": "empty"}] * 3


def test_advise_uses_board_units_when_bench_unreliable(make_advisor):
    # 5스테이지: 보유 유닛이 목표 덱 선정에 전부 반영되는 구간(21 §10 — 2~3스테이지 1성은 빌드업이라 거의 안 센다)
    rec = make_advisor().advise(board_ok_bench_low(shop=SHOP, stage="5-1"))
    assert rec.target_comps
    owned = {u for c in rec.target_comps for u in c.owned_units}
    assert owned, "보드 신뢰 + 벤치 불신이어도 보유 유닛이 목표 덱에 반영되어야 합니다"
    assert UNKNOWN_UNIT_ID not in owned and "DA_18_Maokai" not in owned   # 신뢰도 0.5 벤치 유닛은 세지 않는다
    for c in rec.target_comps:
        assert UNKNOWN_UNIT_ID not in c.missing_units
        if c.owned_units or c.missing_units:
            assert any("미확인 칸" in r for r in c.reasons), c.reasons     # 부족을 확정하지 않는다
    js = rec.debug["jev_state"]
    assert js["unidentified_units"]["bench"] == 4 and js["unidentified_units"]["board"] == 0
    assert "active_traits" in js                                            # 보드는 전부 안다
    sej = js["shop"][0]
    assert "copies_owned" not in sej and sej["copies_owned_at_least"] == 2  # 이름 미상은 사본으로 세지 않는다
    assert sej["buy_makes_2star"] is True                                   # 확인된 1성 2기 → 증명 가능
    yor = js["shop"][1]
    assert yor["copies_owned_at_least"] == 0 and yor["buy_makes_2star"] == "unknown"   # 미확인 칸에 있을 수 있다
    assert "확인 보유 2" in (rec.shop[0].reason or "")


def test_advise_both_reliable_is_certain(make_advisor):
    st = gs(board=[U("DA_18_Zyra"), U("DA_18_Ashe")], bench=[U("DA_18_Sejuani"), U("DA_18_Sejuani")],
            confidence={"board": 0.9, "bench": 0.9}, field_source=VISION, shop=SHOP)
    rec = make_advisor().advise(st)
    js = rec.debug["jev_state"]
    assert "unidentified_units" not in js
    assert js["shop"][0]["copies_owned"] == 2 and js["shop"][0]["buy_makes_2star"] is True
    assert js["shop"][1]["buy_makes_2star"] is False
    for c in rec.target_comps:
        assert not any("미확인 칸" in r for r in c.reasons)


def test_advise_board_unreliable_drops_traits(make_advisor):
    st = gs(board=[U("DA_18_Zyra", 0.8), unknown(), unknown()], bench=[],
            confidence={"board": 0.27, "bench": 0.85}, field_source=VISION, shop=SHOP)
    rec = make_advisor().advise(st)
    js = rec.debug["jev_state"]
    assert [e["unit"] for e in js["board"]] and "active_traits" not in js
    assert js["unidentified_units"]["board"] == 2
    owned = {u for c in rec.target_comps for u in c.owned_units}
    assert owned <= {"DA_18_Zyra"}
