"""코드 전용 파생값(순수 함수): 신뢰도 필터, 자원 풀, 조합표, 사본 수, 구간 라벨, b(x,c), 레벨 추정, 빌드업 보드 선택.

설계: `_workspace/02_jev-strategist_design.md` §2.2, §4.1~4.3, §5.4, §6.
Jev에 보낼 문장(구간 라벨)은 여기 상수로 둔다 — 바꾸면 questions.QUESTIONS_VERSION을 올린다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from ..config import ItemFitWeights
from ..contracts import (
    AugmentRef,
    BuildupBoard,
    CompStats,
    GameState,
    ItemRef,
    ShopSlot,
    UnitOnBoard,
    stage_tuple,
)
from .stats_source import AdvisorStats

# ItemState.others 중 "보유 완성템" 풀에 넣는 카테고리(QA WARN N5a: 유물·찬란한 BIS)
OWNED_OTHER_CATEGORIES = frozenset({"artifact", "radiant"})

HP_BUCKETS = ("healthy", "moderate", "low", "critical", "unknown")


# ---------------------------------------------------------------------------
# 구간 라벨 (Jev state 문장 — QUESTIONS_VERSION과 함께 관리)
# ---------------------------------------------------------------------------


def hp_bucket(hp: int | None) -> str:
    if hp is None:
        return "unknown"
    if hp >= 70:
        return "healthy"
    if hp >= 40:
        return "moderate"
    if hp >= 20:
        return "low"
    return "critical"


def gold_status(gold: int) -> str:
    if gold >= 50:
        return "rich (interest capped)"
    if gold >= 30:
        return "can afford a few buys and still keep interest"
    if gold >= 10:
        return "tight"
    return "broke"


_STAGE_PHASE = {
    1: "stage 1 (opening: only low-cost units, items and board are just starting)",
    2: "stage 2 (early game: field the strongest cheap board and protect health)",
    3: "stage 3 (mid game: stabilize and level toward 7-8)",
    4: "stage 4 (late mid game: level to 8 and find the final comp's core units)",
}
_STAGE_PHASE_LATE = "stage 5 or later (late game: complete and upgrade the final comp)"


def stage_phase(stage_number: int) -> str:
    return _STAGE_PHASE.get(stage_number, _STAGE_PHASE_LATE)


def odds_label(pct: int) -> str:
    if pct <= 0:
        return "none"
    if pct < 10:
        return "rare"
    if pct < 30:
        return "uncommon"
    return "common"


def streak_text(streak: int) -> str:
    if streak > 0:
        return f"{streak}-round win streak"
    if streak < 0:
        return f"{-streak}-round loss streak"
    return "no streak"


# ---------------------------------------------------------------------------
# 신뢰도 필터를 거친 GameState 뷰 (§4.1, §4.3)
# ---------------------------------------------------------------------------


@dataclass
class View:
    """advisor가 보는 '믿을 수 있는' 상태. 모르는 값은 None/False."""

    state: GameState
    stage: str | None = None
    stage_number: int | None = None
    level: int | None = None
    xp: tuple[int, int] | None = None
    gold: int | None = None
    streak: int | None = None
    hp: int | None = None
    hp_bucket: str = "unknown"
    shop_odds: list[int] | None = None
    shop: list[ShopSlot] | None = None
    units_known: bool = False
    board: list[UnitOnBoard] = field(default_factory=list)
    bench: list[UnitOnBoard] = field(default_factory=list)
    items_known: bool = False
    completed: list[str] = field(default_factory=list)      # 벤치 완성템
    emblems: list[str] = field(default_factory=list)        # 벤치 상징
    components: list[str] = field(default_factory=list)     # 벤치 재료
    others_owned: list[str] = field(default_factory=list)   # 벤치 유물·찬란한
    others_misc: list[str] = field(default_factory=list)    # 그 밖의 others(전략가·소모품 등)
    equipped: list[str] = field(default_factory=list)       # 장착분(보드 또는 추적)
    augments: list[AugmentRef] = field(default_factory=list)
    augment_offer: list[AugmentRef] = field(default_factory=list)
    min_conf: float = 0.6

    @property
    def units(self) -> list[UnitOnBoard]:
        return self.board + self.bench

    def owned_pool(self, stats: AdvisorStats) -> list[str]:
        """보유 완성템 multiset P(§5.4-1): 벤치 completed + emblems + 유물/찬란한 + 장착분."""
        if not self.items_known:
            return []
        return self.completed + self.emblems + self.others_owned + self.equipped

    def bench_owned_counter(self) -> Counter[str]:
        """equipped_tracked 추적 대상(벤치의 완성템·상징·유물·찬란한)."""
        return Counter(self.completed + self.emblems + self.others_owned)


def _reliable_refs(refs: Iterable[ItemRef], min_conf: float) -> list[ItemRef]:
    return [r for r in refs if r.confidence >= min_conf]


def build_view(state: GameState, stats: AdvisorStats, min_conf: float, equipped_tracked: Counter[str] | None) -> View:
    rel = lambda f: state.is_reliable(f, min_conf)   # noqa: E731
    v = View(state=state, min_conf=min_conf)
    if rel("stage"):
        v.stage = state.stage
        v.stage_number = stage_tuple(state.stage)[0]   # type: ignore[arg-type]
    if rel("level"):
        v.level = state.level
    if rel("xp"):
        v.xp = state.xp
    if rel("gold"):
        v.gold = state.gold
    if rel("streak"):
        v.streak = state.streak
    if rel("hp"):
        v.hp = state.hp
    v.hp_bucket = hp_bucket(v.hp)
    if rel("shop_odds"):
        v.shop_odds = list(state.shop_odds or [])
    if rel("shop"):
        v.shop = list(state.shop or [])
    # 보유 유닛: board와 bench가 둘 다 신뢰 가능해야 "안다"(§4.3 b)
    if rel("board") and rel("bench"):
        v.units_known = True
        v.board = [u for u in state.board or [] if u.confidence >= min_conf]
        v.bench = [u for u in state.bench or [] if u.confidence >= min_conf]
    if rel("items") and state.items is not None:
        v.items_known = True
        it = state.items
        v.completed = [r.id for r in _reliable_refs(it.completed, min_conf)]
        v.emblems = [r.id for r in _reliable_refs(it.emblems, min_conf)]
        v.components = [r.id for r in _reliable_refs(it.components, min_conf)]
        for r in _reliable_refs(it.others, min_conf):
            cat = r.category or stats.item_category(r.id)
            (v.others_owned if cat in OWNED_OTHER_CATEGORIES else v.others_misc).append(r.id)
        if v.units_known:
            v.equipped = [i for u in v.units for i in u.items]
        elif equipped_tracked:
            v.equipped = list(equipped_tracked.elements())
    if rel("augments_owned"):
        v.augments = [a for a in state.augments_owned or [] if a.confidence >= min_conf]
    if rel("augment_offer"):
        v.augment_offer = [a for a in state.augment_offer or [] if a.confidence >= min_conf]
    return v


# ---------------------------------------------------------------------------
# 조합(§6-1)
# ---------------------------------------------------------------------------


def craftable_items(components: list[str], stats: AdvisorStats) -> dict[str, tuple[str, str]]:
    """재료 multiset에서 만들 수 있는 완성템 → 재료 쌍(결과별 첫 쌍). 같은 재료 두 개는 2개 이상 보유 시."""
    cnt = Counter(components)
    keys = sorted(cnt)
    out: dict[str, tuple[str, str]] = {}
    for i, a in enumerate(keys):
        for b in keys[i:]:
            if a == b and cnt[a] < 2:
                continue
            res = stats.craft(a, b)
            if res and res not in out:
                out[res] = (a, b)
    return out


def is_craftable(item_id: str, stats: AdvisorStats) -> bool:
    return stats.recipe(item_id) is not None


# ---------------------------------------------------------------------------
# 아이템-덱 적합 b(x,c) (§2.2)
# ---------------------------------------------------------------------------


def key_trait_ids(comp: CompStats) -> set[str]:
    return {t.id for t in comp.key_traits}


def final_board_trait_count(comp: CompStats, trait_id: str, stats: AdvisorStats) -> int:
    """덱 최종 보드(유닛 ID 중복 제거)에서 trait_id를 가진 유닛 수. 상징 없이 보드만으로 채우는 인원."""
    seen = {u.id for u in comp.final_board}
    return sum(1 for uid in seen if trait_id in stats.champion_traits(uid))


def emblem_advances(trait_id: str, comp: CompStats, stats: AdvisorStats) -> bool:
    """§2.2 상징 규칙(Phase 3 수정): 상징 1개가 이 덱의 핵심 특성 구간을 실제로 올리는가.

    req = key_traits에서 그 특성의 목표 인원, n = 최종 보드 유닛만으로 채우는 인원, T = max(req, n).
    참: req ≥ 2 이고 [ n < req (덱이 상징으로 목표 인원을 채운다) 또는 T+1 ∈ trait_breakpoints (상징 1개로 다음 구간 도달) ].
    곁가지 특성(req 1), 이미 최고 구간, +1로 구간이 안 바뀌는 특성(예 Juggernaut 2→3, 구간 2/4/6)은 거짓.
    """
    req = next((t.count for t in comp.key_traits if t.id == trait_id), 0)
    if req < 2:
        return False
    n = final_board_trait_count(comp, trait_id, stats)
    if n < req:
        return True
    return (max(req, n) + 1) in set(stats.trait_breakpoints(trait_id))


def item_fit(item_id: str, comp: CompStats, stats: AdvisorStats, w: ItemFitWeights) -> float:
    if item_id in comp.carry_bis_items:
        return w.carry_bis
    for u in comp.final_board:
        if u.is_core and u.id != comp.carry and item_id in u.items:
            return w.core_unit
    if comp.item_usage.get(item_id, 0.0) >= w.usage_min_pcnt:
        return w.usage
    trait = stats.emblem_trait(item_id)
    if trait is not None:
        return w.emblem_key_trait if emblem_advances(trait, comp, stats) else w.emblem_other
    return 0.0


# ---------------------------------------------------------------------------
# 레벨 추정·빌드업 보드 선택 (§5.4, QA 5절 jev 3)
# ---------------------------------------------------------------------------


def estimate_level(stage: str | None, level_timing: dict[int, str]) -> int | None:
    """stage로 현재 레벨 추정: max{lv : level_timing[lv] ≤ stage}.

    공집합(모든 timing보다 이른 stage)이면 min(level_timing 키) − 1 (하한 1).
    stage None 또는 level_timing 비어 있으면 None.
    """
    if stage is None or not level_timing:
        return None
    st = stage_tuple(stage)
    reached = [lv for lv, s in level_timing.items() if stage_tuple(s) <= st]
    if reached:
        return max(reached)
    return max(1, min(level_timing) - 1)


def comp_level(view: View, comp: CompStats) -> int | None:
    """덱 c 기준 현재 레벨 L: 신뢰 가능한 GameState.level, 아니면 stage 추정."""
    if view.level is not None:
        return view.level
    return estimate_level(view.stage, comp.level_timing)


def global_level(view: View, comps: list[CompStats]) -> int | None:
    """덱과 무관한 현재 레벨(S_now 등): level, 아니면 덱별 추정의 최빈값(동률이면 낮은 값)."""
    if view.level is not None:
        return view.level
    ests = [e for c in comps if (e := estimate_level(view.stage, c.level_timing)) is not None]
    if not ests:
        return None
    cnt = Counter(ests)
    return min(cnt, key=lambda lv: (-cnt[lv], lv))


def best_board(boards: list[BuildupBoard]) -> BuildupBoard | None:
    """games 최대(None=0) → avg_place 낮은 쪽 → 원래 순서."""
    best: BuildupBoard | None = None
    for b in boards:
        if best is None:
            best = b
            continue
        g, bg = b.games or 0, best.games or 0
        if g > bg or (g == bg and (b.avg_place or 9) < (best.avg_place or 9)):
            best = b
    return best


def board_at(comp: CompStats, level: int) -> BuildupBoard | None:
    """레벨 level의 대표 보드. BUILDUP 범위(4~10) 밖이면 가장 가까운 레벨로 클램프."""
    if not comp.buildup:
        return None
    lv = min(max(level, 4), 10)
    return best_board(comp.buildup.get(lv, []))


def resource_availability(view: View, owned: list[str]) -> dict[str, bool]:
    """덱 선정 자원 신호 3종(§5.2)의 가용 여부: 아이템(보유 완성템 또는 재료) / 증강 / 보유 유닛."""
    return {
        "item": view.items_known and bool(owned or view.components),
        "augment": bool(view.augments),
        "board": view.units_known and bool(view.units),
    }


def is_late(view: View, until_stage: int) -> bool:
    """'초반'이 아닌가: 스테이지를 알고 그 번호가 until_stage 이상(09 J1). 스테이지를 모르면 False(보수적)."""
    return view.stage_number is not None and view.stage_number >= until_stage


def tempo_fit(view: View, comp: CompStats, span: float) -> tuple[float, int] | None:
    """레벨 템포 적합(09 J1): 현재 스테이지에서 이 덱이 보통 도달하는 레벨과 내 레벨의 차.

    expected = estimate_level(stage, level_timing). 반환 (1 − |level − expected| / span 을 0~1로 자른 값, expected).
    레벨·스테이지·level_timing 중 하나라도 없으면 None. 결정적 계산이라 Jev에 묻지 않는다.
    """
    if view.level is None or view.stage is None:
        return None
    exp = estimate_level(view.stage, comp.level_timing)
    if exp is None:
        return None
    x = 1.0 - abs(view.level - exp) / span if span > 0 else float(view.level == exp)
    return (0.0 if x < 0 else 1.0 if x > 1 else x), exp


def next_buildup_board(comp: CompStats, L: int | None) -> BuildupBoard | None:
    """§5.4 next_buildup_board 2~5단계."""
    if L is None or L >= 10:
        return None
    higher = [lv for lv in comp.level_timing if lv > L]
    T = min(higher) if higher else L + 1
    cand = sorted(lv for lv, boards in comp.buildup.items() if lv >= T and boards)
    if not cand:
        return None
    return best_board(comp.buildup[cand[0]])


# ---------------------------------------------------------------------------
# 유닛 사본(§4.3 b)
# ---------------------------------------------------------------------------

_STAR_EQUIV = {1: 1, 2: 3, 3: 9, 4: 27}


def copies_owned(unit_id: str, units: list[UnitOnBoard]) -> int:
    """1성 등가 개수."""
    return sum(_STAR_EQUIV.get(u.star or 1, 1) for u in units if u.id == unit_id)


def buy_makes_2star(unit_id: str, units: list[UnitOnBoard]) -> bool:
    ones = sum(1 for u in units if u.id == unit_id and (u.star or 1) == 1)
    return ones % 3 == 2


def buy_makes_3star(unit_id: str, units: list[UnitOnBoard]) -> bool:
    """1성 2 + 2성 2가 있으면 구매로 3성."""
    ones = sum(1 for u in units if u.id == unit_id and (u.star or 1) == 1)
    twos = sum(1 for u in units if u.id == unit_id and u.star == 2)
    return ones == 2 and twos == 2


# ---------------------------------------------------------------------------
# 기타
# ---------------------------------------------------------------------------


def comp_board_units(comp: CompStats, stats: AdvisorStats) -> list[str]:
    """기준 보드 B(c)(§5.4): carry → is_core → 그 외, 그룹 안에서 cost 내림차순 → ID."""
    seen: dict[str, tuple[int, int, str]] = {}
    for u in comp.final_board:
        if u.id in seen:
            continue
        group = 0 if u.id == comp.carry else (1 if u.is_core else 2)
        seen[u.id] = (group, -(stats.champion_cost(u.id) or 0), u.id)
    return sorted(seen, key=lambda k: seen[k])


_Q_RE = re.compile(r"\?")


def clean_desc(desc: str | None) -> tuple[str | None, bool]:
    """설명의 치환 안 된 `?` → `X`. 반환 (설명, 의미 손실 여부).

    의미 손실 = `?`가 있었고 숫자가 하나도 남지 않은 설명(QA N8: 147개). 호출자가 gate를 낮춘다.
    """
    if not desc:
        return None, True
    if "?" not in desc:
        return desc, False
    out = _Q_RE.sub("X", desc)
    return out, not any(ch.isdigit() for ch in out)
