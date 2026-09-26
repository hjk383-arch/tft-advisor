"""판 안의 유닛 정체 추적(`vision.unit_track`, 35 보고): 구매 칸 · 옮기기 · 자리 바꾸기 · 판매 · 합성 · 애매하면 버리기."""
from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tft_advisor.vision import units as U  # noqa: E402
from tft_advisor.vision.board import BoardRead, UnitSlot  # noqa: E402
from tft_advisor.vision.unit_track import UnitTracker  # noqa: E402

A, B, C, D = "DA_18_Akali_AD", "DA_18_Varus", "DA_18_Camille", "DA_18_Xayah"


def _crop(color, shape="rect") -> np.ndarray:
    out = np.full((112, 112, 3), (90, 120, 150), np.uint8)
    if shape == "rect":
        cv2.rectangle(out, (40, 20), (72, 100), color, -1)
    else:
        cv2.circle(out, (56, 60), 30, color, -1)
    return out


LOOK = {name: U.descriptor(_crop(c, sh)) for name, c, sh in (
    ("a", (40, 40, 190), "rect"), ("b", (190, 60, 40), "rect"), ("c", (200, 200, 40), "circle"),
    ("d", (160, 40, 160), "circle"), ("a2", (42, 42, 188), "rect"))}


def frame(tracker, t, bench=(), board=(), shop=None, active=True, names=None):
    """bench/board: [(자리, 모양, 성급)] → 추적 결과 {자리: (이름, 출처, 성급)}."""
    names = names or {}
    bs = []
    for s, _, st in bench:
        uid, src, conf = names.get(s, (None, "none", 0.0))
        bs.append(UnitSlot(star=st, bench_slot=s, unit_id=uid, name_source=src, unit_conf=conf))
    hs = [UnitSlot(star=st, hex=h) for h, _, st in board]
    read = BoardRead(board=tuple(hs), bench=tuple(bs))
    out = tracker.update(read, [LOOK[k] for _, k, _ in board], [LOOK[k] for _, k, _ in bench], t,
                         shop=shop, active=active)
    res = {u.bench_slot: (u.unit_id, u.name_source, u.star) for u in out.bench}
    res.update({u.hex: (u.unit_id, u.name_source, u.star) for u in out.board})
    return res, out


def _bought(tr, champ, t, bench_before, new_slot, look):
    """구매 한 건: 장부 이벤트 → 가장 왼쪽 빈 칸에 새 유닛."""
    tr.note_purchase(champ, t)
    return frame(tr, t + 0.1, bench=[*bench_before, (new_slot, look, 1)])


def test_purchase_goes_to_the_leftmost_empty_slot_from_shop_or_ledger():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(1, "b", 1)], shop=(A, B, None, None, None))
    r, _ = frame(tr, 0.3, bench=[(1, "b", 1), (0, "a", 1)], shop=(None, B, None, None, None))
    assert r[0] == (A, "purchase", 1) and r[1][0] is None               # 상점 칸이 비는 프레임에 칸 0(가장 왼쪽 빈 칸)
    tr.note_purchase(A, 0.35)                                           # 같은 구매의 장부 보고(늦게) — 두 번 쓰지 않는다
    r, _ = frame(tr, 0.6, bench=[(1, "b", 1), (0, "a", 1)])
    assert r[0] == (A, "purchase", 1) and r[1][0] is None
    # 장부만 있는 구매: 새 칸이 먼저 보이고 이벤트가 뒤에 와도 짝짓는다
    tr2 = UnitTracker()
    frame(tr2, 0.0, bench=[(0, "a", 1)])
    frame(tr2, 0.3, bench=[(0, "a", 1), (1, "c", 1)])
    tr2.note_purchase(C, 0.4)
    r, _ = frame(tr2, 0.6, bench=[(0, "a", 1), (1, "c", 1)])
    assert r[1] == (C, "purchase", 1) and r[0][0] is None


def test_new_unit_not_at_the_leftmost_empty_slot_is_not_named():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "a", 1)])
    frame(tr, 0.3, bench=[(0, "a", 1), (4, "c", 1)])                    # 가장 왼쪽 빈 칸은 1인데 4에 생김
    tr.note_purchase(C, 0.35)
    r, _ = frame(tr, 0.6, bench=[(0, "a", 1), (4, "c", 1)])
    assert r[4][0] is None


