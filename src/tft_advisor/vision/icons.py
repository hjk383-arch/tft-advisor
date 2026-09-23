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
    via_glyph: bool = False   # 1위 점수가 글리프 정규화 경로(대체 출처 증강 아이콘)에서 나왔는가


class IconMatcher:
    """`group`: ID → 동일시할 묶음 키(예: 사용 횟수만 다른 "자석 제거기" 변형). margin은 다른 묶음 1위와의 차이.

    `templates`는 {ID: 이미지} 또는 [(ID, 이미지), …](한 ID에 템플릿 여러 장).
    """

    def __init__(self, templates: dict[str, np.ndarray] | Iterable[tuple[str, np.ndarray]],
                 group: Callable[[str], str] | None = None,
                 size: int = TEMPLATE_SIZE, search: int = SEARCH_SIZE) -> None:
        pairs = list(templates.items()) if isinstance(templates, dict) else list(templates)
        self.pairs: list[tuple[str, np.ndarray]] = pairs
        self.group = group or (lambda api: api)
        self.size = size
        self.search = search
        """`size`/`search`: 템플릿 정규화 크기와 크롭 확대 크기. 아이템 **벤치** 칸(1080p 45px)은 기본값 32/38이고,
        유닛 **장착** 아이콘(1080p 25px)은 28/34가 실측에서 가장 높았다(16 보고서 §3)."""

    @property
    def ids(self) -> set[str]:
        return {a for a, _ in self.pairs}

    @classmethod
    def from_dirs(cls, dirs: Iterable[str | Path], valid: Callable[[str], bool] | None = None,
                  group: Callable[[str], str] | None = None,
                  size: int = TEMPLATE_SIZE, search: int = SEARCH_SIZE) -> IconMatcher:
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
                pairs.append((p.stem, cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)))
        return cls(pairs, group, size=size, search=search)

    def rescaled(self, size: int, search: int) -> IconMatcher:
        """같은 템플릿을 다른 크기로 다시 정규화한 매처(장착 아이콘용). 디스크를 다시 읽지 않는다."""
        import cv2

        pairs = [(a, cv2.resize(t, (size, size), interpolation=cv2.INTER_AREA)) for a, t in self.pairs]
        return IconMatcher(pairs, self.group, size=size, search=search)

    @classmethod
    def from_dir(cls, template_dir: str | Path, allowed: set[str] | None = None,
                 group: Callable[[str], str] | None = None) -> IconMatcher:
        return cls.from_dirs([template_dir], None if allowed is None else allowed.__contains__, group)

    def __len__(self) -> int:
        return len(self.pairs)

    # -- 빠른 경로 --------------------------------------------------------
    # `cv2.matchTemplate(..., TM_CCOEFF_NORMED)`를 템플릿마다 부르면 222장에 약 43ms다(파이썬 호출 비용).
    # 같은 값을 행렬곱 하나로 구한다: 템플릿을 **채널별 평균을 뺀 뒤 전체 L2로 정규화한 벡터**로 미리 쌓아 두고,
    # 크롭에서 가능한 이동 위치(±(search-size)/2)마다 같은 정규화를 한 패치를 만들어 내적한다.
    # 결과는 OpenCV와 같은 정의다(오차 1e-5 이하, `tests/test_vision_board.py`가 고정).

    def _bank(self) -> tuple[np.ndarray, list[str]] | None:
        bank = getattr(self, "_bank_cache", None)
        if bank is None:
            if not self.pairs:
                return None
            mats = []
            for _, tpl in self.pairs:
                v = tpl.astype(np.float32)
                v -= v.reshape(-1, v.shape[-1]).mean(axis=0) if v.ndim == 3 else v.mean()
                flat = v.ravel()
                n = float(np.linalg.norm(flat))
                mats.append(flat / n if n > 1e-6 else flat)
            bank = (np.asarray(mats, np.float32), [a for a, _ in self.pairs])
            self._bank_cache = bank
        return bank

    def match(self, crop: np.ndarray) -> IconMatch | None:
        if not self.pairs or crop.size == 0:
            return None
        import cv2

        bank = self._bank()
        if bank is None:
            return None
        mat, names = bank
        src = cv2.resize(crop, (self.search, self.search), interpolation=cv2.INTER_AREA).astype(np.float32)
        k = self.size
        span = self.search - k + 1
        patches = np.empty((span * span, mat.shape[1]), np.float32)
        i = 0
        for dy in range(span):
            for dx in range(span):
                w = src[dy:dy + k, dx:dx + k]
                w = w - w.reshape(-1, w.shape[-1]).mean(axis=0) if w.ndim == 3 else w - w.mean()
                flat = w.ravel()
                n = float(np.linalg.norm(flat))
                patches[i] = flat / n if n > 1e-6 else flat
                i += 1
        scores = (patches @ mat.T).max(axis=0)
        best: dict[str, float] = {}
        for api, sc in zip(names, scores):
            v = float(sc)
            if v > best.get(api, -2.0):
                best[api] = v
        ranked = sorted(((sc, api) for api, sc in best.items()), reverse=True)
        top, api = ranked[0]
        g = self.group(api)
        second = next((sc for sc, a in ranked[1:] if self.group(a) != g), -1.0)
        return IconMatch(api_name=api, score=top, margin=top - second)


