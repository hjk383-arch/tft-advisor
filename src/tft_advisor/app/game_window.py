"""게임 창 자동 찾기 — OS 창 목록에서 TFT 게임 창의 위치·크기를 읽어 캡처 영역(모니터 + content_box)을 맞춘다.

왜 필요한가: 게임을 **창 모드**(제목 표시줄 있음)로 띄우면 모니터 한 대 전체를 찍은 프레임 안에 게임이 일부만 있다.
픽셀로 게임 영역을 찾는 기존 감지(`setup.detect`)는 바탕화면이 검지 않으면 경계를 못 찾아 모니터 전체를 게임으로
잡는다(실제 사례: 3440x1440 모니터의 (760,156)에 1920x1080 창 → "3440x1432, 21:9"로 저장돼 ROI가 전부 어긋남).
창의 클라이언트 영역은 OS가 정확히 알려 준다.

순서
1. 창 목록(Windows: user32 `EnumWindows` + `GetWindowTextW`/`GetClassNameW`/`GetClientRect`/`ClientToScreen`,
   스레드 DPI 문맥 = per-monitor v2 → 물리 픽셀, mss 좌표와 같다).
2. 게임 창 고르기(`select_game_window`): 우리 프로세스 창·"TFT Advisor"·League 클라이언트(`RCLIENT`,
   제목 "League of Legends")는 뺀다. 제목이 "TFT"/"Teamfight Tactics"/"League of Legends (TM) Client"이거나
   클래스가 `RiotWindowClass`면 후보. 최소화되지 않은 것 > 게임 클래스(`UnrealWindow`/`RiotWindowClass`) > 넓은 것.
3. 창이 들어 있는 모니터(겹치는 넓이가 가장 큰 모니터)와 그 모니터에 대한 비율 = content_box.
4. **검증**: 가림(창 위 격자 점마다 `WindowFromPoint` → 최상위 창이 게임/우리 창인가), 캡처가 검은가,
   스테이지 막대 픽셀 점수, (인식기가 있으면) 스테이지 글자 OCR. 최소화·가림·검은 캡처면 **저장하지 않는다**.
5. 적용: 실행 중 루프에는 캡처 스레드에서 갈아 끼우고(`LiveLoop.apply_screen`), `settings.toml`
   (`setup.save_settings`, 주석 보존 + .bak) + `_state/setup.json`(`setup.mark_setup_done`)에 저장한다.

다른 OS는 "지원 안 함"을 돌려주고 호출자는 기존 픽셀 감지로 내려간다.

안전(CLAUDE.md 고정 제약): **창의 위치·크기·제목만** 읽는다. 게임 메모리 읽기·프로세스 후킹·입력 주입은 하지 않는다
(`OpenProcess`·`ReadProcessMemory`·`SendInput`·훅 API를 쓰지 않는다). 창 목록은 작업 관리자가 보는 것과 같은 공개 정보다.

테스트: 환경변수 `TFT_ADVISOR_WINDOW_DETECT=0`이면 실제 창 목록을 읽지 않는다(tests/conftest.py가 켠다).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..config import Settings
from .setup import (
    MonitorGrabber, MonitorInfo, MonitorProbe, SetupChoice, SetupDetection, choice_from_detection, is_black_frame,
    mark_setup_done, permission_help, resolve_state_dir, save_settings,
)

log = logging.getLogger(__name__)

DISABLE_ENV = "TFT_ADVISOR_WINDOW_DETECT"
"""이 환경변수가 "0"이면 실제 창 목록을 읽지 않는다(테스트가 사용자의 진짜 게임 창을 보지 않게)."""

GAME_TITLES = frozenset({"tft", "teamfight tactics", "league of legends (tm) client"})
"""게임 창 제목(앞뒤 공백 제거, 대소문자 무시). 실제 TFT 창 제목은 'TFT  '(끝에 공백 2개)다."""
GAME_CLASSES = frozenset({"UnrealWindow", "RiotWindowClass"})
"""게임 창 클래스. 2026-09 TFT 클라이언트는 UnrealWindow, 옛 LoL 게임 클라이언트는 RiotWindowClass."""
RIOT_GAME_CLASS = "RiotWindowClass"
CLIENT_CLASSES = frozenset({"RCLIENT"})
CLIENT_TITLES = frozenset({"league of legends", "riot client"})
"""League 클라이언트(로비) 창 — 게임 창이 아니다(1280x720)."""
OWN_TITLE_PREFIX = "tft advisor"

MIN_GAME_W, MIN_GAME_H = 640, 360
OCCLUDED_FRACTION = 0.5
"""격자 점 중 이 비율 이상이 다른 창이면 '가려짐'으로 보고 저장하지 않는다."""
PARTLY_OCCLUDED = 0.0
GRID = (6, 4)
SAME_PX = 1
"""설정의 content_box와 창 위치가 이 픽셀 이하로만 다르면 같은 것으로 본다(반올림 오차)."""
FOLLOW_RETRY_S = 30.0
"""자동 따라가기가 실패한(가려짐 등) 같은 창 위치를 다시 시도하기까지 기다리는 시간."""
FOLLOW_CONFIRM_S = 0.5
"""창 위치가 바뀐 것을 본 뒤 이만큼 지나 한 번 더 같은 위치면 적용한다(끌어 옮기는 중간 위치를 저장하지 않게)."""

Rect = tuple[int, int, int, int]
"""(left, top, width, height) 화면 물리 픽셀."""

Status = Literal["ok", "not_supported", "not_found", "minimized", "occluded", "black", "too_small",
                 "off_screen", "error"]


# ---------------------------------------------------------------------------
# 창 정보 / 고르기 (순수 로직 — 테스트는 가짜 목록으로)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WinInfo:
    """최상위 창 하나. `client`는 클라이언트 영역의 화면 좌표(제목 표시줄·테두리 제외)."""

    hwnd: int
    title: str
    cls: str
    pid: int
    client: Rect
    visible: bool = True
    iconic: bool = False
    cloaked: bool = False

    @property
    def area(self) -> int:
        return max(0, self.client[2]) * max(0, self.client[3])

    def describe(self) -> str:
        left, top, w, h = self.client
        return f"'{self.title.strip()}' ({self.cls}) {w}x{h} @ ({left},{top})"


def _norm(title: str) -> str:
    return " ".join(title.split()).casefold()


def is_excluded(w: WinInfo, own_pid: int | None) -> bool:
    """우리 창·League 클라이언트·보이지 않는 창은 게임 창이 아니다."""
    title = _norm(w.title)
    if own_pid is not None and w.pid == own_pid:
        return True
    if title.startswith(OWN_TITLE_PREFIX):
        return True
    if w.cls in CLIENT_CLASSES or title in CLIENT_TITLES:
        return True
    return not w.visible or w.cloaked


def is_game_window(w: WinInfo) -> bool:
    """제목이 게임 제목이거나 Riot 게임 창 클래스인가(UnrealWindow는 다른 게임도 쓰므로 제목이 맞아야 한다)."""
    return _norm(w.title) in GAME_TITLES or w.cls == RIOT_GAME_CLASS


def select_game_window(windows: Sequence[WinInfo], own_pid: int | None = None) -> WinInfo | None:
    """후보 중 게임 창 하나. 최소화되지 않은 것 > 게임 클래스 > 넓은 클라이언트 영역."""
    cands = [w for w in windows if not is_excluded(w, own_pid) and is_game_window(w)]
    if not cands:
        return None
    return max(cands, key=lambda w: (not w.iconic, w.cls in GAME_CLASSES, w.area))


# ---------------------------------------------------------------------------
# 좌표 계산 (순수 로직)
# ---------------------------------------------------------------------------


def _overlap(a: Rect, b: Rect) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return max(0, w) * max(0, h)


def monitor_for_rect(rect: Rect, monitors: Sequence[MonitorInfo]) -> MonitorInfo | None:
    """창과 겹치는 넓이가 가장 큰 모니터. 어느 모니터와도 겹치지 않으면 None."""
    best, best_area = None, 0
    for m in monitors:
        area = _overlap(rect, (m.left, m.top, m.width, m.height))
        if area > best_area:
            best, best_area = m, area
    return best


def clip_to_monitor(rect: Rect, mon: MonitorInfo) -> tuple[Rect, bool]:
    """창 영역을 모니터 안으로 자른다. 반환: (모니터 기준 (left, top, w, h), 잘렸는가)."""
    left, top, w, h = rect
    x1 = max(left, mon.left)
    y1 = max(top, mon.top)
    x2 = min(left + w, mon.left + mon.width)
    y2 = min(top + h, mon.top + mon.height)
    clipped = (x1, y1, x2, y2) != (left, top, left + w, top + h)
    return (x1 - mon.left, y1 - mon.top, max(0, x2 - x1), max(0, y2 - y1)), clipped


def content_box_for(local: Rect, mon: MonitorInfo) -> tuple[float, float, float, float] | None:
    """모니터 기준 픽셀 영역 → content_box 비율(소수 6자리). 모니터 전체면 None(= 프레임 전체)."""
    left, top, w, h = local
    if (left, top, w, h) == (0, 0, mon.width, mon.height):
        return None
    return (round(left / mon.width, 6), round(top / mon.height, 6),
            round((left + w) / mon.width, 6), round((top + h) / mon.height, 6))


def same_screen(settings: Settings, choice: SetupChoice, mon: MonitorInfo) -> bool:
    """지금 설정의 화면 값(모니터·해상도·비율·프로파일·content_box)이 이 선택과 같은가(content_box는 ±SAME_PX)."""
    v = settings.vision
    if settings.capture.monitor != choice.monitor or v.resolution != choice.resolution:
        return False
    if v.aspect != choice.aspect or v.profile != choice.profile:
        return False
    have = v.content_box or (0.0, 0.0, 1.0, 1.0)
    want = choice.content_box or (0.0, 0.0, 1.0, 1.0)
    scale = (mon.width, mon.height, mon.width, mon.height)
    return all(abs(a - b) * k <= SAME_PX for a, b, k in zip(have, want, scale))


# ---------------------------------------------------------------------------
# Windows 창 목록 (ctypes user32 — 창 위치·제목만)
# ---------------------------------------------------------------------------


def supported() -> tuple[bool, str]:
    """창 목록을 읽을 수 있는가. (가능, 이유)."""
    if os.environ.get(DISABLE_ENV, "").strip() == "0":
        return False, f"환경변수 {DISABLE_ENV}=0 — 게임 창 찾기를 껐습니다"
    if not sys.platform.startswith("win"):
        return False, "게임 창 자동 찾기는 Windows에서만 됩니다"
    return True, ""


class _DpiScope:
    """이 스레드만 per-monitor DPI v2 문맥으로 바꿨다가 되돌린다(프로세스 설정은 건드리지 않는다)."""

    PER_MONITOR_V2 = -4

    def __init__(self, user32: Any) -> None:
        self.user32 = user32
        self.old = None

    def __enter__(self) -> _DpiScope:
        import ctypes

        fn = getattr(self.user32, "SetThreadDpiAwarenessContext", None)
        if fn is not None:
            try:
                fn.restype = ctypes.c_void_p
                fn.argtypes = [ctypes.c_void_p]
                self.old = fn(ctypes.c_void_p(self.PER_MONITOR_V2))
            except Exception:   # noqa: BLE001 — 옛 Windows: 프로세스 DPI 설정(mss가 per-monitor로 둔다)을 쓴다
                log.debug("SetThreadDpiAwarenessContext 실패", exc_info=True)
        return self

    def __exit__(self, *exc: object) -> None:
        import ctypes

        if self.old:
            try:
                self.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(self.old))
            except Exception:   # noqa: BLE001
                log.debug("DPI 문맥 복원 실패", exc_info=True)


def _user32():
    import ctypes

    return ctypes.windll.user32


def _is_cloaked(hwnd: int) -> bool:
    import ctypes
    import ctypes.wintypes as W

    try:
        dwm = ctypes.windll.dwmapi
        val = W.DWORD(0)
        ok = dwm.DwmGetWindowAttribute(W.HWND(hwnd), 14, ctypes.byref(val), ctypes.sizeof(val))   # DWMWA_CLOAKED
        return ok == 0 and val.value != 0
    except Exception:   # noqa: BLE001
        return False


def list_windows() -> list[WinInfo]:
    """보이는 최상위 창 목록(Windows). 지원하지 않으면 빈 목록."""
    ok, _ = supported()
    if not ok:
        return []
    import ctypes
    import ctypes.wintypes as W

    user32 = _user32()
    out: list[WinInfo] = []
    proto = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            rc = W.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            pt = W.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            pid = W.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = int(hwnd or 0)
            out.append(WinInfo(hwnd=h, title=buf.value, cls=cls.value, pid=int(pid.value),
                               client=(int(pt.x), int(pt.y), int(rc.right - rc.left), int(rc.bottom - rc.top)),
                               visible=True, iconic=bool(user32.IsIconic(hwnd)), cloaked=_is_cloaked(h)))
        except Exception:   # noqa: BLE001 — 창 하나 읽기 실패가 목록 전체를 막지 않는다
            log.debug("창 정보 읽기 실패", exc_info=True)
        return True

    with _DpiScope(user32):
        user32.EnumWindows(proto(cb), 0)
    return out


def point_owner(x: int, y: int) -> tuple[int, int]:
    """화면 점 (x, y)에 보이는 최상위 창 → (hwnd, pid). 클릭 통과(투명) 창은 건너뛴다(OS 판정)."""
    import ctypes
    import ctypes.wintypes as W

    user32 = _user32()
    user32.WindowFromPoint.argtypes = [W.POINT]
    user32.WindowFromPoint.restype = W.HWND
    user32.GetAncestor.argtypes = [W.HWND, ctypes.c_uint]
    user32.GetAncestor.restype = W.HWND
    with _DpiScope(user32):
        h = user32.WindowFromPoint(W.POINT(int(x), int(y)))
        root = user32.GetAncestor(h, 2) if h else None   # GA_ROOT
    pid = W.DWORD()
    if root:
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    return int(root or 0), int(pid.value)


def occlusion(rect: Rect, game: WinInfo, own_pid: int | None,
              owner: Callable[[int, int], tuple[int, int]], grid: tuple[int, int] = GRID) -> float:
    """창 위 격자 점 중 다른 창(게임도 우리 앱도 아닌 창)이 덮고 있는 비율 0~1."""
    left, top, w, h = rect
    cols, rows = grid
    total = covered = 0
    for i in range(cols):
        for j in range(rows):
            x = left + int(w * (i + 0.5) / cols)
            y = top + int(h * (j + 0.5) / rows)
            try:
                hwnd, pid = owner(x, y)
            except Exception:   # noqa: BLE001
                continue
            total += 1
            if hwnd != game.hwnd and pid != game.pid and (own_pid is None or pid != own_pid):
                covered += 1
    return covered / total if total else 0.0


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------


@dataclass
class WindowDetection:
    """게임 창 찾기 결과. `ok`일 때만 적용·저장한다."""

    status: Status
    message: str
    window: WinInfo | None = None
    monitor: MonitorInfo | None = None
    local: Rect | None = None                    # 모니터 기준 게임 영역 (left, top, w, h)
    content_box: tuple[float, float, float, float] | None = None
    confirmed: bool = False                      # 스테이지 글자(OCR)로 TFT 화면을 확인했는가
    pixel_score: float = 0.0
    occluded: float = 0.0
    detection: SetupDetection | None = None      # 설정 화면·저장이 그대로 쓰는 형식
    warnings: list[str] = field(default_factory=list)
    applied: bool = False                        # 실행 중 루프에 적용했는가
    saved: Path | None = None                    # 저장한 settings.toml
    unchanged: bool = False                      # 이미 같은 설정이었다

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def resolution(self) -> str | None:
        return f"{self.local[2]}x{self.local[3]}" if self.local else None

    def lines(self) -> list[str]:
        out = [self.message, *self.warnings]
        if self.ok and self.unchanged:
            out.append("설정이 이미 이 위치와 같습니다.")
        elif self.ok and self.saved is not None:
            out.append("설정 파일에 저장했습니다.")
        return out

    def text(self) -> str:
        return "\n".join(line for line in self.lines() if line)


def found_message(win: WinInfo, mon: MonitorInfo, local: Rect) -> str:
    left, top, w, h = local
    sx, sy = win.client[0], win.client[1]
    return f"게임 창을 찾았습니다: 모니터 {mon.number}, {w}x{h}, 위치 ({sx},{sy})"


def _geometry_detection(monitors: Sequence[MonitorInfo], mon: MonitorInfo, local: Rect,
                        box: tuple[float, float, float, float] | None) -> SetupDetection:
    """창 위치 → 설정 화면이 쓰는 `SetupDetection`(모든 모니터를 목록에 두고 게임 모니터를 고른다)."""
    from ..vision.regions import MEASURED_PROFILES, aspect_name, profile_for_frame

    det = SetupDetection(probes=[MonitorProbe(info=m) for m in monitors])
    det.chosen = next(i for i, m in enumerate(monitors) if m.number == mon.number)
    det.frame_size = mon.size
    det.content_box = box
    det.content_px = None if box is None else local
    det.game_size = (local[2], local[3])
    det.aspect = aspect_name(local[2] / local[3])
    det.measured = det.aspect in MEASURED_PROFILES
    try:
        det.profile_name = profile_for_frame(local[2], local[3]).name
    except ValueError:
        det.profile_name = ""
    det.notes.append("게임 창 위치를 OS 창 목록에서 읽었습니다(창 모드 · 제목 표시줄과 테두리는 뺐습니다)")
    return det


def find_game_window(*, grabber: MonitorGrabber | None = None, scorer: Callable[[Any], float] | None = None,
                     windows: Callable[[], list[WinInfo]] | None = None, own_pid: int | None = None,
                     owner: Callable[[int, int], tuple[int, int]] | None = None,
                     check_support: bool = True) -> WindowDetection:
    """게임 창을 찾고 픽셀로 검증한다. **예외를 내지 않는다**(실패는 status로).

    `windows`/`owner`를 넘기면(테스트) 실제 창 목록 대신 그것을 쓴다 — 그때는 플랫폼 검사도 건너뛴다.
    `scorer`: 인식기의 `screen_score`(스테이지 OCR). 없으면 픽셀 점수만 본다.
    """
    if windows is None and check_support:
        ok, why = supported()
        if not ok:
            return WindowDetection("not_supported", why)
    own_pid = os.getpid() if own_pid is None else own_pid
    own = grabber is None
    grabber = grabber or MonitorGrabber()
    try:
        return _find(grabber, scorer, windows or list_windows, own_pid, owner or point_owner)
    except Exception as e:   # noqa: BLE001 — 창 찾기 실패가 앱·설정 화면을 죽이지 않는다
        log.warning("게임 창 찾기 실패", exc_info=True)
        return WindowDetection("error", f"게임 창을 찾다가 오류가 났습니다: {type(e).__name__}: {e}")
    finally:
        if own:
            grabber.close()


def _find(grabber: MonitorGrabber, scorer, windows, own_pid: int, owner) -> WindowDetection:
    wins = windows()
    win = select_game_window(wins, own_pid)
    if win is None:
        return WindowDetection("not_found", "게임 창을 찾지 못했습니다 — TFT 게임이 실행 중인지 확인해 주세요"
                                            "(로비 클라이언트만 떠 있으면 게임 창이 없습니다).")
    if win.iconic:
        return WindowDetection("minimized", "게임 창이 최소화되어 있습니다 — 게임 창을 화면에 띄운 뒤 다시 눌러 주세요.",
                               window=win)
    _, _, w, h = win.client
    if w < MIN_GAME_W or h < MIN_GAME_H:
        return WindowDetection("too_small", f"게임 창이 너무 작습니다({w}x{h}) — {MIN_GAME_W}x{MIN_GAME_H} 이상으로 "
                                            "키운 뒤 다시 눌러 주세요.", window=win)
    monitors = grabber.monitors()
    mon = monitor_for_rect(win.client, monitors)
    if mon is None:
        return WindowDetection("off_screen", "게임 창이 어느 모니터에도 보이지 않습니다 — 창을 화면 안으로 옮겨 주세요.",
                               window=win)
    local, clipped = clip_to_monitor(win.client, mon)
    box = content_box_for(local, mon)
    res = WindowDetection("ok", found_message(win, mon, local), window=win, monitor=mon, local=local,
                          content_box=box)
    if clipped:
        res.warnings.append(f"게임 창 일부가 {mon.number}번 모니터 밖에 있습니다 — 보이는 부분만 씁니다"
                            "(창을 모니터 안으로 옮기면 정확해집니다).")
    try:
        from ..vision.regions import profile_for_frame

        profile_for_frame(local[2], local[3])
    except ValueError:
        res.status = "too_small"
        res.message = (f"게임 영역 비율({local[2]}x{local[3]})에 맞는 인식 배치가 없습니다 — "
                       "게임 창을 16:9 또는 16:10 크기로 맞춰 주세요.")
        return res

    res.occluded = occlusion(win.client, win, own_pid, owner)
    if res.occluded >= OCCLUDED_FRACTION:
        res.status = "occluded"
        res.message = (f"게임 창이 다른 창에 가려져 있습니다(약 {res.occluded:.0%}) — 게임 창을 앞으로 가져온 뒤 "
                       "다시 눌러 주세요.")
        return res
    if res.occluded > PARTLY_OCCLUDED:
        res.warnings.append(f"게임 창 일부(약 {res.occluded:.0%})가 다른 창에 가려져 있습니다 — 가려진 곳은 인식되지 않습니다.")

    frame = grabber.grab(mon)
    fx, fy, fw, fh = local
    fh_img, fw_img = frame.shape[:2]
    if (fw_img, fh_img) != mon.size:   # 배율 등으로 캡처 크기가 다르면 비율로 옮긴다
        kx, ky = fw_img / mon.width, fh_img / mon.height
        fx, fy, fw, fh = round(fx * kx), round(fy * ky), round(fw * kx), round(fh * ky)
    crop = frame[fy:fy + fh, fx:fx + fw]
    if is_black_frame(crop):
        res.status = "black"
        res.message = "게임 창 자리가 검게 잡힙니다 — 캡처할 수 없는 상태입니다.\n" + permission_help()
        return res
    from ..vision.capture import default_screen_score

    try:
        res.pixel_score = float(default_screen_score(crop))
    except Exception:   # noqa: BLE001
        log.debug("픽셀 점수 실패", exc_info=True)
    score = 0.0
    if scorer is not None:
        try:
            score = float(scorer(crop))
        except Exception:   # noqa: BLE001
            log.debug("스테이지 OCR 채점 실패", exc_info=True)
    from .setup import GAME_FOUND_SCORE

    res.confirmed = score >= GAME_FOUND_SCORE
    det = _geometry_detection(monitors, mon, local, box)
    det.scorer_kind = "ocr" if scorer is not None else "pixel"
    det.window_note = res.message
    det.game_found = res.confirmed
    det.probes[det.chosen].frame = frame
    det.probes[det.chosen].score = max(score, res.pixel_score)
    det.warnings.extend(res.warnings)
    if not res.confirmed:
        note = ("게임 화면(스테이지 글자)은 아직 확인하지 못했습니다 — 로비·로딩 화면일 수 있습니다. "
                "위치는 게임 창 기준이라 그대로 씁니다.")
        res.warnings.append(note)
        det.notes.append(note)
    res.detection = det
    return res


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------


def screen_choice(res: WindowDetection, settings: Settings) -> SetupChoice:
    """화면 관련 값만 채운 선택값(나머지는 지금 설정 그대로)."""
    assert res.detection is not None
    choice = choice_from_detection(res.detection, settings)
    choice.profile = "auto"      # 창 크기가 바뀌었을 수 있다 — 고정 프로파일은 풀고 비율로 고른다
    return choice


def screen_updates(choice: SetupChoice) -> dict[str, dict[str, object]]:
    """`settings.toml`에 쓸 화면 키만([advisor]·[overlay]·[ui]는 건드리지 않는다)."""
    full = choice.updates()
    vision = {k: full["vision"][k] for k in ("resolution", "aspect", "profile", "content_box")}
    return {"capture": {"monitor": choice.monitor}, "vision": vision}


def settings_with(settings: Settings, choice: SetupChoice) -> Settings:
    """메모리 설정에 화면 값만 얹는다(검증 포함)."""
    from .setup import apply_updates

    return apply_updates(settings, screen_updates(choice))


def persist(res: WindowDetection, settings: Settings, *, config_dir: Path | None = None,
            state_dir: Path | None = None, saver: Callable[..., Any] | None = None) -> Settings:
    """결과를 `settings.toml`(화면 키만) + `_state/setup.json`에 저장하고 새 메모리 설정을 돌려준다."""
    choice = screen_choice(res, settings)
    fresh = settings_with(settings, choice)
    res.saved = (saver or save_settings)(screen_updates(choice), config_dir=config_dir)
    mark_setup_done(state_dir or resolve_state_dir(settings), choice, res.detection)
    return fresh


# ---------------------------------------------------------------------------
# 실행 중 다시 찾기 + 자동 따라가기 (캡처 스레드에서 돈다)
# ---------------------------------------------------------------------------


def current_game_rect(windows: Callable[[], list[WinInfo]] | None = None,
                      own_pid: int | None = None) -> WinInfo | None:
    """게임 창 하나(검증 없음, 싸다 — EnumWindows 한 번). 자동 따라가기의 변화 감시용."""
    ok, _ = supported()
    if windows is None and not ok:
        return None
    try:
        return select_game_window((windows or list_windows)(), os.getpid() if own_pid is None else own_pid)
    except Exception:   # noqa: BLE001
        log.debug("게임 창 목록 실패", exc_info=True)
        return None


class ScreenRedetector:
    """"게임 화면 다시 찾기" 버튼과 자동 따라가기. 실제 작업은 **캡처 스레드**의 `tick()`에서 한다.

    `request()`는 아무 스레드에서나 부른다(버튼 → UI 스레드). 결과 콜백은 캡처 스레드에서 불리므로
    Qt 쪽은 시그널로 UI 스레드에 넘긴다(`OverlayWindow.redetected`).
    `LiveLoop.screen_hook`으로 붙인다(`app/live.py`).
    """

    def __init__(self, settings: Settings, *, config_dir: Path | None = None, state_dir: Path | None = None,
                 follow: bool | None = None, interval_s: float | None = None,
                 finder: Callable[..., WindowDetection] | None = None,
                 peek: Callable[[], WinInfo | None] | None = None,
                 saver: Callable[..., Any] | None = None,
                 on_follow: Callable[[WindowDetection], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.config_dir = config_dir
        self.state_dir = state_dir
        self.follow = settings.capture.follow_game_window if follow is None else bool(follow)
        self.interval_s = settings.capture.follow_interval_s if interval_s is None else float(interval_s)
        self._finder = finder or find_game_window
        self._peek = peek or current_game_rect
        self._saver = saver
        self.on_follow = on_follow
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: list[Callable[[WindowDetection], None] | None] = []
        self._apply: Settings | None = None
        self._last_check = -1e9
        self._seen: Rect | None = None          # 마지막으로 적용(또는 설정과 같다고 확인)한 창 영역
        self._failed: tuple[Rect, float] | None = None
        self._candidate: Rect | None = None     # 바뀐 창 영역(한 번 더 같게 보이면 적용)
        self.last: WindowDetection | None = None
        if self.follow and not supported()[0] and peek is None:
            self.follow = False

    # ---- 아무 스레드
    def request(self, on_done: Callable[[WindowDetection], None] | None = None) -> None:
        """다음 캡처 주기에 다시 찾는다(보통 0.25초 안)."""
        with self._lock:
            self._pending.append(on_done)

    def request_apply(self, settings: Settings) -> None:
        """이미 저장된 화면 설정(설정 화면 [저장])을 다음 캡처 주기에 실행 중 루프에 적용한다(재시작 없이)."""
        with self._lock:
            self._apply = settings

    # ---- 캡처 스레드
    def tick(self, loop: Any) -> WindowDetection | None:
        with self._lock:
            pending, self._pending = self._pending, []
            apply, self._apply = self._apply, None
        if apply is not None:
            self.settings = apply
            self._seen = None          # 새 설정 기준으로 다음 따라가기 확인을 다시 한다
            if loop is not None and hasattr(loop, "apply_screen"):
                loop.apply_screen(apply)
        if pending:
            res = self.redetect(loop)
            for cb in pending:
                if cb is not None:
                    try:
                        cb(res)
                    except Exception:   # noqa: BLE001
                        log.exception("다시 찾기 결과 콜백 실패")
            return res
        if not self.follow:
            return None
        now = self.clock()
        if now - self._last_check < self.interval_s:
            return None
        self._last_check = now
        return self._follow_step(loop, now)

    def _follow_step(self, loop: Any, now: float) -> WindowDetection | None:
        win = self._peek()
        if win is None or win.iconic:
            self._candidate = None
            return None
        rect = win.client
        if rect == self._seen:
            self._candidate = None
            return None
        if self._candidate != rect:
            # 창을 끄는 중일 수 있다 → 같은 위치가 한 번 더 보이면(FOLLOW_CONFIRM_S 뒤) 맞춘다
            self._candidate = rect
            self._last_check = now - self.interval_s + FOLLOW_CONFIRM_S
            return None
        self._candidate = None
        if self._failed is not None and self._failed[0] == rect and now - self._failed[1] < FOLLOW_RETRY_S:
            return None
        res = self.redetect(loop, reason="follow")
        if res.ok:
            self._failed = None
            if not res.unchanged:
                log.info("게임 창 변화 → 캡처 영역을 다시 맞췄습니다: %s", res.message)
                if self.on_follow is not None:
                    try:
                        self.on_follow(res)
                    except Exception:   # noqa: BLE001
                        log.exception("따라가기 알림 실패")
        else:
            self._failed = (rect, now)
            log.info("게임 창이 바뀌었지만 맞추지 못했습니다(%s): %s", res.status, res.message)
        return res

    def redetect(self, loop: Any = None, *, reason: str = "manual") -> WindowDetection:
        """찾기 → 검증 → (바뀌었으면) 루프에 적용 + 저장. 캡처 스레드(또는 루프가 없을 때 아무 스레드)에서 부른다."""
        settings = getattr(loop, "settings", None) or self.settings
        scorer = getattr(getattr(loop, "recognizer", None), "screen_score", None)
        res = self._finder(scorer=scorer)
        self.last = res
        if not res.ok:
            log.info("게임 화면 다시 찾기(%s) 실패: %s", reason, res.message)
            return res
        assert res.monitor is not None and res.local is not None and res.window is not None
        if same_screen(settings, screen_choice(res, settings), res.monitor):
            res.unchanged = True
            self._seen = res.window.client
            return res
        try:
            fresh = persist(res, settings, config_dir=self.config_dir, state_dir=self.state_dir, saver=self._saver)
        except Exception as e:   # noqa: BLE001 — 저장이 안 돼도 이번 실행에는 적용한다
            log.warning("게임 화면 설정 저장 실패", exc_info=True)
            res.warnings.append(f"설정 파일 저장에 실패했습니다({type(e).__name__}) — 이번 실행에만 적용됩니다.")
            fresh = settings_with(settings, screen_choice(res, settings))
        self.settings = fresh
        if loop is not None and hasattr(loop, "apply_screen"):
            loop.apply_screen(fresh)
            res.applied = True
        self._seen = res.window.client
        log.info("게임 화면 다시 찾기(%s): %s · content_box=%s", reason, res.message, res.content_box)
        return res


__all__ = [
    "GAME_CLASSES", "GAME_TITLES", "ScreenRedetector", "WinInfo", "WindowDetection", "clip_to_monitor",
    "content_box_for", "current_game_rect", "find_game_window", "is_game_window", "list_windows",
    "monitor_for_rect", "occlusion", "persist", "same_screen", "screen_choice", "screen_updates",
    "select_game_window", "supported",
]
