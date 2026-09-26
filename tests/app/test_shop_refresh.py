"""상점이 바뀌면(라운드 시작·새로고침, 전투 중 포함) 상점 추천을 다시 계산한다 — 목표 덱은 고정.

- 전투(KEEP_MODES)에서 상점을 읽었고 추천이 모르는 새 상품이 있으면 `submit_shop` → 추천 스레드에서 `rescore_shop`.
- 산 칸(빈 칸)·못 읽은 칸만 바뀐 것은 다시 계산하지 않는다.
- 준비 단계의 상점만 바뀐 프레임은 새 추천(캐시 적중 아님)을 만든다.
"""
from __future__ import annotations

import threading

import pytest

from tft_advisor.app.loop import InlineAdviceRunner, LiveLoop, LoopUpdate, ThreadAdviceRunner, rescore_shop
from tft_advisor.app.report import KeptInfo, shop_needs_rescore
from tft_advisor.app.session import SessionTracker
from tft_advisor.contracts import (
    GameState, Recommendation, ScreenMode, ShopAdvice, ShopSlotKind, TargetComp,
)

from .conftest import FakeClock, FakeDetector, FakeRecognizer, FakeSource, planning_state, shop_slots

A = ["DA_18_A1", "DA_18_A2", "DA_18_A3", "DA_18_A4", "DA_18_A5"]
B = ["DA_18_B1", "DA_18_B2", "DA_18_B3", "DA_18_B4", "DA_18_B5"]


def rec_for(ids, comp="c1", score=0.5) -> Recommendation:
    return Recommendation(
        jev_used=True, target_comps=[TargetComp(comp_id=comp, name=f"덱 {comp}", score=score)],
        shop=[ShopAdvice(slot=i, kind=ShopSlotKind.CHAMPION, offer_id=cid, buy=i == 0, score=0.5)
              for i, cid in enumerate(ids) if cid is not None])


class ShopAdvisor:
    """advise는 그 상태의 상점으로 추천(목표 덱은 매번 다른 이름 — 고정되는지 보려고), rescore_shop은 기록만."""

    def __init__(self, with_rescore: bool = True) -> None:
        self.seen: list[GameState] = []
        self.rescored: list[tuple[GameState, Recommendation | None]] = []
        if not with_rescore:
            self.rescore_shop = None   # type: ignore[assignment]

    def advise(self, state: GameState) -> Recommendation:
        self.seen.append(state)
        ids = [s.id for s in (state.shop or [])]
        return rec_for(ids, comp=f"c{len(self.seen)}")

    def rescore_shop(self, state: GameState, previous: Recommendation | None) -> Recommendation:
        self.rescored.append((state, previous))
        new = rec_for([s.id for s in state.shop])
        return previous.model_copy(update={"shop": new.shop}) if previous is not None else new

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


def combat(ids, **kw) -> GameState:
    return GameState(screen_mode=ScreenMode.COMBAT, stage="2-3", shop=shop_slots(ids), **kw)


def make(settings, states, script, advisor):
    updates: list[LoopUpdate] = []
    clock = FakeClock()
    loop = LiveLoop(source=FakeSource(), recognizer=FakeRecognizer(states), settings=settings,
                    tracker=SessionTracker(), detector=FakeDetector(script), clock=clock, sleep=clock.sleep,
                    on_update=updates.append)
    loop.advisor = advisor
    loop.runner = InlineAdviceRunner(advisor, loop._on_advice, on_shop=loop._on_shop_advice)
    return loop, updates


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def test_needs_rescore_only_for_new_offers():
    rec = rec_for(A)
    assert not shop_needs_rescore(rec, combat(A))
    assert not shop_needs_rescore(rec, combat([None, *A[1:]]))            # 산 칸 → 빈 칸
    assert shop_needs_rescore(rec, combat(B))                               # 새로고침
    assert shop_needs_rescore(rec, combat([A[0], A[1], "DA_18_X", A[3], A[4]]))
    assert shop_needs_rescore(rec_for([None] * 5), combat(A))               # 추천에 상점이 없었다(증강 화면 뒤)
    assert not shop_needs_rescore(None, combat(A))


