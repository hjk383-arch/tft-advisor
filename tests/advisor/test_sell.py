"""판매 추천(`advisor.sell`, `_workspace/21_board_trust.md` §14.1) + 보드 배치 섹션 유지(§14.2).

가짜 GameState + mini 통계. 목표 덱 juggernaut-zyra-amumu(최종: 자이라·요릭·세주아니·바이 …),
무관 유닛: 알리스타(2)·쉔(2)·케넨(5).
"""
from __future__ import annotations

import pytest

from tft_advisor.advisor.board_plan import LOW_TRUST_NOTE, plan_board
from tft_advisor.advisor.features import build_view
from tft_advisor.advisor.sell import attach_sell, interest_note, unit_sell_value
from tft_advisor.app.ledger import sell_value as ledger_sell_value
from tft_advisor.app.report import board_plan_lines
from tft_advisor.app.names import NameBook
from tft_advisor.config import SellWeights
from tft_advisor.contracts import UNKNOWN_UNIT_ID, FieldSource, GameState, ShopAdvice, UnitOnBoard

VISION = {"board": FieldSource.VISION, "bench": FieldSource.VISION}
COMP = "juggernaut-zyra-amumu"
BOARD4 = ("DA_18_Zyra", "DA_18_Yorick", "DA_18_Sejuani", "DA_Vi18")
POLITE_BAD = ("한다.", "하라", "해요", "이다.")


def U(uid: str, star: int = 1, conf: float = 0.9, items: list[str] | None = None, slot: int | None = None,
      hex_=None) -> UnitOnBoard:
    return UnitOnBoard(id=uid, star=star, confidence=conf, items=items or [], bench_slot=slot, hex=hex_)


def unknown(slot=None) -> UnitOnBoard:
    return UnitOnBoard(id=UNKNOWN_UNIT_ID, confidence=0.2, star=1, bench_slot=slot)


def gs(bench, *, stage="3-2", level=4, gold=10, board=None, conf=None, shop_odds=None, **kw) -> GameState:
    board = board if board is not None else [U(u, hex_=(0, i)) for i, u in enumerate(BOARD4[:level])]
    bench = [u if u.bench_slot is not None else u.model_copy(update={"bench_slot": i}) for i, u in enumerate(bench)]
    raw = {"screen_mode": "planning", "stage": stage, "level": level, "gold": gold, "board": board, "bench": bench,
           "confidence": conf or {"board": 0.9, "bench": 0.9}, "field_source": VISION, **kw}
    if shop_odds is not None:
        raw["shop_odds"] = shop_odds
    return GameState.model_validate(raw)


@pytest.fixture
def run(stats):
    comp = stats.comp(COMP)

    def _run(state: GameState, shop=(), w: SellWeights | None = None):
        view = build_view(state, stats, 0.6, None)
        plan = plan_board(view, stats, comp, state.level, {}, lambda i: stats.name(i, "ko") or i)
        return attach_sell(plan, view, stats, [comp], state.level, list(shop), w or SellWeights())
    return _run


def sold(plan):
    return [s.unit_id for s in plan.sell]


# ---------------------------------------------------------------------------
# 판매가 · 이자
# ---------------------------------------------------------------------------


def test_sell_value_matches_ledger_rule():
    for cost in range(1, 6):
        for star in (1, 2, 3):
            assert unit_sell_value(cost, star) == ledger_sell_value(cost, star)
    assert unit_sell_value(1, 2) == 3 and unit_sell_value(2, 2) == 5 and unit_sell_value(3, 3) == 25
    assert unit_sell_value(None, 1) is None


def test_interest_note_wording():
    assert interest_note(28, 2) == "팔면 30골드 → 이자 +1"
    assert interest_note(26, 2) == "이자 구간 30골드까지 2 남음"
    assert interest_note(21, 2) is None               # 멀다
    assert interest_note(55, 2) is None               # 이미 최대 이자
    assert interest_note(None, 2) is None


# ---------------------------------------------------------------------------
# 규칙
# ---------------------------------------------------------------------------


def test_full_bench_early_recommends_a_few_singles(run):
    bench = [U("DA_18_Alistar"), U("DA_18_Shen"), U("DA_18_Kennen")] + [unknown() for _ in range(6)]
    p = run(gs(bench, stage="2-5", gold=4))
    assert 1 <= len(p.sell) <= SellWeights().early_max
    assert set(sold(p)) <= {"DA_18_Alistar", "DA_18_Shen", "DA_18_Kennen"}
    assert UNKNOWN_UNIT_ID not in sold(p)
    assert any("벤치 9/9 가득 참" in n for n in p.sell_notes)
    assert any("미확인 유닛 6기는 판단하지 않았습니다" in n for n in p.sell_notes)
    assert all(s.where == "bench" and s.bench_slot is not None for s in p.sell)
    assert p.sell_gold_total == sum(s.gold for s in p.sell)


