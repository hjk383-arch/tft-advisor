"""live 5(37 보고): 판 사이에 다시 켠 앱의 세션 이어받기 · 장부와 무관한 골드 변화를 애매로 세지 않기."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tft_advisor.app.ledger import XP_WAIT_S
from tft_advisor.app.session import SessionTracker
from tft_advisor.contracts import ScreenMode, UnitOnBoard

from .conftest import FakeClock, planning_state
from .test_unit_ledger import C1, C1B, C2, C3, C4, cost, shop_of

GROUPS = ("stage", "hud", "shop")
SHOP = [C3, C1, C2, C1B, C4]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(100.0)


@pytest.fixture
def tracker(clock, tmp_path) -> SessionTracker:
    return SessionTracker(tmp_path / "session.json", clock=clock)


def feed(tracker, clock, dt=0.3, groups=GROUPS, **kw):
    clock.t += dt
    return tracker.observe(planning_state(**kw), groups=groups)


def kinds(tracker) -> list[str]:
    return [e.kind for e in tracker.data.units.events]


# ---------------------------------------------------------------- 장부
def test_xp_bar_read_late_is_still_an_xp_buy(tracker, clock):
    feed(tracker, clock, gold=30, level=5, xp=(2, 20), shop=shop_of(SHOP))
    feed(tracker, clock, gold=26, level=5, xp=(2, 20), shop=shop_of(SHOP))          # 골드만 먼저 -4
    for _ in range(4):                                                                # 2.8초: 예전에는 여기서 애매
        feed(tracker, clock, dt=0.7, gold=26, level=5, xp=(2, 20), shop=shop_of(SHOP))
    feed(tracker, clock, gold=26, level=5, xp=(6, 20), shop=shop_of(SHOP))           # 막대가 늦게 바뀜
    assert tracker.data.units.ambiguous == 0 and "xp" in kinds(tracker)


def test_even_loss_without_any_xp_or_shop_evidence_stays_ambiguous(tracker, clock):
    feed(tracker, clock, gold=30, level=5, xp=(2, 20), shop=shop_of(SHOP))
    feed(tracker, clock, gold=18, level=5, xp=(2, 20), shop=shop_of(SHOP))
    for _ in range(5):
        feed(tracker, clock, dt=XP_WAIT_S / 2, gold=18, level=5, xp=(2, 20), shop=shop_of(SHOP))
    assert tracker.data.units.ambiguous == 1


def test_reroll_while_shop_unread_is_not_ambiguous(tracker, clock):
    feed(tracker, clock, gold=20, level=5, xp=(2, 20), shop=shop_of(SHOP))
    feed(tracker, clock, gold=18, level=5, xp=(2, 20), shop=None)                    # 상점을 못 읽는 사이 리롤
    for _ in range(3):
        feed(tracker, clock, dt=1.0, gold=18, level=5, xp=(2, 20), shop=None)
    feed(tracker, clock, gold=18, level=5, xp=(2, 20), shop=shop_of([C4, C2, C1, C3, C1B]))
    assert tracker.data.units.ambiguous == 0 and tracker.data.units.copies == {}


def test_buy_plus_late_xp_commits_the_buy(tracker, clock):
    feed(tracker, clock, gold=30, level=5, xp=(2, 20), shop=shop_of(SHOP))
    feed(tracker, clock, gold=30 - cost(C1) - 4, level=5, xp=(2, 20), shop=shop_of([C3, None, C2, C1B, C4]))
    for _ in range(4):
        feed(tracker, clock, dt=0.8, gold=30 - cost(C1) - 4, level=5, xp=(2, 20),
             shop=shop_of([C3, None, C2, C1B, C4]))
    feed(tracker, clock, gold=30 - cost(C1) - 4, level=5, xp=(6, 20), shop=shop_of([C3, None, C2, C1B, C4]))
    assert tracker.data.units.copies == {C1: 1} and tracker.data.units.ambiguous == 0


def test_round_income_after_stage_change_with_gold_unread(tracker, clock):
    feed(tracker, clock, gold=12, stage="4-2", shop=shop_of(SHOP))
    feed(tracker, clock, gold=None, stage="4-3", shop=shop_of([C4, C2, C1, C3, C1B]))    # 전환 프레임: 골드 못 읽음
    feed(tracker, clock, gold=21, stage="4-3", shop=shop_of([C4, C2, C1, C3, C1B]))      # +9 수입
    feed(tracker, clock, dt=5, gold=21, stage="4-3", shop=shop_of([C4, C2, C1, C3, C1B]))
    assert tracker.data.units.ambiguous == 0


def test_income_inside_round_when_units_did_not_drop(tracker, clock):
    units = [UnitOnBoard(id=C1, star=1, hex=(0, 0), confidence=0.9)]
    tracker.add_unit(C1B, source="carousel")                 # 판매가 1인 유닛이 있다
    groups = ("stage", "hud", "shop", "board")
    feed(tracker, clock, groups=groups, gold=10, shop=shop_of(SHOP), board=units, bench=[])
    feed(tracker, clock, groups=groups, gold=11, shop=shop_of(SHOP), board=units, bench=[])     # 구슬 +1, 유닛 그대로
    feed(tracker, clock, dt=5, groups=groups, gold=11, shop=shop_of(SHOP), board=units, bench=[])
    assert tracker.data.units.ambiguous == 0 and "income" in kinds(tracker)
    assert tracker.data.units.copies == {C1B: 1}


def test_gain_that_matches_no_owned_unit_is_income(tracker, clock):
    tracker.add_unit(C4, source="carousel")                  # 판매가 4 이상
    feed(tracker, clock, gold=10, shop=shop_of(SHOP))
    feed(tracker, clock, gold=13, shop=shop_of(SHOP))        # +3: 장부 유닛 판매가와 안 맞는다
    feed(tracker, clock, dt=5, gold=13, shop=shop_of(SHOP))
    assert tracker.data.units.ambiguous == 0 and tracker.data.units.copies == {C4: 1}


def test_odd_loss_without_a_vanished_slot_is_still_ambiguous(tracker, clock):
    feed(tracker, clock, gold=20, level=5, xp=(2, 20), shop=shop_of(SHOP))
    feed(tracker, clock, gold=17, level=5, xp=(2, 20), shop=shop_of(SHOP))
    feed(tracker, clock, dt=XP_WAIT_S + 1, gold=17, level=5, xp=(2, 20), shop=shop_of(SHOP))
    assert tracker.data.units.ambiguous == 1


def test_sale_with_unit_drop_but_unknown_unit_stays_ambiguous(tracker, clock):
    tracker.add_unit(C1, source="carousel")
    tracker.add_unit(C1B, source="carousel")
    groups = ("stage", "hud", "shop", "board")
    two = [UnitOnBoard(id="UNKNOWN", star=1, bench_slot=0, confidence=0.2),
           UnitOnBoard(id="UNKNOWN", star=1, bench_slot=1, confidence=0.2)]
    feed(tracker, clock, groups=groups, gold=10, shop=shop_of(SHOP), board=[], bench=two)
    feed(tracker, clock, groups=groups, gold=11, shop=shop_of(SHOP), board=[], bench=two[:1])
    feed(tracker, clock, dt=5, groups=groups, gold=11, shop=shop_of(SHOP), board=[], bench=two[:1])
    assert tracker.data.units.ambiguous == 1 and tracker.data.units.copies == {C1: 1, C1B: 1}


# ---------------------------------------------------------------- 새 판 판단
def saved(tmp_path, *, stage="4-2", minutes_ago=2.0, hp=None) -> SessionTracker:
    t = SessionTracker(tmp_path / "session.json")
    t.data.stage = stage
    t.data.hp = hp
    t.data.frames = 50
    t.add_unit(C3, source="carousel")
    t.save()
    raw = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    raw["updated_at"] = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    (tmp_path / "session.json").write_text(json.dumps(raw), encoding="utf-8")
    return SessionTracker(tmp_path / "session.json")


def test_inherited_session_then_earlier_stage_is_a_new_game(tmp_path):
    t = saved(tmp_path, stage="2-4")
    assert t.load() and t.inherited_stage == "2-4"
    assert "이어받은 세션" in (t.new_game_reason(planning_state(stage="2-1")) or "")
    assert t.new_game_reason(planning_state(stage="2-5")) is None
    t.observe(planning_state(stage="2-5"), groups=GROUPS)       # 같은 판이었다 → 이후 같은 단계 역행은 오독으로 본다
    assert t.inherited_stage is None and t.new_game_reason(planning_state(stage="2-2")) is None


def test_stage_number_going_back_is_a_new_game(tmp_path):
    t = SessionTracker(tmp_path / "s.json")
    t.data.stage = "4-2"
    assert "되돌아감" in t.new_game_reason(planning_state(stage="2-1"))
    assert t.new_game_reason(planning_state(stage="4-1")) is None     # 같은 단계 안: 오독일 수 있다


def test_hp_jump_is_a_new_game(tmp_path):
    t = SessionTracker(tmp_path / "s.json")
    t.observe(planning_state(stage="3-2", hp=50), groups=("stage", "players"))
    assert t.data.hp == 50
    assert "체력" in t.new_game_reason(planning_state(stage="3-2", hp=100))
    assert t.new_game_reason(planning_state(stage="3-5", hp=45)) is None


def test_old_session_after_a_gap_is_archived_not_inherited(tmp_path):
    t = saved(tmp_path, stage="5-1", minutes_ago=45)
    assert t.load() is False and t.data.stage is None and not t.data.units.copies
    assert t.last_archive is not None and t.last_archive.is_file()


def test_loop_resets_when_restarted_between_games(settings, tmp_path):
    from .test_loop import make_loop

    t = saved(tmp_path, stage="4-2")
    assert t.load()
    states = [planning_state(stage="2-1", hp=100)] * 4
    loop, _, _ = make_loop(settings, states, [{"stage"}] * 4, tracker=t)
    updates = []
    loop.on_update = updates.append
    for _ in range(4):
        loop.clock.t += 1.5
        loop.step()
    assert any(u.kind == "reset" for u in updates)
    assert t.data.units.copies == {} and t.data.stage == "2-1"


def test_loop_keeps_inherited_session_mid_game(settings, tmp_path):
    from .test_loop import make_loop

    t = saved(tmp_path, stage="3-2")
    assert t.load()
    loop, _, _ = make_loop(settings, [planning_state(stage="3-3")] * 3, [{"stage"}] * 3, tracker=t)
    updates = []
    loop.on_update = updates.append
    for _ in range(3):
        loop.clock.t += 1.5
        loop.step()
    assert not any(u.kind == "reset" for u in updates) and t.data.units.copies == {C3: 1}
    assert planning_state().screen_mode == ScreenMode.PLANNING


def test_sale_confirmed_when_units_drop(tracker, clock):
    tracker.add_unit(C3, source="carousel")
    groups = ("stage", "hud", "shop", "board")
    one = [UnitOnBoard(id=C3, star=1, bench_slot=0, confidence=0.9)]
    feed(tracker, clock, groups=groups, gold=10, shop=shop_of(SHOP), board=[], bench=one)
    feed(tracker, clock, groups=groups, gold=10 + cost(C3), shop=shop_of(SHOP), board=[], bench=[])
    feed(tracker, clock, dt=5, groups=groups, gold=10 + cost(C3), shop=shop_of(SHOP), board=[], bench=[])
    assert tracker.data.units.copies == {} and tracker.data.units.ambiguous == 0


def test_recog_window_labels_only_ledger_units_as_ledger():
    from dataclasses import dataclass

    from tft_advisor.app.names import NameBook
    from tft_advisor.app.recog_view import ledger_unplaced

    @dataclass
    class Read:
        board: tuple = ()
        bench: tuple = ()
        unplaced: tuple = ()

    names = NameBook()
    st = planning_state(board=[UnitOnBoard(id=C3, star=1, hex=None, confidence=0.8)],
                        bench=[UnitOnBoard(id=C1, star=1, bench_slot=None, confidence=1.0)])
    note = ledger_unplaced(st, Read(), names, ledger_ids=(C1,))
    assert note == f"장부 보유(자리 미상): {names.name(C1)} / 화면 확인(자리 미상): {names.name(C3)}"
    assert ledger_unplaced(st, Read(), names).startswith("장부 보유(자리 미상): ")   # 루프 정보가 없으면 예전 그대로
