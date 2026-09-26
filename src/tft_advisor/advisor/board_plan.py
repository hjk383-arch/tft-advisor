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

**목표 덱 빌드업 기준(21 §17, 2026-09-25, 기본)**: 사용자 규칙 "각 최종 덱에 맞는 빌드업을 추천해야 한다".
목표 덱(1위 또는 고정 덱)의 내 레벨 빌드업 보드(`CompStats.buildup`, MetaTFT comp_details)를 **기준 보드**로 삼는다.
1) 기준 보드 = 그 레벨 보드 중 표본 상위 `ref_top_boards`개에서 보유 유닛이 가장 많은 보드(동률 games → avg_place).
   내 레벨에 없으면 가장 가까운 아래 레벨(없으면 위 레벨). 다음 목표 = 그 위 레벨 보드(기준 + 보유와 가장 겹치는 보드).
2) 기준 보드 보유 유닛을 **먼저**(구조적으로) 올린다 — 같은 챔피언은 높은 성급 사본, 성급 미상은 ★1로 가정하지 않는다.
3) 남는 칸만 임시 유닛: 기준 보드 특성 기여(x `ref_trait_mult`) → 다음 레벨 빌드업 소속 → 성급·아이템 →
   s_now·스테이지 보드 통계(x `ref_now_scale`, 보조). 초반 2성 강한 유닛은 이렇게 빈 자리를 지킨다.
4) 스테이지 보드 추천·다음 스테이지·전환 문구는 notes에서 뺀다(계약 필드 `stage_board`·`transition`은 유지).
`follow_buildup = false`이거나 목표 덱·빌드업이 없으면 위의 예전 동작이다.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from ..config import BoardPlanWeights, UnitStageWeights
from ..contracts import (
    BoardPlan,
    BoardPlanEntry,
    BoardSwap,
    BoardTransition,
    UNKNOWN_UNIT_ID,
    BuildupBoard,
    CompStats,
    StageBoardHint,
    UnitOnBoard,
)
from ..unit_status import OwnedUnits
from .candidates import stage_round
from .features import View, board_at
from .stage_boards import BoardPick, NextHint, StageBoardSource, vs_baseline
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
          w: BoardPlanWeights, sf: _Stage = _Stage(), sig=None, in_pick: bool = False,
          buildup_label: str = "빌드업 유닛", quiet_now: bool = False) -> None:
    """sig: `stage_boards.UnitSignal | None`(유닛 스테이지 성적), in_pick: 추천 스테이지 보드에 든 유닛.
    quiet_now: '지금 강함' 항(s_now·스테이지 통계)을 점수에만 넣고 근거 문구에는 쓰지 않는다(기준 보드 모드, 21 §17)."""
    uid, star = c.unit.id, c.unit.star
    fb = {u.id: u for u in comp.final_board} if comp is not None else {}
    if comp is not None and uid == comp.carry:
        c.reasons.append((w.carry * sf.comp, "목표 덱 캐리"))
    elif uid in fb and fb[uid].is_core:
        c.reasons.append((w.core * sf.comp, "목표 덱 핵심"))
    elif uid in fb:
        c.reasons.append((w.final * sf.comp, "목표 덱 유닛"))
    elif uid in buildup:
        c.reasons.append((w.buildup * sf.comp, buildup_label))
    c.comp_part = c.reasons[0][0] if c.reasons else 0.0
    hidden = 0.0                    # 근거 문구에는 안 나오는 점수
    if star is None:                # 성급 미상: ★1로 가정하지 않는다(★1 < 미상 < ★2)
        hidden += w.star_unknown
    elif star >= 3:
        c.reasons.append((w.star3, f"{star}성"))
    elif star == 2:
        c.reasons.append((w.star2, "2성"))
    n_items = len(c.unit.items)
    if n_items:
        c.reasons.append((w.per_item * n_items, "아이템 보유자" if n_items == 1 else f"아이템 {n_items}개"))
    s = snow.get(uid, 0.0)
    if s >= w.stat_reason_min and not quiet_now:
        c.reasons.append((w.stat * s * sf.now, "현 레벨 통계 상위"))
    else:
        hidden += w.stat * s * sf.now
    if sig is not None:             # 스테이지 유닛 성적(수축 delta → −1~1). 나쁘면 감점, 근거에는 좋을 때만 나온다
        v = w.stage_unit * sig.value * sf.now
        if v > 0 and not quiet_now:
            c.reasons.append((v, sig.reason()))
        else:
            hidden += v
    if in_pick:
        if quiet_now:
            hidden += w.board_member * sf.now
        else:
            c.reasons.append((w.board_member * sf.now, "추천 스테이지 보드"))
    c.base = sum(v for v, _ in c.reasons) + w.per_cost * (stats.champion_cost(uid) or 0) + hidden


