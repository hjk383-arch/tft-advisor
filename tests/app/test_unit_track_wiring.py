"""루프 → 유닛 정체 추적기 연결(vision 35 · QA 36 F1b): 장부 구매는 **프레임 캡처 시각**으로, 판매는 대기 정체 지우기로, 새 판은 초기화."""
from __future__ import annotations

from datetime import UTC, datetime

from tft_advisor.app.loop import InlineAdviceRunner, LiveLoop
from tft_advisor.app.session import SessionTracker

from .conftest import FakeClock, FakeDetector, FakeRecognizer, FakeSource, planning_state, shop_slots

XA, CI = "DA_18_Xayah", "DA_Cinderling18"


class _Spy:
    def __init__(self):
        self.buys, self.sales, self.resets = [], [], 0

    def note_purchase(self, cid, at=None, source="ledger"):
        self.buys.append((cid, at))

    def note_sale(self, cid):
        self.sales.append(cid)

    def reset(self):
        self.resets += 1


class _Advisor:
    def advise(self, state):
        return None

    def reset(self):
        pass

    def close(self):
        pass


def test_ledger_buy_is_forwarded_with_the_frame_capture_time(settings):
    cap0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    cap1 = datetime(2026, 9, 25, 12, 0, 1, tzinfo=UTC)
    s0 = planning_state(gold=10, shop=shop_slots([XA, CI, None, None, None]), captured_at=cap0)
    s1 = planning_state(gold=9, shop=shop_slots([None, CI, None, None, None]), captured_at=cap1)
    rec = FakeRecognizer([s0, s1])
    spy = _Spy()
    rec.unit_tracker = spy
    clock = FakeClock(100.0)
    loop = LiveLoop(source=FakeSource(), recognizer=rec, settings=settings, tracker=SessionTracker(clock=clock),
                    detector=FakeDetector([{"shop"}, {"shop"}]), clock=clock, sleep=clock.sleep)
    loop.runner = InlineAdviceRunner(_Advisor(), loop._on_advice)
    loop.step()
    loop.step()
    assert spy.buys == [(XA, cap1.timestamp())]


def test_new_game_resets_the_tracker(settings):
    rec = FakeRecognizer([planning_state()])
    spy = _Spy()
    rec.unit_tracker = spy
    loop = LiveLoop(source=FakeSource(), recognizer=rec, settings=settings, tracker=SessionTracker(),
                    detector=FakeDetector([]), clock=FakeClock(), sleep=lambda s: None)
    loop._do_reset(planning_state(), ["stage"], "test")
    assert spy.resets == 1
