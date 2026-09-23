"""통계 배치 CLI.

    python -m tft_advisor.stats refresh [--no-fetch] [--date YYYY-MM-DD] [--raw DIR] [--refresh-raw]
                                        [--db PATH] [--diff-out PATH|auto|none]
    python -m tft_advisor.stats load   [JSON]          # 변환된 metatft_*.json → DB (기본: 최신 파일)
    python -m tft_advisor.stats info   [--db PATH]     # 스냅샷 목록 + 현재 스냅샷 요약
    python -m tft_advisor.stats diff   [--old ID] [--new ID] [--db PATH] [--out PATH]

`update`는 `refresh`의 별칭(tft-stats-collect 스킬 표기).

읽기(`info`/`diff`)는 DB를 읽기 전용으로 열어 refresh와 경합하지 않는다. `refresh`/`load`는 결과가 최신 스냅샷과
내용이 같으면 새로 쓰지 않으므로 같은 캐시로 다시 돌려도 diff 기준(이전 스냅샷)이 남는다. 보존 수는
`settings.stats.keep_snapshots`(없으면 5). `load`는 built_at 기준 최신이 되므로 오래된 JSON을 load하면 그것이
"현재" 스냅샷이 된다(의도된 롤백 경로).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tft_advisor.config import load_settings
from tft_advisor.patch_version import latest_snapshot
from tft_advisor.stats import db as statsdb
from tft_advisor.stats import diff as statsdiff
from tft_advisor.stats import refresh as rf


def _db(a: argparse.Namespace) -> Path:
    return Path(a.db) if a.db else rf.resolve(load_settings().stats.db_path)


def cmd_refresh(a: argparse.Namespace) -> int:
    diff_out: Path | str | None = {"auto": "auto", "none": None}.get(a.diff_out, a.diff_out)
    if isinstance(diff_out, str) and diff_out != "auto":
        diff_out = Path(diff_out)
    r = rf.refresh(fetch=not a.no_fetch, date=a.date, raw_dir=Path(a.raw) if a.raw else None,
                   refresh_raw=a.refresh_raw, db_path=_db(a), diff_out=diff_out)
    rep = {k: v for k, v in r.report.items() if k != "comp_augment_tiers"}
    d = r.diff
    print(json.dumps({
        "raw_dir": str(r.raw_dir), "json": str(r.json_path), "db": str(r.db_path),
        "snapshot_id": r.snapshot_id, "previous_snapshot_id": r.previous_snapshot_id,
        "manifest": r.manifest, "report": rep, "notes": r.notes,
        "diff": {"patch": d["patch"], "comps_added": len(d["comps"]["added"]),
                 "comps_removed": len(d["comps"]["removed"]), "comps_changed": len(d["comps"]["changed"]),
                 "augment_tier_changes": len(d["augment_tiers"]["changed"]),
                 "file": str(r.diff_path) if r.diff_path else None},
    }, ensure_ascii=False, indent=1, default=str))
    return 1 if r.manifest and r.manifest.get("failures") else 0


def cmd_load(a: argparse.Namespace) -> int:
    path = Path(a.json) if a.json else latest_snapshot(rf.STATS_DIR, "metatft_")
    if path is None:
        print(f"통계 JSON 없음: {rf.STATS_DIR}")
        return 2
    sid = rf.load_json(path, db_path=_db(a))
    print(f"loaded {path} -> {_db(a)} snapshot {sid}")
    return 0


def cmd_info(a: argparse.Namespace) -> int:
    snaps = statsdb.list_snapshots(_db(a))
    if not snaps:
        print(f"스냅샷 없음: {_db(a)} (python -m tft_advisor.stats refresh)")
        return 1
    for s in snaps:
        print(f"#{s.id} {s.source} patch={s.patch} fetched={s.fetched_at} built={s.built_at} raw={s.raw_dir}")
    from tft_advisor.stats.repository import open_repository

    repo = open_repository(db_path=_db(a))
    print(json.dumps({"current": repo.meta.snapshot_id, "patch": repo.meta.patch, "counts": repo.meta.counts},
                     ensure_ascii=False))
    return 0


def cmd_diff(a: argparse.Namespace) -> int:
    db = _db(a)
    snaps = statsdb.list_snapshots(db, "metatft")
    new_id = a.new or (snaps[0].id if snaps else None)
    old_id = a.old or next((s.id for s in snaps if s.id != new_id), None)
    if new_id is None:
        print("스냅샷 없음", file=sys.stderr)
        return 1
    old = statsdb.read_snapshot(db, old_id) if old_id else None
    md = statsdiff.render_markdown(statsdiff.diff_docs(old, statsdb.read_snapshot(db, new_id)))
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(a.out)
    else:
        print(md)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tft_advisor.stats", description="TFT 통계 배치(수집·변환·DB)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("refresh", "update"):
        r = sub.add_parser(name, help="MetaTFT 수집 → 변환 → DB → diff")
        r.add_argument("--no-fetch", action="store_true", help="네트워크 없이 최신 원본 캐시만 변환")
        r.add_argument("--date", default=None, help="원본 캐시 디렉터리 날짜(기본 오늘)")
        r.add_argument("--raw", default=None, help="원본 디렉터리 직접 지정")
        r.add_argument("--refresh-raw", action="store_true", help="캐시된 원본도 다시 요청")
        r.add_argument("--db", default=None)
        r.add_argument("--diff-out", default="auto", help="auto(패치 변경 시 _workspace/patch_{ver}_diff.md)|none|경로")
        r.set_defaults(func=cmd_refresh)
    ld = sub.add_parser("load", help="metatft_*.json → DB (적재한 것이 '현재' 스냅샷이 됨 = 롤백 경로)")
    ld.add_argument("json", nargs="?")
    ld.add_argument("--db", default=None)
    ld.set_defaults(func=cmd_load)
    inf = sub.add_parser("info", help="스냅샷 목록")
    inf.add_argument("--db", default=None)
    inf.set_defaults(func=cmd_info)
    df = sub.add_parser("diff", help="스냅샷 diff(markdown). 기본: 최신 vs 그 직전(내용이 다른) 스냅샷")
    df.add_argument("--old", type=int, default=None)
    df.add_argument("--new", type=int, default=None)
    df.add_argument("--db", default=None)
    df.add_argument("--out", default=None)
    df.set_defaults(func=cmd_diff)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
