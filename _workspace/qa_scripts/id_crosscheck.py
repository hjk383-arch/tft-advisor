"""Phase 2 QA: ID 체계 전수 대조 (static_data <-> MetaTFT 캐시 <-> fixtures <-> unmapped.json).

실행: .venv\\Scripts\\python _workspace/qa_scripts/id_crosscheck.py
네트워크 호출 없음. 결과는 stdout(JSON 요약).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tft_advisor.contracts import _ID_PATTERN  # noqa: E402
from tft_advisor.fixtures import load_expected  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402

RAW = max((ROOT / "data" / "raw" / "metatft").glob("????-??-??"))  # 최신 날짜 캐시
st = load_static(18)
idre = re.compile(_ID_PATTERN)
out: dict = {}


def j(name):
    return json.loads((RAW / name).read_text(encoding="utf-8"))


def trait_base(t: str) -> str:
    return re.sub(r"_\d+$", "", t.strip())


unmapped = json.loads((ROOT / "data/static/18/unmapped.json").read_text(encoding="utf-8"))
declared_unmapped = (
    set(unmapped["units"]["metatft"]) | set(unmapped["units"]["summons_in_boards"])
    | set(unmapped["items"]["tactics_tools"]) | set(unmapped["augments"]["metatft_augments_tiers"])
)

# ---- 1. 정적 데이터 내부
shop_champs = [r for r in st.tables["champions"] if r.get("shop_pool")]
by_ko = defaultdict(list)
for r in shop_champs:
    by_ko[r["name_ko"]].append(r["apiName"])
out["shop_pool_dup_name_ko"] = {k: v for k, v in by_ko.items() if len(v) > 1}
out["static_ids_violating_CanonicalId_pattern"] = [
    r["apiName"] for k in st.KINDS for r in st.tables[k] if not idre.match(r["apiName"])
]

# ---- 2. MetaTFT ID 수집
units_json = [r["unit"] for r in j("units.json")["results"]]
comps = j("comps_data.json")["results"]["data"]["cluster_details"]
comp_units, comp_traits, comp_items = set(), set(), set()
name_types = set()
for c in comps.values():
    comp_units |= {u.strip() for u in c["units_string"].split(",") if u.strip()}
    comp_traits |= {trait_base(t) for t in c["traits_string"].split(",") if t.strip()}
    for b in c["builds"]:
        comp_units.add(b["unit"])
        comp_items |= set(b["buildName"])
    comp_items |= set(c["build_items"].keys())
    for n in c["name"]:
        name_types.add(n["type"])

det_units, det_items, det_traits = set(), set(), set()
DET_FILES = sorted(RAW.glob("comp_details_*.json"))  # 2026-09-22: 57개 전부
out["comp_details_files"] = len(DET_FILES)
for _f in DET_FILES:
    det = json.loads(_f.read_text(encoding="utf-8"))["results"]
    for boards in det["early_options"].values():
        for b in boards:
            det_units |= set(b["unit_list"].split("&"))
    for boards in det["options"].values():
        for b in boards:
            det_units |= set(b["units_list"].split("&"))
            det_traits |= {trait_base(t) for t in b.get("traits_list", "").split("&") if t}
    for it in det["itemNames"]:
        det_items.add(it["itemNames"])
        for u in it.get("units", []):
            det_units.add(u["units"])
    for u in det["unit_stats"]:
        det_units.add(u["unit"])
    for t in det["traits"]:
        det_traits.add(trait_base(t["trait"]))
    for b in det["builds"]:
        det_units.add(b["unit"])
        det_items |= set(b["buildName"])

opt_units = set()


def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("units_list", "unit_list") and isinstance(v, str):
                opt_units.update(v.split("&"))
            else:
                walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)


if (RAW / "comp_options.json").is_file():  # 09-22 캐시에는 없음
    walk(j("comp_options.json"))

aug_tiers = j("augments_tiers.json")["content"]["content"]["tierList"]
aug_tier_ids = {x["id"] for t in aug_tiers for x in t["content"]}
cat = j("comp_augment_tiers.json")["results"]
cat_ids = {a["id"] for v in cat.values() for a in v["augments"]}


def check(label, ids, kinds):
    ids = {i for i in ids if i}
    missing = sorted(i for i in ids if not any(st.get(k, i) for k in kinds))
    not_pool = []
    if "champions" in kinds:
        not_pool = sorted(i for i in ids if (r := st.get("champions", i)) and not r.get("shop_pool"))
    out[label] = {
        "n": len(ids),
        "missing_in_static": missing,
        "missing_not_declared_in_unmapped": sorted(set(missing) - declared_unmapped),
        "not_shop_pool": not_pool,
        "pattern_violations": sorted(i for i in ids if not idre.match(i)),
    }


check("metatft_units.json", units_json, ["champions"])
check("metatft_comps_data_units", comp_units, ["champions"])
check("metatft_comps_data_traits", comp_traits, ["traits"])
check("metatft_comps_data_items", comp_items, ["items"])
check("metatft_comp_details_units", det_units, ["champions"])
check("metatft_comp_details_items", det_items, ["items"])
check("metatft_comp_details_traits", det_traits, ["traits"])
check("metatft_comp_options_units", opt_units, ["champions"])
check("metatft_augments_tiers", aug_tier_ids, ["augments"])
check("metatft_comp_augment_tiers", cat_ids, ["augments"])

out["comps_name_entry_types"] = sorted(name_types)
out["augments_tiers_labels_sizes"] = [(t["label"], len(t["content"])) for t in aug_tiers]
out["augments_tiers_types"] = dict(Counter(x["type"] for t in aug_tiers for x in t["content"]))
out["comp_augment_tiers_values"] = dict(Counter(a["tier"] for v in cat.values() for a in v["augments"]))
out["comp_augment_tiers_n"] = len(cat)
out["comps_n"] = len(comps)
out["comps_games_keys_n"] = len(j("comps_data.json")["results"]["games"])
out["comp_augment_keys_not_in_comps"] = sorted(set(cat) - set(comps))
out["aug_tier_ids_non_DA"] = sorted(i for i in aug_tier_ids | cat_ids if not i.startswith("DA_"))
out["stats_item_categories"] = dict(Counter(
    (st.get("items", i) or {}).get("category", "MISSING") for i in comp_items | det_items))

# ---- 3. unmapped.json 선언 vs 실제
actual_missing_units = set()
for k in ("metatft_units.json", "metatft_comps_data_units", "metatft_comp_details_units", "metatft_comp_options_units"):
    actual_missing_units |= set(out[k]["missing_in_static"])
declared_units = set(unmapped["units"]["metatft"]) | set(unmapped["units"]["summons_in_boards"])
out["unmapped_units"] = {
    "declared_not_observed": sorted(declared_units - actual_missing_units),
    "observed_not_declared": sorted(actual_missing_units - declared_units),
}
aug_missing = set(out["metatft_augments_tiers"]["missing_in_static"]) | set(out["metatft_comp_augment_tiers"]["missing_in_static"])
declared_aug = set(unmapped["augments"]["metatft_augments_tiers"])
out["unmapped_augments"] = {
    "declared_not_observed": sorted(declared_aug - aug_missing),
    "observed_not_declared": sorted(aug_missing - declared_aug),
}

# ---- 3b. 변환 산출물 data/stats/metatft_*.json ID 전수
conv_path = max((ROOT / "data/stats").glob("metatft_*.json"))
conv = json.loads(conv_path.read_text(encoding="utf-8"))
cu, ci, ct, ca = set(), set(), set(), set()
for c in conv["comps"]:
    for u in c["final_board"]:
        cu.add(u["id"]); ci |= set(u["items"])
    if c.get("carry"): cu.add(c["carry"])
    ci |= set(c["carry_bis_items"]) | set(c["item_conditional"]) | set(c["item_usage"])
    ct |= {t["id"] for t in c["key_traits"]}
    for boards in c["buildup"].values():
        for b in boards: cu |= set(b["units"])
for a in conv["augment_tiers"]:
    ca.add(a["augment_id"])
for r in conv["unit_item_stats"]:
    cu.add(r["unit_id"]); ci |= set(r["item_ids"])
for r in conv["unit_stats"]:
    cu.add(r["unit_id"])
out["converted_file"] = conv_path.name
check("converted_units", cu, ["champions"])
check("converted_items", ci, ["items"])
check("converted_traits", ct, ["traits"])
check("converted_augments", ca, ["augments"])

# ---- 4. fixtures: 이름 -> ID 전수
all_meta_units = set(units_json) | comp_units | det_units | opt_units
fx = {}
for p in sorted((ROOT / "tests/fixtures/screens").glob("*.expected.json")):
    raw = json.loads(p.read_text(encoding="utf-8"))
    try:
        s = load_expected(p).state
    except Exception as e:  # noqa: BLE001
        fx[p.name] = {"error": repr(e)}
        continue
    rows = []
    for i, (slot, rs) in enumerate(zip(s.shop or [], raw.get("shop") or [])):
        name = (rs or {}).get("name") or (rs or {}).get("special")
        row = {"slot": i, "name": name, "kind": str(slot.kind), "id": slot.id}
        if slot.kind == "champion":
            rec = st.get("champions", slot.id)
            row["static_cost"] = rec["cost"]
            row["label_cost"] = rs.get("cost")
            row["name_ambiguous"] = by_ko.get(name, [])
            row["in_metatft"] = slot.id in all_meta_units
        rows.append(row)
    for a in s.augment_offer or []:
        rows.append({"augment": a.name_ko, "id": a.id, "rarity": a.rarity,
                     "in_augments_tiers": a.id in aug_tier_ids, "in_comp_augment_tiers": a.id in cat_ids})
    fx[p.name] = rows
out["fixtures"] = fx

print(json.dumps(out, ensure_ascii=False, indent=1))
