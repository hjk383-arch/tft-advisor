"""추천 결과를 한국어 텍스트로 옮긴다 — 콘솔(`--screenshot`, `--no-overlay`)과 오버레이가 같은 문구를 쓴다.

여기 있는 함수는 순수하다(입출력·Qt 없음). 오버레이는 이 줄들을 HTML로 감싸 그리고, 콘솔은 그대로 출력한다.
문구를 한 곳에 모아 둔 이유: 게임 클라이언트가 한국어라 사용자가 보는 이름·태그가 두 경로에서 달라지면 안 된다.

**표시 순서 계약**: `Recommendation.target_comps`는 advisor가 준 순서 그대로 보여 준다. 타이브레이커·히스테리시스
보호 때문에 score가 단조가 아닐 수 있다. **점수로 다시 정렬하지 않는다**(02_app-integrator_report.md C3.2).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..contracts import (
    UNKNOWN_UNIT_ID, BoardPlan, FallbackReason, GameState, ItemReadiness, Recommendation, ScreenMode, ShopSlotKind,
    TargetComp, UnitOnBoard,
)
from ..unit_status import CONFIRMED_NAME_THRESHOLD, UnitsKnowledge, units_knowledge, units_note
from .names import NameBook

SCREEN_LABELS: dict[ScreenMode, str] = {
    ScreenMode.LOADING: "로딩",
    ScreenMode.PLANNING: "준비",
    ScreenMode.COMBAT: "전투",
    ScreenMode.AUGMENT_SELECT: "증강 선택",
    ScreenMode.CAROUSEL: "캐러셀",
    ScreenMode.ITEM_SELECT: "아이템 선택",
    ScreenMode.GAME_OVER: "게임 종료",
    ScreenMode.UNKNOWN: "알 수 없음",
}
FIELD_LABELS: dict[str, str] = {
    "stage": "스테이지", "level": "레벨", "xp": "경험치", "gold": "골드", "streak": "연승/연패",
    "hp": "체력", "shop_odds": "상점 확률", "shop": "상점", "board": "보드", "bench": "벤치",
    "items": "아이템", "augments_owned": "보유 증강", "augment_offer": "증강 후보", "active_traits": "특성",
}
FALLBACK_LABELS: dict[FallbackReason, str] = {
    FallbackReason.JEV_DISABLED: "Jev 미사용",
    FallbackReason.CIRCUIT_OPEN: "Jev 일시 차단",
    FallbackReason.AUTH: "Jev 인증 실패",
    FallbackReason.RATE_LIMITED: "Jev 요청 한도",
    FallbackReason.OVERLOADED: "Jev 서버 혼잡",
    FallbackReason.SERVER_ERROR: "Jev 서버 오류",
    FallbackReason.TIMEOUT: "Jev 시간 초과",
    FallbackReason.CONNECTION: "Jev 연결 실패",
    FallbackReason.BAD_REQUEST: "Jev 요청 오류",
}
ITEM_STATUS_LABELS = {"owned": "보유", "craftable": "조합가능", "missing": "부족"}
UNKNOWN_UNITS_TEXT = "보드 미인식(구매 추적 대기 · 수동 입력 가능)"
"""보드를 아예 읽지 못했을 때의 문구. 상태별 문구는 `tft_advisor.unit_status.units_note`가 만든다.

