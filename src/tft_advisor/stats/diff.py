"""스냅샷 간 변경 목록(패치 업데이트 흐름 1단계의 `_workspace/patch_{ver}_diff.md`).

입력은 `metatft_convert.dump` 형식 dict 두 개(이전/새). 네트워크·파일 I/O 없음(렌더링 결과만 반환).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AVG_DELTA = 0.10   # 평균 등수 변화 보고 기준(±)


def _by(rows: list[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    return {r[key]: r for r in rows}


def _board(c: Mapping[str, Any]) -> list[str]:
    return sorted(u["id"] for u in c.get("final_board", []))


def diff_docs(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """두 스냅샷 비교. old=None이면 전부 '추가'이고 `has_baseline`=False, `patch_changed`=None(판단 불가)."""
    has_baseline = old is not None
    old = old or {}
    ro, rn = old.get("report") or {}, new.get("report") or {}
    oc, nc = _by(old.get("comps", []), "comp_id"), _by(new.get("comps", []), "comp_id")
    comps_changed = []
    for cid in sorted(oc.keys() & nc.keys()):
        a, b = oc[cid], nc[cid]
        ch: dict[str, Any] = {}
        if a.get("avg_place") is not None and b.get("avg_place") is not None \
                and abs(b["avg_place"] - a["avg_place"]) >= AVG_DELTA:
            ch["avg_place"] = [a["avg_place"], b["avg_place"]]
        for k in ("carry", "carry_bis_items", "levelling"):
            if a.get(k) != b.get(k):
                ch[k] = [a.get(k), b.get(k)]
        if _board(a) != _board(b):
            ch["final_board"] = {"-": sorted(set(_board(a)) - set(_board(b))),
                                 "+": sorted(set(_board(b)) - set(_board(a)))}
        if ch:
            comps_changed.append({"comp_id": cid, "name": b.get("name"), **ch})

    def tiers(doc: Mapping[str, Any]) -> dict[str, str]:
        return {t["augment_id"]: t["tier"] for t in doc.get("augment_tiers", []) if not t.get("comp_id")}

    ot, nt = tiers(old), tiers(new)

    def comp_tiers(doc: Mapping[str, Any]) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for t in doc.get("augment_tiers", []):
            if t.get("comp_id"):
                out.setdefault(t["comp_id"], {})[t["augment_id"]] = t["tier"]
        return out

    oct_, nct = comp_tiers(old), comp_tiers(new)
    by_comp = []   # 양쪽에 모두 있는 덱만(덱 추가·제거는 comps에서 보고)
    for cid in sorted(oct_.keys() & nct.keys()):
        a, b = oct_[cid], nct[cid]
        e = {"added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys()),
             "changed": {x: [a[x], b[x]] for x in sorted(a.keys() & b.keys()) if a[x] != b[x]}}
        if e["added"] or e["removed"] or e["changed"]:
            by_comp.append({"comp_id": cid, "name": (nc.get(cid) or {}).get("name"), **e})

    def placement_moves(key: str, idkey: str) -> list[dict[str, Any]]:
        a, b = _by(old.get(key, []), idkey), _by(new.get(key, []), idkey)
        out = []
        for k in sorted(a.keys() & b.keys()):
            x, y = a[k].get("avg_place"), b[k].get("avg_place")
            if x is not None and y is not None and abs(y - x) >= AVG_DELTA:
                out.append({"id": k, "avg_place": [round(x, 3), round(y, 3)]})
        return out

    return {
        "patch": [ro.get("patch"), rn.get("patch")],
        "fetched_at": [ro.get("fetched_at"), rn.get("fetched_at")],
        "has_baseline": has_baseline,
        "patch_changed": (ro.get("patch") != rn.get("patch")) if has_baseline else None,
        "comps": {
            "added": [{"comp_id": c, "name": nc[c].get("name"), "games": nc[c].get("games"),
                       "avg_place": nc[c].get("avg_place")} for c in sorted(nc.keys() - oc.keys())],
            "removed": [{"comp_id": c, "name": oc[c].get("name")} for c in sorted(oc.keys() - nc.keys())],
            "changed": comps_changed,
        },
        "augment_tiers": {
            "added": sorted(nt.keys() - ot.keys()),
            "removed": sorted(ot.keys() - nt.keys()),
            "changed": {a: [ot[a], nt[a]] for a in sorted(ot.keys() & nt.keys()) if ot[a] != nt[a]},
            "by_comp": by_comp,
        },
        "unit_stats": placement_moves("unit_stats", "unit_id"),
        "item_stats": placement_moves("item_stats", "item_id"),
        "unmapped": {"ids": rn.get("unmapped_ids", []), "augments": rn.get("unmapped_augments", []),
                     "new_ids": sorted(set(rn.get("unmapped_ids", [])) - set(ro.get("unmapped_ids", []))),
                     "new_augments": sorted(set(rn.get("unmapped_augments", []))
                                            - set(ro.get("unmapped_augments", [])))},
    }


def render_markdown(d: Mapping[str, Any]) -> str:
    """diff_docs 결과 → 사람이 읽는 변경 목록."""
    po, pn = d["patch"]
    lines = [f"# 통계 변경 목록: {po or '(없음)'} → {pn}", "",
             f"- 수집 시각: {d['fetched_at'][0]} → {d['fetched_at'][1]}",
             "- 패치 변경: " + ("(비교 대상 없음 — 이전 스냅샷이 없어 전부 '추가'로 표시)"
                               if not d.get("has_baseline", True) else ('예' if d['patch_changed'] else '아니오')),
             f"- 평균 등수 보고 기준: ±{AVG_DELTA}", ""]
    c = d["comps"]
    lines += [f"## 덱 (추가 {len(c['added'])} / 제거 {len(c['removed'])} / 변경 {len(c['changed'])})", ""]
    for x in c["added"]:
        lines.append(f"- 추가 `{x['comp_id']}` {x['name']} — {x['games']}판, 평균 {x['avg_place']}")
    for x in c["removed"]:
        lines.append(f"- 제거 `{x['comp_id']}` {x['name']}")
    for x in c["changed"]:
        parts = [f"{k}: {v[0]} → {v[1]}" for k, v in x.items() if k not in ("comp_id", "name", "final_board")]
        if "final_board" in x:
            parts.append(f"보드 -{x['final_board']['-']} +{x['final_board']['+']}")
        lines.append(f"- 변경 `{x['comp_id']}` {x['name']}: " + "; ".join(parts))
    a = d["augment_tiers"]
    lines += ["", f"## 증강 전체 등급 (추가 {len(a['added'])} / 제거 {len(a['removed'])} / 변경 {len(a['changed'])})", ""]
    lines += [f"- 추가 `{x}`" for x in a["added"]] + [f"- 제거 `{x}`" for x in a["removed"]]
    lines += [f"- `{k}`: {v[0]} → {v[1]}" for k, v in a["changed"].items()]
    bc = a.get("by_comp", [])
    lines += ["", f"## 덱별 증강 등급 (변경된 덱 {len(bc)})", ""]
    for x in bc:
        parts = [f"`{k}` {v[0]}→{v[1]}" for k, v in x["changed"].items()]
        parts += [f"+`{k}`" for k in x["added"]] + [f"-`{k}`" for k in x["removed"]]
        lines.append(f"- `{x['comp_id']}` {x['name']}: " + ", ".join(parts))
    for key, title in (("unit_stats", "유닛"), ("item_stats", "아이템")):
        lines += ["", f"## {title} 평균 등수 변화 ({len(d[key])})", ""]
        lines += [f"- `{x['id']}`: {x['avg_place'][0]} → {x['avg_place'][1]}" for x in d[key]]
    u = d["unmapped"]
    lines += ["", "## 미매핑 ID", "", f"- 아이템/유닛: {u['ids']} (신규 {u['new_ids']})",
              f"- 증강: {u['augments']} (신규 {u['new_augments']})",
              "- 신규 미매핑이 있으면 정적 데이터(CDragon) 갱신: `python -m tft_advisor.stats.static_extract`", ""]
    return "\n".join(lines)
