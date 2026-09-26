"""목표 덱 고정(31 보고): 버튼 클릭 → 고정/해제, 추천 스레드에서 advisor 반영 + 즉시 다시 추천, 세션 영속, 새 판 초기화."""
from __future__ import annotations

import time

from tft_advisor.app.deck_chooser import DeckButtons, DeckChooser, PinController
from tft_advisor.app.loop import InlineAdviceRunner, LiveLoop, ThreadAdviceRunner
from tft_advisor.app.overlay import OverlayWindow
from tft_advisor.app.pinning import (
    PENDING_LABEL, PINNED_LABEL, apply_pin, deck_choices, header_label, readvise_state, toggle_target,
)
from tft_advisor.app.recog_window import RecogController
from tft_advisor.app.session import SessionTracker
from tft_advisor.contracts import GameState, Recommendation, ScreenMode, TargetComp

from .conftest import FakeClock, FakeDetector, FakeRecognizer, FakeSource, planning_state, sample_recommendation


class PinAdvisor:
    """set_pinned_comp를 지원하는 가짜 advisor. 고정 덱을 맨 앞에 두고 pinned_comp_id를 채운다."""

    def __init__(self) -> None:
        self.pinned: str | None = None
        self.pin_calls: list[tuple[str | None, str]] = []
        self.seen: list[GameState] = []
        self.resets = 0

    def set_pinned_comp(self, comp_id):
        import threading

        self.pin_calls.append((comp_id, threading.current_thread().name))
        self.pinned = comp_id

    @property
    def pinned_comp_id(self):
        return self.pinned

    def advise(self, state):
        self.seen.append(state)
        comps = [TargetComp(comp_id=c, name=f"덱 {c}", score=0.5) for c in ("c1", "c2", "c3")]
        if self.pinned:
            comps.sort(key=lambda c: c.comp_id != self.pinned)
        return Recommendation(jev_used=True, target_comps=comps, pinned_comp_id=self.pinned)

    def reset(self):
        self.resets += 1
        self.pinned = None

    def close(self):
        pass


class OldAdvisor:
    """set_pinned_comp가 없는 advisor(어댑터가 무시해야 한다)."""

    def __init__(self):
        self.seen = []

    def advise(self, state):
        self.seen.append(state)
        return Recommendation(jev_used=True)

    def reset(self):
        pass

    def close(self):
        pass


def make_loop(settings, advisor, tracker=None, states=None, script=None, updates=None):
    clock = FakeClock()
    loop = LiveLoop(source=FakeSource(), recognizer=FakeRecognizer(states or [planning_state()]),
                    settings=settings, tracker=tracker if tracker is not None else SessionTracker(),
                    detector=FakeDetector(script or [{"shop"}]), clock=clock, sleep=clock.sleep,
                    on_update=(updates.append if updates is not None else None))
    loop.advisor = advisor
    loop.runner = InlineAdviceRunner(advisor, loop._on_advice)
    loop._restore_pin()
    return loop


# ---------------------------------------------------------------- 순수 로직
def test_toggle_and_labels():
    assert toggle_target(None, "c1") == "c1"
    assert toggle_target("c1", "c1") is None           # 같은 덱을 다시 누르면 해제
    assert toggle_target("c1", "c2") == "c2"           # 다른 덱을 누르면 그 덱으로 바꾼다
    rec = sample_recommendation()
    assert header_label(None, rec) is None
    assert header_label("c1", rec) == PENDING_LABEL     # 아직 그 기준 추천이 오지 않았다
    assert header_label("c1", rec.model_copy(update={"pinned_comp_id": "c1"})) == PINNED_LABEL
    ch = deck_choices(rec, 3, pinned_id="zz", pinned_name="목록 밖 덱")
    assert [c.comp_id for c in ch] == ["c1", "c2", "zz"] and ch[-1].index == 0


def test_readvise_state_turns_combat_into_planning():
    s = planning_state(screen_mode=ScreenMode.COMBAT)
    assert readvise_state(s).screen_mode == ScreenMode.PLANNING
    s2 = planning_state(screen_mode=ScreenMode.AUGMENT_SELECT)
    assert readvise_state(s2).screen_mode == ScreenMode.AUGMENT_SELECT


def test_adapter_noops_without_method(caplog):
    assert apply_pin(OldAdvisor(), "c1") is False
    a = PinAdvisor()
    assert apply_pin(a, "c1") is True and a.pinned == "c1"


# ---------------------------------------------------------------- 루프
def test_request_pin_readvises_from_latest_state_without_capture(settings):
    adv = PinAdvisor()
    updates = []
    loop = make_loop(settings, adv, updates=updates)
    loop.step()
    assert len(adv.seen) == 1 and loop.last_recommendation.pinned_comp_id is None
    grabs = loop.source.grabbed
    assert loop.request_pin("c2", "덱 c2") is True
    assert loop.source.grabbed == grabs                  # 새 캡처 없음
    assert len(adv.seen) == 2 and adv.pinned == "c2"
    assert loop.last_recommendation.pinned_comp_id == "c2"
    assert loop.last_recommendation.target_comps[0].comp_id == "c2"
    assert updates[-1].kind == "advice"
    loop.request_pin(None)
    assert adv.pinned is None and len(adv.seen) == 3 and loop.pin_readvises == 2


def test_pin_during_combat_recomputes_but_shows_kept_view(settings):
    adv = PinAdvisor()
    updates = []
    loop = make_loop(settings, adv, states=[planning_state(), planning_state(screen_mode=ScreenMode.COMBAT)],
                     script=[{"shop"}, {"shop"}], updates=updates)
    loop.step()
    loop.step()                     # 전투: 추천하지 않는다
    assert len(adv.seen) == 1
    loop.request_pin("c3")
    assert len(adv.seen) == 2 and adv.seen[-1].screen_mode == ScreenMode.PLANNING
    assert updates[-1].kind == "advice" and updates[-1].kept is not None


