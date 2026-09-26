"""벤치 체력바가 사라진 프레임에서 벤치를 잃지 않기 — 빈 칸 기준 그림 + 직전 판독(30 보고).

## 문제
`vision.board`는 유닛을 **초록 체력바**로 찾는다. 그런데 벤치 유닛의 체력바는 준비 단계 **끝**(타이머 1초 전후, 전투로 넘어가는
순간)부터 사라진다(라이브 3 `live3 2-3 준비 끝.png`: 벤치 6기, 체력바 0개 → 벤치 0칸으로 읽혔다). 화면 판별은 워터마크·배너·
벤치 체력바가 모두 없어 "준비(애매) 0.7"로 내므로 보드 묶음을 그대로 읽고, 앱에는 "벤치 비었음"이 간다.

## 방법(화면 픽셀만)
칸 그림 = 벤치 칸 i의 체력바 기준점 아래 상자(유닛 몸이 서는 곳, 1080p 100x100px)를 100x100으로 줄인 것.

1. **배우기**(체력바가 보이는 프레임 = 이번 프레임 벤치 체력바 >= 1): 체력바 유닛이 없는 칸의 그림을 그 칸의 **빈 칸 기준**으로
   모은다(칸마다 최대 `EMPTY_REFS`장, 새로운 그림만). 유닛이 있는 칸은 그 칸의 **직전 유닛**(그림 + 판독 칸)으로 둔다.
   전략가(꼬마 전설이) 이름표가 벤치 줄 근처에 있으면 그 칸은 배우지 않는다.
2. **체력바가 사라진 프레임**(벤치 체력바 0개)에서 칸마다(보드는 통째로 직전 판독 — 유닛이 전투 자리로 옮겨 가는 중이다):
   - 빈 칸 기준과 같다(새 윤곽 <= `EMPTY_MAX`) → 빈 칸
   - 직전 유닛 그림과 같다(양방향 새 윤곽 <= `SAME_MAX`) → 그 유닛 그대로(이름·성급 유지, `name_source="held"` = 직전 판독)
   - 빈 칸 기준과 확실히 다르다(모든 기준에 대해 새 윤곽 >= `OCCUPIED_MIN`) → 유닛 있음(이름·성급 모름)
   - 그 밖(기준이 없거나 애매): 직전 유닛이 있었으면 **이름 없이** 직전 판독으로 둔다(사라졌다는 증거가 없다), 없었으면 빈 칸.
   체력바가 사라진 것으로 보는 조건: 위 판정에서 유닛 있음/그대로가 1칸 이상이거나, 직전 판독 벤치가 2칸 이상이었다
   (한 프레임에 2기 이상을 팔 수는 없다).

"새 윤곽" = 비교 그림에 없는 강한 경계(소벨 크기 > 60, 기준 쪽 경계를 5x5로 넓힌 뒤)의 비율. 그림자·모래 반짝임은 경계가
약해 거의 0이다. 실측(라이브 2·3 모래 맵): 빈 칸 vs 빈 칸 0.000~0.039(옆 칸 큰 유닛 그림자 포함), 빈 칸 vs 유닛 0.163(가는
바루스)~0.331. 같은 유닛(가만히 서 있음) 0.002~0.027, 다른 유닛 0.086~0.092 · 같은 유닛 전투 시작 0.12까지 → 그대로 판정은
보수적으로 0.03.

맵이 바뀌면(보드 가운데 바닥 색 중앙값 Lab 거리 > `ARENA_MAX_DIST`) 기준을 쓰지 않는다(원정 전투·새 판).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from .regions import FrameMapper, Profile

log = logging.getLogger(__name__)

PATCH = 100                   # 칸 그림 크기(정사각)
PATCH_HW = 50 / 1080.0        # 칸 가운데에서 좌우(프레임 높이 비)
PATCH_DY1, PATCH_DY2 = 7 / 1080.0, 107 / 1080.0   # 체력바 기준 y 아래(몸이 서는 곳)
EDGE_T = 60.0
EMPTY_REFS = 4
EMPTY_MAX = 0.06              # 빈 칸 기준과 같다(실측 빈 칸 최대 0.039)
OCCUPIED_MIN = 0.10           # 빈 칸 기준과 확실히 다르다(실측 유닛 최소 0.163)
SAME_MAX = 0.03               # 직전 유닛과 같은 그림(실측 같은 유닛 최대 0.027, 다른 유닛 최소 0.086)
NOVEL_MIN = 0.02              # 빈 칸 기준을 새로 더하는 최소 차
ARENA_MAX_DIST = 12.0
HELD_CONF = 0.6               # 직전 판독 칸의 자리 신뢰도
OCC_CONF = 0.55               # 체력바 없이 그림으로만 찾은 칸
TACTICIAN_MIN_H, TACTICIAN_MAX_H = 9 / 1080.0, 18 / 1080.0   # 전략가 이름표 체력바 두께(유닛 4~5px)
TACTICIAN_MIN_W = 40 / 1080.0
TACTICIAN_BAND = (-80 / 1080.0, 40 / 1080.0)   # 벤치 기준 y에 대한 이름표 y 범위(이 안이면 벤치 줄 위의 전략가)
TACTICIAN_DX = 75 / 1080.0


def cell_patch(image: np.ndarray, m: FrameMapper, profile: Profile, i: int) -> np.ndarray | None:
    import cv2

    left, top, w, h = m.box
    cell = profile.bench_cells[i]
    cx, by = left + cell.cx * w, top + cell.cy * h
    x1, x2 = int(round(cx - PATCH_HW * h)), int(round(cx + PATCH_HW * h))
    y1, y2 = int(round(by + PATCH_DY1 * h)), int(round(by + PATCH_DY2 * h))
    if x1 < 0 or y1 < 0 or x2 > image.shape[1] or y2 > image.shape[0] or x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return cv2.resize(image[y1:y2, x1:x2, :3], (PATCH, PATCH), interpolation=cv2.INTER_AREA)


def edge_map(patch: np.ndarray) -> np.ndarray:
    import cv2

    g = cv2.GaussianBlur(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0)
    return np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)) > EDGE_T


def new_edges(ref: np.ndarray, cur: np.ndarray) -> float:
    """`cur`에 있고 `ref`(5x5로 넓힌 경계)에는 없는 강한 경계 픽셀의 비율."""
    import cv2

    er = cv2.dilate(edge_map(ref).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    return float((edge_map(cur) & ~er).mean())


def arena_color(image: np.ndarray, m: FrameMapper) -> np.ndarray:
    """보드 가운데 바닥 색(중앙값 Lab). 맵이 바뀌었는지만 본다."""
    import cv2

    left, top, w, h = m.box
    p = image[top + int(0.22 * h):top + int(0.60 * h), left + int(0.28 * w):left + int(0.72 * w), :3]
    if p.size == 0:
        return np.zeros(3)
    return np.median(cv2.cvtColor(p[::4, ::4], cv2.COLOR_BGR2LAB).reshape(-1, 3), axis=0).astype(np.float64)


def tactician_cells(image: np.ndarray, m: FrameMapper, profile: Profile) -> set[int]:
    """벤치 줄 근처에 있는 **전략가 이름표 체력바**(두께 9~18px 초록 막대) 아래 칸들. 전략가는 유닛이 아니다."""
    import cv2

    left, top, w, h = m.box
    by = top + profile.bench_cells[0].cy * h
    y1, y2 = int(by + TACTICIAN_BAND[0] * h), int(by + TACTICIAN_BAND[1] * h)
    x1, x2 = left, left + w
    crop = image[max(0, y1):max(0, y2), x1:x2, :3]
    if crop.size == 0:
        return set()
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array((40, 100, 80), np.uint8), np.array((85, 255, 255), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out: set[int] = set()
    for k in range(1, n):
        bx, _, bw, bh = stats[k][:4]
        if TACTICIAN_MIN_H * h <= bh <= TACTICIAN_MAX_H * h and bw >= TACTICIAN_MIN_W * h:
            cx = x1 + bx + bw / 2
            for i, cell in enumerate(profile.bench_cells):
                if abs(left + cell.cx * w - cx) <= TACTICIAN_DX * h + bw / 2:
                    out.add(i)
    return out


@dataclass
class _Cell:
    empty: list[np.ndarray] = field(default_factory=list)
    unit_patch: np.ndarray | None = None
    unit_slot: Any = None


@dataclass
class BenchMemory:
    """`Recognizer`가 하나 들고 보드 판독마다 `apply()`를 부른다. 새 판이면 `reset()`."""

    cells: dict[int, _Cell] = field(default_factory=dict)
    arena: np.ndarray | None = None
    last_bench_n: int = 0
    last_board: tuple = ()
    """벤치 체력바가 보이던 마지막 프레임의 보드 칸들. 체력바가 사라진 전환 프레임에서는 보드 자리도 뜻이 없다(유닛이 전투
    자리로 옮겨 가는 중, 라이브 3 2-3 준비 끝: 보드 5기 중 2기만, 한 칸은 자리 None) → 이것을 직전 판독으로 낸다."""
    held_frames: int = 0
    """직전 판독으로 벤치를 채운 프레임 수(진단용)."""

    def reset(self) -> None:
        self.cells.clear()
        self.arena = None
        self.last_bench_n = 0
        self.last_board = ()

    def apply(self, image: np.ndarray, m: FrameMapper, profile: Profile, read: Any) -> Any:
        """판독 → (벤치 체력바가 보이면 배우고 그대로, 사라졌으면 칸 그림으로 채운) 판독."""
        try:
            arena = arena_color(image, m)
            same_arena = self.arena is not None and float(np.linalg.norm(arena - self.arena)) <= ARENA_MAX_DIST
            if read.bench:
                if not same_arena:
                    self.cells.clear()
                self.arena = arena
                self._learn(image, m, profile, read)
                self.last_bench_n = len(read.bench)
                self.last_board = tuple(read.board)
                return read
            if not same_arena and self.arena is not None:
                return read                      # 다른 맵(원정 등): 기준을 쓸 수 없다
            out = self._fill(image, m, profile, read)
            if not out.bench:
                self.last_bench_n = 0
            return out
        except Exception:                        # 보조 기능 — 판독을 막지 않는다
            log.exception("벤치 기억 실패")
            return read

    # ------------------------------------------------------------------
    def _learn(self, image: np.ndarray, m: FrameMapper, profile: Profile, read: Any) -> None:
        by_slot = {u.bench_slot: u for u in read.bench if u.bench_slot is not None}
        skip = tactician_cells(image, m, profile)
        for i in range(len(profile.bench_cells)):
            if i in skip:
                continue
            p = cell_patch(image, m, profile, i)
            if p is None:
                continue
            c = self.cells.setdefault(i, _Cell())
            u = by_slot.get(i)
            if u is not None:
                c.unit_patch, c.unit_slot = p, u
                continue
            c.unit_patch = c.unit_slot = None
            if all(new_edges(r, p) >= NOVEL_MIN or new_edges(p, r) >= NOVEL_MIN for r in c.empty):
                c.empty.append(p)
                del c.empty[:-EMPTY_REFS]

    def _fill(self, image: np.ndarray, m: FrameMapper, profile: Profile, read: Any) -> Any:
        from .board import UnitSlot

        skip = tactician_cells(image, m, profile)
        bench: list[Any] = []
        evidence = 0
        for i in range(len(profile.bench_cells)):
            c = self.cells.get(i)
            p = cell_patch(image, m, profile, i)
            if c is None or p is None:
                continue
            prev = c.unit_slot
            if c.empty and min(new_edges(r, p) for r in c.empty) <= EMPTY_MAX:
                continue                                         # 빈 칸
            if prev is not None and c.unit_patch is not None and \
                    max(new_edges(c.unit_patch, p), new_edges(p, c.unit_patch)) <= SAME_MAX:
                bench.append(replace(prev, confidence=min(prev.confidence, HELD_CONF), name_source="held"))
                evidence += 1
                continue
            if i not in skip and c.empty and min(new_edges(r, p) for r in c.empty) >= OCCUPIED_MIN:
                cell = profile.bench_cells[i]
                if prev is not None:     # 다른 그림 = 다른 유닛일 수 있다 → 이름·성급·아이템 없이
                    bench.append(replace(prev, unit_id=None, unit_conf=0.0, name_source="none", corroborated=None,
                                         star=None, star_conf=0.0, items=(), item_count=0, confidence=OCC_CONF))
                else:
                    bench.append(UnitSlot(bench_slot=i, confidence=OCC_CONF,
                                          anchor=(round(cell.cx, 5), round(cell.cy, 5))))
                evidence += 1
                continue
            if prev is not None:         # 기준 없음/애매: 사라졌다는 증거가 없다 → 이름 없이 직전 판독
                bench.append(replace(prev, unit_id=None, unit_conf=0.0, name_source="held", corroborated=None,
                                     confidence=HELD_CONF))
        if not evidence and self.last_bench_n < 2:
            return read
        self.held_frames += 1
        bench.sort(key=lambda u: u.bench_slot if u.bench_slot is not None else 99)
        board = tuple(replace(u, confidence=min(u.confidence, HELD_CONF), name_source="held") for u in self.last_board)
        slots = (*board, *bench)
        return replace(read, board=board, bench=tuple(bench), bench_held=True, unplaced=(), missed_board=0,
                       confidence=round(min((u.confidence for u in slots), default=read.confidence), 3))
