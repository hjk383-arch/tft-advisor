"""ROI 정의 — 기준 프레임에 대한 **비율 좌표**(0~1)와 실제 픽셀로의 사상.

좌표 출처와 검증 상태 (2026-09-22)
- 모든 값은 `tests/fixtures/screens/` 7장(방송 화면 캡처, 약 2000x1120, 16:9, 한국어 클라이언트, Set 18 HUD)에서
  OCR 박스 위치로 측정했다. 7장 사이 편차는 약 ±0.003(≈6px@1080p)이다.
- 방송 캡처가 게임 화면 전체를 비율 그대로 담았다는 **가정** 위에 있다. 원본 1920x1080 캡처로 검증되기 전까지는
  모두 PROVISIONAL이다(`_workspace/04_vision-engineer_capture_request.md`).
- Phase 1 layout.md의 TFT-OCR-BOT(2024) 좌표는 Set 18 HUD와 맞지 않아(상점·골드·레벨 위치 변경) 쓰지 않는다.

ROI에는 여유(margin)를 두고, 텍스트는 ROI 안에서 OCR 검출(det)로 다시 찾는다. 그래서 ±10px 정도의 어긋남은 흡수한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Rect:
    """비율 좌표 사각형 (x1, y1, x2, y2), 0~1."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if not (0 <= self.x1 < self.x2 <= 1 and 0 <= self.y1 < self.y2 <= 1):
            raise ValueError(f"잘못된 비율 좌표: {self}")

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    def shift(self, dx: float = 0.0, dy: float = 0.0) -> Rect:
        return Rect(self.x1 + dx, self.y1 + dy, self.x2 + dx, self.y2 + dy)


@dataclass(frozen=True)
class FrameMapper:
    """기준 비율 좌표 → 프레임 픽셀.

    `content`는 프레임 안에서 게임 화면이 차지하는 영역(left, top, width, height) 픽셀이다.
    기본은 프레임 전체. 레터박스·창 테두리·방송 크롭처럼 게임 화면이 프레임 일부일 때 지정한다
    (per-fixture scale/offset 보정도 이것으로 한다).
    """

    frame_w: int
    frame_h: int
    content: tuple[int, int, int, int] | None = None

    @classmethod
    def for_image(cls, image: np.ndarray, content: tuple[int, int, int, int] | None = None) -> FrameMapper:
        h, w = image.shape[:2]
        return cls(w, h, content)

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.content or (0, 0, self.frame_w, self.frame_h)

    def to_px(self, r: Rect) -> tuple[int, int, int, int]:
        """(x1, y1, x2, y2) 정수 픽셀, 프레임 안으로 클립."""
        left, top, w, h = self.box
        x1 = int(round(left + r.x1 * w))
        y1 = int(round(top + r.y1 * h))
        x2 = int(round(left + r.x2 * w))
        y2 = int(round(top + r.y2 * h))
        x1, x2 = max(0, x1), min(self.frame_w, x2)
        y1, y2 = max(0, y1), min(self.frame_h, y2)
        return x1, y1, x2, y2

    def to_rel(self, x: float, y: float) -> tuple[float, float]:
        """프레임 픽셀 → 기준 비율 좌표."""
        left, top, w, h = self.box
        return (x - left) / w, (y - top) / h

    def crop(self, image: np.ndarray, r: Rect) -> np.ndarray:
        x1, y1, x2, y2 = self.to_px(r)
        return image[y1:y2, x1:x2]


# ---------------------------------------------------------------------------
# Set 18 HUD, 16:9, 인게임 UI 크기 기본값(가정). PROVISIONAL — 원본 캡처로 검증 필요.
# ---------------------------------------------------------------------------

SHOP_SLOTS = 5
SHOP_CARD_X0 = 0.2865        # 0번 카드 왼쪽 끝 (측정: 이름 텍스트 시작 0.289)
SHOP_CARD_PITCH = 0.1052     # 카드 간격 (측정: 0.289 / 0.394 / 0.499 / 0.605 / 0.710)
SHOP_CARD_W = 0.0995

ITEM_SLOTS = 10
ITEM_SLOT_Y0 = 0.2395        # 왼쪽 세로 아이템 벤치 0번 칸 위쪽 (측정: 3-5 fixture)
ITEM_SLOT_PITCH = 0.0505
ITEM_SLOT_H = 0.040


