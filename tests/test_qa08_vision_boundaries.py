"""QA08 (2026-09-22): vision 07 라운드(1080p · 듀얼 모니터 · 화면 상태 · 보유 증강) 경계면 회귀.

- `MssSource(monitor="auto")` 선택/재확인 로직을 가짜 mss로 고정한다(실제 모니터 없이).
- 세로 모니터가 섞인 가상 데스크톱 캡처에서 인식이 죽지 않는다(QA08에서 ValueError 재현 → 수정).
- 전투/특수 선택/게임 종료 프레임 순서가 앱 병합·추천 유지 규칙과 맞는다.
- 보유 증강 템플릿 → ID가 static·stats와 같은 체계다(템플릿 폴더가 없으면 skip — gitignore 대상).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from tft_advisor.app.session import GROUP_READ_MODES, KEEP_MODES, RESET_MODES, SessionTracker
from tft_advisor.contracts import AugmentRef, FieldSource, GameState, ScreenMode, ShopSlot, ShopSlotKind

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- MssSource(monitor="auto")
class _FakeSct:
    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = frames
        self.monitors = [{"left": 0, "top": 0, "width": 1, "height": 1, "i": 0}] + [
            {"left": 0, "top": 0, "width": f.shape[1], "height": f.shape[0], "i": i + 1} for i, f in enumerate(frames)]
        self.grabs = 0

    def grab(self, box):
        self.grabs += 1
        img = self.frames[box["i"] - 1] if box["i"] else self.frames[0]
        return np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])

    def close(self):
        pass


def _fake_mss(monkeypatch, frames):
    sct = _FakeSct(frames)
    mod = types.ModuleType("mss")
    mod.mss = lambda: sct
    monkeypatch.setitem(sys.modules, "mss", mod)
    return sct


def _mon(v: int) -> np.ndarray:
    return np.full((4, 4, 3), v, np.uint8)


def _scorer(scores: dict[int, float]):
    return lambda img: scores[int(img[0, 0, 0])]


def test_auto_monitor_picks_game_and_sticks_on_ties_and_game_over(monkeypatch):
    from tft_advisor.vision import capture

    frames = [_mon(1), _mon(2)]
    _fake_mss(monkeypatch, frames)
    scores = {1: 0.1, 2: 1.0}
    clock = [0.0]
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    src = capture.MssSource(monitor="auto", scorer=_scorer(scores))
    assert src._sct is None                              # mss는 첫 grab() 스레드에서 만든다
    f = src.grab()
    assert src.monitor == 2 and int(f.image[0, 0, 0]) == 2
    # 게임 종료 화면: 게임 모니터 점수가 떨어져도 탐색기로 옮기지 않는다
    scores.update({1: 0.3, 2: 0.2})
    clock[0] += 31
    src.grab()
    assert src.monitor == 2
    # 다른 모니터에도 TFT(방송 등)가 떠 같은 1.0 → 지금 모니터 유지(QA08 수정)
    scores.update({1: 1.0, 2: 1.0})
    clock[0] += 31
    src.grab()
    assert src.monitor == 2
    # 게임 창을 1번 모니터로 옮김 → 옮긴다
    scores.update({1: 1.0, 2: 0.1})
    clock[0] += 31
    src.grab()
    assert src.monitor == 1
    # 재확인 주기 전에는 채점하지 않는다(프레임마다 OCR 금지)
    calls = []
    src._scorer = lambda img: calls.append(1) or 0.0
    clock[0] += 5
    src.grab()
    assert not calls


def test_auto_monitor_single_monitor_skips_scoring(monkeypatch):
    from tft_advisor.vision import capture

    _fake_mss(monkeypatch, [_mon(7)])
    src = capture.MssSource(monitor="auto", scorer=lambda img: pytest.fail("모니터 1대면 채점하지 않는다"))
    assert int(src.grab().image[0, 0, 0]) == 7 and src.monitor == 1


def test_mss_region_with_auto_monitor_and_bad_number(monkeypatch):
    from tft_advisor.vision import capture

    _fake_mss(monkeypatch, [_mon(1)])
    capture.MssSource(monitor="auto", region=(0, 0, 4, 4))   # QA08: 예전엔 int("auto") ValueError
    src = capture.MssSource(monitor=5)
    with pytest.raises(ValueError):
        src.grab()


def test_capture_monitor_setting_accepts_auto_and_int():
    from pydantic import ValidationError

    from tft_advisor.config import CaptureCfg, load_settings

    assert load_settings().capture.monitor == "auto"
    assert CaptureCfg(monitor=2).monitor == 2
    with pytest.raises(ValidationError):
        CaptureCfg(monitor="primary")
    with pytest.raises(ValidationError):
        CaptureCfg(monitor=-1)


# ---------------------------------------------------------------- 세로 모니터가 섞인 캡처
def test_portrait_monitor_in_virtual_desktop_does_not_crash():
    """QA08 재현: 1080x1920 세로 모니터 + 1920x1080 게임 → profile_for(1080, 1920) ValueError로 recognize()가 죽었다."""
    pytest.importorskip("cv2")
    from tft_advisor.static_data import load_static
    from tft_advisor.vision.recognizer import Recognizer

    class NoOcr:
        name = "none"

        def read(self, image):
            return []

        def read_line(self, image):
            return None

        def read_lines(self, images):
            return [None for _ in images]

    frame = np.zeros((1920, 1080 + 1920, 3), np.uint8)
    frame[:, :1080] = 230
    frame[:1080, 1080:] = 90
    rec = Recognizer(static=load_static(), ocr=NoOcr(), item_template_dir=Path("/nonexistent"))
    box = rec.content_for(frame, None)
    assert box is not None and box[2] / box[3] > 1.3
    rec.recognize(frame)                                   # 예외 없이 끝난다


# ---------------------------------------------------------------- 화면 모드 순서 ↔ 앱 병합
def _shop(ids):
    return [ShopSlot(kind=ShopSlotKind.CHAMPION, id=i, cost=1) if i else ShopSlot(kind=ShopSlotKind.EMPTY)
            for i in ids]


def test_combat_frame_updates_shop_but_owned_augments_survive():
    t = SessionTracker()
    t.set_augments_owned(["DA_ClutteredMind"])
    t.observe(GameState(screen_mode=ScreenMode.PLANNING, stage="2-5", gold=25,
                        shop=_shop(["A", "B", "C", "D", "E"])), {"hud", "shop", "owned"})
    # 전투: HUD·상점은 읽고(1칸 구매), 보유 증강 줄은 읽지 않는다 → 수동 입력 증강 유지
    m = t.observe(GameState(screen_mode=ScreenMode.COMBAT, stage="2-5", gold=21,
                            shop=_shop(["A", None, "C", "D", "E"])), {"hud", "shop", "owned"})
    assert m.gold == 21 and m.shop[1].kind == ShopSlotKind.EMPTY
    assert [a.id for a in m.augments_owned] == ["DA_ClutteredMind"]
    assert t.data.purchases["B"] == 1
    assert ScreenMode.COMBAT in KEEP_MODES and ScreenMode.COMBAT in GROUP_READ_MODES["shop"]


def test_item_select_and_carousel_keep_hud_but_update_items():
    t = SessionTracker()
    t.observe(GameState(screen_mode=ScreenMode.PLANNING, stage="5-1", gold=40, level=8,
                        shop=_shop(["A", "B", "C", "D", "E"])), {"hud", "shop"})
    m = t.observe(GameState(screen_mode=ScreenMode.ITEM_SELECT, stage="5-1"), {"hud", "shop", "items"})
    assert m.gold == 40 and m.level == 8 and m.shop is not None    # 모루 화면에선 HUD를 못 읽음 → 유지
    assert ScreenMode.ITEM_SELECT in KEEP_MODES
    assert ScreenMode.GAME_OVER in RESET_MODES and ScreenMode.LOADING in RESET_MODES


def test_owned_augments_manual_wins_over_conflicting_vision():
    """계약 변경(10 app, A2 반영): 수동값과 어긋나는 vision 값은 버린다(상대 보드 관전 중 상대 증강 줄 오독 방지).
    이전 계약(vision이 수동값을 덮음)은 08 QA 보고서 #15 WARN이었다."""
    t = SessionTracker()
    t.set_augments_owned(["DA_Ascension"])
    m = t.observe(GameState(screen_mode=ScreenMode.PLANNING, stage="3-1",
                            augments_owned=[AugmentRef(id="DA_ClutteredMind", confidence=0.9)],
                            field_source={"augments_owned": FieldSource.VISION}), {"owned"})
    assert [a.id for a in m.augments_owned] == ["DA_Ascension"]