def star_rank(star: int | None) -> float:
    """사본 고르기용 성급 순위. 미상은 ★1로 가정하지 않는다(★1 < 미상 < ★2)."""
    return 1.5 if star is None else float(star)


@dataclass(frozen=True)
class BuildupRef:
    """목표 덱 레벨별 빌드업 기준 보드(21 §17)."""

    level: int                         # 내 레벨(기준으로 삼은 값)
    ref_level: int                     # 기준 보드 레벨(내 레벨에 없으면 가장 가까운 아래, 없으면 위)
    board: BuildupBoard
    next_level: int | None = None
    next_board: BuildupBoard | None = None

    @property
    def units(self) -> list[str]:
        return list(dict.fromkeys(self.board.units))

    @property
    def next_units(self) -> list[str]:
        return list(dict.fromkeys(self.next_board.units)) if self.next_board is not None else []


def _usable_boards(comp: CompStats, lv: int, w: BoardPlanWeights) -> list[BuildupBoard]:
    """레벨 lv 빌드업 보드 중 쓸 만한 것(유닛 수 ≥ lv − gap), 표본 상위 `ref_top_boards`개(games ↓, avg_place ↑)."""
    ok = [b for b in comp.buildup.get(lv) or [] if b.units and len(set(b.units)) >= lv - w.ref_min_units_gap]
    ok.sort(key=lambda b: (-(b.games or 0), b.avg_place if b.avg_place is not None else 9.0))
    return ok[:w.ref_top_boards]


def _best_match(boards: list[BuildupBoard], have: set[str]) -> BuildupBoard | None:
    """보유(have)와 가장 많이 겹치는 보드. 동률이면 표본 순서(games → avg_place) 그대로."""
    best, best_n = None, -1
    for b in boards:
        n = len(set(b.units) & have)
        if n > best_n:
            best, best_n = b, n
    return best


def buildup_reference(comp: CompStats | None, level: int | None, owned_ids: set[str],
                      w: BoardPlanWeights = DEFAULT_WEIGHTS) -> BuildupRef | None:
    """목표 덱의 레벨 level 기준 보드. 목표 덱·레벨·빌드업이 없으면 None."""
    if comp is None or level is None or not comp.buildup:
        return None
    levels = sorted(lv for lv in comp.buildup if _usable_boards(comp, lv, w))
    if not levels:
        return None
    lower = [lv for lv in levels if lv <= level]
    ref_lv = max(lower) if lower else min(levels)
    board = _best_match(_usable_boards(comp, ref_lv, w), owned_ids)
    if board is None:
        return None
    higher = [lv for lv in levels if lv > max(ref_lv, level)]
    nxt_lv = higher[0] if higher else None
    nxt = _best_match(_usable_boards(comp, nxt_lv, w), owned_ids | set(board.units)) if nxt_lv else None
    return BuildupRef(level=level, ref_level=ref_lv, board=board, next_level=nxt_lv if nxt else None, next_board=nxt)


def _marginal(c: _Cand, counts: Counter[str], chosen: set[str], key_traits: set[str], stats: AdvisorStats,
              ko: Callable[[str], str], w: BoardPlanWeights,
              sf: _Stage = _Stage(), key_mult: float | None = None) -> tuple[float, list[tuple[float, str]]]:
    """key_mult: 핵심 특성 배수(None이면 1 + (trait_key_mult − 1) x sf.comp — 예전 동작)."""
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
            if t in key_traits:
                mult = key_mult if key_mult is not None else 1.0 + (w.trait_key_mult - 1.0) * sf.comp
            else:
                mult = 1.0
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
        stat += f" · {vs_baseline(pick.delta)}"
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
    subject = "이 보드는" if nh.close else "비슷한 보드는"
    return f"다음 스테이지: {subject} 보통 {where} 쪽으로 이어집니다({tail})"


def _guess_tail(n: int) -> str:
    """미확인 수 뒤 꼬리(21 §15): 추정 이름도 미확인으로 셌음을 밝힌다."""
    return f"(추정 이름 {n}기 포함)" if n else ""