def test_early_without_pressure_is_quiet(run):
    p = run(gs([U("DA_18_Alistar"), U("DA_18_Kennen")], stage="2-3", gold=1))
    assert p.sell == [] and p.sell_notes == [] and p.interest_note is None


def test_pair_is_kept_while_two_star_is_likely(run):
    # 3-2, 레벨 5 확률 45/33/20/2/0: 쉔(2코스트) 쌍은 둔다, 케넨(5코스트 0%) 쌍은 판다, 알리스타 싱글은 판다
    st = gs([U("DA_18_Shen"), U("DA_18_Shen"), U("DA_18_Kennen"), U("DA_18_Kennen"), U("DA_18_Alistar")],
            level=4, shop_odds=[45, 33, 20, 2, 0])
    p = run(st)
    assert "DA_18_Shen" not in sold(p)
    assert sold(p).count("DA_18_Kennen") == 2 and "DA_18_Alistar" in sold(p)
    ken = next(s for s in p.sell if s.unit_id == "DA_18_Kennen")
    assert "2성 가능성이 낮습니다(상점 5코스트 0%)" in ken.reason


def test_unknown_units_are_never_sold(run):
    bench = [unknown(), unknown(), U("DA_18_Alistar"), unknown()]
    board = [U("DA_18_Zyra", hex_=(0, 0)), UnitOnBoard(id=UNKNOWN_UNIT_ID, confidence=0.2, hex=(0, 1)),
             U("DA_18_Kennen", hex_=(0, 2))]
    p = run(gs(bench, stage="4-2", level=2, board=board, conf={"board": 0.5, "bench": 0.4}))
    assert UNKNOWN_UNIT_ID not in sold(p)
    assert "DA_18_Alistar" in sold(p)
    assert any("미확인 유닛 4기는 판단하지 않았습니다" in n for n in p.sell_notes)


def test_interest_breakpoint_triggers_early_sale(run):
    p = run(gs([U("DA_18_Alistar")], stage="2-5", gold=28))
    assert sold(p) == ["DA_18_Alistar"] and p.sell_gold_total == 2
    assert p.interest_note == "팔면 30골드 → 이자 +1"


def test_late_game_sells_units_outside_final_deck(run):
    st = gs([U("DA_18_Shen"), U("DA_18_Shen"), U("DA_18_Ashe"), U("DA_18_Alistar", star=2)], stage="4-3", gold=40,
            shop_odds=[15, 20, 32, 30, 3])
    p = run(st)
    assert sold(p).count("DA_18_Shen") == 2                      # 후반: 최종 덱 밖 쌍도 판다
    assert "DA_18_Alistar" in sold(p)                             # 후반 벤치 2성도 최종 덱 밖이면 판다
    assert "DA_18_Ashe" not in sold(p)                            # 최종 덱 유닛은 지킨다
    assert all(u not in sold(p) for u in BOARD4)                 # 라인업은 지킨다
    assert any("최종 덱에 없는 유닛입니다" in s.reason for s in p.sell)
    assert any("후반이라 2성 대기보다 골드가 낫습니다" in s.reason for s in p.sell)
    assert p.sell_gold_total == 2 + 2 + 5 and p.interest_note == "이자 구간 50골드까지 1 남음"


def test_item_holder_sale_mentions_items_return(run):
    p = run(gs([U("DA_18_Alistar", items=["DA_Deathblade"])], stage="4-1", gold=12))
    s = p.sell[0]
    assert s.unit_id == "DA_18_Alistar" and s.items == ["DA_Deathblade"]
    assert "아이템 1개는 벤치로 돌아옵니다" in s.reason


def test_mid_game_bench_two_star_kept_as_backup(run):
    p = run(gs([U("DA_18_Alistar", star=2), U("DA_18_Kennen")], stage="3-2", gold=10))
    assert "DA_18_Alistar" not in sold(p) and "DA_18_Kennen" in sold(p)


def test_shop_buy_needs_a_bench_slot(run):
    bench = [U("DA_18_Alistar"), U("DA_18_Shen"), U("DA_18_Shen")] + [unknown() for _ in range(6)]
    buy = ShopAdvice(slot=0, kind="champion", offer_id="DA_18_Rakan", buy=True, score=0.8)
    p = run(gs(bench, stage="2-6", gold=10), shop=[buy])
    assert any("상점 구매 1기 자리가 모자랍니다" in n for n in p.sell_notes)
    assert "DA_18_Alistar" in sold(p)
    # 사면 바로 합성되는 구매(확인된 1성 2기)는 자리가 필요 없다
    buy2 = ShopAdvice(slot=0, kind="champion", offer_id="DA_18_Shen", buy=True, score=0.8)
    p2 = run(gs(bench, stage="2-6", gold=10), shop=[buy2])
    assert not any("자리가 모자랍니다" in n for n in p2.sell_notes)


