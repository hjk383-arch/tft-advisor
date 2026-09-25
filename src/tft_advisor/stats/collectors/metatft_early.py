"""MetaTFT "Early Comps"(스테이지별 보드) 원본 수집기 -> data/raw/metatft_early/{date}/.

출처: metatft.com/early-comps 페이지가 부르는 공개 JSON(인증 없음, robots.txt 전체 허용).
MetaTFT 데스크톱 앱(Overwolf)이 게임 중 기록한 **스테이지 2~5의 실제 보드**를 스테이지마다 클러스터링한 통계다.
Riot 매치 API는 최종 보드만 주므로, 스테이지별 보드 성적(표본 수 포함)을 주는 소스는 현재 이것뿐이다
(`_workspace/28_stage_boards_sources.md`).

    GET https://api.metatft.com/tft-early-comps/comps_overview
        → comps_overview["stage-{2..5}"] = {stats, latest_stats, comps[]{cluster, name(유닛 CSV), stats,
          latest_stats, units{유닛: {"1-star"|"2-star"|"3-star": 성적}}, forwards_links, backwards_links}}
    GET https://api.metatft.com/tft-early-comps/comps_full?stage={s}&cluster_id={c}&clustering_id={id}
        → comps_full = {… + units{유닛: {n-star, items[], loc[]}}(클러스터 안 모든 유닛), items, variations[]
          (정확한 보드), num_units{n: {…, rounds{"3-2": …}}}, transition_to/from_stats[_latest], ranks, servers}

요청 규칙은 `collectors.metatft.Fetcher`와 같다(순차, 간격 >= 1초, UA 명시, 대체 호스트 1회 재시도, 캐시 재사용).
`full`: comps_full을 받을 클러스터 범위. "all"(기본) = 스테이지의 모든 클러스터(약 180요청, 1.2초 간격이면 약 4분),
정수 N = 스테이지마다 표본(latest matchup_count) 상위 N개, 0 = overview만.
유닛별 스테이지 성적(`unit_stage_stats`)은 overview만으로는 클러스터 핵심 유닛만 보이므로 "all"이어야 정확하다.

    python -m tft_advisor.stats.collectors.metatft_early [--date 2026-09-24] [--refresh] [--full all|N]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from tft_advisor.static_data import PROJECT_ROOT
from tft_advisor.stats.collectors.metatft import DEFAULT_UA, HOSTS, Fetcher

EARLY_HOSTS = ("https://api.metatft.com", "https://api2.metatft.com")
"""metatft.com 번들이 실패 시 api.metatft.com → api2.metatft.com으로 바꿔 재시도한다(관측)."""
OVERVIEW = "/tft-early-comps/comps_overview"
FULL = "/tft-early-comps/comps_full"
STAGES = (2, 3, 4, 5)
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "metatft_early"


def full_name(stage: int, cluster: str) -> str:
    return f"comps_full_{stage}_{cluster}.json"


def select_clusters(overview: dict, full: str | int) -> list[tuple[int, str]]:
    """comps_full을 받을 (stage, cluster) 목록. full: "all" | int(스테이지별 상위 N, latest matchup_count 기준)."""
    out: list[tuple[int, str]] = []
    co = overview.get("comps_overview") or {}
    for st in STAGES:
        comps = (co.get(f"stage-{st}") or {}).get("comps") or []
        comps = sorted(comps, key=lambda c: -(((c.get("latest_stats") or c.get("stats") or {})
                                               .get("matchup_count")) or 0))
        if full != "all":
            comps = comps[: max(0, int(full))]
        out += [(st, str(c["cluster"])) for c in comps]
    return out


def collect_early_raw(out: Path, *, interval_s: float = 1.2, user_agent: str = DEFAULT_UA, refresh: bool = False,
                      full: str | int = "all", fetcher: Fetcher | None = None) -> dict:
    """원본을 out/에 저장하고 manifest를 반환한다. 이미 있는 파일은 다시 요청하지 않는다(refresh=True면 강제)."""
    out.mkdir(parents=True, exist_ok=True)
    f = fetcher or Fetcher(interval_s, user_agent, hosts=EARLY_HOSTS)
    failures: dict[str, str] = {}

    def save(name: str, path: str, query: dict | None = None, hosts: tuple[str, ...] | None = None):
        p = out / name
        if p.is_file() and not refresh:
            return json.loads(p.read_text(encoding="utf-8"))
        prev = f.hosts
        if hosts is not None:
            f.hosts = hosts
        try:
            data = f.get_json(path, query)
        except RuntimeError as e:
            failures[name] = str(e)
            return None
        finally:
            f.hosts = prev
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    save("patch.json", "/tft-stat-api/patch", hosts=HOSTS)    # 현재 패치 라벨(api-hc)
    ov = save("comps_overview.json", OVERVIEW)
    wanted: list[tuple[int, str]] = []
    if ov:
        cid = ov.get("clustering_id")
        wanted = select_clusters(ov, full)
        for st, c in wanted:
            save(full_name(st, c), FULL, {"stage": str(st), "cluster_id": c, "clustering_id": str(cid)})
    got = [full_name(st, c) for st, c in wanted if (out / full_name(st, c)).is_file()]
    manifest = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "clustering_id": (ov or {}).get("clustering_id"), "full": full, "interval_s": f.interval_s,
                "requests": f.requests, "wanted_full": len(wanted), "comps_full": len(got),
                "missing_full": [full_name(st, c) for st, c in wanted if full_name(st, c) not in got],
                "failures": failures}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def latest_early_dir(root: Path | None = None) -> Path | None:
    """data/raw/metatft_early/ 아래 comps_overview.json이 있는 가장 최근 날짜 디렉터리."""
    root = root or RAW_ROOT
    dirs = sorted(p for p in root.glob("????-??-??") if (p / "comps_overview.json").is_file())
    return dirs[-1] if dirs else None


def parse_full(v: str) -> str | int:
    return "all" if v == "all" else int(v)


def main(argv: list[str] | None = None) -> None:
    from tft_advisor.config import load_settings

    s = load_settings().stats
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--full", type=parse_full, default="all", help='"all" | 스테이지별 상위 N | 0(overview만)')
    ap.add_argument("--interval", type=float, default=s.request_interval_s)
    ap.add_argument("--user-agent", default=s.user_agent)
    a = ap.parse_args(argv)
    m = collect_early_raw(RAW_ROOT / a.date, interval_s=a.interval, user_agent=a.user_agent, refresh=a.refresh,
                          full=a.full)
    print(json.dumps(m, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
