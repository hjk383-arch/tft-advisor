"""보드·벤치 유닛 **챔피언 이름** 식별 — 화면 픽셀만 쓴다.

근거와 실측: `_workspace/19_unit_naming.md`. `vision.board`가 찾은 칸(체력바·성급·장착 아이템)에 이름을 붙인다.

## 왜 한 가지 방법으로는 안 되는가
보드 유닛은 3D 모델이다. 네모난 챔피언 초상화와 그림이 달라 템플릿 매칭이 통하지 않는다.
그래서 **서로 다른 약한 신호 셋을 겹친다.**

| 신호 | 무엇을 알려 주나 | 한계 |
|---|---|---|
| ① 특성 패널(왼쪽) | 보드 유닛 **집합**. 패널 인원 = 보드의 서로 다른 챔피언 특성 합이므로, 정적 특성표로 풀면 집합이 거의 하나로 정해진다 | 자리(어느 유닛이 누구인지)는 모른다. 벤치는 세지 않는다. 상징·증강으로 특성이 늘면 풀이가 없다(→ 쓰지 않는다) |
| ② 모델 크롭 라이브러리 | 칸마다 "이 모델은 누구와 닮았나"(few-shot, 색 분포) | 표본이 있어야 한다. 사용자 라벨·구속 풀이로 확정된 칸에서 자란다 |
| ③ 같은 프레임 안의 중복 | 벤치 유닛이 이름을 아는 보드 유닛과 같은 모델이면 같은 챔피언이다 | 표본이 없어도 쓸 수 있지만 닮음 점수가 약하면 버린다 |

보드는 ①이 후보 집합을 정하고 ②·③이 그 안에서 칸을 배정한다(전역 최적 배정). 표본이 전혀 없어 칸 배정이
안 되면 **집합만** 내보낸다(`BoardNames.unplaced`: "보드에 이 챔피언들이 있다, 자리는 모른다").
벤치는 ②·③만 쓴다. 어느 쪽이든 **불확실하면 이름을 비운다**(틀린 이름보다 "모름"이 낫다).

## 라이브러리
`data/templates/{set}/units_screen/{apiName}/*.png` — 체력바 기준 1080p 정규화 크롭(112x112). Riot 아트워크이므로
커밋하지 않는다(`.gitignore`). 채우는 방법: `python -m tft_advisor.vision.templates harvest-units SCREENSHOT LABEL.json`
(라벨의 `name`), 그리고 실시간 자동 학습(`UnitNamer.autolearn`: 보드 칸 1개 · 풀이 1개 · 챔피언 1명으로 **모호하지 않게 강제된** 칸만 저장, QA 19 FAIL-1).
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:                                    # pragma: no cover
    from ..static_data import StaticData
    from .board import BoardRead
    from .regions import FrameMapper

log = logging.getLogger(__name__)

# --- 크롭 (1080p 기준 픽셀, 체력바 초록 왼쪽 끝·위쪽 끝 기준) ------------------------------
CROP_HALF_W = 56          # 체력바 가운데에서 좌우
CROP_DY1, CROP_DY2 = 8, 120   # 체력바 위쪽 끝 아래로
CROP_SIZE = 112           # 저장·비교 크기(정사각)

# --- 닮음 ---------------------------------------------------------------------------------
BG_DIST = 18.0            # 배경(크롭 테두리 색 3군집)과 Lab 거리가 이보다 크면 모델 픽셀
BAND_WEIGHT = 0.5         # 세로 3등분(머리·몸·다리) 색 분포의 가중치
_SIM_NORM = 1.0 + 3 * BAND_WEIGHT

# --- 판정 임계값(실측: `_workspace/19_unit_naming.md` §4) -----------------------------------
LIB_MIN_SCORE = 0.50      # 라이브러리 kNN 1위 최소 닮음(같은 모델 실측 0.56~0.92, 다른 모델 1위 <= 0.53)
LIB_MIN_MARGIN = 0.10     # 1위 챔피언 - 2위 챔피언
DUP_MIN_SCORE = 0.55      # 같은 프레임 보드 유닛과의 닮음(표본 없는 중복 판정)
DUP_MIN_MARGIN = 0.12
ASSIGN_MIN_MARGIN = 0.08  # 구속 배정: 이 칸이 다른 챔피언이 되면 전체 점수가 이만큼은 떨어져야 한다
ASSIGN_MIN_SCORE = 0.30   # 구속 배정: 배정된 챔피언과의 닮음 하한(강제 배정·소거법은 예외)
PANEL_SURE = 0.75         # 특성 패널 OCR 신뢰도가 이보다 낮으면 풀이 신뢰도를 깎고 디스크 자동 학습을 하지 않는다
ELIMINATION_MARGIN = 0.25  # 닮음이 낮아도 이 여유 이상이면 소거법 배정으로 받는다(다른 칸이 확실히 정해졌다)
MAX_SOLUTIONS = 24        # 특성 풀이가 이보다 많으면 구속으로 쓰지 않는다
MAX_BOARD_UNITS = 12
NAME_CONF_CAP = 0.95
AUTOLEARN_MIN_CONF = 0.85
AUTOLEARN_CAP = 24        # 챔피언당 디스크 표본 상한(오래된 자동 표본부터 지운다)

AVATAR_TRAIT_NAME = "화신"   # "선택된 화신의 특성은 중첩 2개로 간주" — 그 챔피언의 다른 특성은 2로 센다
EMBLEM_SUFFIX = " 상징"


# ---------------------------------------------------------------------------
# 1. 크롭과 닮음
# ---------------------------------------------------------------------------


def unit_crop(image: np.ndarray, cx: float, bar_y: float, box_h: int) -> np.ndarray:
    """체력바(가운데 x, 초록 위쪽 끝 y — 프레임 픽셀) → 모델 크롭을 1080p 크기로 맞춘 `CROP_SIZE` 정사각형.
    프레임 밖은 검정으로 채운다(가장자리 칸도 같은 모양의 크롭이 나오게)."""
    import cv2

    s = box_h / 1080.0
    x1, x2 = round(cx - CROP_HALF_W * s), round(cx + CROP_HALF_W * s)
    y1, y2 = round(bar_y + CROP_DY1 * s), round(bar_y + CROP_DY2 * s)
    h, w = image.shape[:2]
    out = np.zeros((max(1, y2 - y1), max(1, x2 - x1), 3), np.uint8)
    sx1, sy1, sx2, sy2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if sx2 > sx1 and sy2 > sy1:
        src = image[sy1:sy2, sx1:sx2]
        if src.ndim == 2:
            src = cv2.cvtColor(src, cv2.COLOR_GRAY2BGR)
        elif src.shape[2] == 4:
            src = cv2.cvtColor(src, cv2.COLOR_BGRA2BGR)
        out[sy1 - y1:sy2 - y1, sx1 - x1:sx2 - x1] = src
    return cv2.resize(out, (CROP_SIZE, CROP_SIZE), interpolation=cv2.INTER_AREA)


def model_mask(crop: np.ndarray) -> np.ndarray:
    """크롭에서 **모델 픽셀** 마스크. 배경(모래 보드·돌 벤치)은 크롭 테두리 색 3군집으로 잡고 그와 먼 픽셀을 남긴다.
    아군 체력바의 초록은 뺀다."""
    import cv2

    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    e = max(3, w // 18)
    ring = np.concatenate([lab[:, :e].reshape(-1, 3), lab[:, -e:].reshape(-1, 3), lab[-e:].reshape(-1, 3)])
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    cv2.setRNGSeed(7)
    _, _, cent = cv2.kmeans(ring, 3, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    dist = np.min(np.linalg.norm(lab.reshape(-1, 1, 3) - cent[None], axis=2), axis=1).reshape(h, w)
    mask = (dist > BG_DIST).astype(np.uint8)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = (hsv[..., 0] > 40) & (hsv[..., 0] < 85) & (hsv[..., 1] > 150) & (hsv[..., 2] > 120)
    mask[green] = 0
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def descriptor(crop: np.ndarray) -> np.ndarray:
    """모델 크롭 → 색 분포 기술자(float32). HSV 12x4x4 전체 + 세로 3등분 HS 12x3.
    3D 모델은 자세·애니메이션으로 모양이 흔들리지만 **스킨 색 구성**은 거의 그대로다(실측 §4)."""
    import cv2

    mask = model_mask(crop)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    parts = []
    hist = cv2.calcHist([hsv], [0, 1, 2], mask, [12, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
    parts.append(hist / (hist.sum() + 1e-6))
    h = crop.shape[0]
    for a, b in ((0, h // 3), (h // 3, 2 * h // 3), (2 * h // 3, h)):
        band = np.zeros_like(mask)
        band[a:b] = mask[a:b]
        hh = cv2.calcHist([hsv], [0, 1], band, [12, 3], [0, 180, 0, 256]).flatten()
        parts.append(BAND_WEIGHT * hh / (hh.sum() + 1e-6))
    return np.concatenate(parts).astype(np.float32)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """히스토그램 교집합(0~1)."""
    return float(np.minimum(a, b).sum() / _SIM_NORM)


# ---------------------------------------------------------------------------
# 2. 라이브러리 (few-shot)
# ---------------------------------------------------------------------------


def units_dir(set_number: int) -> Path:
    from ..static_data import PROJECT_ROOT

    return PROJECT_ROOT / "data" / "templates" / str(set_number) / "units_screen"


@dataclass
class UnitLibrary:
    """챔피언 ID → 모델 크롭 기술자 여러 개. 점수는 챔피언별 **최대** 닮음(1-NN)."""

    samples: list[tuple[str, np.ndarray]] = field(default_factory=list)
    save_dir: Path | None = None

    def __len__(self) -> int:
        return len(self.samples)

    @property
    def ids(self) -> set[str]:
        return {c for c, _ in self.samples}

    @classmethod
    def load(cls, directory: str | Path | None, valid: Any = None, *, save: bool = True) -> UnitLibrary:
        """`{dir}/{apiName}/*.png`를 읽는다. `valid(apiName)`가 False인 폴더는 경고하고 건너뛴다."""
        from .capture import load_image

        lib = cls(save_dir=Path(directory) if directory is not None and save else None)
        if directory is None or not Path(directory).is_dir():
            return lib
        for sub in sorted(p for p in Path(directory).iterdir() if p.is_dir()):
            if valid is not None and not valid(sub.name):
                log.warning("유닛 라이브러리: 알 수 없는 챔피언 폴더 %s — 건너뜀", sub.name)
                continue
            for png in sorted(sub.glob("*.png")):
                try:
                    img = load_image(png)
                except Exception:
                    log.warning("유닛 라이브러리: 읽기 실패 %s", png)
                    continue
                if img.shape[:2] != (CROP_SIZE, CROP_SIZE):
                    import cv2

                    img = cv2.resize(img, (CROP_SIZE, CROP_SIZE), interpolation=cv2.INTER_AREA)
                lib.samples.append((sub.name, descriptor(img[..., :3])))
        return lib

    def add(self, champion_id: str, crop: np.ndarray, *, persist: bool = False, tag: str = "auto") -> Path | None:
        """표본 추가. `persist`면 디스크에도 저장한다(같은 그림은 한 번만, 챔피언당 자동 표본 `AUTOLEARN_CAP`장)."""
        self.samples.append((champion_id, descriptor(crop)))
        if not persist or self.save_dir is None:
            return None
        import cv2

        d = self.save_dir / champion_id
        d.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(crop.tobytes()).hexdigest()[:12]
        path = d / f"{tag}_{digest}.png"
        if path.exists():
            return path
        ok, buf = cv2.imencode(".png", crop)
        if ok:
            buf.tofile(str(path))
        autos = sorted(d.glob("auto_*.png"), key=lambda p: p.stat().st_mtime)
        for old in autos[:max(0, len(autos) - AUTOLEARN_CAP)]:
            old.unlink(missing_ok=True)
        return path

    def scores(self, desc: np.ndarray, extra: Iterable[tuple[str, np.ndarray]] = ()) -> dict[str, float]:
        """챔피언 ID → 최대 닮음. `extra`는 이번 프레임에서만 쓰는 임시 표본(보드에서 이름을 안 유닛)."""
        out: dict[str, float] = {}
        for cid, ref in (*self.samples, *extra):
            s = similarity(desc, ref)
            if s > out.get(cid, -1.0):
                out[cid] = s
        return out


def rank(scores: Mapping[str, float]) -> tuple[str | None, float, float]:
    """(1위 ID, 1위 점수, 1위 - 2위). 비어 있으면 (None, 0, 0)."""
    if not scores:
        return None, 0.0, 0.0
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    second = ordered[1][1] if len(ordered) > 1 else 0.0
    return ordered[0][0], ordered[0][1], ordered[0][1] - second


# ---------------------------------------------------------------------------
# 3. 특성 패널 → 보드 챔피언 집합
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraitPanel:
    """특성 패널 판독. `counts`: 특성 ID → 인원. `complete`: 패널이 모든 특성을 보여 줬다("N+" 넘침 표시가 없다)."""

    counts: Mapping[str, int]
    complete: bool = True
    confidence: float = 1.0


@dataclass
class TraitTable:
    """정적 데이터에서 만든 챔피언 → 특성 기여(특성 ID → 1 또는 2) 표."""

    contrib: dict[str, dict[str, int]]
    emblem_trait: dict[str, str]
    unit_less: set[str]

    @classmethod
    def from_static(cls, static: StaticData) -> TraitTable:
        traits = static._load("traits")
        avatar = next((t["apiName"] for t in traits if t.get("name_ko") == AVATAR_TRAIT_NAME), None)
        by_name = {t.get("name_ko"): t["apiName"] for t in traits}
        contrib: dict[str, dict[str, int]] = {}
        for c in static._load("champions"):
            ts = [t for t in (c.get("traits") or []) if t]
            if not ts:
                continue
            w = 2 if avatar is not None and avatar in ts else 1
            contrib[c["apiName"]] = {t: (1 if t == avatar else w) for t in ts}
        emblem = {}
        for it in static._load("items"):
            name = it.get("name_ko") or ""
            if it.get("category") == "emblem" and name.endswith(EMBLEM_SUFFIX):
                tid = by_name.get(name[: -len(EMBLEM_SUFFIX)])
                if tid:
                    emblem[it["apiName"]] = tid
        return cls(contrib=contrib, emblem_trait=emblem,
                   unit_less={t["apiName"] for t in traits if t.get("unit_less")})


def solve_board_sets(panel: TraitPanel, n_units: int, table: TraitTable, *,
                     emblems: Iterable[str] = (), max_solutions: int = MAX_SOLUTIONS) -> list[frozenset[str]] | None:
    """패널 인원을 **정확히** 만드는 서로 다른 챔피언 집합들(크기 1..n_units). 구속으로 쓸 수 없으면 None.

    - 패널에 없는 특성을 가진 챔피언은 후보가 아니다(패널이 완전할 때만 풀기 때문).
    - 같은 챔피언 여러 기는 특성을 한 번만 센다 → 집합 크기 < 유닛 수면 나머지는 중복이다.
    - 장착 상징은 그 특성 인원을 1 줄여서 푼다(상징은 그 특성이 없는 유닛에만 낀다).
    - 풀이가 없거나 `max_solutions`보다 많으면 None(상징·증강·특수 유닛이 특성을 바꾼 것 → 구속을 쓰지 않는다).
    """
    if not panel.complete or n_units <= 0 or n_units > MAX_BOARD_UNITS:
        return None
    need = {t: c for t, c in panel.counts.items() if t not in table.unit_less and c > 0}
    for e in emblems:
        t = table.emblem_trait.get(e)
        if t is not None and t in need:
            need[t] -= 1
    need = {t: c for t, c in need.items() if c > 0}
    if not need:
        return None
    cands = sorted(c for c, tr in table.contrib.items() if all(t in need for t in tr))
    # 특성마다 그 특성을 채울 수 있는 후보가 있어야 한다
    if any(not any(t in table.contrib[c] for c in cands) for t in need):
        return None
    order = sorted(need, key=lambda t: sum(1 for c in cands if t in table.contrib[c]))
    sols: list[frozenset[str]] = []

    def dfs(left: dict[str, int], chosen: list[str], banned: set[str]) -> bool:
        open_t = next((t for t in order if left[t] > 0), None)
        if open_t is None:
            sols.append(frozenset(chosen))
            return len(sols) > max_solutions
        if len(chosen) >= n_units:
            return False
        # 가장 채우기 어려운 특성부터: 그 특성을 가진 후보 중 하나는 반드시 들어간다
        for c in cands:
            if c in banned or open_t not in table.contrib[c]:
                continue
            tr = table.contrib[c]
            if any(left[t] - w < 0 for t, w in tr.items()):
                continue
            for t, w in tr.items():
                left[t] -= w
            chosen.append(c)
            stop = dfs(left, chosen, banned | {c})
            chosen.pop()
            for t, w in tr.items():
                left[t] += w
            banned = banned | {c}      # 같은 집합을 순서만 바꿔 다시 세지 않는다
            if stop:
                return True
        return False

    overflow = dfs(dict(need), [], set())
    if overflow or not sols:
        return None
    return sorted(set(sols), key=lambda s: sorted(s))


# ---------------------------------------------------------------------------
# 4. 구속 배정 (칸 → 챔피언, 집합의 모든 챔피언이 한 번 이상)
# ---------------------------------------------------------------------------


_NEG = -1e9


def slot_values(S: np.ndarray) -> np.ndarray | None:
    """S[i, c] = 칸 i가 챔피언 c일 때 닮음. **모든 챔피언이 한 번 이상 쓰이는** 배정만 허용할 때,
    V[i, c] = "칸 i를 c로 고정했을 때 가능한 최대 합"(불가능하면 -1e9). 불가능한 문제면 None.

    앞·뒤 비트마스크 DP(마스크 = 이미 쓴 챔피언 집합)를 numpy로 한 번씩만 돌린다: O(n·k·2^k).
    """
    n, k = S.shape
    if k == 0 or k > n or k > MAX_BOARD_UNITS:
        return None
    size = 1 << k
    full = size - 1
    masks = np.arange(size)
    bits = [1 << c for c in range(k)]
    F = [np.full(size, _NEG)]
    F[0][0] = 0.0
    for i in range(n):
        nxt = np.full(size, _NEG)
        for c in range(k):
            np.maximum.at(nxt, masks | bits[c], F[i] + S[i, c])
        nxt[nxt < _NEG / 2] = _NEG
        F.append(nxt)
    if F[n][full] <= _NEG / 2:
        return None
    B = [None] * (n + 1)
    B[n] = np.full(size, _NEG)
    B[n][0] = 0.0
    for i in range(n - 1, -1, -1):
        cur = np.full(size, _NEG)
        for c in range(k):
            np.maximum.at(cur, masks | bits[c], B[i + 1] + S[i, c])
        cur[cur < _NEG / 2] = _NEG
        B[i] = cur
    V = np.full((n, k), _NEG)
    for i in range(n):
        sup = B[i + 1].copy()                  # sup[m] = max over m2 ⊇ m of B[m2]
        for bt in bits:
            without = (masks & bt) == 0
            sup[without] = np.maximum(sup[without], sup[masks[without] | bt])
        for c in range(k):
            need = full & ~(masks | bits[c])
            vals = F[i] + S[i, c] + sup[need]
            V[i, c] = vals.max()
    V[V < _NEG / 2] = _NEG
    return V


@dataclass(frozen=True)
class SlotName:
    """칸 하나의 이름 판정."""

    unit_id: str | None
    confidence: float
    source: str          # traits | library | duplicate | forced | none
    score: float = 0.0
    margin: float = 0.0


@dataclass(frozen=True)
class BoardNames:
    """한 프레임의 이름 판정."""

    board: tuple[SlotName, ...]
    bench: tuple[SlotName, ...]
    board_set: frozenset[str] | None = None
    """특성 패널로 확정한 보드 챔피언 집합(풀이가 하나일 때만)."""
    unplaced: tuple[str, ...] = ()
    """집합은 알지만 칸을 정하지 못한 보드 챔피언(이름 없는 보드 칸 수와 같을 때만 채운다)."""
    solutions: int = 0
    panel: TraitPanel | None = None


def _place_board(S: np.ndarray, champs: list[str], set_conf: float,
                 pair_sim: np.ndarray | None = None) -> tuple[float, list[SlotName]] | None:
    """집합 안에서 칸 배정 + 칸별 여유(margin = 1위 고정 합 - 2위 고정 합)로 신뢰도. (최대 합, 칸 판정).

    `pair_sim[i, j]` = 보드 칸끼리의 닮음. 풀이가 챔피언 **하나**인데 칸이 여럿이면 배정이 수학적으로는 "강제"되지만
    (모든 칸 = 그 챔피언), 실제로는 **특성 없는 체력바 유닛**(훈련 봇·골렘·식물 등, 패널에 안 잡힌다)이 섞여 있을 수 있다
    (QA 19 FAIL-1). 그래서 강제(`forced`)는 **칸이 하나**일 때만 쓰고, 칸이 여럿이면 서로 닮은 칸(같은 모델)이거나
    그 챔피언 표본과 닮은 칸에만 이름을 붙인다.
    """
    n, k = S.shape
    if k == 1 and n > 1:
        return _place_single_champion(S, champs[0], set_conf, pair_sim)
    V = slot_values(S)
    if V is None:
        return None
    total = float(V.max(axis=1).max())
    out: list[SlotName] = []
    for i in range(n):
        order = np.argsort(-V[i])
        c = int(order[0])
        second = float(V[i, order[1]]) if len(order) > 1 else _NEG
        forced = second <= _NEG / 2
        margin = float(V[i, c] - second) if not forced else math.inf
        score = float(S[i, c])
        if forced:                      # 여기 오는 것은 칸 1개 · 챔피언 1명뿐이다
            out.append(SlotName(champs[c], round(set_conf * NAME_CONF_CAP, 3), "forced", round(score, 3), 99.0))
        elif margin >= ASSIGN_MIN_MARGIN and (score >= ASSIGN_MIN_SCORE or margin >= ELIMINATION_MARGIN):
            # 닮음이 낮아도(표본 없는 챔피언) 다른 칸들이 확실히 정해져 남은 자리가 하나뿐이면 소거법으로 정한다
            conf = set_conf * min(NAME_CONF_CAP, 0.6 + margin)
            out.append(SlotName(champs[c], round(conf, 3), "traits", round(score, 3), round(margin, 3)))
        else:
            out.append(SlotName(None, 0.0, "none", round(score, 3), round(margin, 3)))
    return total, out


def _place_single_champion(S: np.ndarray, champ: str, set_conf: float,
                           pair_sim: np.ndarray | None) -> tuple[float, list[SlotName]]:
    """풀이 = 챔피언 하나, 보드 칸 여럿. 칸이 모두 서로 닮았으면(같은 모델 여러 기) 전부 그 챔피언,
    아니면 그 챔피언 표본과 닮은 칸만. 나머지는 모름(특성 없는 유닛일 수 있다). 어느 쪽도 `forced`가 아니다(디스크 저장 안 함)."""
    n = S.shape[0]
    total = float(S[:, 0].sum())
    alike = pair_sim is not None and all(
        pair_sim[i, j] >= DUP_MIN_SCORE for i in range(n) for j in range(i + 1, n))
    out: list[SlotName] = []
    for i in range(n):
        score = float(S[i, 0])
        if alike:
            out.append(SlotName(champ, round(set_conf * 0.85, 3), "traits", round(score, 3), 0.0))
        elif score >= LIB_MIN_SCORE:
            out.append(SlotName(champ, round(set_conf * min(0.85, 0.3 + 0.6 * score), 3), "traits",
                                round(score, 3), 0.0))
        else:
            out.append(SlotName(None, 0.0, "none", round(score, 3), 0.0))
    return total, out


def _library_name(scores: Mapping[str, float], *, min_score: float, min_margin: float, source: str) -> SlotName:
    cid, s, mg = rank(scores)
    if cid is None or s < min_score or mg < min_margin:
        return SlotName(None, 0.0, "none", round(s, 3), round(mg, 3))
    conf = min(0.92, 0.3 + 0.6 * s + 0.8 * mg)
    return SlotName(cid, round(conf, 3), source, round(s, 3), round(mg, 3))


def name_units(board_desc: Sequence[np.ndarray], bench_desc: Sequence[np.ndarray], library: UnitLibrary,
               panel: TraitPanel | None, table: TraitTable | None, *, emblems: Iterable[str] = ()) -> BoardNames:
    """칸 기술자들 → 이름. 규칙은 모듈 docstring."""
    n = len(board_desc)
    lib_scores = [library.scores(d) for d in board_desc]
    pair_sim = np.array([[similarity(a, b) for b in board_desc] for a in board_desc]) if n else None
    board: list[SlotName] = [SlotName(None, 0.0, "none")] * n
    sols = None
    if panel is not None and table is not None and n:
        sols = solve_board_sets(panel, n, table, emblems=emblems)
    board_set: frozenset[str] | None = None
    unplaced: tuple[str, ...] = ()
    if sols:
        # 풀이마다 배정 점수를 구해 가장 좋은 풀이를 쓴다. 풀이가 여럿이면 신뢰도를 깎는다.
        scored = []
        for sol in sols:
            champs = sorted(sol)
            S = np.array([[lib_scores[i].get(c, 0.0) for c in champs] for i in range(n)], dtype=np.float64)
            placed = _place_board(S, champs, 1.0, pair_sim)
            if placed is not None:
                scored.append((placed[0], champs, S))
        scored.sort(key=lambda t: -t[0])
        if scored:
            top_total, champs, S = scored[0]
            if len(scored) == 1:
                # 패널 판독이 애매했으면(OCR 점수 낮음) 풀이가 하나여도 신뢰도를 깎는다(QA 19 §3: 행 하나 오독 → 틀린 단일 풀이)
                set_conf = 1.0 if panel.confidence >= PANEL_SURE else 0.85
                board_set = frozenset(champs)
            else:
                # 풀이가 둘 이상: 닮음 합이 확실히 갈라 줄 때만 쓴다(그래도 신뢰도는 깎는다)
                set_conf = 0.85 if top_total - scored[1][0] >= 0.3 else 0.0
            if set_conf > 0:
                placed = _place_board(S, champs, set_conf, pair_sim)
                board = placed[1] if placed is not None else board
                if board_set is not None:
                    named = Counter(s.unit_id for s in board if s.unit_id)
                    left = Counter({c: 1 for c in champs}) - named
                    free = sum(1 for s in board if s.unit_id is None)
                    if free and sum(left.values()) == free:
                        unplaced = tuple(sorted(left.elements()))
    if not sols or all(s.unit_id is None for s in board) and board_set is None:
        # 구속이 없으면 라이브러리만(더 엄격하게). 풀이가 여럿이라 구속을 못 썼으면 **풀이 합집합 안의** 챔피언만 받는다
        allowed = frozenset().union(*sols) if sols else None
        board = [_library_name({c: v for c, v in sc.items() if allowed is None or c in allowed},
                               min_score=LIB_MIN_SCORE, min_margin=LIB_MIN_MARGIN, source="library")
                 if b.unit_id is None else b for sc, b in zip(lib_scores, board)]
    # 벤치: 라이브러리 + 이번 프레임에서 이름을 안 보드 유닛(같은 모델 = 같은 챔피언)
    extra = [(b.unit_id, d) for b, d in zip(board, board_desc) if b.unit_id and b.confidence >= 0.8]
    bench: list[SlotName] = []
    for d in bench_desc:
        lib = _library_name(library.scores(d), min_score=LIB_MIN_SCORE, min_margin=LIB_MIN_MARGIN, source="library")
        dup = SlotName(None, 0.0, "none")
        if len({c for c, _ in extra}) >= 2:     # 후보가 하나뿐이면 2위와의 차가 없다 → 쓰지 않는다
            dup = _library_name(UnitLibrary().scores(d, extra), min_score=DUP_MIN_SCORE,
                                min_margin=DUP_MIN_MARGIN, source="duplicate")
        if lib.unit_id and dup.unit_id and lib.unit_id != dup.unit_id:
            lib = SlotName(None, 0.0, "none", lib.score, lib.margin)   # 두 신호가 엇갈리면 모름
        elif lib.unit_id is None and dup.unit_id:
            lib = dup
        elif lib.unit_id and dup.unit_id and dup.confidence > lib.confidence:
            lib = SlotName(lib.unit_id, dup.confidence, lib.source, lib.score, lib.margin)
        bench.append(lib)
    return BoardNames(board=tuple(board), bench=tuple(bench), board_set=board_set, unplaced=unplaced,
                      solutions=len(sols or ()), panel=panel)


# ---------------------------------------------------------------------------
# 5. 특성 패널 인원 보정 (OCR이 가는 "1" 글자를 놓칠 때)
# ---------------------------------------------------------------------------


_LADDER_RE = re.compile(r"^\s*([0-9Il|])\s*[/1]\s*([0-9Il|])\s*$")


def ladder_count(text: str) -> tuple[int, int] | None:
    """비활성 특성 행의 "인원/다음 구간" 글자("1/2", OCR이 "112"·"|/|"로 읽는 것 포함) → (인원, 다음 구간)."""
    t = text.strip().replace(" ", "")
    m = _LADDER_RE.match(t)
    if not m:
        return None
    a, b = (1 if ch in "Il|" else int(ch) for ch in m.groups())
    return a, b


def thin_one_glyph(crop: np.ndarray, ref_h: float | None = None) -> bool:
    """인원 칸 글자가 가는 세로 막대 하나("1"/"I")인가. OCR이 이 글자를 자주 놓친다.
    `ref_h`: 같은 행 이름 글자 높이(px). 막대 높이가 그 0.4~1.3배여야 한다(없으면 칸 높이의 0.3배 이상)."""
    import cv2

    if crop.size == 0:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    bw = (gray >= 170).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    comps = [st for st in stats[1:] if st[4] >= 4]
    if len(comps) != 1:
        return False
    _, _, w, h, _ = comps[0]
    lo, hi = (0.4 * ref_h, 1.3 * ref_h) if ref_h else (0.3 * gray.shape[0], gray.shape[0])
    return lo <= h <= hi and w <= max(2, 0.35 * h)


# ---------------------------------------------------------------------------
# 6. 인식기에 붙는 판독기
# ---------------------------------------------------------------------------


SESSION_SAMPLES_MAX = 200     # 실행 중 메모리에만 더하는 표본 상한(오래된 것부터 버린다)
SESSION_NOVELTY = 0.90        # 같은 챔피언의 기존 표본과 이보다 닮았으면 더하지 않는다(가만히 선 유닛 = 매 프레임 같은 그림)


@dataclass
class UnitNamer:
    """`BoardRead`에 챔피언 이름을 붙인다. `Recognizer`가 하나 들고 재사용한다(라이브러리를 다시 읽지 않는다).

    `autolearn`: 특성 패널 구속으로 **강제된** 칸(보드에 챔피언이 한 종류뿐 등)의 크롭을 디스크 라이브러리에 저장한다.
    구속 배정·라이브러리로 이름을 붙인 칸(신뢰도 >= `AUTOLEARN_MIN_CONF`)은 이번 실행 동안 메모리에만 더한다
    (자기 강화 오류가 디스크에 쌓이지 않게).
    """

    library: UnitLibrary
    table: TraitTable | None
    names: Mapping[str, str] = field(default_factory=dict)
    autolearn: bool = False
    _session: int = 0
    _base: int = 0

    @classmethod
    def from_static(cls, static: StaticData, directory: str | Path | None = None, *,
                    autolearn: bool = False) -> UnitNamer:
        d = units_dir(static.set_number) if directory is None else directory
        lib = UnitLibrary.load(d, valid=lambda stem: static.get("champions", stem) is not None, save=autolearn)
        names = {c["apiName"]: c.get("name_ko") or c["apiName"] for c in static._load("champions")}
        return cls(library=lib, table=TraitTable.from_static(static), names=names, autolearn=autolearn,
                   _base=len(lib))

    def crops(self, image: np.ndarray, m: FrameMapper, slots: Sequence[Any]) -> list[np.ndarray]:
        left, top, w, h = m.box
        return [unit_crop(image, left + u.anchor[0] * w, top + u.anchor[1] * h, h) for u in slots]

    def name(self, image: np.ndarray, m: FrameMapper, read: BoardRead, panel: TraitPanel | None) -> BoardRead:
        """판독에 이름을 붙인 새 `BoardRead`. 유닛이 없으면 그대로 돌려준다."""
        from dataclasses import replace

        if read.count == 0:
            return read
        bc, nc = self.crops(image, m, read.board), self.crops(image, m, read.bench)
        bd, nd = [descriptor(c) for c in bc], [descriptor(c) for c in nc]
        emblems = [i for u in read.board for i in u.items]
        res = name_units(bd, nd, self.library, panel, self.table, emblems=emblems)
        self._learn(bc, bd, res.board, persist_ok=self._persist_ok(res, bd, panel))
        self._learn(nc, nd, res.bench)

        def put(u: Any, n: SlotName) -> Any:
            return replace(u, unit_id=n.unit_id, unit_conf=n.confidence, name_source=n.source)

        board = tuple(put(u, n) for u, n in zip(read.board, res.board))
        bench = tuple(put(u, n) for u, n in zip(read.bench, res.bench))
        return replace(read, board=board, bench=bench,
                       board_set=tuple(sorted(res.board_set)) if res.board_set else (),
                       unplaced=res.unplaced, trait_solutions=res.solutions)

    def _persist_ok(self, res: BoardNames, board_desc: Sequence[np.ndarray], panel: TraitPanel | None) -> bool:
        """디스크 자동 학습을 해도 되는 **모호하지 않은** 경우만: 보드 칸 1개 · 풀이 1개 · 집합 크기 1 ·
        패널 판독 신뢰도 충분 · 라이브러리가 다른 챔피언이라고 확신하지 않음(모델 닮음과 모순 없음)."""
        if not (self.autolearn and len(board_desc) == 1 and res.solutions == 1 and res.board_set
                and len(res.board_set) == 1 and panel is not None and panel.confidence >= PANEL_SURE):
            return False
        cid, s, mg = rank(self.library.scores(board_desc[0]))
        champ = next(iter(res.board_set))
        return not (cid is not None and cid != champ and s >= LIB_MIN_SCORE and mg >= LIB_MIN_MARGIN)

    def _learn(self, crops: Sequence[np.ndarray], descs: Sequence[np.ndarray], names: Sequence[SlotName],
               *, persist_ok: bool = False) -> None:
        """이름 붙은 칸을 메모리 표본으로 더한다. 디스크에는 `persist_ok`(`_persist_ok`)이고 `forced`인 칸만 쓴다."""
        for crop, d, n in zip(crops, descs, names):
            if n.unit_id is None or n.confidence < AUTOLEARN_MIN_CONF:
                continue
            same = [similarity(d, ref) for cid, ref in self.library.samples if cid == n.unit_id]
            if same and max(same) >= SESSION_NOVELTY:
                continue
            persist = self.autolearn and persist_ok and n.source == "forced"
            self.library.add(n.unit_id, crop, persist=persist, tag="auto")
            self._session += 1
            if self._session > SESSION_SAMPLES_MAX:
                # 이번 실행에서 더한 가장 오래된 표본을 버린다(디스크에서 읽은 표본은 앞쪽 `_base`개)
                del self.library.samples[min(self._base, len(self.library.samples) - 1)]
                self._session -= 1


def labeled_crops(image: np.ndarray, expected_extras: Mapping[str, Any], profile: Any,
                  content: tuple[int, int, int, int] | None = None) -> tuple[list[tuple[str, np.ndarray]], list[str]]:
    """정답 라벨(`fixtures.load_expected(...).extras`의 board_slots/bench_slots, **확인된 `unit_id`만**) →
    [(챔피언 ID, 모델 크롭)]. 라벨 자리에 체력바가 없으면 오류 메시지로 돌려준다(라벨·판독 어긋남)."""
    from .board import BoardReader
    from .regions import FrameMapper

    m = FrameMapper.for_image(image, content)
    read = BoardReader().read(image, m, profile)
    by_hex = {u.hex: u for u in read.board if u.hex is not None}
    by_slot = {u.bench_slot: u for u in read.bench if u.bench_slot is not None}
    left, top, w, h = m.box
    out: list[tuple[str, np.ndarray]] = []
    errors: list[str] = []
    for key, table, pos in (("board_slots", by_hex, "hex"), ("bench_slots", by_slot, "slot")):
        for lab in expected_extras.get(key) or ():
            cid = lab.get("unit_id")
            if not cid:
                continue
            u = table.get(lab.get(pos))
            if u is None:
                errors.append(f"{key} {lab.get(pos)}: 화면에서 그 자리의 유닛을 찾지 못했습니다")
                continue
            out.append((cid, unit_crop(image, left + u.anchor[0] * w, top + u.anchor[1] * h, h)))
    return out, errors


def library_from(pairs: Iterable[tuple[str, np.ndarray]]) -> UnitLibrary:
    """(ID, 크롭) → 메모리 라이브러리(디스크에 쓰지 않는다). 테스트·평가용."""
    lib = UnitLibrary()
    for cid, crop in pairs:
        lib.add(cid, crop)
    return lib


__all__ = ["BoardNames", "UnitNamer", "labeled_crops", "library_from", "SlotName", "TraitPanel", "TraitTable", "UnitLibrary", "descriptor", "ladder_count",
           "model_mask", "name_units", "rank", "similarity", "solve_board_sets", "thin_one_glyph", "unit_crop",
           "units_dir"]
