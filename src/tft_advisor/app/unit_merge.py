"""보유 유닛 장부 + vision 보드 판독 → `GameState.board` / `GameState.bench`.

역할 분담(2026-09-23 합의, vision 보고 `_workspace/16_board_vision.md`가 나오면 그 계약에 맞춘다)

| 누가 | 무엇을 |
|---|---|
| vision | **자리**(보드 육각칸·벤치 칸), **성급**(별 개수), **장착 아이템**, 그리고 확신할 때만 **이름**(`unit_id`, 19 보고: 특성 패널 구속 + 모델 크롭 라이브러리) |
| app(여기) | vision이 이름을 못 붙인 칸의 **정체** — 상점 구매 추적 장부(`app.ledger`) |

병합 규칙
1. vision이 칸마다 `unit_id`까지 줄 수 있으면 그 값이 이긴다(직접 본 것이다).
2. 장부는 **어떤 챔피언을 가졌는지**는 알지만 **어느 칸에 있는지**는 모른다(31 보고 §9, live3: 벤치 2번의 수염 난
   덩치에 "카밀(장부)"이 붙고 진짜 카밀은 벤치 3번이었다). 그래서 이름 없는 칸에 장부 이름을 **유일하게 정해질 때만** 붙인다:
   - 남은 칸 수 = 남은 장부 유닛 수이고 장부 유닛이 전부 같은 챔피언(1칸·1기 포함)
   - vision ★s(s ≥ 2) 칸 수 = 장부에서 ★s인 유닛 수이고 그 유닛들이 한 챔피언뿐(★2가 될 수 있는 챔피언이 하나)
   - ★1도 같은 조건이되, 남은 칸의 성급을 전부 읽었을 때만
   붙인 뒤 남은 칸으로 다시 확인한다(하나를 정하면 나머지가 정해질 수 있다).
   정해지지 않은 장부 유닛은 **자리 미상**(`hex`/`bench_slot` None, 칸의 성급·아이템을 붙이지 않음)으로 내보내고,
   그 수만큼 이름 없는 칸이 목록에서 빠진다(개수는 vision과 같다). 보드/벤치 쪽은 칸 순서(보드 먼저)로 나눈다 —
   이름 없는 칸이 한쪽에만 있으면 정확하고, 양쪽에 있으면 추정이다. 인식 확인 창은 판독의 빈 자리를 "이름 미상"으로,
   자리 미상 장부 유닛을 "장부 보유(자리 미상): …" 한 줄로 보여 준다.
3. **개수가 다르면 vision을 믿는다.** vision이 9기를 보는데 장부가 7기면 남는 2칸은
   `UNKNOWN_UNIT_ID`(정체 미상)로 두고, 신뢰도를 낮춰 advisor가 세지 않게 한다.
   장부가 더 많으면 남는 유닛은 내보내지 않는다(팔았거나 잘못 추적한 것이다).
4. **ID를 지어내지 않는다.** 정체를 모르는 칸은 언제나 `UNKNOWN_UNIT_ID`이고, 그 값은
   실제 챔피언 ID가 아님을 이름표(`이름 미상`)로 드러낸다.
5. vision이 보드 챔피언 **집합**만 알고 칸을 못 정했으면(`BoardRead.unplaced`, 이름 없는 보드 칸 수와 같을 때만)
   그 챔피언들을 **자리 미상**(`hex=None`, 아이템 없음 — 장착 아이템은 `items.equipped`에 소유자 없이 남는다)으로 둔다.
6. vision 이름이 섞이면 보드·벤치 신뢰도를 따로 잰다(이름 아는 유닛 신뢰도 평균 x 이름 아는 비율, 판독 신뢰도 상한).
   출처는 이름을 전부 vision이 붙였으면 `vision`, 장부가 섞이면 `tracked`.
7. vision 판독이 아예 없으면 장부만으로 만든다: 자리는 모르므로 `hex`/`bench_slot`은 None이고,
   레벨만큼 보드에, 나머지는 벤치에 둔다(advisor는 `board + bench`를 합쳐 쓴다).

`GameState.board`/`bench`의 필드 신뢰도는 `ledger.field_confidence()`로 계산한다. 정체를 모르는 칸이
많거나 설명되지 않은 거래가 쌓이면 `[vision] state_min_confidence`(0.6) 아래로 내려가고, advisor는
§4.3b에 따라 보유 유닛을 통째로 "모름"으로 다룬다.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..contracts import UNKNOWN_UNIT_ID, FieldSource, GameState, ItemRef, ItemState, UnitOnBoard
from .ledger import Body, CostBook, LedgerCfg, UnitLedger, field_confidence

log = logging.getLogger(__name__)

BENCH_SLOTS = 9
UNKNOWN_CONFIDENCE = 0.2
"""정체를 모르는 칸의 유닛 신뢰도. `state_min_confidence`(0.6)보다 낮아 advisor의 계산에서 빠진다."""
UNPLACED_CONFIDENCE = 0.8
"""특성 패널 풀이(해가 하나)로 **보드에 있다는 것**은 확실하지만 칸을 모르는 유닛의 신뢰도."""


# ---------------------------------------------------------------------------
# vision 계약 (16_board_vision.md가 나오기 전의 잠정 Protocol)
# ---------------------------------------------------------------------------


@runtime_checkable
class UnitSlotRead(Protocol):
    """vision이 읽은 칸 하나. 속성은 모두 선택이고, 없으면 None으로 본다."""

    star: int | None
    items: Sequence[str]
    confidence: float


@runtime_checkable
class BoardRead(Protocol):
    """vision의 보드 판독 결과(`Recognizer.last_board_read`로 받는다)."""

    board: Sequence[Any]
    bench: Sequence[Any]


@dataclass(frozen=True)
class SlotObs:
    """정규화된 칸 관측 1개."""

    star: int | None = None
    items: tuple[str, ...] = ()
    hex: tuple[int, int] | None = None
    bench_slot: int | None = None
    confidence: float = 1.0
    unit_id: str | None = None      # vision이 정체까지 안 경우(`vision.units.UnitNamer`)
    on_bench: bool = False
    unit_conf: float = 0.0          # vision 이름 신뢰도(0 = 이름 없음 또는 모름)


@dataclass(frozen=True)
class BoardObs:
    """정규화된 보드 판독(보드 칸 + 벤치 칸)."""

    board: tuple[SlotObs, ...] = ()
    bench: tuple[SlotObs, ...] = ()
    confidence: float = 1.0
    unplaced: tuple[str, ...] = ()
    """보드에 있는 것은 확실하지만 칸을 모르는 챔피언(vision 특성 패널 풀이). 이름 없는 보드 칸 수와 같을 때만 쓴다."""

    @property
    def count(self) -> int:
        return len(self.board) + len(self.bench)


def _get(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    return None


def _slot_obs(raw: Any, *, on_bench: bool) -> SlotObs | None:
    """vision 쪽 객체/딕셔너리 하나 → `SlotObs`. 모양이 달라도 흡수한다(계약이 확정되면 단순해진다)."""
    if raw is None:
        return None
    star = _get(raw, "star", "stars", "tier")
    items = _get(raw, "items", "item_ids") or ()
    hexes = _get(raw, "hex", "cell", "position")
    bench_slot = _get(raw, "bench_slot", "slot", "index")
    conf = _get(raw, "confidence", "conf")
    unit_id = _get(raw, "unit_id", "id", "champion_id")
    unit_conf = _get(raw, "unit_conf")
    if isinstance(hexes, Sequence) and not isinstance(hexes, str) and len(hexes) == 2:
        hexes = (int(hexes[0]), int(hexes[1]))
    else:
        hexes = None
    try:
        bench_slot = None if bench_slot is None else int(bench_slot)
    except (TypeError, ValueError):
        bench_slot = None
    return SlotObs(
        star=int(star) if isinstance(star, (int, float)) and 1 <= int(star) <= 4 else None,
        items=tuple(str(i) for i in items if i),
        hex=hexes if not on_bench else None,
        bench_slot=bench_slot if on_bench else None,
        confidence=float(conf) if isinstance(conf, (int, float)) else 1.0,
        unit_id=str(unit_id) if unit_id and str(unit_id) != UNKNOWN_UNIT_ID else None,
        on_bench=on_bench,
        unit_conf=float(unit_conf) if isinstance(unit_conf, (int, float)) else 0.0,
    )


def board_obs_from(raw: Any) -> BoardObs | None:
    """vision 판독(또는 `GameState`) → `BoardObs`. 알아볼 수 없으면 None."""
    if raw is None:
        return None
    if isinstance(raw, BoardObs):
        return raw
    board_raw = _get(raw, "board", "units", "board_slots")
    bench_raw = _get(raw, "bench", "bench_slots")
    if board_raw is None and bench_raw is None:
        return None
    board = tuple(s for s in (_slot_obs(r, on_bench=False) for r in board_raw or ()) if s is not None)
    bench = tuple(s for s in (_slot_obs(r, on_bench=True) for r in bench_raw or ()) if s is not None)
    conf = _get(raw, "confidence")
    unplaced = _get(raw, "unplaced") or ()
    return BoardObs(board=board, bench=bench,
                    confidence=float(conf) if isinstance(conf, (int, float)) else 1.0,
                    unplaced=tuple(str(c) for c in unplaced if c))


def board_obs_from_state(state: GameState) -> BoardObs | None:
    """vision이 `GameState.board`/`bench`를 직접 채운 경우(정체까지 아는 경우)."""
    if state.board is None and state.bench is None:
        return None
    return BoardObs(
        board=tuple(SlotObs(star=u.star, items=tuple(u.items), hex=u.hex, confidence=u.confidence,
                            unit_id=None if u.id == UNKNOWN_UNIT_ID else u.id)
                    for u in state.board or ()),
        bench=tuple(SlotObs(star=u.star, items=tuple(u.items), bench_slot=u.bench_slot,
                            confidence=u.confidence, unit_id=None if u.id == UNKNOWN_UNIT_ID else u.id,
                            on_bench=True)
                    for u in state.bench or ()),
    )


# ---------------------------------------------------------------------------
# 병합
# ---------------------------------------------------------------------------


@dataclass
class MergeResult:
    """병합 결과와 그 품질(표시·로그용)."""

    board: list[UnitOnBoard] = field(default_factory=list)
    bench: list[UnitOnBoard] = field(default_factory=list)
    confidence: float = 0.0
    source: FieldSource = FieldSource.TRACKED
    known: int = 0          # 정체를 아는 유닛 수
    unplaced: int = 0       # 그중 장부로만 알아 자리를 모르는 유닛 수(칸에 이름을 붙이지 않았다)
    unplaced_ids: list[str] = field(default_factory=list)   # 그 유닛들의 챔피언 ID(인식 확인 창 "장부 보유(자리 미상)")
    unknown: int = 0        # vision은 보지만 정체를 모르는 유닛 수
    dropped: int = 0        # 장부에는 있으나 vision이 보지 못해 뺀 유닛 수
    star_conflicts: int = 0  # 장부의 성급과 vision의 성급이 다른 칸 수(vision을 따른다)
    board_confidence: float | None = None   # vision 이름이 섞였을 때만: 보드·벤치를 따로 잰 신뢰도
    bench_confidence: float | None = None

    @property
    def total(self) -> int:
        return len(self.board) + len(self.bench)

    def note(self) -> str | None:
        """사용자에게 보이는 한 줄 요약(없으면 None)."""
        bits = []
        if self.unknown:
            bits.append(f"이름 미상 {self.unknown}기")
        if self.unplaced:
            bits.append(f"장부 보유 자리 미상 {self.unplaced}기")
        if self.dropped:
            bits.append(f"화면에 없어 뺀 유닛 {self.dropped}기")
        if self.star_conflicts:
            bits.append(f"성급 불일치 {self.star_conflicts}기")
        return " · ".join(bits) or None


def _unit(body: Body | None, slot: SlotObs | None, *, star_conflicts: list[int]) -> UnitOnBoard:
    """장부의 유닛 + vision 칸 → `UnitOnBoard`. 성급은 vision이 이긴다(직접 본 값이다)."""
    star = (slot.star if slot is not None and slot.star is not None else None)
    if body is not None and star is not None and star != body.star:
        star_conflicts.append(1)
    if star is None and body is not None:
        star = body.star
    return UnitOnBoard(
        id=body.champion_id if body is not None else UNKNOWN_UNIT_ID,
        star=star,
        items=list(slot.items) if slot is not None else [],
        hex=slot.hex if slot is not None else None,
        bench_slot=slot.bench_slot if slot is not None else None,
        confidence=(UNKNOWN_CONFIDENCE if body is None
                    else min(body.confidence, slot.confidence if slot is not None else 1.0)),
    )


def _assign(bodies: list[Body], slots: list[SlotObs]) -> list[tuple[SlotObs, Body | None, bool]]:
    """칸 ← 장부 유닛. 반환 (칸, 유닛, 자리 확정). 자리가 **유일하게 정해질 때만** 확정(True)이고,
    나머지 장부 유닛은 남은 칸 순서대로 짝지어 자리 미상(False)으로 둔다(개수·보드/벤치 쪽만 맞춘다). 규칙은 모듈 docstring 2."""
    left = list(bodies)
    free = list(range(len(slots)))
    placed: dict[int, Body] = {}

    def take(idx: list[int], group: list[Body]) -> None:
        for i, body in zip(idx, group, strict=True):
            placed[i] = body
            free.remove(i)
            left.remove(body)

    progress = True
    while progress and left and free:
        progress = False
        if len({b.champion_id for b in left}) == 1 and len(left) == len(free):
            take(list(free), list(left))
            break
        all_stars = all(slots[i].star is not None for i in free)
        for star in sorted({slots[i].star for i in free if slots[i].star}, reverse=True):
            idx = [i for i in free if slots[i].star == star]
            group = [b for b in left if b.star == star]
            if not group or len(group) != len(idx) or len({b.champion_id for b in group}) != 1:
                continue
            if star < 2 and not all_stars:
                continue
            take(idx, group)
            progress = True
            break
    out: list[tuple[SlotObs, Body | None, bool]] = []
    rest = list(left)
    for i, slot in enumerate(slots):
        if i in placed:
            out.append((slot, placed[i], True))
        elif rest:
            out.append((slot, rest.pop(0), False))
        else:
            out.append((slot, None, False))
    return out


def merge_units(ledger: UnitLedger, obs: BoardObs | None, *, level: int | None = None,
                cfg: LedgerCfg | None = None, costs: CostBook | None = None) -> MergeResult:
    """장부(정체) + vision 판독(자리·성급·아이템) → board/bench. 규칙은 모듈 docstring 참고."""
    cfg = cfg or LedgerCfg()
    costs = costs or CostBook()
    bodies = ledger.bodies(costs)
    result = MergeResult()

    if obs is None or obs.count == 0:
        if not bodies:
            return result
        cut = max(0, level or 0) or len(bodies)
        board_bodies, bench_bodies = bodies[:cut], bodies[cut:cut + BENCH_SLOTS]
        conflicts: list[int] = []
        result.board = [_unit(b, None, star_conflicts=conflicts) for b in board_bodies]
        result.bench = [_unit(b, None, star_conflicts=conflicts) for b in bench_bodies]
        result.known = result.total
        result.dropped = max(0, len(bodies) - result.total)
        result.confidence = field_confidence(cfg, ledger.ambiguous)
        result.source = FieldSource.MANUAL if ledger.manual else FieldSource.TRACKED
        return result

    # vision이 본 칸들 — 정체를 아는 칸(vision이 직접 준 ID)을 먼저 빼고 나머지를 장부에서 채운다
    slots = [(False, s) for s in obs.board] + [(True, s) for s in obs.bench]
    left = list(bodies)
    fixed: dict[int, Body] = {}
    for i, (_, slot) in enumerate(slots):
        if not slot.unit_id:
            continue
        match = next((b for b in left if b.champion_id == slot.unit_id), None)
        if match is not None:
            left.remove(match)
        vconf = slot.unit_conf if slot.unit_conf > 0 else slot.confidence
        # 성급: vision 배지 > 장부. 둘 다 모르면 None(모름) — 1성으로 지어내지 않는다(35 보고)
        fixed[i] = Body(slot.unit_id, slot.star or (match.star if match else None), min(1.0, vconf), "vision")
    # 집합만 아는 보드 챔피언(자리 미상): 이름 없는 보드 칸 수와 정확히 같을 때만
    unplaced_idx: set[int] = set()
    open_board = [i for i, (on_bench, _) in enumerate(slots) if not on_bench and i not in fixed]
    if obs.unplaced and len(obs.unplaced) == len(open_board):
        stars = {slots[i][1].star for i in open_board}
        star = stars.pop() if len(stars) == 1 else None
        for i, cid in zip(open_board, sorted(obs.unplaced), strict=True):
            match = next((b for b in left if b.champion_id == cid), None)
            if match is not None:
                left.remove(match)
            fixed[i] = Body(cid, star or (match.star if match else None), UNPLACED_CONFIDENCE, "vision")
            unplaced_idx.add(i)
    consumed = len(bodies) - len(left)      # vision 이름과 같은 챔피언이라 장부에서 빠진 유닛 수

    free_idx = [i for i in range(len(slots)) if i not in fixed]
    pairs = _assign(left, [slots[i][1] for i in free_idx])
    conflicts = []
    assigned: dict[int, Body | None] = dict(fixed)
    for idx, (_, body, sure) in zip(free_idx, pairs, strict=False):
        assigned[idx] = body
        if body is not None and not sure:
            unplaced_idx.add(idx)

    vision_known = ledger_known = 0
    for i, (on_bench, slot) in enumerate(slots):
        body = assigned.get(i)
        if i in unplaced_idx and body is not None:
            # 자리 미상: 칸 정보(자리·아이템)를 붙이지 않는다 — 어느 칸의 아이템인지 모른다.
            # vision 집합 풀이(보드 unplaced)는 이름 없는 보드 칸 전부의 성급이 같을 때 그 성급을, 아니면 장부 성급을
            # 이미 body에 담았다. 짝지어진 칸의 성급은 쓰지 않는다 — 짝짓기는 정렬 순서일 뿐이다(QA 32 F1).
            star = body.star
            unit = UnitOnBoard(id=body.champion_id, star=star, hex=None, bench_slot=None,
                               confidence=body.confidence)
            if body.source != "vision":
                result.unplaced += 1
                result.unplaced_ids.append(body.champion_id)
        else:
            unit = _unit(body, slot, star_conflicts=conflicts)
        (result.bench if on_bench else result.board).append(unit)
        if body is None:
            result.unknown += 1
        else:
            result.known += 1
            if body.source == "vision":
                vision_known += 1
            else:
                ledger_known += 1
    result.bench = result.bench[:BENCH_SLOTS]   # 계약상 벤치는 9칸이다(오인식 방어)
    result.star_conflicts = len(conflicts)
    result.dropped = max(0, len(bodies) - ledger_known - consumed)
    if vision_known:
        result.board_confidence = _side_confidence(result.board, obs.confidence, cfg, ledger, ledger_known)
        result.bench_confidence = _side_confidence(result.bench, obs.confidence, cfg, ledger, ledger_known)
        result.confidence = min(result.board_confidence, result.bench_confidence)
    else:
        result.confidence = min(
            field_confidence(cfg, ledger.ambiguous, result.known, max(1, result.total)),
            obs.confidence,
        )
    if ledger.manual:
        result.source = FieldSource.MANUAL
    elif vision_known and not ledger_known:
        result.source = FieldSource.VISION
    else:
        result.source = FieldSource.TRACKED
    if result.unknown or result.dropped:
        log.info("보드 병합: 화면 %d기 · 장부 %d기 → 이름 확인 %d(화면 %d) · 미상 %d · 제외 %d",
                 obs.count, len(bodies), result.known, vision_known, result.unknown, result.dropped)
    return result


def _side_confidence(units: list[UnitOnBoard], cap: float, cfg: LedgerCfg, ledger: UnitLedger,
                     ledger_known: int) -> float:
    """보드 또는 벤치 한쪽의 필드 신뢰도 = (이름 아는 유닛 신뢰도 평균, 판독 신뢰도 상한) x (이름 아는 비율).
    장부 이름이 섞였으면 장부 신뢰도(애매한 거래 반영)를 넘지 않는다. 유닛이 없으면 판독 신뢰도 그대로(빈 벤치도 사실이다)."""
    if not units:
        return round(max(0.0, min(1.0, cap)), 3)
    known = [u.confidence for u in units if u.id != UNKNOWN_UNIT_ID]
    if not known:
        return 0.0
    value = min(sum(known) / len(known), cap) * len(known) / len(units)
    if ledger_known:
        value = min(value, field_confidence(cfg, ledger.ambiguous))
    return round(max(0.0, min(1.0, value)), 3)


# ---------------------------------------------------------------------------
# 장착 아이템 → `ItemState.equipped`, 그리고 GameState에 반영
# ---------------------------------------------------------------------------


def _item_ref(costs: CostBook, item_id: str, *, holder: str | None, confidence: float) -> ItemRef:
    """아이템 ID → `ItemRef`. 정적 데이터가 없어도 ID만으로 만든다(표시 이름은 없어도 된다)."""
    row: Any = None
    try:
        row = costs.static.get("items", item_id)
    except Exception:       # 정적 데이터가 없어도 아이템 보유 사실은 남긴다
        log.debug("아이템 조회 실패: %s", item_id, exc_info=True)
    return ItemRef(id=item_id, name_ko=(row or {}).get("name_ko") or None,
                   category=(row or {}).get("category"), holder=holder,
                   confidence=max(0.0, min(1.0, confidence)))


def equipped_refs(obs: BoardObs | None, result: MergeResult | None = None, *,
                  costs: CostBook | None = None) -> list[ItemRef]:
    """vision이 읽은 **장착 아이템** 전부 → `ItemState.equipped`에 넣을 `ItemRef` 목록.

    아이템 아이콘은 챔피언 모델과 달리 2D 스프라이트라 **정체를 몰라도 아이템은 확실히 읽힌다**
    (`_workspace/16_board_vision.md` §3). 그래서 신뢰도는 칸 신뢰도를 그대로 쓰고, 소유자만
    모를 때 `holder=None`으로 둔다. 소유자를 아는 칸(`merge_units` 결과)은 `holder`에 챔피언 ID가 들어간다.
    """
    if obs is None:
        return []
    costs = costs or CostBook()
    slots = list(obs.board) + list(obs.bench)
    units = (result.board + result.bench) if result is not None else []
    if len(units) != len(slots):        # 벤치 9칸 자르기 등으로 어긋나면 소유자를 붙이지 않는다
        units = []
    refs: list[ItemRef] = []
    for i, slot in enumerate(slots):
        holder = None
        if (i < len(units) and units[i].id != UNKNOWN_UNIT_ID
                and (units[i].hex, units[i].bench_slot) == (slot.hex, slot.bench_slot)):
            holder = units[i].id        # 자리 미상 유닛(vision 집합만 앎)은 어느 칸의 아이템인지 모른다 → 소유자 없음
        for item_id in slot.items:
            refs.append(_item_ref(costs, item_id, holder=holder, confidence=slot.confidence))
    return refs


def with_equipped(state: GameState, refs: Sequence[ItemRef], *, confidence: float = 0.0) -> GameState:
    """`GameState.items.equipped`를 이번 판독의 장착 아이템으로 바꾼다(아이템 벤치는 건드리지 않는다).

    `state.items`가 아예 없으면(아이템 벤치를 못 읽은 화면) 장착분만 담은 `ItemState`를 만들고
    `confidence["items"]`에 판독 신뢰도를 넣는다 — 없는 값을 1.0으로 두면 advisor가 과신한다.
    """
    items = state.items
    if items is None:
        if not refs:
            return state
        return state.model_copy(update={
            "items": ItemState(equipped=list(refs)),
            "confidence": {**state.confidence, "items": round(confidence, 3)},
        })
    if list(items.equipped) == list(refs):
        return state
    return state.model_copy(update={"items": items.model_copy(update={"equipped": list(refs)})})


def state_with_units(state: GameState, result: MergeResult, obs: BoardObs | None = None, *,
                     costs: CostBook | None = None) -> GameState:
    """병합 결과를 `GameState`에 반영한다: `board`/`bench` + `items.equipped` + 신뢰도·출처."""
    if not result.board and not result.bench:
        return state
    state = state.model_copy(update={
        "board": result.board,
        "bench": result.bench,
        "confidence": {**state.confidence,
                       "board": result.confidence if result.board_confidence is None else result.board_confidence,
                       "bench": result.confidence if result.bench_confidence is None else result.bench_confidence},
        "field_source": {**state.field_source, "board": result.source, "bench": result.source},
    })
    if obs is None:
        return state
    return with_equipped(state, equipped_refs(obs, result, costs=costs), confidence=obs.confidence)


def apply_board_read(state: GameState, board_read: Any, *, ledger: UnitLedger | None = None,
                     cfg: LedgerCfg | None = None, costs: CostBook | None = None) -> GameState:
    """vision 보드 판독 하나를 `GameState`에 그대로 반영한다(`--screenshot` 같은 한 장짜리 경로용).

    장부가 없으면(스크린샷 1장에는 구매 기록이 없다) 모든 칸이 `UNKNOWN_UNIT_ID`가 되고 board/bench
    신뢰도는 0이 된다 — **자리·성급·장착 아이템은 남고 정체만 모른다**는 뜻이다. 장착 아이템은
    `items.equipped`로 들어가 `items_ready`(보유 완성템)에 그대로 쓰인다.
    """
    obs = board_obs_from(board_read)
    if obs is None or obs.count == 0:
        return state
    costs = costs or CostBook()
    result = merge_units(ledger if ledger is not None else UnitLedger(), obs,
                         level=state.level, cfg=cfg, costs=costs)
    return state_with_units(state, result, obs, costs=costs)


__all__ = ["BENCH_SLOTS", "BoardObs", "BoardRead", "MergeResult", "SlotObs", "UNKNOWN_CONFIDENCE",
           "UnitSlotRead", "apply_board_read", "board_obs_from", "board_obs_from_state", "equipped_refs",
           "merge_units", "state_with_units", "with_equipped"]
