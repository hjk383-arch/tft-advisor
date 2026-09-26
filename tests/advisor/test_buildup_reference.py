"""보드 배치 = 목표 덱 레벨별 빌드업 기준(`_workspace/21_board_trust.md` §17) + 상점 ★2 규칙(§17.6).

사용자 규칙: "중요한 건 각 최종덱에 맞는 빌드업을 추천해야 한다는 거야."
- 기준 보드 = 목표 덱(1위·고정) 내 레벨 빌드업 보드 중 보유 유닛이 가장 많은 보드(표본 상위 N개 안에서)
- 기준 보드 보유 유닛을 먼저 올리고(높은 성급 사본, 성급 미상 ≠ ★1), 남는 칸만 임시 유닛
- 없는 유닛("상점에서 구하세요"), 다음 레벨("레벨 6: +…"), 내 레벨에 빌드업이 없으면 가장 가까운 아래 레벨
상점: "3성 유닛 덱이 아닌 이상, 이미 2성이면 추천하지 말아줘."
"""
from __future__ import annotations

import pytest

from tft_advisor.advisor.board_plan import buildup_reference, plan_board, star_rank
from tft_advisor.advisor.features import build_view
from tft_advisor.advisor.sell import attach_sell
from tft_advisor.app.names import NameBook
from tft_advisor.app.report import board_plan_lines
from tft_advisor.config import BoardPlanWeights
from tft_advisor.contracts import UNKNOWN_UNIT_ID, FieldSource, GameState

VISION = {"board": FieldSource.VISION, "bench": FieldSource.VISION}
ZYRA = "juggernaut-zyra-amumu"
LUNAR = "lunar-aphelios-nidalee_ap"
KHAZIX = "executioner-khazix"         # 최종 보드 카직스★3 · 헤카림★3
W = BoardPlanWeights()
# mini 자이라 덱 레벨 5 표본 상위 3 보드:
#   A [라칸, 세주아니, 티모, 요릭, 바위 게] 46판 / B [라칸, 요릭, 유나라, 카르마, 바위 게] 38판 / C [르블랑, 라칸, 요릭, 유나라, 카르마] 31판
REF_A = ["DA_18_Rakan", "DA_18_Sejuani", "DA_18_Teemo", "DA_18_Yorick", "DA_Scuttlecrab18"]


def U(uid: str, star: int | None = 1, conf: float = 0.9, items: list[str] | None = None) -> dict:
    return {"id": uid, "star": star, "confidence": conf, "items": items or []}


def gs(board, bench, level=5, stage="3-2", **kw) -> GameState:
    return GameState.model_validate({
        "screen_mode": "planning", "stage": stage, "level": level, "board": board, "bench": bench,
        "confidence": {"board": 0.9, "bench": 0.9}, "field_source": VISION, **kw})


def plan(stats, state: GameState, comp_id: str = ZYRA, level: int | None = None, comp=None, w: BoardPlanWeights = W):
    view = build_view(state, stats, 0.6, None, 0.8)
    comp = comp if comp is not None else stats.comp(comp_id)
    return plan_board(view, stats, comp, level if level is not None else state.level, {},
                      lambda i: stats.name(i, "ko") or i, w)


def ids(entries):
    return [e.unit_id for e in entries]


# ---------------------------------------------------------------------------
# 기준 보드 고르기
# ---------------------------------------------------------------------------


def test_reference_is_the_best_matching_top_board(stats):
    comp = stats.comp(ZYRA)
    none = buildup_reference(comp, 5, set(), W)
    assert none.ref_level == 5 and none.units == REF_A                   # 보유 0 → 표본 1위
    b = buildup_reference(comp, 5, {"DA_18_Yunara", "DA_Karma18", "DA_18_LeBlanc"}, W)
    assert "DA_18_LeBlanc" in b.units and b.board.games == 31            # 보유 3기와 맞는 C(표본 3위)
    # 표본 상위 3 밖의 보드는 보유와 더 맞아도 고르지 않는다(레벨 4의 케이틀린·카밀·카시오페아·엘리스 = 8위)
    off = buildup_reference(comp, 4, {"DA_18_Caitlyn", "DA_18_Camille", "DA_18_Cassiopeia", "DA_18_Elise"}, W)
    assert "DA_18_Caitlyn" not in off.units and off.board.games == 170
    assert b.next_level == 6 and b.next_units                            # 다음 레벨 = 6


