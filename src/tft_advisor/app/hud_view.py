"""오버레이 HUD 그리기(PySide6) — 고정 배치 모델(`hud_model`)을 직접 그린다. 33 보고.

QLabel 리치 텍스트는 줄바꿈으로 높이가 바뀌고 아이콘 테두리·투명도를 못 다뤄서, 줄마다 정해진 자리에 그리는 위젯으로 바꿨다.
- 글자 줄: 창 폭을 넘으면 말줄임(…). 줄바꿈하지 않는다.
- 아이콘 줄: 목표 덱 최종 유닛. 코스트 색 테두리, 부족 유닛은 흐리게, 보유 유닛은 ✓, 캐리는 금색 굵은 테두리 + "C",
  목표 성급(★2·★3)은 아래쪽 작은 글자. 아이콘 파일이 없으면 그 칸은 이름 첫 두 글자.
- `IconBook`은 크기별로 줄인 그림을 캐시한다(그릴 때마다 파일을 읽지 않는다). 없던 파일은 `refresh()` 전까지 다시 찾지 않는다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .hud_model import (
    ACCENT, COST_COLORS, DIM, GOOD, SECTION_GAP, TEXT, WARN, Budgets, HudModel, IconCell, Row, fit_budgets,
    layout_rows, model_height, model_html, plain,
)

log = logging.getLogger(__name__)

FONT_FAMILIES = ["Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", "sans-serif"]
BACKGROUND = QColor(12, 14, 20, 225)
MISSING_OPACITY = 0.5
STALE_OPACITY = 0.45
"""직전 보드 배치(stale) 아이콘 투명도."""
ITEM_DOT = "#f08c00"
"""유닛이 든 아이템 표시 점 색."""
"""부족 유닛 아이콘 투명도(보유는 1.0)."""


class IconBook:
    """챔피언 아이콘(`data/templates/{set}/champions/{apiName}.png`) → 크기별 QPixmap 캐시."""

    def __init__(self, directory: Path | None) -> None:
        self.directory = Path(directory) if directory is not None else None
        self._cache: dict[tuple[str, int], QPixmap | None] = {}
        self.loads = 0

    def refresh(self) -> None:
        """새로 받은 아이콘을 쓰도록 캐시를 비운다(첫 실행 내려받기가 끝났을 때)."""
        self._cache.clear()

    def available(self) -> bool:
        d = self.directory
        return d is not None and d.is_dir() and any(d.glob("*.png"))

    def get(self, unit_id: str, size: int) -> QPixmap | None:
        key = (unit_id, size)
        if key in self._cache:
            return self._cache[key]
        pix = None
        if self.directory is not None:
            path = self.directory / f"{unit_id}.png"
            if path.is_file():
                raw = QPixmap(str(path))
                if not raw.isNull():
                    self.loads += 1
                    pix = raw.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
        self._cache[key] = pix
        return pix


def default_icon_dir(set_number: int) -> Path:
    from ..vision.templates import template_dir

    return template_dir(set_number, "champions")


class HudView(QWidget):
    """고정 배치 HUD. `set_model()`로 내용을 바꾸고, 크기는 부모(오버레이)가 정한다."""

    def __init__(self, cfg, *, icons: IconBook | None = None, deck_slots: int = 3,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.scale = cfg.scale
        self.icons = icons or IconBook(None)
        self.deck_slots = deck_slots
        self.model: HudModel | None = None
        self.base = Budgets.from_cfg(cfg)
        self.budgets = self.base
        self.font_px = max(8, int(12 * self.scale))
        self.font = QFont(FONT_FAMILIES[0])
        self.font.setFamilies(FONT_FAMILIES)
        self.font.setPixelSize(self.font_px)
        self.bold = QFont(self.font)
        self.bold.setBold(True)
        fm = QFontMetrics(self.font)
        self.line_h = fm.height() + max(1, int(2 * self.scale))
        self.icon_px = max(12, int(cfg.icon_size * self.scale))
        self.icon_h = self.icon_px + max(2, int(4 * self.scale))
        self.pad = int(8 * self.scale)
        self.section_y: dict[str, int] = {}
        self.deck_y: list[int] = []
        self.paints = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    # ------------------------------------------------------------------ 배치
    def content_height(self, budgets: Budgets | None = None) -> int:
        b = budgets or self.budgets
        return int(round(model_height(b, self.deck_slots, self.line_h, self.icon_h) + 2 * self.pad))

    def fit(self, height: int) -> Budgets:
        """주어진 높이(px)에 맞춘 줄 수. 높이가 같으면 늘 같은 결과(내용과 무관)."""
        avail = height - 2 * self.pad
        self.budgets = fit_budgets(self.base, self.deck_slots, avail, self.line_h, self.icon_h)
        return self.budgets

    def resizeEvent(self, event) -> None:   # noqa: N802 — Qt 이름
        super().resizeEvent(event)
        self.fit(self.height())

    def set_model(self, model: HudModel) -> None:
        self.model = model
        self.update()

    def text(self) -> str:
        """보이는 줄의 텍스트(HTML 모양, 말줄임 전 원문). 테스트·로그용."""
        return model_html(self.model, self.budgets) if self.model is not None else ""

    # ------------------------------------------------------------------ 그리기
    def paintEvent(self, event) -> None:   # noqa: N802
        self.paints += 1
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self.rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BACKGROUND)
        radius = 8 * self.scale
        p.drawRoundedRect(QRectF(r), radius, radius)
        if self.model is None:
            p.end()
            return
        x0, width = self.pad, r.width() - 2 * self.pad
        y = float(self.pad)
        self.section_y, self.deck_y = {}, []
        for key, row in layout_rows(self.model, self.budgets):
            if row.kind == "gap":
                y += SECTION_GAP * self.line_h
                continue
            if row.kind == "section":
                self.section_y[key] = int(y)
            if row.kind in ("icons", "iconspace", "icontext"):
                if row.kind == "icons":
                    self._icons(p, row.cells, x0, y, width)
                elif row.kind == "icontext":
                    self._text(p, Row("text", row.text, row.color, italic=row.italic), x0,
                               y + (self.icon_h - self.line_h) / 2, width)
                y += self.icon_h
                continue
            if key == "comps" and row.kind == "head":
                self.deck_y.append(int(y))
            self._text(p, row, x0, y, width)
            y += self.line_h
        p.end()

    def _text(self, p: QPainter, row: Row, x: float, y: float, width: float) -> None:
        if row.kind == "blank":
            return
        fm_font = self.bold if row.kind in ("title", "section", "head") else self.font
        if row.kind == "section":
            title = f"[{row.text}]"
            p.setFont(self.bold)
            p.setPen(QColor(ACCENT))
            tw = QFontMetrics(self.bold).horizontalAdvance(title)
            self._draw(p, title, x, y, width, self.bold)
            if row.note:
                p.setFont(self.font)
                p.setPen(QColor(DIM))
                self._draw(p, f"({row.note})", x + tw + 6 * self.scale, y, width - tw - 6 * self.scale, self.font)
            return
        color = ACCENT if row.kind == "title" else (TEXT if row.kind == "head" else row.color)
        font = QFont(fm_font)
        if row.italic:
            font.setItalic(True)
        p.setFont(font)
        p.setPen(QColor(color))
        self._draw(p, plain(row.text), x, y, width, font)

    def _draw(self, p: QPainter, text: str, x: float, y: float, width: float, font: QFont) -> None:
        fm = QFontMetrics(font)
        shown = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(max(0, width)))
        p.drawText(QRectF(x, y, width, self.line_h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   shown)

    def _icons(self, p: QPainter, cells: tuple[IconCell, ...], x: float, y: float, width: float) -> None:
        s = self.icon_px
        gap = max(2, int(3 * self.scale))
        top = y + (self.icon_h - s) / 2
        cx = x + 2 * self.scale
        room = int((width + gap) // (s + gap))
        shown = cells if len(cells) <= room else cells[:max(0, room - 1)]
        for cell in shown:
            self._cell(p, cell, cx, top, s)
            cx += s + gap
        if len(shown) < len(cells):
            p.setFont(self.font)
            p.setPen(QColor(DIM))
            p.drawText(QRectF(cx, top, s + 8, s), int(Qt.AlignmentFlag.AlignVCenter), f"+{len(cells) - len(shown)}")

    def _cell(self, p: QPainter, cell: IconCell, x: float, y: float, s: int) -> None:
        rect = QRectF(x, y, s, s)
        p.save()
        base = STALE_OPACITY if cell.dim else 1.0
        p.setOpacity(base * (MISSING_OPACITY if cell.owned is False else 1.0))
        pix = None if cell.unknown else self.icons.get(cell.unit_id, s)
        if pix is not None:
            src = QRectF((pix.width() - s) / 2, (pix.height() - s) / 2, s, s)
            p.drawPixmap(rect, pix, src)
        else:   # 아이콘 없음: 코스트 색 바탕 + 이름 앞 두 글자
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(40, 44, 54))
            p.drawRect(rect)
            small = QFont(self.font)
            small.setPixelSize(max(7, int(s * 0.36)))
            p.setFont(small)
            p.setPen(QColor(TEXT))
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "?" if cell.unknown else cell.name[:2])
        border = QColor(WARN) if cell.carry else QColor(COST_COLORS.get(cell.cost or 0, DIM))
        pen = QPen(border)
        pen.setWidthF(max(1.5, (2.5 if cell.carry else 1.5) * self.scale))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        p.setOpacity(base)
        tiny = QFont(self.bold)
        tiny.setPixelSize(max(7, int(s * 0.34)))
        p.setFont(tiny)
        if cell.carry:
            self._badge(p, QRectF(x, y, s * 0.42, s * 0.42), "C", QColor(WARN), QColor(20, 20, 20))
        if cell.owned:
            self._badge(p, QRectF(x + s * 0.58, y + s * 0.58, s * 0.42, s * 0.42), "✓", QColor(GOOD), QColor(10, 30, 10))
        if cell.star:
            p.setPen(QColor(WARN))
            p.drawText(QRectF(x, y + s * 0.55, s * 0.6, s * 0.45), int(Qt.AlignmentFlag.AlignCenter), f"★{cell.star}")
        if cell.badge:   # "↑" 벤치에서 올릴 유닛
            self._badge(p, QRectF(x + s * 0.58, y, s * 0.42, s * 0.42), cell.badge, QColor(ACCENT), QColor(8, 20, 30))
        if cell.items:   # 든 아이템: 아래쪽 오른편 작은 네모 점(최대 3)
            d = max(3.0, s * 0.14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ITEM_DOT))
            for k in range(min(3, cell.items)):
                p.drawRect(QRectF(x + s - (k + 1) * (d + 1) - 1, y + s - d - 1, d, d))
        p.restore()

    @staticmethod
    def _badge(p: QPainter, rect: QRectF, text: str, bg: QColor, fg: QColor) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawEllipse(rect)
        p.setPen(fg)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)


__all__ = ["HudView", "IconBook", "default_icon_dir"]
