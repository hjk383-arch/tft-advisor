"""ROI 묶음별 변화 감지 (QA04-V7) — app 루프가 OCR을 바뀐 묶음에만, 화면이 안정된 뒤에만 돌리게 한다.

    from tft_advisor.vision.change import ChangeDetector, roi_signatures
    det = ChangeDetector.from_cfg(profile, settings.vision)   # change_threshold, change_stable_frames
    for frame in source:                                  # 약 4 FPS
        todo = det.update(frame.image, content=box)       # 바뀌었고 2프레임 연속 같은 묶음
        if todo:
            partial = recognizer.recognize(frame.image, content=box, groups=todo & set(GROUPS))
            ...  # 직전 GameState에 FIELD_GROUP 기준으로 합친다(app 책임)

- 서명: ROI마다 회색조로 바꿔 32x16(세로로 긴 ROI는 16x32)으로 줄인 uint8 배열. 묶음 하나에 약 0.1~0.3ms.
- 변화 판정: 같은 ROI 서명의 픽셀 절대차 최댓값 > `threshold`(기본 24/255). 숫자 한 글자가 바뀌면 넘고,
  캡처·인코딩 잡음(±5)은 넘지 않는다.
- 안정: 새 서명이 `stable_frames` 프레임 연속 같아야(서로 변화 없음) 보고한다. 상점 새로고침·구매 애니메이션,
  캐러셀 회전처럼 움직이는 동안에는 읽지 않는다.
- 묶음 이름은 `recognizer.GROUPS`와 같고, 추가로 "stage"(스테이지 글자·라운드 아이콘)가 있다. "stage"가 바뀌면
  화면 상태가 바뀌었을 수 있으니 app은 전체 인식을 돌리면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import VisionCfg
from .regions import FrameMapper, Profile, Rect

SIG_WIDE = (32, 16)     # (w, h)
SIG_TALL = (16, 32)


def roi_groups(profile: Profile) -> dict[str, tuple[Rect, ...]]:
    """묶음 이름 → ROI들."""
    P = profile
    return {
        "stage": (P.stage, P.round_icons),
        "hud": (P.level, P.xp, P.shop_odds, P.gold, P.streak_icon, P.streak_value, P.xp_button, P.refresh_button),
        "shop": P.shop_names + P.shop_costs,
        "augment": (P.augment_title, *P.augment_names),
        "items": P.item_slots,
        "players": (P.player_list,),
        "traits": (P.traits_panel,),
    }


def _sig(crop: np.ndarray) -> np.ndarray:
    import cv2

    if crop.size == 0:
        return np.zeros((1, 1), np.uint8)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    h, w = gray.shape
    return cv2.resize(gray, SIG_WIDE if w >= h else SIG_TALL, interpolation=cv2.INTER_AREA)


def roi_signatures(image: np.ndarray, profile: Profile,
                   content: tuple[int, int, int, int] | None = None) -> dict[str, list[np.ndarray]]:
    """묶음별 작은 회색조 서명."""
    m = FrameMapper.for_image(image, content)
    return {name: [_sig(m.crop(image, r)) for r in rois] for name, rois in roi_groups(profile).items()}


def _differs(a: list[np.ndarray], b: list[np.ndarray], threshold: float) -> bool:
    if len(a) != len(b):
        return True
    for x, y in zip(a, b):
        if x.shape != y.shape:
            return True
        if int(np.abs(x.astype(np.int16) - y.astype(np.int16)).max()) > threshold:
            return True
    return False


def changed_groups(prev: dict[str, list[np.ndarray]] | None, cur: dict[str, list[np.ndarray]],
                   threshold: float = 24.0) -> set[str]:
    """두 서명 사이에 바뀐 묶음. prev가 없으면 전부."""
    if prev is None:
        return set(cur)
    return {k for k, v in cur.items() if k not in prev or _differs(prev[k], v, threshold)}


@dataclass
class ChangeDetector:
    """변화 + 안정(N프레임 연속 동일) 판정. 상태는 서명뿐이다(인식 결과는 들고 있지 않다)."""

    profile: Profile
    stable_frames: int = 2
    threshold: float = 24.0
    _committed: dict[str, list[np.ndarray]] = field(default_factory=dict)   # 마지막으로 보고한 서명
    _last: dict[str, list[np.ndarray]] = field(default_factory=dict)        # 직전 프레임 서명
    _run: dict[str, int] = field(default_factory=dict)                      # 직전과 같은 연속 프레임 수

    @classmethod
    def from_cfg(cls, profile: Profile, cfg: VisionCfg) -> ChangeDetector:
        """설정 `[vision] change_threshold`, `change_stable_frames`로 만든다(app 루프용).
        content는 `update(image, content=cfg.content_px(w, h))`로 넘긴다(Recognizer와 같은 영역)."""
        return cls(profile, stable_frames=cfg.change_stable_frames, threshold=cfg.change_threshold)

    def update(self, image: np.ndarray, content: tuple[int, int, int, int] | None = None) -> set[str]:
        return self.update_signatures(roi_signatures(image, self.profile, content))

    def update_signatures(self, sigs: dict[str, list[np.ndarray]]) -> set[str]:
        """이번 프레임 서명 → 지금 다시 읽어야 할 묶음(바뀌었고 stable_frames 연속 같음)."""
        ready: set[str] = set()
        for k, v in sigs.items():
            same_as_last = k in self._last and not _differs(self._last[k], v, self.threshold)
            self._run[k] = self._run.get(k, 0) + 1 if same_as_last else 1
            self._last[k] = v
            if self._run[k] < self.stable_frames:
                continue
            if k not in self._committed or _differs(self._committed[k], v, self.threshold):
                self._committed[k] = v
                ready.add(k)
        return ready

    def reset(self) -> None:
        self._committed.clear()
        self._last.clear()
        self._run.clear()
