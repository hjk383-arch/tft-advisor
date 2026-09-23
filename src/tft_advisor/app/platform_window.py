"""플랫폼별 창 동작(항상 위 / 클릭 통과)을 작은 헬퍼 뒤에 둔다.

사용자 기기가 Mac인지 Windows인지 정해지지 않았다. 그래서 **플랫폼 분기는 이 파일에만** 있고, 실패하면
예외 대신 "적용 못 함"을 돌려준다. 오버레이는 그 값을 보고 **잠금 해제(=일반 창)로 내려간다**: 클릭을 먹지만
드래그로 옮길 수 있고, 사용자가 게임 클릭을 방해받으면 오버레이를 숨기면 된다.

- **Qt 공통**: `Qt.WindowTransparentForInput` 창 플래그 + `WA_TransparentForMouseEvents` 속성.
  Qt가 macOS에서는 `-[NSWindow setIgnoresMouseEvents:]`, Windows에서는 `WS_EX_TRANSPARENT`로 번역한다.
- **Windows 보강**: 위 플래그가 먹지 않는 조합(일부 드라이버·원격 데스크톱)을 대비해 ctypes로
  `WS_EX_LAYERED | WS_EX_TRANSPARENT`를 직접 얹는다.
- **macOS**: Qt 경로만 쓴다(pyobjc 의존성을 추가하지 않는다). 전용 전체화면(exclusive fullscreen) 게임 위에는
  어떤 오버레이도 뜨지 않는다 → **테두리 없는 창모드**를 쓰라고 안내한다(캡처도 같은 제약).

입력 주입은 하지 않는다. 여기 있는 것은 전부 "우리 창"의 표시 속성이다(CLAUDE.md 고정 제약).
"""
from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
GWL_EXSTYLE = -20


@dataclass(frozen=True)
class WindowEffect:
    """헬퍼 적용 결과. `applied=False`면 오버레이가 대체 동작으로 내려간다."""

    applied: bool
    method: str
    note: str = ""

    def __bool__(self) -> bool:
        return self.applied


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def apply_always_on_top(window, enabled: bool = True) -> WindowEffect:
    """항상 위. Qt 창 플래그만 쓴다(모든 플랫폼 공통)."""
    try:
        from PySide6.QtCore import Qt

        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        return WindowEffect(True, "qt")
    except Exception as e:      # pragma: no cover - Qt 없는 환경
        log.warning("항상 위 설정 실패: %s", e)
        return WindowEffect(False, "none", str(e))


def apply_click_through(window, enabled: bool = True) -> WindowEffect:
    """클릭 통과(마우스 입력을 게임에 그대로 넘김). 실패하면 applied=False."""
    try:
        from PySide6.QtCore import Qt
    except Exception as e:      # pragma: no cover
        return WindowEffect(False, "none", str(e))

    try:
        window.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        window.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
    except Exception as e:
        log.warning("클릭 통과 설정 실패: %s", e)
        return WindowEffect(False, "none", str(e))

    if is_windows():
        win = _windows_ex_style(window, enabled)
        if win is not None:
            return win
        return WindowEffect(True, "qt", "WS_EX_TRANSPARENT 직접 설정 실패 — Qt 플래그만 적용")
    if is_macos():
        return WindowEffect(True, "qt", "macOS: Qt가 NSWindow.ignoresMouseEvents로 번역합니다")
    return WindowEffect(True, "qt", "이 플랫폼에서는 Qt 플래그만 확인했습니다")


def _windows_ex_style(window, enabled: bool) -> WindowEffect | None:
    """Windows: WS_EX_LAYERED | WS_EX_TRANSPARENT 를 직접 얹거나 뗀다."""
    try:
        hwnd = int(window.winId())
        user32 = ctypes.windll.user32                      # type: ignore[attr-defined]
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        return WindowEffect(True, "win32", "WS_EX_LAYERED | WS_EX_TRANSPARENT")
    except Exception as e:      # pragma: no cover - macOS 개발 환경
        log.debug("win32 확장 스타일 설정 실패: %s", e)
        return None


def click_through_note() -> str:
    """UI에 보여 줄 플랫폼 안내 한 줄."""
    if is_windows():
        return "Windows: 클릭 통과 지원. 전용 전체화면 대신 테두리 없는 창모드를 쓰세요"
    if is_macos():
        return "macOS: 클릭 통과는 Qt 경로. 화면 기록 권한 + 테두리 없는 창모드 필요"
    return "이 플랫폼의 클릭 통과는 검증되지 않았습니다 — 잠금 해제(일반 창)로 쓰세요"