def test_two_quick_buys_fill_leftmost_empties_in_order():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[(0, "a", 1), (2, "b", 1)])
    tr.note_purchase(C, 0.1)
    tr.note_purchase(D, 0.2)
    r, _ = frame(tr, 0.4, bench=[(0, "a", 1), (2, "b", 1), (1, "c", 1), (3, "d", 1)])
    assert r[1][0] == C and r[3][0] == D
    # 구매 수와 새 칸 수가 다르면(하나는 합성 등) 추측하지 않는다
    tr2 = UnitTracker()
    frame(tr2, 0.0, bench=[(0, "a", 1)])
    tr2.note_purchase(C, 0.1)
    tr2.note_purchase(D, 0.2)
    r, _ = frame(tr2, 0.4, bench=[(0, "a", 1), (1, "c", 1)])
    assert r[1][0] is None


def test_identity_follows_bench_to_board_and_back_and_swaps():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[])
    _bought(tr, A, 0.1, [], 0, "a")
    _bought(tr, C, 0.3, [(0, "a", 1)], 1, "c")
    r, _ = frame(tr, 1.0, bench=[(1, "c", 1)], board=[((0, 3), "a", 1)])     # 벤치 0 → 보드
    assert r[(0, 3)] == (A, "tracked", 1) and r[1] == (C, "purchase", 1)
    r, _ = frame(tr, 2.0, bench=[(1, "c", 1)], board=[((2, 5), "a", 1)])     # 보드 안에서 옮기기
    assert r[(2, 5)][0] == A
    r, _ = frame(tr, 3.0, bench=[(1, "c", 1), (6, "a", 1)])                    # 보드 → 벤치
    assert r[6][0] == A
    r, _ = frame(tr, 4.0, bench=[(1, "a", 1), (6, "c", 1)])                    # 두 칸이 자리를 바꿈(끌어 놓기)
    assert r[1][0] == A and r[6][0] == C


def test_ambiguous_moves_drop_identity_instead_of_guessing():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[])
    _bought(tr, A, 0.1, [], 0, "a")
    _bought(tr, B, 0.3, [(0, "a", 1)], 1, "a2")          # 거의 같은 그림의 다른 챔피언
    r, _ = frame(tr, 0.5, bench=[(0, "a", 1), (1, "a2", 1)])
    assert r[0][0] == A and r[1][0] == B
    # 두 칸이 동시에 사라지고 거의 같은 그림 두 칸이 생김 → 누가 누구인지 모른다
    r, _ = frame(tr, 1.0, bench=[(4, "a2", 1), (5, "a", 1)])
    assert r[4][0] is None and r[5][0] is None and tr.dropped >= 1


def test_sold_unit_identity_expires_and_a_new_unit_in_that_slot_is_not_renamed():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[])
    _bought(tr, A, 0.1, [], 0, "a")
    frame(tr, 1.0, bench=[])                                   # 팔았다
    tr.note_sale(A)
    r, _ = frame(tr, 20.0, bench=[(0, "c", 1)])               # 한참 뒤 공동 선택 등으로 다른 유닛
    assert r[0][0] is None
    tr2 = UnitTracker()
    frame(tr2, 0.0, bench=[])
    _bought(tr2, A, 0.1, [], 0, "a")
    r, _ = frame(tr2, 0.5, bench=[(0, "c", 1)])               # 같은 칸에 다른 모델 → 옛 정체를 붙이지 않는다
    assert r[0][0] is None


