"""인식 확인 — 보드·벤치·장착 아이템·미사용 아이템을 **인식한 그대로** 보여 주는 표시 모델(순수 함수, Qt 없음).

추천 오버레이와 달리 "추천"이 아니라 **인식 결과**를 사용자가 눈으로 대조하는 용도다(사용자 요청 20).
같은 `GameState`(루프가 advisor에 넘기는 병합 상태)와 마지막 vision 보드 판독(`Recognizer.last_board_read`)만
읽는다 — 추가 캡처·추가 인식은 하지 않는다.

- 창(`app.recog_window.RecogWindow`)과 콘솔(`--live --no-overlay`, `--screenshot … --test-view`)이 같은 문구를 쓴다.
- 칸별 **이름 출처**: `화면`(vision이 칸에서 직접 이름을 붙임 — 특성 구속·모델 비교), `장부`(상점 구매 추적),
  `수동`(사용자 입력), `미상`(`UNKNOWN_UNIT_ID`). 계약(`UnitOnBoard`)에는 칸별 출처 필드가 없으므로
  보드 판독의 같은 자리 칸(`hex`/`bench_slot`)의 `unit_id`와 대조해서 정한다(`unit_source`).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from ..contracts import UNKNOWN_UNIT_ID, FieldSource, GameState, ItemRef, ScreenMode, UnitOnBoard
from .names import NameBook
from .report import screen_label, state_line

TITLE = "인식 확인"
MENU_TEXT = "인식 확인 창"
BENCH_SLOTS = 9
BOARD_MODES = frozenset({ScreenMode.PLANNING, ScreenMode.ITEM_SELECT, ScreenMode.COMBAT})
"""보드·벤치가 화면에 보이는 모드(vision `READ_MODES["board"]` + 전투). 모루·전리품(아이템 선택) 화면도 보드를 읽는다. 나머지 화면에서는 "보드가 보이지 않습니다"를 띄우고 마지막 값을 흐리게 보여 준다."""

SOURCE_LABELS = {"vision": "화면", "ledger": "장부", "manual": "수동", "unknown": "미상"}
VISION_DETAIL = {
    "forced": "특성 구속", "traits": "특성+닮음", "library": "모델 비교", "duplicate": "중복 배정",
    "held": "직전 판독",   # 벤치 체력바가 사라진 프레임(준비 끝): vision이 직전 판독을 이어 씀(vision 30 보고)
}
ITEM_GROUPS = (("components", "재료"), ("completed", "완성"), ("emblems", "상징"), ("others", "기타"))
UNKNOWN_NAME = "이름 미상"
GUESS_MARK = "(추정)"
GUESS_CONF_CAP = 0.75
"""vision `units.LIB_STRICT_CONF_CAP`와 같다: 뒷받침 없는(모델 닮음 하나만) 이름은 신뢰도가 이 값에서 잘린다."""


def is_guess(slot: Any) -> bool:
    """판독 칸의 이름이 뒷받침 없는 추정인가. `corroborated` 속성이 있으면 그것, 없으면(지금 `UnitSlot`)
    "모델 비교(library) + 신뢰도 ≤ 0.75"로 본다(엄격 임계 이름은 0.75 상한, 힌트로 뒷받침된 이름은 0.90까지)."""
    if slot is None or getattr(slot, "unit_id", None) is None:
        return False
    flag = getattr(slot, "corroborated", None)
    if isinstance(flag, bool):
        return not flag
    try:
        conf = float(getattr(slot, "unit_conf", 1.0) or 0.0)
    except (TypeError, ValueError):
        return False
    return str(getattr(slot, "name_source", "")) == "library" and conf <= GUESS_CONF_CAP + 1e-9


# ---------------------------------------------------------------------------
# 입력: 루프 갱신 1건의 인식 부분
# ---------------------------------------------------------------------------


@dataclass
class RecogSnapshot:
    """인식 확인 창이 그리는 한 시점. `LoopUpdate`나 스크린샷 1장에서 만든다."""

    state: GameState | None = None
    board_read: Any = None
    recog_ms: float | None = None
    at: datetime | None = None
    groups: tuple[str, ...] = ()
    kind: str = "recognized"
    message: str | None = None
    label: str | None = None          # 스크린샷 파일 이름 등(실시간이면 None)
    ledger_ids: tuple[str, ...] | None = None   # 장부로만 알아 자리를 모르는 유닛 ID(루프가 줄 때만)

    @classmethod
    def from_update(cls, update: Any, prev: RecogSnapshot | None = None) -> RecogSnapshot | None:
        """루프 갱신 → 스냅숏. 추천 결과(`advice`)는 인식이 아니므로 None(창을 다시 그리지 않는다).

        오류 갱신은 상태가 없으므로 직전 스냅숏에 메시지만 얹는다.
        """
        kind = getattr(update, "kind", "recognized")
        if kind == "advice":
            return None
        if kind == "error":
            base = prev or cls()
            return cls(state=base.state, board_read=base.board_read, recog_ms=None,
                       at=getattr(update, "at", None) or base.at, groups=(), kind="error",
                       message=getattr(update, "message", None), label=base.label)
        return cls(state=getattr(update, "state", None), board_read=getattr(update, "board_read", None),
                   recog_ms=getattr(update, "recog_ms", None), at=getattr(update, "at", None),
                   groups=tuple(getattr(update, "recognized", ()) or ()), kind=kind,
                   message=getattr(update, "message", None),
                   ledger_ids=getattr(update, "ledger_unplaced", None))


# ---------------------------------------------------------------------------
# 출력: 표시 모델
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitRow:
    """보드/벤치 칸 하나의 표시 행."""

    where: str                      # "board" | "bench"
    pos: str                        # "1행 3열" / "벤치 4" / "자리 미상"
    name: str = ""
    unit_id: str | None = None
    star: int | None = None
    items: tuple[str, ...] = ()     # 표시 이름
    unread_items: int = 0           # 아이콘은 있었는데 무엇인지 못 알아본 수
    confidence: float = 0.0
    source: str = "unknown"         # SOURCE_LABELS 키
    detail: str | None = None       # vision 이름 근거(특성 구속 등)
    empty: bool = False
    guess: bool = False             # 뒷받침 없는 추정 이름(모델 닮음 하나만, 신뢰도 상한 0.75) → "(추정)"

    @property
    def name_text(self) -> str:
        return f"{self.name} {GUESS_MARK}" if self.guess else self.name

    @property
    def star_text(self) -> str:
        return f"★{self.star}" if self.star else "★?"

    @property
    def source_text(self) -> str:
        label = SOURCE_LABELS.get(self.source, self.source)
        return f"{label}({self.detail})" if self.detail else label

    @property
    def items_text(self) -> str:
        bits = list(self.items) + (["?"] * self.unread_items)
        return ", ".join(bits) if bits else "-"

    def text(self) -> str:
        if self.empty:
            return f"{self.pos}  (비어 있음)"
        return (f"{self.pos}  {self.name_text} {self.star_text}  [{self.items_text}]"
                f"  신뢰도 {self.confidence:.2f} · {self.source_text}")


@dataclass(frozen=True)
class ItemGroup:
    """장착 아이템(소유자별) 또는 미사용 아이템(종류별) 한 묶음."""

    title: str
    items: tuple[str, ...]
    low_confidence: bool = False

    def text(self) -> str:
        return f"{self.title}: {', '.join(self.items) if self.items else '-'}"


@dataclass
class RecogView:
    """인식 확인 창 한 장 분량. `lines()`는 콘솔, 창은 필드를 직접 읽어 HTML로 그린다."""

    header: list[str] = field(default_factory=list)
    notice: str | None = None            # "보드가 보이지 않습니다" 등(눈에 띄게)
    board_visible: bool = False
    board: list[UnitRow] = field(default_factory=list)
    bench: list[UnitRow] | None = None   # None = 벤치를 읽지 못함
    board_note: str | None = None
    board_common_note: str | None = None   # "보드에 확인된 챔피언(자리 미상): 요릭 · 놓친 유닛 1기"
    bench_note: str | None = None
    ledger_note: str | None = None         # "장부 보유(자리 미상): 카밀 · 쉔" — 장부는 누구를 가졌는지만 안다(31 보고 §9)
    equipped: list[ItemGroup] = field(default_factory=list)
    unused: list[ItemGroup] | None = None   # None = 아이템 벤치를 읽지 못함
    unused_note: str | None = None
    frame_at: datetime | None = None

    @property
    def bench_count(self) -> int:
        return sum(1 for r in self.bench or () if not r.empty)

    def age_text(self, now: datetime | None = None) -> str:
        """"12:34:56 (3초 전)". 시각을 모르면 빈 문자열."""
        at = self.frame_at
        if at is None:
            return ""
        now = now or datetime.now(UTC)
        ref = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
        age = max(0.0, (now - ref).total_seconds())
        return f"{ref.astimezone().strftime('%H:%M:%S')} ({_ago(age)})"

    def lines(self, *, now: datetime | None = None, with_age: bool = True) -> list[str]:
        """콘솔 출력용 텍스트 줄. `with_age=False`는 중복 출력 판정 키로 쓴다(나이는 매번 바뀐다)."""
        out = [f"=== {TITLE} ==="]
        out += self.header
        if with_age and self.frame_at is not None:
            out.append(f"프레임 {self.age_text(now)}")
        if self.notice:
            out.append(f"! {self.notice}")
        out.append(f"[보드] {len(self.board)}기" + (f" — {self.board_note}" if self.board_note else ""))
        out += [f"  {r.text()}" for r in self.board] or ["  (유닛 없음)"]
        if self.board_common_note:
            out.append(f"  {self.board_common_note}")
        if self.bench is None:
            out.append("[벤치] 읽지 못했습니다")
        else:
            out.append(f"[벤치] {self.bench_count}/{BENCH_SLOTS}" + (f" — {self.bench_note}" if self.bench_note else ""))
            out += [f"  {r.text()}" for r in self.bench]
        if self.ledger_note:
            out.append(f"  {self.ledger_note}")
        out.append("[장착 아이템]")
        out += [f"  {g.text()}" for g in self.equipped] or ["  (없음)"]
        if self.unused is None:
            out.append("[미사용 아이템] 읽지 못했습니다")
        else:
            out.append("[미사용 아이템]" + (f" — {self.unused_note}" if self.unused_note else ""))
            out += [f"  {g.text()}" for g in self.unused] or ["  (없음)"]
        return out


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------


def pos_label(u: UnitOnBoard, *, on_bench: bool) -> str:
    """자리 표시. 보드 행은 1행 = 내 쪽 맨 앞(계약 `hex` 행 0)."""
    if on_bench:
        return f"벤치 {u.bench_slot + 1}" if u.bench_slot is not None else "벤치 ?"
    if u.hex is None:
        return "자리 미상"
    return f"{u.hex[0] + 1}행 {u.hex[1] + 1}열"


def find_slot(board_read: Any, u: UnitOnBoard, *, on_bench: bool) -> Any:
    """보드 판독에서 같은 자리의 칸을 찾는다(없으면 None)."""
    if board_read is None:
        return None
    if on_bench:
        if u.bench_slot is None:
            return None
        return next((s for s in getattr(board_read, "bench", ()) or ()
                     if getattr(s, "bench_slot", None) == u.bench_slot), None)
    if u.hex is None:
        return None
    return next((s for s in getattr(board_read, "board", ()) or ()
                 if _hex(getattr(s, "hex", None)) == tuple(u.hex)), None)


def _hex(v: Any) -> tuple[int, int] | None:
    try:
        return (int(v[0]), int(v[1])) if v is not None and len(v) == 2 else None
    except (TypeError, ValueError):
        return None


def unit_source(u: UnitOnBoard, *, on_bench: bool, state: GameState,
                board_read: Any = None) -> tuple[str, str | None]:
    """칸의 이름 출처 → (SOURCE_LABELS 키, vision 세부 근거).

    1. `UNKNOWN_UNIT_ID` → 미상
    2. 같은 자리의 판독 칸이 같은 `unit_id`를 가졌다 → 화면(판독의 `name_source`로 근거를 붙인다)
    3. 자리 미상 보드 유닛이 판독의 `unplaced`(특성 패널 풀이)에 있다 → 화면(자리 미상)
    4. 필드 출처가 vision이면 화면, manual이면 수동, 그 밖은 장부
    """
    if u.id == UNKNOWN_UNIT_ID:
        return "unknown", None
    slot = find_slot(board_read, u, on_bench=on_bench)
    if slot is not None and getattr(slot, "unit_id", None) == u.id:
        return "vision", VISION_DETAIL.get(str(getattr(slot, "name_source", "") or ""))
    if (not on_bench and u.hex is None and board_read is not None
            and u.id in (getattr(board_read, "unplaced", ()) or ())):
        return "vision", "자리 미상"
    src = state.field_source.get("bench" if on_bench else "board")
    if src == FieldSource.VISION:
        return "vision", None
    if src == FieldSource.MANUAL:
        return "manual", None
    return "ledger", None


def board_common_note(read: Any, board_rows: list[UnitRow], names: NameBook) -> str | None:
    """vision `BoardRead.board_common`(보드에 확실히 있는 챔피언, 자리 모름) 중 칸에 안 붙은 것 + `missed_board`(놓친 유닛 수)."""
    if read is None:
        return None
    common = [c for c in (getattr(read, "board_common", ()) or ()) if c]
    placed = {r.unit_id for r in board_rows if r.unit_id and r.pos != "자리 미상"}
    loose = [c for c in common if c not in placed]
    try:
        missed = int(getattr(read, "missed_board", 0) or 0)
    except (TypeError, ValueError):
        missed = 0
    bits = []
    if loose:
        bits.append("보드에 확인된 챔피언(자리 미상): " + " · ".join(names.name(c) for c in loose))
    if missed > 0:
        bits.append(f"놓친 유닛 {missed}기")
    return " · ".join(bits) or None


def unit_row(u: UnitOnBoard, *, on_bench: bool, state: GameState, names: NameBook,
             board_read: Any = None) -> UnitRow:
    source, detail = unit_source(u, on_bench=on_bench, state=state, board_read=board_read)
    slot = find_slot(board_read, u, on_bench=on_bench)
    unread = 0
    if slot is not None:
        try:
            unread = max(0, int(getattr(slot, "item_count", 0) or 0) - len(getattr(slot, "items", ()) or ()))
        except (TypeError, ValueError):
            unread = 0
    return UnitRow(
        where="bench" if on_bench else "board",
        pos=pos_label(u, on_bench=on_bench),
        name=UNKNOWN_NAME if u.id == UNKNOWN_UNIT_ID else names.name(u.id),
        unit_id=None if u.id == UNKNOWN_UNIT_ID else u.id,
        star=u.star,
        items=tuple(names.names(list(u.items))),
        unread_items=unread,
        confidence=u.confidence,
        source=source,
        detail=detail,
        guess=source == "vision" and is_guess(slot) and getattr(slot, "unit_id", None) == u.id,
    )


def _board_sort_key(u: UnitOnBoard) -> tuple:
    return (u.hex is None, tuple(u.hex) if u.hex is not None else (9, 9), u.id)


def _unknown_at(slot: Any, *, on_bench: bool) -> UnitOnBoard | None:
    """판독 칸 → 이름 미상 유닛(자리·성급·아이템은 판독 그대로). 장부 이름을 자리 미상으로 뺀 칸을 다시 그릴 때 쓴다."""
    star = getattr(slot, "star", None)
    try:
        return UnitOnBoard(
            id=UNKNOWN_UNIT_ID, star=star if isinstance(star, int) and 1 <= star <= 4 else None,
            items=[str(i) for i in (getattr(slot, "items", ()) or ()) if i][:3],
            hex=None if on_bench else _hex(getattr(slot, "hex", None)),
            bench_slot=getattr(slot, "bench_slot", None) if on_bench else None,
            confidence=0.0)
    except Exception:   # noqa: BLE001 — 판독 모양이 달라도 창은 그린다
        return None


def bench_rows(state: GameState, names: NameBook, board_read: Any = None) -> list[UnitRow] | None:
    """벤치 1~9칸(빈 칸 포함). 자리를 모르는 벤치 유닛은 판독이 없을 때(장부만)만 뒤에 "벤치 ?"로 붙인다.
    판독이 있으면 판독에는 있는데 상태에 없는 칸은 "이름 미상"으로 그리고, 자리 미상 장부 유닛은 `ledger_unplaced`가 모은다."""
    if state.bench is None:
        return None
    by_slot: dict[int, UnitOnBoard] = {}
    loose: list[UnitOnBoard] = []
    for u in state.bench:
        if u.bench_slot is not None and u.bench_slot not in by_slot:
            by_slot[u.bench_slot] = u
        else:
            loose.append(u)
    read_slots = {getattr(s, "bench_slot", None): s for s in (getattr(board_read, "bench", ()) or ())}         if board_read is not None else {}
    rows: list[UnitRow] = []
    for slot in range(BENCH_SLOTS):
        u = by_slot.get(slot)
        if u is None and slot in read_slots:
            u = _unknown_at(read_slots[slot], on_bench=True)
        if u is None:
            rows.append(UnitRow(where="bench", pos=f"벤치 {slot + 1}", empty=True))
        else:
            rows.append(unit_row(u, on_bench=True, state=state, names=names, board_read=board_read))
    if board_read is None:
        rows += [unit_row(u, on_bench=True, state=state, names=names, board_read=board_read) for u in loose]
    return rows


def board_units(state: GameState, board_read: Any = None) -> list[UnitOnBoard]:
    """보드 행으로 그릴 유닛. 판독이 있으면 자리 미상 **장부** 유닛은 빼고(`ledger_unplaced`), 그 대신 판독에는 있는데
    상태에 없는 칸을 이름 미상으로 넣는다. vision 집합 풀이(판독 `unplaced`)의 자리 미상 유닛은 그대로 둔다."""
    units = list(state.board or [])
    if board_read is None:
        return units
    vision_loose = set(getattr(board_read, "unplaced", ()) or ())
    keep = [u for u in units if u.hex is not None or u.id in vision_loose or u.id == UNKNOWN_UNIT_ID]
    have = {tuple(u.hex) for u in keep if u.hex is not None}
    # 자리 미상 vision 유닛 하나가 이름 없는 판독 칸 하나를 대신한다 — 같은 유닛을 두 번 세지 않는다(QA 32 F2)
    stand_ins = sum(1 for u in keep if u.hex is None)
    for s in getattr(board_read, "board", ()) or ():
        h = _hex(getattr(s, "hex", None))
        if h is not None and h not in have:
            if stand_ins > 0:
                stand_ins -= 1
                continue
            u = _unknown_at(s, on_bench=False)
            if u is not None:
                keep.append(u)
                have.add(h)
    return keep


def ledger_unplaced(state: GameState, board_read: Any, names: NameBook,
                    ledger_ids: tuple[str, ...] | None = None) -> str | None:
    """"장부 보유(자리 미상): 카밀 · 쉔". 판독이 있을 때만(판독이 없으면 "벤치 ?"/"자리 미상" 행으로 이미 보인다).

    `ledger_ids`(루프가 준 마지막 병합의 장부 자리 미상 유닛)가 있으면 그 유닛만 "장부"라고 쓰고, 나머지 자리 없는 이름
    (vision 집합 풀이 등)은 "화면 확인(자리 미상)"으로 따로 쓴다 — 장부가 아닌 이름을 장부라고 하지 않는다(37 보고)."""
    if board_read is None:
        return None
    vision_loose = set(getattr(board_read, "unplaced", ()) or ())
    loose = [u for u in state.board or [] if u.hex is None and u.id != UNKNOWN_UNIT_ID and u.id not in vision_loose]
    loose += [u for u in state.bench or [] if u.bench_slot is None and u.id != UNKNOWN_UNIT_ID]
    if not loose:
        return None

    def text(units) -> str:
        return " · ".join(names.name(u.id) + (f" ★{u.star}" if u.star and u.star > 1 else "") for u in units)
    if ledger_ids is None:
        return f"장부 보유(자리 미상): {text(loose)}"
    left = list(ledger_ids)
    ledger, seen = [], []
    for u in loose:
        if u.id in left:
            left.remove(u.id)
            ledger.append(u)
        else:
            seen.append(u)
    bits = []
    if ledger:
        bits.append(f"장부 보유(자리 미상): {text(ledger)}")
    if seen:
        bits.append(f"화면 확인(자리 미상): {text(seen)}")
    return " / ".join(bits) or None


def equipped_groups(state: GameState, board: list[UnitRow], bench: list[UnitRow] | None,
                    names: NameBook) -> list[ItemGroup]:
    """장착 아이템을 소유자별로. 소유자 = 그 칸의 유닛(이름 미상이어도 자리로 구분한다).

    `items.equipped`에 있는데 어느 칸에도 붙지 않은 것(자리 미상 유닛의 아이템 등)은 "소유자 미상"으로 모은다.
    """
    groups: list[ItemGroup] = []
    on_units: Counter[str] = Counter()
    for row in [*board, *(bench or [])]:
        if row.empty or not (row.items or row.unread_items):
            continue
        groups.append(ItemGroup(f"{row.name} ({row.pos})",
                                row.items + ("?",) * row.unread_items))
        on_units.update(row.items)
    equipped = list(state.items.equipped) if state.items is not None else []
    rest = Counter(names.name(i.id) for i in equipped) - on_units
    if rest:
        holders = {names.name(i.id): i.holder for i in equipped}
        loose = sorted(rest.elements())
        titled: dict[str, list[str]] = {}
        for item in loose:
            holder = holders.get(item)
            titled.setdefault(f"{names.name(holder)} (자리 미상)" if holder else "소유자 미상", []).append(item)
        groups += [ItemGroup(t, tuple(v)) for t, v in titled.items()]
    return groups


def unused_groups(state: GameState, names: NameBook, threshold: float) -> list[ItemGroup] | None:
    """아이템 벤치(미장착) — 재료/완성/상징/기타. 못 읽었으면 None."""
    items = state.items
    if items is None:
        return None
    out: list[ItemGroup] = []
    for attr, title in ITEM_GROUPS:
        refs: list[ItemRef] = list(getattr(items, attr))
        if not refs:
            continue
        labels = tuple(names.name(r.id) + (f"({r.confidence:.2f})" if r.confidence < threshold else "")
                       for r in refs)
        out.append(ItemGroup(f"{title} {len(refs)}", labels,
                             low_confidence=any(r.confidence < threshold for r in refs)))
    return out


def _conf(state: GameState, name: str) -> str:
    if getattr(state, name, None) is None:
        return "-"
    return f"{state.confidence_of(name):.2f}"


def _source(state: GameState, name: str) -> str:
    src = state.field_source.get(name)
    if src is None:
        return ""
    label = {FieldSource.VISION: "화면", FieldSource.TRACKED: "추적", FieldSource.MANUAL: "수동",
             FieldSource.FIXTURE: "정답"}.get(src, str(src))
    return f"({label})"


def _ago(seconds: float) -> str:
    if seconds < 1.0:
        return "방금"
    if seconds < 60:
        return f"{int(seconds)}초 전"
    return f"{int(seconds // 60)}분 전"


def build_view(snap: RecogSnapshot | None, names: NameBook, *, threshold: float = 0.6) -> RecogView:
    """스냅숏 → 표시 모델. 실패하지 않는다(모르는 값은 "-"/"읽지 못했습니다")."""
    view = RecogView()
    state = snap.state if snap is not None else None
    if state is None:
        view.header = [snap.label] if snap is not None and snap.label else []
        view.notice = (snap.message if snap is not None and snap.message
                       else "아직 인식 결과가 없습니다 — 게임 화면을 기다리는 중…")
        return view

    read = snap.board_read
    head: list[str] = []
    if snap.label:
        head.append(snap.label)
    head.append(state_line(state))
    bits = []
    if snap.recog_ms is not None:
        bits.append(f"인식 {snap.recog_ms:.0f}ms")
    if snap.groups:
        bits.append("읽은 묶음: " + ", ".join(snap.groups))
    if bits:
        head.append(" · ".join(bits))
    conf = [f"보드 {_conf(state, 'board')}{_source(state, 'board')}",
            f"벤치 {_conf(state, 'bench')}{_source(state, 'bench')}",
            f"아이템 {_conf(state, 'items')}"]
    if read is not None:
        try:
            conf.append(f"판독 {float(getattr(read, 'confidence', 0.0)):.2f}")
        except (TypeError, ValueError):
            pass
    head.append("신뢰도 " + " · ".join(conf))
    view.header = head
    view.frame_at = state.captured_at or snap.at

    view.board_visible = state.screen_mode in BOARD_MODES
    notices = []
    if snap.message:
        notices.append(snap.message)
    if not view.board_visible:
        tail = " — 아래는 마지막으로 읽은 값입니다" if (state.board or state.bench) else ""
        notices.append(f"보드가 보이지 않습니다 (화면: {screen_label(state.screen_mode)}){tail}")
    elif snap.groups and "board" not in snap.groups and read is not None:
        notices.append("이번 프레임에서는 보드를 다시 읽지 않았습니다(직전 판독)")
    view.notice = " · ".join(notices) or None

    view.board = [unit_row(u, on_bench=False, state=state, names=names, board_read=read)
                  for u in sorted(board_units(state, read), key=_board_sort_key)]
    view.bench = bench_rows(state, names, read)
    view.ledger_note = ledger_unplaced(state, read, names, snap.ledger_ids)
    if state.board is None and state.bench is None:
        view.board_note = ("유닛이 보이지 않습니다" if read is not None and not getattr(read, "count", 0)
                           else "읽지 못했습니다")
    else:
        unknown = sum(1 for r in view.board if r.source == "unknown")
        bits = [f"이름 미상 {unknown}기"] if unknown else []
        unresolved = getattr(read, "unresolved_items", 0) if read is not None else 0
        if isinstance(unresolved, int) and unresolved:
            bits.append(f"못 알아본 아이템 칸 {unresolved}(보드+벤치)")
        view.board_note = " · ".join(bits) or None
        bench_unknown = sum(1 for r in view.bench or () if not r.empty and r.source == "unknown")
        view.bench_note = f"이름 미상 {bench_unknown}기" if bench_unknown else None
    view.board_common_note = board_common_note(read, view.board, names)
    view.equipped = equipped_groups(state, view.board, view.bench, names)
    view.unused = unused_groups(state, names, threshold)
    if view.unused is not None and state.confidence_of("items") < threshold:
        view.unused_note = f"신뢰도 낮음 {state.confidence_of('items'):.2f}"
    return view


def view_lines(snap: RecogSnapshot | None, names: NameBook, *, threshold: float = 0.6,
               now: datetime | None = None, with_age: bool = True) -> list[str]:
    """콘솔용 단축: 스냅숏 → 텍스트 줄."""
    return build_view(snap, names, threshold=threshold).lines(now=now, with_age=with_age)


class ConsolePrinter:
    """`--live --no-overlay` + 인식 확인: 내용이 바뀐 갱신만 출력한다(프레임 나이 말고 같으면 건너뛴다)."""

    def __init__(self, names: NameBook, threshold: float = 0.6, out: Any = print) -> None:
        self.names = names
        self.threshold = threshold
        self.out = out
        self.prev: RecogSnapshot | None = None
        self._last_key: tuple[str, ...] | None = None

    def feed(self, update: Any) -> bool:
        snap = RecogSnapshot.from_update(update, self.prev)
        if snap is None:
            return False
        self.prev = snap
        view = build_view(snap, self.names, threshold=self.threshold)
        # 인식 시간·읽은 묶음은 매 프레임 달라진다 → 내용(보드·벤치·아이템·화면)이 같으면 다시 찍지 않는다
        key = tuple(build_view(replace(snap, recog_ms=None, groups=()), self.names,
                               threshold=self.threshold).lines(with_age=False))
        if key == self._last_key:
            return False
        self._last_key = key
        self.out("\n" + "\n".join(view.lines()))
        return True


__all__ = [
    "BOARD_MODES", "ConsolePrinter", "ItemGroup", "MENU_TEXT", "RecogSnapshot", "RecogView", "SOURCE_LABELS",
    "TITLE", "UnitRow", "build_view", "bench_rows", "equipped_groups", "find_slot", "pos_label", "unit_source",
    "unused_groups", "view_lines",
]