LOW_TRUST_NOTE = "일부 유닛 미확인 — 이름을 확인한 유닛 기준입니다(보드·벤치 신뢰도 낮음)"


def relaxed_view(view: View) -> View | None:
    """필드 신뢰도가 낮아(예: 구매 추적이 애매해 보드 0.50) 보유 유닛을 통째로 못 쓰는 View → 칸마다 이름 신뢰도가
    임계값 이상인 유닛만 쓰는 View(보드 배치 전용, 21 §14.2). 이름 미상·낮은 신뢰도 칸은 자리만 센다.
    쓸 유닛이 하나도 없으면 None. 목표 덱·상점 계산에는 쓰지 않는다(그쪽 규칙은 `unit_status.owned_units`).
    추정 이름(신뢰도 < `view.unit_min_conf`, 21 §15)도 이름 미상처럼 자리만 센다."""
    st = view.state
    if st.board is None:
        return None
    thr = max(view.min_conf, view.unit_min_conf)

    def side(units):
        named = [u for u in units or [] if u.id != UNKNOWN_UNIT_ID and u.confidence >= view.min_conf]
        used = tuple(u for u in named if u.confidence >= thr)
        return used, len(units or []) - len(used), len(named) - len(used)
    board, bh, bg = side(st.board)
    bench, nh, ng = side(st.bench)
    if not board and not bench:
        return None
    owned = OwnedUnits(board=board, bench=bench, board_reliable=False, bench_reliable=False, board_hidden=bh,
                       bench_hidden=nh, board_seen=True, bench_seen=st.bench is not None,
                       board_guessed=bg, bench_guessed=ng)
    return replace(view, units_known=True, units_complete=False, board_complete=False, owned=owned,
                   board=list(board), bench=list(bench))


def plan_view(view: View) -> tuple[View, bool] | None:
    """보드 배치가 실제로 쓰는 View와 저신뢰 여부. 필드 신뢰도가 낮으면 `relaxed_view`(21 §14.2). 계획을 못 세우면 None."""
    if view.state.board is None:
        return None
    if view.units_known:
        return view, False
    relaxed = relaxed_view(view)
    return (relaxed, True) if relaxed is not None else None


def view_reference(view: View, comp: CompStats | None, level: int | None,
                   w: BoardPlanWeights = DEFAULT_WEIGHTS) -> BuildupRef | None:
    """보드 배치와 **같은** 기준 보드(같은 보유 집합·같은 레벨 규칙). 상점 부족 가산이 이것을 쓴다(21 §17.12) —
    "상점에서 구하세요" 목록과 상점 가산 대상이 저신뢰 프레임에서도 어긋나지 않게."""
    if not w.follow_buildup:
        return None
    pv = plan_view(view)
    if pv is None:
        return None
    v = pv[0]
    board_now = len(v.state.board or [])
    lv = level if level is not None else (max(v.level, board_now) if v.level is not None else board_now)
    return buildup_reference(comp, lv, {u.id for u in v.units}, w)


def stale_copy(plan: BoardPlan) -> BoardPlan:
    """이번 화면에서 보드를 못 읽었을 때 보여 줄 직전 계획(21 §14.2). 판매 추천은 비운다(골드·유닛이 바뀌었을 수 있다)."""
    if plan.stale:
        return plan
    return plan.model_copy(update={"stale": True, "sell": [], "sell_gold_total": 0, "interest_note": None,
                                   "sell_notes": []})


