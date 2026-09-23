"""PySide6 오버레이 — 게임 위에 목표 덱·상점·증강·아이템 추천을 띄운다(한국어).

창 성질: 테두리 없음(frameless) · 항상 위 · 반투명 · 기본 클릭 통과. 플랫폼 분기는 `platform_window.py`에만 둔다.
클릭 통과가 안 되는 환경에서는 **일반 창(드래그 가능)** 으로 내려가고 상태줄에 그 사실을 적는다.

스레드: 인식·추천은 전부 루프 스레드에서 돈다. 루프는 `on_loop_update()`를 아무 스레드에서나 부르고, 그 안에서
Qt 시그널로 UI 스레드에 넘긴다(**UI 스레드에서 인식·Jev 호출을 하지 않는다**).

조작(트레이 아이콘 메뉴 / 창이 잠금 해제 상태일 때 단축키)
- Jev 실시간 판단     트레이 메뉴 체크(과금). 켜면 live, 끄면 mock으로 **재시작 없이** 바꾸고 설정에 저장한다
                      (`app/jev_toggle.py`. 교체는 작업 스레드에서 하고, 새 백엔드는 다음 추천부터 쓰인다)
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
    KeptInfo, StatusInfo, augment_lines, comp_lines, item_lines, jev_label, recognition_warnings, shop_lines,
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
    jev_switched = Signal(object)     # jev_toggle.SwitchResult (작업 스레드 → UI 스레드)

    def __init__(self, settings: Settings | None = None, *, names: NameBook | None = None,
                 state_dir: Path | None = None, config_dir: Path | None = None,
                 jev: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings or load_settings()
        self.cfg = self.settings.overlay
        self.names = names or NameBook()
        self.state_dir = state_dir
        self.config_dir = config_dir   # 트레이 메뉴 "설정"이 쓸 설정 디렉터리(None = config/)
        self.scale = self.cfg.scale
        self._opacity = self.settings.overlay_opacity()
        self._locked = self.cfg.locked and self.settings.overlay_click_through()
        self._drag_from = None
        self.click_effect = None      # 마지막 클릭 통과 적용 결과(platform_window.WindowEffect)
        self.state: GameState | None = None
        self.rec: Recommendation | None = None
        self.kept: KeptInfo | None = None   # 직전 추천 표시 중이면(전투 등) 그 정보
        self.status = StatusInfo(backend=self.settings.advisor.jev_backend)
        self.tray: QSystemTrayIcon | None = None
        self.jev = jev                 # jev_toggle.JevSwitcher (없으면 메뉴에 토글을 넣지 않는다)
        if jev is not None:
            self.status.backend = getattr(jev, "backend", self.status.backend)
        self._jev_actions: list[QAction] = []    # 메뉴마다 만들어지는 체크 항목(상태를 같이 맞춘다)
        self.jev_thread = None                   # 마지막 전환 작업 스레드(종료·테스트에서 기다린다)
        self._lock_note: str | None = None       # 상태줄 `extra`의 기본값(잠금 상태)

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
        self.jev_switched.connect(self._on_jev_switched, Qt.ConnectionType.QueuedConnection)
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
                 status: StatusInfo | None = None, kept: KeptInfo | None = None) -> None:
        """상태·추천을 넣고 다시 그린다(UI 스레드에서 호출). `kept`: 직전 추천을 보여 주는 화면이면 그 표시 정보."""
        self.state = state
        self.rec = rec
        self.kept = kept
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

        kept = self.kept
        if kept is not None:
            parts.append(f"<span style='color:{WARN}'>{_line(kept.note())}</span>")
        parts.append(_section("목표 덱", jev_label(rec) + (f" · {kept.label}" if kept is not None else "")))
        shown = rec.target_comps[:self.settings.ui.max_target_comps]
        if not shown:
            parts.append("<i>후보 없음 — 인식 정보 부족</i>")
        for i, comp in enumerate(shown, start=1):   # advisor 순서 그대로. 점수로 재정렬하지 않는다
            lines = comp_lines(comp, i, self.names, compact=True)
            parts.append(f"<b>{_line(lines[0])}</b>")
            parts += [f"<span style='color:{DIM}'>{_line(ln)}</span>" for ln in lines[1:]]
        if rec.shop:
            parts.append(_section("상점", kept.label if kept is not None else None))
            for line in shop_lines(rec, self.names):
                color = GOOD if "[구매]" in line and kept is None else DIM   # 직전 추천은 흐리게
                parts.append(f"<span style='color:{color}'>{_line(line)}</span>")
        elif kept is not None and (kept.bought or kept.changed):
            parts.append(_section("상점", f"{kept.label} — 남은 추천 칸 없음"))
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
        kept = update.kept if update.recommendation is not None else self.kept
        if update.kind == "advice" and update.recommendation is not None:
            self.status.updated_at = update.at
        self.status.warnings = list(recognition_warnings(state, threshold)) if state is not None else []
        if update.kind == "error" and update.message:
            self.status.warnings = [update.message]
        elif update.message:   # "새 판 확인 중" 등
            self.status.warnings = [update.message, *self.status.warnings]
        self.set_data(state, rec, self.status, kept=kept)

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
        self._lock_note = note
        self.status.extra = note
        if self.isVisible():
            self.show()          # 창 플래그를 바꾸면 다시 show()해야 한다
        self._render_status()

    def open_setup(self):
        """트레이 메뉴 "설정" — 실행 전 설정 대화상자를 연다(해상도 자동 감지·테스트 캡처).

        저장한 값은 **다시 시작해야** 적용된다(실시간 루프의 인식기·캡처 소스는 시작할 때 만들어진다).
        캡처에 오버레이가 찍히지 않도록 대화상자가 열려 있는 동안에는 창을 숨긴다.
        """
        try:
            from .setup_dialog import SetupDialog
        except ImportError:   # pragma: no cover — PySide6가 있어야 이 창이 뜬다
            return None
        visible = self.isVisible()
        self.hide()
        dialog = SetupDialog(self.settings, config_dir=self.config_dir, state_dir=self.state_dir)
        try:
            dialog.show()
            dialog.start()
            dialog.exec()
        finally:
            dialog.grabber.close()
            if visible:
                self.show()
        if dialog.outcome.action != "cancelled":
            self._set_extra("설정 저장됨 — 화면 설정은 다시 시작해야 적용된다")
            if self.tray is not None:
                self.tray.showMessage("TFT Advisor", "설정을 저장했다. 화면 설정은 앱을 다시 시작하면 적용된다.")
            self._apply_saved_jev(dialog.outcome)
        return dialog.outcome

    def _apply_saved_jev(self, outcome) -> None:
        """설정 화면에서 바꾼 Jev 백엔드는 **재시작 없이** 적용한다(파일은 대화상자가 이미 썼다)."""
        switcher = self.jev
        choice = getattr(outcome, "choice", None)
        backend = getattr(choice, "jev_backend", None)
        if switcher is None or backend is None or backend == switcher.backend:
            return
        if not switcher.can_toggle:
            self._set_extra(f"Jev는 {switcher.lock_note()}")
            return
        self._set_extra(f"Jev 전환 중… ({backend})")
        # 파일은 대화상자가 이미 썼다 → persist=False
        self.jev_thread = switcher.set_backend(backend, persist=False, on_done=self.jev_switched.emit)

    # ------------------------------------------------------------------ Jev 실시간 판단 토글
    def set_jev_live(self, checked: bool) -> None:
        """트레이 체크 → live / 해제 → mock. 실제 교체는 작업 스레드에서 한다(UI를 멈추지 않는다).

        표시 중인 추천은 그대로 두고 **다음 추천부터** 새 백엔드가 쓰인다(지금 Jev를 부르지 않는다).
        """
        switcher = self.jev
        if switcher is None:
            return
        target = "live" if checked else "mock"
        if target == switcher.backend:
            return
        blocked = switcher.blocked_reason(target)
        if blocked is not None:
            self._set_extra(f"Jev 전환 불가 — {blocked}")
            self._sync_jev_actions()
            return
        self._set_extra(f"Jev 전환 중… ({target})")
        self.jev_thread = switcher.set_backend(target, on_done=self.jev_switched.emit)

    def _on_jev_switched(self, result) -> None:
        """작업 스레드의 전환 결과를 UI 스레드에서 반영한다(상태줄 백엔드 표시 + 트레이 알림)."""
        self.status.backend = result.backend
        self._set_extra(None if result.ok else f"Jev 전환 실패 — {result.message}")
        self._sync_jev_actions()
        if self.tray is not None:
            self.tray.showMessage("TFT Advisor", result.message)

    def _sync_jev_actions(self) -> None:
        """열려 있는 메뉴들의 체크 상태를 지금 백엔드에 맞춘다(트레이 메뉴는 한 번만 만들어진다)."""
        live = self.status.backend == "live"
        alive = []
        for action in self._jev_actions:
            try:
                action.blockSignals(True)
                action.setChecked(live)
                action.blockSignals(False)
            except RuntimeError:   # 메뉴가 이미 지워졌다
                continue
            alive.append(action)
        self._jev_actions = alive

    def _set_extra(self, text: str | None) -> None:
        self.status.extra = text or self._lock_note
        self._render_status()

    def _add_jev_action(self, menu: QMenu) -> None:
        sw = self.jev
        if sw is None:
            return
        from .jev_toggle import MENU_TEXT

        text = MENU_TEXT
        reason = sw.blocked_reason("live")
        if reason is not None and not sw.can_toggle:
            text = f"{MENU_TEXT} — CLI --jev {sw.locked_by_cli} 로 고정"
        elif reason is not None:
            text = f"{MENU_TEXT} — TypeSafe API 키 없음 (설정에서 입력)"
        action = menu.addAction(text)
        action.setCheckable(True)
        action.setChecked(self.status.backend == "live")
        if reason is not None:
            action.setEnabled(False)
            action.setToolTip(reason)
        action.toggled.connect(self.set_jev_live)
        self._jev_actions.append(action)

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
        self._add_jev_action(m)
        _add(m, "설정(화면 자동 감지)…", self.open_setup)
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
                 names: NameBook | None = None, config_dir: Path | None = None,
                 jev: object | None = None) -> tuple[QApplication, OverlayWindow]:
    """QApplication + 오버레이 창을 만든다(이미 있으면 재사용). `jev`: 실행 중 백엔드 전환기."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)   # 창을 숨겨도 앱이 끝나지 않는다
    window = OverlayWindow(settings, names=names, state_dir=state_dir, config_dir=config_dir, jev=jev)
    return app, window


__all__ = ["OverlayWindow", "make_overlay"]