@dataclass(frozen=True)
class Profile:
    """한 해상도 비율/HUD 크기 조합의 ROI 묶음."""

    name: str
    stage: Rect
    round_icons: Rect
    level: Rect
    xp: Rect
    shop_odds: Rect
    gold: Rect
    streak_icon: Rect
    streak_value: Rect
    xp_button: Rect
    refresh_button: Rect
    shop_cards: tuple[Rect, ...]
    shop_names: tuple[Rect, ...]
    shop_costs: tuple[Rect, ...]
    augment_title: Rect
    augment_names: tuple[Rect, ...]
    item_slots: tuple[Rect, ...]
    traits_panel: Rect
    player_list: Rect

    def all_rois(self) -> dict[str, Rect]:
        """디버그 오버레이용 평탄화."""
        out: dict[str, Rect] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Rect):
                out[k] = v
            elif isinstance(v, tuple):
                for i, r in enumerate(v):
                    out[f"{k}[{i}]"] = r
        return out


def _shop_card(i: int) -> Rect:
    x0 = SHOP_CARD_X0 + SHOP_CARD_PITCH * i
    return Rect(x0, 0.866, x0 + SHOP_CARD_W, 0.996)


def _shop_name(i: int) -> Rect:
    x0 = SHOP_CARD_X0 + SHOP_CARD_PITCH * i
    return Rect(x0 + 0.001, 0.957, x0 + 0.078, 0.995)


def _shop_cost(i: int) -> Rect:
    x0 = SHOP_CARD_X0 + SHOP_CARD_PITCH * i
    return Rect(x0 + 0.082, 0.957, x0 + SHOP_CARD_W, 0.995)


def _augment_name(cx: float) -> Rect:
    return Rect(cx - 0.080, 0.485, cx + 0.080, 0.528)


def _item_slot(j: int) -> Rect:
    y0 = ITEM_SLOT_Y0 + ITEM_SLOT_PITCH * j
    return Rect(0.004, y0, 0.027, y0 + ITEM_SLOT_H)


SET18_16X9 = Profile(
    name="set18_16x9",
    # 스테이지 텍스트: Stage 2+ 는 x≈0.396~0.419, Stage 1은 막대가 짧아 x≈0.431~0.446 → 둘 다 덮는다.
    stage=Rect(0.385, 0.000, 0.458, 0.034),
    round_icons=Rect(0.430, 0.000, 0.575, 0.034),
    level=Rect(0.172, 0.806, 0.236, 0.848),      # "4레벨"
    xp=Rect(0.238, 0.808, 0.278, 0.845),         # "2/10"
    shop_odds=Rect(0.284, 0.810, 0.446, 0.845),  # "55% 30% 15% 0% 0%"
    gold=Rect(0.512, 0.806, 0.562, 0.850),       # 동전 아이콘 + 숫자
    streak_icon=Rect(0.574, 0.806, 0.594, 0.842),
    streak_value=Rect(0.592, 0.804, 0.618, 0.844),
    xp_button=Rect(0.182, 0.858, 0.282, 0.922),     # "경험치 구매" — 상점 HUD 존재 앵커(클릭하지 않음)
    refresh_button=Rect(0.182, 0.928, 0.282, 0.994),  # "새로고침" — 앵커(클릭하지 않음)
    shop_cards=tuple(_shop_card(i) for i in range(SHOP_SLOTS)),
    shop_names=tuple(_shop_name(i) for i in range(SHOP_SLOTS)),
    shop_costs=tuple(_shop_cost(i) for i in range(SHOP_SLOTS)),
    augment_title=Rect(0.420, 0.180, 0.580, 0.240),   # "하나 선택"
    augment_names=tuple(_augment_name(cx) for cx in (0.287, 0.499, 0.712)),
    item_slots=tuple(_item_slot(j) for j in range(ITEM_SLOTS)),
    traits_panel=Rect(0.030, 0.230, 0.125, 0.735),
    player_list=Rect(0.895, 0.140, 0.972, 0.790),
)

PROFILES: dict[str, Profile] = {
    SET18_16X9.name: SET18_16X9,
    # settings.toml [vision].profile 기본값 "1920x1080" 은 16:9 프로파일의 별칭이다.
    "1920x1080": SET18_16X9,
    "2560x1440": SET18_16X9,
    "3840x2160": SET18_16X9,
}


def get_profile(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"ROI 프로파일 없음: {name!r} (사용 가능: {sorted(PROFILES)})") from None


def draw_rois(image: np.ndarray, profile: Profile, mapper: FrameMapper | None = None) -> np.ndarray:
    """ROI 박스를 그린 복사본(디버그 PNG 저장용)."""
    import cv2

    mapper = mapper or FrameMapper.for_image(image)
    out = image.copy()
    for name, r in profile.all_rois().items():
        x1, y1, x2, y2 = mapper.to_px(r)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 255), 1)
        cv2.putText(out, name, (x1, max(10, y1 - 2)), cv2.FONT_HERSHEY_PLAIN, 0.8, (0, 255, 255), 1)
    return out
