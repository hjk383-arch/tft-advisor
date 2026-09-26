"""보유 유닛의 스테이지별 영향(2026-09-24 사용자 결정, `_workspace/21_board_trust.md` §10).

"2~3스테이지 유닛은 빌드업 — 지나가는 유닛": 목표 덱 선정에서는 초반 1성 유닛을 작게 센다(아이템·증강이 정한다).
상점 구매(2성 사본)와 보드 배치(지금 무엇을 올릴지)는 지금 라운드 문제라 보유 유닛을 그대로 쓴다.
"""
from __future__ import annotations

import pytest

from tft_advisor.advisor.board_plan import plan_board
from tft_advisor.advisor.candidates import scaled_board_weights, unit_proxy, unit_stage
from tft_advisor.advisor.features import build_view
from tft_advisor.config import UnitStageWeights, Weights
from tft_advisor.contracts import GameState

from .conftest import NO_META, with_overrides

ZYRA_CORE = ["DA_18_Zyra", "DA_18_Yorick", "DA_Amumu18", "DA_Vi18"]   # juggernaut-zyra-amumu 핵심 4기
ZYRA = "juggernaut-zyra-amumu"


def state(stage: str, level: int, board: list[dict], *, bench: list[dict] | None = None,
          items: list[str] = ("DA_JeweledGauntlet",), shop: list[str] | None = None) -> GameState:
    raw = {
        "screen_mode": "planning", "stage": stage, "level": level, "gold": 30, "hp": 70,
        "board": board, "bench": bench or [], "augments_owned": [],
        "items": {"completed": [{"id": i} for i in items], "components": []},
        "confidence": {"board": 0.9, "bench": 0.9},
    }
    if shop is not None:
        raw["shop"] = [{"kind": "champion", "id": u} for u in shop] + [{"kind": "empty"}] * (5 - len(shop))
    return GameState.model_validate(raw)


def ones(ids: list[str], **kw) -> list[dict]:
    return [{"id": u, "star": 1, "confidence": 0.9, **kw} for u in ids]


# ---------------------------------------------------------------------------
# 스케줄
# ---------------------------------------------------------------------------


def test_schedule_lookup_and_interpolation():
    us = UnitStageWeights()
    t = {2: 0.15, 3: 0.25, 4: 0.6, 5: 1.0}
    assert us.at(t, 2, 1) == pytest.approx(0.15)
    assert us.at(t, 2, 6) == pytest.approx(0.15 + 0.10 * 5 / 7)          # 2-6 ≈ 0.22
    assert us.at(t, 4, 1) == pytest.approx(0.6)                            # 4스테이지에 올라간다
    assert us.at(t, 4, 7) == pytest.approx(0.6 + 0.4 * 6 / 7)
    assert us.at(t, 5, 1) == us.at(t, 7, 3) == 1.0                         # 5+ 전부
    assert us.at(t, None) == 1.0                                           # 스테이지 미인식 = 예전 동작
    assert us.at(t, 1, 3) == pytest.approx(0.15 + 0.0)                     # 최소 키 아래 → 최소 키 값(보간은 2와 같음)
    flat = UnitStageWeights(interpolate_rounds=False)
    assert flat.at(t, 3, 7) == 0.25


def test_scaled_board_weights_keep_sum_and_move_share_to_items():
    wi, wa, wb = scaled_board_weights(0.45, 0.25, 0.30, 0.2, True)
    assert wi + wa + wb == pytest.approx(1.0)
    assert wb == pytest.approx(0.06) and wi / wa == pytest.approx(0.45 / 0.25)
    assert scaled_board_weights(0.45, 0.25, 0.30, 0.2, False) == pytest.approx((0.45, 0.25, 0.06))
    assert scaled_board_weights(0.45, 0.25, 0.30, 1.0, True) == pytest.approx((0.45, 0.25, 0.30))


def test_toml_schedule_matches_documented_values(weights: Weights):
    us = weights.unit_stage
    assert us.at(us.deck_board_scale, 2, 6) < 0.3 < us.at(us.deck_board_scale, 4, 1) < 1.0 == us.at(us.deck_board_scale, 5, 1)
    assert us.at(us.deck_one_star, 2, 1) < us.at(us.deck_one_star, 4, 1) < us.at(us.deck_one_star, 5, 1)


# ---------------------------------------------------------------------------
# 목표 덱: 같은 보유 유닛, 스테이지 2 vs 5
# ---------------------------------------------------------------------------


def test_same_units_count_less_for_deck_in_stage2(stats, weights):
    comp = stats.comp(ZYRA)
    v2 = build_view(state("2-3", 4, ones(ZYRA_CORE)), stats, 0.6, None)
    v5 = build_view(state("5-1", 8, ones(ZYRA_CORE)), stats, 0.6, None)
    us2, us5 = unit_stage(v2, weights), unit_stage(v5, weights)
    assert us2.board_scale < 0.3 and us5.board_scale == 1.0
    u2 = unit_proxy(comp, v2, 4, weights, us2)
    u5 = unit_proxy(comp, v5, 8, weights, us5)
    assert u5 == 1.0 and u2 < 0.3 * u5


