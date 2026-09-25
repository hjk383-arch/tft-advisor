"""인식 확인 창(PySide6) — 보드·벤치·장착 아이템·미사용 아이템을 실시간으로 보여 주는 작은 보조 창.

켜는 방법(우선순위: **CLI > 트레이 토글 > settings.toml**)
- CLI `--test-view` / `--no-test-view` — 이번 실행의 시작값. CLI로 정한 실행에서도 트레이로 켜고 끌 수 있지만
  그 변경은 **저장하지 않는다**(이번 실행에만 적용).
- 오버레이 트레이/우클릭 메뉴 "인식 확인 창" 체크 — 실행 중에 켜고 끈다. 플래그 없이 띄운 실행이면 `[ui] test_view`에 저장한다.
- `[ui] test_view = true` / 설정 화면의 "인식 확인 창 표시" 체크박스.

창 성질: 테두리 없음 · 항상 위 · **클릭 통과 아님**(드래그로 옮긴다) · **포커스를 가져가지 않는다**
(`WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating` — 창을 눌러도 게임 창이 활성 상태를 잃지 않는다).
위치는 끌어 놓을 때마다 `{state_dir}/recog_window.json`에 저장하고 다음 실행에 복원한다(`[overlay] remember_position`).

스레드: 이 창은 **UI 스레드에서만** 만진다. 오버레이가 Qt 시그널로 넘겨받은 루프 갱신을 그대로 `feed()`에 넘긴다
(`OverlayWindow._on_update_main` → `RecogController.feed`). 추가 캡처·인식은 하지 않는다.
입력 주입·게임 메모리 접근 없음(CLAUDE.md 고정 제약) — 우리 창의 표시만 다룬다.
"""
from __future__ import annotations

import html
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..config import Settings
from .names import NameBook
from .platform_window import apply_always_on_top
from .recog_view import GUESS_MARK, MENU_TEXT, TITLE, RecogSnapshot, RecogView, UnitRow, build_view

log = logging.getLogger(__name__)

STATE_FILE = "recog_window.json"
ACCENT = "#7fd1ff"
GOOD = "#8ce99a"
WARN = "#ffd43b"
BAD = "#ff8787"
DIM = "#9aa4b2"
WIDTH = 460
REDETECT_TEXT = "게임 화면 다시 찾기"
REVIEW_TEXT = "유닛 사진 검토"
SOURCE_COLORS = {"vision": GOOD, "ledger": ACCENT, "manual": ACCENT, "unknown": BAD}


