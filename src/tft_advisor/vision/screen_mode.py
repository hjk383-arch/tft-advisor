"""화면 상태 판별 — 픽셀/OCR 신호 → ScreenMode + 신뢰도.

신호(모두 화면 픽셀에서 얻는다). 규칙과 근거: `_workspace/07_vision_1080p_modes.md` §4 (1080p 원본 13장, 준비/전투 쌍 4개).
- shop_hud: 하단 "경험치 구매"/"새로고침" 버튼이 보인다(OCR, 없으면 픽셀 대안). **준비와 전투 모두** 보인다.
- board_count: 보드 가운데 반투명 "N/M"(보드 인원/최대) 워터마크. 준비 단계에만 있고 전투가 시작되면 사라진다
  (쌍 4개 모두 확인). 유닛·효과에 가려 못 읽을 수 있으므로 **있으면 준비 확정, 없으면 모름**인 한 방향 신호다.
- prep_banner: 라운드 시작 순간의 "준비" 배너. 한 방향 신호(있으면 준비).
- enemy_bars: 보드 위쪽의 **빨간 체력바** 개수(적 유닛). 전투 쌍 4개 모두 3~10개, 준비 화면 0개. 단 PvE 라운드는
  준비 단계에도 크립이 빨간 체력바를 달고 서 있다(방송 fixture 1-4) → 워터마크를 먼저 보고, PvE에서는 체력바를 쓰지 않는다.
- augment_title / augment_names_matched: 가운데 "하나 선택" 제목 + 증강 이름 3칸 퍼지 매칭 수.
- select_title: **하단 패널** "하나 선택"(모루 아이템 선택, 악의 여단 전리품 선택 등). 증강 선택과 위치가 다르다.
- game_over: "최종 순위" 제목 또는 "나가기" 버튼.
- stage: 상단 스테이지 문자열, dark: 프레임 전체가 어둡고 평탄(로딩 추정).

알려진 한계
- 전투가 끝나 적이 모두 죽은 순간(빨간 체력바 0, 워터마크 없음)은 PLANNING(신뢰도 0.7 = "준비 또는 전투")으로 낸다.
- 특성 전용 선택(악의 여단 등)은 계약에 따로 값이 없어 ITEM_SELECT로 낸다(가장 가까운 값, 보고서 제안 참고).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from ..contracts import ScreenMode

PLANNING_CONFIRMED = 0.9     # 워터마크/배너로 준비 확정
PLANNING_AMBIGUOUS = 0.7     # HUD만 보인다(준비 또는 전투) — 예전 값 그대로
PLANNING_PIXEL_ONLY = 0.55   # HUD를 픽셀로만 확인
COMBAT_PVP = 0.8
# PvE 라운드(1-x, x-7)는 준비 단계에도 크립이 빨간 체력바를 달고 서 있고 워터마크를 가린다(방송 fixture 1-4: "1/3"이
# 크립에 가려 읽히지 않음). 그래서 PvE에서는 체력바만으로 전투라고 하지 않는다(애매 → PLANNING 0.7, 안전한 쪽).
MIN_ENEMY_BARS = 2           # 상대 이름표 막대가 우연히 빨강일 수 있어 1개로는 전투로 보지 않는다


@dataclass(frozen=True)
class ModeSignals:
    shop_hud: bool
    shop_hud_by_ocr: bool
    augment_title: bool
    augment_names_matched: int
    stage: str | None
    dark: bool
    board_count: bool = False
    prep_banner: bool = False
    enemy_bars: int = 0
    select_title: bool = False
    game_over_title: bool = False
    exit_button: bool = False


def hud_panel_pixels(crop: np.ndarray) -> bool:
    """OCR 없이 버튼 패널 존재 추정: 준비/전투 HUD 버튼은 푸른 패널 + 글자 대비(std>35).
    fixture 관측: 상점 보임 std≈43~47, 증강 화면(어둡게 가려짐) ≈12, 캐러셀 ≈22."""
    if crop.size == 0:
        return False
    b, _, r = crop.reshape(-1, 3).mean(axis=0)
    return float(crop.std()) > 35.0 and b > r + 10


def frame_is_dark(image: np.ndarray) -> bool:
    small = image[::8, ::8]
    return float(small.mean()) < 25.0 and float(small.std()) < 20.0


_BOARD_COUNT_RE = re.compile(r"(\d{0,2})\s*[/／]\s*(\d{1,2})")


def parse_board_count(text: str) -> tuple[int | None, int] | None:
    """워터마크 "3/3", "9/9", 가려진 "/5" → (보드 인원 또는 None, 최대). 아니면 None."""
    m = _BOARD_COUNT_RE.search(text.replace(" ", ""))
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else None
    cap = int(m.group(2))
    if not 1 <= cap <= 20 or (n is not None and n > 20):
        return None
    return n, cap


def count_enemy_bars(crop: np.ndarray, frame_h: int) -> int:
    """보드 영역 크롭에서 적 유닛 **빨간 체력바** 개수.

    1080p 실측: 체력바 = 짙은 남색 테두리 안의 64x4px 빨강 그라데이션(H≈1, S≈235, V 190→60). 아군은 초록(H≈60),
    내 전략가 체력바는 주황(H≈15~20)이라 걸리지 않는다. 가로로 긴 얇은 빨강 덩어리만 센다(아이템 아이콘·배경 제외).
    """
    if crop.size == 0:
        return 0
    import cv2

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = (((h <= 6) | (h >= 174)) & (s >= 180) & (v >= 100)).astype(np.uint8)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((1, 5), np.uint8))   # 눈금으로 끊긴 막대를 잇는다
    n, _, stats, _ = cv2.connectedComponentsWithStats(red, 8)
    min_w = 0.022 * frame_h          # 1080p에서 약 24px(막대 64px, 체력이 줄어도 대개 남는다)
    max_h = max(2.0, 0.008 * frame_h)
    return sum(1 for k in range(1, n)
               if stats[k][2] >= min_w and 2 <= stats[k][3] <= max_h and stats[k][2] >= 5 * stats[k][3])


def _is_pve(stage: str | None) -> bool:
    if not stage:
        return False
    s, r = (int(x) for x in stage.split("-"))
    return s == 1 or r == 7


def classify(sig: ModeSignals) -> tuple[ScreenMode, float]:
    if sig.augment_title and sig.augment_names_matched >= 2:
        return ScreenMode.AUGMENT_SELECT, 0.95
    if sig.augment_names_matched >= 2 or (sig.augment_title and not sig.shop_hud and not sig.select_title):
        return ScreenMode.AUGMENT_SELECT, 0.75
    if not sig.shop_hud and (sig.game_over_title or sig.exit_button) and sig.stage is None:
        return ScreenMode.GAME_OVER, 0.95 if (sig.game_over_title and sig.exit_button) else 0.8
    if sig.select_title and not sig.shop_hud:
        return ScreenMode.ITEM_SELECT, 0.8
    if sig.shop_hud:
        if sig.board_count or sig.prep_banner:
            return ScreenMode.PLANNING, PLANNING_CONFIRMED
        # 스테이지를 못 읽었으면 PvE인지 알 수 없다 → 전투라고 하지 않는다(QA08: 1-4 크립 준비 화면에서 스테이지가 가려지면
        # combat 0.8로 확신 오답. 준비를 전투로 보면 추천이 멈추고, 전투를 준비로 보는 쪽은 해가 없다)
        if sig.enemy_bars >= MIN_ENEMY_BARS and sig.stage is not None and not _is_pve(sig.stage):
            return ScreenMode.COMBAT, COMBAT_PVP
        return ScreenMode.PLANNING, PLANNING_AMBIGUOUS if sig.shop_hud_by_ocr else PLANNING_PIXEL_ONLY
    if sig.dark:
        return ScreenMode.LOADING, 0.6
    if sig.stage is not None:
        stage_n, round_n = (int(x) for x in sig.stage.split("-"))
        if round_n == 4 or (stage_n == 1 and round_n == 1):
            return ScreenMode.CAROUSEL, 0.65
    return ScreenMode.UNKNOWN, 0.0
