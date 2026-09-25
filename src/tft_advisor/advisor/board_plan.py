"""보드 배치 추천: 지금 보유한 유닛 중 무엇을 보드에 둘지(코드 전용, Jev 호출 없음).

설계: `_workspace/21_board_trust.md` §6·§10·§11. 결정적이고 틀릴 수 없는 계산(특성 인원·구간, 성급, 아이템 수, 목표 덱 소속)이라
Jev에 묻지 않는다. 통계 신호는 두 가지다.
- `Scorer.s_now_table`: 덱별 레벨 빌드업 보드(MetaTFT comp_details)에 그 유닛이 나오는 비중.
- 스테이지 보드 통계(`stage_boards.StageBoardSource`, MetaTFT Early Comps: 스테이지 2~5 실제 보드): 유닛(성급별) 스테이지
  성적 신호, "지금 이 스테이지 추천 보드"(보유 유닛으로 만들 수 있는 실제 보드) 소속 가산, 다음 스테이지 경로 안내.
상수는 `config/weights.toml [board_plan]`(config.BoardPlanWeights).

선정(탐욕): 칸 수(레벨) − 이름 미상 보드 유닛 수만큼, 매 단계 한계 점수가 가장 큰 유닛을 고른다.
한계 점수 = 기본 점수(목표 덱 소속 · 성급 · 아이템 · 코스트 · 통계) + 특성 이득(지금 라인업 기준 구간 도달/진행)
          + 보드 유지 가산(널뛰기 방지) − 같은 챔피언 중복 감점.
이름 미상 유닛은 어떤 챔피언으로도 세지 않는다: 보드 쪽은 자리만 차지한 채 그대로 두고, 벤치 쪽은 판단하지 않는다.
앞/뒤 라인 균형은 정적 데이터에 역할(role)이 없어(전부 None) 쓰지 않는다.

스테이지(21 §10, `weights.toml [unit_stage]`): 보드 배치는 지금 라운드 문제라 보유 유닛을 그대로 쓴다. 다만 초반(2~3)에는
목표 덱 소속 가산을 `plan_comp_scale`배로 줄이고 '지금 강함'(s_now·스테이지 보드 통계)을 `plan_now_scale`배로 키운다 —
최종 덱에 없는 유닛이라도 지금 강하면 올린다. 대신 `BoardTransition`(유지/다리/교체 예정/다음 목표)으로 전환 경로를 보여 준다.

스테이지 보드 통계가 없으면(빈 스냅샷, mini 통계) 그 항들은 0이고 예전과 같이 동작한다.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import BoardPlanWeights, UnitStageWeights
from ..contracts import (
    BoardPlan,
    BoardPlanEntry,
    BoardSwap,
    BoardTransition,
    CompStats,
    StageBoardHint,
    UnitOnBoard,
)
from .candidates import stage_round
from .features import View, board_at
from .stage_boards import BoardPick, NextHint, StageBoardSource
from .stats_source import AdvisorStats

DEFAULT_WEIGHTS = BoardPlanWeights()
"""weights.toml 없이 부를 때(테스트)의 기본값. 엔진은 `Weights.board_plan`을 넘긴다."""


@dataclass
class _Cand:
    unit: UnitOnBoard
    on_board: bool
    order: int
    traits: list[str]
    base: float = 0.0
    comp_part: float = 0.0          # 목표 덱 소속 점수(중복이면 빼고 센다 — 역할은 이미 채워졌다)
    reasons: list[tuple[float, str]] = field(default_factory=list)
    score: float = 0.0
    pick_reasons: list[tuple[float, str]] = field(default_factory=list)


def _unit_traits(u: UnitOnBoard, stats: AdvisorStats) -> list[str]:
    traits = list(stats.champion_traits(u.id))
    for item in u.items:           # 장착 상징은 특성을 더한다
        t = stats.emblem_trait(item)
        if t and t not in traits:
            traits.append(t)
    return traits


@dataclass(frozen=True)
class _Stage:
    comp: float = 1.0      # 목표 덱 소속 가산 배수(plan_comp_scale)
    now: float = 1.0       # 지금 강함(s_now·스테이지 보드 통계) 배수(plan_now_scale)


def _base(c: _Cand, comp: CompStats | None, buildup: set[str], snow: dict[str, float], stats: AdvisorStats,
          w: BoardPlanWeights, sf: _Stage = _Stage(), sig=None, in_pick: bool = False) -> None:
    """sig: `stage_boards.UnitSignal | None`(유닛 스테이지 성적), in_pick: 추천 스테이지 보드에 든 유닛."""
    uid, star = c.unit.id, c.unit.star or 1
    fb = {u.id: u for u in comp.final_board} if comp is not None else {}
    if comp is not None and uid == comp.carry:
        c.reasons.append((w.carry * sf.comp, "목표 덱 캐리"))
    elif uid in fb and fb[uid].is_core:
        c.reasons.append((w.core * sf.comp, "목표 덱 핵심"))
    elif uid in fb:
        c.reasons.append((w.final * sf.comp, "목표 덱 유닛"))
    elif uid in buildup:
        c.reasons.append((w.buildup * sf.comp, "빌드업 유닛"))
    c.comp_part = c.reasons[0][0] if c.reasons else 0.0
    if star >= 3:
        c.reasons.append((w.star3, f"{star}성"))
    elif star == 2:
        c.reasons.append((w.star2, "2성"))
    n_items = len(c.unit.items)
    if n_items:
        c.reasons.append((w.per_item * n_items, "아이템 보유자" if n_items == 1 else f"아이템 {n_items}개"))
    s = snow.get(uid, 0.0)
    hidden = 0.0                    # 근거 문구에는 안 나오는 점수
    if s >= w.stat_reason_min:
        c.reasons.append((w.stat * s * sf.now, "현 레벨 통계 상위"))
    else:
        hidden += w.stat * s * sf.now
    if sig is not None:             # 스테이지 유닛 성적(수축 delta → −1~1). 나쁘면 감점, 근거에는 좋을 때만 나온다
        v = w.stage_unit * sig.value * sf.now
        if v > 0:
            c.reasons.append((v, sig.reason()))
        else:
            hidden += v
    if in_pick:
        c.reasons.append((w.board_member * sf.now, "추천 스테이지 보드"))
    c.base = sum(v for v, _ in c.reasons) + w.per_cost * (stats.champion_cost(uid) or 0) + hidden


def _marginal(c: _Cand, counts: Counter[str], chosen: set[str], key_traits: set[str], stats: AdvisorStats,
              ko: Callable[[str], str], w: BoardPlanWeights,
              sf: _Stage = _Stage()) -> tuple[float, list[tuple[float, str]]]:
    extra: list[tuple[float, str]] = []
    gain = 0.0
    if c.unit.id in chosen:
        extra.append((w.duplicate, "중복"))
        gain += w.duplicate - c.comp_part
    else:
        for t in c.traits:
            bps = stats.trait_breakpoints(t)
            if not bps:
                continue
            if bps == [1]:
                gain += w.trait_unique
                continue
            n = counts[t]
            mult = 1.0 + (w.trait_key_mult - 1.0) * sf.comp if t in key_traits else 1.0
            if (n + 1) in bps:
                g = w.trait_activate * mult
                gain += g
                extra.append((g, f"{ko(t)} {n}→{n + 1} 활성"))
            elif n + 1 < max(bps):
                gain += w.trait_progress * mult
    if c.on_board:
        gain += w.keep
    return c.base + gain, extra


def _reason_text(parts: list[tuple[float, str]], limit: int = 2) -> str | None:
    top = [txt for v, txt in sorted(parts, key=lambda x: -x[0]) if v > 0][:limit]
    return " · ".join(top) or None


def _stage_factors(view: View, stage: UnitStageWeights | None) -> _Stage:
    if stage is None:
        return _Stage()
    s, r = stage_round(view)
    return _Stage(stage.at(stage.plan_comp_scale, s, r), stage.at(stage.plan_now_scale, s, r))


def _transition(comp: CompStats, lineup: list[_Cand], owned_ids: set[str], level: int | None) -> BoardTransition:
    """지금 라인업 → 목표 덱 전환 경로(코드 분류). 다리 = 현재 레벨 이상 빌드업 보드에 나오는 비최종 유닛."""
    final = {u.id for u in comp.final_board}
    buildup: set[str] = set()
    for lv, boards in comp.buildup.items():
        if level is None or lv >= level:
            for b in boards:
                buildup.update(b.units)
    keep: list[str] = []
    bridge: list[str] = []
    placeholder: list[str] = []
    for c in lineup:
        uid = c.unit.id
        bucket = keep if uid in final else bridge if uid in buildup else placeholder
        if uid not in bucket:
            bucket.append(uid)
    nxt_lv = None
    targets: list[str] = []
    if level is not None:
        for lv in sorted(k for k in comp.buildup if k > level):   # 실제로 보드가 있는 다음 레벨(4 미만이면 4)
            b = board_at(comp, lv)
            if b is not None:
                nxt_lv = lv
                targets = [u for u in b.units if u not in owned_ids]
                break
    if nxt_lv is None:
        targets = [u.id for u in comp.final_board if u.id not in owned_ids]
    return BoardTransition(keep=keep, bridge=bridge, placeholder=placeholder, next_targets=targets[:5],
                           next_level=nxt_lv)


def transition_note(tr: BoardTransition, ko: Callable[[str], str]) -> str | None:
    """한 줄. 예 "전환: 최종 덱 유지 아칼리 / 다리 코그모 / 지금 전력용(교체 예정) 엘리스 / 레벨 6 목표 마스터 이·렝가"."""
    def names(ids: list[str]) -> str:
        return "·".join(ko(u) for u in ids)
    parts = []
    if tr.keep:
        parts.append(f"최종 덱 유지 {names(tr.keep)}")
    if tr.bridge:
        parts.append(f"다리 {names(tr.bridge)}")
    if tr.placeholder:
        parts.append(f"지금 전력용(교체 예정) {names(tr.placeholder)}")
    if tr.next_targets:
        parts.append((f"레벨 {tr.next_level} 목표 " if tr.next_level else "모을 유닛 ") + names(tr.next_targets))
    return ("전환: " + " / ".join(parts)) if parts else None


def _names(ids, ko: Callable[[str], str]) -> str:
    return "·".join(ko(u) for u in ids)


def _pick_note(pick: BoardPick, ko: Callable[[str], str]) -> str:
    missing = [u for u in pick.units if u not in pick.owned]
    stat = f"{pick.stage}스테이지 실제 보드 {pick.games:,}판"
    if pick.delta is not None:
        stat += f" · 평균 등수 {pick.delta:+.2f}".replace("-", "−")
    stat += f" · 보유 {len(pick.owned)}/{len(pick.units)}"
    text = f"지금 이 스테이지 추천 보드: {_names(pick.units, ko)} ({stat})"
    if missing:
        text += f" — {_names(missing, ko)}은(는) 상점에서 구하시면 됩니다"
    return text


def _next_note(nh: NextHint, lineup: list[str], ko: Callable[[str], str]) -> str:
    new = [u for u in nh.units if u not in lineup]
    where = f"+{_names(new, ko)}" if new else _names(nh.units, ko)
    tail = f"경로 {nh.share:.0%}"
    if nh.avg_place is not None:
        tail += f" · 평균 {nh.avg_place:.2f}등"
    return f"다음 스테이지: 이 보드는 보통 {where} 쪽으로 이어집니다({tail})"


def plan_board(view: View, stats: AdvisorStats, comp: CompStats | None, level: int | None,
               snow: dict[str, float], ko: Callable[[str], str],
               w: BoardPlanWeights = DEFAULT_WEIGHTS, *, stage: UnitStageWeights | None = None,
               stage_board: StageBoardSource | None = None) -> BoardPlan | None:
    """보드 배치 추천. 보드를 못 읽었거나 이름 아는 유닛이 하나도 없으면 None.

    stage: 초반 '지금 강함' 우선 스케줄(None이면 스테이지 무관 — 예전 동작).
    stage_board: 스테이지 보드 통계(None이면 그 항 0 — 예전 동작).
    """
    state = view.state
    if state.board is None or not view.units_known:
        return None
    o = view.owned
    cands = [_Cand(u, True, i, _unit_traits(u, stats)) for i, u in enumerate(view.board)]
    cands += [_Cand(u, False, len(cands) + i, _unit_traits(u, stats)) for i, u in enumerate(view.bench)]
    if not cands:
        return None

    notes: list[str] = []
    board_now = len(state.board)
    if view.level is not None:
        slots = max(view.level, board_now)
        if board_now > view.level:
            notes.append(f"보드 {board_now}기가 레벨 {view.level}보다 많아 현재 인원을 칸 수로 봤습니다")
    else:
        slots = board_now
        notes.append(f"레벨 미인식: 현재 보드 {board_now}칸 기준입니다")
    unknown_board, unknown_bench = o.board_hidden, o.bench_hidden
    open_slots = max(0, slots - unknown_board)
    if unknown_board:
        notes.append(f"보드 미확인 {unknown_board}기는 그대로 두었습니다(정체를 몰라 판단하지 않습니다)")
    if unknown_bench:
        notes.append(f"벤치 미확인 {unknown_bench}기는 판단하지 않았습니다 — 강한 유닛이면 직접 올려 주세요")
    if not o.bench_seen:
        notes.append("벤치 미인식: 보드 유닛만으로 판단했습니다")

    key_traits = {t.id for t in comp.key_traits} if comp is not None else set()
    buildup: set[str] = set()
    if comp is not None and level is not None:
        for lv in (level, level + 1):
            b = board_at(comp, lv)
            if b is not None:
                buildup.update(b.units)
    sf = _stage_factors(view, stage)
    comp_id = comp.comp_id if comp is not None else None
    pick: BoardPick | None = None
    if stage_board is not None:
        owned_stars: dict[str, int] = {}
        for c in cands:
            owned_stars[c.unit.id] = max(owned_stars.get(c.unit.id, 0), c.unit.star or 1)
        pick = stage_board.best_board(owned_stars, view.stage, slots, comp_id, sf.comp)
    in_pick = set(pick.owned) if pick is not None else set()
    for c in cands:
        sig = stage_board.unit_signal(c.unit.id, c.unit.star, view.stage, slots) if stage_board is not None else None
        _base(c, comp, buildup, snow, stats, w, sf, sig, c.unit.id in in_pick)

    # 탐욕 선정
    counts: Counter[str] = Counter()
    chosen_ids: set[str] = set()
    left = list(cands)
    lineup: list[_Cand] = []
    while left and len(lineup) < open_slots:
        best, best_key, best_extra = None, None, []
        for c in left:
            m, extra = _marginal(c, counts, chosen_ids, key_traits, stats, ko, w, sf)
            key = (-m, not c.on_board, -(c.unit.star or 1), c.unit.id, c.order)
            if best_key is None or key < best_key:
                best, best_key, best_extra = c, key, extra
        assert best is not None and best_key is not None
        best.score = -best_key[0]
        best.pick_reasons = best_extra
        lineup.append(best)
        left.remove(best)
        if best.unit.id not in chosen_ids:
            chosen_ids.add(best.unit.id)
            counts.update(best.traits)
    for c in left:
        m, extra = _marginal(c, counts, chosen_ids, key_traits, stats, ko, w, sf)
        c.score, c.pick_reasons = m, extra

    def entry(c: _Cand, fielded: bool) -> BoardPlanEntry:
        action = ("keep" if c.on_board else "field") if fielded else ("bench" if c.on_board else "stay")
        if fielded:
            reason = _reason_text(c.reasons + c.pick_reasons)
        elif c.unit.id in chosen_ids:
            reason = "같은 챔피언이 보드에 있습니다(합성 대기)"
        else:
            reason = "자리 부족 — 우선순위가 낮습니다"
        return BoardPlanEntry(unit_id=c.unit.id, star=c.unit.star, on_board=c.on_board, action=action,
                              score=round(c.score, 3), reason=reason)

    ups = sorted((c for c in lineup if not c.on_board), key=lambda c: (-c.score, c.order))
    downs = sorted((c for c in left if c.on_board), key=lambda c: (c.score, c.order))
    swaps = [BoardSwap(field_unit_id=u.unit.id, bench_unit_id=d.unit.id) for u, d in zip(ups, downs, strict=False)]
    swaps += [BoardSwap(field_unit_id=u.unit.id) for u in ups[len(downs):]]
    lineup_ids = list(dict.fromkeys(c.unit.id for c in lineup))
    hint = None
    if pick is not None:
        notes.append(_pick_note(pick, ko))
    nh = stage_board.next_hint(lineup_ids, view.stage, slots, comp_id) if stage_board is not None and lineup_ids else None
    if nh is not None:
        notes.append(_next_note(nh, lineup_ids, ko))
    if pick is not None or nh is not None:
        hint = StageBoardHint(
            stage=pick.stage if pick is not None else nh.stage - 1,   # type: ignore[union-attr]
            kind=pick.kind if pick is not None else "cluster",        # type: ignore[arg-type]
            units=list(pick.units) if pick else [], owned=list(pick.owned) if pick else [],
            games=pick.games if pick else 0, avg_place=pick.avg_place if pick else None,
            delta=pick.delta if pick else None, comp_link=(pick.link if pick and comp_id else None),
            next_stage=nh.stage if nh else None, next_units=list(nh.units) if nh else [],
            next_share=min(1.0, max(0.0, nh.share)) if nh else None, next_avg_place=nh.avg_place if nh else None)
    transition = None
    if comp is not None:
        transition = _transition(comp, lineup, {c.unit.id for c in cands}, level)
        note = transition_note(transition, ko)
        if note:
            notes.append(note)
    return BoardPlan(
        comp_id=comp.comp_id if comp is not None else None, slots=slots,
        lineup=[entry(c, True) for c in lineup],
        bench=[entry(c, False) for c in sorted(left, key=lambda c: (-c.score, c.order))],
        swaps=swaps, free_slots=max(0, open_slots - len(lineup)),
        unknown_on_board=unknown_board, unknown_on_bench=unknown_bench, notes=notes, transition=transition,
        stage_board=hint,
    )


__all__ = ["BoardPlanWeights", "DEFAULT_WEIGHTS", "plan_board", "transition_note"]
