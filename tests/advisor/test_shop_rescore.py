"""상점만 다시 채점(`Advisor.rescore_shop`, `_workspace/21_board_trust.md` §7) + 상점이 캐시 키에 들어가는지.

전투 중 새로고침·라운드 시작 새 상점에서 새 카드 점수를 수 ms 안에, Jev를 새로 부르지 않고 채운다.
"""
from __future__ import annotations

import time

import pytest

from tft_advisor.advisor import MockJevBackend
from tft_advisor.contracts import FieldSource, GameState, ScreenMode

VISION = {"board": FieldSource.VISION, "bench": FieldSource.VISION}


def shop(*ids: str) -> list[dict]:
    return [{"kind": "champion", "id": i} for i in ids] + [{"kind": "empty"}] * (5 - len(ids))


def gs(mode: str = "planning", shop_ids=("DA_18_Sejuani", "DA_18_Yorick"), gold: int = 30) -> GameState:
    return GameState.model_validate({
        "screen_mode": mode, "stage": "3-2", "level": 5, "gold": gold, "hp": 70, "shop": shop(*shop_ids),
        "board": [{"id": "DA_18_Zyra", "star": 1}, {"id": "DA_18_Alistar", "star": 1}],
        "bench": [{"id": "DA_18_Ashe", "star": 1}], "field_source": VISION})


def test_shop_is_part_of_the_cache_key(make_advisor):
    be = MockJevBackend()
    adv = make_advisor(backend=be)
    a = adv.advise(gs(shop_ids=("DA_18_Sejuani", "DA_18_Yorick")))
    b = adv.advise(gs(shop_ids=("DA_18_Kennen", "DA_18_Shen")))
    assert a.state_hash != b.state_hash
    assert be.calls == 2                                           # 상점만 바뀌어도 캐시를 재사용하지 않는다
    assert [s.offer_id for s in b.shop[:2]] == ["DA_18_Kennen", "DA_18_Shen"]
    c = adv.advise(gs(shop_ids=("DA_18_Sejuani", "DA_18_Yorick")))
    assert c.state_hash == a.state_hash and be.calls == 2          # 같은 상태는 캐시


def test_rescore_shop_in_combat_keeps_everything_else(make_advisor):
    be = MockJevBackend()
    adv = make_advisor(backend=be)
    first = adv.advise(gs())
    calls = be.calls
    combat = gs(mode="combat", shop_ids=("DA_18_Kennen", "DA_18_Shen", "DA_18_Sejuani"), gold=28)
    t0 = time.perf_counter()
    rec = adv.rescore_shop(combat)
    elapsed = (time.perf_counter() - t0) * 1000
    assert be.calls == calls                                       # 유료 Jev 호출 없음
    assert elapsed < 100, elapsed                                  # 목표: 수 ms(느린 CI 여유)
    assert [s.offer_id for s in rec.shop[:3]] == ["DA_18_Kennen", "DA_18_Shen", "DA_18_Sejuani"]
    assert all(s.reason for s in rec.shop[:3])
    assert rec.target_comps == first.target_comps and rec.board_plan == first.board_plan
    assert rec.item == first.item and rec.component_priority == first.component_priority
    assert rec.debug["shop_rescore"]["mode"] == "combat" and rec.debug["shop_rescore"]["jev_cached"] is False
    # 전투 화면의 recommend()는 직전 추천을 돌려주는데, 그것이 이제 새 상점이다
    assert adv.advise(gs(mode="combat")).shop == rec.shop


def test_same_card_same_slot_scores_like_the_stats_path(make_advisor):
    """캐시가 없으면 통계 채점이다: Jev를 끈 전체 추천과 같은 상점 점수가 나와야 한다(목표 덱이 같을 때)."""
    off = make_advisor(backend="off")
    base = off.advise(gs())
    again = off.rescore_shop(gs(mode="combat"))
    assert [(s.offer_id, s.buy) for s in again.shop] == [(s.offer_id, s.buy) for s in base.shop]
    # 직전 덱 점수는 debug에 소수 4자리로 남으므로 그만큼의 차이만 허용한다
    assert [s.score for s in again.shop] == pytest.approx([s.score for s in base.shop], abs=1e-3)


def test_rescore_uses_cached_jev_answers_when_state_matches(make_advisor):
    be = MockJevBackend()
    adv = make_advisor(backend=be)
    adv.advise(gs())
    rec = adv.rescore_shop(gs())                                    # 같은 상태 → 캐시 적중
    assert rec.debug["shop_rescore"]["jev_cached"] is True and be.calls == 1


def test_rescore_without_previous_or_unreadable_shop(make_advisor):
    adv = make_advisor()
    assert adv.rescore_shop(gs(mode="combat")) is None              # 직전 추천이 없다
    first = adv.advise(gs())
    blurry = gs(mode="combat").model_copy(update={"confidence": {"shop": 0.2}})
    assert adv.rescore_shop(blurry) is first                         # 상점을 못 읽었으면 그대로
    assert gs(mode="combat").screen_mode is ScreenMode.COMBAT
