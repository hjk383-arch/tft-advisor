"""덱 후보 1차 필터(설계 §2): 사전 정리(min_games, 중복 제거) → p(c) → 쿼터 포함 상위 N.

p(c)의 항 I/A/U/S는 Jev 폴백 프록시(§8.1)로도 그대로 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Weights
from ..contracts import CompStats
from .features import (
    View,
    board_at,
    comp_level,
    craftable_items,
    is_late,
    item_fit,
    key_trait_ids,
    resource_availability,
    tempo_fit,
)
from .stats_source import AdvisorStats

# 09 J1 후반 처리 값은 config/weights.toml `[comp] undecided_until_stage/w_tempo/tempo_span`, `[prefilter] w_tempo`
# (키 정의·기본값: config.py CompWeights/PrefilterWeights). 기본값 4 / 0.30 / 2.0 / 0.25.


@dataclass(frozen=True)
class LateCfg:
    undecided_until_stage: int
    w_tempo: float
    tempo_span: float
    pf_tempo: float


def late_cfg(w: Weights) -> LateCfg:
    return LateCfg(w.comp.undecided_until_stage, w.comp.w_tempo, w.comp.tempo_span, w.prefilter.w_tempo)


def tempo_active(view: View, owned: list[str], w: Weights) -> bool:
    """레벨 템포 항을 쓰는가: 후반(스테이지 ≥ undecided_until_stage)이고 보유 유닛 신호가 없다(보드 미인식).

    보드를 알면 보드 적합(C3/U)이 이미 플랜을 반영하므로 템포를 겹쳐 넣지 않는다.
    """
    return is_late(view, late_cfg(w).undecided_until_stage) and not resource_availability(view, owned)["board"]


def late_blind(view: View, owned: list[str], w: Weights) -> bool:
    """후반인데 아이템·증강·보유 유닛 신호가 하나도 없다 → UI에 '레벨 템포·메타로 추정' 안내."""
    return is_late(view, late_cfg(w).undecided_until_stage) and not any(resource_availability(view, owned).values())


@dataclass
class Candidate:
    comp: CompStats
    p: float
    I: float     # 아이템 적합 프록시
    A: float     # 증강 적합 프록시
    U: float     # 유닛 적합 프록시
    S: float     # stat_norm
    adj: float   # 수축된 평균 등수(표시용)
    L: int | None   # 이 덱 기준 현재 레벨
    T: float | None = None       # 레벨 템포 적합(09 J1, 0~1). 레벨/스테이지 모르면 None
    T_exp: int | None = None     # 이 덱이 현재 스테이지에 보통 도달하는 레벨

    @property
    def comp_id(self) -> str:
        return self.comp.comp_id


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def dedupe(comps: list[CompStats], threshold: float) -> list[CompStats]:
    """최종 보드 유닛 집합 Jaccard ≥ threshold이고 carry가 같으면 표본이 많은 쪽만 남긴다."""
    kept: list[CompStats] = []
    for c in sorted(comps, key=lambda c: (-(c.games or 0), c.comp_id)):
        units = {u.id for u in c.final_board}
        if any(k.carry == c.carry and jaccard(units, {u.id for u in k.final_board}) >= threshold for k in kept):
            continue
        kept.append(c)
    return kept


def tier_score(tier: str | None, w: Weights) -> float | None:
    return None if tier is None else w.augment.editorial_tier_score[tier]   # type: ignore[index]


def augment_comp_fit(aug_id: str, comp: CompStats, stats: AdvisorStats, w: Weights) -> float:
    """t(a,c) (§2.2)."""
    if set(stats.augment_traits(aug_id)) & key_trait_ids(comp):
        return 1.0
    t = stats.augment_tier(aug_id, comp.comp_id)
    if t is not None:
        return w.augment.editorial_tier_score[t.tier]
    t = stats.augment_tier(aug_id, None)
    if t is not None:
        return w.augment.editorial_tier_score[t.tier]
    return w.prefilter.aug_neutral


def stat_adj(comp: CompStats, owned_items: list[str], w: Weights) -> float:
    """adj(c) (§5.1): 보유 완성템 중 item_conditional에 있는 것들의 수축 평균, 없으면 덱 평균 수축."""
    sh = w.shrinkage
    base = sh.adjust(comp.avg_place if comp.avg_place is not None else sh.prior_avg_place, comp.games)
    vals = []
    for it in dict.fromkeys(owned_items):
        cond = comp.item_conditional.get(it)
        if cond is not None and cond.avg_place is not None:
            vals.append(sh.adjust(cond.avg_place, cond.games, prior=base))
    return sum(vals) / len(vals) if vals else base


def stat_norm(adj: float, w: Weights) -> float:
    best, worst = w.comp.stat_avg_best, w.comp.stat_avg_worst
    return min(1.0, max(0.0, (worst - adj) / (worst - best)))


def item_proxy(comp: CompStats, owned: list[str], craftable: list[str], stats: AdvisorStats, w: Weights) -> float:
    pf = w.prefilter
    s = sum(item_fit(x, comp, stats, w.item_fit) for x in owned)
    s += pf.craftable_factor * sum(item_fit(y, comp, stats, w.item_fit) for y in craftable)
    return min(1.0, s / pf.item_saturation)


def augment_proxy(comp: CompStats, augment_ids: list[str], stats: AdvisorStats, w: Weights) -> float:
    if not augment_ids:
        return w.prefilter.aug_neutral
    return sum(augment_comp_fit(a, comp, stats, w) for a in augment_ids) / len(augment_ids)


def unit_proxy(comp: CompStats, view: View, L: int | None, w: Weights) -> float:
    if not view.units_known:
        return 0.0
    pf = w.prefilter
    best_star: dict[str, int] = {}
    for u in view.units:
        best_star[u.id] = max(best_star.get(u.id, 0), u.star or 1)
    final = {u.id: u for u in comp.final_board}
    buildup_units: set[str] = set()
    if L is not None:
        for lv in (L, L + 1):
            b = board_at(comp, lv)
            if b is not None:
                buildup_units.update(b.units)
    total = 0.0
    for uid, star in best_star.items():
        if uid in final:
            wu = pf.unit_w_core if final[uid].is_core else pf.unit_w_final
        elif uid in buildup_units:
            wu = pf.unit_w_buildup
        else:
            continue
        if star >= 2:
            wu *= pf.unit_star_mult
        total += wu
    return min(1.0, total / pf.unit_saturation)


def score_candidate(comp: CompStats, view: View, stats: AdvisorStats, w: Weights,
                    owned: list[str], craftable: list[str], augment_ids: list[str]) -> Candidate:
    pf = w.prefilter
    L = comp_level(view, comp)
    I = item_proxy(comp, owned, craftable, stats, w)
    A = augment_proxy(comp, augment_ids, stats, w)
    U = unit_proxy(comp, view, L, w)
    adj = stat_adj(comp, owned, w)
    S = stat_norm(adj, w)
    p = pf.w_item * I + pf.w_aug * A + pf.w_unit * U + pf.w_stat * S
    tf = tempo_fit(view, comp, late_cfg(w).tempo_span)
    T, T_exp = (tf if tf is not None else (None, None))
    return Candidate(comp=comp, p=p, I=I, A=A, U=U, S=S, adj=adj, L=L, T=T, T_exp=T_exp)


def prefilter(view: View, stats: AdvisorStats, w: Weights, n: int,
              prev_shown: list[str] | None = None) -> tuple[list[Candidate], list[CompStats]]:
    """상위 N 후보(p(c) 내림차순)와 사전 정리 후 전체 덱 목록(S_now 계산용)."""
    pool = dedupe(stats.comps(min_games=w.prefilter.min_games), w.prefilter.dedupe_jaccard)
    owned = view.owned_pool(stats)
    craftable = list(craftable_items(view.components, stats)) if view.items_known else []
    aug_ids = [a.id for a in view.augments]
    scored = [score_candidate(c, view, stats, w, owned, craftable, aug_ids) for c in pool]
    if tempo_active(view, owned, w):   # 09 J1: 보드 미인식 후반에는 템포가 맞는 덱이 후보 N 안에 들어오게 한다
        wt = late_cfg(w).pf_tempo
        for c in scored:
            if c.T is not None:
                c.p += wt * c.T
    by_id = {c.comp_id: c for c in scored}

    chosen: list[str] = []
    for cid in prev_shown or []:
        if cid in by_id and cid not in chosen:
            chosen.append(cid)
    quota = min(w.prefilter.stat_quota, n)
    for c in sorted(scored, key=lambda c: (-c.S, c.comp_id))[:quota]:
        if c.comp_id not in chosen:
            chosen.append(c.comp_id)
    chosen = chosen[:n]
    for c in sorted(scored, key=lambda c: (-c.p, c.comp_id)):
        if len(chosen) >= n:
            break
        if c.comp_id not in chosen:
            chosen.append(c.comp_id)
    cands = sorted((by_id[cid] for cid in chosen), key=lambda c: (-c.p, c.comp_id))
    return cands, pool
