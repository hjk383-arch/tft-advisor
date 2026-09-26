"""QA 36: 유닛 정체 추적(`vision.unit_track`) 적대적 시나리오 — 이름은 절대 틀리면 안 된다(모름은 괜찮다).

통과하는 시나리오는 회귀로 고정하고, 지금 틀린 이름을 내는 시나리오는 strict xfail로 남긴다(고치면 xfail 표시를 지운다).
합성 기술자는 `tests/test_unit_track.py`의 LOOK(서로 닮음 0)을 섞어 원하는 닮음을 만든다. 실측(QA 36 스윕, 원본 캡처
라벨 1239쌍): 다른 챔피언끼리 중앙값 0.21 · 95% 0.42 · 최대 0.59, 0.35 이상 14.4%, 0.55 이상 0.3%.
"""
from __future__ import annotations

import pytest

pytest.importorskip("cv2")

from test_unit_track import LOOK, A, B, C, D, frame  # noqa: E402

from tft_advisor.vision import units as U  # noqa: E402
from tft_advisor.vision.board import BoardRead, UnitSlot  # noqa: E402
from tft_advisor.vision.unit_track import UnitTracker  # noqa: E402


def _mix(a: str, b: str, target: float):
    """LOOK[a]와 닮음이 target인 기술자(LOOK[a]와 LOOK[b]를 섞는다)."""
    lo, hi, m = 0.0, 1.0, LOOK[a]
    for _ in range(40):
        t = (lo + hi) / 2
        m = (1 - t) * LOOK[a] + t * LOOK[b]
        if U.similarity(LOOK[a], m) > target:
            lo = t
        else:
            hi = t
    return m


def _update(tr, t, bench=(), board=(), descs=None, active=True):
    """bench/board: [(자리, 기술자, 성급)] 로 직접(LOOK 이름 대신 기술자)."""
    bs = tuple(UnitSlot(star=st, bench_slot=s) for s, _, st in bench)
    hs = tuple(UnitSlot(star=st, hex=h) for h, _, st in board)
    out = tr.update(BoardRead(board=hs, bench=bs), [d for _, d, _ in board], [d for _, d, _ in bench], t,
                    active=active)
    res = {u.bench_slot: (u.unit_id, u.star) for u in out.bench}
    res.update({u.hex: (u.unit_id, u.star) for u in out.board})
    return res


def _with_board_b():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "a", 1)], board=[((0, 0), "b", 1)])
    tr.slots[("board", (0, 0))].unit_id = B          # 보드 (0,0) = 바루스로 알고 있다
    return tr


# ---------------------------------------------------------------- 통과(회귀 고정)
def test_buy_into_a_gap_in_the_middle_of_the_bench():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "a", 1), (1, "b", 1), (3, "c", 1)])
    tr.note_purchase(D, 0.1)
    r, _ = frame(tr, 0.3, bench=[(0, "a", 1), (1, "b", 1), (3, "c", 1), (2, "d", 1)])
    assert r[2][0] == D
    tr = UnitTracker()                                   # 가장 왼쪽 빈 칸(2)이 아닌 칸(4) → 붙이지 않는다
    frame(tr, 0.0, bench=[(0, "a", 1), (1, "b", 1), (3, "c", 1)])
    tr.note_purchase(D, 0.1)
    r, _ = frame(tr, 0.3, bench=[(0, "a", 1), (1, "b", 1), (3, "c", 1), (4, "d", 1)])
    assert r[4][0] is None


def test_two_buys_and_one_move_in_the_same_frame_never_misname():
    tr = _with_board_b()                                 # 산 둘은 1·2, 보드 바루스는 벤치 5로
    tr.note_purchase(C, 0.1); tr.note_purchase(D, 0.2)
    r, _ = frame(tr, 0.4, bench=[(0, "a", 1), (1, "c", 1), (2, "d", 1), (5, "b", 1)])
    assert r[1][0] in (C, None) and r[2][0] in (D, None) and r[5][0] in (B, None)
    tr = _with_board_b()                                 # 구매 C → 1, 바루스 → 2, 구매 D → 3
    tr.note_purchase(C, 0.1); tr.note_purchase(D, 0.2)
    r, _ = frame(tr, 0.4, bench=[(0, "a", 1), (1, "c", 1), (2, "b", 1), (3, "d", 1)])
    assert r[1][0] in (C, None) and r[2][0] in (B, None) and r[3][0] in (D, None)


def test_drag_onto_an_occupied_slot_swaps_names():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(1, "a", 1), (4, "c", 1)], names={1: (A, "duplicate", 0.9), 4: (C, "duplicate", 0.9)})
    r, _ = frame(tr, 1.0, bench=[(1, "c", 1), (4, "a", 1)])
    assert r[1][0] in (C, None) and r[4][0] in (A, None)
    tr = UnitTracker()                                   # 보드 유닛을 벤치의 다른 유닛 위로 → 둘이 자리를 바꾼다
    frame(tr, 0.0, bench=[(3, "c", 1)], board=[((0, 0), "b", 1)], names={3: (C, "duplicate", 0.9)})
    tr.slots[("board", (0, 0))].unit_id = B
    r, _ = frame(tr, 1.0, bench=[(3, "b", 1)], board=[((0, 0), "c", 1)])
    assert r[3][0] in (B, None) and r[(0, 0)][0] in (C, None)


