"""MetaTFT 원본 수집기 (공개 JSON, 인증 없음) -> data/raw/metatft/{date}/.

실시간 루프 밖에서 돌리는 배치다. 요청은 순차 실행하고 요청 사이에 `interval_s`(기본 1.2초) 이상 쉰다.
이미 캐시된 파일은 다시 요청하지 않는다(`--refresh`로 강제).
변환(원본 -> 계약 모델)은 `tft_advisor.stats.metatft_convert`가 한다. 이 모듈은 받아서 저장만 한다.

    python -m tft_advisor.stats.collectors.metatft [--date 2026-09-22] [--refresh] [--only comp_details]
    (보통은 수집·변환·DB 적재를 한 번에 하는 `python -m tft_advisor.stats refresh`를 쓴다)

요청 파라미터(간격·User-Agent·days·rank)의 기준값은 `config/settings.toml [stats]`다. 아래 DEFAULT_* 상수는
설정 없이 함수를 직접 부를 때의 폴백이며 settings 기본값과 같다.

의존성: 표준 라이브러리만(urllib). 1회 재시도 후 실패하면 해당 파일을 건너뛰고 failures에 남긴다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from tft_advisor.static_data import PROJECT_ROOT

HOSTS = ("https://api-hc.metatft.com", "https://api-hc2.metatft.com")
DEFAULT_RANKS = "CHALLENGER,DIAMOND,GRANDMASTER,MASTER"
DEFAULT_UA = "tft-advisor-research/0.1 (personal use)"


def common_query(days: int = 3, rank_filter: str = DEFAULT_RANKS) -> dict[str, str]:
    return {"queue": "1100", "patch": "current", "days": str(days), "rank": rank_filter,
            "permit_filter_adjustment": "true"}


class Fetcher:
    """순차 GET + 최소 간격 + 1회 재시도(대체 호스트)."""

    def __init__(self, interval_s: float = 1.2, user_agent: str = DEFAULT_UA, timeout_s: float = 30.0) -> None:
        self.interval_s = max(1.0, interval_s)
        self.ua = user_agent
        self.timeout_s = timeout_s
        self._last = 0.0
        self.requests = 0

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.interval_s:
            time.sleep(self.interval_s - gap)
        self._last = time.monotonic()

    def get_json(self, path: str, query: dict[str, str] | None = None):
        qs = ("?" + urlencode(query, safe=",")) if query else ""
        err: Exception | None = None
        for host in HOSTS:  # 1차 + 1회 재시도(대체 호스트)
            self._wait()
            self.requests += 1
            req = urllib.request.Request(host + path + qs, headers={
                "User-Agent": self.ua, "Accept": "application/json", "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    body = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    return json.loads(body)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                err = e
        raise RuntimeError(f"GET {path} 실패: {err}")


def collect_raw(out: Path, *, days: int = 3, rank_filter: str = DEFAULT_RANKS, interval_s: float = 1.2,
                user_agent: str = DEFAULT_UA, refresh: bool = False, only: set[str] | None = None) -> dict:
    """원본을 out/에 저장하고 요약(manifest)을 반환한다. only: {"base","comp_details"} 중 일부."""
    out.mkdir(parents=True, exist_ok=True)
    f = Fetcher(interval_s, user_agent)
    q = common_query(days, rank_filter)
    failures: dict[str, str] = {}

    def save(name: str, path: str, query: dict | None = None):
        p = out / name
        if p.is_file() and not refresh:
            return json.loads(p.read_text(encoding="utf-8"))
        try:
            data = f.get_json(path, query)
        except RuntimeError as e:
            failures[name] = str(e)
            return None
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    want = only or {"base", "comp_details"}
    info = None
    if "base" in want:
        save("patch.json", "/tft-stat-api/patch")
        info = save("latest_cluster_info.json", "/tft-comps-api/latest_cluster_info")
        save("units.json", "/tft-stat-api/units", q)
        save("items.json", "/tft-stat-api/items", q)
        save("augments_tiers.json", "/tft-stat-api/augments_tiers")
        save("comp_augment_tiers.json", "/tft-comps-api/comp_augment_tiers")
    comps = save("comps_data.json", "/tft-comps-api/comps_data", {"queue": "1100"})
    ids: list[str] = []
    if comps:
        ids = sorted(comps["results"]["data"]["cluster_details"])
    if "comp_details" in want:
        if info is None and (out / "latest_cluster_info.json").is_file():
            info = json.loads((out / "latest_cluster_info.json").read_text(encoding="utf-8"))
        for comp in ids:
            save(f"comp_details_{comp}.json", "/tft-comps-api/comp_details",
                 {"comp": comp, "cluster_id": cluster_id_of(comp, info)})
    got = [c for c in ids if (out / f"comp_details_{c}.json").is_file()]
    manifest = {"fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "days": days, "rank_filter": rank_filter, "interval_s": f.interval_s,
                "requests": f.requests, "clusters": len(ids), "comp_details": len(got),
                "missing_comp_details": [c for c in ids if c not in got], "failures": failures}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def cluster_id_of(comp: str, info: dict | None) -> str:
    """덱(comp) ID의 클러스터 ID. latest_cluster_info가 있으면 그 값, 없으면 덱 ID에서 뒤 3자리를 뗀 값.

    관측: 클러스터 424의 덱 ID는 424000~424056(= cluster_id * 1000 + 순번).
    """
    if info:
        cid = (info.get("cluster_info") or {}).get("cluster_id")
        if cid is not None:
            return str(cid)
    return str(comp)[:-3]


def raw_dir_complete(d: Path) -> bool:
    """캐시가 변환에 충분한가: patch/units/comps_data가 있고 comps_data의 모든 클러스터에 comp_details가 있다."""
    if not all((d / f).is_file() for f in ("comps_data.json", "units.json", "patch.json")):
        return False
    try:
        clusters = json.loads((d / "comps_data.json").read_text(encoding="utf-8"))["results"]["data"]["cluster_details"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return all((d / f"comp_details_{cid}.json").is_file() for cid in clusters)


def latest_raw_dir(root: Path | None = None, *, require_complete: bool = True) -> Path | None:
    """data/raw/metatft/ 아래 가장 최근 날짜 디렉터리(comps_data.json이 있는 것).

    require_complete(기본)이면 `raw_dir_complete`인 것 중 최신을 고른다. 완전한 캐시가 하나도 없을 때만 불완전한
    최신 캐시로 폴백한다(stats 08: 2026-09-21 comp_details 1개짜리 캐시가 최신이면 테스트·변환이 깨졌다).
    """
    root = root or PROJECT_ROOT / "data" / "raw" / "metatft"
    dirs = sorted(p for p in root.glob("????-??-??") if (p / "comps_data.json").is_file())
    if require_complete:
        complete = [p for p in dirs if raw_dir_complete(p)]
        if complete:
            return complete[-1]
    return dirs[-1] if dirs else None


def main(argv: list[str] | None = None) -> None:
    """기본값은 settings.stats(days, rank_filter, request_interval_s, user_agent). 인자로 덮어쓸 수 있다."""
    from tft_advisor.config import load_settings

    s = load_settings().stats
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--only", choices=["base", "comp_details"], action="append")
    ap.add_argument("--days", type=int, default=s.days)
    ap.add_argument("--rank", default=s.rank_filter)
    ap.add_argument("--interval", type=float, default=s.request_interval_s)
    ap.add_argument("--user-agent", default=s.user_agent)
    a = ap.parse_args(argv)
    out = PROJECT_ROOT / "data" / "raw" / "metatft" / a.date
    m = collect_raw(out, days=a.days, rank_filter=a.rank, interval_s=a.interval, user_agent=a.user_agent,
                    refresh=a.refresh, only=set(a.only) if a.only else None)
    print(json.dumps(m, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
