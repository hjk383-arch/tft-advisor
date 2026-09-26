"""보유 유닛을 "얼마나 아는가" — advisor 근거 문구와 UI 표시가 같은 판단을 쓰도록 한 곳에 모은다.

`GameState.board`/`bench`는 세 경로로 채워진다(2026-09-23, `_workspace/17_purchase_tracking.md`).

| 상태 | 뜻 | 표시 |
|---|---|---|
| `VISION` | vision이 정체까지 읽었다 | 별도 안내 없음 |
| `TRACKED` | 상점 구매 추적(+수동 입력)으로 안다 | "구매 추적 기준" |
| `PARTIAL` | 보드·벤치 중 한쪽 이상의 신뢰도가 임계값 미만이거나 한쪽을 못 읽었다. 믿을 수 있는 쪽 + 이름을 확인한 유닛(`owned_units`)만 추천에 쓴다. 쓸 유닛이 하나도 없으면(장부가 애매할 때) 추천에 쓰지 않는다 | "부분 확인" |
| `SEEN` | vision이 **자리·성급·장착 아이템**은 읽었으나 챔피언 이름은 하나도 모른다 | "화면 인식 N기 · 이름 미상" |
| `UNKNOWN` | 아무것도 모른다(보드를 아예 읽지 못했다) | "보드 미인식" |

`SEEN`은 `--screenshot`(한 장짜리 입력이라 구매 기록이 없다)과 실시간 초반(장부가 아직 비었다)에서 나온다.
**보드는 읽혔다** — 장착 아이템은 이미 `items.equipped`로 추천에 쓰이고, 챔피언 이름만 빠져 있다.
그래서 "보드 미인식"이라고 하면 사실과 다르다.

`advisor`(scoring)와 `app`(report/overlay) 양쪽이 import하므로 두 패키지 어느 쪽에도 두지 않는다
(`patch_version.py`와 같은 이유).

**보드·벤치는 따로 믿는다**(2026-09-23 사용자 결정, `_workspace/21_board_trust.md`). `owned_units()`가 advisor와
표시 문구가 함께 쓰는 단일 규칙이다.

| 한쪽(보드 또는 벤치)의 필드 신뢰도 | 그쪽에서 쓰는 유닛 |
|---|---|
| ≥ 임계값 | 이름을 아는(`UNKNOWN_UNIT_ID`가 아닌) 유닛 중 유닛 신뢰도 ≥ 임계값 |
| < 임계값, 출처 vision/fixture | 같은 조건 — vision이 칸마다 매긴 이름 신뢰도는 칸 단위로 믿을 수 있다 |
| < 임계값, 출처 tracked/manual | 쓰지 않는다 — 장부의 애매함은 어느 유닛 탓인지 모른다(장부 유닛 신뢰도는 대개 1.0) |

**추정 이름**(2026-09-25, `_workspace/21_board_trust.md` §15): vision이 라이브러리 닮음 하나만으로 붙인 이름은 신뢰도가
0.75로 묶인다(`vision.units.LIB_STRICT_CONF_CAP`, 확인 창의 "(추정)"). 필드 임계값(0.6)은 넘지만 틀릴 수 있다. advisor는
`unit_threshold`(`[board_plan] min_confidence` 0.8)를 넘겨 이런 유닛을 **이름 미상처럼** 뺀다(보유·사본 수·판매·보드 교체).
`UnitOnBoard`에 출처 플래그가 없어 신뢰도만으로 가른다 — 장부 유닛도 자리를 못 정한 칸(0.6)이면 함께 빠진다.
`unit_threshold`를 넘기지 않으면(표시 쪽 기본) 예전과 같다.

쓰지 못한 칸(이름 미상·낮은 신뢰도·믿지 못하는 쪽)이 하나라도 있거나 한쪽을 아예 못 읽었으면 **부분 확인**이다:
advisor는 이름 미상 칸을 특정 챔피언으로 세지 않고, "부족"·"보유 개수"를 확정적으로 말하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import UNKNOWN_UNIT_ID, FieldSource, GameState, UnitOnBoard

DEFAULT_THRESHOLD = 0.6
"""`[vision] state_min_confidence` 기본값. 호출자가 설정값을 넘기는 것이 원칙이다."""

CONFIRMED_NAME_THRESHOLD = 0.8
"""`[board_plan] min_confidence` 기본값(21 §15): 이 미만 유닛 이름은 추정으로 보고 이름 미상처럼 다룬다.
뒷받침 없는 라이브러리 이름의 신뢰도 상한(0.75)보다 높아야 한다. 표시(`units_note`) 기본값으로 쓴다."""


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


# 필드 신뢰도가 낮아도 칸 단위 이름 신뢰도를 믿을 수 있는 출처(모듈 docstring 표)
_PER_UNIT_TRUSTED = frozenset({FieldSource.VISION, FieldSource.FIXTURE})


@dataclass(frozen=True)
class OwnedUnits:
    """advisor가 쓰는 보유 유닛(보드·벤치 따로 판정). 모듈 docstring의 표가 규칙이다."""

    board: tuple[UnitOnBoard, ...] = ()
    bench: tuple[UnitOnBoard, ...] = ()
    board_reliable: bool = False      # 보드 필드 신뢰도 ≥ 임계값
    bench_reliable: bool = False
    board_hidden: int = 0             # 보드에 있으나 쓰지 않은 칸(이름 미상·낮은 신뢰도·믿지 못하는 쪽)
    bench_hidden: int = 0
    board_seen: bool = False          # 보드 필드 값이 있다(None이 아니다)
    bench_seen: bool = False
    board_guessed: int = 0            # hidden 중 이름은 있으나 `unit_threshold` 미만인 칸(추정 이름)
    bench_guessed: int = 0

    @property
    def units(self) -> list[UnitOnBoard]:
        return list(self.board) + list(self.bench)

    @property
    def hidden(self) -> int:
        return self.board_hidden + self.bench_hidden

    @property
    def guessed(self) -> int:
        return self.board_guessed + self.bench_guessed

    @property
    def usable(self) -> bool:
        """보유 유닛 정보를 조금이라도 쓸 수 있다(유닛이 있거나, 한쪽이 '비었음'까지 믿을 수 있다)."""
        return bool(self.board or self.bench) or self.board_reliable or self.bench_reliable

    @property
    def board_complete(self) -> bool:
        """보드의 모든 칸을 안다 — 활성 특성 계산이 가능하다."""
        return self.board_reliable and self.board_hidden == 0

    @property
    def complete(self) -> bool:
        """보드·벤치 전부를 안다 — '부족'·'보유 개수'를 확정적으로 말할 수 있다."""
        return self.board_complete and self.bench_reliable and self.bench_hidden == 0

    def gap_text(self) -> str:
        """부분 확인일 때 무엇을 모르는지(합쇼체 문구 조각)."""
        bits = []
        if self.hidden:
            bits.append(f"이름 미상 {self.hidden}기" + (f"(추정 이름 {self.guessed}기 포함)" if self.guessed else ""))
        if not self.board_seen:
            bits.append("보드 미인식")
        if not self.bench_seen:
            bits.append("벤치 미인식")
        return " · ".join(bits)


def _side(state: GameState, name: str, threshold: float, unit_threshold: float,
          ) -> tuple[tuple[UnitOnBoard, ...], bool, int, int]:
    """(쓰는 유닛, 필드 신뢰, 못 쓴 칸 수, 그중 추정 이름 칸 수)."""
    units = getattr(state, name)
    if units is None:
        return (), False, 0, 0
    reliable = state.is_reliable(name, threshold)
    source = state.field_source.get(name, FieldSource.VISION)
    if reliable or source in _PER_UNIT_TRUSTED:
        named = [u for u in units if u.id != UNKNOWN_UNIT_ID and u.confidence >= threshold]
        used = tuple(u for u in named if u.confidence >= unit_threshold)
        guessed = len(named) - len(used)
    else:
        used, guessed = (), 0
    return used, reliable, len(units) - len(used), guessed


def owned_units(state: GameState, threshold: float = DEFAULT_THRESHOLD,
                unit_threshold: float | None = None) -> OwnedUnits:
    """보유 유닛 중 advisor가 쓸 수 있는 것(보드·벤치를 따로 판정한다).

    unit_threshold: 이름을 **확정**으로 볼 유닛 신뢰도(모듈 docstring "추정 이름"). None이면 `threshold`와 같다.
    """
    ut = max(threshold, unit_threshold if unit_threshold is not None else threshold)
    board, board_rel, board_hidden, board_guessed = _side(state, "board", threshold, ut)
    bench, bench_rel, bench_hidden, bench_guessed = _side(state, "bench", threshold, ut)
    return OwnedUnits(board=board, bench=bench, board_reliable=board_rel, bench_reliable=bench_rel,
                      board_hidden=board_hidden, bench_hidden=bench_hidden,
                      board_seen=state.board is not None, bench_seen=state.bench is not None,
                      board_guessed=board_guessed, bench_guessed=bench_guessed)


def _vision_named(state: GameState) -> bool:
    """이름을 vision이 붙였다(장부가 아니다)."""
    return state.field_source.get("board") == FieldSource.VISION


def units_knowledge(state: GameState, threshold: float = DEFAULT_THRESHOLD,
                    unit_threshold: float | None = None) -> UnitsKnowledge:
    """보유 유닛을 얼마나 아는지 판정한다. advisor의 "안다" 기준(§4.3b)과 같은 조건을 쓴다."""
    if state.board is None and state.bench is None:
        return UnitsKnowledge.UNKNOWN
    owned = owned_units(state, threshold, unit_threshold)
    reliable = owned.board_seen and owned.bench_seen and owned.board_reliable and owned.bench_reliable
    if not reliable:
        if known_unit_count(state) or owned.usable:
            return UnitsKnowledge.PARTIAL
        # 자리는 보이는데 이름을 하나도 모른다 = vision은 읽었고 장부만 비었다(스크린샷·판 초반)
        return UnitsKnowledge.SEEN if unknown_unit_count(state) else UnitsKnowledge.UNKNOWN
    if state.field_source.get("board", FieldSource.VISION) == FieldSource.VISION:
        return UnitsKnowledge.VISION
    return UnitsKnowledge.TRACKED


def _guess_tail(n: int) -> str:
    return f"(추정 이름 {n}기 포함)" if n else ""


def units_reason(state: GameState, threshold: float = DEFAULT_THRESHOLD,
                 unit_threshold: float | None = None) -> str | None:
    """`TargetComp.reasons`에 넣을 한 줄(없으면 None). 합쇼체. unit_threshold: `owned_units`와 같다."""
    kind = units_knowledge(state, threshold, unit_threshold)
    guessed = owned_units(state, threshold, unit_threshold).guessed if unit_threshold is not None else 0
    unknown = unknown_unit_count(state) + guessed
    if kind is UnitsKnowledge.VISION:
        return f"이름 미상 {unknown}기{_guess_tail(guessed)}는 보유/부족 계산에서 뺐습니다" if unknown else None
    if kind is UnitsKnowledge.UNKNOWN:
        return "보드 미인식: 보유/부족 유닛은 구매 추적·수동 입력으로 표시됩니다"
    if kind is UnitsKnowledge.SEEN:
        return f"보드 {unknown}기·장착 아이템은 인식했습니다: 챔피언 이름은 구매 추적·수동 입력으로 표시됩니다"
    if kind is UnitsKnowledge.PARTIAL:
        owned = owned_units(state, threshold, unit_threshold)
        if owned.units:
            gap = owned.gap_text()
            return (f"보유 유닛 {len(owned.units)}기 반영(이름 확인분만)"
                    + (f" · {gap}: 부족 유닛 중 일부는 미확인 칸에 있을 수 있습니다" if gap else ""))
        tail = f", 이름 미상 {unknown}기{_guess_tail(guessed)}" if unknown else ""
        if _vision_named(state):
            return (f"보유 유닛 부분 확인: 화면에서 이름을 확인한 유닛 {known_unit_count(state) - guessed}기{tail}"
                    " — 수동 확인을 권합니다")
        return f"보유 유닛 부분 확인: 구매 추적이 불확실합니다{tail} — 수동 확인을 권합니다"
    return "보유 유닛: 구매 추적 기준" + (f"(이름 미상 {unknown}기{_guess_tail(guessed)})" if unknown else "")


def units_note(state: GameState, threshold: float = DEFAULT_THRESHOLD,
               unit_threshold: float | None = CONFIRMED_NAME_THRESHOLD) -> str | None:
    """목표 덱 줄의 "보유/부족" 뒤에 붙는 꼬리말(없으면 None).

    unit_threshold 기본값은 advisor 기본(`[board_plan] min_confidence`)과 같아 "N기 반영"이 advisor가 실제로 쓴 수와 맞는다.
    추정 이름은 이름 미상 수에 들어간다(21 §15)."""
    kind = units_knowledge(state, threshold, unit_threshold)
    guessed = owned_units(state, threshold, unit_threshold).guessed if unit_threshold is not None else 0
    unknown = unknown_unit_count(state) + guessed
    gt = _guess_tail(guessed)
    if kind is UnitsKnowledge.UNKNOWN:
        return "보드 미인식(구매 추적 대기 · 수동 입력 가능)"
    if kind is UnitsKnowledge.SEEN:
        return f"화면 인식 {unknown}기 · 이름 미상(구매 추적 대기 · 수동 입력 가능)"
    if kind is UnitsKnowledge.PARTIAL:
        how = "화면 인식" if _vision_named(state) else "구매 추적"
        owned = owned_units(state, threshold, unit_threshold)
        if owned.units:
            gap = owned.gap_text()
            return (f"부분 확인 — {how} {len(owned.units)}기 반영"
                    + (f" · {gap} · 부족 중 일부는 미확인 칸에 있을 수 있습니다" if gap else ""))
        known = known_unit_count(state) - guessed
        tail = f" · 이름 미상 {unknown}기{gt}" if unknown else ""
        return f"부분 확인 — {how} {known}기{tail}(추천에는 쓰지 않습니다)"
    if kind is UnitsKnowledge.TRACKED:
        return "구매 추적" + (f" · 이름 미상 {unknown}기{gt}" if unknown else "")
    return f"이름 미상 {unknown}기{gt}" if unknown else None


__all__ = ["CONFIRMED_NAME_THRESHOLD", "DEFAULT_THRESHOLD", "OwnedUnits", "UnitsKnowledge", "known_unit_count", "owned_units",
           "units_knowledge", "units_note", "units_reason", "unknown_unit_count"]
