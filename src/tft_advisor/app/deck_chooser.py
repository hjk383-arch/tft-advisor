"""목표 덱 고정 UI(PySide6) — 오버레이 옆 "목표 덱" 띠 · 인식 확인 창 버튼 줄 · 트레이 "목표 덱 고정 ▸". 31 보고.

오버레이는 잠금 상태에서 **창 전체가 클릭 통과**다(`WS_EX_TRANSPARENT`). 목표 덱 줄만 마우스를 받게 하려면
WM_NCHITTEST를 가로채 줄 위치를 창 좌표로 계산해야 하는데, 리치 텍스트 QLabel의 줄 위치는 글꼴·배율·줄바꿈에 따라
달라지고 WS_EX_TRANSPARENT 창은 hit-test 메시지 자체를 받지 않는다(창 전체를 통과시키는 스타일이다). 그래서 종료 손잡이
(`overlay.QuitHandle`)와 같은 방식으로 **별도의 작은 창**(항상 위 · 테두리 없음 · 포커스를 가져가지 않음 · 클릭 통과 아님)을
오버레이 옆에 붙인다. 누르는 것은 우리 창뿐이다 — 게임에 입력을 보내지 않는다.

`PinController`(UI 스레드 전용)가 고정 상태를 들고, 누르면 `request(comp_id | None, name)`(= `LiveLoop.request_pin`)을
부른다. advisor 호출·다시 추천은 루프가 추천 스레드에서 한다(UI 스레드는 기다리지 않는다).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from ..contracts import Recommendation
from .pinning import PIN_MARK, DeckChoice, deck_choices, header_label, toggle_target

log = logging.getLogger(__name__)

MENU_TEXT = "목표 덱 고정"
UNPIN_TEXT = "고정 해제"
TIP_FREE = "누르면 이 덱으로 목표를 고정합니다(모든 추천이 이 덱 기준). 다시 누르면 고정이 풀립니다."
TIP_PINNED = "고정된 목표 덱입니다. 다시 누르면 고정이 풀리고 자동 선정으로 돌아갑니다."

BUTTON_CSS = (
    "QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 4px;"
    " padding: 2px 8px; font-size: 11px; text-align: left;"
    " font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; }}"
    "QPushButton:hover {{ background: {hover}; }}"
)
FREE_CSS = BUTTON_CSS.format(bg="rgba(20, 30, 44, 230)", fg="#d8ecff", border="#3b5b7a", hover="rgba(40, 70, 100, 240)")
PINNED_CSS = BUTTON_CSS.format(bg="rgba(90, 70, 10, 240)", fg="#fff3bf", border="#ffd43b", hover="rgba(130, 100, 20, 250)")


class PinController:
    """목표 덱 고정 상태 + 버튼 묶음(띠·인식 확인 창)과 트레이 하위 메뉴를 같은 상태로 맞춘다. UI 스레드에서만 부른다.

    `request(comp_id | None, name)`: 루프에 고정/해제를 알리는 함수(`LiveLoop.request_pin`). 예외가 나도 표시는 유지한다.
    """

    def __init__(self, request: Callable[[str | None, str | None], Any], *, pinned: str | None = None,
                 pinned_name: str | None = None, limit: int = 3) -> None:
        self.request = request
        self.pinned = pinned
        self.pinned_name = pinned_name
        self.limit = limit
        self.rec: Recommendation | None = None
        self.views: list[DeckButtons] = []
        self.on_change: Callable[[], None] | None = None   # 오버레이가 머리 표시를 다시 그린다
        self.clicks = 0

    # ------------------------------------------------------------------ 상태
    def choices(self) -> list[DeckChoice]:
        return deck_choices(self.rec, self.limit, self.pinned, self.pinned_name)

    def header(self) -> str | None:
        return header_label(self.pinned, self.rec)

    def set_rec(self, rec: Recommendation | None, *, notify: bool = True) -> None:
        """새 추천(표시 중인 것). 버튼을 다시 만든다. `notify=False`: 오버레이가 스스로 다시 그릴 때."""
        self.rec = rec
        self._refresh(notify=notify)

    def clear(self) -> None:
        """새 판: 고정을 잊는다(루프·세션은 이미 비웠다)."""
        self.pinned = None
        self.pinned_name = None
        self.rec = None
        self._refresh(notify=False)

    def click(self, comp_id: str | None, name: str | None = None) -> str | None:
        """버튼/메뉴를 눌렀다 → 같은 덱이면 해제, 아니면 그 덱 고정. 새 고정 값을 돌려준다."""
        self.clicks += 1
        target = toggle_target(self.pinned, comp_id)
        if target is not None and name is None:
            name = next((c.name for c in self.choices() if c.comp_id == target), None)
        self.pinned = target
        self.pinned_name = name if target else None
        try:
            self.request(target, self.pinned_name)
        except Exception:   # noqa: BLE001 — 루프 쪽 실패로 UI가 멈추지 않는다
            log.exception("목표 덱 고정 요청 실패")
        self._refresh()
        return target

    def unpin(self) -> None:
        if self.pinned is not None:
            self.click(self.pinned)

    # ------------------------------------------------------------------ 보기
    def attach(self, view: DeckButtons) -> None:
        self.views.append(view)
        view.set_choices(self.choices(), self.pinned)

    def detach(self, view: DeckButtons) -> None:
        if view in self.views:
            self.views.remove(view)

    def _refresh(self, notify: bool = True) -> None:
        choices = self.choices()
        alive = []
        for view in self.views:
            try:
                view.set_choices(choices, self.pinned)
            except RuntimeError:   # 이미 지워진 위젯
                continue
            alive.append(view)
        self.views = alive
        if notify and self.on_change is not None:
            try:
                self.on_change()
            except Exception:   # noqa: BLE001
                log.exception("목표 덱 고정 표시 갱신 실패")

    # ------------------------------------------------------------------ 트레이 하위 메뉴
    def add_menu(self, menu: QMenu) -> QMenu:
        """"목표 덱 고정 ▸ (1/2/3/고정 해제)". 열 때마다 지금 목표 덱으로 다시 채운다."""
        sub = menu.addMenu(MENU_TEXT)
        sub.aboutToShow.connect(lambda: self.fill_menu(sub))
        self.fill_menu(sub)
        return sub

    def fill_menu(self, sub: QMenu) -> None:
        sub.clear()
        choices = self.choices()
        if not choices:
            sub.addAction("(목표 덱 없음 — 추천을 기다리는 중)").setEnabled(False)
        for c in choices:
            action = sub.addAction(c.label(False) if c.index else f"{c.name} (목록 밖)")
            action.setCheckable(True)
            action.setChecked(c.comp_id == self.pinned)
            action.triggered.connect(lambda _=False, cid=c.comp_id, nm=c.name: self.click(cid, nm))
        sub.addSeparator()
        off = sub.addAction(UNPIN_TEXT)
        off.setEnabled(self.pinned is not None)
        off.triggered.connect(self.unpin)


class DeckButtons(QWidget):
    """목표 덱 버튼 줄(덱마다 하나). 고정한 덱은 "📌 … 고정"으로 강조한다. 포커스를 가져가지 않는다."""

    def __init__(self, controller: PinController, *, vertical: bool = False, title: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.buttons: list[QPushButton] = []
        self._key: tuple | None = None
        self._layout = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)
        self.title = None
        if title:
            self.title = QLabel(title, self)
            self.title.setStyleSheet("color: #7fd1ff; font-weight: bold; font-size: 11px; background: transparent;")
            self._layout.addWidget(self.title)
        controller.attach(self)

    def set_choices(self, choices: list[DeckChoice], pinned: str | None) -> None:
        key = (tuple(choices), pinned)
        if key == self._key:
            return
        self._key = key
        for b in self.buttons:
            self._layout.removeWidget(b)
            b.deleteLater()
        self.buttons = []
        for c in choices:
            on = c.comp_id == pinned
            b = QPushButton(c.label(on), self)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(f"{c.name}\n{TIP_PINNED if on else TIP_FREE}")
            b.setStyleSheet(PINNED_CSS if on else FREE_CSS)
            b.setProperty("comp_id", c.comp_id)
            b.clicked.connect(lambda _=False, cid=c.comp_id, nm=c.name: self.controller.click(cid, nm))
            self._layout.addWidget(b)
            self.buttons.append(b)
        self.setVisible(bool(choices))
        self.adjustSize()

    def button_for(self, comp_id: str) -> QPushButton | None:
        return next((b for b in self.buttons if b.property("comp_id") == comp_id), None)


class DeckChooser(QWidget):
    """오버레이 옆에 붙는 "목표 덱" 띠(별도의 작은 창). 오버레이가 클릭 통과여도 이 창은 누를 수 있다.

    항상 위 · 테두리 없음 · `WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating`(눌러도 게임이 포커스를 잃지 않는다).
    """

    def __init__(self, controller: PinController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TFT Advisor 목표 덱")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.buttons = DeckButtons(controller, vertical=True, title=f"{PIN_MARK} 목표 덱", parent=self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.buttons)
        self.adjustSize()

    @property
    def has_choices(self) -> bool:
        return bool(self.buttons.buttons)

    def follow(self, overlay: QWidget, top_offset: int | None = None) -> None:
        """오버레이 왼쪽, [목표 덱] 제목 높이(`overlay.chooser_offset()`)에 붙는다. 왼쪽에 자리가 없으면 오른쪽."""
        if top_offset is None:
            fn = getattr(overlay, "chooser_offset", None)
            top_offset = int(fn()) if fn is not None else 28
        self.adjustSize()
        g = overlay.frameGeometry()
        x = g.left() - self.width() - 4
        screen = overlay.screen() or QGuiApplication.primaryScreen()
        left_edge = screen.availableGeometry().left() if screen is not None else 0
        if x < left_edge:
            x = g.right() + 4
        self.move(int(x), int(g.top() + top_offset))


__all__ = ["DeckButtons", "DeckChooser", "MENU_TEXT", "PinController", "UNPIN_TEXT"]
