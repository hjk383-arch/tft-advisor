"""Phase 2 재검증: data/stats/metatft_*.json(변환 산출물) ↔ contracts 0.2.0 ↔ static ↔ 설계 가정.

실행: .venv/bin/python _workspace/qa_scripts/converted_stats_check.py
네트워크 호출 없음. 결과는 stdout(JSON 요약).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tft_advisor.contracts import AugmentTier, CompStats, UnitItemStats, UnitStats  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402

st = load_static(18)
path = max((ROOT / "data/stats").glob("metatft_*.json"))
doc = json.loads(path.read_text(encoding="utf-8"))
out: dict = {"file": path.name}

# 1. 모델 로드 + 왕복
comps = [CompStats.model_validate(c) for c in doc["comps"]]
tiers = [AugmentTier.model_validate(t) for t in doc["augment_tiers"]]
uis = [UnitItemStats.model_validate(r) for r in doc["unit_item_stats"]]
ubs = [UnitItemStats.model_validate(r) for r in doc.get("unit_build_stats", [])]   # R16(Phase 3): 정확한 빌드
us = [UnitStats.model_validate(r) for r in doc["unit_stats"]]
rt_fail = sum(CompStats.model_validate_json(c.model_dump_json()) != c for c in comps)
rt_fail += sum(AugmentTier.model_validate_json(t.model_dump_json()) != t for t in tiers[:500])
rt_fail += sum(UnitItemStats.model_validate_json(r.model_dump_json()) != r for r in uis[:2000])
out["loaded"] = {"comps": len(comps), "augment_tiers": len(tiers), "unit_item_stats": len(uis),
                 "unit_build_stats": len(ubs), "item_stats": len(doc.get("item_stats", [])), "unit_stats": len(us),
                 "holds_multi_item_rows": sum(len(r.item_ids) != 1 for r in uis)}
out["roundtrip_mismatch"] = rt_fail

# 2. 필드 채움
fb = [u for c in comps for u in c.final_board]
out["fill"] = {
    "carry_none": [c.comp_id for c in comps if c.carry is None],
    "carry_not_in_final_board": [c.comp_id for c in comps if c.carry and c.carry not in {u.id for u in c.final_board}],
    "carry_role_not_carry": [c.comp_id for c in comps
                             if c.carry and next((u.role for u in c.final_board if u.id == c.carry), None) != "carry"],
    "carry_not_is_core": [c.comp_id for c in comps
                          if c.carry and not next((u.is_core for u in c.final_board if u.id == c.carry), False)],
    "carry_bis_empty": [c.comp_id for c in comps if not c.carry_bis_items],
    "carry_bis_neq_carry_unit_items": [c.comp_id for c in comps
                                       if c.carry and next((u.items for u in c.final_board if u.id == c.carry), None) != c.carry_bis_items],
    "role_counts": dict(Counter(str(u.role) for u in fb)),
    "role_carry_per_comp": dict(Counter(sum(u.role == "carry" for u in c.final_board) for c in comps)),
    "is_core_true": sum(u.is_core for u in fb), "final_board_units": len(fb),
    "comps_with_zero_core": [c.comp_id for c in comps if not any(u.is_core for u in c.final_board)],
    "item_usage_empty": [c.comp_id for c in comps if not c.item_usage],
    "item_usage_max": max(x for c in comps for x in c.item_usage.values()),
    "item_usage_gt1": sum(x > 1 for c in comps for x in c.item_usage.values()),
    "buildup_empty": [c.comp_id for c in comps if not c.buildup],
    "buildup_levels": dict(Counter(lv for c in comps for lv in c.buildup)),
    "level_timing_empty": [c.comp_id for c in comps if not c.level_timing],
    "item_conditional_empty": [c.comp_id for c in comps if not c.item_conditional],
    "levelling": dict(Counter(c.levelling for c in comps)),
    "key_traits_empty": [c.comp_id for c in comps if not c.key_traits],
    "games_lt_1000": sorted(c.games for c in comps if (c.games or 0) < 1000),
    "top4_set_on_comp": sum(c.top4 is not None for c in comps),
    "buildup_top4_range": [min(b.top4 for c in comps for bs in c.buildup.values() for b in bs if b.top4 is not None),
                           max(b.top4 for c in comps for bs in c.buildup.values() for b in bs if b.top4 is not None)],
    "buildup_top4_none_by_level": dict(Counter(b.level for c in comps for bs in c.buildup.values() for b in bs if b.top4 is None)),
}
# 3. comp_id 일관성
ids = [c.comp_id for c in comps]
idset = set(ids)
out["comp_id"] = {
    "unique": len(ids) == len(idset),
    "comp_ids_map_values_eq": set(doc["comp_ids"].values()) == idset,
    "comp_ids_map_keys_eq_source_cluster": set(doc["comp_ids"]) == {c.source_cluster_id for c in comps},
    "map_consistent": all(doc["comp_ids"][c.source_cluster_id] == c.comp_id for c in comps),
    "augment_tier_comp_ids_unknown": sorted({t.comp_id for t in tiers if t.comp_id} - idset),
    "augment_tier_comps": len({t.comp_id for t in tiers if t.comp_id}),
    "augment_tier_global_rows": sum(t.comp_id is None for t in tiers),
    "unit_item_comp_ids_unknown": sorted({r.comp_id for r in uis} - idset),
    "unit_item_comp_none": sum(r.comp_id is None for r in uis),
    "unit_item_units_not_in_comp_board": 0,
}
board_units = {c.comp_id: {u.id for u in c.final_board} for c in comps}
all_units = {c.comp_id: board_units[c.comp_id] | {x for bs in c.buildup.values() for b in bs for x in b.units} for c in comps}
out["comp_id"]["unit_item_units_not_in_comp_board"] = sum(r.unit_id not in board_units[r.comp_id] for r in uis)
out["comp_id"]["unit_item_units_not_in_comp_any_board"] = sum(r.unit_id not in all_units[r.comp_id] for r in uis)
out["comp_id"]["unit_item_place_change_none"] = sum(r.place_change is None for r in uis)
# 설계 6.3: holder(carry) 의 place_change 가 있는가
out["comp_id"]["carry_has_unit_item_rows"] = sum(
    any(r.comp_id == c.comp_id and r.unit_id == c.carry for r in uis) for c in comps if c.carry)

# 4. 정적 대조: 아이템 composition (items_ready craftable 판정용)
def comp_of(i):
    r = st.get("items", i) or {}
    return r.get("composition") or []
bis = {i for c in comps for i in c.carry_bis_items}
out["bis_items"] = {
    "n": len(bis),
    "no_composition": sorted(i for i in bis if len(comp_of(i)) != 2),
    "categories": dict(Counter((st.get("items", i) or {}).get("category", "MISSING") for i in bis)),
}
# key_traits: unique trait 포함 여부 (A(c)/상징 규칙 영향)
kt = Counter(t.id for c in comps for t in c.key_traits)
out["key_traits_unique_like"] = sorted(k for k in kt if "Unique" in k)
# Eclipse
ecl = st.get("traits", "DA_18_Eclipse")
out["eclipse"] = {k: ecl.get(k) for k in ("breakpoints", "unit_less")} if ecl else None

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
