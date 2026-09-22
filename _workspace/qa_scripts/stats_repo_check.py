"""Phase 3 stats QA: StatsRepository ↔ 원본 MetaTFT ↔ JSON ↔ SQLite ↔ 설계 요구.

실행: .venv/bin/python _workspace/qa_scripts/stats_repo_check.py
네트워크 없음. DB는 읽기만 한다(보존 개수 검증은 임시 DB). 결과는 stdout(JSON 요약).
"""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

t0 = time.perf_counter()
from tft_advisor.stats import db as statsdb  # noqa: E402
from tft_advisor.stats.collectors.metatft import latest_raw_dir  # noqa: E402
from tft_advisor.stats.repository import (  # noqa: E402
    InMemoryStatsRepository, StatsRepository, default_db_path, latest_json, open_repository)

t_import = time.perf_counter() - t0
out: dict = {}

# ------------------------------------------------------------------ 0. 로드 시간
t = time.perf_counter()
repo = open_repository()
t_load = time.perf_counter() - t
t = time.perf_counter()
repo_lazy = InMemoryStatsRepository.from_doc(statsdb.read_snapshot(default_db_path(), repo.meta.snapshot_id),
                                             repo.static, preload=False)
t_lazy = time.perf_counter() - t
jpath = latest_json()
t = time.perf_counter()
repo_js = InMemoryStatsRepository.from_json(jpath, repo.static)
t_json = time.perf_counter() - t
doc = json.loads(jpath.read_text(encoding="utf-8"))
out["load_s"] = {"import": round(t_import, 3), "open_repository(sqlite,preload)": round(t_load, 3),
                 "sqlite_preload_false": round(t_lazy, 3), "from_json": round(t_json, 3)}

# ------------------------------------------------------------------ 1. Protocol 완전성
proto = [n for n, v in vars(StatsRepository).items() if not n.startswith("_") and callable(v)]
missing_impl = [n for n in proto if not callable(getattr(repo, n, None))]
sig_mismatch = []
for n in proto:
    ps, pi = inspect.signature(getattr(StatsRepository, n)), inspect.signature(getattr(type(repo), n))
    if list(ps.parameters) != list(pi.parameters):
        sig_mismatch.append([n, str(ps), str(pi)])
try:
    from tft_advisor.advisor.stats_source import AdvisorStats
    adv_ok = isinstance(repo, AdvisorStats)
    adv_missing = [n for n, v in vars(AdvisorStats).items()
                   if not n.startswith("_") and callable(v) and not hasattr(repo, n)]
except Exception as e:  # noqa: BLE001 - advisor 작업 중
    adv_ok, adv_missing = repr(e), None
out["protocol"] = {"methods": len(proto), "missing_impl": missing_impl, "signature_mismatch": sig_mismatch,
                   "isinstance_AdvisorStats": adv_ok, "advisor_methods_missing": adv_missing}

# ------------------------------------------------------------------ 2. SQLite ↔ JSON (모델 수준)
def dump_all(r: InMemoryStatsRepository) -> dict:
    comps = r.comps()
    units = sorted({x["unit_id"] for x in doc["unit_item_stats"]} | {x["unit_id"] for x in doc["unit_build_stats"]})
    scopes = [None] + [c.comp_id for c in comps]
    return {
        "comps": [c.model_dump(mode="json") for c in comps],
        "aug": {str(s): {k: v.model_dump(mode="json") for k, v in r.augment_tiers(s).items()} for s in scopes},
        "units": {k: v.model_dump(mode="json") for k, v in r.all_unit_stats().items()},
        "items": {i: (r.item_stats(i).model_dump(mode="json") if r.item_stats(i) else None)
                  for i in sorted({x["item_id"] for x in doc["item_stats"]})},
        "holds": {f"{u}|{s}": [x.model_dump(mode="json") for x in r.unit_item_stats(u, s)]
                  for u in units for s in scopes},
        "builds": {f"{u}|{s}": [x.model_dump(mode="json") for x in r.unit_builds(u, s)]
                   for u in units for s in scopes[1:]},
    }


a, b = dump_all(repo), dump_all(repo_js)
out["sqlite_vs_json"] = {k: ("equal" if a[k] == b[k] else "DIFF") for k in a}
out["sqlite_vs_json"]["meta"] = {
    "sqlite": [repo.meta.patch, str(repo.meta.fetched_at), repo.meta.rank_filter_units, repo.meta.snapshot_id],
    "json": [repo_js.meta.patch, str(repo_js.meta.fetched_at), repo_js.meta.rank_filter_units, repo_js.meta.snapshot_id]}
