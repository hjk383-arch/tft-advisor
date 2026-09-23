"""배치 갱신: MetaTFT 수집 → 변환 → JSON + SQLite 스냅샷 → 이전 스냅샷과 diff.

실시간 루프 밖에서만 실행한다(`python -m tft_advisor.stats refresh`). 수집 파라미터(간격·UA·days·rank)는
`settings.stats`에서 읽는다.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tft_advisor.config import Settings, load_settings
from tft_advisor.patch_version import latest_snapshot
from tft_advisor.static_data import PROJECT_ROOT, StaticData, load_static
from tft_advisor.stats import db as statsdb
from tft_advisor.stats import diff as statsdiff
from tft_advisor.stats import metatft_convert as mc
from tft_advisor.stats.collectors import metatft as collector

RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "metatft"
STATS_DIR = PROJECT_ROOT / "data" / "stats"
WORKSPACE = PROJECT_ROOT / "_workspace"


def resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else PROJECT_ROOT / p


def collect_kwargs(settings: Settings) -> dict[str, Any]:
    """settings.stats → collect_raw 인자(QA recheck 03 stats-4: 하드코딩 제거)."""
    s = settings.stats
    return {"days": s.days, "rank_filter": s.rank_filter, "interval_s": s.request_interval_s,
            "user_agent": s.user_agent}


@dataclass
class RefreshResult:
    raw_dir: Path
    json_path: Path
    db_path: Path
    snapshot_id: int
    previous_snapshot_id: int | None
    manifest: dict[str, Any] | None
    report: dict[str, Any]
    diff: dict[str, Any]
    diff_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    skipped_identical: bool = False


def refresh(*, fetch: bool = True, date: str | None = None, raw_dir: Path | None = None, refresh_raw: bool = False,
            db_path: Path | None = None, json_dir: Path | None = None, diff_out: Path | str | None = "auto",
            settings: Settings | None = None, static: StaticData | None = None,
            keep: int | None = None) -> RefreshResult:
    """수집(선택) → 변환 → 저장 → diff.

    fetch=False면 네트워크 없이 `raw_dir`(기본: 최신 캐시)만 변환한다.
    diff_out: "auto" = 패치가 바뀌었을 때만 `_workspace/patch_{ver}_diff.md`, Path = 항상 그 경로, None = 쓰지 않음.
    keep: 출처별 보존 스냅샷 수. None이면 `settings.stats.keep_snapshots`(기본 5).
    변환 결과가 DB의 최신 스냅샷과 내용이 같으면 새로 쓰지 않는다(`skipped_identical`) — 이전 스냅샷 보존.
    """
    settings = settings or load_settings()
    static = static or load_static(settings.app.set_number)
    db_path = db_path or resolve(settings.stats.db_path)
    json_dir = json_dir or STATS_DIR
    notes: list[str] = []
    keep = keep_snapshots(settings) if keep is None else keep

    manifest = None
    if fetch:
        raw_dir = raw_dir or RAW_ROOT / (date or dt.date.today().isoformat())
        manifest = collector.collect_raw(raw_dir, refresh=refresh_raw, **collect_kwargs(settings))
        if manifest.get("failures"):
            notes.append(f"수집 실패 {len(manifest['failures'])}건: {sorted(manifest['failures'])}")
    else:
        raw_dir = raw_dir or collector.latest_raw_dir(RAW_ROOT)
    if raw_dir is None or not (raw_dir / "comps_data.json").is_file():
        raise FileNotFoundError(f"MetaTFT 원본 없음: {raw_dir}")

    prev_info = statsdb.find_snapshot(db_path, source="metatft") if db_path.is_file() else None
    prev_doc = statsdb.read_snapshot(db_path, prev_info.id) if prev_info else None
    if prev_doc is None:
        js = latest_snapshot(json_dir, "metatft_")   # 패치 번호 숫자 비교(수정 시각은 checkout 뒤 뒤섞인다)
        if js is not None:
            prev_doc = json.loads(js.read_text(encoding="utf-8"))
            notes.append(f"이전 스냅샷: DB 없음 → {js.name}")
    previous_boards = ({c["comp_id"]: [u["id"] for u in c["final_board"]] for c in prev_doc["comps"]}
                       if prev_doc else None)

    result = mc.build_all(raw_dir, static, previous=previous_boards)
    json_path = mc.dump(result, json_dir)
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    sid = statsdb.write_snapshot(db_path, doc, source="metatft", set_number=static.set_number, keep=keep)
    skipped = prev_info is not None and sid == prev_info.id
    if skipped:
        notes.append(f"내용이 최신 스냅샷 #{sid}와 같아 새로 쓰지 않음(이전 스냅샷 보존)")

    d = statsdiff.diff_docs(prev_doc, doc)
    diff_path: Path | None = None
    if diff_out == "auto":
        if d["patch_changed"]:
            diff_path = WORKSPACE / f"patch_{doc['report'].get('patch')}_diff.md"
    elif diff_out is not None:
        diff_path = Path(diff_out)
    if diff_path is not None:
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text(statsdiff.render_markdown(d), encoding="utf-8")
    return RefreshResult(raw_dir=raw_dir, json_path=json_path, db_path=db_path, snapshot_id=sid,
                         previous_snapshot_id=prev_info.id if prev_info else None, manifest=manifest,
                         report=result["report"], diff=d, diff_path=diff_path, notes=notes,
                         skipped_identical=skipped)


def keep_snapshots(settings: Settings) -> int:
    """보존 스냅샷 수 = `settings.stats.keep_snapshots`(기본 5, ge=1은 config가 검증)."""
    return settings.stats.keep_snapshots


def load_json(path: Path, *, db_path: Path | None = None, settings: Settings | None = None,
              keep: int | None = None) -> int:
    """이미 변환된 metatft_*.json을 DB에 적재(수집·변환 없이). 최신 스냅샷과 내용이 같으면 그 id를 반환."""
    settings = settings or load_settings()
    keep = keep_snapshots(settings) if keep is None else keep
    db_path = db_path or resolve(settings.stats.db_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    return statsdb.write_snapshot(db_path, doc, source="metatft", set_number=settings.app.set_number, keep=keep)
