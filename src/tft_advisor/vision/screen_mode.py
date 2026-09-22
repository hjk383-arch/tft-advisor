"""화면 상태 판별 — 픽셀/OCR 신호 → ScreenMode + 신뢰도.

신호(모두 화면 픽셀에서 얻는다)
- shop_hud: 하단 "경험치 구매"/"새로고침" 버튼이 보인다(OCR, 없으면 픽셀 대안)
- augment_title: 가운데 "하나 선택" 제목, augment_names_matched: 증강 이름 칸 3개 중 퍼지 매칭 성공 수
- stage: 상단 스테이지 문자열
- dark: 프레임 전체가 어둡고 평탄(로딩 추정)

한계(PROVISIONAL): 전투 중(COMBAT)·게임 종료(GAME_OVER)·세트 특수 선택(ITEM_SELECT)은 fixture가 없어 판별 규칙이 없다.
상점 HUD는 전투 중에도 보이므로 현재 PLANNING은 "준비 또는 전투"를 뜻한다 → 신뢰도를 0.7로 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..contracts import ScreenMode


@dataclass(frozen=True)
class ModeSignals:
    shop_hud: bool
    shop_hud_by_ocr: bool
    augment_title: bool
    augment_names_matched: int
    stage: str | None
    dark: bool


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


def classify(sig: ModeSignals) -> tuple[ScreenMode, float]:
    if sig.augment_title and sig.augment_names_matched >= 2:
        return ScreenMode.AUGMENT_SELECT, 0.95
    if sig.augment_names_matched >= 2 or (sig.augment_title and not sig.shop_hud):
        return ScreenMode.AUGMENT_SELECT, 0.75
    if sig.shop_hud:
        return ScreenMode.PLANNING, 0.7 if sig.shop_hud_by_ocr else 0.55
    if sig.dark:
        return ScreenMode.LOADING, 0.6
    if sig.stage is not None:
        stage_n, round_n = (int(x) for x in sig.stage.split("-"))
        if round_n == 4 or (stage_n == 1 and round_n == 1):
            return ScreenMode.CAROUSEL, 0.65
    return ScreenMode.UNKNOWN, 0.0
