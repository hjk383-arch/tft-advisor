"""10 app-integrator: 전투 중 직전 추천 표시, 보유 증강 '늘기만 한다' 병합, 새 판 확인·세션 보관, 설정 키, latest_json."""
from __future__ import annotations

import json
import os

import pytest

from tft_advisor.app.loop import InlineAdviceRunner, LiveLoop, LoopUpdate
from tft_advisor.app.report import KeptInfo, format_report, kept_view
from tft_advisor.app.session import SessionTracker, augments_allowed_at
from tft_advisor.contracts import AugmentRef, FieldSource, GameState, ScreenMode

from .conftest import FakeClock, FakeDetector, FakeRecognizer, FakeSource, planning_state, sample_recommendation, shop_slots


class FakeAdvisor:
    def __init__(self, rec=None) -> None:
        self.rec = rec or sample_recommendation()
        self.seen: list[GameState] = []
        self.resets = 0

    def advise(self, state):
        self.seen.append(state)
        return self.rec

    def reset(self):
        self.resets += 1

    def close(self):
        pass


def make_loop(settings, states, script, *, tracker=None, clock=None, updates=None, advisor=None):
    clock = clock or FakeClock()
    advisor = advisor or FakeAdvisor()
    rec = FakeRecognizer(states)
    loop = LiveLoop(source=FakeSource(), recognizer=rec, settings=settings,
                    tracker=tracker if tracker is not None else SessionTracker(),
                    detector=FakeDetector(script), clock=clock, sleep=clock.sleep,
                    on_update=(updates.append if updates is not None else None))
    loop.advisor = advisor
    loop.runner = InlineAdviceRunner(advisor, loop._on_advice)
    return loop, rec, advisor, clock


def aug_state(ids, stage="3-2", **kw):
    return planning_state(stage=stage, augments_owned=[AugmentRef(id=i, confidence=0.9) for i in ids],
                          field_source={"augments_owned": FieldSource.VISION}, **kw)


# --------------------------------------------------------------------------- 1. 전투 중 직전 추천


def test_kept_view_drops_bought_and_changed_slots_but_keeps_comps():
    rec = sample_recommendation()   # 0번 칸 Xayah [구매], 1번 칸 Yorick [보류]
    state = GameState(screen_mode=ScreenMode.COMBAT, shop=shop_slots([None, "DA_18_Zyra", "C", "D", "E"]))
    shown, kept = kept_view(rec, state)
    assert shown.shop == [] and kept == KeptInfo(ScreenMode.COMBAT, bought=1, changed=1)
    assert shown.target_comps == rec.target_comps          # 목표 덱은 흔들지 않는다
    assert len(rec.shop) == 2                              # 원본은 그대로
    same = GameState(screen_mode=ScreenMode.COMBAT, shop=shop_slots(["DA_18_Xayah", "DA_18_Yorick", "C", "D", "E"]))
    assert kept_view(rec, same)[0].shop == rec.shop
    unknown = GameState(screen_mode=ScreenMode.COMBAT)
    assert kept_view(rec, unknown)[0].shop == rec.shop       # 지금 상점을 모르면 거르지 않는다


def test_combat_frame_hides_bought_unit_and_labels_kept(settings):
    shop0 = shop_slots(["DA_18_Xayah", "DA_18_Yorick", "C", "D", "E"])
    shop1 = shop_slots([None, "DA_18_Yorick", "C", "D", "E"])
    states = [planning_state(shop=shop0), GameState(screen_mode=ScreenMode.COMBAT, stage="2-3", shop=shop1)]
    updates: list[LoopUpdate] = []
    loop, _, advisor, _ = make_loop(settings, states, [{"stage"}, {"shop"}], updates=updates)
    loop.step()
    loop.step()
    assert len(advisor.seen) == 1                      # 전투 중 재계산 없음(기존 계약)
    u = updates[-1]
    assert u.kind == "kept" and u.kept is not None and u.kept.bought == 1
    assert [a.offer_id for a in u.recommendation.shop] == ["DA_18_Yorick"]
    assert len(loop.last_recommendation.shop) == 2     # 원본 추천은 그대로(다음 준비 단계 비교용)
    text = format_report(u.state, u.recommendation, kept=u.kept)
    assert "직전 추천(전투 중)" in text and "산 칸 1개 제외" in text


def test_overlay_shows_kept_label(qapp, settings):
    from tft_advisor.app.overlay import OverlayWindow

    w = OverlayWindow(settings)
    rec, kept = kept_view(sample_recommendation(), GameState(screen_mode=ScreenMode.COMBAT,
                                                             shop=shop_slots([None, "DA_18_Yorick", "C", "D", "E"])))
    w.set_data(GameState(screen_mode=ScreenMode.COMBAT), rec, kept=kept)
    assert "직전 추천(전투 중)" in w.body.text()
    w.set_data(planning_state(), sample_recommendation())
    assert "직전 추천" not in w.body.text()
    w.close()


# --------------------------------------------------------------------------- 2. 보유 증강 병합


