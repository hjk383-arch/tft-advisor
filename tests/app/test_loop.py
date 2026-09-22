"""실시간 루프: 변화 감지 연결, 묶음 선택, 화면 모드 전환, 세션 추적, mock에서 네트워크 없음."""
from __future__ import annotations

import socket
import threading

import pytest

from tft_advisor.app.loop import InlineAdviceRunner, LiveLoop, LoopUpdate, ThreadAdviceRunner
from tft_advisor.app.session import SessionTracker
from tft_advisor.contracts import GameState, Recommendation, ScreenMode
from tft_advisor.vision.recognizer import DEFAULT_GROUPS

from .conftest import FakeClock, FakeDetector, FakeRecognizer, FakeSource, planning_state, shop_slots


class FakeAdvisor:
    """호출을 기록하는 추천기. `advise`는 준 목록을 차례로 돌려준다."""

    def __init__(self, results: list[Recommendation | None] | None = None) -> None:
        self.results = list(results or [])
        self.seen: list[GameState] = []
        self.resets = 0
        self.closed = False

    def advise(self, state: GameState) -> Recommendation | None:
        self.seen.append(state)
        if not self.results:
            return Recommendation(jev_used=True)
        return self.results[min(len(self.seen) - 1, len(self.results) - 1)]

    def reset(self) -> None:
        self.resets += 1

    def close(self) -> None:
        self.closed = True


def make_loop(settings, states, script, advisor=None, updates=None, clock=None, tracker=None):
    advisor = advisor or FakeAdvisor()
    rec = FakeRecognizer(states)
    clock = clock or FakeClock()
    loop = LiveLoop(source=FakeSource(), recognizer=rec, settings=settings,
                    tracker=tracker if tracker is not None else SessionTracker(),
                    detector=FakeDetector(script), clock=clock, sleep=clock.sleep,
                    on_update=(updates.append if updates is not None else None))
    loop.advisor = advisor
    loop.runner = InlineAdviceRunner(advisor, loop._on_advice)
    return loop, rec, advisor


def test_no_change_means_no_recognition(settings):
    loop, rec, advisor = make_loop(settings, [planning_state()], [set(), set()])
    assert loop.step() is None
    assert loop.step() is None
    assert rec.calls == [] and advisor.seen == []


def test_partial_groups_are_passed_through(settings):
    loop, rec, _ = make_loop(settings, [planning_state()], [{"shop"}])
    loop.step()
    assert rec.calls[0]["groups"] >= {"shop"}
    assert "players" not in rec.calls[0]["groups"]


def test_stage_change_triggers_full_recognition(settings):
    loop, rec, _ = make_loop(settings, [planning_state()], [{"stage"}])
    loop.step()
    assert rec.calls[0]["groups"] >= set(DEFAULT_GROUPS)


def test_traits_group_added_on_its_own_period(settings):
    clock = FakeClock()
    loop, rec, _ = make_loop(settings, [planning_state()], [{"shop"}, {"shop"}, {"shop"}], clock=clock)
    loop.step()                                    # t=0: 첫 인식에 traits 포함
    assert "traits" in rec.calls[0]["groups"]
    loop.step()                                    # 주기 전 → 빠진다
    assert "traits" not in rec.calls[1]["groups"]
    clock.t += settings.vision.traits_every_s + 0.1
    loop.step()
    assert "traits" in rec.calls[2]["groups"]


def test_planning_advises_and_keeps_result(settings):
    updates: list[LoopUpdate] = []
    loop, _, advisor = make_loop(settings, [planning_state()], [{"shop"}], updates=updates)
    loop.step()
    assert len(advisor.seen) == 1
    assert loop.last_recommendation is not None
    assert [u.kind for u in updates] == ["advice", "recognized"]   # 추천 콜백이 먼저 돌아온다(동기 실행기)


@pytest.mark.parametrize("mode", [ScreenMode.COMBAT, ScreenMode.ITEM_SELECT, ScreenMode.UNKNOWN])
def test_keep_modes_do_not_recompute(settings, mode):
    """전투·아이템 선택·알 수 없음: 직전 추천을 그대로 두고 advisor를 부르지 않는다."""
    states = [planning_state(), GameState(screen_mode=mode, stage="2-3")]
    updates: list[LoopUpdate] = []
    loop, _, advisor = make_loop(settings, states, [{"shop"}, {"shop"}], updates=updates)
    loop.step()
    first = loop.last_recommendation
    loop.step()
    assert len(advisor.seen) == 1                      # 두 번째 프레임은 추천을 다시 만들지 않는다
    assert updates[-1].kind == "kept"
    assert updates[-1].recommendation is first
    assert loop.last_state.gold == 10                  # 상태는 계속 병합한다


