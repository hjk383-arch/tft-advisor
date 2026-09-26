"""QA 36: 성급 미상(None)이 vision → unit_merge → GameState → advisor → 표시("★?")까지 1성으로 바뀌지 않고 흐르는지.

vision 판독 칸(추적기가 이름을 붙였지만 배지를 못 읽은 칸)을 `apply_board_read`로 넣고, mock Jev advisor의 보드 배치와
리포트 줄을 본다(Jev 네트워크 호출 없음).
"""
from __future__ import annotations

from tft_advisor.app.names import NameBook
from tft_advisor.app.report import board_plan_lines
from tft_advisor.app.unit_merge import apply_board_read
from tft_advisor.contracts import FieldSource, GameState
from tft_advisor.vision.board import BoardRead, UnitSlot

YORICK, AKALI, ORNN = "DA_18_Yorick", "DA_18_Akali_AD", "DA_18_Ornn"


def _state() -> GameState:
    return GameState.model_validate({
        "screen_mode": "planning", "stage": "3-2", "level": 5, "gold": 30, "hp": 70,
        "shop": [{"kind": "empty"}] * 5, "items": {"components": []},
        "field_source": {"items": FieldSource.VISION}})


def _read() -> BoardRead:
    return BoardRead(
        board=(UnitSlot(star=None, hex=(0, 0), unit_id=YORICK, unit_conf=0.85, name_source="tracked",
                        corroborated=True, confidence=0.9),
               UnitSlot(star=1, hex=(0, 1), unit_id=AKALI, unit_conf=0.9, name_source="purchase",
                        corroborated=True, confidence=0.9)),
        bench=(UnitSlot(star=None, bench_slot=2, unit_id=ORNN, unit_conf=0.85, name_source="tracked",
                        corroborated=True, confidence=0.9),))


def test_unknown_star_flows_to_state_advisor_and_report(make_advisor, stats):
    state = apply_board_read(_state(), _read())
    stars = {u.id: u.star for u in (state.board or []) + (state.bench or [])}
    assert stars[YORICK] is None and stars[ORNN] is None and stars[AKALI] == 1   # 장부 없음 → 1로 지어내지 않는다
    rec = make_advisor().advise(state)
    plan = rec.board_plan
    assert plan is not None
    got = {e.unit_id: e.star for e in plan.lineup + plan.bench}
    assert got.get(YORICK, "absent") in (None, "absent") and got.get(ORNN, "absent") in (None, "absent")
    assert any(v is None for v in got.values())
    text = "\n".join(board_plan_lines(plan, NameBook()))
    assert "★?" in text
    assert "★1" not in text