def test_noise_board_with_too_few_units_is_ignored(stats):
    comp = stats.comp(ZYRA)
    noisy = comp.buildup[5][0].model_copy(update={"units": ["DA_18_Yorick"], "games": 9999})
    c2 = comp.model_copy(update={"buildup": {**comp.buildup, 5: [noisy, *comp.buildup[5]]}})
    ref = buildup_reference(c2, 5, {"DA_18_Yorick"}, W)
    assert len(ref.units) == 5                                           # 레벨 5에 1기 보드(수집 잡음)는 제외


def test_nearest_lower_level_fallback_and_next_level(stats):
    comp = stats.comp(ZYRA)
    c2 = comp.model_copy(update={"buildup": {lv: b for lv, b in comp.buildup.items() if lv != 6}})
    ref = buildup_reference(c2, 6, set(), W)
    assert ref.level == 6 and ref.ref_level == 5 and ref.next_level == 7
    low = buildup_reference(comp, 3, set(), W)                            # 레벨 3: 아래가 없으면 가장 낮은 레벨(4)
    assert low.ref_level == 4 and low.next_level == 5
    p = plan(stats, gs([U("DA_18_Yorick")], [], level=6), comp=c2)
    assert p.level == 6 and p.reference_level == 5 and p.next_level == 7
    text = "\n".join(board_plan_lines(p, NameBook(), comp_name="자이라"))
    assert "레벨 6 빌드업 통계가 없어 레벨 5 기준입니다" in text and "레벨 7: " in text


def test_no_comp_or_follow_off_keeps_old_behaviour(stats):
    st = gs([U("DA_18_Alistar")], [U("DA_18_Yorick")], level=1)
    off = plan(stats, st, w=W.model_copy(update={"follow_buildup": False}))
    assert off.reference_units == [] and off.lineup[0].in_reference is None
    none = plan_board(build_view(st, stats, 0.6, None), stats, None, 1, {}, lambda i: i)
    assert none.reference_units == [] and none.comp_id is None


# ---------------------------------------------------------------------------
# 보드 배치: 기준 보드 먼저
# ---------------------------------------------------------------------------


def test_owned_reference_units_are_fielded_first_and_missing_listed(stats):
    st = gs(board=[U("DA_18_Alistar", 2), U("DA_18_Shen"), U("DA_18_Kennen"), U("DA_18_Rakan"), U("DA_18_Ashe")],
            bench=[U("DA_18_Yorick"), U("DA_18_Sejuani")], level=5)
    p = plan(stats, st)
    assert p.reference_level == 5 and p.reference_units == REF_A
    assert set(p.owned_in_reference) == {"DA_18_Rakan", "DA_18_Sejuani", "DA_18_Yorick"}
    assert p.missing == ["DA_18_Teemo", "DA_Scuttlecrab18"]
    assert set(ids(p.lineup[:3])) == set(p.owned_in_reference)          # 기준 보드 보유 유닛이 먼저
    assert all(e.in_reference for e in p.lineup[:3]) and not any(e.in_reference for e in p.lineup[3:])
    assert all(e.reason.startswith("레벨 5 빌드업") for e in p.lineup[:3])
    assert all(e.reason.startswith("임시") for e in p.lineup[3:])
    assert {s.field_unit_id for s in p.swaps} == {"DA_18_Yorick", "DA_18_Sejuani"}
    assert all(s.bench_unit_id in {"DA_18_Alistar", "DA_18_Shen", "DA_18_Kennen", "DA_18_Ashe"} for s in p.swaps)
    lines = board_plan_lines(p, NameBook(), comp_name="전쟁기계 자이라 아무무")
    text = "\n".join(lines)
    ko = NameBook().name
    assert lines[1].startswith("레벨 5 빌드업(전쟁기계 자이라 아무무): ") and lines[1].endswith("보유 3/5")
    assert f"상점에서 구하세요: {ko('DA_18_Teemo')} · {ko('DA_Scuttlecrab18')}" in text
    assert "교체: ↑ 벤치에서 올리기 " in text and "레벨 6: +" in text
    assert not any(ln.startswith("참고: 전환: ") for ln in lines)
    assert not any(bad in text for bad in ("한다.", "하라", "해요"))


