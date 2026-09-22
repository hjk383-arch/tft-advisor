"""QA 04 (qa-validator) advisor 회귀: 실제 통계 저장소 연동, 결측×폴백 매트릭스, 모드 계약, 알려진 설계 한계.

네트워크 없음(mock/off). 실제 저장소가 없으면(StatsNotFound) 해당 테스트만 skip.
"""
from __future__ import annotations

import copy

import pytest

from tft_advisor.advisor import Advisor, MockJevBackend
from tft_advisor.contracts import FallbackReason, GameState, Recommendation, ScreenMode

from .conftest import fixture_names, load_fixture


def _steps(name: str) -> list[dict]:
    fx = load_fixture(name)
    return fx.get("steps") or [{"state": fx["state"]}]


def _inv(rec: Recommendation) -> None:
    Recommendation.model_validate(rec.model_dump())
    assert rec.jev_used == (rec.fallback_reason is None)
    assert 1 <= len(rec.target_comps) <= 3
    assert all(0 <= t.score <= 1 for t in rec.target_comps)


@pytest.fixture(scope="module")
def repo():
    from tft_advisor.stats.repository import StatsNotFound, open_repository

    try:
        return open_repository()
    except StatsNotFound as e:   # pragma: no cover - 통계 미수집 환경
        pytest.skip(f"실제 통계 없음: {e}")


@pytest.mark.parametrize("backend", ["mock", "off"])
def test_real_repository_all_fixtures(repo, settings, weights, backend):
    """실제 open_repository()로 모든 fixture 스텝이 예외 없이 계약을 만족하고, 제작 불가 BIS는 craftable이 아니다."""
    for name in fixture_names():
        adv = Advisor(stats=repo, settings=settings, weights=weights, backend=backend)
        for step in _steps(name):
            rec = adv.advise(GameState.model_validate(step["state"]))
            assert rec is not None, name
            _inv(rec)
            for t in rec.target_comps:
                for r in t.items_ready:
                    assert not (r.status == "craftable" and not repo.is_craftable(r.item_id)), (name, r)
            assert all(repo.is_component(c) for c in rec.component_priority), name
        adv.close()


def test_real_repository_ap_vs_ad_items_differ(repo, settings, weights):
    """설계 §9 핵심 규칙이 mini가 아닌 실제 통계에서도 성립."""
    tops = []
    for name in ("s01_board_ap_items", "s02_board_ad_items"):
        adv = Advisor(stats=repo, settings=settings, weights=weights, backend="mock")
        tops.append(adv.advise(GameState.model_validate(load_fixture(name)["state"])).target_comps[0])
    assert tops[0].comp_id != tops[1].comp_id and tops[0].carry != tops[1].carry


def _variants() -> dict[str, dict]:
    b = load_fixture("s01_board_ap_items")["state"]
    out = {"base": b}
    for f in ("hp", "board", "bench", "items", "shop", "level", "stage", "gold"):
        v = copy.deepcopy(b)
        v[f] = None
        out[f"{f}=None"] = v
    v = copy.deepcopy(b)
    for f in ("hp", "board", "bench", "items", "shop", "level", "stage", "gold", "xp", "streak", "shop_odds",
              "active_traits", "augments_owned"):
        v[f] = None
    out["all_None"] = v
    v = copy.deepcopy(load_fixture("s05_augment_trait")["state"])
    v["hp"] = v["board"] = v["bench"] = v["items"] = None
    out["augment_mvp"] = v
    return out


@pytest.mark.parametrize("variant", sorted(_variants()))
@pytest.mark.parametrize("reason", [None, *FallbackReason], ids=lambda r: r.value if r else "jev")
def test_missing_fields_x_fallback_reason(stats, settings, weights, variant, reason):
    st = GameState.model_validate(_variants()[variant])
    if reason is FallbackReason.JEV_DISABLED:
        adv = Advisor(stats=stats, settings=settings, weights=weights, backend="off")
    elif reason is FallbackReason.CIRCUIT_OPEN:
        adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
        adv.gateway._open_until = adv.gateway.clock() + 999
    else:
        adv = Advisor(stats=stats, settings=settings, weights=weights,
                      backend=MockJevBackend(fail=reason) if reason else MockJevBackend())
    rec = adv.advise(st)
    _inv(rec)
    assert rec.fallback_reason == reason


def test_modes_without_previous(make_advisor):
    adv = make_advisor()
    st = GameState.model_validate(load_fixture("s01_board_ap_items")["state"])
    for m in (ScreenMode.COMBAT, ScreenMode.ITEM_SELECT, ScreenMode.UNKNOWN, ScreenMode.LOADING, ScreenMode.GAME_OVER):
        assert adv.advise(st.model_copy(update={"screen_mode": m})) is None, m
    car = adv.advise(st.model_copy(update={"screen_mode": ScreenMode.CAROUSEL}))
    assert car is not None and car.shop == [] and car.augment is None


def test_loading_resets_cache_and_session(make_advisor):
    adv = make_advisor()
    st = GameState.model_validate(load_fixture("s01_board_ap_items")["state"])
    adv.advise(st)
    assert adv.gateway._cache
    assert adv.advise(st.model_copy(update={"screen_mode": ScreenMode.LOADING})) is None
    assert not adv.gateway._cache and adv.session.last is None and adv.session.prev_sig is None


def test_s09_single_bis_item_already_switches(make_advisor):
    """설계 §9 #9 문구(완성템 1개 추가)로도 B 덱 전환이 일어난다(fixture는 2개를 넣었다)."""
    fx = load_fixture("s09_hysteresis")
    adv = make_advisor()
    adv.advise(GameState.model_validate(fx["steps"][0]["state"]))
    raw = copy.deepcopy(fx["steps"][0]["state"])
    raw["items"]["completed"].append({"id": "DA_StrikersFlail"})
    rec = adv.advise(GameState.model_validate(raw))
    assert rec.debug["sig_unchanged"] is False
    assert rec.target_comps[0].comp_id == "invoker-ahri"


def test_hysteresis_protects_previous_top1(make_advisor, weights):
    fx = load_fixture("s09_hysteresis")
    raw = fx["steps"][0]["state"]
    adv = make_advisor()
    r1 = adv.advise(GameState.model_validate(raw))
    assert len(r1.target_comps) >= 2
    top, second = r1.target_comps[0].comp_id, r1.target_comps[1].comp_id
    rows = {c["comp_id"]: (i, c) for i, c in enumerate(r1.debug["candidates"])}
    k2, c2 = rows[second]
    gap = rows[top][1]["score"] - c2["score"]
    # 2위가 1위를 hysteresis_bonus의 절반만큼 추월하도록 2위의 item_fit 답만 올린다(시그니처 불변)
    delta = gap + weights.comp.hysteresis_bonus / 2
    adv.gateway.backend.overrides = {
        f"comp_item_fit_{k2}": min(1.0, c2["terms"]["item"]["norm"] + delta / (weights.comp.wi * (1 - weights.comp.wt)))}
    r2 = adv.advise(GameState.model_validate({**raw, "gold": (raw.get("gold") or 0) + 1}))
    assert r2.debug["sig_unchanged"] is True
    assert r2.target_comps[0].comp_id == top