def test_augments_allowed_at():
    assert augments_allowed_at("1-4") == 0
    assert augments_allowed_at("2-1") == 1 and augments_allowed_at("3-1") == 1
    assert augments_allowed_at("3-2") == 2 and augments_allowed_at("4-2") == 3 and augments_allowed_at("6-1") == 3
    assert augments_allowed_at(None) is None


def test_vision_extension_is_accepted_after_augment_round():
    t = SessionTracker()
    t.observe(aug_state(["A1"], stage="2-2"), {"owned"})
    m = t.observe(aug_state(["A1", "A2"], stage="3-2"), {"owned"})
    assert [a.id for a in m.augments_owned] == ["A1", "A2"] and t.data.augments_source == "vision"


@pytest.mark.parametrize("ids,stage", [
    (["X1", "X2"], "3-3"),          # 기존 칸이 다르다(상대 보드)
    (["A1", "A2"], "3-1"),          # 3-2 전에는 늘 수 없다
    (["A1", "A2"], None),           # 스테이지 모름 → 늘리지 않는다
])
def test_vision_conflicts_are_rejected(ids, stage):
    t = SessionTracker()
    t.observe(aug_state(["A1"], stage="2-2"), {"owned"})
    t.data.stage = None
    st = aug_state(ids, stage="3-3").model_copy(update={"stage": stage})
    m = t.observe(st, {"owned"})
    assert [a.id for a in m.augments_owned] == ["A1"]
    assert t.data.augments_owned == ["A1"] and t.augments_rejected == 1


def test_fewer_slots_rejected():
    t = SessionTracker()
    t.observe(aug_state(["A1", "A2"], stage="3-2"), {"owned"})
    m = t.observe(aug_state(["A1"], stage="3-3"), {"owned"})
    assert [a.id for a in m.augments_owned] == ["A1", "A2"]


def test_manual_prefix_is_extended_not_replaced():
    t = SessionTracker()
    t.set_augments_owned(["M1"])
    m = t.observe(aug_state(["M1", "V2"], stage="3-2"), {"owned"})
    assert [a.id for a in m.augments_owned] == ["M1", "V2"]
    assert t.data.augments_source == "tracked" and m.field_source["augments_owned"] == FieldSource.TRACKED
    same = t.observe(aug_state(["M1"], stage="3-3"), {"owned"})   # 짧은 판독은 버린다
    assert [a.id for a in same.augments_owned] == ["M1", "V2"]


def test_same_picture_keeps_session_name():
    class Learner:
        def same_picture(self, a, b):
            return {a, b} == {"Beast", "BeastPlus"}

    t = SessionTracker()
    t.learner = Learner()
    t.set_augments_owned(["BeastPlus"], source="tracked")
    m = t.observe(aug_state(["Beast"], stage="3-3"), {"owned"})
    assert [a.id for a in m.augments_owned] == ["BeastPlus"] and t.augments_rejected == 0


def test_rejected_vision_value_never_reaches_advisor_when_session_empty():
    t = SessionTracker()
    m = t.observe(aug_state(["A1", "A2", "A3"], stage="2-5"), {"owned"})   # 2-5에 3개는 불가능
    assert m.augments_owned is None and "augments_owned" not in m.field_source


def test_learn_owned_ignores_row_that_contradicts_session():
    class Row:
        ids = ["X1", None]
        cells = [None, None]

    t = SessionTracker()
    t.set_augments_owned(["A1"])
    t.data.stage = "3-2"
    t.data.offer_pool, t.data.offer_base, t.data.owned_count = ["P1", "P2", "P3"], 1, 1
    t._learn_owned(Row())
    assert t.data.owned_count == 1 and t.data.offer_pool == ["P1", "P2", "P3"] and not t.data.learned


# --------------------------------------------------------------------------- 3. 새 판 확인·보관


def weak_over():
    return GameState(screen_mode=ScreenMode.GAME_OVER, confidence={"screen_mode": 0.8})


def test_weak_game_over_once_does_not_reset(settings, tmp_path):
    tracker = SessionTracker(tmp_path / "session.json")
    tracker.set_augments_owned(["M1"])
    updates: list[LoopUpdate] = []
    loop, _, advisor, _ = make_loop(settings, [planning_state(), weak_over(), planning_state()],
                                    [{"stage"}, {"stage"}, {"stage"}], tracker=tracker, updates=updates)
    loop.step()
    loop.step()
    assert updates[-1].kind == "kept" and "새 판 확인 중" in updates[-1].message
    assert advisor.resets == 0 and tracker.data.augments_owned == ["M1"]
    loop.step()   # 메뉴를 닫아 준비 화면으로 → 확인 대기 취소
    assert loop._pending_reset is None and tracker.data.augments_owned == ["M1"]


