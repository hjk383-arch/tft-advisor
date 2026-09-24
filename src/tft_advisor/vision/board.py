"""보드·벤치 유닛 판독 — **자리 · 성급 · 장착 아이템**만 읽는다(챔피언 정체는 읽지 않는다).

근거와 실측: `_workspace/16_board_vision.md`. 안전 원칙은 그대로다 — 화면 픽셀만 본다.

## 챔피언 이름은 여기서 읽지 않는다
보드 유닛은 3D 모델이라 각도·애니메이션·스킨·성급마다 그림이 달라진다. 이 모듈은 **2D로 그려져 흔들리지 않는 것만** 읽고,
이름은 `vision.units.UnitNamer`가 따로 붙인다(특성 패널 구속 + 모델 크롭 라이브러리, `_workspace/19_unit_naming.md`):

| 읽는 것 | 신호 | 실측 |
|---|---|---|
| 유닛이 있다 | 머리 위 **초록 체력바**(아군 고정 색) | 만피 64x4px @1080p, 항상 같은 모양 |
| 성급 | 체력바 왼쪽 **성급 배지**(2D 스프라이트, 픽셀 단위로 동일) | 1성 청동 꺾쇠 / 2성 은색 마름모 |
| 장착 아이템 | 체력바 **아래 붙는 아이콘 0~3개** | 칸 25px, 아이콘 23px @1080p |
| 자리 | 체력바 x → 칸(열), y → 줄(행) | 열은 확실, **줄은 추정**(§ `regions.BAR_RISE`) |

`BoardReader.read()`의 `unit_id`는 None이고, `Recognizer`가 `UnitNamer`로 채운다. `app.unit_merge`가 상점 구매 장부와 합쳐
`GameState.board`/`bench`를 만든다(vision 이름이 장부보다 우선).

## 판독 순서
1. `find_ally_bars()` — 보드/벤치 탐색 영역에서 초록 체력바를 찾는다. 적(빨강)·내 전략가(주황)는 걸리지 않는다.
2. `read_star()` — 바 왼쪽 배지. 배지는 픽셀 단위로 늘 같은 스프라이트라 **정확한 색 팔레트 개수**로 가른다.
3. `read_items()` — 바 아래 아이콘 칸 수를 **칸 경계선**으로 세고, 칸마다 아이템 템플릿 매칭.
4. `assign_slots()` — 바 → 육각칸 28개 + 벤치 9칸. 한 칸에 한 유닛(전역 최소비용 배정).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .regions import BAR_RISE, BOARD_COLS, FrameMapper, Profile, Rect

if TYPE_CHECKING:                                    # pragma: no cover
    from .icons import IconMatcher
    from .item_ids import ItemCatalog

log = logging.getLogger(__name__)

# --- 체력바 (1080p 실측: 만피 초록 64x4px, 짙은 남색 테두리 안) ------------------
BAR_W = 64 / 1080.0          # 만피 초록 길이(프레임 높이 비)
BAR_MIN_W = 18 / 1080.0      # 피가 깎여도 이만큼은 남아야 바로 본다
BAR_MAX_H = 7 / 1080.0
GREEN_HSV_LO = (40, 100, 80)
GREEN_HSV_HI = (85, 255, 255)
FRAME_DARK = 45             # 바 위 테두리(짙은 남색) 밝기 상한
FRAME_MIN = 0.75            # 바로 위 줄이 이 비율 이상 어두워야 체력바다
FRAME_OUTER_MAX = 0.6       # 그 한 줄 위는 밝아야 한다(그림자 속 초록 덩어리 배제)

# --- 성급 배지 (바 왼쪽) ---------------------------------------------------------
BADGE_DX1, BADGE_DX2 = -23 / 1080.0, -1 / 1080.0
BADGE_DY1, BADGE_DY2 = -7 / 1080.0, 13 / 1080.0
BADGE_OUTLINE = (35, 18, 2)     # 배지 테두리 색(BGR). 어느 캡처에서나 이 값 그대로다
BADGE_TOL = 3                   # 팔레트 색 허용 오차(채널별). 화면 크기가 달라도 칠한 면의 색은 그대로다
BADGE_OUTLINE_MIN = 45          # 1080p 기준 최소 테두리 픽셀 수(실측 1성 90 / 2성 146). 화면 크기에 비례해 줄인다
STAR_PALETTE: dict[int, tuple[tuple[int, int, int], ...]] = {
    # 배지는 2D 스프라이트라 픽셀 색이 캡처마다 **완전히 같다**. 1080p 캡처 7장 102개 배지에서
    # "배경이 달라도 값이 같은 픽셀"만 모아 뽑은 색이다(색 하나라도 배경에 우연히 나오기는 어렵다).
    1: ((48, 142, 239), (33, 97, 164), (34, 71, 119), (34, 71, 120), (71, 134, 195),
        (75, 140, 218), (94, 136, 189), (93, 135, 189), (59, 80, 126), (71, 93, 128),
        (80, 111, 159), (41, 101, 163), (48, 105, 163), (69, 140, 210), (97, 139, 189)),
    2: ((247, 231, 181), (247, 232, 181), (206, 203, 165), (163, 150, 114), (163, 149, 114),
        (148, 142, 112), (138, 131, 104), (138, 132, 104), (107, 101, 90), (101, 93, 70),
        (76, 67, 55), (76, 67, 56), (154, 148, 117), (154, 149, 118), (114, 108, 93),
        (200, 185, 144), (187, 172, 133), (217, 201, 157), (195, 180, 139), (206, 191, 148)),
    # 3성(금색) 배지는 아직 캡처가 없다 → 팔레트를 지어내지 않는다. 배지는 보이는데 1·2성 어느 쪽도 아니면
    # star=None(모름)으로 낸다. 장부(app.unit_merge)가 구매 기록으로 성급을 채운다 — 추측보다 안전하다.
}
STAR_MIN_HITS = 8               # 팔레트 색이 이만큼은 나와야 그 성급으로 본다
STAR_MARGIN = 2.0               # 1위가 2위의 이 배 이상이어야 한다
STAR_CONF = 0.92                # 배지가 픽셀 단위로 동일해 사실상 결정적이다(보수적으로 0.92)

# --- 장착 아이템 (바 아래) -------------------------------------------------------
ITEM_MAX = 3
ITEM_DX0 = -6 / 1080.0          # 아이콘 줄 왼쪽 끝 (바 초록 왼쪽 끝 기준)
ITEM_PITCH = 25 / 1080.0
ITEM_DY1, ITEM_DY2 = 10 / 1080.0, 35 / 1080.0
ITEM_SEP_DY1, ITEM_SEP_DY2 = 12 / 1080.0, 33 / 1080.0
ITEM_SEP_DARK = 26              # 칸 경계선 밝기 상한(회색조)
ITEM_SEP_FRAC = 0.35            # 경계선 자리 픽셀 중 이 비율 이상이 어두우면 칸이 있다
ITEM_TEMPLATE, ITEM_SEARCH = 28, 34   # 장착 아이콘 크기에 맞춘 템플릿/탐색 크기
ITEM_SCORE_MIN = 0.62
"""장착 아이콘 매칭 최소 점수. 1080p·16:10 캡처의 장착 아이콘 38칸 실측에서 **맞는 답이 0.664~0.948**,
같은 칸의 2위(다른 아이템)는 0.52 이하였다. 아이템 벤치(45px)보다 아이콘이 작아 점수 자체가 낮게 나온다."""
ITEM_MARGIN_MIN = 0.12          # 실측 최소 차 0.142

# --- 자리 배정 -------------------------------------------------------------------
SLOT_X_TOL = 0.055              # 칸 중심에서 이 이상(프레임 높이 비) 벗어나면 그 칸이 아니다 (1080p 약 60px)
SLOT_Y_TOL = 0.055
ROW_UNSURE_MARGIN = 0.012       # 1·2순위 줄의 비용 차가 이보다 작으면 줄이 애매하다고 기록한다
SLOT_CONF = 0.85                # 자리가 정해진 유닛의 기본 신뢰도
SLOT_CONF_LOOSE = 0.6           # 어느 칸에도 못 붙인 유닛(자리 None)


@dataclass(frozen=True)
class Bar:
    """찾은 아군 체력바 하나(프레임 픽셀 좌표)."""

    x: int          # 초록 왼쪽 끝
    y: int          # 초록 위쪽 끝
    w: int          # 남은 초록 길이
    full_w: int     # 만피 길이(이 프레임 크기 기준)

    @property
    def cx(self) -> float:
        """유닛 가로 중심 = 바 가운데(피가 깎여도 왼쪽 끝은 그대로다)."""
        return self.x + self.full_w / 2

    @property
    def hp_frac(self) -> float:
        return min(1.0, self.w / max(1, self.full_w))


@dataclass(frozen=True)
class UnitSlot:
    """보드/벤치 칸 하나의 판독 결과. `app.unit_merge.UnitSlotRead`/`SlotObs` 계약에 맞춘 속성 이름이다."""

    star: int | None = None
    items: tuple[str, ...] = ()
    hex: tuple[int, int] | None = None
    bench_slot: int | None = None
    confidence: float = SLOT_CONF
    unit_id: str | None = None
    """챔피언 ID. `vision.units.UnitNamer`가 붙인다(특성 패널 구속 + 모델 크롭 라이브러리). 모르면 None."""
    unit_conf: float = 0.0
    """이름 판정 신뢰도(0 = 이름 없음)."""
    name_source: str = "none"
    """이름 근거: forced(특성 구속만으로 결정) | traits(구속 + 닮음 배정) | library | duplicate | none."""
    item_count: int = 0
    """화면에 붙어 있던 아이템 칸 수(0~3). `len(items)`보다 크면 못 알아본 아이템이 있다는 뜻이다."""
    item_conf: float = 1.0
    star_conf: float = 0.0
    row_unsure: bool = False
    """줄(행)이 애매했다(체력바 높이는 챔피언 모델 키에 따라 다르다). 칸(열)은 영향받지 않는다."""
    anchor: tuple[float, float] = (0.0, 0.0)
    """체력바 가운데의 비율 좌표(게임 화면 기준). 프레임 사이 같은 유닛을 잇는 데 쓸 수 있다."""


@dataclass(frozen=True)
class BoardRead:
    """한 프레임의 보드 판독. `app.unit_merge.BoardRead` 프로토콜과 같은 모양이다."""

    board: tuple[UnitSlot, ...] = ()
    bench: tuple[UnitSlot, ...] = ()
    confidence: float = 0.0
    bars: int = 0
    """찾은 아군 체력바 수(보드 + 벤치). `len(board) + len(bench)`와 같다."""
    unresolved_items: int = 0
    """아이콘은 붙어 있는데 어떤 아이템인지 못 알아본 칸 수."""
    board_set: tuple[str, ...] = ()
    """특성 패널로 확정한 보드의 서로 다른 챔피언 집합(풀이가 하나일 때만). 비었으면 모름."""
    unplaced: tuple[str, ...] = ()
    """집합은 알지만 칸을 정하지 못한 보드 챔피언. 이름 없는 보드 칸 수와 같을 때만 채운다(자리 미상으로 쓰라는 뜻)."""
    trait_solutions: int = 0
    """특성 패널 풀이 개수(0 = 구속을 쓰지 못함)."""

    @property
    def count(self) -> int:
        return len(self.board) + len(self.bench)

    def all_item_ids(self) -> list[str]:
        """보드+벤치 전체 장착 아이템(중복 포함). advisor의 보유 아이템 계산에 쓴다."""
        return [i for u in (*self.board, *self.bench) for i in u.items]


# ---------------------------------------------------------------------------
# 1. 체력바
# ---------------------------------------------------------------------------


def _wide_runs(mask: np.ndarray, min_w: int) -> list[tuple[int, int, int]]:
    """마스크에서 길이 `min_w` 이상인 가로 연속 구간 [(row, x, length)]."""
    pad = np.zeros((mask.shape[0], 1), np.int8)
    d = np.diff(np.hstack([pad, (mask > 0).astype(np.int8), pad]), axis=1)
    rows, starts = np.nonzero(d == 1)
    _, ends = np.nonzero(d == -1)
    keep = (ends - starts) >= min_w
    return [(int(r), int(a), int(b - a)) for r, a, b in zip(rows[keep], starts[keep], ends[keep])]


def find_ally_bars(image: np.ndarray, m: FrameMapper, area: Rect) -> list[Bar]:
    """탐색 영역에서 **아군 초록 체력바**들을 찾는다(왼쪽 위부터).

    적 유닛은 빨강(`screen_mode.count_enemy_bars`), 내 전략가는 주황이라 걸리지 않는다.
    덩어리(connected component)가 아니라 **가로 구간(run)** 을 본다: 초록 유닛 모델·특성 이펙트가 바에 닿아도
    바만 정확히 4~5줄 높이로 남는다(덩어리로 보면 높이가 어긋나 바를 통째로 놓친다).
    """
    import cv2

    crop = m.crop(image, area)
    if crop.size == 0:
        return []
    _, _, _, box_h = m.box
    full_w = max(8, round(BAR_W * box_h))
    min_w = max(4, round(BAR_MIN_W * box_h))
    max_h = max(3, round(BAR_MAX_H * box_h))
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(GREEN_HSV_LO, np.uint8), np.array(GREEN_HSV_HI, np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((1, 9), np.uint8))
    groups: list[list[tuple[int, int, int]]] = []
    for r, x, w in sorted(_wide_runs(mask, min_w), key=lambda t: (t[1], t[0])):
        for g in groups:
            lr, lx, _ = g[-1]
            if abs(lx - x) <= 2 and 0 < r - lr <= 2:
                g.append((r, x, w))
                break
        else:
            groups.append([(r, x, w)])
    x0, y0, _, _ = m.to_px(area)
    out: list[Bar] = []
    for g in groups:
        if not 2 <= len(g) <= max_h:      # 바는 4~5줄(1080p). 초록 덩어리(유닛·이펙트)는 훨씬 두껍다
            continue
        bar = Bar(x=x0 + min(x for _, x, _ in g), y=y0 + g[0][0],
                  w=max(w for _, _, w in g), full_w=full_w)
        if has_frame(image, bar, box_h) and has_badge(image, bar, box_h):
            out.append(bar)
    return sorted(out, key=lambda b: (b.y, b.x))


# ---------------------------------------------------------------------------
# 2. 성급 배지
# ---------------------------------------------------------------------------


_PALETTE_ARR: dict[int, np.ndarray] = {k: np.array(v, np.int16) for k, v in STAR_PALETTE.items()}
_OUTLINE_ARR = np.array([BADGE_OUTLINE], np.int16)


def _palette_hits(crop: np.ndarray, palette: np.ndarray, tol: int = BADGE_TOL) -> int:
    """크롭에서 팔레트(N x 3 BGR) 중 하나와 오차 `tol` 안에서 같은 픽셀 수."""
    flat = crop.reshape(-1, 1, 3).astype(np.int16)
    return int((np.abs(flat - palette[None, :, :]).max(axis=2) <= tol).any(axis=1).sum())


def badge_crop(image: np.ndarray, bar: Bar, box_h: int) -> np.ndarray:
    """체력바 왼쪽 성급 배지 상자."""
    x1 = max(0, bar.x + round(BADGE_DX1 * box_h))
    x2 = max(0, bar.x + round(BADGE_DX2 * box_h))
    y1 = max(0, bar.y + round(BADGE_DY1 * box_h))
    y2 = max(0, bar.y + round(BADGE_DY2 * box_h))
    return image[y1:y2, x1:x2]


def _area_scale(box_h: int) -> float:
    """1080p 기준 픽셀 개수를 이 화면 크기로 환산하는 배율(면적비)."""
    return max(0.15, (box_h / 1080.0) ** 2)


def has_frame(image: np.ndarray, bar: Bar, box_h: int) -> bool:
    """체력바 위쪽 **짙은 테두리**가 있는가. 테두리는 2px이고 그 위는 밝다 →
    그림자 속 초록 덩어리(어두운 줄이 계속 이어진다)를 걸러낸다."""
    import cv2

    d1 = max(1, round(box_h / 1080))          # 테두리 안쪽 줄
    d2 = max(3, round(3 * box_h / 1080))      # 테두리 바깥(밝아야 하는) 줄. 테두리는 2px 고정이다
    if bar.y - d2 < 0:
        return False
    strip = image[bar.y - d2:bar.y - d1 + 1, bar.x:bar.x + bar.w]
    if strip.size == 0:
        return False
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) if strip.ndim == 3 else strip
    return float((gray[-1] < FRAME_DARK).mean()) >= FRAME_MIN and \
        float((gray[0] < FRAME_DARK).mean()) <= FRAME_OUTER_MAX


def has_badge(image: np.ndarray, bar: Bar, box_h: int) -> bool:
    """이 체력바가 **유닛의 것**인가 = 왼쪽에 성급 배지 테두리가 있는가.
    초록 유닛 모델·특성 이펙트가 만든 가짜 가로 구간을 걸러내는 결정적 조건이다."""
    crop = badge_crop(image, bar, box_h)
    if crop.size == 0:
        return False
    return _palette_hits(crop, _OUTLINE_ARR) >= BADGE_OUTLINE_MIN * _area_scale(box_h)


def read_star(image: np.ndarray, bar: Bar, box_h: int) -> tuple[int | None, float]:
    """체력바 왼쪽 성급 배지 → (성급, 신뢰도). 배지가 없거나 아는 성급이 아니면 (None, 0.0).

    배지는 2D 스프라이트라 색이 픽셀 단위로 고정이다 → 성급별 **정확한 색 팔레트 개수**로 가른다.
    3성(금색) 배지는 표본이 없어 팔레트가 없다. 그래서 3성 유닛은 None이 되고, 장부의 성급이 그대로 쓰인다.
    """
    crop = badge_crop(image, bar, box_h)
    if crop.size == 0 or _palette_hits(crop, _OUTLINE_ARR) < BADGE_OUTLINE_MIN * _area_scale(box_h):
        return None, 0.0
    hits = {star: _palette_hits(crop, pal) for star, pal in _PALETTE_ARR.items()}
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    top, second = ranked[0], (ranked[1] if len(ranked) > 1 else (0, 0))
    if top[1] < STAR_MIN_HITS * _area_scale(box_h) or top[1] < STAR_MARGIN * max(1, second[1]):
        log.debug("성급 배지를 가리지 못했습니다(3성 팔레트 미수집?): hits=%s", hits)
        return None, 0.0
    return top[0], STAR_CONF


# ---------------------------------------------------------------------------
# 3. 장착 아이템
# ---------------------------------------------------------------------------


def item_cell_count(image: np.ndarray, bar: Bar, box_h: int) -> int:
    """체력바 아래 붙어 있는 아이템 칸 수 0~3.

    칸은 1px 짙은 경계선으로 나뉜다 → 왼쪽부터 경계선이 이어지는 동안 센다(노란 사용 횟수 숫자가
    아래 테두리를 가려도 세로 경계선은 남는다).
    """
    import cv2

    left = bar.x + round(ITEM_DX0 * box_h)
    pitch = ITEM_PITCH * box_h
    y1 = bar.y + round(ITEM_SEP_DY1 * box_h)
    y2 = bar.y + round(ITEM_SEP_DY2 * box_h)
    if y2 > image.shape[0] or y1 < 0:
        return 0
    n = 0
    for i in range(ITEM_MAX):
        xb = left + round(pitch * (i + 1))
        col = image[y1:y2, max(0, xb - 1):xb + 1]
        if col.size == 0:
            break
        gray = cv2.cvtColor(col, cv2.COLOR_BGR2GRAY) if col.ndim == 3 else col
        if float((gray < ITEM_SEP_DARK).mean()) < ITEM_SEP_FRAC:
            break
        n = i + 1
    return n


def read_items(image: np.ndarray, bar: Bar, box_h: int, matcher: IconMatcher | None,
               catalog: ItemCatalog | None, *, score_min: float = ITEM_SCORE_MIN,
               margin_min: float = ITEM_MARGIN_MIN) -> tuple[tuple[str, ...], int, float]:
    """(알아본 아이템 ID들, 붙어 있던 칸 수, 최소 점수). 못 알아본 칸은 ID 목록에서 빠진다."""
    count = item_cell_count(image, bar, box_h)
    if count == 0 or matcher is None or not len(matcher):
        return (), count, 1.0
    import cv2

    left = bar.x + round(ITEM_DX0 * box_h)
    pitch = ITEM_PITCH * box_h
    y1, y2 = bar.y + round(ITEM_DY1 * box_h), bar.y + round(ITEM_DY2 * box_h)
    ids: list[str] = []
    scores: list[float] = []
    for i in range(count):
        x1, x2 = left + round(pitch * i), left + round(pitch * (i + 1))
        cell = image[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if cell.size == 0 or min(cell.shape[:2]) < 6:
            continue
        if cell.ndim == 3 and cell.shape[2] == 4:
            cell = cv2.cvtColor(cell, cv2.COLOR_BGRA2BGR)
        match = matcher.match(cell)
        if match is None or match.score < score_min or match.margin < margin_min:
            continue
        ids.append(catalog.rep(match.api_name) if catalog is not None else match.api_name)
        scores.append(match.score)
    return tuple(ids), count, min(scores) if scores else 1.0


# ---------------------------------------------------------------------------
# 4. 자리 배정
# ---------------------------------------------------------------------------


@dataclass
class _Cand:
    """배정 후보 = 칸 하나의 기준점(비율 좌표)."""

    hex: tuple[int, int] | None
    bench_slot: int | None
    cx: float
    cy: float


def slot_anchors(profile: Profile, on_bench: bool) -> list[_Cand]:
    """칸 → 그 칸에 선 유닛의 **체력바 가운데**가 오리라 기대되는 비율 좌표."""
    if on_bench:
        return [_Cand(None, i, r.cx, r.cy) for i, r in enumerate(profile.bench_cells)]
    out: list[_Cand] = []
    for k, r in enumerate(profile.board_hexes):
        out.append(_Cand((k // BOARD_COLS, k % BOARD_COLS), None, r.cx, r.cy - BAR_RISE))
    return out


def assign_slots(bars: list[Bar], m: FrameMapper, profile: Profile, on_bench: bool,
                 ) -> list[tuple[Bar, _Cand | None, bool]]:
    """바 → 칸. 한 칸에 한 유닛(비용이 작은 쌍부터 확정). 돌려주는 bool은 "줄이 애매했다"."""
    _, _, box_w, box_h = m.box
    ratio = box_w / max(1, box_h)
    cands = slot_anchors(profile, on_bench)
    pairs: list[tuple[float, int, int]] = []
    obs: list[tuple[float, float]] = []
    for bi, bar in enumerate(bars):
        bx, by = m.to_rel(bar.cx, bar.y)
        obs.append((bx, by))
        for ci, c in enumerate(cands):
            dx, dy = abs(bx - c.cx) * ratio, abs(by - c.cy)     # 가로도 프레임 높이 단위로 맞춘다
            if dx > SLOT_X_TOL or dy > SLOT_Y_TOL:
                continue
            pairs.append((dx * 2.0 + dy, bi, ci))
    pairs.sort()
    taken_b: dict[int, int] = {}
    taken_c: set[int] = set()
    for _, bi, ci in pairs:
        if bi in taken_b or ci in taken_c:
            continue
        taken_b[bi] = ci
        taken_c.add(ci)
    out: list[tuple[Bar, _Cand | None, bool]] = []
    for bi, bar in enumerate(bars):
        ci = taken_b.get(bi)
        if ci is None:
            out.append((bar, None, False))
            continue
        c = cands[ci]
        unsure = False
        if not on_bench:
            bx, by = obs[bi]
            same_col = sorted(abs(by - o.cy) for o in cands
                              if o.hex is not None and o.hex[1] == c.hex[1] and o.hex != c.hex)
            unsure = bool(same_col) and same_col[0] - abs(by - c.cy) < ROW_UNSURE_MARGIN
        out.append((bar, c, unsure))
    return out


# ---------------------------------------------------------------------------
# 판독 한 번
# ---------------------------------------------------------------------------


@dataclass
class BoardReader:
    """보드 판독기. `Recognizer`가 하나 들고 재사용한다(템플릿을 다시 읽지 않는다)."""

    matcher: IconMatcher | None = None
    catalog: ItemCatalog | None = None
    score_min: float = ITEM_SCORE_MIN
    margin_min: float = ITEM_MARGIN_MIN

    @classmethod
    def from_recognizer(cls, item_matcher: IconMatcher | None, catalog: ItemCatalog | None,
                        **kw) -> BoardReader:
        """상점 아이템 매처를 **장착 아이콘 크기로 다시 정규화**해서 쓴다(디스크 재로딩 없음)."""
        m = item_matcher.rescaled(ITEM_TEMPLATE, ITEM_SEARCH) if item_matcher and len(item_matcher) else None
        return cls(matcher=m, catalog=catalog, **kw)

    def _slots(self, image: np.ndarray, m: FrameMapper, profile: Profile, area: Rect, on_bench: bool,
               ) -> tuple[list[UnitSlot], int]:
        _, _, _, box_h = m.box
        bars = find_ally_bars(image, m, area)
        unresolved = 0
        out: list[UnitSlot] = []
        for bar, cand, unsure in assign_slots(bars, m, profile, on_bench):
            star, star_conf = read_star(image, bar, box_h)
            ids, count, item_conf = read_items(image, bar, box_h, self.matcher, self.catalog,
                                               score_min=self.score_min, margin_min=self.margin_min)
            unresolved += count - len(ids)
            ax, ay = m.to_rel(bar.cx, bar.y)
            conf = SLOT_CONF if cand is not None else SLOT_CONF_LOOSE
            out.append(UnitSlot(
                star=star, items=ids,
                hex=cand.hex if cand is not None else None,
                bench_slot=cand.bench_slot if cand is not None else None,
                confidence=round(conf, 3), item_count=count, item_conf=round(item_conf, 3),
                star_conf=star_conf, row_unsure=unsure, anchor=(round(ax, 5), round(ay, 5)),
            ))
        out.sort(key=lambda u: (u.hex is None and u.bench_slot is None, u.hex or (), u.bench_slot or 0))
        return out, unresolved

    def read(self, image: np.ndarray, m: FrameMapper, profile: Profile) -> BoardRead:
        """프레임 → 보드 판독. 유닛이 하나도 없으면 빈 판독(신뢰도는 그대로)이다."""
        board, u1 = self._slots(image, m, profile, profile.board_area, on_bench=False)
        bench, u2 = self._slots(image, m, profile, profile.bench_area, on_bench=True)
        slots = board + bench
        conf = min((u.confidence for u in slots), default=SLOT_CONF)
        return BoardRead(board=tuple(board), bench=tuple(bench), confidence=round(conf, 3),
                         bars=len(slots), unresolved_items=u1 + u2)
