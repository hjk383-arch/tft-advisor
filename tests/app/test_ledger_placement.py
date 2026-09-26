"""장부 이름은 **유일하게 정해질 때만** 칸에 붙인다(31 보고 §9, live3: 벤치 2번 덩치에 "카밀(장부)"이 붙었다).

장부는 어떤 챔피언을 가졌는지는 알지만 어느 칸인지는 모른다. 정해지지 않으면 칸은 "이름 미상", 장부 유닛은
자리 미상(hex/bench_slot None)으로 advisor에 그대로 보유 유닛으로 들어간다.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tft_advisor.app.ledger import CostBook, UnitLedger
from tft_advisor.app.names import NameBook
from tft_advisor.app.overlay import STALE, WARN, OverlayWindow
from tft_advisor.app.recog_view import RecogSnapshot, build_view
from tft_advisor.app.unit_merge import BoardObs, SlotObs, equipped_refs, merge_units, state_with_units
from tft_advisor.contracts import (
    UNKNOWN_UNIT_ID, BoardPlan, BoardPlanEntry, Recommendation, SellAdvice,
)
from tft_advisor.unit_status import owned_units

from .conftest import planning_state

COSTS = CostBook()
SHEN = "DA_18_Xayah"          # 이름은 상관없다 — 서로 다른 1코스트 셋
CAMILLE = "DA_Cinderling18"
RAKAN = "DA_Murkwolf18"
AKALI = "DA_18_Hecarim"


def ledger(**copies: int) -> UnitLedger:
    led = UnitLedger()
    for cid, n in copies.items():
        led.set_copies(cid, n, source="shop")
    led.manual = False
    return led


def bench(*stars, items=None) -> tuple[SlotObs, ...]:
    """(칸 번호, 성급) 쌍 → 벤치 판독 칸."""
    items = items or {}
    return tuple(SlotObs(star=st, bench_slot=i, on_bench=True, items=tuple(items.get(i, ())))
                 for i, st in stars)


def test_live3_bench_does_not_get_names_in_order():
    """live3: 벤치 1·2·4번에 이름 없는 1성 3기, 장부 쉔·카밀·라칸 → 어느 칸에도 장부 이름을 붙이지 않는다."""
    led = ledger(**{SHEN: 1, CAMILLE: 1, RAKAN: 1})
    obs = BoardObs(bench=bench((0, 1), (1, 1), (3, 1), items={1: ("DA_Bloodthirster",)}))
    out = merge_units(led, obs, level=4, costs=COSTS)
    assert len(out.bench) == 3 and out.unplaced == 3 and out.unknown == 0
    assert {u.id for u in out.bench} == {SHEN, CAMILLE, RAKAN}
    assert all(u.bench_slot is None and u.items == [] for u in out.bench)      # 가짜 자리·아이템 없음
    assert all(r.holder is None for r in equipped_refs(obs, out, costs=COSTS))  # 아이템 소유자도 모른다
    assert "자리 미상 3기" in (out.note() or "")
    # advisor는 여전히 세 챔피언을 보유 유닛으로 본다(자리 없이)
    state = state_with_units(planning_state(), out, obs, costs=COSTS)
    assert {u.id for u in owned_units(state).units} == {SHEN, CAMILLE, RAKAN}


def test_star2_slot_is_determined_by_the_only_ledger_champion_with_three_copies():
    led = ledger(**{AKALI: 3, SHEN: 1, CAMILLE: 1})
    obs = BoardObs(bench=bench((0, 1), (2, 2), (5, 1)))
    out = merge_units(led, obs, level=4, costs=COSTS)
    placed = {u.bench_slot: u.id for u in out.bench if u.bench_slot is not None}
    assert placed == {2: AKALI}
    assert out.unplaced == 2 and {u.id for u in out.bench if u.bench_slot is None} == {SHEN, CAMILLE}


def test_single_slot_single_unit_and_same_champion_pairs_are_placed():
    out = merge_units(ledger(**{CAMILLE: 1}), BoardObs(bench=bench((3, 1))), level=4, costs=COSTS)
    assert out.bench[0].id == CAMILLE and out.bench[0].bench_slot == 3 and out.unplaced == 0
    out = merge_units(ledger(**{CAMILLE: 2}), BoardObs(bench=bench((1, 1), (4, 1))), level=4, costs=COSTS)
    assert [(u.id, u.bench_slot) for u in out.bench] == [(CAMILLE, 1), (CAMILLE, 4)]


def test_placing_one_can_determine_the_rest():
    """★2 칸이 아칼리로 정해지면 남은 1칸·1기(카밀)도 정해진다."""
    led = ledger(**{AKALI: 3, CAMILLE: 1})
    out = merge_units(led, BoardObs(bench=bench((0, 1), (6, 2))), level=4, costs=COSTS)
    assert {u.bench_slot: u.id for u in out.bench} == {6: AKALI, 0: CAMILLE} and out.unplaced == 0


def test_star1_groups_need_every_star_read():
    led = ledger(**{AKALI: 3, CAMILLE: 1, SHEN: 1})
    obs = BoardObs(bench=bench((0, None), (1, 1), (2, 2)))
    out = merge_units(led, obs, level=4, costs=COSTS)
    assert {u.bench_slot: u.id for u in out.bench if u.bench_slot is not None} == {2: AKALI}


@dataclass
class Read:
    board: tuple = ()
    bench: tuple = ()
    confidence: float = 0.9
    unplaced: tuple = ()


def test_recognition_window_shows_unknown_slots_and_ledger_line():
    led = ledger(**{SHEN: 1, CAMILLE: 1, RAKAN: 1})
    obs = BoardObs(bench=bench((0, 1), (1, 1), (3, 1)))
    out = merge_units(led, obs, level=4, costs=COSTS)
    state = state_with_units(planning_state(), out, obs, costs=COSTS)
    names = NameBook()
    read = Read(bench=obs.bench)
    view = build_view(RecogSnapshot(state=state, board_read=read), names)
    rows = {r.pos: r for r in view.bench if not r.empty}
    assert set(rows) == {"벤치 1", "벤치 2", "벤치 4"}
    assert all(r.source == "unknown" and r.unit_id is None for r in rows.values())
    assert view.ledger_note is not None and view.ledger_note.startswith("장부 보유(자리 미상): ")
    for cid in (SHEN, CAMILLE, RAKAN):
        assert names.name(cid) in view.ledger_note
    assert view.bench_count == 3 and "이름 미상 3기" in (view.bench_note or "")


def test_overlay_highlights_sell_and_dims_stale_plan(qapp, settings, tmp_path):
    plan = BoardPlan(slots=4, lineup=[BoardPlanEntry(unit_id=SHEN, action="keep", on_board=True)],
                     sell=[SellAdvice(unit_id=CAMILLE, where="bench", gold=1, reason="목표 덱·빌드업에 없습니다")],
                     sell_gold_total=1)
    rec = Recommendation(jev_used=True, board_plan=plan)
    w = OverlayWindow(settings, state_dir=tmp_path)
    w.set_data(planning_state(), rec)
    html = w.body.text()
    assert f"color:{WARN}'>판매:" in html
    w.set_data(planning_state(), rec.model_copy(update={"board_plan": plan.model_copy(update={"stale": True})}))
    html = w.body.text()
    assert "(직전)" in html and f"color:{STALE}'>" in html and f"color:{WARN}'>판매:" not in html
    w.deleteLater()


def test_unknown_id_is_never_invented():
    out = merge_units(ledger(**{SHEN: 1}), BoardObs(bench=bench((0, 1), (1, 1))), level=4, costs=COSTS)
    ids = [u.id for u in out.bench]
    assert ids.count(UNKNOWN_UNIT_ID) == 1 and ids.count(SHEN) == 1
    assert next(u for u in out.bench if u.id == SHEN).bench_slot is None


def test_qa32_vision_unplaced_set_does_not_take_the_star_of_an_arbitrary_slot():
    """QA 32 (live3 2-2 실제 재현): 특성 풀이 집합 {아칼리, 세주아니, 바루스, 피들스틱}(자리 미상) + 이름 없는 보드 칸 성급
    [1, 1, 2, 1]. 어느 챔피언이 ★2인지는 모르는데, 집합을 정렬 순서로 칸에 짝지어 그 칸의 성급을 붙이면 **바루스 ★2**가
    된다(실제 ★2는 아칼리 — 장부 사본 3개). 성급이 칸마다 다르면 칸 성급을 쓰지 않아야 한다(모듈 주석과 같게)."""
    akali, sejuani, varus, fiddle = "DA_18_Akali_AD", "DA_18_Sejuani", "DA_18_Varus", "DA_Fiddlesticks18"
    board = tuple(SlotObs(star=st, hex=h) for st, h in ((1, (0, 1)), (1, (0, 2)), (2, (3, 0)), (1, (3, 2))))
    obs = BoardObs(board=board, unplaced=(akali, sejuani, varus, fiddle))
    out = merge_units(ledger(**{akali: 3, sejuani: 1, varus: 1, fiddle: 1}), obs, level=4, costs=COSTS)
    stars = {u.id: u.star for u in out.board}
    assert stars[varus] == 1 and stars[akali] == 2


def test_qa32_recognition_window_does_not_double_count_vision_unplaced_board():
    akali, sejuani, varus, fiddle = "DA_18_Akali_AD", "DA_18_Sejuani", "DA_18_Varus", "DA_Fiddlesticks18"
    board = tuple(SlotObs(star=st, hex=h) for st, h in ((1, (0, 1)), (1, (0, 2)), (2, (3, 0)), (1, (3, 2))))
    obs = BoardObs(board=board, unplaced=(akali, sejuani, varus, fiddle))
    out = merge_units(UnitLedger(), obs, level=4, costs=COSTS)
    state = state_with_units(planning_state(), out, obs, costs=COSTS)
    view = build_view(RecogSnapshot(state=state, board_read=Read(board=board, unplaced=obs.unplaced)), NameBook())
    assert len(view.board) == 4
