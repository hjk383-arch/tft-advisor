"""보유 유닛 장부 — 상점 구매·판매 추적으로 "무엇을 가지고 있는지"를 안다.

vision은 보드의 3D 모델에서 챔피언을 식별하지 못한다(위치·성급·장착 아이템까지가 한계다).
그래서 **정체(identity)는 상점에서 추적한다**: 연속된 두 프레임 사이에 상점 칸이 사라지고
그만큼 골드가 줄었으면 그 챔피언을 샀다는 뜻이다. 판매·리롤·경험치 구매·라운드 전환도 같은
골드 수식으로 구분한다.

추론 규칙 (`PurchaseTracker`)
- **정산 단위는 프레임 쌍이 아니라 "미결 거래"다.** 상점 ROI와 골드 ROI는 서로 다른 묶음이라
  한 프레임 차이로 따로 도착할 수 있다(상점이 먼저 바뀌고 골드가 다음 프레임에 읽힌다).
  그래서 사라진 칸을 `pending`에 쌓아 두고, **골드 변화가 정확히 맞아떨어질 때** 한꺼번에 확정한다.
- 확정 조건: `쓴 골드 == 미결 칸들의 코스트 합`(+리롤 2, +경험치 4의 조합까지 허용).
  맞아떨어지면 `evidence="gold"`(신뢰도 1.0)로 장부에 넣는다.
- 골드를 아예 모를 때는 `evidence="slot"`(신뢰도 `SLOT_ONLY_CONFIDENCE`)로 넣는다 — 칸 하나가
  사라졌고 나머지 칸이 그대로면 "무엇을" 샀는지는 애매하지 않기 때문이다.
- **맞아떨어지지 않으면 추측하지 않는다.** `settle_s`(기본 2초) 안에 설명되지 않은 거래는 버리고
  `ambiguous`를 올린다. 이 값이 쌓이면 장부 신뢰도(`field_confidence`)가 내려가고, 임계값 아래로
  떨어지면 advisor는 보유 유닛을 "모름"으로 다룬다(없는 유닛을 지어내는 것보다 낫다).
- 리롤(2골드)·라운드 전환은 상점 칸이 3개 이상 한꺼번에 바뀌는 것으로 알아본다. 그때 미결 거래가
  남아 있으면 확정할 기회가 사라진 것이므로 `ambiguous`가 된다.
- 판매는 골드가 **늘었을 때**만 본다. 같은 라운드 안에서(스테이지가 그대로) 늘어난 양이 보유
  유닛의 판매가와 **유일하게** 맞을 때만 장부에서 뺀다. 여럿이 맞으면 애매한 것으로 둔다
  (라운드가 바뀌면 이자·연승 수입이 들어오므로 아예 보지 않는다).

- **장부와 무관한 골드 변화는 애매로 세지 않는다**(37 보고, live 5: 10분에 "설명되지 않는 변화" 10번). 창이 끝날 때:
  - 골드가 **늘었고** 미결 칸이 없다: 보드+벤치 유닛 수가 줄지 않았거나(둘 다 읽었을 때) · 라운드 전환 직후거나 ·
    늘어난 양이 장부 유닛의 판매가 어느 것과도 맞지 않으면 → 수입(라운드 수입·PvE 구슬·특성/증강 골드, `income`).
    유닛이 줄었거나 판매가가 맞는 유닛이 있는데 누구인지 모르면 → 애매(판매일 수 있다).
  - 골드가 줄었다: 미결 칸 코스트 합을 빼고 남은 양이 경험치(4의 배수, 경험치 막대가 늘었거나 막대를 못 읽음) +
    리롤 2(상점이 바뀌었거나 그동안 상점을 못 읽음)로 설명되면 → 구매 확정 + `xp`/`reroll`.
    미결 칸 없이 4의 배수만큼 줄면 경험치 막대를 `XP_WAIT_S`까지 기다린다.
  - 라운드가 바뀐 프레임에 골드를 못 읽었으면 골드 기준을 비운다(다음에 읽은 값이 새 기준, 수입을 오해하지 않게).
    라운드가 바뀐 뒤 `INCOME_WINDOW_S` 안에 늘어난 골드도 수입이다.
  - 홀수만큼 줄었는데 사라진 칸이 없다(보지 못한 구매) · 유닛이 줄면서 골드가 늘었는데 판매가가 유일하게 맞지 않는다 → 여전히 애매.

장부(`UnitLedger`)는 챔피언 → **1성 등가 사본 수**를 센다(3사본 = 2성, 9사본 = 3성).
상점 밖에서 얻는 유닛(공동 선택, 증강, 모루/구슬)은 상점 이벤트가 없으므로 `add()`로 직접 넣는다.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ..contracts import GameState, ScreenMode, ShopSlotKind

log = logging.getLogger(__name__)

STAR_COPIES: dict[int, int] = {1: 1, 2: 3, 3: 9, 4: 27}
"""성급 → 1성 등가 사본 수(3개가 모이면 한 단계 올라간다)."""

REROLL_COST = 2
XP_COST = 4
XP_WAIT_S = 6.0
"""미결 칸 없이 4의 배수만큼 줄면(경험치 구매로 보임) 경험치 막대가 따라 바뀌기를 이만큼 기다린다."""
XP_AMOUNT = 4
SHOP_SLOTS = 5
REPLACE_MIN_SLOTS = 3
INCOME_WINDOW_S = 8.0
"""라운드(스테이지 글자)가 바뀐 뒤 이 시간 안에 늘어난 골드는 라운드 수입으로 본다(골드 글자가 늦게 바뀌어 읽힌다)."""
MAX_REROLL_XP_GOLD = 40
"""미결 칸 없이 짝수만큼 줄어든 골드를 리롤·경험치로 볼 상한(더 크면 OCR 오독일 가능성이 커 애매로 둔다)."""
"""이만큼의 칸이 한꺼번에 다른 유닛으로 바뀌면 상점 새로고침(리롤·라운드 전환)으로 본다."""
SLOT_ONLY_CONFIDENCE = 0.6
"""골드를 못 읽어 칸 변화만으로 넣은 구매의 신뢰도."""
MAX_EVENTS = 80
"""세션 파일에 남기는 최근 장부 이벤트 수(디버깅·되돌리기용)."""

SOURCE_LABELS = {
    "shop": "상점",
    "manual": "수동 입력",
    "carousel": "공동 선택",
    "augment": "증강",
    "anvil": "모루/구슬",
    "vision": "화면 인식",
    "external": "상점 밖",
}
"""장부에 들어온 경로(사용자 표시용)."""

EventKind = Literal["buy", "sell", "add", "remove", "set", "reroll", "xp", "income", "ambiguous", "noise", "clear"]


def bodies_for(copies: int) -> list[int]:
    """1성 등가 사본 수 → 실제 유닛들의 성급 목록(내림차순). 3사본=2성, 9사본=3성 자동 합성 규칙."""
    if copies <= 0:
        return []
    n3, rest = divmod(copies, STAR_COPIES[3])
    n2, n1 = divmod(rest, STAR_COPIES[2])
    return [3] * n3 + [2] * n2 + [1] * n1


def copies_for_star(star: int | None) -> int:
    return STAR_COPIES.get(star or 1, 1)


def sell_value(cost: int | None, star: int | None) -> int | None:
    """유닛 판매가. 1코스트는 전액, 2코스트 이상은 2성 3c-1 / 3성 9c-2(게임 규칙)."""
    if cost is None:
        return None
    n = copies_for_star(star)
    if cost <= 1:
        return cost * n
    return cost * n - ((star or 1) - 1)


# ---------------------------------------------------------------------------
# 코스트 표(정적 데이터)
# ---------------------------------------------------------------------------


class CostBook:
    """ID → 코스트. `champions.json`(과 특수 상품)에서 읽고 캐시한다."""

    def __init__(self, static: Any = None) -> None:
        self._static = static
        self._cache: dict[str, int | None] = {}

    @property
    def static(self) -> Any:
        if self._static is None:
            from ..static_data import load_static

            self._static = load_static()
        return self._static

    def cost(self, canonical_id: str | None) -> int | None:
        if not canonical_id:
            return None
        if canonical_id in self._cache:
            return self._cache[canonical_id]
        value: int | None = None
        try:
            kind = self.static.kind_of(canonical_id)
            row = self.static.get(kind, canonical_id) if kind else None
            raw = (row or {}).get("cost")
            value = int(raw) if raw is not None else None
        except Exception:   # 정적 데이터가 없어도 추적은 계속된다(코스트 미상 = 애매)
            log.debug("코스트 조회 실패: %s", canonical_id, exc_info=True)
        self._cache[canonical_id] = value
        return value


# ---------------------------------------------------------------------------
# 장부
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Body:
    """장부가 아는 유닛 1기(합성 규칙으로 사본 수에서 계산한 것)."""

    champion_id: str
    star: int
    confidence: float = 1.0
    source: str = "shop"


@dataclass
class LedgerEvent:
    """장부 변화 1건(세션 파일·디버깅용)."""

    kind: EventKind
    unit_id: str | None = None
    copies: int = 0
    gold: int | None = None
    evidence: str = ""
    note: str | None = None
    at: float | None = None
    stage: str | None = None

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None and v != ""}

    @classmethod
    def from_json(cls, raw: Mapping) -> LedgerEvent:
        return cls(
            kind=raw.get("kind", "add"), unit_id=raw.get("unit_id"), copies=int(raw.get("copies") or 0),
            gold=raw.get("gold"), evidence=str(raw.get("evidence") or ""), note=raw.get("note"),
            at=raw.get("at"), stage=raw.get("stage"),
        )


@dataclass
class UnitLedger:
    """챔피언 → 1성 등가 사본 수. 성급은 사본 수에서 계산한다(3=2성, 9=3성)."""

    copies: dict[str, int] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    ambiguous: int = 0
    """설명되지 않은 거래 수. 쌓이면 장부 신뢰도가 내려간다(advisor가 '모름'으로 다룬다)."""
    manual: bool = False
    """사용자가 직접 고친 적이 있는가(표시·출처용)."""
    events: list[LedgerEvent] = field(default_factory=list)

    # -------------------------------------------------------------- 변경
    def add(self, champion_id: str, copies: int = 1, *, source: str = "shop",
            confidence: float = 1.0, note: str | None = None, at: float | None = None,
            kind: EventKind = "add") -> LedgerEvent:
        cur = self.copies.get(champion_id, 0)
        self.copies[champion_id] = cur + max(0, copies)
        self.sources.setdefault(champion_id, source)
        if source == "manual":
            self.sources[champion_id] = "manual"
            self.manual = True
        old = self.confidence.get(champion_id)
        self.confidence[champion_id] = confidence if old is None else min(old, confidence)
        return self._log(LedgerEvent(kind=kind, unit_id=champion_id, copies=copies, evidence=source,
                                     note=note, at=at))

    def remove(self, champion_id: str, copies: int = 1, *, source: str = "manual",
               note: str | None = None, at: float | None = None, kind: EventKind = "remove") -> LedgerEvent:
        cur = self.copies.get(champion_id, 0)
        left = max(0, cur - max(0, copies))
        if left:
            self.copies[champion_id] = left
        else:
            self.copies.pop(champion_id, None)
            self.sources.pop(champion_id, None)
            self.confidence.pop(champion_id, None)
        if source == "manual":
            self.manual = True
        return self._log(LedgerEvent(kind=kind, unit_id=champion_id, copies=-min(cur, copies),
                                     evidence=source, note=note, at=at))

    def set_copies(self, champion_id: str, copies: int, *, source: str = "manual",
                   confidence: float = 1.0) -> LedgerEvent:
        """사본 수를 그대로 정한다(0이면 삭제). 수동 교정의 기본 수단."""
        if copies <= 0:
            return self.remove(champion_id, self.copies.get(champion_id, 0), source=source, kind="set")
        self.copies[champion_id] = copies
        self.sources[champion_id] = source
        self.confidence[champion_id] = confidence
        if source == "manual":
            self.manual = True
        return self._log(LedgerEvent(kind="set", unit_id=champion_id, copies=copies, evidence=source))

    def set_star(self, champion_id: str, star: int, *, source: str = "manual") -> LedgerEvent:
        """"이 챔피언은 N성입니다" → 사본 수를 그 성급의 등가로 맞춘다(남는 사본은 유지)."""
        need = copies_for_star(star)
        cur = self.copies.get(champion_id, 0)
        extra = cur % need if cur > need else 0
        return self.set_copies(champion_id, need + extra, source=source)

    def clear(self, *, source: str = "manual") -> LedgerEvent:
        self.copies.clear()
        self.sources.clear()
        self.confidence.clear()
        self.ambiguous = 0
        if source == "manual":
            self.manual = True
        return self._log(LedgerEvent(kind="clear", evidence=source))

    def confirm(self) -> None:
        """사용자가 "지금 장부가 맞습니다"라고 확인했다 → 쌓인 애매 건수를 지운다."""
        self.ambiguous = 0
        self.manual = True

    def apply(self, events: Iterable[LedgerEvent]) -> list[LedgerEvent]:
        """`PurchaseTracker`가 만든 이벤트를 장부에 반영한다."""
        out = []
        for e in events:
            if e.kind == "buy" and e.unit_id:
                conf = 1.0 if e.evidence == "gold" else SLOT_ONLY_CONFIDENCE
                self.add(e.unit_id, max(1, e.copies), source="shop", confidence=conf, note=e.note,
                         at=e.at, kind="buy")
            elif e.kind == "sell" and e.unit_id:
                self.remove(e.unit_id, max(1, -e.copies if e.copies < 0 else e.copies),
                            source="shop", note=e.note, at=e.at, kind="sell")
            elif e.kind == "ambiguous":
                self.ambiguous += 1
                self._log(e)
            else:
                self._log(e)
            out.append(e)
        return out

    def _log(self, event: LedgerEvent) -> LedgerEvent:
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            del self.events[:-MAX_EVENTS]
        return event

    # -------------------------------------------------------------- 조회
    def total_copies(self) -> int:
        return sum(self.copies.values())

    def bodies(self, costs: CostBook | None = None) -> list[Body]:
        """실제 유닛 목록. 정렬은 (성급 내림 → 코스트 내림 → ID)로 고정한다(표시·배치가 결정적이어야 한다)."""
        out: list[Body] = []
        for cid, n in self.copies.items():
            conf = self.confidence.get(cid, 1.0)
            src = self.sources.get(cid, "shop")
            out += [Body(cid, star, conf, src) for star in bodies_for(n)]
        cost_of = (lambda c: costs.cost(c) or 0) if costs is not None else (lambda c: 0)
        out.sort(key=lambda b: (-b.star, -cost_of(b.champion_id), b.champion_id))
        return out

    def summary_rows(self) -> list[tuple[str, int, int, str]]:
        """(챔피언 ID, 사본 수, 최고 성급, 출처) — 수동 교정 UI·콘솔 표시용."""
        rows = []
        for cid, n in sorted(self.copies.items()):
            stars = bodies_for(n)
            rows.append((cid, n, stars[0] if stars else 0, self.sources.get(cid, "shop")))
        return rows

    # -------------------------------------------------------------- 영속
    def to_json(self) -> dict:
        return {
            "copies": dict(self.copies),
            "sources": dict(self.sources),
            "confidence": {k: round(v, 3) for k, v in self.confidence.items()},
            "ambiguous": self.ambiguous,
            "manual": self.manual,
            "events": [e.to_json() for e in self.events],
        }

    @classmethod
    def from_json(cls, raw: Mapping | None) -> UnitLedger:
        raw = raw or {}
        led = cls()
        for k, v in (raw.get("copies") or {}).items():
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                led.copies[str(k)] = n
        led.sources = {str(k): str(v) for k, v in (raw.get("sources") or {}).items() if str(k) in led.copies}
        for k, v in (raw.get("confidence") or {}).items():
            try:
                led.confidence[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        led.ambiguous = int(raw.get("ambiguous") or 0)
        led.manual = bool(raw.get("manual"))
        led.events = [LedgerEvent.from_json(e) for e in (raw.get("events") or []) if isinstance(e, Mapping)]
        return led


# ---------------------------------------------------------------------------
# 프레임 관측
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotKey:
    """상점 한 칸의 비교용 표현. 빈 칸과 '무언가 있지만 못 읽음'을 구분한다(빈 칸만 구매 신호다)."""

    kind: str
    id: str | None = None
    cost: int | None = None

    @property
    def is_unit(self) -> bool:
        return self.kind in ("champion", "special")

    @property
    def is_empty(self) -> bool:
        return self.kind == "empty"

    def same_offer(self, other: SlotKey) -> bool:
        return self.kind == other.kind and self.id == other.id


EMPTY_SLOT = SlotKey("empty")
UNKNOWN_SLOT = SlotKey("unknown")


@dataclass(frozen=True)
class FrameObs:
    """추론에 쓰는 한 프레임의 요약(병합된 GameState에서 만든다)."""

    at: float
    mode: ScreenMode = ScreenMode.UNKNOWN
    stage: str | None = None
    level: int | None = None
    xp: tuple[int, int] | None = None
    gold: int | None = None
    shop: tuple[SlotKey, ...] | None = None
    units: int | None = None
    """보드+벤치 유닛 수(둘 다 읽었을 때). 골드가 늘 때 판매(유닛 감소)와 수입을 가른다."""

    @classmethod
    def of(cls, state: GameState, at: float, costs: CostBook) -> FrameObs:
        shop: tuple[SlotKey, ...] | None = None
        if state.shop is not None:
            keys = []
            for slot in state.shop:
                if slot.kind == ShopSlotKind.EMPTY:
                    keys.append(EMPTY_SLOT)
                elif slot.kind == ShopSlotKind.UNKNOWN or not slot.id:
                    keys.append(UNKNOWN_SLOT)
                else:
                    cost = slot.cost if slot.cost is not None else costs.cost(slot.id)
                    keys.append(SlotKey(str(slot.kind.value), slot.id, cost))
            shop = tuple(keys)
        units = None
        if state.board is not None and state.bench is not None:
            units = len(state.board) + len(state.bench)
        return cls(at=at, mode=state.screen_mode, stage=state.stage, level=state.level,
                   xp=tuple(state.xp) if state.xp else None, gold=state.gold, shop=shop, units=units)


@dataclass
class _Pending:
    """확정을 기다리는 사라진 칸 1개."""

    slot: int
    key: SlotKey
    at: float


# ---------------------------------------------------------------------------
# 구매 추론
# ---------------------------------------------------------------------------


@dataclass
class LedgerCfg:
    """추론·신뢰도 설정(`[app] ledger_*`)."""

    enabled: bool = True
    settle_s: float = 2.0
    confidence: float = 0.85
    confidence_uncertain: float = 0.5
    uncertain_after: int = 3
    track_sales: bool = True

    @classmethod
    def from_settings(cls, settings: Any) -> LedgerCfg:
        app = getattr(settings, "app", None)
        if app is None:
            return cls()
        return cls(
            enabled=getattr(app, "ledger_enabled", True),
            settle_s=getattr(app, "ledger_settle_s", 2.0),
            confidence=getattr(app, "ledger_confidence", 0.85),
            confidence_uncertain=getattr(app, "ledger_confidence_uncertain", 0.5),
            uncertain_after=getattr(app, "ledger_uncertain_after", 3),
            track_sales=getattr(app, "ledger_track_sales", True),
        )


def field_confidence(cfg: LedgerCfg, ambiguous: int, known: int = 1, total: int = 1) -> float:
    """`GameState.confidence["board"/"bench"]`에 넣을 값.

    애매한 거래가 쌓일수록 내려간다(`uncertain_after`건이면 `confidence_uncertain`). vision이 본 유닛 수보다
    이름을 아는 유닛이 적으면 그 비율만큼 더 내린다. `[vision] state_min_confidence`(0.6) 아래로 내려가면
    advisor는 보유 유닛을 "모름"으로 다룬다.
    """
    span = max(0.0, cfg.confidence - cfg.confidence_uncertain)
    ratio = min(1.0, ambiguous / max(1, cfg.uncertain_after))
    value = cfg.confidence - span * ratio
    if total > 0:
        value *= max(0, known) / total
    return round(max(0.0, min(1.0, value)), 3)


class PurchaseTracker:
    """상점 칸 변화 + 골드 변화 → 구매/판매/리롤/경험치 이벤트. 장부는 건드리지 않고 이벤트만 만든다."""

    def __init__(self, cfg: LedgerCfg | None = None, costs: CostBook | None = None) -> None:
        self.cfg = cfg or LedgerCfg()
        self.costs = costs or CostBook()
        self.reset()

    def reset(self) -> None:
        self._shop: tuple[SlotKey, ...] | None = None
        self._anchor_gold: int | None = None
        self._stage: str | None = None
        self._level: int | None = None
        self._xp: tuple[int, int] | None = None
        self._anchor_level: int | None = None
        self._anchor_xp: tuple[int, int] | None = None
        """정산 기준(골드를 맞춘 순간)의 레벨·경험치. 경험치 막대가 골드보다 **먼저** 바뀌어 읽히면 직전 프레임과만 비교해서는
        경험치 구매를 못 알아본다(30 보고: "미결 0칸, 골드 4"가 설명되지 않는 변화로 남았다)."""
        self._pending: list[_Pending] = []
        self._open_at: float | None = None
        self._anchor_units: int | None = None
        self._shop_gap = False                     # 기준 이후 상점을 못 읽은 프레임이 있었다(리롤을 놓쳤을 수 있다)
        self._income_until: float | None = None   # 라운드 전환 뒤 수입으로 볼 시각
        self.buys = 0
        self.sales = 0
        self.rerolls = 0
        self.xp_buys = 0
        self.noise = 0
        self.income = 0          # 수입으로 본 골드 증가(애매로 세지 않음)
        self.reroll_xp = 0       # 리롤·경험치로 본 골드 감소(칸 증거 없음)

    # ------------------------------------------------------------------
    def observe(self, obs: FrameObs, ledger: UnitLedger) -> list[LedgerEvent]:
        """프레임 1장을 넣고 확정된 이벤트를 돌려준다(아직 확정되지 않으면 빈 목록)."""
        if not self.cfg.enabled:
            return []
        prev_shop, self._shop = self._shop, obs.shop
        if obs.shop is None:
            self._shop_gap = True
        stage_changed = bool(obs.stage and self._stage and obs.stage != self._stage)
        level_up = self._level is not None and obs.level is not None and obs.level > self._level
        xp_up = bool(self._xp and obs.xp and obs.xp[0] > self._xp[0]) or level_up or self._xp_up_since_anchor(obs)
        events: list[LedgerEvent] = []

        if obs.shop is None or prev_shop is None or len(prev_shop) != len(obs.shop):
            # 비교할 상점이 없다 → 미결 거래는 이번이 마지막 기회다(설명되면 확정, 아니면 애매)
            events += self._settle(obs, ledger, stage_changed, xp_up, force=True)
            self._anchor(obs, stage_changed)
            return events

        vanished, changed = [], []
        for i, (before, after) in enumerate(zip(prev_shop, obs.shop, strict=False)):
            if before.same_offer(after):
                continue
            if before.is_unit and after.is_empty:
                vanished.append(_Pending(i, before, obs.at))
            else:
                changed.append(i)

        if len(changed) >= REPLACE_MIN_SLOTS or stage_changed:
            # 리롤 또는 라운드 전환: 이번 정산이 마지막 기회다
            reroll = not stage_changed
            self._pending += vanished
            events += self._settle(obs, ledger, stage_changed, xp_up, reroll=reroll, force=True)
            self._anchor(obs, stage_changed)
            if reroll and not stage_changed:
                self.rerolls += 1
            return events

        if changed:   # 1~2칸이 다른 유닛으로 바뀌었다 = 인식 흔들림(라운드 안에서는 일어나지 않는다)
            self.noise += len(changed)
            log.debug("상점 칸 %s이(가) 새로고침 없이 바뀌었습니다(인식 흔들림)", changed)
        self._pending += vanished
        if vanished and self._open_at is None:
            self._open_at = obs.at
        events += self._settle(obs, ledger, stage_changed, xp_up)
        self._remember(obs)
        return events

    # ------------------------------------------------------------------
    def _remember(self, obs: FrameObs) -> None:
        if obs.stage:
            self._stage = obs.stage
        if obs.level is not None:
            self._level = obs.level
        if obs.xp is not None:
            self._xp = obs.xp

    def _anchor(self, obs: FrameObs, stage_changed: bool = False) -> None:
        self._pending = []
        self._open_at = None
        moved = obs.gold is not None and obs.gold != self._anchor_gold
        if obs.gold is not None:
            self._anchor_gold = obs.gold
        elif stage_changed:
            # 라운드가 바뀌었는데 골드를 못 읽었다: 예전 기준과 비교하면 라운드 수입이 "설명되지 않는 변화"가 된다
            self._anchor_gold = None
        if stage_changed:
            self._income_until = obs.at + INCOME_WINDOW_S
        if obs.units is not None:
            self._anchor_units = obs.units
        self._shop_gap = False
        self._remember(obs)
        # 경험치 기준은 골드 기준이 **움직일 때만** 옮긴다: 골드가 그대로인 프레임에서 옮기면 막대가 먼저 바뀐 경우를 놓친다
        if moved or stage_changed or self._anchor_xp is None:
            self._anchor_level, self._anchor_xp = self._level, self._xp

    def _xp_up_since_anchor(self, obs: FrameObs) -> bool:
        """정산 기준 이후 레벨이 올랐거나 같은 레벨에서 경험치가 늘었다(경험치 구매의 화면 증거)."""
        level = obs.level if obs.level is not None else self._level
        xp = obs.xp if obs.xp is not None else self._xp
        if self._anchor_level is not None and level is not None and level > self._anchor_level:
            return True
        return bool(self._anchor_xp and xp and (level is None or level == self._anchor_level)
                    and xp[0] > self._anchor_xp[0])

    def _settle(self, obs: FrameObs, ledger: UnitLedger, stage_changed: bool, xp_up: bool,
                *, reroll: bool = False, force: bool = False) -> list[LedgerEvent]:
        spent = None if (obs.gold is None or self._anchor_gold is None) else self._anchor_gold - obs.gold
        if spent is not None and spent != 0 and self._open_at is None:
            self._open_at = obs.at   # 골드만 먼저 움직였다 → 상점 칸이 따라오기를 기다린다
        events = self._explain(obs, ledger, spent, stage_changed, xp_up, reroll)
        if events is not None:
            self._anchor(obs)
            return events
        waited = obs.at - self._open_at if self._open_at is not None else 0.0
        limit = self.cfg.settle_s
        rest = _even_rest(spent, self._pending) if spent is not None else None
        if rest is not None and rest > 0 and rest % XP_COST == 0:
            limit = max(limit, XP_WAIT_S)   # 경험치 막대가 골드보다 늦게 읽힐 수 있다 → 조금 더 기다린다
        expired = force or (self._open_at is not None and waited > limit)
        if not expired:
            return []
        xp_ok = xp_up or obs.xp is None or self._anchor_xp is None
        return self._expire(obs, "골드 변화가 맞지 않습니다", spent=spent, ledger=ledger, reroll=reroll, xp_ok=xp_ok)

    def _explain(self, obs: FrameObs, ledger: UnitLedger, spent: int | None, stage_changed: bool,
                 xp_up: bool, reroll: bool) -> list[LedgerEvent] | None:
        """설명이 되면 이벤트 목록, 아니면 None(더 기다린다)."""
        pending = self._pending
        if spent is None:
            return None   # 골드를 모른다 → 창이 끝날 때 칸 증거만으로 판단한다
        if stage_changed:
            # 라운드 전환: 수입(이자·연승)이 섞여 골드 수식을 쓸 수 없다. 칸 증거만 쓴다.
            return self._slot_only(obs, pending) if pending else []
        need = 0
        unknown_cost = False
        for p in pending:
            if p.key.cost is None:
                unknown_cost = True
            else:
                need += p.key.cost
        if unknown_cost:
            champs = [p for p in pending if p.key.kind == "champion"]
            if champs:
                return None   # 코스트를 모르는 칸이 섞여 있으면 수식이 성립하지 않는다
            return []         # 특수 상품만 사라졌다 → 장부와 무관
        rest = spent - need - (REROLL_COST if reroll else 0)
        if rest == 0:
            return self._commit(obs, pending, "gold", spent)
        if (rest > 0 and rest % XP_COST == 0 and rest <= XP_COST * 4
                and (xp_up or (obs.xp is None and obs.level is None))):
            out = self._commit(obs, pending, "gold", spent)
            for _ in range(rest // XP_COST):
                self.xp_buys += 1
                out.append(LedgerEvent(kind="xp", gold=-XP_COST, evidence="gold", at=obs.at, stage=obs.stage))
            return out
        if rest < 0 and not pending and self.cfg.track_sales:
            # 미결 구매가 있으면 판매로 보지 않는다(골드가 아직 안 읽힌 구매를 판매로 오인할 수 있다).
            # 보드+벤치 유닛 수를 아는데 줄지 않았으면 아직 판매로 보지 않는다(구슬·특성 골드일 수 있다, 37 보고) — 창 끝에 다시 본다
            if self._anchor_units is not None and obs.units is not None and obs.units >= self._anchor_units:
                return None
            sold = self._match_sale(-rest, ledger)
            if sold is not None:
                out = self._commit(obs, pending, "gold", spent)
                cid, star, copies = sold
                self.sales += 1
                out.append(LedgerEvent(kind="sell", unit_id=cid, copies=copies, gold=-rest, evidence="gold",
                                       note=f"{star}성 판매", at=obs.at, stage=obs.stage))
                return out
        return None

    def _explain_loose(self, obs: FrameObs, pending: list[_Pending], spent: int | None,
                       units_before: int | None, *, ledger: UnitLedger | None = None, reroll: bool = False,
                       xp_ok: bool = False) -> list[LedgerEvent] | None:
        """창이 끝날 때(정확한 수식이 맞지 않았다) 장부와 무관한 골드 변화를 가려낸다(37 보고).
        반환: 이벤트 목록(애매 아님) 또는 None(여전히 애매 — 장부가 틀렸을 수 있다)."""
        if spent is None:
            return None
        if not pending and spent < 0:
            gained = -spent
            known = units_before is not None and obs.units is not None
            if known and obs.units < units_before:
                # 유닛이 줄었다 = 판매. 판매가가 유일하게 맞으면 뺀다, 아니면 누구인지 모른다(애매)
                sold = self._match_sale(gained, ledger) if ledger is not None and self.cfg.track_sales else None
                if sold is None:
                    return None
                cid, star, copies = sold
                self.sales += 1
                return [LedgerEvent(kind="sell", unit_id=cid, copies=copies, gold=gained, evidence="gold",
                                    note=f"{star}성 판매", at=obs.at, stage=obs.stage)]
            window = self._income_until is not None and obs.at <= self._income_until
            could_sell = ledger is not None and any(
                sell_value(self.costs.cost(b.champion_id), b.star) == gained for b in ledger.bodies(self.costs))
            if known or window or not could_sell:
                # 유닛이 그대로 · 라운드 수입 시간 · 장부 유닛 판매가와 안 맞는다 → 장부와 무관한 수입
                self.income += 1
                why = "라운드 수입" if window else "수입(구슬·특성·증강 골드)"
                return [LedgerEvent(kind="income", gold=gained, evidence="gold", note=why, at=obs.at, stage=obs.stage)]
            return None
        if spent <= 0:
            return None
        rest = _even_rest(spent, pending)
        if rest is None or rest < 0 or rest > MAX_REROLL_XP_GOLD:
            return None
        for r in ((0, REROLL_COST) if reroll else (0,)):
            left = rest - r
            if left < 0 or left % XP_COST or (left and not xp_ok):
                continue
            out = self._commit(obs, pending, "gold", spent) if pending else []
            if r:
                self.rerolls += 1
                out.append(LedgerEvent(kind="reroll", gold=-r, evidence="gold", note="리롤(상점 변화 못 봄)",
                                       at=obs.at, stage=obs.stage))
            for _ in range(left // XP_COST):
                self.xp_buys += 1
                out.append(LedgerEvent(kind="xp", gold=-XP_COST, evidence="gold", note="경험치(늦게 확인)",
                                       at=obs.at, stage=obs.stage))
            self.reroll_xp += 1
            return out
        return None

    def _match_sale(self, gained: int, ledger: UnitLedger) -> tuple[str, int, int] | None:
        """늘어난 골드와 판매가가 **유일하게** 맞는 보유 유닛. 여럿이면 None(추측하지 않는다)."""
        hits = []
        for body in ledger.bodies(self.costs):
            if sell_value(self.costs.cost(body.champion_id), body.star) == gained:
                hits.append((body.champion_id, body.star, copies_for_star(body.star)))
        uniq = {h[0]: h for h in hits}
        if len(hits) == 1 or len(uniq) == 1:
            return hits[0]
        return None

    def _commit(self, obs: FrameObs, pending: list[_Pending], evidence: str,
                spent: int | None) -> list[LedgerEvent]:
        out: list[LedgerEvent] = []
        for p in pending:
            if p.key.kind != "champion" or not p.key.id:
                continue   # 특수 상품은 장부에 넣지 않는다(챔피언이 아니다)
            self.buys += 1
            out.append(LedgerEvent(kind="buy", unit_id=p.key.id, copies=1, gold=p.key.cost, evidence=evidence,
                                   at=obs.at, stage=obs.stage))
        if pending:
            log.info("구매 확정 %d건(증거 %s, 골드 %s)", len(out), evidence, spent)
        return out

    def _slot_only(self, obs: FrameObs, pending: list[_Pending]) -> list[LedgerEvent] | None:
        """골드 증거 없이 칸 변화만으로 확정할 수 있는가 — 사라진 칸이 전부 챔피언일 때만."""
        champs = [p for p in pending if p.key.kind == "champion" and p.key.id]
        if len(champs) != len(pending) or not champs:
            return None
        return self._commit(obs, champs, "slot", None)

    def _expire(self, obs: FrameObs, why: str, *, spent: int | None = None, ledger: UnitLedger | None = None,
                reroll: bool = False, xp_ok: bool = False) -> list[LedgerEvent]:
        """창이 끝났는데 설명이 안 된다 → 칸 증거만으로 되는 것만 넣고, 나머지는 애매로 남긴다."""
        pending, self._pending = self._pending, []
        self._open_at = None
        units_before = self._anchor_units
        shop_gap = self._shop_gap
        # 설명하지 못한 변화는 한 번만 알린다: 골드 기준을 지금 값으로 옮긴다. 옮기지 않으면 같은 차이가 settle_s마다
        # 다시 "설명되지 않는 변화"가 되어 ambiguous가 끝없이 쌓이고(세션 기록: 같은 "골드 12"가 10번), 그 뒤의 구매·판매도
        # 낡은 기준과 비교되어 모두 어긋난다(경험치 구매 하나를 놓치면 다음 수입이 "골드 -4"로 남는다).
        self._anchor(obs)
        if not pending and not spent:
            return []
        if pending and spent is None:
            out = self._slot_only(obs, pending)
            if out is not None:
                return out
        explained = self._explain_loose(obs, pending, spent, units_before, ledger=ledger,
                                        reroll=reroll or shop_gap, xp_ok=xp_ok)
        if explained is not None:
            return explained
        note = f"{why} (미결 {len(pending)}칸, 골드 {spent if spent is not None else '?'})"
        log.info("장부: 설명되지 않는 변화 — %s", note)
        return [LedgerEvent(kind="ambiguous", copies=len(pending), gold=spent, evidence="none",
                            note=note, at=obs.at, stage=obs.stage)]


def _even_rest(spent: int, pending: list[_Pending]) -> int | None:
    """쓴 골드 − 미결 칸 코스트 합. 코스트를 모르는 칸이 있으면 None."""
    need = 0
    for p in pending:
        if p.key.cost is None:
            return None
        need += p.key.cost
    return spent - need


__all__ = [
    "Body", "CostBook", "EMPTY_SLOT", "FrameObs", "LedgerCfg", "LedgerEvent", "PurchaseTracker",
    "SLOT_ONLY_CONFIDENCE", "SOURCE_LABELS", "STAR_COPIES", "SlotKey", "UNKNOWN_SLOT", "UnitLedger",
    "bodies_for", "copies_for_star", "field_confidence", "sell_value",
]