def test_two_star_and_item_holder_still_count_early(stats, weights):
    comp = stats.comp(ZYRA)
    one = build_view(state("2-3", 4, ones(["DA_18_Zyra"])), stats, 0.6, None)
    two = build_view(state("2-3", 4, [{"id": "DA_18_Zyra", "star": 2, "confidence": 0.9}]), stats, 0.6, None)
    held = build_view(state("2-3", 4, ones(["DA_18_Zyra"], items=["DA_ArchangelsStaff"])), stats, 0.6, None)
    us = unit_stage(one, weights)
    u1, u2, uh = (unit_proxy(comp, v, 4, weights, us) for v in (one, two, held))
    assert u1 < uh < u2          # 1성 < 아이템 든 1성 < 2성(2성은 할인 없이 x unit_star_mult)
    assert u2 == pytest.approx(weights.prefilter.unit_w_core * weights.prefilter.unit_star_mult
                               / weights.prefilter.unit_saturation)


@pytest.mark.parametrize("backend", ["off", "mock"])
def test_items_decide_early_units_decide_late(make_advisor, backend):
    """보석 건틀릿(invoker/spellweaver BIS) + zyra 덱 1성 핵심 4기: 2-3은 아이템 덱, 5-1은 유닛 덱이 1위."""
    adv = make_advisor(backend=backend if backend == "off" else None)
    early = adv.advise(state("2-3", 4, ones(ZYRA_CORE)))
    adv.reset()
    late = adv.advise(state("5-1", 8, ones(ZYRA_CORE)))
    assert early.target_comps[0].comp_id in ("spellweaver-veigar", "invoker-ahri")
    assert late.target_comps[0].comp_id == ZYRA
    e, l = early.debug["unit_stage"], late.debug["unit_stage"]
    assert e["weights"]["board"] < l["weights"]["board"] == weights_board(adv)
    assert e["weights"]["item"] > l["weights"]["item"]


def weights_board(adv) -> float:
    return adv.w.comp.wb


# ---------------------------------------------------------------------------
# 상점·보드 배치: 2스테이지에도 보유 유닛을 그대로 쓴다
# ---------------------------------------------------------------------------


def test_shop_still_uses_owned_copies_in_stage2(make_advisor, weights):
    # 케넨 경로 덱은 mini 메타 상위 5 밖 → 제한을 끄고 '2스테이지에도 보유 사본을 센다'만 본다(21 §16)
    rec = make_advisor(weights_=with_overrides(weights, NO_META)).advise(state("2-3", 4, ones(["DA_18_Kennen"]), bench=ones(["DA_18_Kennen"]),
                                      shop=["DA_18_Kennen", "DA_18_Shen"]))
    rows = {r["id"]: r for r in rec.debug["shop"]["rows"]}
    assert rows["DA_18_Kennen"]["bonus"] >= make_advisor().w.shop.two_star_bonus   # 3번째 사본 → 2성
    assert "보유 2" in rows["DA_18_Kennen"]["reason"]
    kennen = next(s for s in rec.shop if s.offer_id == "DA_18_Kennen")
    shen = next(s for s in rec.shop if s.offer_id == "DA_18_Shen")
    assert kennen.score > shen.score


def _plan(stats, weights, stage: str, board, bench, level: int, stage_board=None):
    view = build_view(state(stage, level, board, bench=bench), stats, 0.6, None)
    return plan_board(view, stats, stats.comp(ZYRA), level, {}, lambda i: stats.name(i, "ko") or i,
                      weights.board_plan, stage=weights.unit_stage, stage_board=stage_board)


def test_board_plan_prefers_strong_now_early_and_final_deck_late(stats, weights):
    """1칸: 2성 무관 유닛(알리스타) vs 1성 목표 덱 핵심(요릭). 2스테이지는 지금 강한 2성, 5스테이지는 목표 덱 핵심."""
    board = [{"id": "DA_18_Alistar", "star": 2, "confidence": 0.9}]
    bench = ones(["DA_18_Yorick"])
    early = _plan(stats, weights, "2-3", board, bench, 1)
    late = _plan(stats, weights, "5-1", board, bench, 1)
    assert [e.unit_id for e in early.lineup] == ["DA_18_Alistar"] and early.swaps == []
    assert [e.unit_id for e in late.lineup] == ["DA_18_Yorick"]
    # 전환 경로: 지금 전력용 유닛과 모을 목표 덱 유닛을 보여 준다
    tr = early.transition
    assert tr is not None and tr.placeholder == ["DA_18_Alistar"] and tr.keep == []
    nb = stats.comp(ZYRA).buildup[tr.next_level]                          # 보드가 있는 다음 레벨(레벨 1 → 4)
    assert tr.next_level == min(stats.comp(ZYRA).buildup) and nb
    assert tr.next_targets and "DA_18_Yorick" not in tr.next_targets     # 이미 가진 유닛은 모을 목록에서 뺀다
    assert any(n.startswith("전환: ") and "교체 예정" in n for n in early.notes)
    assert late.transition.keep == ["DA_18_Yorick"]


def test_board_plan_uses_owned_units_in_stage2(stats, weights):
    p = _plan(stats, weights, "2-3", ones(["DA_18_Shen", "DA_18_Kennen"]), ones(["DA_18_Zyra"]), 3)
    assert {e.unit_id for e in p.lineup} == {"DA_18_Shen", "DA_18_Kennen", "DA_18_Zyra"}

