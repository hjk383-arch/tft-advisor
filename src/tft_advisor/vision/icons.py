"""아이콘 템플릿 매칭 (아이템 벤치 칸 등 텍스트가 없는 대상).

템플릿: `data/templates/{set}/items/{apiName}.png`(CommunityDragon 원본 아이콘, `templates.py fetch-items`)와
`data/templates/{set}/items_screen/{apiName}.png`(원본 캡처에서 잘라낸 실화면 템플릿, `templates.py harvest-items`).
두 디렉터리는 **ID별로 합친다**(QA04-V1): 실화면 템플릿은 원본 아이콘을 대체하지 않고 **추가**되며, 한 ID의 점수는
그 ID 템플릿들 중 최댓값이다. 실화면 템플릿이 몇 개뿐이어도 나머지 아이템은 원본 아이콘으로 계속 인식된다.
파일명(stem)은 정적 데이터 아이템 apiName이어야 하고, 아니면 로드하지 않고 경고한다(한글 파일명 등).
템플릿이 없으면 매처는 비어 있고 결과는 None이다.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

TEMPLATE_SIZE = 32          # 템플릿 정규화 크기(px)
SEARCH_SIZE = 38            # 크롭을 이 크기로 키워 ±3px 위치 오차를 탐색


def slot_is_empty(crop: np.ndarray, max_mean: float = 40.0, max_std: float = 18.0) -> bool:
    """빈 아이템 칸 = 거의 검은 사각형(fixture 관측). 밝기 평균·표준편차가 모두 낮으면 빈 칸."""
    if crop.size == 0:
        return True
    import cv2

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    h, w = gray.shape
    inner = gray[h // 5: h - h // 5, w // 5: w - w // 5]   # 테두리 제외
    return float(inner.mean()) <= max_mean and float(inner.std()) <= max_std


@dataclass(frozen=True)
class IconMatch:
    api_name: str
    score: float      # TM_CCOEFF_NORMED 최댓값 (-1~1)
    margin: float     # 1위 - 다른 묶음 1위


class IconMatcher:
    """`group`: ID → 동일시할 묶음 키(예: 사용 횟수만 다른 "자석 제거기" 변형). margin은 다른 묶음 1위와의 차이.

    `templates`는 {ID: 이미지} 또는 [(ID, 이미지), …](한 ID에 템플릿 여러 장).
    """

    def __init__(self, templates: dict[str, np.ndarray] | Iterable[tuple[str, np.ndarray]],
                 group: Callable[[str], str] | None = None) -> None:
        pairs = list(templates.items()) if isinstance(templates, dict) else list(templates)
        self.pairs: list[tuple[str, np.ndarray]] = pairs
        self.group = group or (lambda api: api)

    @property
    def ids(self) -> set[str]:
        return {a for a, _ in self.pairs}

    @classmethod
    def from_dirs(cls, dirs: Iterable[str | Path], valid: Callable[[str], bool] | None = None,
                  group: Callable[[str], str] | None = None) -> IconMatcher:
        """여러 디렉터리의 `{ID}.png`를 ID별로 합쳐 로드. `valid(stem)`이 False면 건너뛰고 경고."""
        import cv2

        from .capture import load_image

        pairs: list[tuple[str, np.ndarray]] = []
        for d in dirs:
            d = Path(d)
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.png")):
                if valid is not None and not valid(p.stem):
                    log.warning("아이템 템플릿 무시(정적 데이터 ID 아님): %s", p)
                    continue
                img = load_image(p)
                pairs.append((p.stem, cv2.resize(img, (TEMPLATE_SIZE, TEMPLATE_SIZE), interpolation=cv2.INTER_AREA)))
        return cls(pairs, group)

    @classmethod
    def from_dir(cls, template_dir: str | Path, allowed: set[str] | None = None,
                 group: Callable[[str], str] | None = None) -> IconMatcher:
        return cls.from_dirs([template_dir], None if allowed is None else allowed.__contains__, group)

    def __len__(self) -> int:
        return len(self.pairs)

    def match(self, crop: np.ndarray) -> IconMatch | None:
        if not self.pairs or crop.size == 0:
            return None
        import cv2

        src = cv2.resize(crop, (SEARCH_SIZE, SEARCH_SIZE), interpolation=cv2.INTER_AREA)
        best: dict[str, float] = {}
        for api, tpl in self.pairs:
            sc = float(cv2.matchTemplate(src, tpl, cv2.TM_CCOEFF_NORMED).max())
            if sc > best.get(api, -2.0):
                best[api] = sc
        scores = sorted(((sc, api) for api, sc in best.items()), reverse=True)
        top, api = scores[0]
        g = self.group(api)
        second = next((sc for sc, a in scores[1:] if self.group(a) != g), -1.0)
        return IconMatch(api_name=api, score=top, margin=top - second)