def test_kept_info_labels():
    k = KeptInfo(ScreenMode.COMBAT)
    assert k.shop_label == k.label and "직전 추천" in k.label
    f = KeptInfo(ScreenMode.COMBAT, shop_fresh=True)
    assert "새 상점 기준" in f.shop_label and "다시 계산했습니다" in f.note()


# ---------------------------------------------------------------------------
# 루프
# ---------------------------------------------------------------------------


def test_combat_reroll_rescores_shop_and_keeps_target_decks(settings):
    advisor = ShopAdvisor()
    loop, updates = make(settings, [planning_state(shop=shop_slots(A)), combat(B)], [{"shop"}, {"shop"}], advisor)
    loop.step()
    first = loop.last_recommendation
    loop.step()
    assert len(advisor.seen) == 1                        # 전체 추천은 다시 하지 않는다
    assert len(advisor.rescored) == 1 and advisor.rescored[0][1] is first
    rec = loop.last_recommendation
    assert [a.offer_id for a in rec.shop] == B           # 상점은 새 상점 기준
    assert rec.target_comps == first.target_comps        # 목표 덱 고정
    adv = [u for u in updates if u.kind == "advice"][-1]
    assert adv.kept is not None and adv.kept.shop_fresh and [a.offer_id for a in adv.recommendation.shop] == B
    kept = updates[-1]
    assert kept.kind == "kept" and kept.kept.shop_fresh and len(kept.recommendation.shop) == 5
    assert loop.shop_rescores == 1


def test_buying_in_combat_does_not_rescore(settings):
    advisor = ShopAdvisor()
    loop, updates = make(settings, [planning_state(shop=shop_slots(A)), combat([None, *A[1:]])],
                         [{"shop"}, {"shop"}], advisor)
    loop.step()
    loop.step()
    assert advisor.rescored == [] and loop.shop_rescores == 0
    assert updates[-1].kept.bought == 1 and not updates[-1].kept.shop_fresh


def test_same_new_shop_is_rescored_once(settings):
    advisor = ShopAdvisor()
    loop, _ = make(settings, [planning_state(shop=shop_slots(A)), combat(B), combat(B), combat([None, *B[1:]])],
                   [{"shop"}] * 4, advisor)
    for _ in range(4):
        loop.step()
    assert len(advisor.rescored) == 1


def test_shop_not_read_this_frame_does_not_rescore(settings):
    advisor = ShopAdvisor()
    loop, _ = make(settings, [planning_state(shop=shop_slots(A)), combat(B)], [{"shop"}, {"hud"}], advisor)
    loop.step()
    loop.step()
    assert advisor.rescored == []


def test_low_confidence_shop_does_not_rescore(settings):
    advisor = ShopAdvisor()
    low = combat(B, confidence={"shop": 0.2})
    loop, _ = make(settings, [planning_state(shop=shop_slots(A)), low], [{"shop"}, {"shop"}], advisor)
    loop.step()
    loop.step()
    assert advisor.rescored == []


def test_full_advice_after_rescore_clears_fresh_flag(settings):
    advisor = ShopAdvisor()
    loop, updates = make(settings, [planning_state(shop=shop_slots(A)), combat(B),
                                    planning_state(stage="2-4", shop=shop_slots(B))],
                         [{"shop"}, {"shop"}, {"stage"}], advisor)
    for _ in range(3):
        loop.step()
    assert loop.shop_fresh is False and len(advisor.seen) == 2


def test_planning_shop_only_change_makes_a_fresh_recommendation(settings):
    advisor = ShopAdvisor()
    loop, _ = make(settings, [planning_state(shop=shop_slots(A)), planning_state(shop=shop_slots(B))],
                   [{"shop"}, {"shop"}], advisor)
    loop.step()
    loop.step()
    assert len(advisor.seen) == 2 and [s.id for s in advisor.seen[-1].shop] == B
    assert [a.offer_id for a in loop.last_recommendation.shop] == B