# ---------------------------------------------------------------------------
# 보유 증강 줄 (보드 왼쪽 위, 1080p 원본 실측 2026-09-22)
# ---------------------------------------------------------------------------
# 준비 화면에서 보드 왼쪽 위에 짙은 회색 상자(BGR ≈ 28,29,29) 안에 보유 증강 글리프가 한 줄로 그려진다.
# 칸은 38x37px 정사각(1080p), 줄은 가운데 정렬(x≈0.266)로 증강 수만큼 늘어난다(2-2 1칸, 5-1 3칸 확인).
# 글리프는 CommunityDragon hexcore 아이콘(`fetch-augments`)과 같은 그림이다(어수선한 마음 0.905, 초월 0.956).

AUG_BG = (28, 29, 29)        # 줄 상자 배경색(BGR)
AUG_CELL = 38                # 칸을 이 크기로 맞춘다
AUG_PAD = 4                  # ±4px 위치 오차 탐색
AUG_TEMPLATE = 36            # 템플릿 크기(실측 최적: 칸 38 대비 36)


def find_icon_row(crop: np.ndarray, frame_h: int) -> list[tuple[int, int, int, int]]:
    """탐색 영역 크롭 → 증강 칸들 [(x1, y1, x2, y2)] (크롭 좌표, 왼쪽부터). 줄이 없으면 [].

    짙은 회색 상자를 찾는다: 배경색 픽셀 마스크 → 글리프 구멍을 닫기 → 가장 큰 덩어리. 높이가 칸 크기(화면 높이의 약 3.4%)와
    맞고 가로가 칸 크기의 정수배(1~5)에 가까울 때만 줄로 본다(보드 무늬·유닛 그림자 오탐 방지).
    """
    if crop.size == 0:
        return []
    import cv2

    diff = np.abs(crop.astype(np.int16) - np.array(AUG_BG, np.int16)).max(axis=2)
    mask = (diff <= 10).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return []
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(v) for v in stats[k][:4])
    cell = 0.0343 * frame_h                      # 1080p 37px
    if not 0.8 * cell <= h <= 1.25 * cell:
        return []
    count = round(w / h)
    if not 1 <= count <= 5 or abs(w / count - h) > 0.2 * h:
        return []
    if float(mask[y:y + h, x:x + w].mean()) < 0.85:   # 상자는 꽉 찬 사각형이어야 한다
        return []
    step = w / count
    return [(int(round(x + i * step)), y, int(round(x + (i + 1) * step)), y + h) for i in range(count)]


