"""PySide6 오버레이 — 게임 위에 목표 덱·상점·증강·아이템 추천을 띄운다(한국어).

창 성질: 테두리 없음(frameless) · 항상 위 · 반투명 · 기본 클릭 통과. 플랫폼 분기는 `platform_window.py`에만 둔다.
클릭 통과가 안 되는 환경에서는 **일반 창(드래그 가능)** 으로 내려가고 상태줄에 그 사실을 적는다.

스레드: 인식·추천은 전부 루프 스레드에서 돈다. 루프는 `on_loop_update()`를 아무 스레드에서나 부르고, 그 안에서
Qt 시그널로 UI 스레드에 넘긴다(**UI 스레드에서 인식·Jev 호출을 하지 않는다**).

조작(트레이 아이콘 메뉴 / 창이 잠금 해제 상태일 때 단축키)
- 표시/숨기기         Ctrl+Shift+O
- 이동 잠금/해제       Ctrl+Shift+L  (해제하면 드래그로 옮길 수 있다. 잠금 = 클릭 통과)
- 위치 저장           Ctrl+S         → `_state/overlay.json`(다음 실행에 복원)
- 불투명도 ±          Ctrl+Shift+Up / Down
- 종료                Ctrl+Q
전역(게임에 포커스가 있을 때 동작하는) 단축키는 **만들지 않는다**: 키보드 후킹은 CLAUDE.md 고정 제약에 걸린다.
트레이 아이콘이 없는 환경에서는 잠금을 해제하고 창을 우클릭하면 같은 메뉴가 나온다.
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

from ..config import Settings, load_settings
from ..contracts import GameState, Recommendation
from .names import NameBook
from .platform_window import apply_always_on_top, apply_click_through, click_through_note
from .report import (
    StatusInfo, augment_lines, comp_lines, item_lines, jev_label, recognition_warnings, shop_lines,
    state_line, status_line,
)

log = logging.getLogger(__name__)

STATE_FILE = "overlay.json"
ACCENT = "#7fd1ff"
GOOD = "#8ce99a"
WARN = "#ffd43b"
DIM = "#9aa4b2"


class OverlayWindow(QWidget):
    """추천 표시 창. 테스트는 이 클래스를 offscreen 플랫폼으로 만들고 `set_data()`를 부른다."""

    loop_update = Signal(object)

    def __init__(self, settings: Settings | None = None, *, names: NameBook | None = None,
                 state_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings or load_settings()
        self.cfg = self.settings.overlay
        self.names = names or NameBook()
        self.state_dir = state_dir
        self.scale = self.cfg.scale
        self._opacity = self.settings.overlay_opacity()
        self._locked = self.cfg.locked and self.settings.overlay_click_through()
        self._drag_from = None
        self.click_effect = None      # 마지막 클릭 통과 적용 결과(platform_window.WindowEffect)
        self.state: GameState | None = None
        self.rec: Recommendation | None = None
        self.status = StatusInfo(backend=self.settings.advisor.jev_backend)
        self.tray: QSystemTrayIcon | None = None

        self.setWindowTitle("TFT Advisor")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.menu().exec(self.mapToGlobal(pos)))

        self.body = QLabel("", self)
        self.foot = QLabel("", self)
        for label in (self.body, self.foot):
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        pad = int(10 * self.scale)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(int(4 * self.scale))
        layout.addWidget(self.body)
        layout.addWidget(self.foot)
        self.setStyleSheet(self._stylesheet())
        self.setFixedWidth(self.cfg.width)
        self.setWindowOpacity(self._opacity)

        self.loop_update.connect(self._on_update_main, Qt.ConnectionType.QueuedConnection)
        self._age_timer = QTimer(self)
        self._age_timer.setInterval(5000)          # 상태줄의 "N초 전"을 갱신한다
        self._age_timer.timeout.connect(self._render_status)
        self._shortcuts()
        self.render()

    # ------------------------------------------------------------------ 모양
    def _stylesheet(self) -> str:
        base = int(12 * self.scale)
        return f"""
            QWidget {{ background: transparent; }}
            QLabel {{
                background: rgba(12, 14, 20, 225);
                color: #e8ecf1;
                border-radius: {int(8 * self.scale)}px;
                padding: {int(8 * self.scale)}px;
                font-size: {base}px;
                font-family: "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif;
            }}
        """

    def _shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut

        def add(seq: str, fn) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(fn)

        add("Ctrl+Shift+O", self.toggle_visible)
        add("Ctrl+Shift+L", self.toggle_locked)
        add("Ctrl+S", self.save_position)
        add("Ctrl+Shift+Up", lambda: self.adjust_opacity(0.05))
        add("Ctrl+Shift+Down", lambda: self.adjust_opacity(-0.05))
        add("Ctrl+Q", self.quit)

    # ------------------------------------------------------------------ 표시 내용
    def set_data(self, state: GameState | None, rec: Recommendation | None,
                 status: StatusInfo | None = None) -> None:
        """상태·추천을 넣고 다시 그린다(UI 스레드에서 호출)."""
        self.state = state
        self.rec = rec
        if status is not None:
            self.status = status
        self.status.rec = rec
        self.render()

    def render(self) -> None:
        self.body.setText(self._body_html())
        self._render_status()
        self.adjustSize()
        self.setFixedWidth(self.cfg.width)

    def _render_status(self) -> None:
        self.foot.setText(f"<span style='color:{DIM}'>{_line(status_line(self.status))}</span>")

    def _body_html(self) -> str:
        parts: list[str] = [f"<b style='color:{ACCENT}'>TFT Advisor</b>"]
        state, rec = self.state, self.rec
        if state is not None:
            parts.append(f"<span style='color:{DIM}'>{_line(state_line(state))}</span>")
        if rec is None:
            parts.append("<i>추천 대기 중…</i>" if state is None else "<i>이 화면에서는 새 추천이 없다</i>")
            return "<br>".join(parts)

        parts.append(_section("목표 덱", jev_label(rec)))
        shown = rec.target_comps[:self.settings.ui.max_target_comps]
        if not shown:
            parts.append("<i>후보 없음 — 인식 정보 부족</i>")
        for i, comp in enumerate(shown, start=1):   # advisor 순서 그대로. 점수로 재정렬하지 않는다
            lines = comp_lines(comp, i, self.names, compact=True)
            parts.append(f"<b>{_line(lines[0])}</b>")
            parts += [f"<span style='color:{DIM}'>{_line(ln)}</span>" for ln in lines[1:]]
        if rec.shop:
            parts.append(_section("상점"))
            for line in shop_lines(rec, self.names):
                color = GOOD if "[구매]" in line else DIM
                parts.append(f"<span style='color:{color}'>{_line(line)}</span>")
        if rec.augment is not None:
            parts.append(_section("증강 선택"))
            parts += [f"<span style='color:{WARN if ln.startswith('★') else DIM}'>{_line(ln)}</span>"
                      for ln in augment_lines(rec, self.names)]
        if rec.item is not None and (rec.item.suggestions or rec.item.hold):
            parts.append(_section("아이템"))
            parts += [f"<span style='color:{DIM}'>{_line(ln)}</span>" for ln in item_lines(rec, self.names)]
        if rec.component_priority:
            parts.append(f"<span style='color:{DIM}'>재료: "
                         f"{html.escape(self.names.joined(rec.component_priority, limit=5))}</span>")
        return "<br>".join(parts)

    # ------------------------------------------------------------------ 루프 연결
    def on_loop_update(self, update) -> None:
        """루프(다른 스레드)에서 부른다 → Qt 큐를 거쳐 UI 스레드에서 처리한다."""
        self.loop_update.emit(update)

    def _on_update_main(self, update) -> None:
        threshold = self.settings.vision.state_min_confidence
        if update.kind == "reset":
            self.set_data(update.state, None, self.status)
            return
        state = update.state or self.state
        rec = update.recommendation if update.recommendation is not None else self.rec
        if update.kind == "advice" and update.recommendation is not None:
            self.status.updated_at = update.at
        self.status.warnings = list(recognition_warnings(state, threshold)) if state is not None else []
        if update.kind == "error" and update.message:
            self.status.warnings = [update.message]
        self.set_data(state, rec, self.status)

    # ------------------------------------------------------------------ 창 조작
    def place(self) -> None:
        """설정(anchor + x/y) 또는 저장된 위치로 창을 놓는다."""
        saved = self.load_position()
        if saved is not None:
            self.move(*saved)
            return
        screens = QGuiApplication.screens()
        if not screens:
            return
        screen = screens[min(self.cfg.screen, len(screens) - 1)]
        area = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = area.left() + self.cfg.x if "left" in self.cfg.anchor else area.right() - w - self.cfg.x
        y = area.top() + self.cfg.y if "top" in self.cfg.anchor else area.bottom() - h - self.cfg.y
        self.move(int(x), int(y))

    def show_overlay(self) -> None:
        """창을 띄우고 항상 위·클릭 통과를 적용한다."""
        apply_always_on_top(self, self.cfg.always_on_top)
        self.apply_lock(self._locked)
        self.show()
        self.place()
        self._age_timer.start()
        if self.tray is None:
            self.tray = _make_tray(self)

    def apply_lock(self, locked: bool) -> None:
        """잠금 = 클릭 통과(이동 불가). 해제 = 일반 창(드래그·단축키 사용 가능)."""
        effect = apply_click_through(self, locked)
        self._locked = locked and bool(effect)
        self.click_effect = effect
        note = "잠금(클릭 통과)" if self._locked else "이동 가능"
        if locked and not effect:
            note = f"클릭 통과 실패 — {click_through_note()}"
        self.status.extra = note
        if self.isVisible():
            self.show()          # 창 플래그를 바꾸면 다시 show()해야 한다
        self._render_status()

    def toggle_locked(self) -> None:
        self.apply_lock(not self._locked)

    def toggle_visible(self) -> None:
        self.setVisible(not self.isVisible())

    def adjust_opacity(self, delta: float) -> None:
        self._opacity = max(0.2, min(1.0, self._opacity + delta))
        self.setWindowOpacity(self._opacity)

    def quit(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ------------------------------------------------------------------ 위치 저장
    def position_file(self) -> Path | None:
        if self.state_dir is None:
            return None
        return Path(self.state_dir) / STATE_FILE

    def save_position(self) -> None:
        path = self.position_file()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"x": self.x(), "y": self.y(), "opacity": self._opacity,
                                        "locked": self._locked}, indent=1), encoding="utf-8")
            log.info("오버레이 위치 저장: %s", path)
        except OSError as e:
            log.warning("오버레이 위치 저장 실패: %s", e)

    def load_position(self) -> tuple[int, int] | None:
        path = self.position_file()
        if path is None or not self.cfg.remember_position or not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return int(raw["x"]), int(raw["y"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    # ------------------------------------------------------------------ 드래그(잠금 해제 상태)
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._locked:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None

    # ------------------------------------------------------------------ 메뉴
    def menu(self) -> QMenu:
        m = QMenu(self)
        _add(m, "표시/숨기기\tCtrl+Shift+O", self.toggle_visible)
        _add(m, "이동 잠금 해제\tCtrl+Shift+L" if self._locked else "이동 잠금\tCtrl+Shift+L", self.toggle_locked)
        _add(m, "위치 저장\tCtrl+S", self.save_position)
        m.addSeparator()
        _add(m, "불투명도 +", lambda: self.adjust_opacity(0.05))
        _add(m, "불투명도 −", lambda: self.adjust_opacity(-0.05))
        m.addSeparator()
        _add(m, "종료\tCtrl+Q", self.quit)
        return m


def _line(text: str) -> str:
    """텍스트 한 줄 → HTML. 리치 텍스트는 공백을 접으므로 들여쓰기와 구분 공백을 살려 준다."""
    stripped = text.lstrip(" ")
    indent = "&nbsp;" * (len(text) - len(stripped)) * 2
    return indent + html.escape(stripped).replace("  ", " &middot; ")


def _section(title: str, note: str | None = None) -> str:
    tail = f" <span style='color:{DIM}'>({html.escape(note)})</span>" if note else ""
    return f"<b style='color:{ACCENT}'>[{html.escape(title)}]</b>{tail}"


def _add(menu: QMenu, text: str, fn) -> QAction:
    action = menu.addAction(text)
    action.triggered.connect(fn)
    return action


def _icon() -> QIcon:
    """트레이 아이콘(파일 자원 없이 그린다)."""
    pix = QPixmap(32, 32)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(ACCENT))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 28, 28)
    painter.setPen(QColor(10, 12, 18))
    font = QFont()
    font.setBold(True)
    font.setPointSize(14)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    painter.end()
    return QIcon(pix)


def _make_tray(window: OverlayWindow) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.info("시스템 트레이를 쓸 수 없다 — 잠금 해제 후 창 우클릭 메뉴를 쓸 것")
        return None
    tray = QSystemTrayIcon(_icon(), window)
    tray.setToolTip("TFT Advisor")
    tray.setContextMenu(window.menu())
    tray.show()
    return tray


def make_overlay(settings: Settings | None = None, *, state_dir: Path | None = None,
                 names: NameBook | None = None) -> tuple[QApplication, OverlayWindow]:
    """QApplication + 오버레이 창을 만든다(이미 있으면 재사용)."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)   # 창을 숨겨도 앱이 끝나지 않는다
    window = OverlayWindow(settings, names=names, state_dir=state_dir)
    return app, window


__all__ = ["OverlayWindow", "make_overlay"]