def test_reference_units_not_owned_leave_placeholders_stage2(stats):
    """2스테이지: 기준 보드 유닛(요릭)은 반드시 올리고, 남는 칸은 지금 강한 ★2 무관 유닛이 임시로 지킨다."""
    st = gs(board=[U("DA_18_Alistar", 2), U("DA_18_Shen"), U("DA_18_Kennen")], bench=[U("DA_18_Yorick")],
            level=3, stage="2-3")
    p = plan(stats, st)
    assert p.reference_level == 4 and p.level == 3
    assert ids(p.lineup)[0] == "DA_18_Yorick" and "DA_18_Alistar" in ids(p.lineup)
    assert next(e for e in p.lineup if e.unit_id == "DA_18_Alistar").reason.startswith("임시")
    assert p.missing                                                     # 기준 보드는 늘 보여 준다
    text = "\n".join(board_plan_lines(p, NameBook()))
    assert "레벨 4 빌드업: " in text and "상점에서 구하세요: " in text


def test_placeholder_prefers_next_level_unit(stats):
    """레벨 3(기준 = 레벨 4 [세주아니, 요릭, 유나라, 카르마]), 3칸: 기준 유닛 2기 다음 임시 칸은
    다음 레벨(5) 빌드업 유닛 티모가 무관한 케넨보다 먼저."""
    st = gs(board=[U("DA_18_Kennen"), U("DA_18_Yorick")], bench=[U("DA_18_Sejuani"), U("DA_18_Teemo")], level=3)
    p = plan(stats, st)
    assert p.reference_units == ["DA_18_Sejuani", "DA_18_Yorick", "DA_18_Yunara", "DA_Karma18"]
    assert p.next_level == 5 and "DA_18_Teemo" in p.next_level_units
    assert set(ids(p.lineup[:2])) == {"DA_18_Yorick", "DA_18_Sejuani"} and ids(p.lineup)[2] == "DA_18_Teemo"
    teemo = next(e for e in p.lineup if e.unit_id == "DA_18_Teemo")
    assert teemo.reason.startswith("임시") and "레벨 5 빌드업" in teemo.reason
    assert [(s.field_unit_id, s.bench_unit_id) for s in p.swaps][0] == ("DA_18_Sejuani", "DA_18_Kennen")


def test_unknown_star_is_not_treated_as_one_star(stats):
    assert star_rank(1) < star_rank(None) < star_rank(2)
    # 보드 ★1 요릭 vs 벤치 성급 미상 요릭 → 굳이 바꾸지 않는다(미상 가산 < 보드 유지 가산)
    p = plan(stats, gs([U("DA_18_Yorick", 1)], [U("DA_18_Yorick", None)], level=1))
    assert p.swaps == [] and p.lineup[0].star == 1
    # 벤치에 ★1과 성급 미상 → 미상 쪽을 올린다(★1로 가정하지 않는다), 표시는 ★ 없이
    p = plan(stats, gs([U("DA_18_Alistar")], [U("DA_18_Yorick", 1), U("DA_18_Yorick", None)], level=1))
    assert p.lineup[0].unit_id == "DA_18_Yorick" and p.lineup[0].star is None
    # 알려진 ★2는 보드 ★1보다 먼저
    p = plan(stats, gs([U("DA_18_Yorick", 1)], [U("DA_18_Yorick", 2)], level=1))
    assert p.lineup[0].star == 2 and p.lineup[0].action == "field"