def test_disabled_and_report_lines(run, stats):
    st = gs([U("DA_18_Alistar")], stage="2-5", gold=28)
    assert run(st, w=SellWeights(enabled=False)).sell == []
    p = run(st)
    lines = board_plan_lines(p, NameBook(stats.static))
    sell = [ln for ln in lines if ln.startswith("판매:")]
    assert sell == ["판매: 알리스타 (+2골드 · 팔면 30골드 → 이자 +1)"]
    assert any(ln.startswith("판매 이유: 알리스타 — ") for ln in lines)
    assert not any(bad in " ".join(lines) for bad in POLITE_BAD)


# ---------------------------------------------------------------------------
# 보드 배치 섹션 유지(§14.2)
# ---------------------------------------------------------------------------


def _planning(board=None, bench=None, conf=None, source=None, mode="planning", stage="3-2") -> GameState:
    raw = {"screen_mode": mode, "stage": stage, "level": 4, "gold": 20,
           "board": board, "bench": bench,
           "confidence": conf or {"board": 0.9, "bench": 0.9},
           "field_source": source or VISION}
    raw = {k: v for k, v in raw.items() if v is not None}
    return GameState.model_validate(raw)


BOARD_UNITS = [U(u, hex_=(0, i)) for i, u in enumerate(BOARD4)]


def test_confidence_dip_keeps_plan_with_known_units(make_advisor):
    tracked = {"board": FieldSource.TRACKED, "bench": FieldSource.TRACKED}
    st = _planning(board=BOARD_UNITS[:3] + [UnitOnBoard(id=UNKNOWN_UNIT_ID, confidence=0.2, hex=(1, 0))],
                   bench=[U("DA_18_Alistar", conf=0.5, slot=0)], conf={"board": 0.5, "bench": 0.5}, source=tracked)
    rec = make_advisor().advise(st)
    p = rec.board_plan
    assert p is not None and p.low_trust and not p.stale
    assert LOW_TRUST_NOTE in p.notes
    assert p.unknown_on_board == 1 and p.unknown_on_bench == 1       # 낮은 신뢰도 칸은 미확인(자리만)
    assert UNKNOWN_UNIT_ID not in [e.unit_id for e in p.lineup + p.bench]
    assert p.sell == []                                              # 저신뢰 계획에서는 판매 추천 없음


def test_board_unreadable_carries_previous_plan_marked_stale(make_advisor, stats):
    adv = make_advisor()
    first = adv.advise(_planning(board=BOARD_UNITS, bench=[U("DA_18_Alistar", slot=0)]))
    assert first.board_plan is not None and not first.board_plan.stale
    rec = adv.advise(_planning(board=None, bench=None, conf={}))
    assert rec.board_plan is not None and rec.board_plan.stale
    assert [e.unit_id for e in rec.board_plan.lineup] == [e.unit_id for e in first.board_plan.lineup]
    assert rec.board_plan.sell == []
    assert board_plan_lines(rec.board_plan, NameBook(stats.static))[0].startswith("(직전)")


def test_augment_and_carousel_carry_previous_plan(make_advisor):
    adv = make_advisor()
    adv.advise(_planning(board=BOARD_UNITS, bench=[U("DA_18_Alistar", slot=0)]))
    aug = adv.advise(_planning(mode="augment_select", conf={}))
    assert aug.board_plan is not None and aug.board_plan.stale
    car = adv.advise(_planning(mode="carousel", conf={}))
    assert car.board_plan is not None and car.board_plan.stale
    combat = adv.advise(_planning(mode="combat", conf={}))
    assert combat.board_plan is not None


def test_fresh_board_replaces_stale_and_reset_clears(make_advisor):
    adv = make_advisor()
    adv.advise(_planning(board=BOARD_UNITS, bench=[]))
    assert adv.advise(_planning(conf={})).board_plan.stale
    again = adv.advise(_planning(board=BOARD_UNITS, bench=[]))
    assert again.board_plan is not None and not again.board_plan.stale
    adv.reset()
    assert adv.advise(_planning(conf={})).board_plan is None


def test_qa32_unplaced_ledger_unit_sale_never_points_at_a_slot(run):
    """QA 32: 장부로만 아는 자리 미상 유닛(bench_slot/hex None, 31 §9)을 팔라고 할 때 칸 번호를 지어내지 않는다.
    같은 벤치의 이름 미상 칸(자리는 앎)은 판매 후보가 아니다."""
    st = gs([U("DA_18_Alistar"), unknown(), unknown()], stage="4-2", gold=12)
    bench = [st.bench[0].model_copy(update={"bench_slot": None}), *st.bench[1:]]   # 알리스타 = 장부 자리 미상
    st = st.model_copy(update={"bench": bench})
    p = run(st)
    ali = [s for s in p.sell if s.unit_id == "DA_18_Alistar"]
    assert ali and all(s.bench_slot is None and s.hex is None for s in ali)
    assert UNKNOWN_UNIT_ID not in sold(p)