class RecogWindow(QWidget):
    """인식 결과 표시 창. `passive=True`(실시간): 항상 위 + 포커스 안 가져감. False(스크린샷): 일반 창."""

    def __init__(self, settings: Settings, *, names: NameBook | None = None, state_dir: Path | None = None,
                 passive: bool = True, on_close: Callable[[], None] | None = None,
                 on_redetect: Callable[[], Any] | None = None,
                 on_quit: Callable[[], Any] | None = None,
                 on_review: Callable[[], Any] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_review = on_review
        self.on_redetect = on_redetect
        self.on_quit = on_quit
        self.settings = settings
        self.names = names or NameBook()
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.passive = passive
        self.on_close = on_close
        self.threshold = settings.vision.state_min_confidence
        self.snapshot: RecogSnapshot | None = None
        self.view: RecogView | None = None
        self.history: list[RecogSnapshot] = []   # 스크린샷 모드: ←/→ 로 넘겨 본다
        self.index = 0
        self._drag_from = None
        self._last_html = ""
        self.renders = 0                         # 테스트·진단: 실제로 다시 그린 횟수

        self.setWindowTitle(f"TFT Advisor — {TITLE}")
        if passive:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                                | Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.menu().exec(self.mapToGlobal(pos)))

        self.title = QLabel(f"<b style='color:{ACCENT}'>{TITLE}</b>", self)
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.close_btn = QPushButton("×", self)
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.setToolTip("인식 확인 창을 닫습니다")
        self.close_btn.clicked.connect(self.close_window)
        # "게임 화면 다시 찾기": 게임 창 위치를 다시 읽어 캡처 영역을 맞춘다(app/game_window.py, 캡처 스레드에서).
        self.redetect_btn = QPushButton(REDETECT_TEXT, self)
        self.redetect_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.redetect_btn.setToolTip("게임 창의 위치·크기를 다시 읽어 인식 영역을 맞춥니다(창을 옮기거나 크기를 바꿨을 때).")
        self.redetect_btn.setStyleSheet("font-size: 12px; padding: 2px 8px;")
        self.redetect_btn.clicked.connect(self.redetect)
        self.redetect_btn.setVisible(on_redetect is not None)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.title, 1)
        top.addWidget(self.redetect_btn)
        self.review_btn = QPushButton(REVIEW_TEXT, self)
        self.review_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.review_btn.setToolTip("모은 유닛 사진을 승인·삭제·이름 고치기(승인한 사진만 이름 인식에 쓰입니다)")
        self.review_btn.setStyleSheet("font-size: 12px; padding: 2px 8px;")
        self.review_btn.clicked.connect(self._review)
        self.review_btn.setVisible(on_review is not None)
        top.addWidget(self.review_btn)
        # [앱 종료]: 두 번 눌러야 끝난다(실수 방지). 오버레이와 같은 종료 처리(OverlayWindow.quit)로 간다.
        from .overlay import ConfirmButton

        self.quit_btn = ConfirmButton("앱 종료", "정말 종료? 다시 클릭", self._quit, self)
        self.quit_btn.setToolTip("TFT Advisor를 끝냅니다(두 번 누르면 종료, 세션은 저장됩니다)")
        self.quit_btn.setStyleSheet("QPushButton { font-size: 12px; padding: 2px 8px; background: rgb(70, 30, 34); }"
                                    "QPushButton:hover { background: rgb(140, 30, 36); }")
        self.quit_btn.setVisible(on_quit is not None)
        top.addWidget(self.quit_btn)
        top.addWidget(self.close_btn)
        self.redetect_label = QLabel("", self)
        self.redetect_label.setTextFormat(Qt.TextFormat.RichText)
        self.redetect_label.setWordWrap(True)
        self.redetect_label.setVisible(False)

        self.body = QLabel("", self)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.scroll = QScrollArea(self)
        self.scroll.setWidget(self.body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.foot = QLabel("", self)
        self.foot.setTextFormat(Qt.TextFormat.RichText)
        self.foot.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addWidget(self.redetect_label)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.foot)
        self.setStyleSheet(f"""
            QWidget {{ background: rgb(14, 16, 22); color: #e8ecf1;
                       font-family: "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
                       font-size: 12px; }}
            QPushButton {{ background: rgb(40, 44, 54); border: none; border-radius: 4px; font-size: 14px; }}
            QPushButton:hover {{ background: rgb(90, 40, 40); }}
        """)
        self.setFixedWidth(WIDTH)
        self.resize(WIDTH, 560)

        self._age_timer = QTimer(self)
        self._age_timer.setInterval(1000)     # 프레임 나이("3초 전")만 갱신한다 — 본문은 다시 그리지 않는다
        self._age_timer.timeout.connect(self._render_foot)
        self.render()

    # ------------------------------------------------------------------ 내용
    def show_snapshot(self, snap: RecogSnapshot | None) -> None:
        """새 인식 결과(UI 스레드에서 호출). 내용이 같으면 다시 그리지 않는다."""
        self.snapshot = snap
        self.render()

    def set_history(self, snaps: list[RecogSnapshot], index: int | None = None) -> None:
        """스크린샷 모드: 여러 장을 ←/→ 로 넘겨 본다(기본은 마지막 장)."""
        self.history = list(snaps)
        self.index = len(self.history) - 1 if index is None else max(0, min(index, len(self.history) - 1))
        self.show_snapshot(self.history[self.index] if self.history else None)

    def step_history(self, delta: int) -> None:
        if not self.history:
            return
        self.index = (self.index + delta) % len(self.history)
        self.show_snapshot(self.history[self.index])

    def render(self) -> None:
        self.view = build_view(self.snapshot, self.names, threshold=self.threshold)
        text = self._html(self.view)
        if text != self._last_html:
            self._last_html = text
            self.body.setText(text)
            self.renders += 1
            self._fit_height()
        self._render_foot()

    def _render_foot(self) -> None:
        view = self.view
        bits = []
        if view is not None and view.frame_at is not None:
            bits.append(f"프레임 {view.age_text()}")
        if len(self.history) > 1:
            bits.append(f"{self.index + 1}/{len(self.history)}장 (←/→)")
        bits.append("드래그로 이동 · 우클릭 메뉴")
        self.foot.setText(f"<span style='color:{DIM}'>{html.escape(' · '.join(bits))}</span>")

    def _fit_height(self) -> None:
        """내용 높이에 맞추되 화면의 85%를 넘기지 않는다(넘치면 스크롤)."""
        inner = WIDTH - 20 - self.scroll.verticalScrollBar().sizeHint().width()
        body_h = self.body.heightForWidth(inner)
        want = (body_h if body_h > 0 else self.body.sizeHint().height()) + 80
        screen = self.screen() or QGuiApplication.primaryScreen()
        cap = int(screen.availableGeometry().height() * 0.85) if screen is not None else 900
        self.resize(WIDTH, max(160, min(want, cap)))

    def _html(self, view: RecogView) -> str:
        parts: list[str] = [f"<span style='color:{DIM}'>{_esc(line)}</span>" for line in view.header]
        if view.notice:
            parts.append(f"<b style='color:{WARN}'>{_esc(view.notice)}</b>")
        if self.snapshot is None or self.snapshot.state is None:
            return "<br>".join(parts)
        dim_all = not view.board_visible     # 보드가 안 보이는 화면: 마지막 값은 흐리게
        parts.append(_section(f"보드 {len(view.board)}기", view.board_note))
        parts.append(_rows(view.board, self.threshold, dim_all) if view.board else _dim("(유닛 없음)"))
        if view.board_common_note:
            parts.append(f"<span style='color:{WARN}'>{_esc(view.board_common_note)}</span>")
        if view.bench is None:
            parts.append(_section("벤치", "읽지 못했습니다"))
        else:
            parts.append(_section(f"벤치 {view.bench_count}/9", view.bench_note))
            parts.append(_rows(view.bench, self.threshold, dim_all))
        parts.append(_section("장착 아이템"))
        parts += [_group(g.title, g.items) for g in view.equipped] or [_dim("(없음)")]
        if view.unused is None:
            parts.append(_section("미사용 아이템", "읽지 못했습니다"))
        else:
            parts.append(_section("미사용 아이템", view.unused_note))
            parts += [_group(g.title, g.items, WARN if g.low_confidence else None)
                      for g in view.unused] or [_dim("(없음)")]
        return "<br>".join(parts)

    def _review(self) -> None:
        if self.on_review is not None:
            self.on_review()

    def _quit(self) -> None:
        if self.on_quit is not None:
            self.on_quit()

    # ------------------------------------------------------------------ 게임 화면 다시 찾기
    def redetect(self) -> None:
        """[게임 화면 다시 찾기] 버튼. 결과는 `show_redetect()`로 돌아온다."""
        if self.on_redetect is not None:
            self.on_redetect()

    def show_redetect(self, text: str, *, ok: bool | None = None, busy: bool = False) -> None:
        """다시 찾기 진행/결과 한 줄. `busy`면 버튼을 잠근다."""
        color = DIM if ok is None else (GOOD if ok else BAD)
        self.redetect_label.setText(f"<span style='color:{color}'>{_esc(text).replace(chr(10), '<br>')}</span>")
        self.redetect_label.setVisible(bool(text))
        self.redetect_btn.setEnabled(not busy)

    # ------------------------------------------------------------------ 창 조작
    def show_window(self) -> None:
        if self.passive:
            apply_always_on_top(self, True)
        self.show()
        self.place()
        self._age_timer.start()

    def close_window(self) -> None:
        """× 버튼/메뉴 "닫기". 컨트롤러가 있으면 토글을 끈 것으로 처리한다(트레이 체크도 풀린다)."""
        if self.on_close is not None:
            self.on_close()
        else:
            self.hide_window()

    def hide_window(self) -> None:
        if self.isVisible():
            self.save_position()
        self._age_timer.stop()
        self.hide()

    def place(self) -> None:
        saved = self.load_position()
        if saved is not None:
            self.move(*saved)
            return
        screens = QGuiApplication.screens()
        if not screens:
            return
        area = screens[min(self.settings.overlay.screen, len(screens) - 1)].availableGeometry()
        self.move(area.left() + 24, area.top() + 24)   # 오버레이(기본 오른쪽 위)와 겹치지 않게 왼쪽 위

    def position_file(self) -> Path | None:
        return None if self.state_dir is None else self.state_dir / STATE_FILE

    def save_position(self) -> None:
        path = self.position_file()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"x": self.x(), "y": self.y()}), encoding="utf-8")
        except OSError as e:
            log.warning("인식 확인 창 위치 저장 실패: %s", e)

    def load_position(self) -> tuple[int, int] | None:
        path = self.position_file()
        if path is None or not self.settings.overlay.remember_position or not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return int(raw["x"]), int(raw["y"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("위치 저장").triggered.connect(self.save_position)
        if self.on_redetect is not None:
            m.addAction(REDETECT_TEXT).triggered.connect(self.redetect)
        if self.on_review is not None:
            m.addAction(REVIEW_TEXT).triggered.connect(self._review)
        if len(self.history) > 1:
            m.addAction("이전 장 (←)").triggered.connect(lambda: self.step_history(-1))
            m.addAction("다음 장 (→)").triggered.connect(lambda: self.step_history(1))
        m.addSeparator()
        m.addAction("닫기").triggered.connect(self.close_window)
        return m

    # ------------------------------------------------------------------ 입력(우리 창 안에서만)
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_from is not None:
            self.save_position()      # 끌어 놓을 때마다 기억한다
        self._drag_from = None

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Left:
            self.step_history(-1)
        elif event.key() == Qt.Key.Key_Right:
            self.step_history(1)
        elif event.key() == Qt.Key.Key_Escape and not self.passive:
            self.close_window()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.save_position()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# HTML 조각
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return html.escape(text)


def _dim(text: str) -> str:
    return f"<span style='color:{DIM}'>{_esc(text)}</span>"


def _section(title: str, note: str | None = None) -> str:
    tail = f" <span style='color:{DIM}'>({_esc(note)})</span>" if note else ""
    return f"<b style='color:{ACCENT}'>[{_esc(title)}]</b>{tail}"


def _group(title: str, items: tuple[str, ...], color: str | None = None) -> str:
    style = f" style='color:{color}'" if color else ""
    return f"&nbsp;&nbsp;<b>{_esc(title)}</b>: <span{style}>{_esc(', '.join(items) or '-')}</span>"


def _rows(rows: list[UnitRow], threshold: float, dim_all: bool) -> str:
    """유닛 행 → HTML 표(자리 · 이름★ · 아이템 · 신뢰도 · 출처)."""
    cells = []
    for r in rows:
        if r.empty:
            cells.append(f"<tr><td style='color:{DIM}'>{_esc(r.pos)}</td>"
                         f"<td colspan='4' style='color:{DIM}'>(비어 있음)</td></tr>")
            continue
        name_color = DIM if dim_all else (BAD if r.source == "unknown" else "#e8ecf1")
        conf_color = DIM if dim_all else (WARN if r.confidence < threshold else GOOD)
        src_color = DIM if dim_all else SOURCE_COLORS.get(r.source, DIM)
        cells.append(
            "<tr>"
            f"<td style='color:{DIM}'>{_esc(r.pos)}</td>"
            f"<td style='color:{name_color}'>{_esc(r.name)}"
            + (f" <span style='color:{WARN}'>{_esc(GUESS_MARK)}</span>" if r.guess else "")
            + f" {_esc(r.star_text)}</td>"
            f"<td>{_esc(r.items_text)}</td>"
            f"<td style='color:{conf_color}'>{r.confidence:.2f}</td>"
            f"<td style='color:{src_color}'>{_esc(r.source_text)}</td>"
            "</tr>")
    return "<table cellspacing='0' cellpadding='2'>" + "".join(cells) + "</table>"


# ---------------------------------------------------------------------------
# 토글 컨트롤러 — 오버레이 트레이 메뉴와 창을 잇는다
# ---------------------------------------------------------------------------


def initial_enabled(settings: Settings, cli: bool | None) -> bool:
    """시작값: CLI(--test-view/--no-test-view) > `[ui] test_view`."""
    return settings.ui.test_view if cli is None else bool(cli)


class RecogController:
    """인식 확인 창 켜기/끄기 + 루프 갱신 전달. **UI 스레드에서만** 부른다.

    `cli`: CLI 플래그 값(None = 플래그 없음). 플래그가 있으면 트레이 토글은 이번 실행에만 적용하고 저장하지 않는다.
    `saver`: 테스트에서 `setup.save_settings` 대신 꽂는 자리. `window_factory`: 테스트에서 창 생성 대체.
    """

    def __init__(self, settings: Settings, *, state_dir: Path | None = None, config_dir: Path | None = None,
                 cli: bool | None = None, names: NameBook | None = None,
                 saver: Callable[..., Any] | None = None,
                 window_factory: Callable[..., RecogWindow] | None = None,
                 on_redetect: Callable[[], Any] | None = None,
                 on_quit: Callable[[], Any] | None = None,
                 on_review: Callable[[], Any] | None = None) -> None:
        self.settings = settings
        self.on_review = on_review         # 오버레이의 open_unit_review(없으면 버튼을 두지 않는다)
        self.on_quit = on_quit             # 오버레이의 quit(없으면 [앱 종료] 버튼을 두지 않는다)
        self.on_redetect = on_redetect     # 오버레이의 request_redetect(없으면 창에 버튼을 두지 않는다)
        self.redetect_note: tuple[str, bool | None, bool] | None = None   # (글, ok, busy) — 창을 켤 때 다시 보여 준다
        self.state_dir = state_dir
        self.config_dir = config_dir
        self.cli = cli
        self.names = names
        self._saver = saver
        self._factory = window_factory
        self.enabled = initial_enabled(settings, cli)
        self.window: RecogWindow | None = None
        self.last: RecogSnapshot | None = None
        self._actions: list[Any] = []
        self.saved = 0

    @property
    def persists(self) -> bool:
        """트레이 토글을 설정 파일에 저장하는가(CLI로 정한 실행은 저장하지 않는다)."""
        return self.cli is None

    def start(self) -> None:
        """앱 시작 시: 켜져 있으면 창을 띄운다."""
        if self.enabled:
            self._show()

    def feed(self, update: Any) -> None:
        """루프 갱신(UI 스레드). 창이 꺼져 있어도 마지막 스냅숏은 기억한다(켜는 순간 바로 보이게)."""
        snap = RecogSnapshot.from_update(update, self.last)
        if snap is None:
            return
        self.last = snap
        if self.enabled and self.window is not None:
            self.window.show_snapshot(snap)

    def set_enabled(self, on: bool, *, persist: bool = True) -> None:
        on = bool(on)
        if on != self.enabled:
            self.enabled = on
            if on:
                self._show()
            elif self.window is not None:
                self.window.hide_window()
            if persist and self.persists:
                self._save(on)
            try:
                self.settings.ui.test_view = on
            except Exception:   # noqa: BLE001 — 메모리 설정 갱신 실패는 표시와 무관하다
                log.debug("메모리 설정 갱신 실패", exc_info=True)
        self._sync_actions()

    def toggle(self) -> None:
        self.set_enabled(not self.enabled)

    def add_menu_action(self, menu: QMenu) -> Any:
        text = MENU_TEXT
        if not self.persists:
            text = f"{MENU_TEXT} (CLI 지정 — 이번 실행에만 적용)"
        action = menu.addAction(text)
        action.setCheckable(True)
        action.setChecked(self.enabled)
        action.toggled.connect(self.set_enabled)
        self._actions.append(action)
        return action

    def show_redetect(self, text: str, *, ok: bool | None = None, busy: bool = False) -> None:
        """다시 찾기 진행/결과를 창에 보여 준다(창이 꺼져 있으면 기억했다가 켤 때 보여 준다)."""
        self.redetect_note = (text, ok, busy)
        if self.window is not None and hasattr(self.window, "show_redetect"):
            self.window.show_redetect(text, ok=ok, busy=busy)

    # ------------------------------------------------------------------
    def _show(self) -> None:
        if self.window is None:
            factory = self._factory or RecogWindow
            kwargs = {k: v for k, v in (("on_redetect", self.on_redetect), ("on_quit", self.on_quit),
                                        ("on_review", self.on_review)) if v is not None}
            self.window = factory(self.settings, names=self.names, state_dir=self.state_dir, passive=True,
                                  on_close=lambda: self.set_enabled(False), **kwargs)
            if self.redetect_note is not None and hasattr(self.window, "show_redetect"):
                text, ok, busy = self.redetect_note
                self.window.show_redetect(text, ok=ok, busy=busy)
        self.window.show_snapshot(self.last)
        self.window.show_window()

    def _save(self, on: bool) -> None:
        updates = {"ui": {"test_view": on}}
        try:
            if self._saver is not None:
                self._saver(updates, config_dir=self.config_dir)
            else:
                from .setup import save_settings

                save_settings(updates, config_dir=self.config_dir)
            self.saved += 1
        except Exception:   # noqa: BLE001 — 저장이 실패해도 이번 실행에는 적용된다
            log.warning("[ui] test_view 저장 실패 — 이번 실행에만 적용됩니다", exc_info=True)

    def _sync_actions(self) -> None:
        alive = []
        for action in self._actions:
            try:
                action.blockSignals(True)
                action.setChecked(self.enabled)
                action.blockSignals(False)
            except RuntimeError:   # 메뉴가 이미 지워졌다
                continue
            alive.append(action)
        self._actions = alive


def show_snapshots(settings: Settings, snaps: list[RecogSnapshot], *, names: NameBook | None = None,
                   state_dir: Path | None = None, block: bool = True) -> RecogWindow:
    """스크린샷 모드: 일반 창으로 띄우고(항상 위 아님) 창을 닫을 때까지 기다린다(`block=False`면 바로 돌아온다)."""
    app = QApplication.instance() or QApplication([])
    window = RecogWindow(settings, names=names, state_dir=state_dir, passive=False)
    window.on_close = window.close
    window.set_history(snaps)
    window.show_window()
    if block:
        app.setQuitOnLastWindowClosed(True)
        app.exec()
    return window


__all__ = ["RecogController", "RecogWindow", "initial_enabled", "show_snapshots"]
