"""세션: 부분 인식 병합 규칙, 보유 증강·구매 추적, `_state/session.json` 영속."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from tft_advisor.app.session import (
    GROUP_READ_MODES, KEEP_MODES, RESET_MODES, SessionTracker, field_group, merge_state, session_path,
)
from tft_advisor.contracts import AugmentRef, FieldSource, GameState, ScreenMode

from .conftest import planning_state, shop_slots


def test_field_group_covers_every_group_rule():
    """`GROUP_READ_MODES`에 vision의 모든 묶음이 있어야 병합 규칙에 구멍이 없다."""
    assert set(field_group().values()) <= set(GROUP_READ_MODES)


def test_merge_keeps_unread_fields():
    prev = planning_state(gold=17, shop=shop_slots(["DA_18_Xayah"] * 5))
    new = GameState(screen_mode=ScreenMode.PLANNING, stage="2-4")   # hud/shop을 읽지 않았다
    merged = merge_state(prev, new, groups={"stage"})
    assert merged.gold == 17 and merged.shop is not None
    assert merged.stage == "2-4"


def test_merge_drops_stale_value_when_group_was_read():
    """상점 묶음을 읽었는데 값이 안 나오면 낡은 상점을 버린다(이미 산 유닛을 계속 권하지 않게)."""
    prev = planning_state(shop=shop_slots(["DA_18_Xayah"] * 5))
    new = GameState(screen_mode=ScreenMode.PLANNING, stage="2-3")
    merged = merge_state(prev, new, groups={"stage", "shop", "hud"})
    assert merged.shop is None


def test_merge_keeps_value_when_group_unreadable_on_this_screen():
    """전투·unknown 화면에서는 상점 묶음을 요청해도 읽히지 않는다 → 직전 값을 유지한다."""
    prev = planning_state(shop=shop_slots(["DA_18_Xayah"] * 5), gold=30)
    new = GameState(screen_mode=ScreenMode.UNKNOWN, stage="2-3")
    merged = merge_state(prev, new, groups={"stage", "shop", "hud", "items"})
    assert merged.shop is not None and merged.gold == 30
    assert merged.screen_mode == ScreenMode.UNKNOWN   # 화면 상태는 상속하지 않는다


def test_merge_drops_transient_augment_offer():
    prev = GameState(screen_mode=ScreenMode.AUGMENT_SELECT, stage="2-1",
                     augment_offer=[AugmentRef(id="DA_SpreadingRoots")])
    merged = merge_state(prev, planning_state(stage="2-2"), groups={"stage", "hud", "shop"})
    assert merged.augment_offer is None


def test_merge_carries_confidence_and_source():
    prev = planning_state(gold=17, confidence={"gold": 0.8}, field_source={"gold": FieldSource.VISION})
    new = GameState(screen_mode=ScreenMode.COMBAT, stage="2-4", confidence={"screen_mode": 0.9})
    merged = merge_state(prev, new, groups={"stage"})
    assert merged.confidence_of("gold") == 0.8
    assert merged.confidence["screen_mode"] == 0.9
    assert merged.field_source["gold"] == FieldSource.VISION


def test_mode_sets_match_advisor_contract():
    assert RESET_MODES == {ScreenMode.LOADING, ScreenMode.GAME_OVER}
    assert KEEP_MODES == {ScreenMode.COMBAT, ScreenMode.ITEM_SELECT, ScreenMode.UNKNOWN}


# --------------------------------------------------------------------------- 추적


def test_purchase_tracking_single_slot_emptied():
    t = SessionTracker()
    t.observe(planning_state(shop=shop_slots(["A", "B", "C", "D", "E"])), {"shop"})
    t.observe(planning_state(shop=shop_slots(["A", None, "C", "D", "E"])), {"shop"})
    assert t.data.purchases["B"] == 1


def test_refresh_is_not_counted_as_purchase():
    t = SessionTracker()
    t.observe(planning_state(shop=shop_slots(["A", "B", "C", "D", "E"])), {"shop"})
    t.observe(planning_state(shop=shop_slots(["F", "G", "H", "I", "J"])), {"shop"})
    assert not t.data.purchases


def test_augments_owned_not_guessed_from_offer():
    """증강 후보만 보고 사용자가 무엇을 골랐는지 추정하지 않는다(C3.1)."""
    t = SessionTracker()
    state = GameState(screen_mode=ScreenMode.AUGMENT_SELECT, stage="2-1",
                      augment_offer=[AugmentRef(id="DA_SpreadingRoots"), AugmentRef(id="DA_LateGameScaling")])
    merged = t.observe(state, {"augment"})
    assert merged.augments_owned is None
    assert t.data.last_offer == ["DA_SpreadingRoots", "DA_LateGameScaling"]
    assert not t.data.augments_owned


def test_manual_augments_are_applied_with_manual_source():
    t = SessionTracker()
    t.set_augments_owned(["DA_SpreadingRoots"])
    merged = t.observe(planning_state(), {"hud"})
    assert [a.id for a in merged.augments_owned] == ["DA_SpreadingRoots"]
    assert merged.field_source["augments_owned"] == FieldSource.MANUAL


def test_vision_augments_win_over_session():
    t = SessionTracker()
    t.set_augments_owned(["DA_LateGameScaling"])
    state = planning_state(augments_owned=[AugmentRef(id="DA_SpreadingRoots")],
                           field_source={"augments_owned": FieldSource.VISION})
    merged = t.observe(state, {"hud"})
    assert [a.id for a in merged.augments_owned] == ["DA_SpreadingRoots"]
    assert t.data.augments_owned == ["DA_SpreadingRoots"]


# --------------------------------------------------------------------------- 영속


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "session.json"
    t = SessionTracker(path)
    t.set_augments_owned(["DA_SpreadingRoots"])
    t.observe(planning_state(shop=shop_slots(["A", "B", "C", "D", "E"])), {"shop"})
    t.save()
    other = SessionTracker(path)
    assert other.load()
    assert other.data.augments_owned == ["DA_SpreadingRoots"]
    assert other.data.frames == 1


def test_stale_session_is_ignored(tmp_path):
    path = tmp_path / "session.json"
    raw = {"version": 1, "updated_at": (datetime.now(UTC) - timedelta(hours=5)).isoformat(),
           "augments_owned": ["DA_SpreadingRoots"]}
    path.write_text(json.dumps(raw), encoding="utf-8")
    t = SessionTracker(path)
    assert not t.load()
    assert not t.data.augments_owned


def test_broken_session_file_does_not_raise(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("{not json", encoding="utf-8")
    assert not SessionTracker(path).load()


def test_reset_clears_state(tmp_path):
    t = SessionTracker(tmp_path / "session.json")
    t.set_augments_owned(["DA_SpreadingRoots"])
    t.observe(planning_state(), {"hud"})
    t.reset("테스트")
    assert t.state is None and not t.data.augments_owned and t.data.frames == 0


def test_session_path_uses_state_dir(settings):
    p = session_path(settings.app.state_dir)
    assert p.name == "session.json" and p.parent.name == "_state"
