"""보유 유닛 장부: 구매·판매 추론, 애매한 프레임, 상점 밖 획득, vision 병합, 수동 교정, 영속.

핵심 계약(17_purchase_tracking.md)
- 사라진 상점 칸 + 정확히 맞는 골드 변화 = 구매. 맞지 않으면 **추측하지 않는다**(애매로 세고 신뢰도를 내린다).
- 장부는 1성 등가 사본 수를 세고 3사본 = 2성, 9사본 = 3성으로 환산한다.
- vision이 보드를 보면 **개수는 vision이 이긴다**. 이름을 모르는 칸은 `UNKNOWN_UNIT_ID`이고 신뢰도가 낮다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tft_advisor.app.ledger import (
    CostBook, LedgerCfg, UnitLedger, bodies_for, copies_for_star, field_confidence, sell_value,
)
from tft_advisor.app.session import SessionTracker
from tft_advisor.app.unit_merge import UNKNOWN_CONFIDENCE, BoardObs, SlotObs, board_obs_from, merge_units
from tft_advisor.app.units_cmd import apply_command
from tft_advisor.contracts import (
    UNKNOWN_UNIT_ID, FieldSource, GameState, ScreenMode, ShopSlot, ShopSlotKind, UnitOnBoard,
)
from tft_advisor.unit_status import UnitsKnowledge, units_knowledge, units_note, units_reason

from .conftest import FakeClock, planning_state

COSTS = CostBook()
GROUPS = ("stage", "hud", "shop")

C1 = "DA_18_Xayah"        # 1코스트
C1B = "DA_Cinderling18"   # 1코스트(다른 챔피언)
C2 = "DA_Murkwolf18"      # 2코스트
C3 = "DA_18_Hecarim"      # 3코스트
C4 = "DA_Sentinel18"      # 4코스트


def cost(cid: str) -> int:
    value = COSTS.cost(cid)
    assert value is not None, cid
    return value


def shop_of(ids: list[str | None]) -> list[ShopSlot]:
    """정적 데이터의 실제 코스트를 넣은 상점 5칸(빈 칸은 None)."""
    out = []
    for cid in ids:
        if cid is None:
            out.append(ShopSlot(kind=ShopSlotKind.EMPTY))
        else:
            out.append(ShopSlot(kind=ShopSlotKind.CHAMPION, id=cid, cost=cost(cid)))
    return out


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(100.0)


@pytest.fixture
def tracker(clock, tmp_path) -> SessionTracker:
    return SessionTracker(tmp_path / "session.json", clock=clock)


def feed(tracker: SessionTracker, **kw) -> GameState:
    """준비 화면 프레임 1장을 넣고 합친 상태를 돌려준다."""
    return tracker.observe(planning_state(**kw), groups=GROUPS)


# ---------------------------------------------------------------------------
# 1. 합성 규칙과 판매가
# ---------------------------------------------------------------------------


def test_copies_become_stars():
    assert bodies_for(0) == []
    assert bodies_for(2) == [1, 1]
    assert bodies_for(3) == [2]              # 3사본 = 2성
    assert bodies_for(5) == [2, 1, 1]
    assert bodies_for(9) == [3]              # 9사본 = 3성
    assert bodies_for(10) == [3, 1]
    assert copies_for_star(2) == 3 and copies_for_star(3) == 9


def test_sell_value_rules():
    assert sell_value(1, 1) == 1 and sell_value(1, 2) == 3 and sell_value(1, 3) == 9   # 1코스트는 전액
    assert sell_value(3, 1) == 3 and sell_value(3, 2) == 8 and sell_value(3, 3) == 25
    assert sell_value(None, 1) is None


# ---------------------------------------------------------------------------
# 2. 프레임 열 → 구매 추론
# ---------------------------------------------------------------------------


def test_clean_buy(tracker):
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=20 - cost(C3), shop=shop_of([None, C1, C2, C1B, C4]))
    assert tracker.data.units.copies == {C3: 1}
    assert tracker.data.units.confidence[C3] == 1.0      # 골드로 확인한 구매
    assert tracker.data.units.ambiguous == 0


def test_buy_confirmed_one_frame_later(tracker, clock):
    """상점 ROI와 골드 ROI는 따로 도착할 수 있다 — 칸이 먼저 비고 골드가 다음 프레임에 읽혀도 확정한다."""
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    clock.t += 0.25
    feed(tracker, gold=20, shop=shop_of([None, C1, C2, C1B, C4]))   # 골드는 아직 옛 값
    assert tracker.data.units.copies == {}                           # 아직 확정하지 않는다
    clock.t += 0.25
    feed(tracker, gold=20 - cost(C3), shop=shop_of([None, C1, C2, C1B, C4]))
    assert tracker.data.units.copies == {C3: 1}


def test_two_buys_in_one_frame(tracker):
    """변화 감지가 두 구매를 한 프레임으로 묶어도 코스트 합이 맞으면 둘 다 넣는다."""
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=20 - cost(C3) - cost(C2), shop=shop_of([None, C1, None, C1B, C4]))
    assert tracker.data.units.copies == {C3: 1, C2: 1}
    assert tracker.data.units.ambiguous == 0


def test_reroll_is_not_a_purchase(tracker):
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=18, shop=shop_of([C1, C1B, C2, C3, C4]))
    assert tracker.data.units.copies == {}
    assert tracker.purchases.rerolls == 1 and tracker.data.units.ambiguous == 0


def test_xp_buy_is_not_a_purchase(tracker):
    feed(tracker, gold=20, xp=(2, 6), shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=16, xp=(6, 6), shop=shop_of([C3, C1, C2, C1B, C4]))
    assert tracker.data.units.copies == {}
    assert tracker.purchases.xp_buys == 1 and tracker.data.units.ambiguous == 0


def test_buy_plus_xp_in_one_window(tracker):
    feed(tracker, gold=20, xp=(2, 6), level=4, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=20 - cost(C3) - 4, xp=(6, 6), level=4,
         shop=shop_of([None, C1, C2, C1B, C4]))
    assert tracker.data.units.copies == {C3: 1}
    assert tracker.purchases.xp_buys == 1


def test_xp_bar_read_before_gold_is_still_an_xp_buy(tracker, clock):
    """30 보고: 경험치 막대가 골드보다 **한 프레임 먼저** 바뀌어 읽혀도 경험치 구매다(예전: 골드가 줄던 프레임에는 직전
    프레임 대비 경험치 변화가 없어 설명되지 않는 변화 "미결 0칸, 골드 4"로 남았다)."""
    shop = [C3, C1, C2, C1B, C4]
    feed(tracker, gold=20, level=4, xp=(2, 10), shop=shop_of(shop))
    clock.t += 0.3
    feed(tracker, gold=20, level=4, xp=(6, 10), shop=shop_of(shop))     # 막대 먼저
    clock.t += 0.3
    feed(tracker, gold=16, level=4, xp=(6, 10), shop=shop_of(shop))     # 골드는 다음 프레임
    clock.t += 3.0
    feed(tracker, gold=16, level=4, xp=(6, 10), shop=shop_of(shop))
    assert tracker.purchases.xp_buys == 1 and tracker.data.units.ambiguous == 0


def test_unexplained_change_is_reported_once_and_rebased(tracker, clock):
    """설명되지 않는 골드 변화는 **한 번만** 애매로 센다(예전: 기준을 옮기지 않아 같은 "골드 12"가 settle_s마다 다시 쌓였다).
    그 뒤의 구매는 새 기준으로 정상 확정된다."""
    shop = [C3, C1, C2, C1B, C4]
    feed(tracker, gold=30, level=5, xp=(2, 20), shop=shop_of(shop))
    clock.t += 0.3
    feed(tracker, gold=18, level=5, xp=(2, 20), shop=shop_of(shop))     # 설명 안 되는 -12(예: 골드 오독·모르는 지출)
    for _ in range(6):
        clock.t += 2.5
        feed(tracker, gold=18, level=5, xp=(2, 20), shop=shop_of(shop))
    assert tracker.data.units.ambiguous == 1
    clock.t += 0.3
    feed(tracker, gold=18 - cost(C3), level=5, xp=(2, 20), shop=shop_of([None, C1, C2, C1B, C4]))
    assert tracker.data.units.copies == {C3: 1} and tracker.data.units.ambiguous == 1


def test_level_up_does_not_disturb_the_ledger(tracker):
    feed(tracker, gold=20, level=5, xp=(2, 6), shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=16, level=6, xp=(0, 10), shop=shop_of([C3, C1, C2, C1B, C4]))
    assert tracker.data.units.copies == {} and tracker.data.units.ambiguous == 0


def test_sale_removes_copies(tracker):
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=20 - cost(C3), shop=shop_of([None, C1, C2, C1B, C4]))
    assert tracker.data.units.copies == {C3: 1}
    feed(tracker, gold=20, shop=shop_of([None, C1, C2, C1B, C4]))   # 다시 팔았다(+3)
    assert tracker.data.units.copies == {}
    assert tracker.purchases.sales == 1


def test_ambiguous_sale_is_not_guessed(tracker):
    """판매가가 같은 유닛이 둘이면 어느 쪽을 팔았는지 모른다 → 장부를 건드리지 않는다."""
    tracker.add_unit(C1, source="carousel")
    tracker.add_unit(C1B, source="carousel")
    feed(tracker, gold=10, shop=shop_of([C3, C2, C4, None, None]))
    feed(tracker, gold=11, shop=shop_of([C3, C2, C4, None, None]))
    tracker._now.t += 5                                    # 정산 창이 끝난다
    feed(tracker, gold=11, shop=shop_of([C3, C2, C4, None, None]))
    assert tracker.data.units.copies == {C1: 1, C1B: 1}
    assert tracker.data.units.ambiguous == 1


def test_gold_mismatch_is_recorded_not_guessed(tracker, clock):
    """칸 2개가 비었는데 골드가 한 명분만 줄었다 → 무엇을 샀는지 모른다. 유닛을 지어내지 않는다."""
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=20 - cost(C3), shop=shop_of([None, C1, None, C1B, C4]))
    clock.t += 5
    feed(tracker, gold=20 - cost(C3), shop=shop_of([None, C1, None, C1B, C4]))
    assert tracker.data.units.copies == {}
    assert tracker.data.units.ambiguous == 1


def test_skipped_frames_do_not_invent_units(tracker, clock):
    """프레임을 건너뛰어 상점이 통째로 바뀐 채로 도착하면(리롤/라운드 전환) 구매를 지어내지 않는다."""
    feed(tracker, gold=30, shop=shop_of([C3, C1, C2, C1B, C4]))
    clock.t += 30
    feed(tracker, gold=12, stage="3-2", shop=shop_of([C1, C2, C3, C4, C1B]))
    assert tracker.data.units.copies == {}


def test_unreadable_shop_slot_is_not_a_purchase(tracker, clock):
    """'무언가 있는데 못 읽음'은 빈 칸이 아니다 → 구매로 보지 않는다."""
    first = shop_of([C3, C1, C2, C1B, C4])
    second = list(first)
    second[0] = ShopSlot(kind=ShopSlotKind.UNKNOWN)
    feed(tracker, gold=20, shop=first)
    feed(tracker, gold=20, shop=second)
    clock.t += 5
    feed(tracker, gold=20, shop=second)
    assert tracker.data.units.copies == {}


def test_buy_without_gold_uses_slot_evidence(tracker, clock):
    """골드를 못 읽어도 칸 하나만 사라졌으면 무엇을 샀는지는 애매하지 않다(신뢰도는 낮게)."""
    feed(tracker, gold=None, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=None, shop=shop_of([None, C1, C2, C1B, C4]))
    clock.t += 5
    feed(tracker, gold=None, shop=shop_of([None, C1, C2, C1B, C4]))
    assert tracker.data.units.copies == {C3: 1}
    assert tracker.data.units.confidence[C3] == pytest.approx(0.6)


def test_three_copies_make_a_two_star(tracker):
    gold = 20
    shop = [C1, C1, C1, C2, C4]
    feed(tracker, gold=gold, shop=shop_of(shop))
    for i in range(3):
        gold -= cost(C1)
        shop[i] = None
        feed(tracker, gold=gold, shop=shop_of(shop))
    assert tracker.data.units.copies == {C1: 3}
    bodies = tracker.data.units.bodies(COSTS)
    assert [b.star for b in bodies] == [2]
    state = tracker.state
    assert [u.star for u in (state.board or []) + (state.bench or [])] == [2]


# ---------------------------------------------------------------------------
# 3. 상점 밖 획득 · 수동 교정
# ---------------------------------------------------------------------------


def test_units_gained_outside_the_shop(tracker):
    """공동 선택·증강·모루는 상점 이벤트가 없다 → 직접 넣는다."""
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    tracker.add_unit(C4, source="carousel")
    tracker.add_unit(C2, star=2, source="anvil")
    assert tracker.data.units.copies == {C4: 1, C2: 3}
    assert tracker.data.units.sources[C4] == "carousel"
    assert [b.star for b in tracker.data.units.bodies(COSTS) if b.champion_id == C2] == [2]


def test_manual_corrections(tracker):
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    tracker.add_unit(C3)
    tracker.set_unit_star(C3, 2)
    assert tracker.data.units.copies == {C3: 3}
    tracker.remove_unit(C3, 1)
    assert tracker.data.units.copies == {C3: 2}
    tracker.remove_unit(C3, 0)                      # 0 = 전부
    assert tracker.data.units.copies == {}
    tracker.set_units({C1: 1, C2: 3})
    assert tracker.data.units.copies == {C1: 1, C2: 3}
    assert tracker.state.field_source["board"] == FieldSource.MANUAL
    tracker.clear_units()
    assert tracker.data.units.copies == {}
    assert tracker.state.board is None and tracker.state.bench is None   # 지운 보드가 남지 않는다
    assert feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4])).board is None


def test_confirm_restores_confidence(tracker, clock):
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=19, shop=shop_of([None, C1, None, C1B, C4]))
    clock.t += 5
    feed(tracker, gold=19, shop=shop_of([None, C1, None, C1B, C4]))
    assert tracker.data.units.ambiguous == 1
    tracker.confirm_units()
    assert tracker.data.units.ambiguous == 0


def test_units_command_surface(tracker):
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    assert apply_command(tracker, "무관한 입력") is None
    assert "비어 있습니다" in apply_command(tracker, "유닛")
    answer = apply_command(tracker, "유닛 추가 자야")
    assert "자야" in answer and tracker.data.units.copies == {C1: 1}
    apply_command(tracker, "유닛 성급 자야 2")
    assert tracker.data.units.copies == {C1: 3}
    apply_command(tracker, "유닛 제거 자야 전부")
    assert tracker.data.units.copies == {}
    assert "찾지 못했습니다" in apply_command(tracker, "유닛 추가 없는챔피언")
    assert "사용법" in apply_command(tracker, "유닛 이상한명령") or "유닛 추가" in apply_command(tracker, "유닛 이상한명령")


# ---------------------------------------------------------------------------
# 4. vision 판독과의 병합
# ---------------------------------------------------------------------------


def _ledger(**copies) -> UnitLedger:
    led = UnitLedger()
    for cid, n in copies.items():
        led.set_copies(cid, n, source="shop")
    led.manual = False
    return led


def test_merge_without_vision_splits_by_level():
    led = _ledger(**{C4: 1, C3: 3, C1: 2})
    out = merge_units(led, None, level=2, costs=COSTS)
    assert len(out.board) == 2 and len(out.bench) == 2      # 4기: 2성 1 + 1성 3
    assert out.board[0].star == 2                            # 성급·코스트 순으로 보드에 올린다
    assert all(u.hex is None and u.bench_slot is None for u in out.board)
    assert out.confidence >= 0.6


def test_vision_count_wins_and_extras_are_unknown():
    """장부 3기 · vision 5기 → 2칸은 이름 미상(ID를 지어내지 않는다)."""
    led = _ledger(**{C4: 1, C3: 1, C1: 1})
    obs = BoardObs(board=(SlotObs(star=1, hex=(0, 0)), SlotObs(star=1, hex=(0, 1)),
                          SlotObs(star=1, hex=(0, 2)), SlotObs(star=1, hex=(0, 3))),
                   bench=(SlotObs(star=1, bench_slot=0),))
    out = merge_units(led, obs, level=5, costs=COSTS)
    assert out.total == 5 and out.known == 3 and out.unknown == 2
    ids = [u.id for u in out.board + out.bench]
    assert ids.count(UNKNOWN_UNIT_ID) == 2
    unknown = [u for u in out.board + out.bench if u.id == UNKNOWN_UNIT_ID]
    assert all(u.confidence == UNKNOWN_CONFIDENCE for u in unknown)
    assert out.confidence < 0.85 and "이름 미상 2기" in (out.note() or "")


def test_ledger_larger_than_vision_drops_extras():
    led = _ledger(**{C4: 1, C3: 1, C1: 1})
    obs = BoardObs(board=(SlotObs(star=1, hex=(0, 0)),))
    out = merge_units(led, obs, level=5, costs=COSTS)
    assert out.total == 1 and out.dropped == 2
    assert out.board[0].id == C4        # 가장 비싼 유닛부터 배치한다(결정적)


def test_star_from_vision_wins_and_items_are_attached():
    led = _ledger(**{C3: 1})
    obs = BoardObs(board=(SlotObs(star=2, items=("DA_Bloodthirster",), hex=(1, 2), confidence=0.9),))
    out = merge_units(led, obs, level=5, costs=COSTS)
    assert out.board[0].star == 2 and out.board[0].items == ["DA_Bloodthirster"]
    assert out.board[0].hex == (1, 2)
    assert out.star_conflicts == 1


def test_board_read_adapter_accepts_vision_shape():
    """vision의 `BoardRead`/`UnitSlot`(dataclass)과 딕셔너리 둘 다 받는다."""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Slot:
        star: int | None = None
        items: tuple = ()
        hex: tuple | None = None
        bench_slot: int | None = None
        confidence: float = 0.9
        unit_id: None = None

    @dataclass(frozen=True)
    class Read:
        board: tuple = ()
        bench: tuple = ()
        confidence: float = 0.8

    obs = board_obs_from(Read(board=(Slot(star=2, hex=(0, 1)),), bench=(Slot(bench_slot=3),)))
    assert obs is not None and obs.count == 2 and obs.board[0].star == 2
    assert obs.bench[0].bench_slot == 3 and obs.confidence == 0.8
    assert board_obs_from({"board": [{"star": 1, "hex": [2, 3]}], "bench": []}).board[0].hex == (2, 3)
    assert board_obs_from(None) is None


def test_observe_merges_board_read_into_state(tracker):
    feed(tracker, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(tracker, gold=20 - cost(C3), shop=shop_of([None, C1, C2, C1B, C4]))
    read = BoardObs(board=(SlotObs(star=1, hex=(0, 3), items=("DA_Bloodthirster",)),
                           SlotObs(star=1, hex=(0, 4))))
    state = tracker.observe(planning_state(gold=20 - cost(C3), shop=shop_of([None, C1, C2, C1B, C4])),
                            groups=GROUPS, board_read=read)
    assert state.board is not None and len(state.board) == 2
    assert state.board[0].id == C3 and state.board[1].id == UNKNOWN_UNIT_ID
    assert state.field_source["board"] == FieldSource.TRACKED
    assert units_knowledge(state, 0.6) in (UnitsKnowledge.TRACKED, UnitsKnowledge.PARTIAL)


def test_field_confidence_degrades_with_ambiguity():
    cfg = LedgerCfg()
    assert field_confidence(cfg, 0) == 0.85
    assert field_confidence(cfg, 3) == 0.5          # 임계값(0.6) 아래 = advisor는 '모름'으로 다룬다
    assert 0.6 < field_confidence(cfg, 1) < 0.85
    assert field_confidence(cfg, 0, known=1, total=2) < 0.6


# ---------------------------------------------------------------------------
# 5. 상태 문구(합쇼체)
# ---------------------------------------------------------------------------


def test_unit_status_wording():
    blind = GameState(screen_mode=ScreenMode.PLANNING)
    assert units_knowledge(blind) is UnitsKnowledge.UNKNOWN
    assert "보드 미인식" in units_reason(blind) and "보드 미인식" in units_note(blind)

    tracked = GameState(screen_mode=ScreenMode.PLANNING, board=[UnitOnBoard(id=C3, star=1)], bench=[],
                        confidence={"board": 0.85, "bench": 0.85},
                        field_source={"board": FieldSource.TRACKED, "bench": FieldSource.TRACKED})
    assert units_knowledge(tracked) is UnitsKnowledge.TRACKED
    assert "구매 추적" in units_reason(tracked)

    partial = tracked.model_copy(update={"confidence": {"board": 0.4, "bench": 0.4}})
    assert units_knowledge(partial) is UnitsKnowledge.PARTIAL
    assert "부분 확인" in units_reason(partial) and "부분 확인" in units_note(partial)

    seen = tracked.model_copy(update={"field_source": {"board": FieldSource.VISION,
                                                       "bench": FieldSource.VISION}})
    assert units_knowledge(seen) is UnitsKnowledge.VISION and units_reason(seen) is None

    for text in (units_reason(blind), units_reason(tracked), units_reason(partial)):
        assert not any(bad in text for bad in ("한다", "하라", "해요", "이다"))


def test_report_shows_the_new_states():
    from tft_advisor.app.report import format_report

    from .conftest import sample_recommendation

    rec = sample_recommendation()
    blind = planning_state()
    assert "보드 미인식" in format_report(blind, rec)
    known = planning_state(board=[UnitOnBoard(id="DA_18_Khazix", star=2)], bench=[],
                           confidence={"board": 0.85, "bench": 0.85},
                           field_source={"board": FieldSource.TRACKED, "bench": FieldSource.TRACKED})
    text = format_report(known, rec)
    assert "구매 추적" in text and "보드 미인식" not in text


# ---------------------------------------------------------------------------
# 6. 영속 · 새 판
# ---------------------------------------------------------------------------


def test_ledger_round_trip(tmp_path, clock):
    path = tmp_path / "session.json"
    t = SessionTracker(path, clock=clock)
    feed(t, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(t, gold=20 - cost(C3), shop=shop_of([None, C1, C2, C1B, C4]))
    t.add_unit(C1, 2, source="carousel")
    t.save()

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert raw["units"]["copies"] == {C3: 1, C1: 2}

    back = SessionTracker(path, clock=clock)
    assert back.load()
    assert back.data.units.copies == {C3: 1, C1: 2}
    assert back.data.units.sources[C1] == "carousel"
    assert back.data.units.events                      # 이벤트 기록도 남는다


def test_reset_clears_the_ledger(tmp_path, clock):
    t = SessionTracker(tmp_path / "session.json", clock=clock)
    feed(t, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    feed(t, gold=20 - cost(C3), shop=shop_of([None, C1, C2, C1B, C4]))
    assert t.data.units.copies
    t.reset("game_over")
    assert t.data.units.copies == {} and t.state is None
    # 새 판의 첫 프레임이 옛 상점과 비교되어 구매로 잡히면 안 된다
    feed(t, gold=2, shop=shop_of([C1, C1B, C2, C3, C4]))
    assert t.data.units.copies == {}


def test_old_session_file_without_ledger_still_loads(tmp_path, clock):
    path = tmp_path / "session.json"
    t = SessionTracker(path, clock=clock)
    t.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("units")
    path.write_text(json.dumps(raw), encoding="utf-8")
    back = SessionTracker(path, clock=clock)
    assert back.load() and back.data.units.copies == {}


def test_disabled_ledger_keeps_the_old_behaviour(tmp_path, clock):
    t = SessionTracker(tmp_path / "session.json", clock=clock, ledger_cfg=LedgerCfg(enabled=False))
    feed(t, gold=20, shop=shop_of([C3, C1, C2, C1B, C4]))
    state = feed(t, gold=20 - cost(C3), shop=shop_of([None, C1, C2, C1B, C4]))
    assert t.data.units.copies == {} and state.board is None


# ---------------------------------------------------------------------------
# 7. advisor가 실제로 달라지는가
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_advisor():
    from tft_advisor.advisor import Advisor, JsonStatsAdapter
    from tft_advisor.config import load_settings, load_weights

    mini = Path(__file__).resolve().parents[1] / "fixtures" / "stats" / "mini_18.json"
    return lambda: Advisor(stats=JsonStatsAdapter.from_file(mini), settings=load_settings(),
                           weights=load_weights(), backend="mock")


def test_advisor_receives_tracked_units_and_changes_its_advice(mock_advisor, tmp_path, clock):
    """보드를 모를 때와 장부가 채워졌을 때의 추천이 다르다(보유/부족 유닛이 실제로 나온다)."""
    owned = ["DA_18_Zyra", "DA_Amumu18", "DA_18_Sivir"]
    base = dict(screen_mode=ScreenMode.PLANNING, stage="3-2", level=6, gold=30, hp=70,
                shop=shop_of([C3, C1, C2, C1B, C4]))

    blind = mock_advisor().advise(GameState(**base))
    assert blind.target_comps
    assert all(not c.owned_units for c in blind.target_comps)
    assert any("보드 미인식" in r for c in blind.target_comps for r in c.reasons)

    t = SessionTracker(tmp_path / "session.json", clock=clock)
    t.observe(GameState(**base), groups=GROUPS)
    t.set_units(dict.fromkeys(owned, 1), source="carousel")
    state = t.state
    assert state.board is not None and state.bench is not None
    assert state.confidence_of("board") >= 0.6

    known = mock_advisor().advise(state)
    assert any(c.owned_units for c in known.target_comps)
    assert not any("보드 미인식" in r for c in known.target_comps for r in c.reasons)
    assert [c.owned_units for c in known.target_comps] != [c.owned_units for c in blind.target_comps]
