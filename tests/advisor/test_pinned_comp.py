"""사용자 고정 덱(`Advisor.set_pinned_comp`, `_workspace/21_board_trust.md` §14.3).

고정 → 스테이지가 바뀌어도 1위 / 해제 → 점수 순서 / reset이면 풀림 / 캐시 키 / rescore_shop / 모르는 덱 무시.
"""
from __future__ import annotations

import json
import threading

from tft_advisor.advisor import MockJevBackend
from tft_advisor.contracts import FieldSource, GameState

from .conftest import STATES

VISION = {"board": FieldSource.VISION, "bench": FieldSource.VISION}
ZYRA = "juggernaut-zyra-amumu"
KAYLE = "lunar-aphelios-kayle"      # s03에서 1차 필터 후보에 들지 않는 덱


def fixture_state(name: str) -> GameState:
    raw = json.loads((STATES / f"{name}.json").read_text(encoding="utf-8"))
    return GameState.model_validate(raw.get("state", raw))


def shop(*ids: str) -> list[dict]:
    return [{"kind": "champion", "id": i} for i in ids] + [{"kind": "empty"}] * (5 - len(ids))


def gs(mode="planning", stage="3-2", shop_ids=("DA_18_Sejuani", "DA_18_Yorick", "DA_18_Veigar")) -> GameState:
    return GameState.model_validate({
        "screen_mode": mode, "stage": stage, "level": 5, "gold": 30, "hp": 70, "shop": shop(*shop_ids),
        "board": [{"id": "DA_18_Zyra", "star": 1}, {"id": "DA_18_Alistar", "star": 1}],
        "bench": [{"id": "DA_18_Ashe", "star": 1}], "field_source": VISION})


def ids(rec):
    return [t.comp_id for t in rec.target_comps]


def test_pin_fixes_first_comp_across_stages(make_advisor):
    adv = make_advisor()
    base = adv.advise(fixture_state("s03_2-1_shop_early"))
    assert ids(base)[0] != ZYRA and base.pinned_comp_id is None
    adv.set_pinned_comp(ZYRA)
    assert adv.pinned_comp_id == ZYRA
    for name in ("s03_2-1_shop_early", "s04_4-1_shop_late"):
        rec = adv.advise(fixture_state(name))
        assert ids(rec)[0] == ZYRA and rec.pinned_comp_id == ZYRA
        assert rec.target_comps[0].reasons[0] == "사용자 고정 덱"
        assert 1 <= len(rec.target_comps) <= 3
        assert all(t.reasons[0] == "대안 덱(고정 덱 아래)" for t in rec.target_comps[1:])
        assert rec.debug["pinned"] == ZYRA
        assert "comp_pick" not in rec.debug["question_ids"]          # 덱 선택은 묻지 않는다
        assert rec.debug["jev_state"]["user_pinned_comp"]["comp"]
        if rec.board_plan is not None:
            assert rec.board_plan.comp_id == ZYRA


def test_unpin_returns_to_scored_order(make_advisor):
    ref = make_advisor().advise(fixture_state("s03_2-1_shop_early"))
    adv = make_advisor()
    adv.set_pinned_comp(ZYRA)
    adv.advise(fixture_state("s03_2-1_shop_early"))
    adv.set_pinned_comp(None)
    rec = adv.advise(fixture_state("s03_2-1_shop_early"))
    assert rec.pinned_comp_id is None and ids(rec)[0] == ids(ref)[0]
    assert "comp_pick" in rec.debug["question_ids"]


def test_pin_outside_candidate_pool_is_loaded_from_stats(make_advisor):
    adv = make_advisor()
    base = adv.advise(fixture_state("s03_2-1_shop_early"))
    assert KAYLE not in [c["comp_id"] for c in base.debug["candidates"]]
    adv.set_pinned_comp(KAYLE)
    rec = adv.advise(fixture_state("s03_2-1_shop_early"))
    assert ids(rec)[0] == KAYLE and rec.pinned_comp_id == KAYLE


def test_reset_clears_pin(make_advisor):
    adv = make_advisor()
    adv.set_pinned_comp(ZYRA)
    adv.reset()
    assert adv.pinned_comp_id is None
    assert adv.advise(fixture_state("s03_2-1_shop_early")).pinned_comp_id is None


def test_unknown_pin_is_ignored(make_advisor, caplog):
    ref = make_advisor().advise(fixture_state("s03_2-1_shop_early"))
    adv = make_advisor()
    adv.set_pinned_comp("no-such-comp")
    with caplog.at_level("WARNING"):
        rec = adv.advise(fixture_state("s03_2-1_shop_early"))
    assert rec.pinned_comp_id is None and ids(rec) == ids(ref)
    assert "no-such-comp" in caplog.text


def test_pin_change_invalidates_cache(make_advisor):
    be = MockJevBackend()
    adv = make_advisor(backend=be)
    a = adv.advise(gs())
    adv.set_pinned_comp(ZYRA)
    b = adv.advise(gs())
    assert a.state_hash != b.state_hash and be.calls == 2
    adv.set_pinned_comp(None)
    c = adv.advise(gs())
    assert c.state_hash == a.state_hash and be.calls == 2            # 고정 해제 = 예전 상태 → 캐시


def test_rescore_shop_honors_pin(make_advisor):
    adv = make_advisor()
    free = adv.advise(gs())
    free_scores = {s.offer_id: s.score for s in adv.rescore_shop(gs(mode="combat"), free).shop if s.offer_id}
    adv.set_pinned_comp(ZYRA)
    pinned = adv.advise(gs())
    assert ids(pinned)[0] == ZYRA
    rec = adv.rescore_shop(gs(mode="combat"), pinned)
    scores = {s.offer_id: s.score for s in rec.shop if s.offer_id}
    # 자이라 덱 유닛(세주아니·요릭)은 고정 뒤 점수가 오르고, 다른 덱 유닛(베이가)은 내려간다
    assert scores["DA_18_Sejuani"] > free_scores["DA_18_Sejuani"]
    assert scores["DA_18_Yorick"] > free_scores["DA_18_Yorick"]
    assert scores["DA_18_Veigar"] < free_scores["DA_18_Veigar"]


def test_pin_during_combat_refreshes_without_jev(make_advisor):
    be = MockJevBackend()
    adv = make_advisor(backend=be)
    first = adv.advise(gs())
    calls = be.calls
    adv.set_pinned_comp(ZYRA)
    rec = adv.advise(gs(mode="combat"))
    assert be.calls == calls and ids(rec)[0] == ZYRA and rec.pinned_comp_id == ZYRA
    assert rec.shop == first.shop


def test_set_pinned_comp_is_thread_safe(make_advisor):
    adv = make_advisor()
    ts = [threading.Thread(target=adv.set_pinned_comp, args=(ZYRA if i % 2 else None,)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert adv.pinned_comp_id in (ZYRA, None)
