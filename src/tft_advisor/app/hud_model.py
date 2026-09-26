"""오버레이 HUD의 표시 모델(Qt 없음) — 고정 크기·고정 섹션 배치(33 보고 `_workspace/33_hud_fixed_and_icons.md`).

사용자 요청: "HUD 크기를 좀 늘려줘 — 자꾸 라인이 늘어났다 줄었다 해서 헷갈려."

규칙
- 창 크기는 설정값(`[overlay] width`/`height`)으로 고정. 내용이 바뀌어도 크기·섹션 위치가 그대로다.
- 섹션 순서와 줄 수가 고정이다: 머리(3줄) → [목표 덱](덱마다 머리 줄 + 유닛 아이콘 줄 + `lines_comp`줄) → [보드 배치]
  → [상점] → [증강 선택] → [아이템]. 넘치면 마지막 줄을 "… 외 N줄"로, 비면 자리 표시 한 줄("(없음)" 등) + 빈 줄로 채운다.
- 긴 줄은 줄바꿈하지 않고 말줄임(…)한다(그리는 쪽, `hud_view`).
- 전체가 높이를 넘으면 우선순위가 낮은 섹션부터 줄 수를 줄인다: 증강 → 아이템 → 상점 → 보드 배치 → 목표 덱(`fit_budgets`).
  아래 섹션을 잘라내지 않는다. 줄 수는 설정·글꼴 크기·창 높이로만 정해지므로 갱신 사이에 바뀌지 않는다.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from ..contracts import GameState, Recommendation, TargetComp
from ..unit_status import units_note
from .names import NameBook
from .report import (
    KeptInfo, augment_lines, board_plan_lines, comp_lines, item_lines, jev_label, shop_lines, state_line,
)

ACCENT = "#7fd1ff"
GOOD = "#8ce99a"
WARN = "#ffd43b"
DIM = "#9aa4b2"
TEXT = "#e8ecf1"
STALE = "#6b7380"
"""직전 보드 배치(stale) — DIM보다 더 흐리게."""

COST_COLORS = {1: "#9aa4b2", 2: "#1db954", 3: "#2f8cff", 4: "#c440da", 5: "#ffb93b"}
"""메타 사이트처럼 코스트별 테두리 색(1 회색 · 2 초록 · 3 파랑 · 4 보라 · 5 금색)."""

SECTION_KEYS = ("comps", "board", "shop", "augment", "item")
SECTION_TITLES = {"comps": "목표 덱", "board": "보드 배치", "shop": "상점", "augment": "증강 선택", "item": "아이템"}
SHRINK_ORDER = ("augment", "item", "shop", "board", "comps")
"""높이가 모자랄 때 줄 수를 줄이는 순서(우선순위 낮은 것부터). 목표 덱 > 보드 배치 > 상점 > 아이템 > 증강."""
HEADER_ROWS = 3
SECTION_GAP = 0.4
"""섹션 제목 앞 빈칸(줄 높이 배수)."""


@dataclass(frozen=True)
class IconCell:
    """최종 덱 유닛 아이콘 1칸."""

    unit_id: str
    name: str
    cost: int | None = None
    owned: bool | None = None       # True 보유 · False 부족 · None 모름(보드 미인식)
    carry: bool = False
    star: int | None = None         # 목표 성급(3 이상일 때만 표시 — 리롤 덱)

    def text(self) -> str:
        mark = "✓" if self.owned else ""
        tail = "(캐리)" if self.carry else ""
        star = f"★{self.star}" if self.star and self.star >= 3 else ""
        return f"{self.name}{star}{mark}{tail}"


@dataclass(frozen=True)
class Row:
    """HUD 한 줄. kind: text | head(굵게) | title(TFT Advisor) | section(섹션 제목) | icons | blank."""

    kind: str
    text: str = ""
    color: str = TEXT
    note: str | None = None                 # section: 제목 옆 흐린 글
    cells: tuple[IconCell, ...] = ()        # icons
    italic: bool = False

    def html(self) -> str:
        """텍스트 표현(테스트·콘솔 대조용, 예전 QLabel 본문과 같은 모양)."""
        if self.kind == "title":
            return f"<b style='color:{ACCENT}'>{html.escape(self.text)}</b>"
        if self.kind == "section":
            tail = f" <span style='color:{DIM}'>({html.escape(self.note)})</span>" if self.note else ""
            return f"<b style='color:{ACCENT}'>[{html.escape(self.text)}]</b>{tail}"
        if self.kind == "head":
            return f"<b>{line_html(self.text)}</b>"
        if self.kind == "icons":
            return f"<span style='color:{DIM}'>{line_html(icons_text(self.cells))}</span>"
        if self.kind == "blank":
            return ""
        body = line_html(self.text)
        if self.italic:
            body = f"<i>{body}</i>"
        return f"<span style='color:{self.color}'>{body}</span>"


def icons_text(cells: tuple[IconCell, ...]) -> str:
    return "최종: " + " · ".join(c.text() for c in cells)


def plain(text: str) -> str:
    """report 줄 → 그릴 글자. 들여쓰기는 살리고 두 칸 공백 구분은 " · "로(예전 `_line`과 같다)."""
    stripped = text.lstrip(" ")
    indent = " " * (len(text) - len(stripped))
    return indent + stripped.replace("  ", " · ")


def line_html(text: str) -> str:
    stripped = text.lstrip(" ")
    indent = "&nbsp;" * (len(text) - len(stripped)) * 2
    return indent + html.escape(stripped).replace("  ", " &middot; ")


@dataclass
class DeckBlock:
    """목표 덱 1자리: 머리 줄 + 아이콘 줄 + 설명 줄들(자리는 비어 있어도 유지)."""

    head: Row
    icons: Row | None
    details: list[Row] = field(default_factory=list)
    comp_id: str | None = None


@dataclass
class Section:
    key: str
    title: str
    note: str | None = None
    rows: list[Row] = field(default_factory=list)
    placeholder: str = "(없음)"
    decks: list[DeckBlock] = field(default_factory=list)   # comps 섹션만


@dataclass
class Budgets:
    """섹션별 줄 수(목표 덱은 덱 1개당 설명 줄 수). `fit_budgets`가 높이에 맞춰 줄인다."""

    comps: int = 3
    board: int = 6
    shop: int = 5
    augment: int = 4
    item: int = 4
    icons: bool = True

    @classmethod
    def from_cfg(cls, cfg: Any) -> Budgets:
        return cls(comps=cfg.lines_comp, board=cfg.lines_board, shop=cfg.lines_shop, augment=cfg.lines_augment,
                   item=cfg.lines_item, icons=bool(cfg.unit_icons))

    def get(self, key: str) -> int:
        return int(getattr(self, key))


@dataclass
class HudModel:
    header: list[Row]
    sections: list[Section]
    deck_slots: int = 3

    def section(self, key: str) -> Section:
        return next(s for s in self.sections if s.key == key)


def model_height(budgets: Budgets, deck_slots: int, line_h: float, icon_h: float) -> float:
    """고정 배치의 전체 높이(px, 여백 제외). 내용과 무관하다 — 그래서 갱신 사이에 배치가 움직이지 않는다."""
    h = HEADER_ROWS * line_h
    for key in SECTION_KEYS:
        h += (SECTION_GAP + 1) * line_h   # 빈칸 + 제목
        if key == "comps":
            per = line_h * (1 + budgets.comps) + (icon_h if budgets.icons else 0)
            h += per * deck_slots
        else:
            h += budgets.get(key) * line_h
    return h


def fit_budgets(budgets: Budgets, deck_slots: int, avail: float, line_h: float, icon_h: float) -> Budgets:
    """높이가 모자라면 `SHRINK_ORDER` 순서로 한 섹션씩 1줄까지 줄인다. 그래도 넘치면 아이콘 줄을 글자 없이 뺀다."""
    b = Budgets(**vars(budgets))
    for key in SHRINK_ORDER:
        while model_height(b, deck_slots, line_h, icon_h) > avail and b.get(key) > 1:
            setattr(b, key, b.get(key) - 1)
    if model_height(b, deck_slots, line_h, icon_h) > avail and b.icons:
        b.icons = False
    return b


def fit_rows(rows: list[Row], budget: int, placeholder: str) -> list[Row]:
    """섹션 줄을 정확히 `budget`줄로: 넘치면 마지막 줄 "… 외 N줄", 모자라면 빈 줄(비었으면 자리 표시)."""
    if not rows:
        rows = [Row("text", placeholder, DIM, italic=True)]
    if len(rows) > budget:
        hidden = len(rows) - budget + 1
        rows = rows[:budget - 1] + [Row("text", f"… 외 {hidden}줄", DIM)] if budget > 1 else \
            [Row(rows[0].kind, rows[0].text + f"  … 외 {len(rows) - 1}줄", rows[0].color)]
    return rows + [Row("blank")] * (budget - len(rows))


# ---------------------------------------------------------------------------
# 추천 → 모델
# ---------------------------------------------------------------------------


def _cost(names: NameBook, unit_id: str) -> int | None:
    try:
        row = names.static.get("champions", unit_id)
    except Exception:   # noqa: BLE001
        row = None
    cost = (row or {}).get("cost")
    return int(cost) if isinstance(cost, (int, float)) else None


def deck_cells(comp: TargetComp, names: NameBook) -> tuple[IconCell, ...]:
    """목표 덱의 최종 유닛 → 아이콘 칸(통계 순서). 보유/부족은 advisor의 `owned_units`/`missing_units`."""
    known = bool(comp.owned_units or comp.missing_units)
    owned = set(comp.owned_units)
    units = list(getattr(comp, "final_board", None) or [])
    out = []
    for u in units:
        out.append(IconCell(unit_id=u.id, name=names.name(u.id), cost=_cost(names, u.id),
                            owned=(u.id in owned) if known else None, carry=u.id == comp.carry,
                            star=u.star if u.star and u.star >= 3 else None))   # ★3 목표(리롤)만 표시 — ★2는 거의 전부라 소음
    return tuple(out)


def build_model(state: GameState | None, rec: Recommendation | None, names: NameBook, *,
                kept: KeptInfo | None = None, pin_note: str | None = None, deck_slots: int = 3,
                threshold: float = 0.6, plan_color=None) -> HudModel:
    """상태·추천 → 고정 배치 모델(섹션은 늘 전부 있다)."""
    header = [Row("title", "TFT Advisor")]
    header.append(Row("text", state_line(state), DIM) if state is not None else Row("blank"))
    if rec is None:
        header.append(Row("text", "추천 대기 중…" if state is None else "이 화면에서는 새 추천이 없습니다", DIM,
                          italic=True))
    elif kept is not None:
        header.append(Row("text", kept.note(), WARN))
    else:
        header.append(Row("blank"))

    sections: list[Section] = []
    # [목표 덱]
    note = None
    if rec is not None:
        note = jev_label(rec) + (f" · {kept.label}" if kept is not None else "") + (f" · {pin_note}" if pin_note else "")
    comps = Section("comps", SECTION_TITLES["comps"], note)
    shown = list(rec.target_comps[:deck_slots]) if rec is not None else []
    unote = units_note(state, threshold) if state is not None else None
    for i in range(deck_slots):
        if i < len(shown):
            comp = shown[i]
            lines = comp_lines(comp, i + 1, names, compact=True, units_note=unote)
            cells = deck_cells(comp, names)
            icons = Row("icons", cells=cells) if cells else Row("text", "   최종 유닛 정보 없음", DIM)
            comps.decks.append(DeckBlock(Row("head", lines[0]), icons,
                                         [Row("text", ln, DIM) for ln in lines[1:]], comp.comp_id))
        else:
            empty = i == 0
            text = ("(후보 없음 — 인식 정보 부족)" if rec is not None else "(추천 대기)") if empty else ""
            comps.decks.append(DeckBlock(Row("text", text, DIM, italic=True) if text else Row("blank"), None, []))
    sections.append(comps)

    # [보드 배치]
    board = Section("board", SECTION_TITLES["board"], placeholder="(보드 배치 추천 없음)")
    plan = rec.board_plan if rec is not None else None
    if plan is not None:
        basis = next((c.name for c in rec.target_comps if c.comp_id == plan.comp_id), None)
        stale = bool(getattr(plan, "stale", False))
        board.note = "직전" if stale else None
        for ln in board_plan_lines(plan, names, comp_name=basis):
            color = plan_color(ln, plan, stale) if plan_color is not None else DIM
            board.rows.append(Row("text", ln, color))
    sections.append(board)

    # [상점]
    shop = Section("shop", SECTION_TITLES["shop"])
    if rec is not None and rec.shop:
        shop.note = kept.shop_label if kept is not None else None
        fresh = kept is None or kept.shop_fresh
        for line in shop_lines(rec, names):
            shop.rows.append(Row("text", line, GOOD if "[구매]" in line and fresh else DIM))
    elif kept is not None and (kept.bought or kept.changed):
        shop.note = f"{kept.shop_label} — 남은 추천 칸 없음"
    sections.append(shop)

    # [증강 선택]
    aug = Section("augment", SECTION_TITLES["augment"], placeholder="(증강 선택 화면에서 표시합니다)")
    if rec is not None and rec.augment is not None:
        aug.rows = [Row("text", ln, WARN if ln.startswith("★") else DIM) for ln in augment_lines(rec, names)]
    sections.append(aug)

    # [아이템]
    item = Section("item", SECTION_TITLES["item"], placeholder="(아이템 추천 없음)")
    if rec is not None and rec.item is not None and (rec.item.suggestions or rec.item.hold):
        item.rows = [Row("text", ln, DIM) for ln in item_lines(rec, names)]
    if rec is not None and rec.component_priority:
        item.rows.append(Row("text", "재료: " + names.joined(rec.component_priority, limit=5), DIM))
    sections.append(item)
    return HudModel(header=header, sections=sections, deck_slots=deck_slots)


def layout_rows(model: HudModel, budgets: Budgets) -> list[tuple[str, Row]]:
    """모델 + 줄 수 → 그릴 줄 목록 (섹션 키, 줄). 줄 수는 내용과 무관하게 `model_height`와 같다."""
    out: list[tuple[str, Row]] = [("header", r) for r in model.header[:HEADER_ROWS]]
    out += [("header", Row("blank"))] * (HEADER_ROWS - len(model.header))
    for sec in model.sections:
        out.append((sec.key, Row("gap")))
        out.append((sec.key, Row("section", sec.title, note=sec.note)))
        if sec.key == "comps":
            for deck in sec.decks:
                out.append((sec.key, deck.head))
                details = list(deck.details)
                if budgets.icons:
                    icons = deck.icons if deck.icons is not None else Row("iconspace")
                    if icons.kind != "icons":   # 아이콘 칸이 없는 덱(정보 없음): 같은 높이의 글자 줄
                        icons = Row("icontext", icons.text, icons.color)
                    out.append((sec.key, icons))
                elif deck.icons is not None and deck.icons.kind == "icons":
                    details = [Row("text", "   " + icons_text(deck.icons.cells), DIM), *details]
                elif deck.icons is not None:
                    details = [deck.icons, *details]
                if deck.head.kind == "head":
                    details = fit_rows(details, budgets.comps, "")
                else:
                    details = [Row("blank")] * budgets.comps
                out += [(sec.key, r) for r in details]
        else:
            out += [(sec.key, r) for r in fit_rows(sec.rows, budgets.get(sec.key), sec.placeholder)]
    return out


def model_html(model: HudModel, budgets: Budgets) -> str:
    """보이는 줄의 텍스트 표현(예전 본문 QLabel과 같은 모양 — 테스트가 문자열로 확인한다)."""
    parts = []
    for _, row in layout_rows(model, budgets):
        if row.kind in ("gap", "blank", "iconspace"):
            continue
        parts.append(Row("text", row.text, row.color).html() if row.kind == "icontext" else row.html())
    return "<br>".join(parts)


__all__ = [
    "ACCENT", "Budgets", "COST_COLORS", "DIM", "DeckBlock", "GOOD", "HudModel", "IconCell", "Row", "SHRINK_ORDER",
    "STALE", "Section", "TEXT", "WARN", "build_model", "deck_cells", "fit_budgets", "fit_rows", "layout_rows",
    "model_height", "model_html", "plain",
]