# ---------------------------------------------------------------- 증강 ID 체계
def test_augment_icon_ids_are_static_ids_and_prefer_set_native():
    from tft_advisor.static_data import load_static
    from tft_advisor.vision.recognizer import Recognizer, augment_template_dirs

    static = load_static()
    tdir = augment_template_dirs(static.set_number)[0]
    if not tdir.is_dir() or not any(tdir.glob("*.png")):
        pytest.skip("증강 템플릿 없음(fetch-augments, gitignore)")
    rec = Recognizer.__new__(Recognizer)
    rec.static = static
    from tft_advisor.vision.recognizer import _augment_icon_owners

    rec._icon_owners = _augment_icon_owners(static)
    resolved = {}
    for p in tdir.glob("*.png"):
        r = Recognizer._resolve_augment_icon(rec, p.stem)
        if r is not None:
            assert static.get("augments", r["apiName"]) is not None
            resolved[p.stem] = r["apiName"]
    assert resolved.get("DA_ClutteredMind") == "DA_ClutteredMind"
    assert resolved.get("DA_Ascension") == "DA_Ascension"
    assert len(resolved) >= 250


def test_black_patch_splitting_one_screen_does_not_crash():
    """QA08 재현: 한 화면 위쪽에 순수 검정 조각이 닿으면 find_screens가 좁은 조각 둘로 나눴고, 둘 다 프로파일 유도가 안 돼
    recognize()가 ValueError로 죽었다. 이제 쓸 수 있는 후보가 없으면 한 화면으로 본다."""
    pytest.importorskip("cv2")
    from tft_advisor.static_data import load_static
    from tft_advisor.vision.recognizer import Recognizer
    from tft_advisor.vision.regions import find_screens

    class NoOcr:
        name = "none"

        def read(self, image):
            return []

        def read_line(self, image):
            return None

    frame = np.full((1120, 1993, 3), 90, np.uint8)
    frame[:, 0] = 0                                   # 방송 크롭처럼 가장자리에 순수 검정 열(느린 길)
    frame[0:40, 770:915] = 0                          # 스테이지 칸이 순수 검정
    assert len(find_screens(frame)) >= 2              # 전제: 쪼개진다
    rec = Recognizer(static=load_static(), ocr=NoOcr(), item_template_dir=Path("/nonexistent"))
    box = rec.content_for(frame, None)
    assert box is None or box[2] / box[3] > 1.3
    rec.recognize(frame)


def test_combat_needs_known_pvp_stage():
    """QA08: 스테이지를 못 읽으면 PvE(크립 체력바)인지 모른다 → 전투로 확신하지 않는다."""
    from tft_advisor.vision.screen_mode import ModeSignals, classify

    hud = dict(shop_hud=True, shop_hud_by_ocr=True, augment_title=False, augment_names_matched=0, dark=False)
    assert classify(ModeSignals(**hud, stage=None, enemy_bars=4)) == (ScreenMode.PLANNING, 0.7)
    assert classify(ModeSignals(**hud, stage="2-2", enemy_bars=4))[0] == ScreenMode.COMBAT
