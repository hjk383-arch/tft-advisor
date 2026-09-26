"""구매·판매·유닛 수 변화 뒤 보드+벤치+특성 패널을 통째로 다시 읽는다(vision 30 보고, 사용자: "유닛을 사면 보드를 다시 읽어줘야 해").

루프는 바뀐 ROI 묶음만 읽고 특성 패널은 `traits_every_s`(3초)마다만 읽는다. 구매 직후에는 벤치 칸 수가 먼저 바뀌고 이름·특성은
늦게 따라왔다. 이제 구매 신호를 보면 `FORCE_REREAD_DELAY_S` 뒤 첫 프레임에서(화면 변화가 없어도) 전체를 다시 읽는다.
"""
from __future__ import annotations

from types import SimpleNamespace

from tft_advisor.app.loop import FORCE_REREAD_DELAY_S, InlineAdviceRunner, LiveLoop
from tft_advisor.app.session import SessionTracker
from tft_advisor.vision.recognizer import DEFAULT_GROUPS

from .conftest import FakeClock, FakeDetector, FakeRecognizer, FakeSource, planning_state, shop_slots

A, B, C, D, E = "DA_18_Xayah", "DA_Cinderling18", "DA_Murkwolf18", "DA_18_Hecarim", "DA_18_Ornn"


class _Advisor:
    def advise(self, state):
        return None

    def reset(self):
        pass

    def close(self):
        pass


class _Rec(FakeRecognizer):
    """상태 + 보드 판독(벤치 칸 수)을 차례로 돌려준다."""

    def __init__(self, states, benches):
        super().__init__(states)
        self.benches = list(benches)
        self.last_board_read = None

    def recognize(self, image, **kw):
        state = super().recognize(image, **kw)
        i = min(len(self.calls) - 1, len(self.benches) - 1)
        n = self.benches[i]
        self.last_board_read = None if n is None else SimpleNamespace(board=(), bench=tuple(range(n)))
        return state


def _loop(settings, states, script, benches=(None,)):
    clock = FakeClock(100.0)
    rec = _Rec(states, benches)
    loop = LiveLoop(source=FakeSource(), recognizer=rec, settings=settings, tracker=SessionTracker(),
                    detector=FakeDetector(script), clock=clock, sleep=clock.sleep)
    advisor = _Advisor()
    loop.advisor = advisor
    loop.runner = InlineAdviceRunner(advisor, loop._on_advice)
    return loop, rec, clock


def test_shop_slot_emptied_forces_full_reread_after_delay(settings):
    shop1 = planning_state(gold=10, shop=shop_slots([A, B, C, D, E]))
    shop2 = planning_state(gold=9, shop=shop_slots([None, B, C, D, E]))
    loop, rec, clock = _loop(settings, [shop1, shop2, shop2], [{"shop"}, {"shop"}, set(), set()])
    loop.step()
    clock.t += settings.vision.traits_every_s / 10     # 특성 주기 안
    loop.step()                                       # 상점 칸이 비었다 → 다시 읽기 예약
    assert len(rec.calls) == 2
    loop.step()                                       # 아직 지연 전 · 화면 변화 없음 → 아무것도 안 읽는다
    assert len(rec.calls) == 2
    clock.t += FORCE_REREAD_DELAY_S + 0.01
    loop.step()                                       # 화면 변화가 없어도 전체 + 특성
    assert len(rec.calls) == 3
    assert rec.calls[2]["groups"] >= set(DEFAULT_GROUPS) | {"traits", "board"}
    assert loop.forced_rereads == 1


def test_bench_count_change_forces_reread(settings):
    st = planning_state()
    loop, rec, clock = _loop(settings, [st], [{"board"}, {"board"}, set()], benches=[2, 3, 3])
    loop.step()
    clock.t += 0.1
    loop.step()                                       # 벤치 2 → 3
    clock.t += FORCE_REREAD_DELAY_S + 0.01
    loop.step()
    assert len(rec.calls) == 3 and "traits" in rec.calls[2]["groups"]


def test_no_purchase_no_extra_reads(settings):
    st = planning_state(shop=shop_slots([A, B, C, D, E]))
    loop, rec, clock = _loop(settings, [st], [{"shop"}, {"board"}, set(), set()], benches=[2, 2])
    loop.step()
    loop.step()
    clock.t += 5.0
    loop.step()
    loop.step()
    assert len(rec.calls) == 2 and loop.forced_rereads == 0


def test_reroll_is_not_a_purchase(settings):
    s1 = planning_state(gold=10, shop=shop_slots([A, B, C, D, E]))
    s2 = planning_state(gold=8, shop=shop_slots([E, D, C, B, A]))
    loop, rec, clock = _loop(settings, [s1, s2], [{"shop"}, {"shop"}, set()])
    loop.step()
    loop.step()
    clock.t += FORCE_REREAD_DELAY_S + 0.01
    loop.step()
    assert len(rec.calls) == 2


def test_qa32_forced_reread_happens_once_and_does_not_loop(settings):
    """QA 32: 구매 신호 한 번 → 다시 읽기 정확히 한 번. 다시 읽은 프레임이 같은 판독이면 새로 예약하지 않는다(5초 동안)."""
    shop1 = planning_state(gold=10, shop=shop_slots([A, B, C, D, E]))
    shop2 = planning_state(gold=9, shop=shop_slots([None, B, C, D, E]))
    loop, rec, clock = _loop(settings, [shop1, shop2, shop2], [{"shop"}, {"shop"}] + [set()] * 60, benches=[2, 3, 3])
    loop.step()
    clock.t += 0.1
    loop.step()                                        # 상점 칸 비움 + 벤치 2 → 3 (신호 둘 = 예약 하나)
    for _ in range(50):                                # 5초, 화면 변화 없음
        clock.t += 0.1
        loop.step()
    assert loop.forced_rereads == 1 and len(rec.calls) == 3


def test_qa32_pin_request_does_not_consume_or_duplicate_forced_reread(settings):
    """QA 32: 다시 읽기 예약 중에 목표 덱을 고정해도(캡처 없이 다시 추천) 예약은 그대로 한 번 실행된다."""
    shop1 = planning_state(gold=10, shop=shop_slots([A, B, C, D, E]))
    shop2 = planning_state(gold=9, shop=shop_slots([None, B, C, D, E]))
    loop, rec, clock = _loop(settings, [shop1, shop2, shop2], [{"shop"}, {"shop"}] + [set()] * 20)
    loop.step()
    clock.t += 0.1
    loop.step()
    grabs = len(rec.calls)
    assert loop.request_pin("some-comp") is True
    assert len(rec.calls) == grabs                     # 고정은 인식을 부르지 않는다
    for _ in range(20):
        clock.t += 0.1
        loop.step()
    assert loop.forced_rereads == 1 and len(rec.calls) == grabs + 1
