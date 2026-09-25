"""GameState(View) + 후보 덱 → Jev용 영어 state(JSON 객체), ID ↔ 영어 이름 사전 (설계 §4).

규칙
- 신뢰 불가/None 필드는 키 자체를 뺀다(§4.1, §4.3). "unknown" 문자열을 넣지 않는다.
- 통계 수치(avg_place, games, 티어)는 넣지 않는다(이중 계산 방지).
- 숫자는 코드가 구간 라벨로 바꿔 함께 넣는다.
- `main_carry`는 반드시 `CompStats.carry`(QA N5b). `role="carry"`는 딜러 의미라 덱당 여러 명일 수 있다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..contracts import CompStats, ShopSlotKind
from .candidates import Candidate
from .features import (
    View,
    board_at,
    buy_makes_2star,
    clean_desc,
    copies_owned,
    craftable_items,
    gold_status,
    odds_label,
    stage_phase,
    streak_text,
)
from .stats_source import AdvisorStats


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def state_hash(jev_state: dict[str, Any], questions_version: str, model: str) -> str:
    """§8.3 캐시 키 = Recommendation.state_hash."""
    return hashlib.sha256((canonical_json(jev_state) + questions_version + model).encode("utf-8")).hexdigest()


@dataclass
class NameBook:
    """요청 1회 동안 ID → 고유한 영어 이름. 같은 이름이 다른 ID에 이미 쓰였으면 접미사를 붙인다."""

    stats: AdvisorStats
    by_id: dict[str, str] = field(default_factory=dict)
    by_name: dict[str, str] = field(default_factory=dict)

    def __call__(self, api_id: str) -> str:
        if api_id in self.by_id:
            return self.by_id[api_id]
        base = self.stats.name(api_id, "en") or _strip_id(api_id)
        name = base
        n = 2
        while name in self.by_name:
            name = f"{base} ({n})"
            n += 1
        self.by_id[api_id] = name
        self.by_name[name] = api_id
        return name

    def id_of(self, name: str) -> str | None:
        return self.by_name.get(name)


def _strip_id(api_id: str) -> str:
    s = api_id
    for pre in ("DA_18_", "DA_", "TFT18_", "TFT_"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return s.replace("_", " ")


def trait_label(trait_id: str, names: NameBook) -> str:
    return names(trait_id)


def augment_entry(aug_id: str, stats: AdvisorStats, names: NameBook) -> tuple[dict[str, str], bool]:
    """(state 항목, 설명 없음/의미 손실 여부). 설명 우선순위: desc_en(`?` 없음) → desc_en_opgg → desc_en(?→X)."""
    rec = stats.augment(aug_id) or {}
    desc = rec.get("desc_en")
    opgg = rec.get("desc_en_opgg")
    if desc and "?" in desc and opgg and "?" not in opgg:
        desc = opgg
    elif not desc:
        desc = opgg
    cleaned, lost = clean_desc(desc)
    if cleaned is None:
        return {"name": names(aug_id), "description": "unknown (new augment)"}, True
    return {"name": names(aug_id), "description": cleaned}, lost


def _unit_entry(u, stats: AdvisorStats, names: NameBook, with_items: bool = True) -> dict[str, Any]:
    e: dict[str, Any] = {
        "unit": names(u.id),
        "cost": stats.champion_cost(u.id),
        "star": u.star,
        "traits": [names(t) for t in stats.champion_traits(u.id)],
    }
    if with_items:
        e["items"] = [names(i) for i in u.items]
    return {k: v for k, v in e.items() if v is not None}


def unidentified_note(view: View) -> dict[str, Any]:
    """부분 확인일 때 Jev에 알리는 미확인 칸 요약(board/bench 목록에 없는 유닛)."""
    o = view.owned
    out: dict[str, Any] = {}
    out["board"] = o.board_hidden if o.board_seen else "not read"
    out["bench"] = o.bench_hidden if o.bench_seen else "not read"
    out["note"] = ("Only identified units are listed in board and bench. The unidentified units may be any "
                   "champion, so a unit missing from those lists may still be owned.")
    return out


def active_traits(view: View, stats: AdvisorStats, names: NameBook) -> list[dict[str, Any]]:
    """보드 유닛(서로 다른 챔피언)에서 계산한 활성 특성. 보드를 모르면 호출하지 않는다."""
    counts: dict[str, int] = {}
    for uid in {u.id for u in view.board}:
        for t in stats.champion_traits(uid):
            counts[t] = counts.get(t, 0) + 1
    out = []
    for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        bps = stats.trait_breakpoints(t)
        active = max((b for b in bps if b <= n), default=None)
        nxt = min((b for b in bps if b > n), default=None)
        e: dict[str, Any] = {"trait": names(t), "count": n}
        if active is not None:
            e["active_at"] = active
        if nxt is not None:
            e["next_at"] = nxt
        out.append(e)
    return out


def comp_entry(c: Candidate, stats: AdvisorStats, names: NameBook, comp_label: str) -> dict[str, Any]:
    comp: CompStats = c.comp
    final = []
    for u in comp.final_board:
        e: dict[str, Any] = {"unit": names(u.id), "cost": stats.champion_cost(u.id)}
        role = u.role
        if u.id == comp.carry:
            role = "main carry"
        elif role == "carry":
            role = "secondary carry"
        if role:
            e["role"] = role
        if u.is_core:
            e["core"] = True
        if u.items:
            e["items"] = [names(i) for i in u.items]
        final.append({k: v for k, v in e.items() if v is not None})
    entry: dict[str, Any] = {"name": comp_label}
    if comp.levelling:
        entry["plan"] = comp.levelling
    if comp.carry:
        entry["main_carry"] = names(comp.carry)
    entry["main_carry_items"] = [names(i) for i in comp.carry_bis_items]
    entry["tanks"] = [names(u.id) for u in comp.final_board if u.role == "tank"]
    entry["key_traits"] = [f"{names(t.id)} {t.count}" for t in comp.key_traits]
    entry["final_board"] = final
    if c.L is not None:
        buildup = {}
        for lv in (c.L, c.L + 1):
            b = board_at(comp, lv)
            if b is not None and lv <= 10:
                buildup[f"level {lv}"] = [names(u) for u in b.units]
        if buildup:
            entry["buildup"] = buildup
    return entry


@dataclass
class StateParts:
    state: dict[str, Any]
    comp_labels: list[str]   # candidate_comps[k].name
    shop_desc_lost: dict[int, bool] = field(default_factory=dict)   # 특수 상품 설명 의미 손실
    aug_desc_lost: dict[int, bool] = field(default_factory=dict)    # augment_offer 설명 없음/손실


def build_state(view: View, cands: list[Candidate], stats: AdvisorStats, names: NameBook,
                *, include_shop: bool, include_offer: bool) -> StateParts:
    game: dict[str, Any] = {}
    if view.stage is not None:
        game["stage"] = view.stage
        game["stage_phase"] = stage_phase(view.stage_number)   # type: ignore[arg-type]
    if view.level is not None:
        game["level"] = view.level
    if view.xp is not None:
        game["xp_to_next"] = max(0, view.xp[1] - view.xp[0])
    if view.gold is not None:
        game["gold"] = view.gold
        game["gold_status"] = gold_status(view.gold)
    if view.hp is not None:
        game["health"] = view.hp
        game["health_status"] = view.hp_bucket
    if view.streak is not None:
        game["streak"] = streak_text(view.streak)
    odds = view.shop_odds or (stats.shop_odds(view.level) if view.level is not None else None)
    if odds:
        game["shop_odds"] = {f"{i + 1}-cost": odds_label(p) for i, p in enumerate(odds)}

    resources: dict[str, Any] = {}
    if view.items_known:
        # 유물·찬란한 아이템도 완성템으로 취급(QA WARN N5a): BIS 비교 대상
        resources["completed_items"] = [names(i) for i in view.completed + view.others_owned + view.equipped
                                        if stats.item_category(i) != "emblem"]
        resources["emblems"] = [names(i) for i in view.emblems + view.equipped if stats.item_category(i) == "emblem"]
        resources["item_components"] = [names(i) for i in view.components]
        resources["craftable_items"] = [
            {"item": names(item), "from": [names(a), names(b)]}
            for item, (a, b) in craftable_items(view.components, stats).items()
        ]
        resources["other_items"] = [names(i) for i in view.others_misc]
    if view.augments:
        resources["augments"] = [augment_entry(a.id, stats, names)[0] for a in view.augments]

    state: dict[str, Any] = {"game": game, "resources": resources}
    if view.units_known:
        state["board"] = [_unit_entry(u, stats, names) for u in view.board]
        state["bench"] = [_unit_entry(u, stats, names) for u in view.bench]
        if view.board_complete:     # 보드에 미확인 칸이 있으면 특성 인원이 모자라게 나온다
            state["active_traits"] = active_traits(view, stats, names)
        if view.units_partial:      # 21_board_trust: 미확인 칸을 특정 챔피언으로 추측하지 않게 알린다
            state["unidentified_units"] = unidentified_note(view)

    labels: list[str] = []
    comps = []
    for c in cands:
        base = c.comp.name_en or c.comp.comp_id
        label = base
        n = 2
        while label in labels:
            label = f"{base} ({n})"
            n += 1
        labels.append(label)
        comps.append(comp_entry(c, stats, names, label))
    state["candidate_comps"] = comps
    parts = StateParts(state=state, comp_labels=labels)

    if include_shop and view.shop is not None:
        shop = []
        for i, slot in enumerate(view.shop):
            if slot.kind in (ShopSlotKind.EMPTY, ShopSlotKind.UNKNOWN) or slot.id is None:
                shop.append({"slot": i, "status": slot.kind.value})
                continue
            if slot.confidence < view.min_conf:
                shop.append({"slot": i, "status": "unknown"})
                continue
            if slot.kind == ShopSlotKind.CHAMPION:
                e: dict[str, Any] = {
                    "slot": i, "unit": names(slot.id),
                    "cost": slot.cost if slot.cost is not None else stats.champion_cost(slot.id),
                    "traits": [names(t) for t in stats.champion_traits(slot.id)],
                }
                if view.units_complete:
                    e["copies_owned"] = copies_owned(slot.id, view.units)
                    e["buy_makes_2star"] = buy_makes_2star(slot.id, view.units)
                elif view.units_known:
                    # 부분 확인: 확인된 사본 수는 하한이고, 2성 불가는 확정할 수 없다(미확인 칸에 사본이 있을 수 있다)
                    e["copies_owned_at_least"] = copies_owned(slot.id, view.units)
                    e["buy_makes_2star"] = True if buy_makes_2star(slot.id, view.units) else "unknown"
                shop.append(e)
            else:   # special
                rec = stats.shop_special(slot.id) or {}
                desc, lost = clean_desc(rec.get("desc_en"))
                e = {"slot": i, "special_offer": names(slot.id)}
                if desc is not None:
                    e["description"] = desc
                parts.shop_desc_lost[i] = lost
                shop.append(e)
        state["shop"] = shop

    if include_offer and view.augment_offer:
        offer = []
        for a_i, a in enumerate(view.augment_offer):
            entry, lost = augment_entry(a.id, stats, names)
            offer.append(entry)
            parts.aug_desc_lost[a_i] = lost
        state["augment_offer"] = offer
    return parts
