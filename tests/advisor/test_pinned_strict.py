"""고정 덱 = 그 덱에 맞는 기물·아이템만(`_workspace/21_board_trust.md` §17.8) + 성급 미상 처리(§17.7).

사용자 규칙: "목표덱을 내가 고정하면 그 덱에 적합한 기물과 아이템만 추천해줘야 해."
- 상점: 고정 덱 최종 보드·빌드업 경로(내 레벨 ~ +2) 밖 유닛은 [보류] "고정 덱에 없음"
- 아이템: 고정 덱 캐리 BIS·핵심 아이템만. 없으면 "고정 덱 아이템을 만들 재료가 아직 없습니다 — 재료 보관"
- 판매: 고정 중에는 대안(2·3위) 덱 유닛을 지키지 않는다
- 고정하지 않으면 예전 그대로
"""
from __future__ import annotations

from tft_advisor.advisor.board_plan import plan_board
from tft_advisor.advisor.engine import resource_signature
from tft_advisor.advisor.features import build_view, buy_makes_2star, buy_makes_3star, copies_owned
from tft_advisor.advisor.jev_state import NameBook as JevNames
from tft_advisor.advisor.jev_state import _unit_entry
from tft_advisor.advisor.sell import attach_sell, unit_sell_value
from tft_advisor.app.names import NameBook
from tft_advisor.app.report import board_plan_lines, item_lines
from tft_advisor.contracts import FieldSource, GameState, UnitOnBoard

VISION = {"board": FieldSource.VISION, "bench": FieldSource.VISION}
ZYRA = "juggernaut-zyra-amumu"
LUNAR = "lunar-aphelios-nidalee_ap"
C = "DA_Component_"
SHOP = ["DA_18_Ornn", "DA_18_Alistar", "DA_18_Varus", "DA_18_Yorick", "DA_18_Shen"]


def gs(stage="2-5", level=5, components=(), shop=SHOP, board=None, bench=None, **kw) -> GameState:
    return GameState.model_validate({
        "screen_mode": "planning", "stage": stage, "level": level, "gold": 50, "hp": 70,
        "shop": [{"kind": "champion", "id": i} for i in shop] + [{"kind": "empty"}] * (5 - len(shop)),
        "board": board if board is not None else [{"id": "DA_18_Yorick", "star": 1}, {"id": "DA_18_Akali_AD", "star": 1}],
        "bench": bench if bench is not None else [],
        "items": {"components": [{"id": C + c} for c in components]},
        "field_source": {**VISION, "items": FieldSource.VISION}, **kw})


def by_id(rec):
    return {s.offer_id: s for s in rec.shop}


# ---------------------------------------------------------------------------
# 상점
# ---------------------------------------------------------------------------


def test_pinned_shop_buys_only_pinned_deck_units(make_advisor):
    free = by_id(make_advisor().advise(gs()))
    assert free["DA_18_Ornn"].buy and not any("고정 덱" in (s.reason or "") for s in free.values())
    adv = make_advisor()
    adv.set_pinned_comp(ZYRA)
    rec = adv.advise(gs())
    shop = by_id(rec)
    for uid in ("DA_18_Ornn", "DA_18_Alistar", "DA_18_Varus", "DA_18_Shen"):     # 자이라 덱 경로 밖
        assert not shop[uid].buy and shop[uid].reason.startswith("고정 덱에 없음")
    assert "고정 덱에 없음" not in shop["DA_18_Yorick"].reason                     # 고정 덱 핵심 유닛
    # 다른 덱을 고정하면 그 덱 유닛은 살 수 있다(달빛: 오른·바루스·쉔은 레벨 5 빌드업 부족 유닛)
    adv.set_pinned_comp(LUNAR)
    lunar = by_id(adv.advise(gs()))
    assert lunar["DA_18_Ornn"].buy and lunar["DA_18_Varus"].buy
    assert lunar["DA_18_Yorick"].reason.startswith("고정 덱에 없음") and not lunar["DA_18_Yorick"].buy


def test_pinned_rule_also_applies_to_rescore_shop(make_advisor):
    adv = make_advisor()
    adv.set_pinned_comp(ZYRA)
    adv.advise(gs())
    rec = adv.rescore_shop(gs(shop=["DA_18_Ornn", "DA_18_Sejuani"]).model_copy(update={"screen_mode": "combat"}))
    shop = by_id(rec)
    assert not shop["DA_18_Ornn"].buy and shop["DA_18_Ornn"].reason.startswith("고정 덱에 없음")
    assert "고정 덱에 없음" not in shop["DA_18_Sejuani"].reason


def test_pin_other_rel_is_zero_by_default(make_advisor):
    assert make_advisor().w.comp.pin_other_rel == 0.0


# ---------------------------------------------------------------------------
# 아이템
# ---------------------------------------------------------------------------


def test_pinned_items_only_for_the_pinned_deck(make_advisor):
    adv = make_advisor()
    adv.set_pinned_comp(ZYRA)
    rec = adv.advise(gs(components=["BFSword", "ChainVest", "NeedlesslyLargeRod", "SparringGloves"]))
    assert rec.item.suggestions and rec.item.note is None
    assert all(s.reason.startswith("고정 덱(전쟁기계 자이라 아무무)") for s in rec.item.suggestions)
    assert not any("보조" in s.reason or "지금 전력용" in s.reason for s in rec.item.suggestions)


