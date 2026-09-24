"""보유 유닛을 "얼마나 아는가" — advisor 근거 문구와 UI 표시가 같은 판단을 쓰도록 한 곳에 모은다.

`GameState.board`/`bench`는 세 경로로 채워진다(2026-09-23, `_workspace/17_purchase_tracking.md`).

| 상태 | 뜻 | 표시 |
|---|---|---|
| `VISION` | vision이 정체까지 읽었다 | 별도 안내 없음 |
| `TRACKED` | 상점 구매 추적(+수동 입력)으로 안다 | "구매 추적 기준" |
| `PARTIAL` | 추적값은 있으나 신뢰도가 임계값 미만이다(설명되지 않은 거래가 쌓였거나 이름 미상이 많다) | "부분 확인" |
| `SEEN` | vision이 **자리·성급·장착 아이템**은 읽었으나 챔피언 이름은 하나도 모른다 | "화면 인식 N기 · 이름 미상" |
| `UNKNOWN` | 아무것도 모른다(보드를 아예 읽지 못했다) | "보드 미인식" |

`SEEN`은 `--screenshot`(한 장짜리 입력이라 구매 기록이 없다)과 실시간 초반(장부가 아직 비었다)에서 나온다.
**보드는 읽혔다** — 장착 아이템은 이미 `items.equipped`로 추천에 쓰이고, 챔피언 이름만 빠져 있다.
그래서 "보드 미인식"이라고 하면 사실과 다르다.

`advisor`(scoring)와 `app`(report/overlay) 양쪽이 import하므로 두 패키지 어느 쪽에도 두지 않는다
(`patch_version.py`와 같은 이유).
"""
from __future__ import annotations

from enum import StrEnum

from .contracts import UNKNOWN_UNIT_ID, FieldSource, GameState

DEFAULT_THRESHOLD = 0.6
"""`[vision] state_min_confidence` 기본값. 호출자가 설정값을 넘기는 것이 원칙이다."""


class UnitsKnowledge(StrEnum):
    UNKNOWN = "unknown"
    SEEN = "seen"
    PARTIAL = "partial"
    TRACKED = "tracked"
    VISION = "vision"


def unknown_unit_count(state: GameState) -> int:
    """자리는 보이지만 정체를 모르는 유닛 수(`UNKNOWN_UNIT_ID`)."""
    return sum(1 for u in (state.board or []) + (state.bench or []) if u.id == UNKNOWN_UNIT_ID)


def known_unit_count(state: GameState) -> int:
    """정체를 아는 유닛 수."""
    return sum(1 for u in (state.board or []) + (state.bench or []) if u.id != UNKNOWN_UNIT_ID)


def _vision_named(state: GameState) -> bool:
    """이름을 vision이 붙였다(장부가 아니다)."""
    return state.field_source.get("board") == FieldSource.VISION


def units_knowledge(state: GameState, threshold: float = DEFAULT_THRESHOLD) -> UnitsKnowledge:
    """보유 유닛을 얼마나 아는지 판정한다. advisor의 "안다" 기준(§4.3b)과 같은 조건을 쓴다."""
    if state.board is None and state.bench is None:
        return UnitsKnowledge.UNKNOWN
    reliable = (state.board is not None and state.bench is not None
                and state.is_reliable("board", threshold) and state.is_reliable("bench", threshold))
    if not reliable:
        if known_unit_count(state):
            return UnitsKnowledge.PARTIAL
        # 자리는 보이는데 이름을 하나도 모른다 = vision은 읽었고 장부만 비었다(스크린샷·판 초반)
        return UnitsKnowledge.SEEN if unknown_unit_count(state) else UnitsKnowledge.UNKNOWN
    if state.field_source.get("board", FieldSource.VISION) == FieldSource.VISION:
        return UnitsKnowledge.VISION
    return UnitsKnowledge.TRACKED


def units_reason(state: GameState, threshold: float = DEFAULT_THRESHOLD) -> str | None:
    """`TargetComp.reasons`에 넣을 한 줄(없으면 None). 합쇼체."""
    kind = units_knowledge(state, threshold)
    if kind is UnitsKnowledge.VISION:
        return None
    unknown = unknown_unit_count(state)
    if kind is UnitsKnowledge.UNKNOWN:
        return "보드 미인식: 보유/부족 유닛은 구매 추적·수동 입력으로 표시됩니다"
    if kind is UnitsKnowledge.SEEN:
        return f"보드 {unknown}기·장착 아이템은 인식했습니다: 챔피언 이름은 구매 추적·수동 입력으로 표시됩니다"
    if kind is UnitsKnowledge.PARTIAL:
        tail = f", 이름 미상 {unknown}기" if unknown else ""
        if _vision_named(state):
            return f"보유 유닛 부분 확인: 화면에서 이름을 확인한 유닛 {known_unit_count(state)}기{tail} — 수동 확인을 권합니다"
        return f"보유 유닛 부분 확인: 구매 추적이 불확실합니다{tail} — 수동 확인을 권합니다"
    return "보유 유닛: 구매 추적 기준" + (f"(이름 미상 {unknown}기)" if unknown else "")


def units_note(state: GameState, threshold: float = DEFAULT_THRESHOLD) -> str | None:
    """목표 덱 줄의 "보유/부족" 뒤에 붙는 꼬리말(없으면 None)."""
    kind = units_knowledge(state, threshold)
    unknown = unknown_unit_count(state)
    if kind is UnitsKnowledge.UNKNOWN:
        return "보드 미인식(구매 추적 대기 · 수동 입력 가능)"
    if kind is UnitsKnowledge.SEEN:
        return f"화면 인식 {unknown}기 · 이름 미상(구매 추적 대기 · 수동 입력 가능)"
    if kind is UnitsKnowledge.PARTIAL:
        known = known_unit_count(state)
        tail = f" · 이름 미상 {unknown}기" if unknown else ""
        how = "화면 인식" if _vision_named(state) else "구매 추적"
        return f"부분 확인 — {how} {known}기{tail}(추천에는 쓰지 않습니다)"
    if kind is UnitsKnowledge.TRACKED:
        return "구매 추적" + (f" · 이름 미상 {unknown}기" if unknown else "")
    return f"이름 미상 {unknown}기" if unknown else None


__all__ = ["DEFAULT_THRESHOLD", "UnitsKnowledge", "known_unit_count", "units_knowledge", "units_note",
           "units_reason", "unknown_unit_count"]