def test_buy_that_combines_into_two_star_keeps_the_surviving_slot():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[])
    _bought(tr, A, 0.1, [], 0, "a")
    _bought(tr, A, 0.3, [(0, "a", 1)], 1, "a")
    tr.note_purchase(A, 1.0)                                  # 세 번째: 새 칸 없이 칸 0이 ★2, 칸 1 사본은 사라진다
    r, _ = frame(tr, 1.2, bench=[(0, "a", 2)])
    assert r[0] == (A, "purchase", 2)
    tr2 = UnitTracker()                                       # 이름 모르던 칸도 합성 구매 한 건 + 성급 상승 한 칸(+사본 사라짐)이면 정한다
    frame(tr2, 0.0, bench=[(3, "d", 1), (5, "d", 1)])
    tr2.note_purchase(D, 1.0)
    r, _ = frame(tr2, 1.2, bench=[(3, "d", 2)])
    assert r[3] == (D, "purchase", 2)
    tr3 = UnitTracker()                                       # 사본이 사라지지 않은 성급 상승(구슬·증강) → 구매로 보지 않는다
    frame(tr3, 0.0, bench=[(3, "d", 1)])
    tr3.note_purchase(D, 1.0)
    r, _ = frame(tr3, 1.2, bench=[(3, "d", 2)])
    assert r[3][0] is None


def test_strong_vision_name_is_adopted_and_a_conflict_drops_both():
    tr = UnitTracker()
    r, _ = frame(tr, 0.0, board=[((0, 1), "b", 1)], bench=[(2, "c", 1)], names={2: (C, "duplicate", 0.85)})
    assert r[2][0] == C
    r, _ = frame(tr, 1.0, bench=[(5, "c", 1)], board=[((0, 1), "b", 1)])      # vision 이름 없이 옮겨도 이어진다
    assert r[5] == (C, "tracked", 1)
    _bought(tr, A, 1.1, [(5, "c", 1)], 0, "a")
    r, _ = frame(tr, 1.4, bench=[(5, "c", 1), (0, "a", 1)], names={0: (B, "traits", 0.9)})
    assert r[0][0] is None                                                      # 구매(아칼리) vs 특성 배정(바루스) → 모름


def test_hp_bar_less_frames_only_label_held_slots_and_do_not_update():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[])
    _bought(tr, A, 0.1, [], 0, "a")
    held = BoardRead(bench=(UnitSlot(star=1, bench_slot=0, name_source="held"),
                            UnitSlot(star=None, bench_slot=3)), bench_held=True)
    out = tr.update(held, [], [], 5.0)
    got = {u.bench_slot: (u.unit_id, u.name_source) for u in out.bench}
    assert got[0] == (A, "tracked") and got[3] == (None, "none")
    r, _ = frame(tr, 6.0, bench=[(0, "a", 1)])                                # 체력바가 돌아와도 그대로
    assert r[0][0] == A
    r, _ = frame(tr, 7.0, bench=[(0, "a", 1), (4, "c", 1)], active=False)     # 준비 단계가 아니면 갱신 안 함
    assert r[4][0] is None and ("bench", 4) not in tr.slots


def test_unknown_star_stays_none_and_moved_unit_keeps_its_star():
    tr = UnitTracker()
    r, _ = frame(tr, 0.0, bench=[(0, "b", None)])
    assert r[0][2] is None
    frame(tr, 1.0, bench=[(3, "d", 2)])
    r, _ = frame(tr, 2.0, bench=[], board=[((1, 1), "d", None)])             # 옮긴 뒤 배지를 못 읽음 → 그 유닛의 ★2
    assert r[(1, 1)][2] == 2


def test_board_names_shrink_unplaced():
    tr = UnitTracker()
    frame(tr, 0.0, bench=[])
    _bought(tr, A, 0.1, [], 0, "a")
    read = BoardRead(board=(UnitSlot(star=1, hex=(0, 0)), UnitSlot(star=1, hex=(0, 1))), bench=(),
                     board_set=(A, C), unplaced=(A, C))
    out = tr.update(read, [LOOK["a"], LOOK["c"]], [], 1.0)
    assert {u.hex: u.unit_id for u in out.board} == {(0, 0): A, (0, 1): None}
    assert out.unplaced == (C,)