@pytest.mark.parametrize("mode", [ScreenMode.LOADING, ScreenMode.GAME_OVER])
def test_reset_modes_clear_everything(settings, mode):
    states = [planning_state(), GameState(screen_mode=mode)]
    updates: list[LoopUpdate] = []
    tracker = SessionTracker()
    loop, _, advisor = make_loop(settings, states, [{"shop"}, {"stage"}], updates=updates, tracker=tracker)
    loop.step()
    loop.step()
    assert advisor.resets == 1
    assert loop.last_recommendation is None and loop.last_state is None
    assert tracker.state is None and tracker.data.frames == 0
    assert updates[-1].kind == "reset"


def test_state_is_merged_across_frames(settings):
    """두 번째 프레임이 상점만 읽어도 골드·레벨이 유지된다."""
    states = [planning_state(gold=50, shop=shop_slots(["A"] * 5)),
              GameState(screen_mode=ScreenMode.PLANNING, stage="2-4", shop=shop_slots(["B"] * 5))]
    loop, _, advisor = make_loop(settings, states, [{"stage"}, {"shop"}])
    loop.step()
    loop.step()
    assert advisor.seen[-1].gold == 50 and advisor.seen[-1].shop[0].id == "B"


def test_recognition_error_becomes_update_not_crash(settings):
    class Boom(FakeRecognizer):
        def recognize(self, *a, **kw):
            raise RuntimeError("OCR 폭발")

    updates: list[LoopUpdate] = []
    loop = LiveLoop(source=FakeSource(), recognizer=Boom([]), settings=settings,
                    detector=FakeDetector([{"shop"}]), on_update=updates.append)
    assert loop.step().kind == "error"
    assert "OCR 폭발" in updates[-1].message


def test_run_stops_when_source_is_exhausted(settings):
    clock = FakeClock()
    loop, _, _ = make_loop(settings, [planning_state()], [{"shop"}] * 3, clock=clock)
    loop.source = FakeSource(count=3)
    loop.run(threading.Event(), max_frames=None)
    assert loop.source.grabbed == 3
    assert clock.slept and all(s <= 1.0 / settings.vision.capture_fps for s in clock.slept)


def test_run_respects_stop_event(settings):
    stop = threading.Event()
    stop.set()
    loop, rec, _ = make_loop(settings, [planning_state()], [{"shop"}])
    loop.run(stop)
    assert rec.calls == []


def test_thread_runner_keeps_only_latest_state(settings):
    done = threading.Event()
    got: list[GameState] = []

    advisor = FakeAdvisor()
    runner = ThreadAdviceRunner(advisor, lambda state, rec: (got.append(state), done.set()))
    try:
        runner.submit(planning_state(gold=1))
        runner.submit(planning_state(gold=2))
        assert done.wait(3.0)
    finally:
        runner.close()
    assert advisor.closed
    assert got and got[-1].gold in (1, 2)
    assert len(advisor.seen) <= 2


def test_mock_advisor_makes_no_network_call(settings, monkeypatch):
    """`--jev mock`(기본)에서는 소켓을 열지 않는다."""
    from tft_advisor.advisor import MockJevBackend, create_advisor

    advisor = create_advisor("mock", settings=settings)
    assert advisor.backend_name == "mock" and isinstance(advisor.gateway.backend, MockJevBackend)

    blocked: list = []
    real_connect = socket.socket.connect

    def guard(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr[0] not in ("127.0.0.1", "::1", "localhost"):
            blocked.append(addr)
            raise OSError(f"네트워크 차단: {addr}")
        return real_connect(self, addr, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guard)
    loop, _, _ = make_loop(settings, [planning_state()], [{"shop"}], advisor=advisor)
    try:
        loop.step()
    finally:
        advisor.close()
    assert blocked == []
    assert loop.last_recommendation is not None and loop.last_recommendation.jev_used