def test_pin_without_state_only_records(settings):
    adv = PinAdvisor()
    loop = make_loop(settings, adv)
    assert loop.request_pin("c1") is False
    assert adv.seen == [] and loop.pinned_comp_id == "c1"
    loop.step()                     # 첫 추천 직전에 반영
    assert adv.pin_calls[0][0] == "c1" and loop.last_recommendation.pinned_comp_id == "c1"


def test_old_advisor_is_not_broken(settings):
    adv = OldAdvisor()
    loop = make_loop(settings, adv)
    loop.step()
    assert loop.request_pin("c1") is True and len(adv.seen) == 2


def test_thread_runner_applies_pin_on_advice_thread(settings):
    adv = PinAdvisor()
    got = []
    runner = ThreadAdviceRunner(adv, lambda s, r: got.append(r), name="tft-advisor-test")
    try:
        runner.set_pin("c2")
        runner.submit(planning_state())
        deadline = time.time() + 3
        while not got and time.time() < deadline:
            time.sleep(0.01)
        assert got and got[0].pinned_comp_id == "c2"
        assert adv.pin_calls == [("c2", "tft-advisor-test")]      # UI 스레드가 아니라 추천 스레드
    finally:
        runner.close()


def test_session_persists_pin_and_reset_clears(settings, tmp_path):
    path = tmp_path / "session.json"
    t = SessionTracker(path)
    adv = PinAdvisor()
    loop = make_loop(settings, adv, tracker=t)
    loop.step()
    loop.request_pin("c2", "덱 c2")
    # 앱 재시작: 세션에서 고정을 이어받고 첫 추천부터 반영한다
    t2 = SessionTracker(path)
    assert t2.load() and t2.data.pinned_comp_id == "c2" and t2.data.pinned_comp_name == "덱 c2"
    adv2 = PinAdvisor()
    loop2 = make_loop(settings, adv2, tracker=t2)
    assert loop2.pinned_comp_id == "c2"
    loop2.step()
    assert adv2.pin_calls[0][0] == "c2" and loop2.last_recommendation.pinned_comp_id == "c2"
    # 새 판: 세션·advisor 모두 풀린다
    loop2._do_reset(planning_state(screen_mode=ScreenMode.LOADING), {"stage"}, "test")
    assert loop2.pinned_comp_id is None
    t3 = SessionTracker(path)
    assert t3.load() and t3.data.pinned_comp_id is None
    loop2.last_state = planning_state()
    loop2.runner.submit(planning_state())
    assert adv2.pinned is None


# ---------------------------------------------------------------- Qt(offscreen)
def test_chooser_buttons_pin_and_unpin(qapp):
    calls = []
    ctl = PinController(lambda cid, name: calls.append((cid, name)))
    chooser = DeckChooser(ctl)
    ctl.set_rec(sample_recommendation())
    btns = chooser.buttons.buttons
    assert [b.property("comp_id") for b in btns] == ["c1", "c2"]
    from PySide6.QtCore import Qt

    assert chooser.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert not (chooser.windowFlags() & Qt.WindowType.WindowTransparentForInput)
    chooser.buttons.button_for("c2").click()
    assert calls == [("c2", "가짜 덱 B")] and ctl.pinned == "c2"
    assert "📌" in chooser.buttons.button_for("c2").text() and "고정" in chooser.buttons.button_for("c2").text()
    chooser.buttons.button_for("c2").click()          # 다시 누르면 해제
    assert calls[-1] == (None, None) and ctl.pinned is None
    assert "📌" not in chooser.buttons.button_for("c2").text()
    chooser.deleteLater()


def test_overlay_header_menu_and_recog_window_share_state(qapp, settings, tmp_path):
    calls = []
    ctl = PinController(lambda cid, name: calls.append(cid))
    w = OverlayWindow(settings, state_dir=tmp_path)
    w.attach_pin(ctl)
    w.show_overlay()
    rec = sample_recommendation()
    w.set_data(planning_state(), rec)
    assert w.deck_chooser is not None and w.deck_chooser.isVisible()
    # 트레이 하위 메뉴
    m = w.menu()
    sub = next(a.menu() for a in m.actions() if a.menu() is not None and a.text() == "목표 덱 고정")
    acts = [a for a in sub.actions() if a.text()]
    assert [a.text() for a in acts][-1] == "고정 해제" and not acts[-1].isEnabled()
    acts[0].trigger()
    assert calls == ["c1"] and PENDING_LABEL in w.body.text()
    w.set_data(planning_state(), rec.model_copy(update={"pinned_comp_id": "c1"}))
    assert PINNED_LABEL in w.body.text()
    # 인식 확인 창의 같은 버튼 줄
    rc = RecogController(settings, state_dir=tmp_path, cli=False, names=w.names, pin=ctl)
    rc.set_enabled(True, persist=False)
    db = rc.window.deck_buttons
    assert isinstance(db, DeckButtons) and "📌" in db.button_for("c1").text()
    db.button_for("c1").click()                        # 해제 → 오버레이 띠도 같이 바뀐다
    assert calls[-1] is None and "📌" not in w.deck_chooser.buttons.button_for("c1").text()
    # 새 판(reset 갱신) → 고정 표시가 사라진다
    ctl.click("c2")
    from tft_advisor.app.loop import LoopUpdate

    w._on_update_main(LoopUpdate(kind="reset", state=planning_state()))
    assert ctl.pinned is None and not w.deck_chooser.isVisible()
    rc.window.close_window()
    w.quit_handle.hide()
    w.deck_chooser.hide()
    w.hide()
    w.deleteLater()