out["counts"] = repo.meta.counts
out["counts_json_file"] = {k: len(v) for k, v in doc.items() if isinstance(v, list)}

# ------------------------------------------------------------------ 3. 원본 MetaTFT 스팟 체크(R16)
raw = latest_raw_dir()
by_cluster = {c.source_cluster_id: c.comp_id for c in repo.comps()}
hold_fail, build_fail, raw_hold_rows, raw_build_rows = [], [], 0, 0
raw_hold_dupes, raw_build_dupes, build_skipped = 0, 0, Counter()
checked = 0
for p in sorted(raw.glob("comp_details_*.json")):
    det = json.loads(p.read_text(encoding="utf-8"))["results"]
    cid = by_cluster.get(str(det["cluster"]))
    if cid is None:
        continue
    checked += 1
    seen = Counter()
    for it in det.get("itemNames") or []:
        for u in it.get("units") or []:
            raw_hold_rows += 1
            seen[(u["units"], it["itemNames"])] += 1
            r = repo.unit_item_stat(u["units"], it["itemNames"], cid)
            if r is None or r.games != u.get("count") or r.avg_place != u.get("avg") \
                    or r.place_change != u.get("place_change") or r.comp_id != cid:
                hold_fail.append([cid, u["units"], it["itemNames"], u.get("count"), r and r.games])
    raw_hold_dupes += sum(n - 1 for n in seen.values() if n > 1)
    bseen = Counter()
    for bd in det.get("builds") or []:
        items = list(bd.get("buildName") or [])
        if not items or len(items) > 3 or not bd.get("unit"):
            build_skipped["empty" if not items else ">3" if len(items) > 3 else "no_unit"] += 1
            continue
        raw_build_rows += 1
        bseen[(bd["unit"], tuple(items))] += 1
        rows = [x for x in repo.unit_builds(bd["unit"], cid) if x.item_ids == items and x.games == bd.get("count")]
        if not rows or rows[0].place_change != bd.get("place_change") or rows[0].avg_place != bd.get("avg"):
            build_fail.append([cid, bd["unit"], items, bd.get("count")])
    raw_build_dupes += sum(n - 1 for n in bseen.values() if n > 1)
out["raw_spotcheck"] = {"comps_checked": checked, "raw_hold_rows": raw_hold_rows,
                        "repo_hold_rows": repo.meta.counts["unit_item_stats"], "hold_mismatch": hold_fail[:5],
                        "hold_mismatch_n": len(hold_fail), "raw_hold_dupe_keys": raw_hold_dupes,
                        "raw_build_rows": raw_build_rows, "repo_build_rows": repo.meta.counts["unit_build_stats"],
                        "build_mismatch_n": len(build_fail), "build_mismatch": build_fail[:5],
                        "raw_build_dupe_keys": raw_build_dupes, "build_skipped": dict(build_skipped)}

# JSON holds 키 중복(분리 전 4,980건) / holds에 다중 아이템 행
hk = Counter((r["unit_id"], r.get("comp_id"), tuple(r["item_ids"])) for r in doc["unit_item_stats"])
out["holds_shape"] = {"dupe_keys": sum(1 for n in hk.values() if n > 1),
                      "multi_item_rows": sum(len(r["item_ids"]) != 1 for r in doc["unit_item_stats"]),
                      "comp_id_none_rows": sum(r.get("comp_id") is None for r in doc["unit_item_stats"]),
                      "builds_item_len": dict(Counter(len(r["item_ids"]) for r in doc["unit_build_stats"])),
                      "builds_comp_none": sum(r.get("comp_id") is None for r in doc["unit_build_stats"])}

# ------------------------------------------------------------------ 4. 전체(comp_id=None) 파생값 가중 평균
agg = defaultdict(list)
for r in doc["unit_item_stats"]:
    agg[(r["unit_id"], r["item_ids"][0])].append(r)
bad, n_checked, clipped = [], 0, 0
for (u, i), rows in agg.items():
    got = repo.unit_item_stat(u, i)
    g = sum(r.get("games") or 0 for r in rows)
    pw = [(r["place_change"], r["games"]) for r in rows if r.get("place_change") is not None and r.get("games")]
    aw = [(r["avg_place"], r["games"]) for r in rows if r.get("avg_place") is not None and r.get("games")]
    epc = round(sum(v * n for v, n in pw) / sum(n for _, n in pw), 4) if pw else None
    eap = round(sum(v * n for v, n in aw) / sum(n for _, n in aw), 4) if aw else None
    n_checked += 1
    if eap is not None and not 1 <= eap <= 8:
        clipped += 1
    ok = got is not None and got.comp_id is None and got.games == g and got.place_change == epc \
        and (eap is None or abs(got.avg_place - min(8, max(1, eap))) < 1e-9)
    if not ok:
        bad.append([u, i, g, got and got.games, epc, got and got.place_change])