def test_pinned_without_deck_items_holds_with_note(make_advisor, stats):
    free = make_advisor().advise(gs(components=["Spatula", "NegatronCloak"]))
    assert free.item.suggestions and free.item.note is None                       # 고정 없으면 예전 그대로
    adv = make_advisor()
    adv.set_pinned_comp(ZYRA)
    rec = adv.advise(gs(components=["Spatula", "NegatronCloak"]))
    assert rec.item.suggestions == [] and rec.item.hold
    assert rec.item.note == "고정 덱 아이템을 만들 재료가 아직 없습니다 — 재료 보관"
    assert item_lines(rec, NameBook()) == ["· 고정 덱 아이템을 만들 재료가 아직 없습니다 — 재료 보관"]


# ---------------------------------------------------------------------------
# 판매
# ---------------------------------------------------------------------------


def test_pinned_sell_does_not_protect_alternative_decks(stats, weights):
    """3스테이지, 고정 자이라 + 표시된 대안 달빛: 아펠리오스(달빛 최종 보드)는 고정 중에만 판매 대상."""
    st = gs(stage="3-2", level=2, shop=[], board=[{"id": "DA_18_Yorick", "star": 1}, {"id": "DA_18_Rakan", "star": 1}],
            bench=[{"id": "DA_18_Aphelios", "star": 1, "confidence": 0.9}])
    view = build_view(st, stats, 0.6, None, 0.8)
    comps = [stats.comp(ZYRA), stats.comp(LUNAR)]
    plan = plan_board(view, stats, comps[0], 2, {}, lambda i: i)
    free = attach_sell(plan, view, stats, comps, 2, [], weights.sell)
    pinned = attach_sell(plan, view, stats, comps, 2, [], weights.sell, pinned=True)
    assert "DA_18_Aphelios" not in {s.unit_id for s in free.sell}
    assert "DA_18_Aphelios" in {s.unit_id for s in pinned.sell}


def test_pinned_sell_keeps_stage2_quiet(stats, weights):
    st = gs(stage="2-2", level=2, shop=[], gold=3, board=[{"id": "DA_18_Yorick", "star": 1}],
            bench=[{"id": "DA_18_Aphelios", "star": 1, "confidence": 0.9}])
    view = build_view(st, stats, 0.6, None, 0.8)
    comps = [stats.comp(ZYRA), stats.comp(LUNAR)]
    plan = plan_board(view, stats, comps[0], 2, {}, lambda i: i)
    assert attach_sell(plan, view, stats, comps, 2, [], weights.sell, pinned=True).sell == []


# ---------------------------------------------------------------------------
# 성급 미상(star None) — ★1로 가정하지 않는다(21 §17.7)
# ---------------------------------------------------------------------------


def _u(uid: str, star: int | None) -> UnitOnBoard:
    return UnitOnBoard(id=uid, star=star, confidence=0.9)


def test_copy_counting_with_unknown_star():
    units = [_u("X", None), _u("X", 1)]
    assert copies_owned("X", units) == 2                         # 미상 = 적어도 1기(하한)
    assert not buy_makes_2star("X", units)                       # 미상은 ★1 쌍에 세지 않는다
    assert buy_makes_2star("X", [_u("X", 1), _u("X", 1)])
    assert not buy_makes_3star("X", [_u("X", None), _u("X", 1), _u("X", 2), _u("X", 2)])


def test_jev_state_sends_null_star(stats):
    e = _unit_entry(_u("DA_18_Yorick", None), stats, JevNames(stats))
    assert "star" in e and e["star"] is None
    assert _unit_entry(_u("DA_18_Yorick", 2), stats, JevNames(stats))["star"] == 2


def test_resource_signature_ignores_unknown_star(stats):
    four = next(cid for cid in ("DA_18_Ashe", "DA_18_Sivir", "DA_18_Zyra", "DA_18_Aphelios")
                if (stats.champion_cost(cid) or 0) >= 4)
    a = build_view(gs(board=[{"id": four, "star": None}]), stats, 0.6, None)
    b = build_view(gs(board=[{"id": four, "star": 2}]), stats, 0.6, None)
    base = build_view(gs(board=[]), stats, 0.6, None)
    assert resource_signature(a, [], stats) == resource_signature(base, [], stats)
    assert resource_signature(b, [], stats) != resource_signature(base, [], stats)


def test_sell_unknown_star_uses_minimum_and_says_so(stats, weights):
    assert unit_sell_value(3, None) == unit_sell_value(3, 1) == 3
    st = gs(stage="4-2", level=1, shop=[], board=[{"id": "DA_18_Yorick", "star": 1}],
            bench=[{"id": "DA_18_Shen", "star": None, "confidence": 0.9}])
    view = build_view(st, stats, 0.6, None, 0.8)
    comp = stats.comp(ZYRA)
    plan = attach_sell(plan_board(view, stats, comp, 1, {}, lambda i: i), view, stats, [comp], 1, [], weights.sell)
    shen = next(s for s in plan.sell if s.unit_id == "DA_18_Shen")
    assert shen.star is None and "성급 미확인(판매가는 최소값)" in shen.reason
    text = "\n".join(board_plan_lines(plan, NameBook()))
    assert "골드 이상" in text and "★?" in text and "★1" not in text


def test_board_plan_displays_unknown_star_as_question(stats):
    st = gs(board=[{"id": "DA_18_Yorick", "star": None}], level=1)
    p = plan_board(build_view(st, stats, 0.6, None), stats, stats.comp(ZYRA), 1, {}, lambda i: i)
    assert p.lineup[0].star is None
    assert any("★?" in ln for ln in board_plan_lines(p, NameBook()) if ln.startswith("보드: "))