def test_same_slot_moderately_similar_other_unit_is_not_kept():
    """전투 중(보드를 안 읽는 동안) 팔고 같은 칸에 다른 유닛을 샀다: 다음 준비 프레임의 같은 칸 = 다른 유닛.
    같은 프레임 다른 유닛끼리도 닮음이 0.5대까지 나오므로 KEEP_MIN(0.55) 아래면 정체를 버린다."""
    from tft_advisor.vision import unit_track as T

    tr = UnitTracker()
    frame(tr, 0.0, bench=[])
    _bought(tr, A, 0.1, [], 0, "a")
    half = (LOOK["a"] + LOOK["c"]) / 2                      # 닮음 약 0.5
    assert 0.3 < U.similarity(LOOK["a"], half) < T.KEEP_MIN
    read = BoardRead(bench=(UnitSlot(star=1, bench_slot=0),))
    out = tr.update(read, [], [half], 30.0)
    assert out.bench[0].unit_id is None


def test_away_arena_freezes_tracking_and_home_resumes_without_wiping():
    """35 §8(라이브): 원정 전투·관전으로 맵 서명이 바뀌어도 정체를 지우지 않는다. 다른 맵 프레임에서는 갱신도 이름도 없다."""
    tr = UnitTracker()
    read = BoardRead(bench=(UnitSlot(star=1, bench_slot=0, unit_id=C, name_source="duplicate", unit_conf=0.9),))
    tr.update(read, [], [LOOK["c"]], 0.0, arena="0a080a")
    plain = BoardRead(bench=(UnitSlot(star=1, bench_slot=0),))
    assert tr.update(plain, [], [LOOK["c"]], 1.0, arena="0b080a").bench[0].unit_id == C     # 같은 맵(밝기만 다름)
    away = BoardRead(bench=(UnitSlot(star=1, bench_slot=0), UnitSlot(star=1, bench_slot=4)))
    out = tr.update(away, [], [LOOK["a"], LOOK["b"]], 2.0, arena="080806")                  # 다른 플레이어 맵
    assert tr.frozen and all(u.unit_id is None for u in out.bench) and ("bench", 4) not in tr.slots
    assert tr.update(plain, [], [LOOK["c"]], 30.0, arena="0a080a").bench[0].unit_id == C    # 돌아오면 그대로
    assert not tr.frozen and tr.home_arena == "0a080a"
    tr.reset()                                                                                 # 새 판은 루프가 비운다
    assert tr.update(plain, [], [LOOK["c"]], 31.0, arena="0a080a").bench[0].unit_id is None


def test_realistic_live_timing_names_the_bought_unit():
    """실제 루프 시각(캡처 4fps · 변화 안정 2프레임 · 강제 다시 읽기 0.5초 · 장부 정산): 장부 구매 이벤트와 새 칸 판독이
    1초 넘게 떨어져도(앞뒤 모두) 이름이 붙는다."""
    for buy_at, seen_at in ((10.0, 11.25), (10.0, 12.0), (12.3, 10.5)):
        tr = UnitTracker()
        frame(tr, 9.0, bench=[(0, "a", 1)])
        frame(tr, 9.75, bench=[(0, "a", 1)])
        if buy_at <= seen_at:
            tr.note_purchase(C, buy_at)
            r, _ = frame(tr, seen_at, bench=[(0, "a", 1), (1, "c", 1)])
        else:
            frame(tr, seen_at, bench=[(0, "a", 1), (1, "c", 1)])
            tr.note_purchase(C, buy_at)
            r, _ = frame(tr, buy_at + 0.25, bench=[(0, "a", 1), (1, "c", 1)])
        assert r[1][0] == C, (buy_at, seen_at)


def test_pending_buy_takes_the_leftmost_slot_even_if_a_unit_vanished_in_the_same_frame():
    """같은 프레임에 벤치 유닛 하나가 보드로 가고(사라짐) 산 유닛이 가장 왼쪽 빈 칸에 생기면, 새 칸은 구매이지 옮기기가 아니다."""
    tr = UnitTracker()
    frame(tr, 0.0, bench=[])
    _bought(tr, C, 0.1, [], 0, "c")                          # 칸 0 = 카밀
    tr.note_purchase(D, 1.0)
    # 칸 0 카밀이 보드로 올라가고, 같은 프레임에 산 자야가 칸 0(이제 가장 왼쪽 빈 칸)에 — 직전 빈 칸 기준으로는 칸 1
    r, _ = frame(tr, 1.2, bench=[(1, "d", 1)], board=[((0, 0), "c", 1)])
    assert r[(0, 0)][0] == C and r[1][0] == D