def _composite(img: np.ndarray, bg: tuple[int, int, int] = AUG_BG) -> np.ndarray:
    """RGBA/회색 아이콘 → 줄 배경색 위에 합성한 BGR."""
    if img.ndim == 2:
        return np.dstack([img] * 3)
    if img.shape[2] == 4:
        a = img[..., 3:].astype(np.float32) / 255.0
        return (img[..., :3].astype(np.float32) * a + np.array(bg, np.float32) * (1 - a)).astype(np.uint8)
    return img


GLYPH_SIZE = 36             # 글리프 정규화 비교 크기(글리프 외곽 정사각형 → 이 크기)
GLYPH_PAD = 3               # 정규화한 칸 주위 여백(±3px 탐색)
GLYPH_DIFF = 40             # 배경색과 이만큼(채널 최대 차) 다르면 글리프 픽셀


def glyph_normalize(img: np.ndarray, size: int = GLYPH_SIZE) -> np.ndarray | None:
    """아이콘/칸 → 글리프 외곽(배경과 다른 픽셀) 정사각형을 잘라 `size`로 맞춘 BGR. 글리프가 없으면 None.

    대체 출처(tactics.tools) 증강 아이콘은 글리프 둘레에 빛 번짐이 있고 글리프가 차지하는 비율(약 0.81)이 CDragon 원본(약 0.92)·
    게임 칸(약 0.95)과 달라, 원본 기하(`match`의 고정 크기 ±4px)로는 정답도 0.42~0.50에 그친다(1080p 실측). 양쪽을 글리프 외곽으로
    맞추면 정답 0.83~0.86, 다른 증강 1위 0.73 이하가 된다(`_workspace/09_vision_augment_icons.md` §3).
    """
    if img is None or img.size == 0:
        return None
    import cv2

    c = _composite(img)
    d = np.abs(c.astype(np.int16) - np.array(AUG_BG, np.int16)).max(axis=2)
    ys, xs = np.nonzero(d > GLYPH_DIFF)
    if len(xs) < 4:
        return None
    x1, x2, y1, y2 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    side = max(x2 - x1, y2 - y1)
    canvas = np.empty((side, side, 3), np.uint8)
    canvas[:] = AUG_BG
    ox, oy = (side - (x2 - x1)) // 2, (side - (y2 - y1)) // 2
    canvas[oy:oy + y2 - y1, ox:ox + x2 - x1] = c[y1:y2, x1:x2]
    return cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)