def test_unknown_and_guessed_units_never_counted_as_owned(stats):
    st = gs(board=[U("DA_18_Rakan"), {"id": UNKNOWN_UNIT_ID, "confidence": 0.2}],
            bench=[U("DA_18_Yorick", conf=0.75)], level=5)          # 0.75 = 추정 이름(21 §15)
    p = plan(stats, st)
    assert "DA_18_Yorick" not in p.owned_in_reference and "DA_18_Yorick" in p.missing
    assert UNKNOWN_UNIT_ID not in ids(p.lineup)
    text = "\n".join(board_plan_lines(p, NameBook()))
    assert "(미확인 유닛 중에 있을 수 있습니다)" in text


def test_sell_never_contradicts_the_reference(stats, weights):
    """3스테이지 벤치 가득: 기준 보드·다음 레벨 유닛은 판매 대상이 아니다."""
    bench = [U("DA_18_Teemo"), U("DA_18_Yunara"), U("DA_18_Kennen", 1), U("DA_18_Shen", 1), U("DA_18_Ashe", 2),
             U("DA_18_Alistar", 1), U("DA_18_Kobuko", 1), U("DA_18_Leona", 1), U("DA_18_Varus", 1)]
    st = gs(board=[U("DA_18_Rakan"), U("DA_18_Yorick")], bench=bench, level=2, stage="3-2", gold=10)
    view = build_view(st, stats, 0.6, None, 0.8)
    comp = stats.comp(ZYRA)
    p = plan_board(view, stats, comp, 2, {}, lambda i: i, W)
    p = attach_sell(p, view, stats, [comp], 2, [], weights.sell)
    protected = set(p.reference_units) | set(p.next_level_units)
    assert p.sell and not ({s.unit_id for s in p.sell} & protected)


# ---------------------------------------------------------------------------
# 엔진: 1위 덱 / 고정 덱
# ---------------------------------------------------------------------------


def test_pinned_comp_switch_changes_the_reference(make_advisor, stats):
    st = gs(board=[U("DA_18_Yorick"), U("DA_18_Rakan"), U("DA_18_Varus")],
            bench=[U("DA_18_Shen"), U("DA_18_Akali_AD")], level=5, gold=10, hp=70)
    adv = make_advisor()
    for cid in (ZYRA, LUNAR):
        adv.set_pinned_comp(cid)
        p = adv.advise(st).board_plan
        owned = {"DA_18_Yorick", "DA_18_Rakan", "DA_18_Varus", "DA_18_Shen", "DA_18_Akali_AD"}
        exp = buildup_reference(stats.comp(cid), 5, owned, adv.w.board_plan)
        assert p.comp_id == cid and p.reference_units == exp.units
        assert set(ids(p.lineup)) >= set(p.owned_in_reference)
    assert ZYRA != LUNAR


# ---------------------------------------------------------------------------
# 상점 ★2 규칙(21 §17.6)
# ---------------------------------------------------------------------------


def _shop_rec(make_advisor, pin: str, owned: list[dict], offer: str, rescore: bool = False, **w_over):
    adv = make_advisor()
    if w_over:
        adv = make_advisor(weights_=adv.w.model_copy(update={"shop": adv.w.shop.model_copy(update=w_over)}))
    adv.set_pinned_comp(pin)
    st = gs(board=owned, bench=[], level=5, gold=50, hp=70,
            shop=[{"kind": "champion", "id": offer}] + [{"kind": "empty"}] * 4)
    rec = adv.advise(st)
    if rescore:
        rec = adv.rescore_shop(st.model_copy(update={"screen_mode": "combat"}))
    return next(s for s in rec.shop if s.offer_id == offer)


@pytest.mark.parametrize("rescore", [False, True])
def test_owned_star2_is_not_recommended_unless_three_star_target(make_advisor, rescore):
    s = _shop_rec(make_advisor, ZYRA, [U("DA_18_Yorick", 2)], "DA_18_Yorick", rescore)
    assert not s.buy and s.reason.startswith("이미 2성 보유")
    k = _shop_rec(make_advisor, KHAZIX, [U("DA_18_KhaZix", 2)], "DA_18_KhaZix", rescore)
    assert k.reason.startswith("3성 목표 · 보유 3/9") and "이미 2성" not in k.reason
    three = _shop_rec(make_advisor, KHAZIX, [U("DA_18_KhaZix", 3)], "DA_18_KhaZix", rescore)
    assert not three.buy and three.reason.startswith("이미 3성 보유")


