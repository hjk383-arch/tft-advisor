"""판매 추천: 필요 없는 보유 유닛을 팔아 골드를 챙긴다(코드·통계 전용, Jev 호출 없음).

설계: `_workspace/21_board_trust.md` §14. 상수는 `config/weights.toml [sell]`(config.SellWeights).

입력은 보드 배치 계획(`BoardPlan`, 라인업)과 표시된 목표 덱(1위 = 기준). 판단은 전부 결정적이다.

지키는 유닛(하나라도 해당하면 팔라고 하지 않는다)
1. 이름 미상·낮은 신뢰도·추정 이름 유닛 — `View`에 들어오지 않으므로 애초에 후보가 아니다. 수만 알린다.
   추정 이름(뒷받침 없는 라이브러리 닮음, 신뢰도 상한 0.75)은 `[board_plan] min_confidence`(0.8)로 View에서 빠지고,
   여기서도 `[sell] min_confidence` 미만이면 한 번 더 거른다(21 §15) — 틀린 이름으로 팔라고 하지 않는다.
2. 보드 배치 라인업(지금 올릴 유닛).
3. 1위 덱 최종 보드(캐리·핵심 포함). `late_from_stage` 전에는 현재~다음 레벨 빌드업 보드 유닛도.
4. `protect_shown_until_stage` 이하: 표시된 2·3위 덱 최종 보드 유닛(아직 덱이 정해지지 않았다).
5. 2성이 될 만한 1성 쌍(확인된 1성 2기 이상): `late_from_stage` 전이고 그 코스트 상점 확률 ≥ `pair_min_odds`.
6. `keep_bench_star2_until_stage` 이하: 라인업 밖 벤치 2성 이상(교체 대기) — 벤치 압박이 없을 때만.

보여 주는 조건
- 초반(스테이지 ≤ `early_until_stage`): 벤치 압박(≥ `bench_near_full`, 이름 미상 포함) · 상점 구매 자리 부족 ·
  `interest_max_units`기 이하로 다음 이자 구간에 닿을 때만. 수는 `early_max`까지(이자 구간에 필요한 만큼은 예외).
- 그 뒤: 후보를 전부 보여 준다(점수 낮은 순).

판매가: 1코스트 = 사본 수, 2코스트 이상 = 코스트 x 사본 수 − (성급 − 1). `app/ledger.py sell_value`와 같은 규칙
(advisor는 app을 import하지 않으므로 여기 따로 두고, 테스트가 두 함수가 같음을 고정한다).
아이템을 든 유닛을 팔면 아이템은 아이템 벤치로 돌아온다(근거에 적는다).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ..config import SellWeights
from ..contracts import BoardPlan, CompStats, SellAdvice, ShopAdvice, ShopSlotKind, UnitOnBoard
from .candidates import stage_round
from .features import View, board_at
from .stats_source import AdvisorStats

DEFAULT_SELL = SellWeights()
STAR_COPIES = {1: 1, 2: 3, 3: 9, 4: 27}


def unit_sell_value(cost: int | None, star: int | None) -> int | None:
    """유닛 판매가(게임 규칙). 1코스트는 전액, 2코스트 이상은 2성 3c−1 / 3성 9c−2.
    성급 미상(None)이면 **최솟값**(★1 판매가)이다 — 부르는 쪽이 "최소"라고 밝힌다(`sell_advice`)."""
    if cost is None:
        return None
    s = star if star is not None else 1
    n = STAR_COPIES.get(s, 1)
    if cost <= 1:
        return cost * n
    return cost * n - (s - 1)


def interest(gold: int, w: SellWeights = DEFAULT_SELL) -> int:
    return min(w.interest_cap, max(0, gold) // w.interest_step)


def interest_note(gold: int | None, sell_gold: int, w: SellWeights = DEFAULT_SELL) -> str | None:
    """판매 뒤 이자 변화 한 줄. 예 "팔면 30골드 → 이자 +1" / "이자 구간 30골드까지 1 남음". 골드 모름·최대 이자면 None."""
    if gold is None or interest(gold, w) >= w.interest_cap:
        return None
    after = gold + sell_gold
    gain = interest(after, w) - interest(gold, w)
    if sell_gold > 0 and gain > 0:
        return f"팔면 {after}골드 → 이자 +{gain}"
    nxt = (after // w.interest_step + 1) * w.interest_step
    if nxt - after <= w.interest_near:
        return f"이자 구간 {nxt}골드까지 {nxt - after} 남음"
    return None


@dataclass
class _Inst:
    unit: UnitOnBoard
    where: str            # "board" | "bench"
    order: int
    score: float = 0.0    # 보드 배치 점수(낮을수록 먼저 판다)
    gold: int | None = None


def _odds(view: View, stats: AdvisorStats) -> list[int] | None:
    if view.shop_odds:
        return view.shop_odds
    if view.level is not None:
        try:
            return stats.shop_odds(view.level)
        except Exception:   # 통계 어댑터에 표가 없을 수 있다
            return None
    return None


def attach_sell(plan: BoardPlan | None, view: View, stats: AdvisorStats, comps: Sequence[CompStats],
                level: int | None, shop: Sequence[ShopAdvice],
                w: SellWeights = DEFAULT_SELL, *, pinned: bool = False) -> BoardPlan | None:
    """보드 배치 계획에 판매 추천을 붙인 사본. 계획이 없거나(보드 모름) 직전·저신뢰 계획이면 그대로 돌려준다.
    pinned: 사용자 고정 덱(comps[0])이면 대안 덱(2·3위) 유닛을 지키지 않는다(21 §17.8)."""
    if plan is None or plan.stale or plan.low_trust or not w.enabled:
        return plan
    sell, total, note, notes = sell_advice(plan, view, stats, comps, level, shop, w, pinned=pinned)
    return plan.model_copy(update={"sell": sell, "sell_gold_total": total, "interest_note": note,
                                   "sell_notes": notes})


def sell_advice(plan: BoardPlan, view: View, stats: AdvisorStats, comps: Sequence[CompStats], level: int | None,
                shop: Sequence[ShopAdvice],
                w: SellWeights = DEFAULT_SELL, *,
                pinned: bool = False) -> tuple[list[SellAdvice], int, str | None, list[str]]:
    stage, _ = stage_round(view)
    early = stage is not None and stage <= w.early_until_stage
    late = stage is not None and stage >= w.late_from_stage
    state = view.state

    # 추정 이름은 판단하지 않는다(21 §15): 판매 후보·사본 수 어디에도 넣지 않는다
    board_units = [u for u in view.board if u.confidence >= w.min_confidence]
    bench_units = [u for u in view.bench if u.confidence >= w.min_confidence]
    extra_guessed = len(view.board) + len(view.bench) - len(board_units) - len(bench_units)
    units = board_units + bench_units
    insts = [_Inst(u, "board", i) for i, u in enumerate(board_units)]
    insts += [_Inst(u, "bench", len(insts) + i) for i, u in enumerate(bench_units)]
    scores: dict[tuple[str, int | None], list[float]] = {}
    for e in plan.bench:
        scores.setdefault((e.unit_id, e.star), []).append(e.score)

    # --- 라인업(지금 올릴 유닛) 소비: (id, 성급, 보드 여부)가 같은 인스턴스 하나씩 ---
    in_lineup: set[int] = set()
    for e in plan.lineup:
        for x in insts:
            if x.order in in_lineup or x.unit.id != e.unit_id or x.unit.star != e.star:   # 성급 미상은 미상끼리
                continue
            if (x.where == "board") != e.on_board:
                continue
            in_lineup.add(x.order)
            break

    # --- 목표 덱 경로 ---
    top = comps[0] if comps else None
    final_top: set[str] = set()
    path: set[str] = set()
    if top is not None:
        final_top = {u.id for u in top.final_board} | ({top.carry} if top.carry else set())
        path |= final_top
        lv = level if level is not None else view.level
        if not late and lv is not None:
            for d in range(w.buildup_levels_ahead + 1):
                b = board_at(top, lv + d)
                if b is not None:
                    path.update(b.units)
        if not late:   # 보드 배치가 따르는 기준 보드·다음 레벨 보드(21 §17)도 지킨다 — 두 추천이 엇갈리지 않게
            path.update(plan.reference_units)
            path.update(plan.next_level_units)
    shown_final: set[str] = set()
    if not pinned and (stage is None or stage <= w.protect_shown_until_stage):   # 고정 덱이면 대안 덱을 지키지 않는다(21 §17.8)
        for c in comps[1:]:
            shown_final |= {u.id for u in c.final_board}

    # --- 벤치 압박 · 상점 구매 자리 ---
    bench_used = len(state.bench) if state.bench is not None else None
    # 성급 미상(None)은 ★1 쌍에도 ★2에도 세지 않는다(★1 가정 금지)
    ones = Counter(u.id for u in units if u.star == 1)
    stars2 = Counter(u.id for u in units if u.star is not None and u.star >= 2)
    buys_needing_slot = [a for a in shop if a.buy and a.kind == ShopSlotKind.CHAMPION and a.offer_id
                         and ones[a.offer_id] < 2]   # 확인된 1성 2기가 있으면 사는 즉시 합성된다(자리 불필요)
    free = (w.bench_size - bench_used) if bench_used is not None else None
    full = free is not None and free <= 0
    pressure = bench_used is not None and bench_used >= w.bench_near_full
    need_slot = free is not None and len(buys_needing_slot) > free

    odds = _odds(view, stats)

    def pair_reasonable(uid: str) -> tuple[bool, str | None]:
        if late:
            return False, "후반이라 2성 대기보다 골드가 낫습니다"
        cost = stats.champion_cost(uid)
        if odds is None or cost is None or not 1 <= cost <= len(odds):
            return True, None
        p = odds[cost - 1]
        if p >= w.pair_min_odds:
            return True, None
        return False, f"2성 가능성이 낮습니다(상점 {cost}코스트 {p}%)"

    cands: list[tuple[_Inst, str]] = []
    for x in insts:
        uid, star = x.unit.id, x.unit.star
        if x.order in in_lineup:
            continue
        if uid in path:
            continue
        if uid in shown_final:
            continue
        why: str | None = None
        if star == 1 and ones[uid] >= 2:
            ok, why = pair_reasonable(uid)
            if ok:
                continue
        elif star == 1 and stars2[uid]:
            why = "2성이 이미 있어 3성은 어렵습니다"
        if (star is not None and star >= 2 and x.where == "bench" and stage is not None and stage <= w.keep_bench_star2_until_stage
                and not (full or need_slot)):
            continue
        if why is None:
            why = "최종 덱에 없는 유닛입니다" if late else "목표 덱·빌드업에 없습니다"
        if x.where == "board":
            why = "보드에서 빼도 되는 유닛 · " + why
        if x.unit.items:
            why += f" · 아이템 {len(x.unit.items)}개는 벤치로 돌아옵니다"
        x.gold = unit_sell_value(stats.champion_cost(uid), star)
        if star is None:
            why += " · 성급 미확인(판매가는 최소값)"
        sc = scores.get((uid, x.unit.star), [])
        x.score = min(sc) if sc else 0.0
        cands.append((x, why))

    # 파는 순서: 벤치 먼저(자리) → 보드 배치 점수 낮은 순 → 판매가 낮은 순
    cands.sort(key=lambda t: (t[0].where != "bench", t[0].score, t[0].gold or 0, t[0].order))

    gold = view.gold
    notes: list[str] = []
    chosen: list[tuple[_Inst, str]]
    if early:
        chosen = []
        if pressure or need_slot:
            chosen = cands[:max(w.early_max, len(buys_needing_slot) - (free or 0))]
        if gold is not None and interest(gold, w) < w.interest_cap and w.interest_max_units:
            step = (gold // w.interest_step + 1) * w.interest_step
            run, prefix = 0, []
            for c in cands[:w.interest_max_units]:
                prefix.append(c)
                run += c[0].gold or 0
                if gold + run >= step:
                    break
            if prefix and gold + run >= step and len(prefix) > len(chosen):
                chosen = prefix
    else:
        chosen = cands

    if bench_used is not None and (pressure or need_slot):
        tag = "가득 참" if full else "거의 참"
        msg = f"벤치 {bench_used}/{w.bench_size} {tag}"
        if need_slot:
            msg += f" — 상점 구매 {len(buys_needing_slot)}기 자리가 모자랍니다"
        if not chosen:
            msg += " · 팔 만한 확인 유닛이 없습니다"
        notes.append(msg)
    hidden = plan.unknown_on_board + plan.unknown_on_bench + extra_guessed
    guessed = view.owned.guessed + extra_guessed
    if hidden and (chosen or pressure or need_slot):
        tail = f"(추정 이름 {guessed}기 포함)" if guessed else ""
        notes.append(f"미확인 유닛 {hidden}기{tail}는 판단하지 않았습니다")

    out = [SellAdvice(unit_id=x.unit.id, star=x.unit.star, where=x.where,   # type: ignore[arg-type]
                      hex=x.unit.hex if x.where == "board" else None,
                      bench_slot=x.unit.bench_slot if x.where == "bench" else None,
                      gold=x.gold, items=list(x.unit.items), reason=why)
           for x, why in chosen]
    total = sum(s.gold or 0 for s in out)
    note = interest_note(gold, total, w) if out else None
    return out, total, note, notes


__all__ = ["DEFAULT_SELL", "attach_sell", "interest", "interest_note", "sell_advice", "unit_sell_value"]