def test_weak_game_over_confirmed_by_recheck_without_screen_change(settings, tmp_path):
    tracker = SessionTracker(tmp_path / "session.json")
    tracker.set_augments_owned(["M1"])
    updates: list[LoopUpdate] = []
    loop, rec, advisor, clock = make_loop(settings, [planning_state(), weak_over()], [{"stage"}, {"stage"}],
                                          tracker=tracker, updates=updates)
    loop.step()
    loop.step()                       # 1회 관측
    assert loop.step() is None        # 화면 변화 없음 + 재확인 주기 전 → 아무것도 안 함
    clock.t += settings.app.reset_recheck_s
    loop.step()                       # 2회(정지 화면 재판별)
    assert rec.calls[-1]["groups"] == {"stage"} and advisor.resets == 0
    clock.t += settings.app.reset_recheck_s
    loop.step()                       # 3회 → 새 판
    assert advisor.resets == 1 and updates[-1].kind == "reset"
    assert not tracker.data.augments_owned
    archived = list((tmp_path / "sessions").glob("session_*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text(encoding="utf-8"))["augments_owned"] == ["M1"]


def test_strong_game_over_resets_immediately(settings):
    strong = GameState(screen_mode=ScreenMode.GAME_OVER, confidence={"screen_mode": 0.95})
    loop, _, advisor, _ = make_loop(settings, [planning_state(), strong], [{"stage"}, {"stage"}])
    loop.step()
    loop.step()
    assert advisor.resets == 1


def test_stage_regression_needs_confirmation(settings):
    tracker = SessionTracker()
    tracker.data.stage = "4-2"
    states = [planning_state(stage="4-2"), planning_state(stage="1-2"), planning_state(stage="4-2"),
              planning_state(stage="1-3"), planning_state(stage="1-3"), planning_state(stage="1-3")]
    loop, _, advisor, _ = make_loop(settings, states, [{"stage"}] * 6, tracker=tracker)
    loop.step()
    loop.step()                  # 1-2 한 번(오독일 수 있다) → 대기, 스테이지 병합 안 함
    assert advisor.resets == 0 and tracker.data.stage == "4-2"
    loop.step()                  # 4-2로 돌아옴 → 취소
    assert loop._pending_reset is None
    for _ in range(3):
        loop.step()
    assert advisor.resets == 1


def test_archive_keeps_only_n(tmp_path):
    t = SessionTracker(tmp_path / "session.json", archive_keep=2)
    for i in range(4):
        t.set_augments_owned([f"A{i}"])
        t.reset("테스트")
        assert t.last_archive is not None
    kept = sorted((tmp_path / "sessions").glob("session_*.json"))
    assert len(kept) == 2
    assert [json.loads(p.read_text(encoding="utf-8"))["augments_owned"] for p in kept] == [["A2"], ["A3"]]
    assert SessionTracker(tmp_path / "x.json", archive_keep=0).archive() is None
    empty = SessionTracker(tmp_path / "session.json")
    assert empty.archive() is None     # 쌓인 것 없는 세션은 보관하지 않는다


# --------------------------------------------------------------------------- 4. 가중치 설정 키


def test_late_cfg_reads_weights_with_same_defaults():
    from tft_advisor.advisor.candidates import late_cfg
    from tft_advisor.config import Weights, load_weights

    expected = (4, 0.30, 2.0, 0.25)   # 09 J1 candidates.py 상수와 같은 값(동작 변화 없음)
    for w in (Weights(), load_weights()):
        c = late_cfg(w)
        assert (c.undecided_until_stage, c.w_tempo, c.tempo_span, c.pf_tempo) == expected
    w = Weights.model_validate({"comp": {"undecided_until_stage": 5, "w_tempo": 0.2, "tempo_span": 3},
                                "prefilter": {"w_tempo": 0.1}})
    c = late_cfg(w)
    assert (c.undecided_until_stage, c.w_tempo, c.tempo_span, c.pf_tempo) == (5, 0.2, 3.0, 0.1)


def test_weights_toml_has_late_keys():
    import tomllib

    from tft_advisor.config import DEFAULT_CONFIG_DIR

    raw = tomllib.loads((DEFAULT_CONFIG_DIR / "weights.toml").read_text(encoding="utf-8"))
    assert {"undecided_until_stage", "w_tempo", "tempo_span"} <= set(raw["comp"])
    assert "w_tempo" in raw["prefilter"]


# --------------------------------------------------------------------------- 5. latest_json


def test_latest_json_uses_patch_number_not_mtime(tmp_path):
    from tft_advisor.stats.repository import latest_json

    names = ["metatft_18.10.json", "metatft_18.9.json", "metatft_18.2b.json"]
    for i, n in enumerate(names):
        p = tmp_path / n
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (1000 + i * 100, 1000 + i * 100))   # 18.10이 가장 오래된 파일(checkout 뒤처럼)
    assert latest_json(tmp_path).name == "metatft_18.10.json"
    (tmp_path / "metatft_18.10.json").unlink()
    assert latest_json(tmp_path).name == "metatft_18.9.json"
    assert latest_json(tmp_path / "none") is None


def test_patch_sort_key_is_shared():
    from tft_advisor.advisor import stats_source
    from tft_advisor.patch_version import patch_sort_key

    assert stats_source.patch_sort_key is patch_sort_key
    assert patch_sort_key("18.3") > patch_sort_key("18.2b") > patch_sort_key("18.2")
