"""ROI 정의 — 기준 프레임에 대한 **비율 좌표**(0~1)와 실제 픽셀로의 사상.

좌표 출처와 검증 상태 (2026-09-22 갱신)
- 16:9 값(`SET18_16X9`)은 처음에 `tests/fixtures/screens/` 7장(방송 화면 캡처, 약 2000x1120)에서 OCR 박스 위치로
  측정했고, **사용자 원본 1920x1080 캡처 13장**(`tests/fixtures/screens/raw/`, 비공개, 듀얼 모니터 Win+PrtSc)으로
  다시 확인·보정했다(`_workspace/07_vision_1080p_modes.md`): 하단 HUD 세로 +0.003, 아이템 벤치 칸 실측,
  상점 가격 칸 폭, 특성 패널 폭. 새 ROI(보드 인원 워터마크, "준비" 배너, 하단 "하나 선택", 게임 종료, 보유 증강 줄,
  전투 체력바 탐색 영역)도 1080p 원본에서 측정했다.
- 16:10 값(`SET18_16X10`)은 사용자 실제 게임 캡처 6장(약 1275x797, `tests/fixtures/screens/raw/`, 비공개)에서
  측정·검증했다. `_workspace/05_vision_aspect_and_labels.md` 참고.
- Phase 1 layout.md의 TFT-OCR-BOT(2024) 좌표는 Set 18 HUD와 맞지 않아(상점·골드·레벨 위치 변경) 쓰지 않는다.

**화면 비율(aspect)에 따른 HUD 배치** — 16:10 실측으로 확인한 규칙
TFT HUD는 화면 **높이에 비례**해서 커지고, 요소마다 가로 기준점(anchor)이 다르다. 그래서 비율이 달라지면
가로 비율 좌표만 바뀌고 세로 비율 좌표는 그대로다.

| anchor | 요소 | 사상 (k = 기준비율 / 대상비율) |
|---|---|---|
| CENTER | 상단 스테이지 막대, 하단 HUD 전체(레벨·XP·확률·골드·연승·버튼·상점 5칸), 증강 카드 3장 | `x' = 0.5 + (x - 0.5)·k` |
| LEFT | 왼쪽 세로 아이템 벤치, 특성 패널 | `x' = x·k` |
| RIGHT | 오른쪽 플레이어 목록 | `x' = 1 - (1 - x)·k` |

즉 16:9(1.778) → 16:10(1.600)이면 k=1.111이고, HUD는 가로로 **더 넓은 비율**을 차지한다(중앙 기준으로 벌어진다).
`derive_profile()`이 이 규칙을 적용하고, 실측값이 있으면 `overrides`로 덮어쓴다.

ROI에는 여유(margin)를 두고, 텍스트는 ROI 안에서 OCR 검출(det)로 다시 찾는다. 그래서 ±10px 정도의 어긋남은 흡수한다.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import lru_cache

import numpy as np

log = logging.getLogger(__name__)


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
# 왼쪽 세로 아이템 벤치 (1080p 원본 실측: 칸 바깥 테두리 x 10~55px, 0번 칸 y 261px, 간격 54px, 높이 45px)
ITEM_SLOT_X1 = 0.0052
ITEM_SLOT_X2 = 0.0286
ITEM_SLOT_Y0 = 0.2417
ITEM_SLOT_PITCH = 0.0500
ITEM_SLOT_H = 0.0417

# ---------------------------------------------------------------------------
# 보드 육각칸 · 벤치 칸 (1080p 원본 실측, 2026-09-23 — `_workspace/16_board_vision.md`)
# ---------------------------------------------------------------------------
# 준비 단계 카메라는 고정이라 내 보드 28칸(4줄 x 7칸)과 벤치 9칸은 화면 좌표가 늘 같다.
# 실측은 "하나 선택"(모루) 캡처에서 보라색으로 빛나는 육각칸 9개를 재서 원근 격자에 맞춘 값이다
# (칸 중심 오차 <= 10px). 줄 번호 0 = 내 쪽 맨 앞(전선, 화면에서 가장 위), 3 = 벤치에 가장 가까운 줄.
BOARD_ROWS, BOARD_COLS = 4, 7
_BOARD_ROW_Y = (446.0, 516.0, 592.0, 671.0)        # 줄 중심 y (1080p px)
_BOARD_ROW_PITCH = (118.0, 121.4, 125.0, 128.0)    # 같은 줄 옆 칸 간격 (원근으로 앞줄이 넓다)
_BOARD_ROW_CX = (909.0, 906.0, 903.0, 900.0)       # 줄 기준점 x (칸 3 / 홀수 줄은 칸 2.5)
_BOARD_TILT = -0.02                                 # 오른쪽으로 갈수록 줄이 올라가는 기울기 (py/px)
_BOARD_HEX_HH = 34.0                                # 칸 세로 반높이
BENCH_SLOTS = 9
_BENCH_CX0, _BENCH_PITCH = 416.0, 121.0             # 벤치 칸 중심 x (실측: 416 ~ 1384)
_BENCH_BAR_Y = 705.0                                # 벤치 유닛 체력바가 그려지는 평균 y
_BENCH_HW, _BENCH_HH = 60.0, 38.0

BAR_RISE = 102.0 / 1080
"""체력바가 유닛이 선 칸 중심보다 위에 그려지는 높이(프레임 높이 비). 1080p 실측 평균 102px, 표준편차 약 16px
(챔피언 모델 키에 따라 69~133px). 그래서 **칸(열)은 확실하지만 줄(행)은 추정**이다(16 보고서 §4)."""

REF_W, REF_H = 1920.0, 1080.0
"""16:9 기준 프로파일을 잰 화면 크기. 아래 픽셀 실측값을 비율 좌표로 바꿀 때만 쓴다."""


def _px_rect(cx: float, cy: float, hw: float, hh: float) -> Rect:
    return Rect(max(0.0, (cx - hw) / REF_W), max(0.0, (cy - hh) / REF_H),
                min(1.0, (cx + hw) / REF_W), min(1.0, (cy + hh) / REF_H))


def board_hex_px(row: int, col: int) -> tuple[float, float]:
    """(줄, 칸) → 1080p 화면에서 그 육각칸 중심 (x, y) px."""
    pitch = _BOARD_ROW_PITCH[row]
    cx = _BOARD_ROW_CX[row] + pitch * (col - 3 + (0.5 if row % 2 else 0.0))
    return cx, _BOARD_ROW_Y[row] + _BOARD_TILT * (cx - _BOARD_ROW_CX[row])


def _board_hex(row: int, col: int) -> Rect:
    cx, cy = board_hex_px(row, col)
    return _px_rect(cx, cy, _BOARD_ROW_PITCH[row] / 2, _BOARD_HEX_HH)


def _bench_cell(i: int) -> Rect:
    """벤치 칸 i의 **체력바 기준 상자**(유닛이 선 자리가 아니라 바가 그려지는 높이)."""
    return _px_rect(_BENCH_CX0 + _BENCH_PITCH * i, _BENCH_BAR_Y, _BENCH_HW, _BENCH_HH)


HUD_DY_1080P = 0.003
"""하단 HUD를 방송 크롭 측정값에서 내린 양. 1080p 원본에서 글자 중심이 ROI 중심보다 2~3px 아래였다
(레벨 +0.002, XP +0.0013, 확률 +0.002, 골드 +0.003, 상점 이름 +0.0027)."""


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
    # --- 화면 상태 신호 / 보유 증강 (1080p 원본 실측, 2026-09-22) ---
    board_count: Rect        # 준비 단계 보드 가운데 반투명 "N/M"(보드 인원/최대) 워터마크 — 전투 중에는 사라진다
    prep_banner: Rect        # 라운드 시작 "준비" 배너
    select_title: Rect       # 하단 패널 "하나 선택" 제목(모루·특성 선택 등 특수 선택 화면, 증강 선택과 위치가 다르다)
    game_over_title: Rect    # 게임 종료 "최종 순위 / N위"
    exit_button: Rect        # 게임 종료 "나가기" 버튼
    augments_owned: Rect     # 보드 왼쪽 위 보유 증강 아이콘 줄의 **탐색 영역**(줄은 가운데 정렬로 1~N칸 늘어난다)
    combat_area: Rect        # 적 유닛 빨간 체력바 탐색 영역(보드 위쪽 절반 + 여유)
    # --- 보드/벤치 유닛 (1080p 원본 실측, 2026-09-23) ---
    board_area: Rect                 # 내 보드 위 **아군 초록 체력바** 탐색 영역
    bench_area: Rect                 # 벤치 9칸 위 아군 체력바 탐색 영역
    board_hexes: tuple[Rect, ...]    # 28칸(줄 0~3 x 칸 0~6, row-major). 줄 0 = 내 쪽 맨 앞
    bench_cells: tuple[Rect, ...]    # 9칸(왼쪽부터). 체력바 기준 상자다

    def all_rois(self) -> dict[str, Rect]:
        """디버그 오버레이용 평탄화."""
        out: dict[str, Rect] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Rect):
                out[k] = v
            elif isinstance(v, tuple):
                for i, r in enumerate(v):
                    if isinstance(r, Rect):
                        out[f"{k}[{i}]"] = r
        return out


def _shop_card(i: int) -> Rect:
    x0 = SHOP_CARD_X0 + SHOP_CARD_PITCH * i
    return Rect(x0, 0.866, x0 + SHOP_CARD_W, 0.996)


def _shop_name(i: int) -> Rect:
    x0 = SHOP_CARD_X0 + SHOP_CARD_PITCH * i
    return Rect(x0 + 0.001, 0.957, x0 + 0.078, 0.995)


def _shop_cost(i: int) -> Rect:
    # 오른쪽 끝을 카드 끝(+0.0995)에서 +0.1035로 넓혔다: 1080p 원본에서 가격 숫자 오른쪽 끝이 카드 끝과 거의 같았다
    # (칸 3 "3" 0.6917~0.7016, 이전 ROI 끝 0.7016). 카드 사이 틈(0.0057) 안이라 옆 칸을 침범하지 않는다.
    x0 = SHOP_CARD_X0 + SHOP_CARD_PITCH * i
    return Rect(x0 + 0.082, 0.957, x0 + 0.1035, 0.995)


def _augment_name(cx: float) -> Rect:
    return Rect(cx - 0.080, 0.485, cx + 0.080, 0.528)


def _item_slot(j: int) -> Rect:
    y0 = ITEM_SLOT_Y0 + ITEM_SLOT_PITCH * j
    return Rect(ITEM_SLOT_X1, y0, ITEM_SLOT_X2, y0 + ITEM_SLOT_H)


_SET18_16X9_BASE = Profile(
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
    traits_panel=Rect(0.030, 0.230, 0.132, 0.735),   # 1080p: 구간 사다리("3 > 4 > 5 > 7") 끝이 x≈0.128
    player_list=Rect(0.895, 0.140, 0.972, 0.790),
    # 아래 7개는 1080p 원본 실측(방송 크롭에는 없던 신호).
    board_count=Rect(0.440, 0.190, 0.610, 0.310),     # "3/3" 글자 x 0.466~0.589, y 0.21~0.29
    prep_banner=Rect(0.450, 0.120, 0.550, 0.190),     # "준비" x 0.484~0.516, y 0.137~0.168
    select_title=Rect(0.430, 0.745, 0.570, 0.795),    # "하나 선택" x 0.477~0.521, y 0.757~0.780 (모루 / 악의 여단)
    game_over_title=Rect(0.400, 0.100, 0.600, 0.200), # "최종 순위" y 0.12~0.14, "1위" y 0.15~0.18
    exit_button=Rect(0.420, 0.910, 0.580, 0.965),     # "나가기" y 0.93~0.95
    augments_owned=Rect(0.200, 0.190, 0.340, 0.265),  # 줄(칸 38px 정사각, 가운데 x≈0.266) y 0.211~0.245
    combat_area=Rect(0.200, 0.030, 0.800, 0.620),
    # 탐색 영역은 칸 격자 + 체력바 높이 오차(약 ±40px)를 모두 덮게 잡았다.
    board_area=Rect(0.210, 0.230, 0.780, 0.592),
    bench_area=Rect(0.170, 0.592, 0.790, 0.700),
    board_hexes=tuple(_board_hex(r, c) for r in range(BOARD_ROWS) for c in range(BOARD_COLS)),
    bench_cells=tuple(_bench_cell(i) for i in range(BENCH_SLOTS)),
)
"""하단 HUD 세로 좌표가 방송 크롭 측정값 그대로인 기준. `SET18_16X9` = 이것 + 하단 HUD `HUD_DY_1080P`(아래)."""

# ---------------------------------------------------------------------------
# 화면 비율 → ROI 프로파일
# ---------------------------------------------------------------------------

REF_ASPECT = 16 / 9
"""SET18_16X9를 측정한 기준 비율. 다른 비율은 여기서 유도한다."""


class Anchor(StrEnum):
    """ROI의 가로 기준점. 세로는 항상 프레임 높이 기준(비율 그대로)."""

    CENTER = "center"   # 화면 가로 중앙 기준 (상·하단 HUD, 증강 카드)
    LEFT = "left"       # 왼쪽 가장자리 기준 (아이템 벤치, 특성 패널)
    RIGHT = "right"     # 오른쪽 가장자리 기준 (플레이어 목록)


ANCHORS: dict[str, Anchor] = {
    "stage": Anchor.CENTER, "round_icons": Anchor.CENTER,
    "level": Anchor.CENTER, "xp": Anchor.CENTER, "shop_odds": Anchor.CENTER, "gold": Anchor.CENTER,
    "streak_icon": Anchor.CENTER, "streak_value": Anchor.CENTER,
    "xp_button": Anchor.CENTER, "refresh_button": Anchor.CENTER,
    "shop_cards": Anchor.CENTER, "shop_names": Anchor.CENTER, "shop_costs": Anchor.CENTER,
    "augment_title": Anchor.CENTER, "augment_names": Anchor.CENTER,
    "item_slots": Anchor.LEFT, "traits_panel": Anchor.LEFT,
    "player_list": Anchor.RIGHT,
    # 보드 위 요소(워터마크·증강 줄·체력바)는 3D 보드가 화면 높이에 맞춰 가운데 그려지므로 CENTER. 배너·선택 패널·종료 화면도 가운데.
    "board_count": Anchor.CENTER, "prep_banner": Anchor.CENTER, "select_title": Anchor.CENTER,
    "game_over_title": Anchor.CENTER, "exit_button": Anchor.CENTER, "augments_owned": Anchor.CENTER,
    "combat_area": Anchor.CENTER,
    # 보드 육각칸·벤치 칸도 3D 보드와 함께 화면 높이에 맞춰 가운데 그려진다 → CENTER.
    "board_area": Anchor.CENTER, "bench_area": Anchor.CENTER,
    "board_hexes": Anchor.CENTER, "bench_cells": Anchor.CENTER,
}

# 화면 아래쪽에 붙는 HUD 막대. 방송 크롭은 게임 화면보다 아래로 조금 길어 세로 좌표가 위로 치우쳐 있었다.
# 1080p 원본 캡처(2026-09-22)로 잰 차이 HUD_DY_1080P(=0.003)를 16:9 기준값 자체에 반영했다(= 예전 임시 보정의 정리).
BOTTOM_HUD: frozenset[str] = frozenset({
    "level", "xp", "shop_odds", "gold", "streak_icon", "streak_value",
    "xp_button", "refresh_button", "shop_cards", "shop_names", "shop_costs",
})


def _shift_fields(p: Profile, fields: Collection[str], dy: float, name: str | None = None) -> Profile:
    def one(r: Rect) -> Rect:
        return Rect(r.x1, min(1.0, r.y1 + dy), r.x2, min(1.0, r.y2 + dy))

    kw: dict[str, Rect | tuple[Rect, ...]] = {}
    for f in fields:
        v = getattr(p, f)
        kw[f] = tuple(one(r) for r in v) if isinstance(v, tuple) else one(v)
    return replace(p, name=name or p.name, **kw)


SET18_16X9 = _shift_fields(_SET18_16X9_BASE, BOTTOM_HUD, HUD_DY_1080P)
"""16:9 기준 프로파일(1080p 원본으로 확인). 다른 비율은 여기서 유도한다."""

BOTTOM_HUD_DY_16X10 = 0.003
"""16:10 실캡처 6장(Mac, 가장자리가 몇 px 잘린 1273~1279 x 795~802)에서 잰 하단 HUD 추가 보정.
예전에는 방송 크롭 기준으로 0.006이었고, 기준이 1080p 원본으로 0.003 내려갔으므로 나머지 0.003만 남는다 →
**`SET18_16X10`의 수치는 예전과 똑같다**(회귀 방지, 테스트로 고정). 예외는 상점 가격 칸 오른쪽 끝(16:9와 같은
규칙으로 카드 사이 틈 안까지 넓어짐)과 새 ROI 7개(16:9에서 유도, 16:10 미검증)다. 원인은 그 캡처들의 위쪽 크롭으로 보이며,
16:10 원본을 다시 받으면 0으로 확인할 수 있다. 실측이 없는 비율의 유도에는 쓰지 않는다(보정 0)."""

ASPECTS: dict[str, float] = {
    "4:3": 4 / 3,
    "16:10": 16 / 10,
    "16:9": 16 / 9,
    "21:9": 64 / 27,      # 2560x1080, 3440x1440
    "32:9": 32 / 9,       # 5120x1440
}
"""지원 비율 이름 → 가로/세로. `config.VisionCfg.aspect`의 Literal과 같아야 한다(test_vision이 검사)."""

ASPECT_MATCH_TOL = 0.035
"""측정 비율이 이 상대오차 안이면 그 이름으로 본다(예: 1273/795 = 1.6013 → "16:10", 0.08% 차이)."""

_RES_RE = re.compile(r"^\s*(\d{3,5})\s*[x×:/]\s*(\d{3,5})\s*$")
_RATIO_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*[:x×/]\s*(\d{1,3}(?:\.\d+)?)\s*$")


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


def _map_x(x: float, anchor: Anchor, k: float) -> float:
    if k == 1.0:
        return x            # 같은 비율이면 항등(부동소수 왕복 오차를 만들지 않는다)
    if anchor is Anchor.CENTER:
        return _clamp01(0.5 + (x - 0.5) * k)
    if anchor is Anchor.LEFT:
        return _clamp01(x * k)
    return _clamp01(1.0 - (1.0 - x) * k)


def derive_profile(ref: Profile, name: str, aspect: float, *, ref_aspect: float = REF_ASPECT,
                   dy: float = 0.0, dy_fields: Collection[str] | None = None,
                   overrides: Mapping[str, Rect | tuple[Rect, ...]] | None = None) -> Profile:
    """기준 프로파일 → 다른 화면 비율의 프로파일. 모듈 머리 주석의 anchor 규칙을 적용한다.

    `dy`는 `dy_fields`(기본: 모든 필드)의 세로 좌표를 내린다. `overrides`는 실측값으로 덮어쓴다.
    """
    if aspect <= 0 or ref_aspect <= 0:
        raise ValueError(f"화면 비율은 양수여야 합니다: aspect={aspect}, ref_aspect={ref_aspect}")
    k = ref_aspect / aspect
    shift = set(ANCHORS) if dy_fields is None else set(dy_fields)
    kw: dict[str, Rect | tuple[Rect, ...]] = {}
    for fname, value in ref.__dict__.items():
        if fname == "name":
            continue
        anchor = ANCHORS[fname]
        d = dy if fname in shift else 0.0

        def one(r: Rect, anchor: Anchor = anchor, d: float = d) -> Rect:
            return Rect(_map_x(r.x1, anchor, k), _clamp01(r.y1 + d), _map_x(r.x2, anchor, k), _clamp01(r.y2 + d))

        kw[fname] = tuple(one(r) for r in value) if isinstance(value, tuple) else one(value)
    kw.update(overrides or {})
    return Profile(name=name, **kw)   # type: ignore[arg-type]


# --- 16:10 (실측: 사용자 캡처 6장, 약 1275x797) -----------------------------------
# 유도값이 실측과 어긋난 두 곳만 덮어쓴다.
#  - item_slots: 벤치 사각형 실측 x = 4~9px, 폭 33~34px, y0 = 193px, 간격 40px (795~802px 높이 기준).
#    캡처마다 가장자리 크롭이 ±3px 달라 평균을 썼다. 아이콘 템플릿 매칭은 ±3px만 탐색하므로 유도값(약 4px 왼쪽)보다 낫다.
#  - player_list / traits_panel: 이 두 ROI는 OCR **검출기**가 작은 글자(플레이어 HP 9~20px, 특성 인원수)를 찾을지가
#    ROI 폭에 민감하다(크롭 폭 → 업스케일 배율 → 검출). 유도값은 기하학적으로 맞지만 검출률이 낮아, 6장에서
#    검출이 가장 잘 되는 폭으로 좁혔다. **이 두 값은 6장에 맞춘 튜닝이며 다른 캡처에서 달라질 수 있다.**
#    둘 다 실패해도 틀린 값이 아니라 None(안전 실패)이다.
_I10_X1, _I10_X2 = 0.0055, 0.0315
_I10_Y0, _I10_PITCH, _I10_H = 0.2428, 0.0503, 0.0415

SET18_16X10 = derive_profile(
    SET18_16X9, "set18_16x10", ASPECTS["16:10"], dy=BOTTOM_HUD_DY_16X10, dy_fields=BOTTOM_HUD,
    overrides={
        "item_slots": tuple(
            Rect(_I10_X1, _I10_Y0 + _I10_PITCH * j, _I10_X2, _I10_Y0 + _I10_PITCH * j + _I10_H)
            for j in range(ITEM_SLOTS)
        ),
        "player_list": Rect(0.910, 0.140, 0.975, 0.790),
        "traits_panel": Rect(0.036, 0.230, 0.128, 0.740),
    },
)

MEASURED_PROFILES: dict[str, Profile] = {"16:9": SET18_16X9, "16:10": SET18_16X10}
"""실제 캡처로 측정·검증한 비율. 나머지 비율은 `derive_profile`로 유도하고 경고를 남긴다."""

PROFILES: dict[str, Profile] = {
    SET18_16X9.name: SET18_16X9,
    SET18_16X10.name: SET18_16X10,
    # 해상도 별칭(옛 settings.toml `profile = "1920x1080"` 호환).
    "1920x1080": SET18_16X9,
    "2560x1440": SET18_16X9,
    "3840x2160": SET18_16X9,
    "1280x800": SET18_16X10,
    "1680x1050": SET18_16X10,
    "1920x1200": SET18_16X10,
    "2560x1600": SET18_16X10,
}


def parse_aspect(text: str) -> float | None:
    """`"16:10"`, `"16x10"`, `"1920x1200"`, `"1.6"` → 가로/세로. 해석 불가면 None.

    지원 비율 이름은 `ASPECTS`의 값을 그대로 쓴다("21:9" = 64/27 이지 21/9 가 아니다).
    """
    text = text.strip()
    if text in ASPECTS:
        return ASPECTS[text]
    m = _RES_RE.match(text)
    if m:
        return int(m.group(1)) / int(m.group(2))
    m = _RATIO_RE.match(text)
    if m:
        d = float(m.group(2))
        return float(m.group(1)) / d if d else None
    try:
        v = float(text)
    except ValueError:
        return None
    return v if v > 0 else None


def aspect_name(ratio: float) -> str | None:
    """측정 비율 → 가장 가까운 지원 비율 이름. `ASPECT_MATCH_TOL`을 넘으면 None."""
    if ratio <= 0:
        return None
    name, err = min(((n, abs(ratio - v) / v) for n, v in ASPECTS.items()), key=lambda t: t[1])
    return name if err <= ASPECT_MATCH_TOL else None


@lru_cache(maxsize=16)
def profile_for_aspect(ratio: float) -> Profile:
    """화면 비율 → ROI 프로파일. 실측 프로파일이 있으면 그것, 없으면 16:9에서 유도한다(경고)."""
    name = aspect_name(ratio)
    if name is not None and name in MEASURED_PROFILES:
        return MEASURED_PROFILES[name]
    label = name or f"{ratio:.4f}"
    log.warning("화면 비율 %s 는 실측 프로파일이 없습니다 → 16:9에서 유도합니다(미검증). "
                "[vision] aspect/profile 로 고정하거나 캡처를 제공하면 정확해집니다", label)
    return derive_profile(SET18_16X9, f"set18_{label.replace(':', 'x')}", ratio)


def get_profile(name: str) -> Profile:
    """프로파일 이름·해상도 별칭·비율 문자열("16:10", "1920x1200") → Profile."""
    if name in PROFILES:
        return PROFILES[name]
    ratio = parse_aspect(name)
    if ratio is not None:
        return profile_for_aspect(ratio)
    raise KeyError(f"ROI 프로파일 없음: {name!r} (사용 가능: {sorted(PROFILES)} 또는 \"16:9\"/\"16:10\" 같은 비율)")


def profile_for_frame(width: int, height: int, setting: str = "auto") -> Profile:
    """이 프레임에 쓸 ROI 프로파일.

    `setting`이 "auto"(기본)면 프레임 크기에서 비율을 재서 고른다. 그 밖의 값은 `get_profile`로 해석한다
    (프로파일 이름 / 해상도 별칭 / 비율 문자열) — 사용자가 설정으로 고정한 경우다.
    """
    if setting and setting != "auto":
        return get_profile(setting)
    if width <= 0 or height <= 0:
        raise ValueError(f"프레임 크기가 잘못됨: {width}x{height}")
    return profile_for_aspect(width / height)


# ---------------------------------------------------------------------------
# 게임 화면 영역(content box) 자동 탐지 — 레터박스/필러박스
# ---------------------------------------------------------------------------

CONTENT_DARK_MEAN = 12.0    # 띠로 볼 줄/열의 평균 밝기 상한
CONTENT_DARK_MAX = 44.0     # 같은 줄/열의 최댓값 상한(글자·아이콘이 있으면 넘는다)
CONTENT_MIN_TRIM = 8        # 이보다 적게 잘리면 띠가 아니라고 본다(테두리 1~2px 등)
CONTENT_MAX_TRIM = 0.25     # 한 방향에서 이 비율을 넘게 자르지 않는다(안전장치)


def detect_content_box(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """프레임 안 게임 화면 영역 (left, top, width, height) px. 레터박스/필러박스 **검은 띠만** 잘라낸다.

    전체화면·테두리 없는 창모드 캡처(띠 없음)에서는 None(= 프레임 전체)을 돌려준다.
    창모드에서 바탕화면이 함께 찍히는 경우는 검은 띠가 아니므로 탐지하지 않는다 →
    사용자가 `[vision] content_box`로 지정하거나 `MssSource(region=…)`로 창만 캡처한다.
    """
    if image.ndim == 3:
        gray = image.mean(axis=2)
    else:
        gray = image.astype(float)
    h, w = gray.shape
    if h < 32 or w < 32:
        return None

    def trim(profile_mean: np.ndarray, profile_max: np.ndarray, n: int) -> tuple[int, int]:
        dark = (profile_mean <= CONTENT_DARK_MEAN) & (profile_max <= CONTENT_DARK_MAX)
        lo = 0
        while lo < n and dark[lo]:
            lo += 1
        hi = n
        while hi > lo and dark[hi - 1]:
            hi -= 1
        limit = int(n * CONTENT_MAX_TRIM)
        if lo < CONTENT_MIN_TRIM or lo > limit:
            lo = 0
        if n - hi < CONTENT_MIN_TRIM or n - hi > limit:
            hi = n
        return lo, hi

    top, bottom = trim(gray.mean(axis=1), gray.max(axis=1), h)
    left, right = trim(gray.mean(axis=0), gray.max(axis=0), w)
    if (top, bottom, left, right) == (0, h, 0, w):
        return None
    return left, top, right - left, bottom - top


# ---------------------------------------------------------------------------
# 여러 모니터를 이어 붙인 캡처(Win+PrtSc 가상 데스크톱) → 모니터별 화면
# ---------------------------------------------------------------------------

SCREEN_MIN_PX = 320          # 이보다 작은 조각은 모니터로 보지 않는다
SCREEN_EDGE_TOL = 8          # 열마다 잰 위/아래 끝이 이만큼 안에서 같으면 같은 모니터
SCREEN_SMOOTH = 31           # 열 단위 위/아래 끝의 중앙값 필터 폭(화면 안의 순수 검정 열 잡음 제거)
MULTI_ASPECT_MIN = 2.5       # 한 조각인데 이보다 넓으면(예: 3840x1080 = 모니터 2대 나란히) 같은 폭으로 나눈 후보도 만든다


def _runs(values: np.ndarray, tol: int) -> list[tuple[int, int]]:
    """1차원 값이 tol 안에서 같은 구간들 [(start, end)]."""
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or abs(int(values[i]) - int(values[start])) > tol:
            out.append((start, i))
            start = i
    return out


def _edges_have_void(image: np.ndarray, min_frac: float = 0.15) -> bool:
    """네 가장자리 줄/열 중 하나라도 순수 검정(0,0,0)이 min_frac 이상이면 True. 모니터가 덮지 않는 빈 곳은 항상 가장자리에 닿는다."""
    edges = (image[0], image[-1], image[:, 0], image[:, -1])
    for e in edges:
        z = (e.max(axis=-1) == 0) if e.ndim == 2 else (e == 0)
        if float(z.mean()) >= min_frac:
            return True
    return False


def find_screens(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """가상 데스크톱 캡처 → 모니터 영역 [(left, top, width, height)] (왼쪽부터).

    Windows `Win+PrtSc`는 모든 모니터를 한 장에 이어 붙이고, **어떤 모니터도 덮지 않는 곳은 순수 검정 (0,0,0)** 으로
    채운다(사용자 캡처: 4480x1440 = 왼쪽 2560x1440 + 오른쪽 1920x1080, 오른쪽 아래 360px 순수 검정).
    열마다 순수 검정이 아닌 픽셀의 위/아래 끝을 재고, 끝이 같은 열끼리 묶어 모니터로 본다.
    높이가 같은 모니터가 나란히 붙으면(3840x1080 등) 경계를 알 수 없으므로 한 조각으로 돌려준다 → `screen_candidates`.
    모니터 하나(보통의 실시간 캡처)면 [(0, 0, W, H)].
    """
    h, w = image.shape[:2]
    if not _edges_have_void(image):
        return [(0, 0, w, h)]                       # 빠른 길: 가장자리에 순수 검정 띠가 없다(실시간 단일 모니터 프레임)
    nz = image.max(axis=2) > 0 if image.ndim == 3 else image > 0
    has = nz.any(axis=0)
    top = np.where(has, nz.argmax(axis=0), h)
    bottom = np.where(has, h - nz[::-1].argmax(axis=0), 0)
    if int(top.max()) == 0 and int(bottom.min()) == h:
        return [(0, 0, w, h)]                       # 모든 열이 위아래 끝까지 찼다 = 모니터 하나
    k = SCREEN_SMOOTH
    if w > k:   # 화면 안의 순수 검정 열(아이템 칸 안쪽 등)을 이웃 열 중앙값으로 누른다
        pad = k // 2
        tp = np.pad(top, pad, mode="edge")
        bp = np.pad(bottom, pad, mode="edge")
        win = np.lib.stride_tricks.sliding_window_view
        top = np.median(win(tp, k), axis=1).astype(int)
        bottom = np.median(win(bp, k), axis=1).astype(int)
    out: list[tuple[int, int, int, int]] = []
    for a, b in _runs(top, SCREEN_EDGE_TOL):
        for c, d in _runs(bottom[a:b], SCREEN_EDGE_TOL):
            x1, x2 = a + c, a + d
            t = int(np.median(top[x1:x2]))
            bt = int(np.median(bottom[x1:x2]))
            if x2 - x1 >= SCREEN_MIN_PX and bt - t >= SCREEN_MIN_PX:
                out.append((x1, t, x2 - x1, bt - t))
    return out or [(0, 0, w, h)]


def screen_candidates(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """게임 화면일 수 있는 영역 후보. `find_screens` 결과 + (한 조각이 지나치게 넓으면) 같은 폭 2·3등분.

    후보가 하나면 그것이 곧 게임 화면이다. 여럿이면 인식기가 스테이지 글자(OCR)로 고른다(`Recognizer.content_for`).
    """
    out: list[tuple[int, int, int, int]] = []
    for left, top, w, h in find_screens(image):
        out.append((left, top, w, h))
        if w / h >= MULTI_ASPECT_MIN:
            for n in (2, 3):
                part = w // n
                if aspect_name(part / h) in ("16:9", "16:10", "4:3", "21:9"):
                    out.extend((left + i * part, top, part, h) for i in range(n))
    return out


def stage_bar_score(image: np.ndarray, box: tuple[int, int, int, int], profile: Profile) -> float:
    """OCR 없이 본 "TFT 화면다움" 0~1: 위쪽 가운데 스테이지 막대가 어둡다(탐색기·브라우저 같은 밝은 창은 0에 가깝다).

    스테이지 글자를 못 읽는 화면(게임 종료·로딩)에서 후보를 고를 때만 쓰는 약한 신호다.
    """
    m = FrameMapper.for_image(image, box)
    r = profile.stage
    crop = m.crop(image, Rect(r.x1, 0.0, profile.round_icons.x2, 0.045))
    if crop.size == 0:
        return 0.0
    dark = float((crop.max(axis=2) < 70).mean())
    white = float((m.crop(image, Rect(0, 0, 1, 1))[::8, ::8].min(axis=2) > 225).mean())
    return max(0.0, min(1.0, dark - white))


def game_box_by_pixels(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """OCR 없이 고른 게임 화면 영역(도구용: `templates harvest-*`, `debug-rois`). 모니터가 하나면 레터박스만 본다.

    인식기(`Recognizer.content_for`)는 스테이지 글자 OCR로 더 확실하게 고른다. 여기서는 스테이지 막대 픽셀 점수만 쓴다.
    """
    cands = screen_candidates(image)
    if len(cands) <= 1:
        return detect_content_box(image)
    left, top, w, h = max(cands, key=lambda c: (stage_bar_score(image, c, profile_for_frame(c[2], c[3])), c[2] * c[3]))
    inner = detect_content_box(image[top:top + h, left:left + w])
    if inner is not None:
        return left + inner[0], top + inner[1], inner[2], inner[3]
    return left, top, w, h


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
