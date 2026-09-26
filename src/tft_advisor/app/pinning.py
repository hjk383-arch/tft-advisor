"""목표 덱 고정(사용자 클릭) — Qt 없이 쓰는 부분. 31 보고(`_workspace/31_pin_target_deck.md`).

흐름
- 사용자가 목표 덱 버튼(오버레이 옆 "목표 덱" 띠 · 인식 확인 창 · 트레이 "목표 덱 고정 ▸")을 누른다(UI 스레드)
- `toggle_target(현재 고정, 누른 덱)` → 같은 덱이면 해제(None), 아니면 그 덱
- `LiveLoop.request_pin()` → 세션에 기록(`session.json`, 새 판에서 비워진다) → 추천 스레드에 고정 값을 넘기고
  마지막 상태로 **즉시 다시 추천**(새 캡처 없음)
- 추천 스레드는 advise 직전에 `apply_pin(advisor, comp_id)`로 advisor에 반영한다(advisor는 그 스레드 전용)

`apply_pin`은 어댑터다: advisor에 `set_pinned_comp`가 없으면(구버전·가짜 advisor) 로그만 남기고 아무것도 하지 않는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..contracts import GameState, Recommendation, ScreenMode
from .session import KEEP_MODES

log = logging.getLogger(__name__)

PIN_MARK = "📌"
PINNED_LABEL = f"{PIN_MARK} 사용자 고정"
PENDING_LABEL = f"{PIN_MARK} 고정 적용 중…"
SHORT_NAME_MAX = 8

_warned: set[str] = set()


def apply_pin(advisor: Any, comp_id: str | None) -> bool:
    """advisor에 고정 덱을 알린다(**추천 스레드에서** 부른다). 지원하지 않으면 False(로그 1회)."""
    fn = getattr(advisor, "set_pinned_comp", None)
    if fn is None:
        name = type(advisor).__name__
        if name not in _warned:
            _warned.add(name)
            log.warning("목표 덱 고정: %s에 set_pinned_comp가 없어 고정이 추천에 반영되지 않습니다", name)
        return False
    try:
        fn(comp_id)
    except Exception:   # 고정 실패로 추천이 멈추지 않는다
        log.exception("목표 덱 고정 반영 실패 (comp_id=%s)", comp_id)
        return False
    return True


def readvise_state(state: GameState) -> GameState:
    """고정/해제 직후 다시 추천할 상태. 전투·캐러셀 등 '직전 추천 유지' 화면이면 준비 단계로 계산한다
    (advisor 계약상 그 화면에서는 새 목표 덱을 만들지 않으므로). 표시는 루프가 지금 화면 기준으로 거른다."""
    if state.screen_mode in KEEP_MODES or state.screen_mode == ScreenMode.CAROUSEL:
        return state.model_copy(update={"screen_mode": ScreenMode.PLANNING})
    return state


def toggle_target(current: str | None, clicked: str | None) -> str | None:
    """누른 덱이 지금 고정한 덱이면 해제(None), 아니면 그 덱을 고정."""
    if clicked is None or clicked == current:
        return None
    return clicked


def rec_pinned(rec: Recommendation | None) -> str | None:
    """추천이 어느 덱으로 고정돼 계산됐는지(계약 필드가 없던 추천도 읽는다)."""
    return getattr(rec, "pinned_comp_id", None) if rec is not None else None


def short_name(name: str, limit: int = SHORT_NAME_MAX) -> str:
    name = (name or "").strip()
    return name if len(name) <= limit else name[:limit].rstrip() + "…"


@dataclass(frozen=True)
class DeckChoice:
    """버튼 하나: 목표 덱 순번(1~3, 목록에 없는 고정 덱은 0)·ID·이름."""

    index: int
    comp_id: str
    name: str

    def label(self, pinned: bool) -> str:
        head = f"{self.index} " if self.index else ""
        text = f"{head}{short_name(self.name)}"
        return f"{PIN_MARK} {text} 고정" if pinned else text


def deck_choices(rec: Recommendation | None, limit: int = 3, pinned_id: str | None = None,
                 pinned_name: str | None = None) -> list[DeckChoice]:
    """표시 중인 목표 덱 → 버튼 목록. 고정한 덱이 목록에 없으면 맨 뒤에 붙인다(해제할 수 있게)."""
    out: list[DeckChoice] = []
    comps = list(rec.target_comps[:limit]) if rec is not None else []
    for i, comp in enumerate(comps, start=1):
        out.append(DeckChoice(i, comp.comp_id, comp.name))
    if pinned_id and all(c.comp_id != pinned_id for c in out):
        out.append(DeckChoice(0, pinned_id, pinned_name or pinned_id))
    return out


def header_label(pinned_id: str | None, rec: Recommendation | None) -> str | None:
    """[목표 덱] 머리 표시. 고정했고 추천이 그 기준이면 "📌 사용자 고정", 아직 반영 전이면 "적용 중"."""
    applied = rec_pinned(rec)
    if pinned_id is None:
        return PINNED_LABEL if applied else None
    return PINNED_LABEL if applied == pinned_id else PENDING_LABEL


__all__ = [
    "DeckChoice", "PENDING_LABEL", "PINNED_LABEL", "PIN_MARK", "apply_pin", "deck_choices", "header_label",
    "readvise_state", "rec_pinned", "short_name", "toggle_target",
]