def plan_board(view: View, stats: AdvisorStats, comp: CompStats | None, level: int | None,
               snow: dict[str, float], ko: Callable[[str], str],
               w: BoardPlanWeights = DEFAULT_WEIGHTS, *, stage: UnitStageWeights | None = None,
               stage_board: StageBoardSource | None = None) -> BoardPlan | None:
    """보드 배치 추천. 보드를 못 읽었거나 이름 아는 유닛이 하나도 없으면 None.

    stage: 초반 '지금 강함' 우선 스케줄(None이면 스테이지 무관 — 예전 동작).
    stage_board: 스테이지 보드 통계(None이면 그 항 0 — 예전 동작).
    """
    state = view.state
    if state.board is None:
        return None
    low_trust = False
    if not view.units_known:
        # 필드 신뢰도가 떨어졌다고 섹션을 통째로 지우지 않는다(21 §14.2): 이름 신뢰도 높은 유닛으로 세운다
        relaxed = relaxed_view(view)
        if relaxed is None:
            return None
        view, low_trust = relaxed, True
    o = view.owned
    cands = [_Cand(u, True, i, _unit_traits(u, stats)) for i, u in enumerate(view.board)]
    cands += [_Cand(u, False, len(cands) + i, _unit_traits(u, stats)) for i, u in enumerate(view.bench)]
    if not cands:
        return None

    notes: list[str] = [LOW_TRUST_NOTE] if low_trust else []
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
    # 추정 이름(21 §15)은 이름 미상과 똑같이: 보드 쪽은 자리만 차지한 채 그대로, 벤치 쪽은 올리지도 팔지도 않는다
    if unknown_board:
        notes.append(f"보드 미확인 {unknown_board}기{_guess_tail(o.board_guessed)}는 그대로 두었습니다"
                     "(정체를 몰라 판단하지 않습니다)")
    if unknown_bench:
        notes.append(f"벤치 미확인 {unknown_bench}기{_guess_tail(o.bench_guessed)}는 판단하지 않았습니다"
                     " — 강한 유닛이면 직접 올려 주세요")
    if not o.bench_seen:
        notes.append("벤치 미인식: 보드 유닛만으로 판단했습니다")

    key_traits = {t.id for t in comp.key_traits} if comp is not None else set()
    sf = _stage_factors(view, stage)
    comp_id = comp.comp_id if comp is not None else None
    owned_ids = {c.unit.id for c in cands}
    ref: BuildupRef | None = None
    if w.follow_buildup:   # view_reference와 같은 규칙(상점 가산이 같은 목록을 쓴다, 21 §17.12)
        ref = buildup_reference(comp, level if level is not None else slots, owned_ids, w)
    ref_ids = set(ref.units) if ref is not None else set()
    buildup: set[str] = set()
    buildup_label = "빌드업 유닛"
    key_mult: float | None = None
    if ref is not None:
        # 기준 보드가 드라이버: 목표 덱 소속은 스테이지와 무관하게 온전히, 지금 강함(s_now·스테이지 통계)은 보조
        sf = _Stage(comp=1.0, now=w.ref_now_scale)
        buildup = set(ref.next_units) - ref_ids
        if ref.next_level is not None:
            buildup_label = f"레벨 {ref.next_level} 빌드업"
        for u in ref.units:            # 기준 보드 특성(목표 덱 핵심 특성과 함께)을 임시 유닛이 채우게 한다
            key_traits.update(stats.champion_traits(u))
        key_mult = w.ref_trait_mult
    elif comp is not None and level is not None:
        for lv in (level, level + 1):
            b = board_at(comp, lv)
            if b is not None:
                buildup.update(b.units)
    pick: BoardPick | None = None
    if stage_board is not None:
        owned_stars: dict[str, int] = {}
        for c in cands:
            # 스테이지 보드 매칭용 최소 성급: 성급 미상은 "적어도 ★1"(보유 여부만 쓴다)
            owned_stars[c.unit.id] = max(owned_stars.get(c.unit.id, 0), c.unit.star if c.unit.star is not None else 1)
        pick = stage_board.best_board(owned_stars, view.stage, slots, comp_id, sf.comp)
    in_pick = set(pick.owned) if pick is not None else set()
    w_base = w.model_copy(update={"buildup": w.buildup + w.next_member}) if ref is not None else w
    for c in cands:
        sig = stage_board.unit_signal(c.unit.id, c.unit.star, view.stage, slots) if stage_board is not None else None
        _base(c, comp, buildup, snow, stats, w_base, sf, sig, c.unit.id in in_pick, buildup_label,
              quiet_now=ref is not None)

    # 탐욕 선정. 기준 보드가 있으면 1단계: 기준 보드 보유 유닛(챔피언마다 가장 좋은 사본 1기)을 먼저 전부
    counts: Counter[str] = Counter()
    chosen_ids: set[str] = set()
    left = list(cands)
    lineup: list[_Cand] = []

    def take(pool_ok: Callable[[_Cand], bool]) -> None:
        while len(lineup) < open_slots:
            best, best_key, best_extra = None, None, []
            for c in left:
                if not pool_ok(c):
                    continue
                m, extra = _marginal(c, counts, chosen_ids, key_traits, stats, ko, w, sf, key_mult)
                key = (-m, not c.on_board, -star_rank(c.unit.star), c.unit.id, c.order)
                if best_key is None or key < best_key:
                    best, best_key, best_extra = c, key, extra
            if best is None or best_key is None:
                return
            best.score = -best_key[0]
            best.pick_reasons = best_extra
            lineup.append(best)
            left.remove(best)
            if best.unit.id not in chosen_ids:
                chosen_ids.add(best.unit.id)
                counts.update(best.traits)

    if ref is not None:
        take(lambda c: c.unit.id in ref_ids and c.unit.id not in chosen_ids)
    take(lambda c: True)
    for c in left:
        m, extra = _marginal(c, counts, chosen_ids, key_traits, stats, ko, w, sf, key_mult)
        c.score, c.pick_reasons = m, extra

    ref_label = f"레벨 {ref.ref_level} 빌드업" if ref is not None else ""

    def entry(c: _Cand, fielded: bool) -> BoardPlanEntry:
        action = ("keep" if c.on_board else "field") if fielded else ("bench" if c.on_board else "stay")
        in_ref = (c.unit.id in ref_ids) if ref is not None else None
        if fielded:
            reason = _reason_text(c.reasons + c.pick_reasons)
            if in_ref:
                reason = ref_label + (f" · {reason}" if reason else "")
            elif ref is not None:
                reason = "임시" + (f" · {reason}" if reason else "")
        elif c.unit.id in chosen_ids:
            reason = "같은 챔피언이 보드에 있습니다(합성 대기)"
        else:
            reason = "자리 부족 — 우선순위가 낮습니다"
        return BoardPlanEntry(unit_id=c.unit.id, star=c.unit.star, on_board=c.on_board, action=action,
                              score=round(c.score, 3), reason=reason, in_reference=in_ref)

    ups = sorted((c for c in lineup if not c.on_board), key=lambda c: (-c.score, c.order))
    if ref is not None:   # 기준 보드 유닛을 먼저 올린다
        ups.sort(key=lambda c: c.unit.id not in ref_ids)
    downs = sorted((c for c in left if c.on_board), key=lambda c: (c.score, c.order))
    swaps = [BoardSwap(field_unit_id=u.unit.id, bench_unit_id=d.unit.id) for u, d in zip(ups, downs, strict=False)]
    swaps += [BoardSwap(field_unit_id=u.unit.id) for u in ups[len(downs):]]
    lineup_ids = list(dict.fromkeys(c.unit.id for c in lineup))
    hint = None
    nh = stage_board.next_hint(lineup_ids, view.stage, slots, comp_id) if stage_board is not None and lineup_ids else None
    if ref is None:       # 기준 보드가 있으면 스테이지 통계 문구는 내지 않는다(다른 보드를 권해 헷갈린다, 21 §17)
        if pick is not None:
            notes.append(_pick_note(pick, ko))
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
        transition = _transition(comp, lineup, owned_ids, level)
        if ref is not None:
            transition = transition.model_copy(update={
                "next_level": ref.next_level,
                "next_targets": ([u for u in ref.next_units if u not in owned_ids] if ref.next_board is not None
                                 else [u.id for u in comp.final_board if u.id not in owned_ids])[:5]})
        else:
            note = transition_note(transition, ko)
            if note:
                notes.append(note)
    ref_fields: dict = {}
    if ref is not None:
        ref_fields = dict(
            level=min(10, max(1, ref.level)), reference_level=ref.ref_level, reference_units=ref.units,
            reference_games=ref.board.games, reference_avg_place=ref.board.avg_place,
            owned_in_reference=[u for u in ref.units if u in owned_ids],
            missing=[u for u in ref.units if u not in owned_ids],
            next_level=ref.next_level, next_level_units=ref.next_units)
    return BoardPlan(
        comp_id=comp.comp_id if comp is not None else None, slots=slots,
        lineup=[entry(c, True) for c in lineup],
        bench=[entry(c, False) for c in sorted(left, key=lambda c: (-c.score, c.order))],
        swaps=swaps, free_slots=max(0, open_slots - len(lineup)),
        unknown_on_board=unknown_board, unknown_on_bench=unknown_bench, notes=notes, transition=transition,
        stage_board=hint, low_trust=low_trust, **ref_fields,
    )


__all__ = ["BoardPlanWeights", "BuildupRef", "DEFAULT_WEIGHTS", "LOW_TRUST_NOTE", "buildup_reference", "plan_board",
           "plan_view", "relaxed_view", "stale_copy", "star_rank", "transition_note", "view_reference"]