**보드를 읽었지만 챔피언 이름만 모르는 경우**(`UnitsKnowledge.SEEN`)는 이 문구가 아니다 —
자리·성급·장착 아이템은 이미 추천에 쓰이고 있으므로 "미인식"이라고 하면 사실과 다르다."""
CORE_FIELDS = ("stage", "level", "gold", "hp", "shop", "items", "board")
"""추천 품질에 직접 영향을 주는 필드 — 못 읽었으면 사용자에게 알린다."""


# ---------------------------------------------------------------------------
# 상태 요약
# ---------------------------------------------------------------------------


def screen_label(mode: ScreenMode | None) -> str:
    return SCREEN_LABELS.get(mode or ScreenMode.UNKNOWN, "알 수 없음")


def state_line(state: GameState) -> str:
    """한 줄 상태 요약: 화면 · 스테이지 · 레벨 · 골드 · 연승 · 체력."""
    bits = [screen_label(state.screen_mode)]
    if state.stage:
        bits.append(f"스테이지 {state.stage}")
    if state.level is not None:
        xp = f" ({state.xp[0]}/{state.xp[1]})" if state.xp else ""
        bits.append(f"레벨 {state.level}{xp}")
    if state.gold is not None:
        bits.append(f"골드 {state.gold}")
    if state.streak:
        bits.append(f"{abs(state.streak)}{'연승' if state.streak > 0 else '연패'}")
    if state.hp is not None:
        bits.append(f"체력 {state.hp}")
    return "  ".join(bits)


def missing_fields(state: GameState, fields: tuple[str, ...] = CORE_FIELDS) -> list[str]:
    """값이 없는(=인식하지 못한) 핵심 필드의 한국어 이름."""
    return [FIELD_LABELS.get(f, f) for f in fields if getattr(state, f, None) is None]


def low_confidence_fields(state: GameState, threshold: float) -> list[str]:
    """값은 있으나 신뢰도가 낮아 advisor가 쓰지 않는 필드."""
    out = []
    for f in state.confidence:
        if getattr(state, f, None) is not None and state.confidence_of(f) < threshold:
            out.append(f"{FIELD_LABELS.get(f, f)} {state.confidence_of(f):.2f}")
    return sorted(out)


def recognition_warnings(state: GameState, threshold: float) -> list[str]:
    """오버레이 상태줄·콘솔에 띄울 인식 경고.

    보드를 읽었지만 챔피언 이름만 모르는 상태(`UnitsKnowledge.SEEN`)는 **실패가 아니다.**
    보드/벤치 신뢰도 0.00을 "낮은 신뢰도"로 늘어놓는 대신 무엇을 알고 무엇을 모르는지 한 줄로 적는다.
    """
    warns: list[str] = []
    if state.screen_mode == ScreenMode.UNKNOWN:
        warns.append("화면 판별 실패")
    miss = missing_fields(state)
    if miss:
        warns.append("미인식: " + ", ".join(miss))
    low = low_confidence_fields(state, threshold)
    if units_knowledge(state, threshold) is UnitsKnowledge.SEEN:
        seen = len(state.board or []) + len(state.bench or [])
        low = [x for x in low if not x.startswith(("보드 ", "벤치 "))]
        warns.append(f"보드 {seen}기·장착 아이템 인식 · 챔피언 이름 미상(구매 추적 대기)")
    if low:
        warns.append("낮은 신뢰도: " + ", ".join(low))
    return warns


def _unit_label(u: UnitOnBoard, nb: NameBook, threshold: float) -> str:
    name = "이름 미상" if u.id == UNKNOWN_UNIT_ID else nb.name(u.id)
    if u.id != UNKNOWN_UNIT_ID and u.confidence < threshold:
        name += "(?)"
    elif u.id != UNKNOWN_UNIT_ID and u.confidence < CONFIRMED_NAME_THRESHOLD:
        name += " (추정)"   # 추천에는 이름 미상으로 쓴다(21 §15)
    if u.star is None and u.id != UNKNOWN_UNIT_ID:
        name += " ★?"   # 성급 미상 — ★1로 보이지 않게(QA 36 W2). 이름 미상 칸은 이미 모른다고 쓰므로 붙이지 않는다
    elif u.star is not None and u.star > 1:
        name += f" {u.star}성"
    return name


def units_lines(state: GameState, nb: NameBook, threshold: float = 0.6) -> list[str]:
    """보드·벤치 유닛 목록(이름·자리). 보드는 (줄,칸) — 줄 0 = 내 쪽 맨 앞, 벤치는 왼쪽부터 1~9.
    이름을 모르면 "이름 미상", 신뢰도가 임계값 미만이면 "(?)", 추정 이름(< `CONFIRMED_NAME_THRESHOLD`)이면 "(추정)", 보드에 있는 것만 알고 칸을 모르면 "자리 미상"."""
    out: list[str] = []
    if state.board is not None:
        bits = []
        for u in state.board:
            where = f"({u.hex[0]},{u.hex[1]})" if u.hex is not None else "(자리 미상)"
            bits.append(f"{_unit_label(u, nb, threshold)} {where}")
        out.append(f"보드 {len(state.board)}기: " + (" · ".join(bits) or "없음"))
    if state.bench is not None:
        bits = [f"{(u.bench_slot + 1) if u.bench_slot is not None else '?'} {_unit_label(u, nb, threshold)}"
                for u in state.bench]
        out.append(f"벤치 {len(state.bench)}기: " + (" · ".join(bits) or "없음"))
    return out


def confidence_line(state: GameState) -> str:
    items = [f"{FIELD_LABELS.get(f, f)} {state.confidence_of(f):.2f}"
             for f in state.confidence if getattr(state, f, None) is not None]
    return ", ".join(sorted(items)) or "(없음)"


# ---------------------------------------------------------------------------
# 추천 요약
# ---------------------------------------------------------------------------


def jev_label(rec: Recommendation | None) -> str:
    """Jev 사용 여부 표시. 캐러셀 통계 경로는 '실패'가 아니라 '캐러셀(통계)'로 보인다."""
    if rec is None:
        return "추천 없음"
    if rec.jev_used:
        return "Jev 사용"
    if rec.debug.get("mode") == ScreenMode.CAROUSEL.value or rec.debug.get("carousel_reuse"):
        return "캐러셀(통계)"
    reason = rec.fallback_reason
    return FALLBACK_LABELS.get(reason, str(reason)) if reason else "통계 전용"


def item_readiness_text(ready: list[ItemReadiness], names: NameBook) -> str:
    return " / ".join(f"{names.name(r.item_id)}({ITEM_STATUS_LABELS.get(r.status, r.status)})" for r in ready)


def final_units_text(comp: TargetComp, names: NameBook) -> str:
    """목표 덱 최종 유닛(통계 순서) → "자야★3(캐리)✓ · 라칸✓ · 요릭". ✓ = 보유(advisor `owned_units`), 보유를 모르면 ✓ 없음.
    오버레이는 같은 정보를 아이콘 줄로 그린다(`hud_view`), 콘솔·`--screenshot`은 이 글자."""
    owned = set(comp.owned_units)
    bits = []
    for u in getattr(comp, "final_board", None) or []:
        star = f"★{u.star}" if u.star and u.star >= 3 else ""   # ★3 목표(리롤)만 — ★2는 거의 전부라 표시하지 않는다
        carry = "(캐리)" if u.id == comp.carry else ""
        bits.append(f"{names.name(u.id)}{star}{carry}{'✓' if u.id in owned else ''}")
    return " · ".join(bits) or "-"


def comp_lines(comp: TargetComp, rank: int, names: NameBook, *, compact: bool = False,
               units_note: str | None = None) -> list[str]:
    """목표 덱 1개 → 표시 줄들. rank는 advisor 순서(1부터).

    `units_note`: 보유 유닛을 어디까지 아는지(`tft_advisor.unit_status.units_note`). 보유/부족 줄 뒤에 붙는다.
    보유 유닛을 하나도 모르면 이 문구만 보여 준다(기본값: "보드 미인식").
    """
    head = f"{rank}. {comp.name}  적합도 {comp.score:.2f}"
    if comp.carry:
        head += f"  캐리 {names.name(comp.carry)}"
    if comp.levelling:
        head += f"  운영 {comp.levelling}"
    out = [head]
    tail = f" ({units_note})" if units_note else ""
    if comp.owned_units or comp.missing_units:
        out.append(f"   보유 {len(comp.owned_units)}: {names.joined(comp.owned_units, limit=6) or '-'}{tail}")
        out.append(f"   부족 {len(comp.missing_units)}: {names.joined(comp.missing_units, limit=6) or '-'}")
    else:
        out.append(f"   보유/부족: {units_note or UNKNOWN_UNITS_TEXT}")
    if comp.items_ready:
        out.append(f"   아이템: {item_readiness_text(comp.items_ready, names)}")
    if not compact and getattr(comp, "final_board", None):
        out.append(f"   최종 덱: {final_units_text(comp, names)}")
    if not compact:
        if comp.next_buildup_board:
            b = comp.next_buildup_board
            out.append(f"   다음 빌드업(레벨 {b.level}): {names.joined(b.units, limit=9)}")
        if comp.reasons:
            out.append(f"   근거: {' · '.join(comp.reasons[:3])}")
    return out


def shop_lines(rec: Recommendation, names: NameBook) -> list[str]:
    out = []
    for advice in rec.shop:
        if advice.kind == ShopSlotKind.EMPTY:
            out.append(f"{advice.slot + 1}. (빈 칸)")
            continue
        mark = "구매" if advice.buy else "보류"
        name = names.name(advice.offer_id) if advice.offer_id else "인식 실패"
        tag = f" · {advice.reason_tag.label}" if advice.reason_tag else ""
        reason = f" — {advice.reason}" if advice.reason else ""
        out.append(f"{advice.slot + 1}. [{mark}] {name} {advice.score:.2f}{tag}{reason}")
    return out


def board_plan_lines(plan: BoardPlan, names: NameBook, *, comp_name: str | None = None) -> list[str]:
    """보드 배치 추천(`Recommendation.board_plan`) → 표시 줄들. 오버레이도 이 줄을 쓸 수 있다(21 §6.3).

    목표 덱 빌드업 기준 보드가 있으면(21 §17) 기준 줄 · 교체(↑ 벤치에서 올리기) · 상점에서 구할 유닛 · 다음 레벨을
    먼저 보여 준다(고정 크기 HUD에서 잘리지 않게 중요한 줄부터)."""

    def label(uid: str, star: int | None) -> str:   # 성급 미상은 ★?(★1로 가정하지 않는다)
        return names.name(uid) + ("★?" if star is None else f"★{star}" if star >= 2 else "")

    def joined(ids) -> str:
        return " · ".join(names.name(u) for u in ids)

    head = []
    if comp_name:
        head.append(f"기준 {comp_name}")
    if plan.slots is not None:
        head.append(f"칸 {plan.slots}")
    if plan.free_slots:
        head.append(f"빈 칸 {plan.free_slots}")
    if plan.stale:
        head.insert(0, "(직전)")
    out = [" · ".join(head)] if head else []
    ref = bool(getattr(plan, "reference_units", None))
    if ref:
        who = f"({comp_name})" if comp_name else ""
        line = (f"레벨 {plan.reference_level} 빌드업{who}: {joined(plan.reference_units)}"
                f" — 보유 {len(plan.owned_in_reference)}/{len(plan.reference_units)}")
        if plan.level is not None and plan.reference_level is not None and plan.level != plan.reference_level:
            line += f" (레벨 {plan.level} 빌드업 통계가 없어 레벨 {plan.reference_level} 기준입니다)"
        out.append(line)
    lineup = [f"{label(e.unit_id, e.star)}" + (f"({e.reason})" if e.reason else "") for e in plan.lineup]
    if plan.unknown_on_board:
        lineup.append(f"미확인 {plan.unknown_on_board}기(그대로)")
    out.append("보드: " + (" · ".join(lineup) or "-"))
    if plan.swaps and ref:
        moves = [f"{names.name(sw.field_unit_id)}(↔ 보드 {names.name(sw.bench_unit_id)} 내리기)" if sw.bench_unit_id
                 else f"{names.name(sw.field_unit_id)}(빈 칸)" for sw in plan.swaps]
        out.append("교체: ↑ 벤치에서 올리기 " + " · ".join(moves))
    elif plan.swaps:
        moves = [f"벤치 {names.name(sw.field_unit_id)} ↔ 보드 {names.name(sw.bench_unit_id)}" if sw.bench_unit_id
                 else f"빈 칸에 {names.name(sw.field_unit_id)} 올리기" for sw in plan.swaps]
        out.append("교체: " + " / ".join(moves))
    else:
        out.append("교체: 없음(지금 배치를 유지하세요)")
    if ref and plan.missing:
        line = f"상점에서 구하세요: {joined(plan.missing)}"
        if plan.unknown_on_bench or plan.unknown_on_board:
            line += " (미확인 유닛 중에 있을 수 있습니다)"
        out.append(line)
    elif plan.free_slots:
        out.append(f"빈 칸 {plan.free_slots}개: 상점에서 유닛을 사서 채우세요")
    if ref and plan.next_level is not None and plan.next_level_units:
        new = [u for u in plan.next_level_units if u not in plan.reference_units]
        out.append(f"레벨 {plan.next_level}: " + (f"+{joined(new)}" if new else "같은 유닛 유지")
                   + f" (빌드업 {len(plan.next_level_units)}기)")
    if ref:   # 판매는 할 일이라 벤치 목록보다 먼저
        out += sell_lines(plan, names)
    if plan.bench:
        out.append("벤치: " + " · ".join(f"{label(e.unit_id, e.star)}" for e in plan.bench[:6])
                   + (f" 외 {len(plan.bench) - 6}" if len(plan.bench) > 6 else ""))
    if not ref:
        out += sell_lines(plan, names)
    out += [f"참고: {n}" for n in plan.notes]
    return out


def sell_lines(plan: BoardPlan, names: NameBook, *, max_units: int = 5) -> list[str]:
    """판매 추천(21 §14) → 줄들. 예 "판매: 쉔 · 라칸 (+4골드 · 이자 구간 30골드까지 1 남음)".
    판매할 유닛이 없고 안내(벤치 가득 등)도 없으면 빈 목록."""
    if not plan.sell and not plan.sell_notes:
        return []

    def label(uid: str, star: int | None) -> str:   # 성급 미상은 ★?(★1로 가정하지 않는다)
        return names.name(uid) + ("★?" if star is None else f"★{star}" if star >= 2 else "")

    out = []
    if plan.sell:
        units = " · ".join(label(s.unit_id, s.star) for s in plan.sell[:max_units])
        if len(plan.sell) > max_units:
            units += f" 외 {len(plan.sell) - max_units}"
        at_least = " 이상" if any(s.star is None for s in plan.sell) else ""   # 성급 미상 = 최소 판매가
        tail = [f"+{plan.sell_gold_total}골드{at_least}"] if plan.sell_gold_total else []
        if plan.interest_note:
            tail.append(plan.interest_note)
        out.append(f"판매: {units}" + (f" ({' · '.join(tail)})" if tail else ""))
        why = [f"{label(s.unit_id, s.star)} — {s.reason}" for s in plan.sell[:max_units] if s.reason]
        if why:
            out.append("판매 이유: " + " / ".join(why))
    if plan.sell_notes:
        out.append("판매 참고: " + " · ".join(plan.sell_notes))
    return out


def augment_lines(rec: Recommendation, names: NameBook) -> list[str]:
    if rec.augment is None:
        return []
    out = []
    for choice in rec.augment.choices:
        mark = "★" if choice.augment_id == rec.augment.pick else "  "
        tier = f" [{choice.editorial_tier}]" if choice.editorial_tier else ""
        why = f" — {choice.reasons[0]}" if choice.reasons else ""
        out.append(f"{mark} {names.name(choice.augment_id)} {choice.score:.2f}{tier}{why}")
    return out


def item_lines(rec: Recommendation, names: NameBook) -> list[str]:
    if rec.item is None:
        return []
    out = []
    for s in rec.item.suggestions:
        comps = " + ".join(names.names(s.components))
        holder = f" → {names.name(s.holder_unit_id)}" if s.holder_unit_id else ""
        why = f" — {s.reason}" if s.reason else ""
        out.append(f"· {names.name(s.item_id)}{f' ({comps})' if comps else ''}{holder} {s.score:.2f}{why}")
    note = getattr(rec.item, "note", None)
    if note:
        out.append(f"· {note}")
    elif rec.item.hold:
        out.append("· 재료 보관 권장(지금 조합하지 않음)")
    return out


# ---------------------------------------------------------------------------
# 직전 추천 유지(전투·아이템 선택·알 수 없는 화면)
# ---------------------------------------------------------------------------

KEPT_LABELS: dict[ScreenMode, str] = {
    ScreenMode.COMBAT: "전투 중",
    ScreenMode.ITEM_SELECT: "아이템 선택 중",
    ScreenMode.UNKNOWN: "화면 판별 실패",
}


@dataclass(frozen=True)
class KeptInfo:
    """새 추천을 만들지 않는 화면에서 직전 추천을 보여 줄 때의 표시 정보."""

    mode: ScreenMode
    bought: int = 0      # 직전 추천 칸이 지금 빈 칸 → 산 것으로 보고 뺐다
    changed: int = 0     # 직전 추천 칸에 지금 다른 유닛 → 낡은 추천이라 뺐다(새로고침 등)
    shop_fresh: bool = False
    """상점 칸 추천은 **지금 상점**으로 다시 계산한 것이다(전투 중 새로고침·라운드 시작, `LiveLoop` 상점 재평가).
    목표 덱은 여전히 직전 추천 그대로다."""

    @property
    def label(self) -> str:
        return f"직전 추천({KEPT_LABELS.get(self.mode, screen_label(self.mode))})"

    @property
    def shop_label(self) -> str:
        """[상점] 머리의 꼬리표. 다시 계산한 상점이면 "새 상점 기준", 아니면 직전 추천."""
        if self.shop_fresh:
            return f"새 상점 기준({KEPT_LABELS.get(self.mode, screen_label(self.mode))})"
        return self.label

    def note(self) -> str:
        bits = [f"{self.label}: 준비 단계 추천을 유지합니다(목표 덱 고정)"]
        if self.shop_fresh:
            bits.append("상점은 지금 상점으로 다시 계산했습니다")
        if self.bought:
            bits.append(f"산 칸 {self.bought}개 제외")
        if self.changed:
            bits.append(f"바뀐 칸 {self.changed}개는 다시 계산 중")
        return " · ".join(bits)


def shop_ids(state: GameState | None) -> tuple[str | None, ...] | None:
    """상점 칸별 상품 ID(빈 칸·못 읽은 칸 = None). 상점을 모르면 None."""
    if state is None or state.shop is None:
        return None
    return tuple(s.id if s.kind in (ShopSlotKind.CHAMPION, ShopSlotKind.SPECIAL) else None for s in state.shop)


def shop_needs_rescore(rec: Recommendation | None, state: GameState) -> bool:
    """지금 상점에 추천이 모르는 **새 상품**이 있는가(새로고침·라운드 시작).

    추천했던 칸이 빈 칸이 된 것(= 산 것)이나 못 읽은 칸은 새 상품이 아니다 → 다시 계산하지 않는다.
    """
    cur = shop_ids(state)
    if rec is None or cur is None:
        return False
    known = {a.slot: (a.offer_id or None) for a in rec.shop}
    return any(cid is not None and known.get(i) != cid for i, cid in enumerate(cur))


def kept_view(rec: Recommendation, state: GameState) -> tuple[Recommendation, KeptInfo]:
    """직전 추천 → 지금 화면에 맞춘 표시용 사본 + 표시 정보. 목표 덱은 그대로 두고 상점 칸만 거른다.

    직전 추천의 상점 칸이 지금 상점(병합 상태)과 다르면 뺀다: 빈 칸이 됐으면 산 것, 다른 유닛이면 새로고침된 것이다.
    지금 상점을 모르면(None) 거르지 않는다. 원본 `rec`는 바꾸지 않는다(advisor 세션의 직전 추천이다).
    """
    cur = state.shop
    if cur is None or not rec.shop:
        return rec, KeptInfo(state.screen_mode)
    keep: list = []
    bought = changed = 0
    for advice in rec.shop:
        slot = cur[advice.slot] if advice.slot < len(cur) else None
        cur_id = slot.id if slot is not None and slot.kind in (ShopSlotKind.CHAMPION, ShopSlotKind.SPECIAL) else None
        if (advice.offer_id or None) == cur_id:
            keep.append(advice)
        elif cur_id is None:
            bought += 1
        else:
            changed += 1
    return rec.model_copy(update={"shop": keep}), KeptInfo(state.screen_mode, bought, changed)


# ---------------------------------------------------------------------------
# 상태줄
# ---------------------------------------------------------------------------


@dataclass
class StatusInfo:
    """오버레이·콘솔 상태줄 재료."""

    patch: str | None = None
    backend: str = "mock"
    rec: Recommendation | None = None
    updated_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)
    extra: str | None = None


def age_text(updated_at: datetime | None, now: datetime | None = None) -> str:
    if updated_at is None:
        return "갱신 없음"
    now = now or datetime.now(UTC)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    seconds = max(0.0, (now - updated_at).total_seconds())
    if seconds < 60:
        return f"{seconds:.0f}초 전"
    return f"{seconds / 60:.0f}분 전"


def status_line(info: StatusInfo, now: datetime | None = None) -> str:
    bits = [f"패치 {info.patch or '?'}", f"{info.backend} · {jev_label(info.rec)}", age_text(info.updated_at, now)]
    if info.rec is not None and info.rec.latency_ms is not None:
        bits.append(f"{info.rec.latency_ms:.0f}ms")
    if info.extra:
        bits.append(info.extra)
    if info.warnings:
        bits.append("⚠ " + " / ".join(info.warnings))
    return " · ".join(bits)


# ---------------------------------------------------------------------------
# 전체 리포트(콘솔)
# ---------------------------------------------------------------------------


def format_report(state: GameState, rec: Recommendation | None, *, names: NameBook | None = None,
                  status: StatusInfo | None = None, threshold: float = 0.6,
                  title: str | None = None, max_comps: int = 3, kept: KeptInfo | None = None) -> str:
    """스크린샷·콘솔 모드의 사람이 읽는 요약(한국어). `kept`: 직전 추천을 보여 주는 화면이면 그 표시 정보(`kept_view`)."""
    nb = names or NameBook()
    lines: list[str] = []
    if title:
        lines += [f"=== {title} ===" ]
    lines.append(state_line(state))
    if state.shop_odds:
        lines.append("상점 확률: " + "/".join(str(p) for p in state.shop_odds))
    lines += units_lines(state, nb, threshold)
    lines.append("")

    if rec is None:
        if state.screen_mode in (ScreenMode.COMBAT, ScreenMode.ITEM_SELECT, ScreenMode.UNKNOWN):
            lines.append(f"[추천] {screen_label(state.screen_mode)} 화면 — 직전 추천을 유지합니다(이 화면 단독으로는 추천 없음).")
        elif state.screen_mode in (ScreenMode.LOADING, ScreenMode.GAME_OVER):
            lines.append(f"[추천] {screen_label(state.screen_mode)} 화면 — 세션을 초기화합니다.")
        else:
            lines.append("[추천] 없음")
    else:
        if kept is not None:
            lines.append(f"[{kept.note()}]")
        lines.append(f"[목표 덱] advisor 순서 (점수로 재정렬하지 않음) — {jev_label(rec)}")
        if not rec.target_comps:
            lines.append("  (후보 없음 — 인식 정보 부족)")
        note = units_note(state, threshold)
        for i, comp in enumerate(rec.target_comps[:max_comps], start=1):
            lines += ["  " + ln for ln in comp_lines(comp, i, nb, units_note=note)]
        if rec.board_plan is not None:
            basis = next((c.name for c in rec.target_comps if c.comp_id == rec.board_plan.comp_id), None)
            lines += ["", "[보드 배치]"] + ["  " + ln for ln in board_plan_lines(rec.board_plan, nb, comp_name=basis)]
        if rec.shop:
            head = f"[상점 — {kept.shop_label}]" if kept is not None else "[상점]"
            lines += ["", head] + ["  " + ln for ln in shop_lines(rec, nb)]
        elif kept is not None and (kept.bought or kept.changed):
            lines += ["", f"[상점 — {kept.label}] 남은 추천 칸 없음"]
        if rec.augment is not None:
            lines += ["", "[증강 선택]"] + ["  " + ln for ln in augment_lines(rec, nb)]
        if rec.item is not None and (rec.item.suggestions or rec.item.hold):
            lines += ["", "[아이템]"] + ["  " + ln for ln in item_lines(rec, nb)]
        if rec.component_priority:
            lines += ["", "[재료 우선순위] " + nb.joined(rec.component_priority, limit=6)]

    lines += ["", "--- 인식 품질 ---", "신뢰도: " + confidence_line(state)]
    warns = recognition_warnings(state, threshold)
    lines += ["경고: " + (" / ".join(warns) if warns else "없음")]
    if status is not None:
        lines.append("상태: " + status_line(status))
    return "\n".join(lines)


def ensure_utf8_stdio() -> None:
    """콘솔 출력을 UTF-8로 맞춘다. Windows에서 출력을 파이프로 넘기면 cp1252가 되어 한국어에서
    `UnicodeEncodeError`로 죽었다(`--screenshot … | more`). 바꿀 수 없는 스트림(테스트 캡처 등)은 그대로 둔다."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        reconfigure = getattr(stream, "reconfigure", None)
        if enc == "utf8" or reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass
