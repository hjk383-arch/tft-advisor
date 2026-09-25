"""MetaTFT Early Comps 원본(`collectors.metatft_early`) → 스테이지별 보드·유닛 통계 dict.

산출물(`dump` → data/stats/stage_boards_{patch}.json, DB 적재는 `db.write_snapshot(source="metatft_early")`):

- stage_baseline[]    {stage, avg_place, win_rate, round_win_rate, games, rounds}
- stage_boards[]      {stage, kind, cluster, units[], avg_place, win_rate, round_win_rate, avg_hp_delta, avg_hp,
                       games, rounds, share}
      kind "cluster"   = 그 스테이지의 보드 클러스터(대표 유닛 = MetaTFT name). 성적은 latest_stats(현재 패치).
      kind "variation" = 클러스터 안의 **정확한 보드**(comps_full.variations). 성적은 전체 기간(stats) — 소스가
                         variation별 latest를 주지 않는다. 표본 < VARIATION_MIN_GAMES는 버린다.
- unit_stage_stats[]  {unit_id, stage, star(None=전체|1|2|3), avg_place, win_rate, round_win_rate, avg_hp_delta,
                       games, rounds, pick_rate}
      그 스테이지에서 그 유닛을 **보드에 올린** 참가자-스테이지의 성적. 모든 클러스터의 comps_full.units 합
      (클러스터가 스테이지 표본을 정확히 분할함을 확인: 클러스터 matchup_count 합 == 스테이지 합).
      comps_full이 일부만 있으면 overview의 클러스터 핵심 유닛만 합산되고 report.unit_coverage="core_only".
- stage_transitions[] {stage, cluster, next_cluster, share, avg_place, win_rate, round_win_rate, games}
      이 스테이지 클러스터 → 다음 스테이지 클러스터로 간 비율과 그 경로의 최종 성적(comps_full.transition_to_stats).
- stage_round_sizes[] {stage, round, num_units, avg_place, win_rate, round_win_rate, games, rounds}
      라운드별 보드 인원수 성적(comps_full.num_units[n].rounds 합). 예: 3-2에 6명 vs 5명.

지표 의미(소스 필드 → 저장 필드)
- games = final_place_count(그 보드로 관측된 참가자-게임 수), rounds = matchup_count(그 보드로 치른 전투 수)
- avg_place = final_place_avg(최종 등수), win_rate = final_place_winrate(**1등 비율**), top4는 소스에 없다
- round_win_rate = matchup_winrate(그 스테이지 전투 승률), avg_hp_delta = matchup_avg_hp_delta
- 표본은 MetaTFT 데스크톱 앱 사용자(전 티어). 스테이지 기준선 avg_place가 4.5보다 낮다(4.44~) → 비교는 같은
  스테이지 기준선 대비 차이(`delta`)로 할 것.

ID: 소스는 `TFT18_Ahri` 형식이다. 정적 데이터 상점 풀 챔피언의 name_en으로 canonical ID(`DA_18_Ahri` 등)에 매핑한다
(`build_id_map`). 매핑 실패는 버리지 않고 report.unmapped_ids에 남긴다(해당 유닛이 든 보드 행은 그 유닛만 뺀다).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from tft_advisor.static_data import PROJECT_ROOT, StaticData, load_static
from tft_advisor.stats.collectors.metatft_early import STAGES, full_name, latest_early_dir

SOURCE = "metatft_early"
JSON_PREFIX = "stage_boards_"
"""JSON 파일 접두사. `metatft_`로 시작하면 안 된다 — `latest_snapshot(dir, "metatft_")`(본 통계 폴백·load·refresh
기준선)가 `metatft_early_18.3.json`을 "패치 early_18.3"으로 읽어 최신으로 고른다."""
VARIATION_MIN_GAMES = 30          # variation 행 최소 표본(games = final_place_count)
UNIT_MIN_ROUNDS = 1
ID_OVERRIDES: dict[str, str] = {}  # 이름 규칙으로 안 되는 예외(현재 없음: Pebbles/MamaBeak/Lux_Base도 name_en으로 맞음)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def build_id_map(static: StaticData) -> dict[str, str]:
    """정규화 이름 → canonical 챔피언 ID(상점 풀만). name_en과 apiName 핵심부 둘 다 키로 쓴다."""
    out: dict[str, str] = {}
    for r in static.tables["champions"]:
        if not r.get("shop_pool"):
            continue
        for key in (r.get("name_en") or "",):
            k = _norm(key)
            if k and k not in out:
                out[k] = r["apiName"]
    return out


def map_unit(src: str, id_map: Mapping[str, str], static: StaticData) -> str | None:
    """`TFT18_Lux_Coven` → `DA_18_Lux_Coven`. 이미 canonical이면 그대로. 실패 시 None."""
    if src in ID_OVERRIDES:
        return ID_OVERRIDES[src]
    rec = static.get("champions", src)
    if rec is not None and rec.get("shop_pool"):
        return src
    core = src.split("_", 1)[1] if src.upper().startswith("TFT") and "_" in src else src
    for cand in (core, core.split("_", 1)[0]):
        got = id_map.get(_norm(cand))
        if got:
            return got
    return None


def patch_for_source(patch_json: Mapping[str, Any], last_updated_ms: Any) -> tuple[str, str | None]:
    """소스 집계 시각에 유효했던 패치 라벨과 메모.

    /tft-stat-api/patch는 **현재** 패치만 준다. Early Comps 집계(lastUpdated)가 현재 패치 시작보다 앞서면 그 데이터는
    직전 패치 것이다. b패치(예 18.3b)면 직전은 b를 뗀 "18.3"으로 확정할 수 있고, 아니면 현재 라벨을 쓰고 메모를 남긴다.
    """
    cur = f"{patch_json['patch']}{patch_json.get('b_patch_version') or ''}"
    try:
        start = dt.datetime.fromisoformat(str(patch_json["start"]).replace("Z", "+00:00"))
        upd = dt.datetime.fromtimestamp(float(last_updated_ms) / 1000, dt.UTC)
    except (KeyError, TypeError, ValueError):
        return cur, None
    if upd >= start:
        return cur, None
    if patch_json.get("b_patch_version"):
        return str(patch_json["patch"]), f"source updated {upd.isoformat(timespec='seconds')} before {cur} start"
    return cur, f"source updated {upd.isoformat(timespec='seconds')} before {cur} start; previous patch label unknown"


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _stat_row(s: Mapping[str, Any] | None) -> dict[str, Any]:
    s = s or {}
    fp = s.get("final_place_count") or 0
    return {
        "avg_place": round(float(s["final_place_avg"]), 4) if s.get("final_place_avg") is not None and fp else None,
        "win_rate": round(float(s["final_place_winrate"]), 4) if s.get("final_place_winrate") is not None and fp else None,
        "round_win_rate": round(float(s["matchup_winrate"]), 4) if s.get("matchup_winrate") is not None else None,
        "avg_hp_delta": round(float(s["matchup_avg_hp_delta"]), 3) if s.get("matchup_avg_hp_delta") is not None else None,
        "games": int(round(fp)),
        "rounds": int(round(s.get("matchup_count") or 0)),
    }


class _Acc:
    """가중 합산기: avg_place·win_rate는 games, round_win_rate·avg_hp_delta는 rounds 가중."""

    __slots__ = ("g", "r", "place", "win", "rwin", "hp")

    def __init__(self) -> None:
        self.g = self.r = 0.0
        self.place = self.win = self.rwin = self.hp = 0.0

    def add(self, s: Mapping[str, Any]) -> None:
        g = float(s.get("final_place_count") or 0)
        r = float(s.get("matchup_count") or 0)
        if g and s.get("final_place_avg") is not None:
            self.g += g
            self.place += g * float(s["final_place_avg"])
            self.win += g * float(s.get("final_place_winrate") or 0)
        if r and s.get("matchup_winrate") is not None:
            self.r += r
            self.rwin += r * float(s["matchup_winrate"])
            self.hp += r * float(s.get("matchup_avg_hp_delta") or 0)

    def row(self) -> dict[str, Any]:
        return {"avg_place": round(self.place / self.g, 4) if self.g else None,
                "win_rate": round(self.win / self.g, 4) if self.g else None,
                "round_win_rate": round(self.rwin / self.r, 4) if self.r else None,
                "avg_hp_delta": round(self.hp / self.r, 3) if self.r else None,
                "games": int(round(self.g)), "rounds": int(round(self.r))}


def _units(csv: str, id_map: Mapping[str, str], static: StaticData, unmapped: set[str]) -> list[str]:
    out = []
    for u in (x for x in csv.split(",") if x):
        cid = map_unit(u, id_map, static)
        if cid is None:
            unmapped.add(u)
        elif cid not in out:
            out.append(cid)
    return sorted(out)


def build_early(raw: Path, static: StaticData | None = None) -> dict[str, Any]:
    """raw 디렉터리(comps_overview.json + comps_full_*.json) → 산출물 dict."""
    static = static or load_static(18)
    id_map = build_id_map(static)
    ov = json.loads((raw / "comps_overview.json").read_text(encoding="utf-8"))
    patch, patch_note = None, None
    if (raw / "patch.json").is_file():
        patch, patch_note = patch_for_source(json.loads((raw / "patch.json").read_text(encoding="utf-8")),
                                             ov.get("lastUpdated"))
    fetched = dt.datetime.fromtimestamp((raw / "comps_overview.json").stat().st_mtime, dt.UTC)
    unmapped: set[str] = set()
    co = ov.get("comps_overview") or {}

    baseline, boards, transitions, sizes, unit_rows = [], [], [], [], []
    full_count = 0
    wanted = 0
    for st in STAGES:
        sd = co.get(f"stage-{st}") or {}
        base = _stat_row(sd.get("latest_stats") or sd.get("stats"))
        baseline.append({"stage": st, **base})
        stage_rounds = float((sd.get("stats") or {}).get("matchup_count") or 0)
        unit_acc: dict[tuple[str, int | None], _Acc] = defaultdict(_Acc)
        size_acc: dict[tuple[str, int], _Acc] = defaultdict(_Acc)
        comps = sd.get("comps") or []
        wanted += len(comps)
        have_full = 0
        for c in comps:
            cl = str(c["cluster"])
            units = _units(c.get("name") or "", id_map, static, unmapped)
            row = _stat_row(c.get("latest_stats") or c.get("stats"))
            boards.append({"stage": st, "kind": "cluster", "cluster": cl, "units": units, **row,
                           "avg_hp": _num((c.get("latest_stats") or {}).get("avg_hp")),
                           "share": round(row["rounds"] / base["rounds"], 5) if base["rounds"] else None})
            fp = raw / full_name(st, cl)
            full = json.loads(fp.read_text(encoding="utf-8")).get("comps_full") if fp.is_file() else None
            unit_src = (full or c).get("units") or {}
            if full:
                have_full += 1
                for v in full.get("variations") or []:
                    vr = _stat_row(v)
                    if vr["games"] < VARIATION_MIN_GAMES:
                        continue
                    boards.append({"stage": st, "kind": "variation", "cluster": cl,
                                   "units": _units(v.get("units") or "", id_map, static, unmapped), **vr,
                                   "avg_hp": _num(v.get("avg_hp")), "share": None})
                trans = full.get("transition_to_stats_latest") or full.get("transition_to_stats") or {}
                tot = sum(float(t.get("matchup_count") or 0) for t in trans.values())
                for nxt, t in trans.items():
                    n = float(t.get("matchup_count") or 0)
                    if not n or not tot:
                        continue
                    tr = _stat_row(t)
                    transitions.append({"stage": st, "cluster": cl, "next_cluster": str(nxt),
                                        "share": round(n / tot, 5), "avg_place": tr["avg_place"],
                                        "win_rate": tr["win_rate"], "round_win_rate": tr["round_win_rate"],
                                        "games": round(float(t.get("final_place_count") or 0), 2)})
                for n_units, nv in (full.get("num_units") or {}).items():
                    for rnd, rv in (nv.get("rounds") or {}).items():
                        size_acc[(rnd, int(n_units))].add(rv)
            for u, stars in unit_src.items():
                cid = map_unit(u, id_map, static)
                if cid is None:
                    unmapped.add(u)
                    continue
                for sk, sv in stars.items():
                    if not sk.endswith("-star") or not isinstance(sv, Mapping):
                        continue
                    star = int(sk.split("-")[0])
                    unit_acc[(cid, star)].add(sv)
                    unit_acc[(cid, None)].add(sv)
        full_count += have_full
        for (cid, star), acc in sorted(unit_acc.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
            r = acc.row()
            if r["rounds"] < UNIT_MIN_ROUNDS:
                continue
            unit_rows.append({"unit_id": cid, "stage": st, "star": star, **r,
                              "pick_rate": round(r["rounds"] / stage_rounds, 5) if stage_rounds else None})
        for (rnd, n), acc in sorted(size_acc.items()):
            sizes.append({"stage": st, "round": rnd, "num_units": n, **acc.row()})

    coverage = "full" if wanted and full_count == wanted else ("partial" if full_count else "core_only")
    report = {
        "source": SOURCE, "patch": patch, "patch_note": patch_note, "set": ov.get("tft_set"),
        "clustering_id": ov.get("clustering_id"), "clustering_created_at": ov.get("clustering_created_at"),
        "source_updated": (dt.datetime.fromtimestamp(ov["lastUpdated"] / 1000, dt.UTC).isoformat(timespec="seconds")
                           if ov.get("lastUpdated") else None),
        "sample_size": ov.get("sampleSize"), "latest_patch_sample_size": ov.get("latestPatchSampleSize"),
        "stages": list(STAGES), "clusters": wanted, "comps_full": full_count, "unit_coverage": coverage,
        "variation_min_games": VARIATION_MIN_GAMES,
        "unmapped_ids": sorted(unmapped), "raw_dir": raw.name, "fetched_at": fetched.isoformat(),
        "counts": {"stage_boards": len(boards), "unit_stage_stats": len(unit_rows),
                   "stage_transitions": len(transitions), "stage_round_sizes": len(sizes)},
        "semantics": {"games": "final_place_count", "rounds": "matchup_count", "win_rate": "1st place rate",
                      "round_win_rate": "combat round win rate", "top4": "not provided",
                      "cluster_rows": "latest patch (latest_stats)",
                      "variation_unit_transition_rows": "all days in source window (stats) unless *_latest exists",
                      "population": "MetaTFT desktop app users, all ranks"},
    }
    return {"report": report, "stage_baseline": baseline, "stage_boards": boards, "unit_stage_stats": unit_rows,
            "stage_transitions": transitions, "stage_round_sizes": sizes}


def dump(doc: Mapping[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{JSON_PREFIX}{doc['report'].get('patch') or 'unknown'}.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return p


def main(argv: Iterable[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="MetaTFT Early Comps 원본 -> 스테이지 보드 JSON")
    ap.add_argument("--raw", default=None)
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "stats"))
    a = ap.parse_args(list(argv) if argv is not None else None)
    raw = Path(a.raw) if a.raw else latest_early_dir()
    if raw is None:
        raise SystemExit("원본 없음: python -m tft_advisor.stats.collectors.metatft_early 먼저 실행")
    doc = build_early(raw)
    print(dump(doc, Path(a.out)))
    print(json.dumps({k: v for k, v in doc["report"].items() if k != "semantics"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
