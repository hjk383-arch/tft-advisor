"""advisor 단위 테스트: 파생값(features), 1차 필터, 각 스코어러, 결측 규칙(§4.3), 폴백·게이트웨이, 엔진 모드."""
from __future__ import annotations

import asyncio
from collections import Counter

import httpx2
import pytest

from tft_advisor.advisor import Advisor, JevGateway, MockJevBackend
from tft_advisor.advisor.candidates import dedupe, prefilter, stat_norm
from tft_advisor.advisor.engine import resource_signature
from tft_advisor.advisor.features import (
    board_at,
    build_view,
    buy_makes_2star,
    clean_desc,
    copies_owned,
    craftable_items,
    estimate_level,
    hp_bucket,
    item_fit,
    next_buildup_board,
)
from tft_advisor.advisor.jev_client import JevCallError, classify_exception
from tft_advisor.advisor.questions import QMeta
from tft_advisor.advisor.scoring import Scorer
from tft_advisor.contracts import (
    CompStats,
    FallbackReason,
    GameState,
    ItemState,
    ItemRef,
    ScreenMode,
    UnitOnBoard,
)

C = "DA_Component_"


def gs(**kw) -> GameState:
    kw.setdefault("screen_mode", "planning")
    return GameState.model_validate(kw)


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------


def test_hp_bucket_edges():
    assert [hp_bucket(x) for x in (None, 100, 70, 69, 40, 39, 20, 19, 0)] == [
        "unknown", "healthy", "healthy", "moderate", "moderate", "low", "low", "critical", "critical"]


def test_estimate_level_rules():
    lt = {5: "2-5", 6: "3-2", 8: "4-2"}
    assert estimate_level("3-2", lt) == 6
    assert estimate_level("3-5", lt) == 6
    assert estimate_level("4-2", lt) == 8
    # 공집합: 모든 timing보다 이른 stage → min(키) − 1 (QA 5절 jev 3)
    assert estimate_level("1-4", lt) == 4
    assert estimate_level("1-1", {1: "1-4"}) == 1   # 하한 1
    assert estimate_level(None, lt) is None
    assert estimate_level("3-2", {}) is None


def test_next_buildup_board_uses_level_timing_skip(stats):
    comp = stats.comp("juggernaut-zyra-amumu")
    b = next_buildup_board(comp, 6)
    assert b is not None and b.level == 7
    assert next_buildup_board(comp, 10) is None
    assert next_buildup_board(comp, None) is None
    # games 최대 보드 선택
    lv7 = comp.buildup[7]
    assert b.games == max(x.games or 0 for x in lv7)


def test_craftable_items_and_duplicate_components(stats):
    got = craftable_items([C + "NeedlesslyLargeRod", C + "TearOfTheGoddess", C + "ChainVest"], stats)
    assert "DA_ArchangelsStaff" in got
    assert "DA_BrambleVest" not in got   # 조끼 1개로는 조끼+조끼 불가
    got2 = craftable_items([C + "ChainVest", C + "ChainVest"], stats)
    assert list(got2) == ["DA_BrambleVest"]


def test_copies_and_two_star():
    units = [UnitOnBoard(id="DA_18_Ornn", star=1), UnitOnBoard(id="DA_18_Ornn", star=1),
             UnitOnBoard(id="DA_18_Ornn", star=2)]
    assert copies_owned("DA_18_Ornn", units) == 5
    assert buy_makes_2star("DA_18_Ornn", units)
    assert not buy_makes_2star("DA_18_Ornn", units[:1])


def test_clean_desc_question_marks():
    assert clean_desc("Gain 3 gold.") == ("Gain 3 gold.", False)
    assert clean_desc("Gain ? gold.") == ("Gain X gold.", True)          # 숫자 없음 → 의미 손실
    assert clean_desc("Gain ? gold and 2 XP.") == ("Gain X gold and 2 XP.", False)
    assert clean_desc(None) == (None, True)


def test_item_fit_ladder(stats, weights):
    zyra = stats.comp("juggernaut-zyra-amumu")
    w = weights.item_fit
    assert item_fit("DA_ArchangelsStaff", zyra, stats, w) == w.carry_bis
    # 상징(§2.2 Phase 3 수정): +1명이 핵심 특성의 다음 구간에 닿을 때만 key
    assert item_fit("DA_18_EmblemHunter", zyra, stats, w) == w.emblem_key_trait      # Hunter 2 → 3(구간 2/3/4/5)
    assert item_fit("DA_18_EmblemJuggernaut", zyra, stats, w) == w.emblem_other      # Juggernaut 6 = 최고 구간
    assert item_fit("DA_18_EmblemSprykin", zyra, stats, w) == w.emblem_other         # key_traits 밖
    assert item_fit("DA_TacticiansCape", zyra, stats, w) == 0.0


def test_emblem_advances_rule(stats):
    from tft_advisor.advisor.features import emblem_advances

    comps = {c.comp_id: c for c in stats.comps()}
    for c in comps.values():
        for t in c.key_traits:
            bp = set(stats.trait_breakpoints(t.id))
            n = len({u.id for u in c.final_board if t.id in stats.champion_traits(u.id)})
            expect = t.count >= 2 and (n < t.count or max(t.count, n) + 1 in bp)
            assert emblem_advances(t.id, c, stats) is expect, (c.comp_id, t)
        assert not emblem_advances("DA_18_NotATrait", c, stats)