# fallback 의미
c0 = repo.comps()[0]
fb_probe = None
for (u, i) in agg:
    if repo.unit_item_stat(u, i, c0.comp_id) is None:
        fb_probe = {"unit": u, "item": i, "comp": c0.comp_id,
                    "exact": None, "fallback_games": repo.unit_item_stat(u, i, c0.comp_id, fallback_overall=True).games}
        break
out["overall_derived"] = {"pairs": n_checked, "mismatch_n": len(bad), "mismatch": bad[:5], "avg_out_of_range": clipped,
                          "fallback_probe": fb_probe,
                          "rank_filter_of_rows": dict(Counter(str(r.get("rank_filter")) for r in doc["unit_item_stats"]))}

# ------------------------------------------------------------------ 5. is_craftable / item_category
items = repo.static.tables["items"]
cat_craft = defaultdict(Counter)
for r in items:
    cat_craft[str(r.get("category"))][repo.is_craftable(r["apiName"])] += 1
da_native = [r for r in items if r["apiName"].startswith("DA_") and r.get("set_native")]
emblems = [r["apiName"] for r in da_native if r.get("category") == "emblem"]
bis = sorted({x for c in repo.comps() for x in c.carry_bis_items})
bis_nc = {x: repo.item_category(x) for x in bis if not repo.is_craftable(x)}
all_comp_items = sorted({x for c in repo.comps() for u in c.final_board for x in u.items})
comps_ids = repo.components()
craft_results = {}
for ii, x in enumerate(comps_ids):
    for y in comps_ids[ii:]:
        z = repo.craft(x, y)
        if z:
            craft_results[(x, y)] = z
rt_bad = [[k, v, repo.recipe(v)] for k, v in craft_results.items() if repo.recipe(v) != tuple(sorted(k))]
non_da_recipes = sorted(k for k in repo._recipes if not k.startswith("DA_"))  # noqa: SLF001
out["items"] = {
    "category_x_craftable": {k: {str(kk): vv for kk, vv in v.items()} for k, v in sorted(cat_craft.items())},
    "components": len(comps_ids), "craft_pairs": len(craft_results),
    "craft_result_dupes": sum(n > 1 for n in Counter(craft_results.values()).values()),
    "craft_recipe_roundtrip_bad": rt_bad[:5],
    "component_is_craftable": [x for x in comps_ids if repo.is_craftable(x)],
    "component_is_component": all(repo.is_component(x) for x in comps_ids),
    "emblems_da_native": len(emblems),
    "emblems_craftable": sorted(e for e in emblems if repo.is_craftable(e)),
    "emblems_not_craftable": sorted(e for e in emblems if not repo.is_craftable(e)),
    "emblems_trait_unmapped": sorted(e for e in emblems if repo.emblem_trait(e) is None),
    "emblem_traits_not_in_static": sorted({repo.emblem_trait(e) for e in emblems if repo.emblem_trait(e)}
                                          - {t["apiName"] for t in repo.static.tables["traits"]}),
    "artifact_craftable": [r["apiName"] for r in items if r.get("category") == "artifact" and repo.is_craftable(r["apiName"])],
    "radiant_craftable": [r["apiName"] for r in items if r.get("category") == "radiant" and repo.is_craftable(r["apiName"])],
    "bis_unique": len(bis), "bis_not_craftable": bis_nc,
    "comps_with_noncraftable_bis": sum(any(not repo.is_craftable(x) for x in c.carry_bis_items) for c in repo.comps()),
    "bis_category_none": [x for x in bis if repo.item_category(x) is None],
    "final_board_items_category_none": [x for x in all_comp_items if repo.item_category(x) is None],
    "non_DA_items_with_recipe": len(non_da_recipes), "non_DA_recipe_sample": non_da_recipes[:3],
    "DA_non_native_recipe": sorted(k for k in repo._recipes if k.startswith("DA_")  # noqa: SLF001
                                   and not (repo.item(k) or {}).get("set_native"))[:5],
}

