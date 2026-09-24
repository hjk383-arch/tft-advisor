"""app(실시간 루프·오버레이) 테스트 공용 픽스처.

- Qt는 **반드시 offscreen**으로 돈다(CI·헤드리스). QApplication은 프로세스당 1개다.
- 무거운 `Recognizer`(OCR 모델 로드)는 세션 픽스처로 한 번만 만든다.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # PySide6 import 전에 정해야 한다

from tft_advisor.config import load_settings          # noqa: E402
from tft_advisor.contracts import (                   # noqa: E402
    GameState, ItemReadiness, Recommendation, ReasonTag, ScreenMode, ShopAdvice, ShopSlot, ShopSlotKind, TargetComp,
)
from tft_advisor.vision.capture import Frame          # noqa: E402

TESTS = Path(__file__).resolve().parents[1]
SCREENS = TESTS / "fixtures" / "screens"
RAW = SCREENS / "raw"


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def recognizer(settings):
    from tft_advisor.vision.recognizer import Recognizer

    # 테스트가 디스크 유닛 라이브러리(data/templates/{set}/units_screen/)에 쓰지 않도록 자동 학습을 끈다.
    return Recognizer(cfg=settings.vision.model_copy(update={"unit_autolearn": False}))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app


def frame(source: str = "fake", size: tuple[int, int] = (8, 8)) -> Frame:
    return Frame(image=np.zeros((size[1], size[0], 3), np.uint8), captured_at=datetime.now(UTC), source=source)


class FakeSource:
    """정해진 개수의 프레임을 주고 끝난다."""

    def __init__(self, count: int = 100) -> None:
        self.count = count
        self.grabbed = 0
        self.closed = False

    def grab(self) -> Frame | None:
        if self.grabbed >= self.count:
            return None
        self.grabbed += 1
        return frame(f"fake[{self.grabbed - 1}]")

    def close(self) -> None:
        self.closed = True


class FakeDetector:
    """미리 정한 '바뀐 묶음' 목록을 차례로 돌려준다."""

    def __init__(self, script: list[set[str]]) -> None:
        self.script = list(script)
        self.i = 0
        self.resets = 0

    def update(self, image, content=None) -> set[str]:
        if self.i >= len(self.script):
            return set()
        out = self.script[self.i]
        self.i += 1
        return set(out)

    def reset(self) -> None:
        self.resets += 1


class FakeRecognizer:
    """정해진 GameState를 차례로 돌려주고 호출 인자를 기록한다."""

    def __init__(self, states: list[GameState]) -> None:
        self.states = list(states)
        self.calls: list[dict] = []

    @property
    def profile(self):
        """실제 ChangeDetector를 붙여 보는 테스트용(16:9 기본 프로파일)."""
        from tft_advisor.vision.regions import profile_for_frame

        return profile_for_frame(1920, 1080)

    def profile_for(self, width, height):
        return self.profile

    def content_for(self, image, content):
        return None

    def recognize(self, image, *, content=None, source_image=None, captured_at=None, groups=None) -> GameState:
        self.calls.append({"groups": set(groups or ()), "source": source_image})
        i = min(len(self.calls) - 1, len(self.states) - 1)
        return self.states[i]


class FakeClock:
    """수동으로 돌리는 시계(초)."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def shop_slots(ids: list[str | None]) -> list[ShopSlot]:
    out = []
    for cid in ids:
        if cid is None:
            out.append(ShopSlot(kind=ShopSlotKind.EMPTY))
        else:
            out.append(ShopSlot(kind=ShopSlotKind.CHAMPION, id=cid, cost=1))
    return out


def sample_recommendation(names: tuple[str, ...] = ("가짜 덱 A", "가짜 덱 B")) -> Recommendation:
    """오버레이·리포트 테스트용. 점수는 **일부러 오름차순이 아니다**(재정렬 금지 검증)."""
    comps = [
        TargetComp(comp_id="c1", name=names[0], score=0.41, carry="DA_18_Khazix",
                   owned_units=["DA_18_Khazix"], missing_units=["DA_18_Zyra"],
                   items_ready=[ItemReadiness(item_id="DA_Bloodthirster", status="owned")],
                   reasons=["직전 추천 유지"]),
        TargetComp(comp_id="c2", name=names[1], score=0.77, carry="DA_18_Zyra"),
    ]
    return Recommendation(
        jev_used=True, target_comps=comps,
        shop=[ShopAdvice(slot=0, kind=ShopSlotKind.CHAMPION, offer_id="DA_18_Xayah", buy=True, score=0.8,
                         reason_tag=ReasonTag.NOW_POWER),
              ShopAdvice(slot=1, kind=ShopSlotKind.CHAMPION, offer_id="DA_18_Yorick", buy=False, score=0.2)],
        component_priority=["DA_Component_BFSword"], latency_ms=12.0)


def planning_state(**kw) -> GameState:
    base = dict(screen_mode=ScreenMode.PLANNING, stage="2-3", level=4, gold=10, hp=90)
    base.update(kw)
    return GameState(**base)
