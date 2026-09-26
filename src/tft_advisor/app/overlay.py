"""PySide6 오버레이 — 게임 위에 목표 덱·상점·증강·아이템 추천을 띄운다(한국어).

창 성질: 테두리 없음(frameless) · 항상 위 · 반투명 · 기본 클릭 통과. 플랫폼 분기는 `platform_window.py`에만 둔다.
클릭 통과가 안 되는 환경에서는 **일반 창(드래그 가능)** 으로 내려가고 상태줄에 그 사실을 적는다.

스레드: 인식·추천은 전부 루프 스레드에서 돈다. 루프는 `on_loop_update()`를 아무 스레드에서나 부르고, 그 안에서
Qt 시그널로 UI 스레드에 넘긴다(**UI 스레드에서 인식·Jev 호출을 하지 않는다**).

조작(트레이 아이콘 메뉴 / 창이 잠금 해제 상태일 때 단축키)
- Jev 실시간 판단     트레이 메뉴 체크(과금). 켜면 live, 끄면 mock으로 **재시작 없이** 바꾸고 설정에 저장한다
                      (`app/jev_toggle.py`. 교체는 작업 스레드에서 하고, 새 백엔드는 다음 추천부터 쓰인다)
- 인식 확인 창        트레이 메뉴 체크. 보드·벤치·장착/미사용 아이템을 보여 주는 보조 창(`app/recog_window.py`)
- 목표 덱 고정        오버레이 왼쪽의 "📌 목표 덱" 띠(별도의 작은 창, 클릭 통과 아님)·인식 확인 창 버튼·트레이
                      "목표 덱 고정 ▸". 누르면 그 덱으로 고정(모든 추천이 그 덱 기준), 다시 누르면 해제(`app/deck_chooser.py`)
- 게임 화면 다시 찾기  트레이 메뉴. 게임 창 위치·크기를 OS 창 목록에서 다시 읽어 캡처 영역을 재시작 없이 맞춘다
                      (`app/game_window.py`. 찾기는 캡처 스레드에서 한다)
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
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget

from ..config import Settings, load_settings
from ..contracts import GameState, Recommendation
from .hud_model import ACCENT, DIM, GOOD, STALE, WARN, build_model
from .hud_view import HudView, IconBook, default_icon_dir
from .names import NameBook
from .pinning import header_label
from .platform_window import apply_always_on_top, apply_click_through, click_through_note
from .report import (
    KeptInfo, StatusInfo, recognition_warnings,
    shop_lines, state_line, status_line,
)

log = logging.getLogger(__name__)

STATE_FILE = "overlay.json"
QUIT_HINT = "종료: 트레이 아이콘 오른쪽 클릭 → 종료, 또는 ✕ 버튼"
QUIT_HINT_MS = 30000          # 시작 안내를 상태줄에 보여 주는 시간
CONFIRM_MS = 3000             # 종료 버튼: 첫 클릭 뒤 이 시간 안에 한 번 더 눌러야 끝난다(실수 방지)
__all_colors__ = (ACCENT, DIM, GOOD, STALE, WARN)   # 색은 hud_model 한 곳에서 정한다(여기서는 다시 내보낸다)


class ConfirmButton(QPushButton):
    """두 번 눌러야 동작하는 버튼(종료 실수 방지). 첫 클릭 → 확인 문구, `CONFIRM_MS` 안에 다시 누르면 `action()`."""

    def __init__(self, text: str, confirm_text: str, action, parent: QWidget | None = None,
                 timeout_ms: int = CONFIRM_MS) -> None:
        super().__init__(text, parent)
        self._text = text
        self._confirm_text = confirm_text
        self._action = action
        self.armed = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self.disarm)
        self.clicked.connect(self._on_click)

    def _on_click(self) -> None:
        if not self.armed:
            self.armed = True
            self.setText(self._confirm_text)
            self._timer.start()
            return
        self.disarm()
        self._action()

    def disarm(self) -> None:
        self.armed = False
        self._timer.stop()
        self.setText(self._text)


class QuitHandle(QWidget):
    """오버레이 옆의 작은 "✕ 종료" 손잡이 창.

    오버레이는 잠금(클릭 통과) 상태에서 마우스를 전혀 받지 않으므로, 종료 버튼만 **별도의 작은 창**으로 띄운다
    (항상 위 · 테두리 없음 · 포커스를 가져가지 않음 · 클릭 통과 아님). 창 일부만 입력을 받게 하는 방식
    (WM_NCHITTEST 가로채기)보다 플랫폼 차이가 없고 튼튼하다. 오버레이를 따라 움직이고 함께 숨는다.
    """

    def __init__(self, on_quit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TFT Advisor 종료")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button = ConfirmButton("✕ 종료", "정말 종료? 다시 클릭", on_quit, self)
        self.button.setToolTip("TFT Advisor를 끝냅니다(두 번 누르면 종료, 세션은 저장됩니다)")
        self.button.setStyleSheet(
            "QPushButton { background: rgba(60, 20, 24, 230); color: #ffd8d8; border: 1px solid #a33;"
            " border-radius: 4px; padding: 2px 8px; font-size: 11px;"
            " font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; }"
            "QPushButton:hover { background: rgba(140, 30, 36, 240); }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.button)
        self.adjustSize()

    def follow(self, overlay: QWidget) -> None:
        """오버레이 오른쪽 위 모서리 바로 위(화면 밖이면 아래 왼쪽)에 붙는다."""
        self.adjustSize()
        g = overlay.frameGeometry()
        x = g.right() - self.width() + 1
        y = g.top() - self.height() - 2
        if y < 0:
            y = g.bottom() + 2
        self.move(int(x), int(y))


class OverlayWindow(QWidget):
    """추천 표시 창. 테스트는 이 클래스를 offscreen 플랫폼으로 만들고 `set_data()`를 부른다."""

    loop_update = Signal(object)
    jev_switched = Signal(object)     # jev_toggle.SwitchResult (작업 스레드 → UI 스레드)
    redetected = Signal(object)       # game_window.WindowDetection (캡처 스레드 → UI 스레드)
    icons_ready = Signal(object)      # 챔피언 아이콘 첫 내려받기 끝(작업 스레드 → UI 스레드)

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
        self.recog = None                        # recog_window.RecogController (인식 확인 창, 없으면 메뉴 항목 없음)
        self.quit_handle: QuitHandle | None = None   # 클릭 통과 중에도 누를 수 있는 "✕ 종료" 손잡이(별도 창)
        self.redetector = None                   # game_window.ScreenRedetector ("게임 화면 다시 찾기", 없으면 메뉴 항목 없음)
        self.last_redetect = None                # 마지막 다시 찾기 결과(WindowDetection)
        self.unit_review_opener = None           # () -> 검토 창 ("유닛 사진 검토", app/unit_review.py). 없으면 메뉴 항목 없음
        self.review_window = None                # 열린 검토 창(참조를 들고 있어야 GC로 닫히지 않는다)
        self.pin = None                          # deck_chooser.PinController (목표 덱 고정, 없으면 띠·메뉴 없음)
        self.deck_chooser = None                 # deck_chooser.DeckChooser (오버레이 옆 "목표 덱" 띠 창)

        self.setWindowTitle("TFT Advisor")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.menu().exec(self.mapToGlobal(pos)))

        # 본문 = 고정 배치 HUD(섹션 자리·줄 수 고정, 33 보고). 상태줄은 아래 고정 높이 라벨.
        self.icons = IconBook(default_icon_dir(self.settings.app.set_number) if self.cfg.unit_icons else None)
        self.body = HudView(self.cfg, icons=self.icons, deck_slots=self.settings.ui.max_target_comps, parent=self)
        self.foot = QLabel("", self)
        self.foot.setTextFormat(Qt.TextFormat.RichText)
        self.foot.setWordWrap(True)
        self.foot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        pad = int(10 * self.scale)
        self._pad = pad
        layout = QVBoxLayout(self)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(int(4 * self.scale))
        layout.addWidget(self.body, 1)
        layout.addWidget(self.foot)
        self.setStyleSheet(self._stylesheet())
        self.foot.setFixedHeight(2 * self.body.line_h + int(10 * self.scale))   # 상태줄 2줄 고정
        self._apply_geometry()
        self.setWindowOpacity(self._opacity)

        self.loop_update.connect(self._on_update_main, Qt.ConnectionType.QueuedConnection)
        self.jev_switched.connect(self._on_jev_switched, Qt.ConnectionType.QueuedConnection)
        self.redetected.connect(self._on_redetected, Qt.ConnectionType.QueuedConnection)
        self.icons_ready.connect(self._on_icons_ready, Qt.ConnectionType.QueuedConnection)
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
        if self.pin is not None:
            self.pin.set_rec(rec, notify=False)
        self.render()
        self._place_chooser()

    def render(self) -> None:
        """내용만 바꾼다. **창 크기·섹션 자리는 바꾸지 않는다**(고정 배치, 33 보고)."""
        rec = self.rec
        pin_note = self.pin.header() if self.pin is not None else header_label(None, rec)
        self.body.set_model(build_model(
            self.state, rec, self.names, kept=self.kept, pin_note=pin_note,
            deck_slots=self.settings.ui.max_target_comps, threshold=self.settings.vision.state_min_confidence,
            plan_color=_plan_color))
        self._render_status()

    def _on_icons_ready(self, _count=None) -> None:
        self.icons.refresh()
        self.body.update()

    def fixed_size(self) -> tuple[int, int]:
        """설정 크기(`[overlay] width`/`height`). 화면보다 크면 화면 크기로 줄인다(섹션 줄 수가 대신 줄어든다)."""
        w, h = self.cfg.width, self.cfg.height
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            w, h = min(w, area.width()), min(h, area.height())
        return int(w), int(h)

    def _apply_geometry(self) -> None:
        """창을 고정 크기로 두고, 본문 높이에 맞춰 섹션 줄 수를 한 번 정한다(내용과 무관)."""
        w, h = self.fixed_size()
        self.setFixedSize(w, h)
        body_h = h - 2 * self._pad - self.foot.height() - self.layout().spacing()
        self.body.setFixedHeight(max(50, body_h))
        self.body.fit(self.body.height())

    def chooser_offset(self) -> int:
        """목표 덱 띠를 [목표 덱] 제목 높이에 맞춘다(창 위쪽에서 px)."""
        y = self.body.section_y.get("comps")
        if y is None:   # 아직 그리지 않았다 — 고정 배치라 계산으로 같은 값
            y = int(self.body.pad + (3 + 0.4) * self.body.line_h)
        return self.body.y() + y

    def _render_status(self) -> None:
        self.foot.setText(f"<span style='color:{DIM}'>{_line(status_line(self.status))}</span>")

    # ------------------------------------------------------------------ 루프 연결
    def on_loop_update(self, update) -> None:
        """루프(다른 스레드)에서 부른다 → Qt 큐를 거쳐 UI 스레드에서 처리한다."""
        self.loop_update.emit(update)

    def attach_recog(self, controller) -> None:
        """인식 확인 창 컨트롤러를 붙인다(트레이 메뉴를 만들기 전, 즉 `show_overlay()` 전에 부른다)."""
        self.recog = controller

    # ------------------------------------------------------------------ 목표 덱 고정(31 보고)
    def attach_pin(self, controller) -> None:
        """`deck_chooser.PinController`를 붙인다(`show_overlay()` 전에). 띠 창·트레이 하위 메뉴가 생긴다."""
        self.pin = controller
        if controller is not None:
            controller.on_change = self._on_pin_changed

    def _on_pin_changed(self) -> None:
        pin = self.pin
        if pin is not None:
            self._set_extra(f"목표 덱 고정: {pin.pinned_name or pin.pinned}" if pin.pinned else "목표 덱 고정 해제")
        self.render()
        self._place_chooser()

    def _place_chooser(self) -> None:
        chooser = self.deck_chooser
        if chooser is None:
            return
        want = self.isVisible() and chooser.has_choices
        if want:
            chooser.follow(self)
        if chooser.isVisible() != want:
            chooser.setVisible(want)

    # ------------------------------------------------------------------ 게임 화면 다시 찾기
    def attach_redetector(self, redetector) -> None:
        """`game_window.ScreenRedetector`를 붙인다(`show_overlay()` 전에). 자동 따라가기 결과도 여기로 알린다."""
        self.redetector = redetector
        if redetector is not None:
            redetector.on_follow = self.redetected.emit

    def request_redetect(self) -> bool:
        """트레이 "게임 화면 다시 찾기" / 인식 확인 창 버튼. 실제 찾기는 캡처 스레드에서 한다(UI를 멈추지 않는다)."""
        if self.redetector is None:
            self._set_extra("게임 화면 다시 찾기를 쓸 수 없습니다(실시간 캡처가 아닙니다)")
            return False
        self._set_extra("게임 화면을 다시 찾는 중…")
        if self.recog is not None and hasattr(self.recog, "show_redetect"):
            self.recog.show_redetect("게임 화면을 다시 찾는 중…", busy=True)
        self.redetector.request(self.redetected.emit)
        return True

    def _on_redetected(self, res) -> None:
        """다시 찾기 결과(UI 스레드). 상태줄 + 트레이 알림 + 인식 확인 창에 한국어로 보여 준다."""
        self.last_redetect = res
        text = res.text()
        self._set_extra(res.message if res.ok else f"게임 화면 찾기 실패 — {res.message}")
        if self.recog is not None and hasattr(self.recog, "show_redetect"):
            self.recog.show_redetect(text, ok=res.ok)
        if self.tray is not None:
            self.tray.showMessage("TFT Advisor", text)

    # ------------------------------------------------------------------ 유닛 사진 검토
    def attach_unit_review(self, opener) -> None:
        """"유닛 사진 검토" 창을 여는 함수를 붙인다(`show_overlay()` 전에). 창은 UI 스레드에서 열리고 루프는 계속 돈다."""
        self.unit_review_opener = opener

    def open_unit_review(self):
        """트레이 "유닛 사진 검토" / 인식 확인 창 버튼. 이미 열려 있으면 앞으로 가져온다(모달 아님)."""
        if self.unit_review_opener is None:
            self._set_extra("유닛 사진 검토를 쓸 수 없습니다(유닛 이름 인식이 꺼져 있습니다)")
            return None
        win = self.review_window
        try:
            if win is not None and win.isVisible():
                win.raise_()
                win.activateWindow()
                return win
        except RuntimeError:   # 이미 지워진 창
            pass
        try:
            self.review_window = self.unit_review_opener()
        except Exception as e:   # noqa: BLE001 — 검토 창 실패가 오버레이를 멈추지 않는다
            log.exception("유닛 사진 검토 창을 열지 못했습니다")
            self._set_extra(f"유닛 사진 검토 창을 열지 못했습니다: {type(e).__name__}")
            return None
        return self.review_window

    def _feed_recog(self, update) -> None:
        if self.recog is None:
            return
        try:
            self.recog.feed(update)
        except Exception:   # 보조 창 오류로 추천 표시가 멈추지 않는다
            log.exception("인식 확인 창 갱신 실패")

    def _on_update_main(self, update) -> None:
        self._feed_recog(update)
        threshold = self.settings.vision.state_min_confidence
        if update.kind == "reset":
            if self.pin is not None:   # 새 판: 목표 덱 고정도 풀린다(루프·세션은 이미 비웠다)
                self.pin.clear()
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
        self._apply_geometry()   # 창이 놓일 화면이 정해진 뒤 한 번 더(화면보다 크면 줄인다)
        self.place()
        self._age_timer.start()
        if self.tray is None:
            self.tray = _make_tray(self)
        self._show_quit_handle()
        self._show_chooser()
        self.show_quit_hint()

    def _show_chooser(self) -> None:
        if self.pin is None:
            return
        if self.deck_chooser is None:
            from .deck_chooser import DeckChooser

            self.deck_chooser = DeckChooser(self.pin)
            apply_always_on_top(self.deck_chooser, True)
        self._place_chooser()

    def _show_quit_handle(self) -> None:
        if self.quit_handle is None:
            self.quit_handle = QuitHandle(self.quit)
            apply_always_on_top(self.quit_handle, True)
        self.quit_handle.follow(self)
        self.quit_handle.show()

    def show_quit_hint(self) -> None:
        """시작할 때 한 번: 종료 방법(로그 + 상태줄, QUIT_HINT_MS 동안)."""
        log.info(QUIT_HINT)
        self._set_extra(QUIT_HINT)

        def clear() -> None:
            if self.status.extra == QUIT_HINT:
                self._set_extra(None)

        QTimer.singleShot(QUIT_HINT_MS, clear)

    def moveEvent(self, event) -> None:   # noqa: N802 — Qt 이름
        super().moveEvent(event)
        if self.quit_handle is not None and self.quit_handle.isVisible():
            self.quit_handle.follow(self)
        if self.deck_chooser is not None and self.deck_chooser.isVisible():
            self.deck_chooser.follow(self)

    def resizeEvent(self, event) -> None:   # noqa: N802
        super().resizeEvent(event)
        if self.quit_handle is not None and self.quit_handle.isVisible():
            self.quit_handle.follow(self)
        if self.deck_chooser is not None and self.deck_chooser.isVisible():
            self.deck_chooser.follow(self)

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
            fresh = dialog.outcome.settings
            if self.redetector is not None and fresh is not None:
                self.redetector.request_apply(fresh)   # 화면 설정도 재시작 없이(캡처 스레드에서 갈아 끼운다)
                msg = "설정을 저장하고 화면 설정을 바로 적용했습니다."
            else:
                msg = "설정을 저장했습니다. 화면 설정은 앱을 다시 시작하면 적용됩니다."
            self._set_extra(msg)
            if self.tray is not None:
                self.tray.showMessage("TFT Advisor", msg)
            self._apply_saved_jev(dialog.outcome)
            self._apply_saved_recog(dialog.outcome)
        return dialog.outcome

    def _apply_saved_recog(self, outcome) -> None:
        """설정 화면의 "인식 확인 창 표시"도 재시작 없이 적용한다(파일은 대화상자가 이미 썼다)."""
        choice = getattr(outcome, "choice", None)
        want = getattr(choice, "test_view", None)
        if self.recog is None or want is None or bool(want) == self.recog.enabled:
            return
        self.recog.set_enabled(bool(want), persist=False)

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
        if self.quit_handle is not None:   # 숨기면 손잡이도 숨긴다(종료는 트레이 메뉴로)
            self.quit_handle.setVisible(self.isVisible())
            if self.isVisible():
                self.quit_handle.follow(self)
        self._place_chooser()   # 목표 덱 띠도 함께 숨고 나타난다

    def adjust_opacity(self, delta: float) -> None:
        self._opacity = max(0.2, min(1.0, self._opacity + delta))
        self.setWindowOpacity(self._opacity)

    def quit(self) -> None:
        """모든 종료 경로(트레이 "종료"·Ctrl+Q·✕ 손잡이·인식 확인 창 [앱 종료])가 여기로 온다 → `app.quit()` →
        `aboutToQuit`에 붙은 종료 처리(루프 정지·세션 저장, `app/live._run_overlay`)가 한 번 돈다."""
        log.info("종료 요청")
        for w in (self.quit_handle, self.deck_chooser):
            if w is not None:
                w.hide()
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
        if self.pin is not None:
            self.pin.add_menu(m)
        if self.recog is not None:
            self.recog.add_menu_action(m)
        if self.redetector is not None:
            _add(m, "게임 화면 다시 찾기", self.request_redetect)
        if self.unit_review_opener is not None:
            _add(m, "유닛 사진 검토…", self.open_unit_review)
        _add(m, "설정(화면 자동 감지)…", self.open_setup)
        _add(m, "불투명도 +", lambda: self.adjust_opacity(0.05))
        _add(m, "불투명도 −", lambda: self.adjust_opacity(-0.05))
        m.addSeparator()
        _add(m, "종료\tCtrl+Q", self.quit)
        return m


def _plan_color(line: str, plan, stale: bool) -> str:
    """보드 배치 한 줄의 색. 할 일(교체·판매)은 WARN, 직전 계획은 전부 흐리게."""
    if stale:
        return STALE
    if line.startswith("교체:") and plan.swaps:
        return WARN
    if line.startswith("판매:"):
        return WARN
    return DIM


def _line(text: str) -> str:
    """텍스트 한 줄 → HTML. 리치 텍스트는 공백을 접으므로 들여쓰기와 구분 공백을 살려 준다."""
    stripped = text.lstrip(" ")
    indent = "&nbsp;" * (len(text) - len(stripped)) * 2
    return indent + html.escape(stripped).replace("  ", " &middot; ")


SECTION_GAP = "<span style='font-size:5pt'>&nbsp;</span>"
"""섹션 제목 앞 빈 줄(본문 한 줄보다 낮다). 섹션끼리 구분되게 한다."""


def _section(title: str, note: str | None = None) -> str:
    tail = f" <span style='color:{DIM}'>({html.escape(note)})</span>" if note else ""
    return f"{SECTION_GAP}<br><b style='color:{ACCENT}'>[{html.escape(title)}]</b>{tail}"


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
        log.info("시스템 트레이를 쓸 수 없습니다 — 잠금 해제 후 창 우클릭 메뉴를 쓰세요")
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


__all__ = ["ConfirmButton", "OverlayWindow", "QUIT_HINT", "QuitHandle", "make_overlay"]
