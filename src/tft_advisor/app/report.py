"""추천 결과를 한국어 텍스트로 옮긴다 — 콘솔(`--screenshot`, `--no-overlay`)과 오버레이가 같은 문구를 쓴다.

여기 있는 함수는 순수하다(입출력·Qt 없음). 오버레이는 이 줄들을 HTML로 감싸 그리고, 콘솔은 그대로 출력한다.
문구를 한 곳에 모아 둔 이유: 게임 클라이언트가 한국어라 사용자가 보는 이름·태그가 두 경로에서 달라지면 안 된다.

**표시 순서 계약**: `Recommendation.target_comps`는 advisor가 준 순서 그대로 보여 준다. 타이브레이커·히스테리시스
보호 때문에 score가 단조가 아닐 수 있다. **점수로 다시 정렬하지 않는다**(02_app-integrator_report.md C3.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..contracts import (
    FallbackReason, GameState, ItemReadiness, Recommendation, ScreenMode, ShopSlotKind, TargetComp,
)
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
    """오버레이 상태줄·콘솔에 띄울 인식 경고."""
    warns: list[str] = []
    if state.screen_mode == ScreenMode.UNKNOWN:
        warns.append("화면 판별 실패")
    miss = missing_fields(state)
    if miss:
        warns.append("미인식: " + ", ".join(miss))
    low = low_confidence_fields(state, threshold)
    if low:
        warns.append("낮은 신뢰도: " + ", ".join(low))
    return warns


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


def comp_lines(comp: TargetComp, rank: int, names: NameBook, *, compact: bool = False) -> list[str]:
    """목표 덱 1개 → 표시 줄들. rank는 advisor 순서(1부터)."""
    head = f"{rank}. {comp.name}  적합도 {comp.score:.2f}"
    if comp.carry:
        head += f"  캐리 {names.name(comp.carry)}"
    if comp.levelling:
        head += f"  운영 {comp.levelling}"
    out = [head]
    if comp.owned_units or comp.missing_units:
        out.append(f"   보유 {len(comp.owned_units)}: {names.joined(comp.owned_units, limit=6) or '-'}")
        out.append(f"   부족 {len(comp.missing_units)}: {names.joined(comp.missing_units, limit=6) or '-'}")
    else:
        out.append("   보유/부족: 보드 미인식")
    if comp.items_ready:
        out.append(f"   아이템: {item_readiness_text(comp.items_ready, names)}")
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
    if rec.item.hold:
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

    @property
    def label(self) -> str:
        return f"직전 추천({KEPT_LABELS.get(self.mode, screen_label(self.mode))})"

    def note(self) -> str:
        bits = [f"{self.label}: 준비 단계 추천을 유지한다(목표 덱 고정)"]
        if self.bought:
            bits.append(f"산 칸 {self.bought}개 제외")
        if self.changed:
            bits.append(f"바뀐 칸 {self.changed}개는 준비 단계에서 다시 계산")
        return " · ".join(bits)


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
    lines.append("")

    if rec is None:
        if state.screen_mode in (ScreenMode.COMBAT, ScreenMode.ITEM_SELECT, ScreenMode.UNKNOWN):
            lines.append(f"[추천] {screen_label(state.screen_mode)} 화면 — 직전 추천을 유지한다(이 화면 단독으로는 추천 없음).")
        elif state.screen_mode in (ScreenMode.LOADING, ScreenMode.GAME_OVER):
            lines.append(f"[추천] {screen_label(state.screen_mode)} 화면 — 세션을 초기화한다.")
        else:
            lines.append("[추천] 없음")
    else:
        if kept is not None:
            lines.append(f"[{kept.note()}]")
        lines.append(f"[목표 덱] advisor 순서 (점수로 재정렬하지 않음) — {jev_label(rec)}")
        if not rec.target_comps:
            lines.append("  (후보 없음 — 인식 정보 부족)")
        for i, comp in enumerate(rec.target_comps[:max_comps], start=1):
            lines += ["  " + ln for ln in comp_lines(comp, i, nb)]
        if rec.shop:
            head = f"[상점 — {kept.label}]" if kept is not None else "[상점]"
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