def test_buy_that_combines_with_a_copy_on_the_board():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "a", 1)], board=[((1, 1), "a", 1)], names={0: (A, "duplicate", 0.9)})
    tr.note_purchase(A, 1.0)
    r, _ = frame(tr, 1.2, bench=[], board=[((1, 1), "a", 2)])
    assert r[(1, 1)][0] in (A, None) and r[(1, 1)][2] == 2
    tr = UnitTracker()                                   # 같은 프레임에 무관한 유닛도 성급이 오르면 아무것도 정하지 않는다
    frame(tr, 0.0, bench=[(0, "a", 1), (1, "a", 1)], board=[((1, 1), "c", 1)])
    tr.note_purchase(A, 1.0)
    r, _ = frame(tr, 1.2, bench=[(0, "a", 2)], board=[((1, 1), "c", 2)])
    assert r[(1, 1)][0] != A


def test_carousel_unit_without_buy_stays_unnamed():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "a", 1)], names={0: (A, "duplicate", 0.9)})
    r, _ = frame(tr, 40.0, bench=[(0, "a", 1), (1, "c", 1)])
    assert r[1][0] is None
    tr = UnitTracker()                                   # 공동 선택 유닛 + 같은 프레임에 산 유닛 → 둘 다 모름(추측 없음)
    frame(tr, 0.0, bench=[(0, "a", 1)], names={0: (A, "duplicate", 0.9)})
    tr.note_purchase(D, 39.9)
    r, _ = frame(tr, 40.0, bench=[(0, "a", 1), (1, "c", 1), (2, "d", 1)])
    assert r[1][0] != D and r[2][0] in (D, None)


def test_sell_during_combat_then_buy_into_the_same_slot_typical_similarity():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(1, "b", 1)])
    tr.note_purchase(A, 0.1)
    frame(tr, 0.3, bench=[(1, "b", 1), (0, "a", 1)])
    tr.note_sale(A)
    tr.note_purchase(C, 10.0)                            # 전투 중(보드를 읽지 않음) 판매 + 구매
    r = _update(tr, 30.0, bench=[(1, LOOK["b"], 1), (0, _mix("a", "c", 0.45), 1)])
    assert r[0][0] != A


# ---------------------------------------------------------------- QA36에서 틀린 이름이던 시나리오(35 추가로 고침)
# (QA36 xfail 해제 — 35 보고 추가 §7)
def test_qa36_drag_to_leftmost_empty_plus_buy_in_same_frame():
    tr = _with_board_b()
    tr.note_purchase(D, 0.1)
    # 바루스를 벤치 1(가장 왼쪽 빈 칸)로 끌고, 산 자야는 그다음 빈 칸 2에. 바루스와 자야의 닮음 0.40(실측 다른 챔피언 95%)
    r = _update(tr, 0.4, bench=[(0, LOOK["a"], 1), (1, LOOK["b"], 1), (2, _mix("b", "d", 0.40), 1)])
    assert r[1][0] in (B, None) and r[2][0] in (D, None)


# (QA36 xfail 해제 — 35 보고 추가 §7)
def test_qa36_combat_early_buy_lands_late_buy_combines():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "c", 1), (1, "c", 1)])
    tr.note_purchase(A, 5.0)                             # 전투 초반 아칼리 구매(칸 2에 떨어짐, 다음 준비 프레임엔 만료)
    tr.note_purchase(C, 29.0)                            # 전투 끝 무렵 카밀 세 번째 → 벤치에서 바로 ★2(새 칸 없음)
    r, _ = frame(tr, 30.0, bench=[(0, "c", 2), (2, "a", 1)])
    assert r[2][0] in (A, None)


# (QA36 xfail 해제 — 35 보고 추가 §7)
def test_qa36_carousel_unit_then_combining_buy_within_window():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "a", 1), (3, "d", 1), (4, "d", 1)], names={0: (A, "duplicate", 0.9)})
    r, _ = frame(tr, 40.0, bench=[(0, "a", 1), (1, "c", 1), (3, "d", 1), (4, "d", 1)])    # 공동 선택 유닛(카밀) 칸 1
    assert r[1][0] is None
    tr.note_purchase(D, 41.0)                            # 자야 세 번째 → 합성(새 칸 없음, 성급 오른 칸도 아직 안 보임)
    r, _ = frame(tr, 41.2, bench=[(0, "a", 1), (1, "c", 1), (3, "d", 1), (4, "d", 1)])
    assert r[1][0] != D


# (QA36 xfail 해제 — 35 보고 추가 §7)
def test_qa36_label_only_frame_does_not_name_a_different_unit_in_the_slot():
    tr = UnitTracker()
    frame(tr, 0.0, board=[((0, 2), "a", 2)])
    tr.slots[("board", (0, 2))].unit_id = A
    out = _update(tr, 5.0, board=[((0, 2), LOOK["c"], 1)], active=False)          # 모루 화면, 같은 칸에 다른 ★1
    assert out[(0, 2)][0] is None


# (QA36 xfail 해제 — 35 보고 추가 §7)
def test_qa36_same_slot_star_drop_means_a_different_unit():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "a", 2)], names={0: (A, "duplicate", 0.9)})
    r = _update(tr, 30.0, bench=[(0, _mix("a", "c", 0.57), 1)])                  # 전투 중 팔고 같은 칸에 다른 ★1
    assert r[0][0] is None