def test_real_mock_advisor_shop_change_is_not_a_cache_hit(settings):
    """실제 Advisor(mock): 상점만 다른 준비 단계 두 상태 → state_hash가 달라 캐시를 쓰지 않는다."""
    from tft_advisor.advisor import create_advisor
    from tft_advisor.static_data import load_static

    static = load_static(settings.app.set_number)
    champs = [c["apiName"] for c in static._load("champions") if c.get("cost") == 1][:10]
    if len(champs) < 10:
        pytest.skip("1코스트 챔피언 데이터 부족")
    advisor = create_advisor("mock", settings=settings)
    try:
        r1 = advisor.advise(planning_state(shop=shop_slots(champs[:5])))
        r2 = advisor.advise(planning_state(shop=shop_slots(champs[5:])))
    finally:
        advisor.close()
    assert r1 is not None and r2 is not None and r1.state_hash != r2.state_hash
    assert {a.offer_id for a in r2.shop} <= set(champs[5:]) and r2.shop


# ---------------------------------------------------------------------------
# 러너 · 어댑터
# ---------------------------------------------------------------------------


def test_adapter_without_advisor_api_moves_only_the_shop(settings):
    advisor = ShopAdvisor(with_rescore=False)
    prev = rec_for(A, comp="keep")
    out = rescore_shop(advisor, combat(B), prev)
    assert advisor.seen[-1].screen_mode == ScreenMode.PLANNING    # 준비 단계로 한 번 계산
    assert [a.offer_id for a in out.shop] == B and out.target_comps == prev.target_comps


def test_thread_runner_runs_shop_job_and_full_submit_supersedes(settings):
    advisor = ShopAdvisor()
    got: list = []
    done = threading.Event()

    def on_shop(state, rec):
        got.append(("shop", rec))
        done.set()

    runner = ThreadAdviceRunner(advisor, lambda s, r: got.append(("full", r)), on_shop=on_shop)
    try:
        runner.submit_shop(combat(B), rec_for(A))
        assert done.wait(3.0)
    finally:
        runner.close()
    assert got[0][0] == "shop" and [a.offer_id for a in got[0][1].shop] == B

    advisor2 = ShopAdvisor()
    runner = ThreadAdviceRunner.__new__(ThreadAdviceRunner)   # 스레드 없이 대기열 규칙만
    runner._lock = threading.Lock()
    runner._wake = threading.Event()
    runner._pending = planning_state()
    runner._pending_shop = None
    runner.submit_shop(combat(B), rec_for(A))
    assert runner._pending_shop is None                       # 전체 추천이 대기 중이면 상점 재평가는 필요 없다
    runner._pending = None
    runner.submit_shop(combat(B), rec_for(A))
    runner.submit(planning_state())
    assert runner._pending_shop is None                       # 전체 추천이 상점 재평가를 대신한다
    assert advisor2.rescored == []


# ---------------------------------------------------------------------------
# 오버레이
# ---------------------------------------------------------------------------


def test_overlay_shows_fresh_shop_label_and_board_plan(qapp, settings):
    from tft_advisor.app.overlay import OverlayWindow
    from tft_advisor.contracts import BoardPlan, BoardPlanEntry, BoardSwap

    w = OverlayWindow(settings)
    rec = rec_for(B).model_copy(update={"board_plan": BoardPlan(
        comp_id="c1", slots=4,
        lineup=[BoardPlanEntry(unit_id=B[0], on_board=True, action="keep", reason="목표 덱 핵심")],
        swaps=[BoardSwap(field_unit_id=B[1], bench_unit_id=B[2])])})
    w.set_data(combat(B), rec, kept=KeptInfo(ScreenMode.COMBAT, shop_fresh=True))
    html = w.body.text()
    assert "새 상점 기준" in html and "[보드 배치]" in html and "교체" in html
    assert html.index("[목표 덱]") < html.index("[보드 배치]") < html.index("[상점]")
    w.set_data(combat(B), rec_for(B), kept=KeptInfo(ScreenMode.COMBAT))
    # 고정 배치(33 보고): 보드 배치 섹션은 자리를 지키고 자리 표시만 보인다
    assert "직전 추천" in w.body.text() and "보드 배치 추천 없음" in w.body.text() and "교체" not in w.body.text()
    w.deleteLater()