# ------------------------------------------------------------------ 6. 설계 조회 대상 ID 정합
kt = {t.id for c in repo.comps() for t in c.key_traits}
aug_tr = {t for a in repo.static.tables["augments"] for t in (a.get("associated_traits") or [])}
emb_tr = {repo.emblem_trait(e) for e in emblems} - {None}
fb_units = {u.id for c in repo.comps() for u in c.final_board}
bu_units = {x for c in repo.comps() for bs in c.buildup.values() for b in bs for x in b.units}
tiers_seen = Counter(t.tier for s in [None, *repo.comps_with_augment_tiers()] for t in repo.augment_tiers(s).values())
lt_min = Counter(min(c.level_timing) if c.level_timing else None for c in repo.comps())
out["design_ids"] = {
    "key_traits_not_in_static": sorted(kt - {t["apiName"] for t in repo.static.tables["traits"]}),
    "emblem_traits_in_key_traits": len(emb_tr & kt), "emblem_traits_total": len(emb_tr),
    "augment_traits_in_key_traits": len(aug_tr & kt), "augment_traits_total": len(aug_tr),
    "final_board_non_champion": sorted(u for u in fb_units if not repo.is_champion(u)),
    "buildup_non_champion": sorted(u for u in bu_units if not repo.is_champion(u)),
    "carry_x_bis_comp_rows_missing": [c.comp_id for c in repo.comps() for x in c.carry_bis_items
                                      if c.carry and repo.unit_item_stat(c.carry, x, c.comp_id) is None],
    "tiers_seen": dict(tiers_seen),
    "comps_with_comp_tiers": len(repo.comps_with_augment_tiers()),
    "level_timing_min_key": {str(k): v for k, v in lt_min.items()},
    "shop_odds_levels": [lv for lv in range(1, 11) if sum(repo.shop_odds(lv)) == 100],
    "games_lt_1000": sorted(c.games for c in repo.comps() if (c.games or 0) < 1000),
}

# ------------------------------------------------------------------ 7. 조회 속도(실시간 루프 모사)
comps = repo.comps(min_games=1000)
t = time.perf_counter()
N = 20
for _ in range(N):
    for c in repo.comps(min_games=1000):
        for u in c.final_board:
            repo.champion_cost(u.id); repo.is_champion(u.id)
            for x in u.items:
                repo.unit_item_stat(u.id, x, c.comp_id, fallback_overall=True); repo.recipe(x); repo.is_craftable(x)
        for x in c.carry_bis_items:
            repo.item_category(x); repo.emblem_trait(x)
        for a in list(repo.augment_tiers())[:3]:
            repo.augment_tier_for(a, c.comp_id); repo.augment_traits(a)
    for lv in range(1, 11):
        repo.shop_odds(lv)
t_loop = (time.perf_counter() - t) / N
t = time.perf_counter()
for _ in range(10000):
    repo.unit_item_stat("DA_18_Zyra", "DA_ArchangelsStaff", comps[0].comp_id)
t_one = (time.perf_counter() - t) / 10000
t = time.perf_counter()
for u in list(repo_lazy._raw_holds)[:20]:  # noqa: SLF001 - lazy 첫 조회 비용
    repo_lazy.unit_item_stats(u)
t_lazy_first = (time.perf_counter() - t) / 20
out["lookup"] = {"full_pass_ms(57comps boards+items+aug)": round(t_loop * 1000, 3),
                 "unit_item_stat_us": round(t_one * 1e6, 3),
                 "lazy_first_unit_ms": round(t_lazy_first * 1000, 3), "comps_after_min_games": len(comps)}

# ------------------------------------------------------------------ 8. 보존 개수(임시 DB)
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "s.sqlite"
    small = {"report": dict(doc["report"]), "comps": doc["comps"][:2], "augment_tiers": doc["augment_tiers"][:5],
             "unit_stats": doc["unit_stats"][:2], "unit_item_stats": doc["unit_item_stats"][:10],
             "unit_build_stats": doc["unit_build_stats"][:10], "item_stats": doc["item_stats"][:3]}
    ids = []
    for k in range(7):
        small["report"]["fetched_at"] = f"2026-09-{10 + k:02d}T00:00:00+00:00"
        ids.append(statsdb.write_snapshot(p, small, keep=5))
    left = [s.id for s in statsdb.list_snapshots(p)]
    small["report"]["fetched_at"] = "2026-09-16T00:00:00+00:00"   # 같은 fetched_at 재적재 = 교체
    rid = statsdb.write_snapshot(p, small, keep=5)
    left2 = [s.id for s in statsdb.list_snapshots(p)]
    import sqlite3
    con = sqlite3.connect(p)
    orphans = {tbl: con.execute(f"SELECT count(*) FROM {tbl} WHERE snapshot_id NOT IN (SELECT id FROM snapshots)").fetchone()[0]
               for tbl in ("comps", "augment_tiers", "unit_stats", "unit_items", "item_stats")}
    con.close()
    latest = statsdb.find_snapshot(p)
out["retention"] = {"written": ids, "kept": left, "after_replace": left2, "replace_id": rid,
                    "orphan_rows": orphans, "find_latest": latest.id if latest else None}

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