class AugmentIconMatcher:
    """증강 글리프 템플릿 매칭. 템플릿: {apiName: 이미지} 또는 [(apiName, 이미지)](실화면 템플릿은 ID별로 추가).

    `glyph_templates`: 대체 출처 아이콘(CDragon에 없는 증강). 칸과 템플릿을 모두 글리프 외곽으로 정규화해 비교한다
    (`glyph_normalize`). 한 ID의 점수는 두 경로 중 최댓값이고, 1위가 글리프 경로에서 나왔으면 `IconMatch.via_glyph`.
    """

    def __init__(self, templates: Iterable[tuple[str, np.ndarray]],
                 glyph_templates: Iterable[tuple[str, np.ndarray]] = ()) -> None:
        self.pairs: list[tuple[str, np.ndarray]] = []
        self.glyph_pairs: list[tuple[str, np.ndarray]] = []
        for api, img in templates:
            self.add(api, img)
        for api, img in glyph_templates:
            self.add(api, img, glyph=True)

    def add(self, api: str, img: np.ndarray, glyph: bool = False) -> bool:
        """템플릿 1장 추가(실시간 학습이 저장한 칸도 이것으로 바로 쓴다). 글리프가 없는 이미지는 False."""
        import cv2

        if glyph:
            g = glyph_normalize(img)
            if g is None:
                return False
            self.glyph_pairs.append((api, g))
        else:
            self.pairs.append((api, cv2.resize(_composite(img), (AUG_TEMPLATE, AUG_TEMPLATE),
                                               interpolation=cv2.INTER_AREA)))
        return True

    def __len__(self) -> int:
        return len(self.pairs) + len(self.glyph_pairs)

    @property
    def ids(self) -> set[str]:
        return {a for a, _ in self.pairs} | {a for a, _ in self.glyph_pairs}

    @staticmethod
    def _load_dir(d: Path, valid: Callable[[str], bool] | None) -> list[tuple[str, np.ndarray]]:
        import cv2

        out: list[tuple[str, np.ndarray]] = []
        if not d.is_dir():
            return out
        for p in sorted(d.glob("*.png")):
            if valid is not None and not valid(p.stem):
                log.warning("증강 템플릿 무시(정적 데이터 ID 아님): %s", p)
                continue
            img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if img is not None:
                out.append((p.stem, img))
        return out

    @classmethod
    def from_dirs(cls, dirs: Iterable[str | Path], valid: Callable[[str], bool] | None = None,
                  glyph_dirs: Iterable[str | Path] = ()) -> AugmentIconMatcher:
        pairs = [p for d in dirs for p in cls._load_dir(Path(d), valid)]
        glyphs = [p for d in glyph_dirs for p in cls._load_dir(Path(d), valid)]
        return cls(pairs, glyphs)

    def scores(self, cell: np.ndarray) -> dict[str, tuple[float, bool]]:
        """ID → (점수, 글리프 경로 여부). 한 ID의 점수는 그 ID 템플릿들(두 경로) 중 최댓값."""
        best: dict[str, tuple[float, bool]] = {}
        if cell.size == 0:
            return best
        import cv2

        if self.pairs:
            src = cv2.resize(cell, (AUG_CELL, AUG_CELL), interpolation=cv2.INTER_AREA)
            src = cv2.copyMakeBorder(src, AUG_PAD, AUG_PAD, AUG_PAD, AUG_PAD, cv2.BORDER_REPLICATE)
            for api, tpl in self.pairs:
                sc = float(cv2.matchTemplate(src, tpl, cv2.TM_CCOEFF_NORMED).max())
                if sc > best.get(api, (-2.0, False))[0]:
                    best[api] = (sc, False)
        if self.glyph_pairs:
            g = glyph_normalize(cell)
            if g is not None:
                src = cv2.copyMakeBorder(g, GLYPH_PAD, GLYPH_PAD, GLYPH_PAD, GLYPH_PAD, cv2.BORDER_CONSTANT,
                                         value=AUG_BG)
                for api, tpl in self.glyph_pairs:
                    sc = float(cv2.matchTemplate(src, tpl, cv2.TM_CCOEFF_NORMED).max())
                    if sc > best.get(api, (-2.0, False))[0]:
                        best[api] = (sc, True)
        return best

    def match(self, cell: np.ndarray) -> IconMatch | None:
        if not len(self) or cell.size == 0:
            return None
        best = self.scores(cell)
        if not best:
            return None
        ranked = sorted(((sc, api, via) for api, (sc, via) in best.items()), reverse=True)
        top, api, via = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else -1.0
        return IconMatch(api_name=api, score=top, margin=top - second, via_glyph=via)


def augment_cell_template(cell: np.ndarray) -> np.ndarray:
    """실화면 칸 → 저장용 템플릿(AUG_CELL로 맞춘 뒤 가운데 AUG_TEMPLATE만). `match`의 기하와 같아 자기 자신과 1.0."""
    import cv2

    big = cv2.resize(cell, (AUG_CELL, AUG_CELL), interpolation=cv2.INTER_AREA)
    off = (AUG_CELL - AUG_TEMPLATE) // 2
    return big[off:off + AUG_TEMPLATE, off:off + AUG_TEMPLATE]