def test_view_reliability_filter(stats):
    st = gs(stage="3-2", level=6, hp=50, gold=10,
            board=[UnitOnBoard(id="DA_18_Ornn", confidence=0.3), UnitOnBoard(id="DA_18_Varus")], bench=[],
            items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff", confidence=0.4), ItemRef(id="DA_Deathblade")],
                            others=[ItemRef(id="DA_Artifact_LichBane"), ItemRef(id="DA_TacticiansCape")]),
            confidence={"hp": 0.5, "level": 0.9})
    v = build_view(st, stats, 0.6, None)
    assert v.hp is None and v.hp_bucket == "unknown"
    assert v.level == 6
    assert [u.id for u in v.board] == ["DA_18_Varus"]
    assert v.completed == ["DA_Deathblade"]
    # 유물은 보유 풀, 전략가 망토는 기타(QA WARN N5a)
    assert v.others_owned == ["DA_Artifact_LichBane"]
    assert "DA_Artifact_LichBane" in v.owned_pool(stats)
    # board만 있고 bench None → 보드는 쓰되 "부분 확인"(21_board_trust: 보드·벤치 따로 판정)
    v2 = build_view(gs(board=[UnitOnBoard(id="DA_18_Ornn")]), stats, 0.6, None)
    assert v2.units_known and v2.units_partial and v2.board_complete
    assert [u.id for u in v2.units] == ["DA_18_Ornn"]


# ---------------------------------------------------------------------------
# 1차 필터
# ---------------------------------------------------------------------------


def test_prefilter_min_games_quota_and_prev(stats, weights):
    v = build_view(gs(stage="3-2", level=6), stats, 0.6, None)
    cands, pool = prefilter(v, stats, weights, 4, prev_shown=["inferno-ashe"])
    ids = [c.comp_id for c in cands]
    assert "lunar-aphelios-kayle" not in {c.comp_id for c in pool}   # games 604 < 1000
    assert "inferno-ashe" in ids                                      # 직전 표시 쿼터
    top_s = sorted(pool, key=lambda c: 0)  # noqa: F841 - 가독성
    best2 = sorted(cands + [], key=lambda c: -c.S)
    assert len(ids) == 4 and len(set(ids)) == 4
    assert cands == sorted(cands, key=lambda c: (-c.p, c.comp_id))
    assert best2


def test_dedupe_same_carry_high_overlap(stats):
    z = stats.comp("juggernaut-zyra-amumu")
    clone = z.model_copy(update={"comp_id": "clone", "games": 10})
    kept = dedupe([z, clone], 0.75)
    assert [c.comp_id for c in kept] == ["juggernaut-zyra-amumu"]


def test_stat_norm_clip(weights):
    assert stat_norm(3.5, weights) == 1.0
    assert stat_norm(6.0, weights) == 0.0
    assert 0 < stat_norm(4.6, weights) < 1


# ---------------------------------------------------------------------------
# 스코어러
# ---------------------------------------------------------------------------


def make_scorer(stats, weights, settings, state: GameState, answers=None, **kw) -> Scorer:
    v = build_view(state, stats, settings.vision.state_min_confidence, None)
    cands, pool = prefilter(v, stats, weights, settings.advisor.max_candidate_comps)
    sc = Scorer(view=v, stats=stats, w=weights, settings=settings, cands=cands, pool=pool,
                owned=v.owned_pool(stats), craftable=craftable_items(v.components, stats) if v.items_known else {},
                answers=answers, **kw)
    sc.score_comps()
    return sc


def test_comp_score_mass_moves_to_stats_without_resources(stats, weights, settings):
    sc = make_scorer(stats, weights, settings, gs(stage="2-1", level=4, items=ItemState(), board=[], bench=[]))
    for r in sc.comp_rows:
        assert r["m"] == 0
        assert r["score"] == pytest.approx(r["cand"].S)