def test_star2_rule_needs_confirmed_name_and_star(make_advisor):
    guessed = _shop_rec(make_advisor, ZYRA, [U("DA_18_Yorick", 2, conf=0.75)], "DA_18_Yorick")
    assert "이미 2성" not in guessed.reason                                 # 추정 이름은 적용하지 않는다
    unknown = _shop_rec(make_advisor, ZYRA, [U("DA_18_Yorick", None)], "DA_18_Yorick")
    assert "이미 2성" not in unknown.reason                                  # 성급 미상은 적용하지 않는다
    off = _shop_rec(make_advisor, ZYRA, [U("DA_18_Yorick", 2)], "DA_18_Yorick", skip_owned_star2=False)
    assert "이미 2성" not in off.reason


def test_star2_rule_blocks_a_buy_that_would_otherwise_happen(make_advisor):
    before = _shop_rec(make_advisor, ZYRA, [U("DA_18_Yorick", 2)], "DA_18_Yorick", skip_owned_star2=False)
    after = _shop_rec(make_advisor, ZYRA, [U("DA_18_Yorick", 2)], "DA_18_Yorick")
    assert before.buy and not after.buy and before.score == after.score


def test_missing_reference_unit_gets_a_shop_bonus(make_advisor):
    """보드 배치 "상점에서 구하세요"와 상점 [구매]가 엇갈리지 않게: 기준 보드 부족 유닛은 상점 가산."""
    board = [U("DA_18_Alistar"), U("DA_18_Ornn")]
    on = _shop_rec(make_advisor, LUNAR, board, "DA_18_Xayah")
    off = _shop_rec(make_advisor, LUNAR, board, "DA_18_Xayah", reference_missing_bonus=0.0)
    assert on.reason.startswith("레벨 5 빌드업 부족") and not off.reason.startswith("레벨")
    assert on.score == pytest.approx(min(1.0, off.score + 0.3), abs=1e-3)
    owned = _shop_rec(make_advisor, LUNAR, board, "DA_18_Ornn")          # 이미 가진 기준 유닛은 가산 없음
    assert not owned.reason.startswith("레벨")


def test_low_trust_frame_shop_bonus_matches_plan_missing(make_advisor):
    """QA 36 W3: 필드 신뢰도가 낮아 보드 배치가 relaxed_view로 세워져도(low_trust) "상점에서 구하세요" 목록과
    상점 부족 가산 대상은 같은 목록이다(21 §17.12)."""
    adv = make_advisor()
    adv.set_pinned_comp(LUNAR)
    st = GameState.model_validate({
        "screen_mode": "planning", "stage": "3-2", "level": 5, "gold": 50, "hp": 70,
        "board": [U("DA_18_Alistar"), U("DA_18_Ornn")], "bench": [],
        "confidence": {"board": 0.5, "bench": 0.5}, "field_source": {"board": "tracked", "bench": "tracked"},
        "shop": [{"kind": "champion", "id": i} for i in ("DA_18_Xayah", "DA_18_Shen", "DA_18_Ornn")]
        + [{"kind": "empty"}] * 2})
    rec = adv.advise(st)
    p = rec.board_plan
    assert p is not None and p.low_trust and "DA_18_Xayah" in p.missing and "DA_18_Ornn" in p.owned_in_reference
    shop = {s.offer_id: s for s in rec.shop}
    for uid in p.missing:
        if uid in shop:
            assert shop[uid].reason.startswith(f"레벨 {p.reference_level} 빌드업 부족")
    assert not shop["DA_18_Ornn"].reason.startswith("레벨")               # 이미 가진 기준 유닛은 가산 없음