def test_low_confidence_jev_shifts_weight_to_stats(stats, weights, settings, make_advisor):
    st = gs(stage="3-2", level=6, items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")]))
    hi = make_advisor(confidence=0.9).advise(st)
    lo = make_advisor(confidence=0.2).advise(st)
    t_hi = next(c for c in hi.debug["candidates"] if c["comp_id"] == "juggernaut-zyra-amumu")
    t_lo = next(c for c in lo.debug["candidates"] if c["comp_id"] == "juggernaut-zyra-amumu")
    assert t_hi["terms"]["item"]["gate"] == 1.0
    assert t_lo["terms"]["item"]["gate"] == weights.jev.low_confidence_scale


def test_items_ready_artifact_owned_and_never_craftable(stats, weights, settings):
    """QA WARN N5a: others의 유물은 owned, 제작 불가 BIS는 craftable 판정 없음·component_priority 제외."""
    st = gs(stage="3-2", level=6,
            items=ItemState(others=[ItemRef(id="DA_Artifact_LichBane", category="artifact")],
                            components=[ItemRef(id=C + "BFSword")] * 4))
    sc = make_scorer(stats, weights, settings, st)
    kz = next(c for c in sc.cands if c.comp_id == "executioner-khazix")
    ready = {r.item_id: r.status for r in sc.items_ready(kz)}
    assert ready["DA_Artifact_LichBane"] == "owned"
    st2 = gs(stage="3-2", level=6, items=ItemState(components=[ItemRef(id=C + "BFSword")] * 4))
    sc2 = make_scorer(stats, weights, settings, st2)
    kz2 = next(c for c in sc2.cands if c.comp_id == "executioner-khazix")
    r2 = {r.item_id: r.status for r in sc2.items_ready(kz2)}
    assert r2["DA_Artifact_LichBane"] == "missing"
    assert stats.recipe("DA_Artifact_LichBane") is None


def test_items_ready_duplicate_bis_and_component_reuse(stats, weights, settings):
    st = gs(stage="3-2", level=6, items=ItemState(
        components=[ItemRef(id=C + "NeedlesslyLargeRod"), ItemRef(id=C + "TearOfTheGoddess")]))
    sc = make_scorer(stats, weights, settings, st)
    z = next(c for c in sc.cands if c.comp_id == "juggernaut-zyra-amumu")
    got = [r.status for r in sc.items_ready(z)]
    assert got == ["craftable", "missing", "missing"]   # 재료 1세트는 대천사 1개만
    assert all(r.holder_unit_id == "DA_18_Zyra" for r in sc.items_ready(z))


def test_shop_gold_accumulation_limits_buys(make_advisor):
    shop = [{"kind": "champion", "id": x} for x in
            ("DA_18_Ornn", "DA_18_LeBlanc", "DA_18_Veigar", "DA_18_RekSai", "DA_18_Varus")]
    rich = make_advisor().advise(gs(stage="2-1", level=4, gold=50, shop=shop, board=[], bench=[]))
    poor = make_advisor().advise(gs(stage="2-1", level=4, gold=2, shop=shop, board=[], bench=[]))
    assert sum(s.buy for s in rich.shop) > sum(s.buy for s in poor.shop)
    spent = sum(s.buy * (1 if s.offer_id != "DA_18_LeBlanc" else 2) for s in poor.shop)
    assert spent <= 2


def test_two_star_bonus_only_when_units_known(make_advisor):
    shop = [{"kind": "champion", "id": "DA_18_Warwick"}] + [{"kind": "empty"}] * 4
    known = make_advisor().advise(gs(stage="2-1", level=4, gold=10, shop=shop, bench=[],
                                     board=[{"id": "DA_18_Warwick"}, {"id": "DA_18_Warwick"}]))
    assert known.shop[0].reason_tag.value == "two_star"
    unknown = make_advisor().advise(gs(stage="2-1", level=4, gold=10, shop=shop))
    assert unknown.shop[0].reason_tag is None or unknown.shop[0].reason_tag.value != "two_star"
    assert "copies_owned" not in unknown.debug["jev_state"]["shop"][0]


def test_hp_danger_shift_applies_only_when_known(stats, weights, settings):
    low = make_scorer(stats, weights, settings, gs(stage="4-1", level=7, hp=25))
    unk = make_scorer(stats, weights, settings, gs(stage="4-1", level=7))
    base = weights.shop.for_stage(4)
    assert unk.shop_weights() == (base.ws, base.wp)
    assert low.shop_weights()[0] == pytest.approx(base.ws + weights.shop.hp_danger_shift)


def test_item_hold_rules(make_advisor):
    comps = [ItemRef(id=C + "Spatula"), ItemRef(id=C + "NegatronCloak")]
    base = dict(stage="2-5", level=5, items=ItemState(components=comps))
    assert make_advisor().advise(gs(**base, hp=90)).item.hold is True
    assert make_advisor().advise(gs(**base)).item.hold is True          # hp 모름 = moderate
    assert make_advisor().advise(gs(**base, hp=10)).item.hold is False   # critical 안전장치
    late = dict(base, stage="4-2")
    assert make_advisor().advise(gs(**late, hp=90)).item.hold is False  # hold_until_stage
    # Jev가 hold를 골라도 critical이면 무시
    r = make_advisor(overrides={"item_pick": "hold_components"}).advise(gs(**base, hp=10))
    assert r.item.hold is False


def test_item_suggestions_do_not_reuse_components(make_advisor):
    comps = [ItemRef(id=C + x) for x in ("NeedlesslyLargeRod", "TearOfTheGoddess", "ChainVest", "BFSword")]
    rec = make_advisor().advise(gs(stage="3-2", level=6, hp=60, items=ItemState(components=comps)))
    used = Counter(c for s in rec.item.suggestions for c in s.components)
    assert all(n <= 1 for n in used.values())
    assert len([s for s in rec.item.suggestions if s.components]) <= 2


def test_item_holder_comes_from_the_deck_whose_fit_was_used(stats, weights, settings):
    """27 W3: 아이템 적합도(bis)를 준 덱과 화면의 보유자 덱이 같아야 한다. 점수(bis·st)는 그대로."""
    comps = [ItemRef(id=C + x) for x in ("BFSword", "GiantsBelt", "ChainVest", "RecurveBow",
                                         "NeedlesslyLargeRod", "TearOfTheGoddess")]
    sc = make_scorer(stats, weights, settings, gs(stage="3-2", level=6, hp=60, items=ItemState(components=comps)))
    assert len(sc.shown) >= 2
    top = sc.shown[0]["cand"]
    shown = {r["cand"].comp_id: r["cand"] for r in sc.shown}
    crossed = 0
    for x in sc.craftable:
        bx, src = sc.bis_source(x)
        assert bx == sc.bis(x)
        h, deck = sc.item_holder(x)
        if deck is None:     # 1위 덱 기준(예전 동작)
            assert h == sc.holder_for(x)
            assert src is None or src.comp_id == top.comp_id or src.comp_id not in shown                 or sc.holder_for(x, src.comp) is None
        else:                # 2·3위 표시 덱 기준 → 그 덱의 보유자와 덱 이름
            crossed += 1
            assert src.comp_id in shown and src.comp_id != top.comp_id and deck == src.comp.name
            assert h == sc.holder_for(x, src.comp) and h in {u.id for u in src.comp.final_board}
    assert crossed >= 1
    adv = sc.item_advice()
    rows = {r["item"]: r for r in sc.debug["item"]["rows"]}
    for s_ in adv.suggestions:
        r = rows.get(s_.item_id)
        if r is not None and r["deck"]:
            assert s_.reason.endswith(f"({r['deck']})") and s_.holder_unit_id == r["holder"]
        # 점수의 st는 여전히 1위 덱 보유자 통계
        if r is not None:
            assert r["st"] == sc.item_stat(r["item"], sc.holder_for(r["item"]))


def test_augment_fallback_prefers_trait_augment(make_advisor):
    st = gs(screen_mode="augment_select", stage="3-2", level=6, hp=70, board=[{"id": "DA_18_Ornn"}], bench=[],
            augment_offer=[{"id": "DA_Hustler"}, {"id": "DA_18_ElderwoodTraitAugment"}])
    rec = make_advisor(fail=FallbackReason.TIMEOUT).advise(st)
    assert rec.jev_used is False and rec.fallback_reason == FallbackReason.TIMEOUT
    assert rec.augment.pick == "DA_18_ElderwoodTraitAugment"
    assert rec.shop == []


def test_new_augment_without_description_forces_low_gate(stats, make_advisor):
    rec = make_advisor().advise(gs(screen_mode="augment_select", stage="3-2", level=6,
                                   augment_offer=[{"id": "DA_Lineup"}, {"id": "DA_Hustler"}]))
    offer = rec.debug["jev_state"]["augment_offer"]
    assert offer[0]["description"] == "unknown (new augment)"


def test_hysteresis_bonus_keeps_previous_when_signature_same(stats, weights, settings):
    st = gs(stage="3-2", level=6, items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")]))
    base = make_scorer(stats, weights, settings, st)
    second = base.order[1]["cand"].comp_id
    held = make_scorer(stats, weights, settings, st, prev_shown=[second], sig_unchanged=True)
    row = next(r for r in held.comp_rows if r["cand"].comp_id == second)
    assert row["H"] == 1 and row["final"] == pytest.approx(min(1, row["score"] + weights.comp.hysteresis_bonus))
    changed = make_scorer(stats, weights, settings, st, prev_shown=[second], sig_unchanged=False)
    assert all(r["H"] == 0 for r in changed.comp_rows)


def test_resource_signature_ignores_components(stats):
    a = build_view(gs(items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")])), stats, 0.6, None)
    b = build_view(gs(items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")],
                                      components=[ItemRef(id=C + "BFSword")])), stats, 0.6, None)
    assert resource_signature(a, a.owned_pool(stats), stats) == resource_signature(b, b.owned_pool(stats), stats)


def test_component_priority_from_missing_bis(make_advisor):
    rec = make_advisor().advise(gs(stage="3-2", level=6, items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")])))
    assert rec.component_priority
    assert len(rec.component_priority) <= 10
    assert all(x.startswith("DA_Component_") for x in rec.component_priority)


# ---------------------------------------------------------------------------
# 결측 규칙 §4.3 / 질문 변형
# ---------------------------------------------------------------------------


def test_nohp_variants_and_key_omission(make_advisor):
    adv = make_advisor()
    st = gs(stage="3-2", level=6, items=ItemState(components=[ItemRef(id=C + "Spatula"), ItemRef(id=C + "NegatronCloak")]),
            augment_offer=[{"id": "DA_Hustler"}], screen_mode="augment_select")
    rec = adv.advise(st)
    js = rec.debug["jev_state"]
    assert "health" not in js["game"] and "health_status" not in js["game"]
    q = adv.gateway.backend.last_questions
    assert "health" not in q["aug_standalone_0"]["instructions"]
    assert "too late in the game" in q["aug_standalone_0"]["criteria"][0]
    assert "wait for better components" in q["item_pick"]["criteria"]["hold_components"]
    assert "health" not in q["item_pick"]["criteria"]["hold_components"]
    rec2 = adv.advise(st.model_copy(update={"hp": 50}))
    q2 = adv.gateway.backend.last_questions
    assert rec2.debug["jev_state"]["game"]["health_status"] == "moderate"
    assert "health" in q2["aug_standalone_0"]["instructions"]


def test_main_carry_is_compstats_carry(make_advisor, stats):
    rec = make_advisor().advise(gs(stage="3-2", level=6))
    for entry in rec.debug["jev_state"]["candidate_comps"]:
        comp = next(c for c in stats.comps() if (c.name_en or c.comp_id) == entry["name"])
        assert entry["main_carry"] == stats.name(comp.carry, "en")
        roles = [u.get("role") for u in entry["final_board"]]
        assert roles.count("main carry") == 1


# ---------------------------------------------------------------------------
# Jev 게이트웨이·폴백
# ---------------------------------------------------------------------------


def _api_error(status: int):
    import typesafe_sdk as ts
    from typesafe_sdk._core.errors import api_error

    return api_error(status, {"error": "x"}, httpx2.Headers({}))


@pytest.mark.parametrize("status,reason", [
    (401, FallbackReason.AUTH), (403, FallbackReason.AUTH), (429, FallbackReason.RATE_LIMITED),
    (529, FallbackReason.OVERLOADED), (500, FallbackReason.SERVER_ERROR), (503, FallbackReason.SERVER_ERROR),
    (400, FallbackReason.BAD_REQUEST), (422, FallbackReason.BAD_REQUEST), (404, FallbackReason.BAD_REQUEST),
])
def test_classify_http_errors(status, reason):
    assert classify_exception(_api_error(status)) == reason


def test_classify_other_errors():
    import typesafe_sdk as ts

    assert classify_exception(ts.TypeSafeAPITimeoutError(1.2)) == FallbackReason.TIMEOUT
    assert classify_exception(ts.TypeSafeAPIConnectionError("boom")) == FallbackReason.CONNECTION
    assert classify_exception(asyncio.TimeoutError()) == FallbackReason.TIMEOUT
    assert classify_exception(ValueError("bug")) == FallbackReason.BAD_REQUEST
    assert classify_exception(JevCallError(FallbackReason.AUTH)) == FallbackReason.AUTH


def _meta():
    return {"q": QMeta("score", 4, 0.5)}


def _qs():
    return {"q": {"type": "score", "instructions": "x", "criteria": ["a", "b", "c", "d"]}}


def test_gateway_cache_and_disabled(settings):
    be = MockJevBackend()
    gw = JevGateway(be, settings.advisor)
    r1 = asyncio.run(gw.ask("k", {"a": 1}, _qs(), _meta()))
    r2 = asyncio.run(gw.ask("k", {"a": 1}, _qs(), _meta()))
    assert be.calls == 1 and r2.answers.cached and not r1.answers.cached
    off = JevGateway(be, settings.advisor.model_copy(update={"jev_enabled": False}))
    assert asyncio.run(off.ask("k2", {}, _qs(), _meta())).reason == FallbackReason.JEV_DISABLED
    nob = JevGateway(None, settings.advisor)
    assert asyncio.run(nob.ask("k3", {}, _qs(), _meta())).reason == FallbackReason.JEV_DISABLED


def test_gateway_circuit_breaker_and_auth_disable(settings):
    now = [0.0]
    be = MockJevBackend(fail=FallbackReason.OVERLOADED)
    gw = JevGateway(be, settings.advisor, clock=lambda: now[0])
    reasons = [asyncio.run(gw.ask(f"k{i}", {}, _qs(), _meta())).reason for i in range(4)]
    assert reasons[:3] == [FallbackReason.OVERLOADED] * 3
    assert reasons[3] == FallbackReason.CIRCUIT_OPEN and be.calls == 3
    now[0] += settings.advisor.circuit_cooldown_s + 1
    assert asyncio.run(gw.ask("k9", {}, _qs(), _meta())).reason == FallbackReason.OVERLOADED   # 쿨다운 후 재시도

    auth_be = MockJevBackend(fail=FallbackReason.AUTH)
    gw2 = JevGateway(auth_be, settings.advisor)
    assert asyncio.run(gw2.ask("a", {}, _qs(), _meta())).reason == FallbackReason.AUTH
    assert asyncio.run(gw2.ask("b", {}, _qs(), _meta())).reason == FallbackReason.AUTH
    assert auth_be.calls == 1   # 세션 동안 비활성


def test_gateway_timeout_budget(settings):
    cfg = settings.advisor.model_copy(update={"jev_retry_budget_s": 0.05, "jev_timeout_s": 0.05})
    gw = JevGateway(MockJevBackend(delay_s=0.5), cfg)
    t = asyncio.run(gw.ask("k", {}, _qs(), _meta()))
    assert t.reason == FallbackReason.TIMEOUT and t.answers is None


def test_live_backend_without_key_is_auth(settings, monkeypatch, make_advisor):
    from tft_advisor.advisor import LiveJevBackend

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    rec = make_advisor(backend=LiveJevBackend(settings.advisor)).advise(gs(stage="3-2", level=6))
    assert rec.jev_used is False and rec.fallback_reason == FallbackReason.AUTH
    assert rec.target_comps   # 폴백으로도 추천은 나온다


def test_fallback_keeps_resource_logic(make_advisor):
    st = gs(stage="3-2", level=6, items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")]))
    ok = make_advisor().advise(st)
    fb = make_advisor(fail=FallbackReason.SERVER_ERROR).advise(st)
    assert fb.fallback_reason == FallbackReason.SERVER_ERROR
    assert fb.target_comps[0].comp_id == ok.target_comps[0].comp_id == "juggernaut-zyra-amumu"
    assert any("Jev 미사용" in r for r in fb.target_comps[0].reasons)
    assert fb.state_hash == ok.state_hash   # 폴백도 보낼 예정이던 state로 해시


def test_off_backend_reason(stats, settings, weights):
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend="off")
    rec = adv.advise(gs(stage="3-2", level=6))
    assert rec.fallback_reason == FallbackReason.JEV_DISABLED


# ---------------------------------------------------------------------------
# 엔진 모드
# ---------------------------------------------------------------------------


def test_modes_keep_reset_and_carousel(make_advisor):
    adv = make_advisor()
    st = gs(stage="3-2", level=6, items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")]))
    first = adv.advise(st)
    assert adv.advise(st.model_copy(update={"screen_mode": ScreenMode.COMBAT})) is first
    assert adv.advise(st.model_copy(update={"screen_mode": ScreenMode.UNKNOWN})) is first
    assert adv.advise(st.model_copy(update={"screen_mode": ScreenMode.ITEM_SELECT})) is first
    calls = adv.gateway.backend.calls
    car = adv.advise(st.model_copy(update={"screen_mode": ScreenMode.CAROUSEL,
                                           "items": ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")],
                                                              components=[ItemRef(id=C + "TearOfTheGoddess")])}))
    assert adv.gateway.backend.calls == calls   # 캐러셀은 Jev 호출 없음
    assert car.target_comps == first.target_comps
    assert C + "TearOfTheGoddess" not in car.component_priority or car.component_priority != first.component_priority
    assert adv.advise(st.model_copy(update={"screen_mode": ScreenMode.GAME_OVER})) is None
    assert adv.session.last is None and adv.session.prev_shown == []
    assert adv.advise(st.model_copy(update={"screen_mode": ScreenMode.COMBAT})) is None


def test_equipped_tracking_and_return(make_advisor):
    adv = make_advisor()
    arch = ItemRef(id="DA_ArchangelsStaff")
    adv.advise(gs(stage="3-1", level=6, items=ItemState(completed=[arch])))
    adv.advise(gs(stage="3-2", level=6, items=ItemState()))
    assert adv.session.equipped_tracked == Counter({"DA_ArchangelsStaff": 1})
    adv.advise(gs(stage="3-3", level=6, items=ItemState(completed=[arch])))   # 판매로 벤치 복귀
    assert adv.session.equipped_tracked == Counter()
    # 신뢰 불가 프레임이 끼면 추적하지 않는다
    adv.advise(gs(stage="3-4", level=6, items=ItemState(completed=[arch]), confidence={"items": 0.2}))
    adv.advise(gs(stage="3-5", level=6, items=ItemState()))
    assert adv.session.equipped_tracked == Counter()


def test_cache_hit_on_identical_state(make_advisor):
    adv = make_advisor()
    st = gs(stage="3-2", level=6, gold=20)
    r1 = adv.advise(st)
    r2 = adv.advise(st)
    assert adv.gateway.backend.calls == 1
    assert r2.debug["jev"]["cached"] is True and r1.state_hash == r2.state_hash
    r3 = adv.advise(st.model_copy(update={"gold": 21}))
    assert r3.state_hash != r1.state_hash and adv.gateway.backend.calls == 2


def test_api_key_never_in_output(monkeypatch, make_advisor, caplog):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test-SHOULD-NOT-LEAK-123")
    caplog.set_level("DEBUG")
    rec = make_advisor(fail=FallbackReason.AUTH).advise(gs(stage="3-2", level=6))
    assert "SHOULD-NOT-LEAK" not in rec.model_dump_json()
    assert "SHOULD-NOT-LEAK" not in caplog.text


def test_latency_budget_full_stats(settings, weights):
    """실제(전체) 통계로도 mock 추천 1회가 예산 안(코드 경로 ≤ 수백 ms)."""
    from tft_advisor.advisor import load_stats

    adv = Advisor(stats=load_stats(), settings=settings, weights=weights, backend="mock")
    st = gs(stage="3-2", level=6, gold=30, hp=60,
            shop=[{"kind": "champion", "id": "DA_18_Ornn"}] * 5,
            items=ItemState(completed=[ItemRef(id="DA_ArchangelsStaff")],
                            components=[ItemRef(id=C + "BFSword"), ItemRef(id=C + "ChainVest")]))
    adv.advise(st)
    rec = adv.advise(st.model_copy(update={"gold": 31}))
    assert rec.latency_ms < 500
    assert isinstance(CompStats.model_validate(adv.stats.comps()[0].model_dump()), CompStats)


# ---------------------------------------------------------------------------
# 아이템: 1위 덱 우선 재료 배분 (2026-09-25, _workspace/21_board_trust.md §13)
# ---------------------------------------------------------------------------
_APH_COMPS = ("RecurveBow", "NeedlesslyLargeRod", "SparringGloves", "FryingPan")
_ORNN_BOARD = [{"id": x, "star": 1} for x in ("DA_18_Ornn", "DA_18_Varus", "DA_18_Xayah", "DA_18_Shen")]


def _item_state(stage, level, comps, board=None, **kw):
    d = dict(stage=stage, level=level, hp=60, items=ItemState(components=[ItemRef(id=C + x) for x in comps]), **kw)
    if board is not None:
        d.update(board=board, bench=[])
    return gs(**d)


def _with_item(weights, **upd):
    return weights.model_copy(update={"item": weights.item.model_copy(update=upd)})


def test_top_carry_bis_first_even_when_another_deck_item_scores_higher(stats, weights, settings):
    """1위 덱 캐리 BIS(구인수 = 곡궁+지팡이)와 다른 표시 덱 아이템(보석 건틀릿 = 지팡이+장갑)이 지팡이를 나눠 쓰고,
    건틀릿 점수가 더 높다. 예전 탐욕 선택은 건틀릿을 먼저 골라 구인수를 건너뛰었다."""
    sc = make_scorer(stats, weights, settings, _item_state("3-2", 6, _APH_COMPS))
    top = sc.shown[0]["cand"].comp
    assert top.comp_id == "lunar-aphelios-nidalee_ap" and "DA_GuinsoosRageblade" in top.carry_bis_items
    orig = sc.bis
    sc.bis = lambda x: 1.0 if x == "DA_JeweledGauntlet" else orig(x)     # 다른 덱 아이템을 최고 점수로
    _, src = sc.bis_source("DA_JeweledGauntlet")
    assert src is not None and src.comp_id != top.comp_id                # 다른 덱에서 온 적합도
    adv = sc.item_advice()
    rows = {r["item"]: r for r in sc.debug["item"]["rows"]}
    jg, gr = rows["DA_JeweledGauntlet"], rows["DA_GuinsoosRageblade"]
    assert jg["score"] > gr["score"] and jg["role"] is None and gr["role"] == "carry"
    assert set(sc.craftable["DA_JeweledGauntlet"]) & set(sc.craftable["DA_GuinsoosRageblade"])   # 재료 공유
    # 예전 탐욕 선택이라면 건틀릿이 먼저 → 구인수 불가
    old, rem = [], Counter(sc.view.components)
    for r in sorted(rows.values(), key=lambda r: (-r["score"], r["item"])):
        need = Counter(sc.craftable[r["item"]])
        if len(old) < 2 and all(rem[k] >= n for k, n in need.items()):
            rem.subtract(need)
            old.append(r["item"])
    assert "DA_GuinsoosRageblade" not in old
    # 새 배분: 구인수가 먼저, 아펠리오스에게. 건틀릿은 빠지고, 남은 재료의 아이템은 '보조'로만
    s0 = adv.suggestions[0]
    assert s0.item_id == "DA_GuinsoosRageblade" and s0.holder_unit_id == top.carry
    assert s0.reason.startswith("1위 덱(") and "캐리 아이템" in s0.reason
    ids = [s.item_id for s in adv.suggestions]
    assert "DA_JeweledGauntlet" not in ids
    used = Counter(c for s in adv.suggestions for c in s.components)
    assert all(n <= 1 for n in used.values())
    for s in adv.suggestions[1:]:
        assert s.reason.startswith("보조: "), s.reason
    assert sc.debug["item"]["mode"] == "top"


def test_top_core_item_does_not_take_the_carry_bis_components(stats, weights, settings):
    """1위 덱 핵심(상징)이 캐리 BIS보다 점수가 높고 재료(곡궁)를 나눠 써도 캐리 BIS가 먼저다(사전식: 캐리 > 핵심)."""
    sc = make_scorer(stats, weights, settings, _item_state("3-2", 6, _APH_COMPS))
    adv = sc.item_advice()
    rows = {r["item"]: r for r in sc.debug["item"]["rows"]}
    assert rows["DA_18_EmblemRapidfire"]["role"] == "core"
    assert rows["DA_18_EmblemRapidfire"]["score"] > rows["DA_GuinsoosRageblade"]["score"]
    assert adv.suggestions[0].item_id == "DA_GuinsoosRageblade"
    assert "DA_18_EmblemRapidfire" not in [s.item_id for s in adv.suggestions]


def test_secondary_item_never_uses_a_component_the_missing_carry_bis_needs(stats, weights, settings):
    """아직 없는 1위 덱 캐리 BIS의 재료를 다른 덱·범용 아이템이 가져가는 조합은 '보조'로 내지 않는다."""
    checked = 0
    names = ("BFSword", "ChainVest", "RecurveBow", "GiantsBelt", "NeedlesslyLargeRod", "TearOfTheGoddess",
             "NegatronCloak", "SparringGloves", "Spatula", "FryingPan")
    import itertools as it

    for comps in it.combinations(names, 4):
        sc = make_scorer(stats, weights, settings, _item_state("3-2", 6, comps))
        adv = sc.item_advice()
        d = sc.debug["item"]
        if d["mode"] != "top":
            continue
        top = sc.shown[0]["cand"].comp
        picked = Counter(p["item"] for p in d["picked"] if p["kind"] == "top")
        still = Counter(d["need"]["carry"]) - picked
        reserved = {c for x in still for c in (stats.recipe(x) or ())}
        for s in adv.suggestions:
            if s.components and s.reason.startswith("보조: "):
                checked += 1
                assert not set(s.components) & reserved, (comps, s, reserved, top.comp_id)
    assert checked >= 1


def test_duplicate_carry_bis_counts_only_what_the_deck_still_needs(stats, weights, settings):
    """구인수 재료 두 벌(+ 프라이팬): 1위 덱이 필요한 개수(1)만 1위 덱 아이템으로 센다. 두 번째 곡궁은
    구인수 두 번째가 아니라 1위 덱의 다른 핵심 아이템(속사포 상징)으로 간다."""
    comps = ("RecurveBow", "NeedlesslyLargeRod", "RecurveBow", "NeedlesslyLargeRod", "FryingPan")
    sc = make_scorer(stats, weights, settings, _item_state("3-2", 6, comps))
    top = sc.shown[0]["cand"].comp
    need = top.carry_bis_items.count("DA_GuinsoosRageblade")
    adv = sc.item_advice()
    tops = [p for p in sc.debug["item"]["picked"] if p["kind"] == "top" and p["item"] == "DA_GuinsoosRageblade"]
    assert len(tops) == need == 1
    assert adv.suggestions[0].item_id == "DA_GuinsoosRageblade"
    assert [s.item_id for s in adv.suggestions].count("DA_GuinsoosRageblade") == 1
    assert "DA_18_EmblemRapidfire" in [p["item"] for p in sc.debug["item"]["picked"] if p["kind"] == "top"]


def test_many_components_fall_back_to_ordered_greedy(stats, weights, settings):
    """재료가 exact_max_components를 넘으면 탐욕(캐리 → 핵심 → 점수 순). 1위 덱 캐리 BIS 먼저는 그대로."""
    comps = _APH_COMPS + ("BFSword", "ChainVest", "GiantsBelt", "TearOfTheGoddess", "NegatronCloak", "Spatula",
                          "RecurveBow", "NeedlesslyLargeRod")
    sc = make_scorer(stats, _with_item(weights, exact_max_components=10), settings, _item_state("3-2", 6, comps))
    assert len(sc.view.components) > 10
    adv = sc.item_advice()
    d = sc.debug["item"]
    assert d["mode"] == "top" and d["picked"][0]["kind"] == "top"
    assert {r["item"]: r["role"] for r in d["rows"]}[adv.suggestions[0].item_id] == "carry"
    used = Counter(c for s in adv.suggestions for c in s.components)
    have = Counter(sc.view.components)
    assert all(used[c] <= have[c] for c in used)


def test_exact_allocation_matches_brute_force_on_small_sets(stats, weights, settings):
    """전체 탐색은 가능한 배분 중 (캐리 BIS 수, 핵심 수) 사전식 최댓값을 고른다(무차별 대입과 비교)."""
    import itertools as it

    seen_top = 0
    for comps in (_APH_COMPS, ("BFSword", "RecurveBow", "NeedlesslyLargeRod", "Spatula"),
                  ("ChainVest", "GiantsBelt", "RecurveBow", "NeedlesslyLargeRod", "FryingPan", "SparringGloves"),
                  ("BFSword", "ChainVest", "NegatronCloak", "NeedlesslyLargeRod", "TearOfTheGoddess", "Spatula")):
        sc = make_scorer(stats, weights, settings, _item_state("3-2", 6, comps))
        sc.item_advice()
        d = sc.debug["item"]
        if d["mode"] != "top":
            continue
        seen_top += 1
        need_c, need_k = Counter(d["need"]["carry"]), Counter(d["need"]["core"])
        roles = {r["item"]: r["role"] for r in d["rows"]}
        have = Counter(sc.view.components)
        best = (0, 0)
        for k in range(len(comps) // 2 + 1):
            for sel in it.combinations_with_replacement(list(sc.craftable), k):
                use = Counter(c for x in sel for c in sc.craftable[x])
                if any(use[c] > have[c] for c in use):
                    continue
                cn, kn, a, b = Counter(need_c), Counter(need_k), 0, 0
                for x in sel:
                    if roles[x] == "carry" and cn[x] > 0:
                        cn[x] -= 1
                        a += 1
                    elif roles[x] in ("carry", "core") and kn[x] > 0:
                        kn[x] -= 1
                        b += 1
                best = max(best, (a, b))
        got = [p["item"] for p in d["picked"] if p["kind"] == "top"]
        n_carry = sum(1 for x in got if roles[x] == "carry")
        assert (n_carry, len(got) - n_carry) == best, (comps, got, best)
    assert seen_top >= 2


def test_no_top_item_early_slams_for_tempo_on_a_board_unit(stats, weights, settings):
    sc = make_scorer(stats, weights, settings, _item_state("2-5", 5, ("ChainVest", "RecurveBow"), _ORNN_BOARD))
    adv = sc.item_advice()
    assert sc.debug["item"]["mode"] == "fallback" and adv.hold is False
    s0 = adv.suggestions[0]
    assert s0.holder_unit_id in {u["id"] for u in _ORNN_BOARD}
    assert "지금 전력용" in s0.reason and "지금 보드의" in s0.reason


def test_no_top_item_late_says_so_and_keeps_the_best_item(stats, weights, settings):
    sc = make_scorer(stats, weights, settings, _item_state("4-2", 7, ("BFSword", "NegatronCloak"), _ORNN_BOARD))
    adv = sc.item_advice()
    assert sc.debug["item"]["mode"] == "fallback"
    assert adv.suggestions[0].item_id == "DA_Bloodthirster"
    assert adv.suggestions[0].reason.startswith("1위 덱 아이템은 지금 만들 수 없습니다")
    assert "지금 전력용" not in adv.suggestions[0].reason


def test_hold_wording_does_not_tell_the_player_to_craft(stats, weights, settings):
    """보관(hold) 권장과 '지금 전력용으로 권해 드립니다'가 같이 나오지 않는다."""
    sc = make_scorer(stats, weights, settings, _item_state("2-5", 5, ("Spatula", "NegatronCloak"), _ORNN_BOARD))
    adv = sc.item_advice()
    assert adv.hold is True
    assert all("권해 드립니다" not in (s.reason or "") for s in adv.suggestions)


def test_temp_holder_only_when_the_top_holder_is_not_owned(stats, weights, settings):
    comps = ("ChainVest", "GiantsBelt")
    sc = make_scorer(stats, weights, settings, _item_state("2-5", 5, comps, _ORNN_BOARD))
    s0 = sc.item_advice().suggestions[0]
    assert s0.item_id == "DA_SunfireCape" and s0.holder_unit_id == "DA_18_RekSai"
    assert "렉사이 확보 전까지 오른에게 임시로" in s0.reason
    owned = _ORNN_BOARD + [{"id": "DA_18_RekSai", "star": 1}]
    sc2 = make_scorer(stats, weights, settings, _item_state("2-5", 5, comps, owned))
    s1 = next(s for s in sc2.item_advice().suggestions if s.item_id == "DA_SunfireCape")
    if s1.holder_unit_id == "DA_18_RekSai":
        assert "임시로" not in s1.reason and s1.reason.endswith("렉사이에게")
    # 설정으로 끌 수 있다
    sc3 = make_scorer(stats, _with_item(weights, temp_holder=False), settings, _item_state("2-5", 5, comps, _ORNN_BOARD))
    assert "임시로" not in sc3.item_advice().suggestions[0].reason


def test_item_pick_question_prefers_the_best_fitting_comps_carry():
    from tft_advisor.advisor.questions import QUESTIONS_VERSION, i1

    assert QUESTIONS_VERSION == "q4"
    for hp in (True, False):
        for board in (True, False):
            assert "main carry of the candidate comp that best fits" in i1(hp, board)
