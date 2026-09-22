"""런타임 통계 DB (SQLite 1파일, 기본 `settings.stats.db_path` = data/stats/stats.sqlite, gitignore).

한 DB에 여러 스냅샷(출처 × 패치 × 수집 시각)을 둔다. 각 행은 계약 모델의 JSON을 그대로 담고(`json` 열),
조회·diff에 쓰는 키 열만 따로 뺀다. 읽는 쪽(`repository`)은 스냅샷 하나를 통째로 메모리에 올린다.

테이블
- snapshots(id, source, patch, set_number, fetched_at, built_at, raw_dir, rank_filter_units, report_json)
- comps(snapshot_id, comp_id, source_cluster_id, avg_place, games, json)                 CompStats
- augment_tiers(snapshot_id, augment_id, comp_id, tier, json)                            AugmentTier
- unit_stats(snapshot_id, unit_id, json)                                                 UnitStats
- unit_items(snapshot_id, kind, unit_id, comp_id, item_key, games, avg_place, place_change, json)
      kind: "holds"(itemNames: 유닛이 아이템 x를 든 판) | "build"(builds: 정확한 1~3아이템 구성)
      item_key: item_ids를 '|'로 이은 문자열(순서 유지)
- item_stats(snapshot_id, item_id, avg_place, top4, win_rate, games)                     PlacementStats + id

쓰기는 트랜잭션 1개. 같은 (source, patch, fetched_at) 스냅샷을 다시 쓰면 교체한다. 출처별 최근 `keep`개만 남긴다.
단, 새 스냅샷의 내용 해시(`content_hash`)가 출처의 최신 스냅샷과 같으면 아무것도 쓰지 않고 그 id를 돌려준다
(같은 캐시로 `refresh`를 다시 돌려도 diff 기준이 되는 이전 스냅샷이 밀려나지 않게 — QA 04 W2).

연결 규칙(QA 04 W1)
- 쓰기(`write_snapshot`)만 `_connect_rw`로 연다: 스키마 생성·마이그레이션·`journal_mode=WAL`.
- 읽기(`list_snapshots`/`find_snapshot`/`read_snapshot`)는 `file:...?mode=ro` URI로 연다. DDL·INSERT를 하지 않는다.
  WAL이라 refresh가 쓰는 동안에도 읽기가 막지도 막히지도 않는다(읽기는 커밋된 직전 상태를 본다).
  `read_snapshot`은 조회 전체를 한 읽기 트랜잭션으로 묶어 일관된 시점을 본다.
- 파일·디렉터리가 쓰기 불가라 `-shm`을 만들 수 없는 읽기 전용 배포에서는 `immutable=1`로 재시도한다
  (이 경우 동시 쓰기는 애초에 불가능하므로 안전하다).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2          # 2: snapshots.content_hash
DEFAULT_KEEP = 5            # 출처별 보존 스냅샷 수 기본값(refresh는 settings.stats.keep_snapshots를 넘긴다)
BUSY_TIMEOUT_S = 10.0

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    patch TEXT,
    set_number INTEGER NOT NULL,
    fetched_at TEXT,
    built_at TEXT NOT NULL,
    raw_dir TEXT,
    rank_filter_units TEXT,
    report_json TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT,
    UNIQUE (source, patch, fetched_at)
);
CREATE TABLE IF NOT EXISTS comps (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    comp_id TEXT NOT NULL, source_cluster_id TEXT, avg_place REAL, games INTEGER, json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, comp_id)
);
CREATE TABLE IF NOT EXISTS augment_tiers (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    augment_id TEXT NOT NULL, comp_id TEXT, tier TEXT NOT NULL, json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_aug ON augment_tiers(snapshot_id, augment_id);
CREATE TABLE IF NOT EXISTS unit_stats (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    unit_id TEXT NOT NULL, json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, unit_id)
);
CREATE TABLE IF NOT EXISTS unit_items (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('holds', 'build')),
    unit_id TEXT NOT NULL, comp_id TEXT, item_key TEXT NOT NULL,
    games INTEGER, avg_place REAL, place_change REAL, json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ui ON unit_items(snapshot_id, unit_id, comp_id);
CREATE TABLE IF NOT EXISTS item_stats (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL, avg_place REAL, top4 REAL, win_rate REAL, games INTEGER,
    PRIMARY KEY (snapshot_id, item_id)
);
"""


@dataclass(frozen=True)
class SnapshotInfo:
    id: int
    source: str
    patch: str | None
    set_number: int
    fetched_at: str | None
    built_at: str
    raw_dir: str | None
    rank_filter_units: str | None
    report: dict[str, Any]


def _connect_rw(db_path: Path) -> sqlite3.Connection:
    """쓰기 연결: 스키마 생성·마이그레이션·WAL. `write_snapshot`에서만 쓴다."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    _migrate(con)
    con.execute("INSERT OR REPLACE INTO schema_info VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()
    return con


def connect(db_path: Path) -> sqlite3.Connection:
    """하위 호환 별칭(쓰기 연결). 읽기에는 쓰지 말 것 — `connect_ro`."""
    return _connect_rw(db_path)


def _migrate(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(snapshots)")}
    if "content_hash" not in cols:   # v1 → v2: 기존 스냅샷의 해시를 채운다
        con.execute("ALTER TABLE snapshots ADD COLUMN content_hash TEXT")
    for (sid,) in con.execute("SELECT id FROM snapshots WHERE content_hash IS NULL").fetchall():
        con.execute("UPDATE snapshots SET content_hash=? WHERE id=?", (_hash_stored(con, sid), sid))


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """읽기 전용 연결(`mode=ro`). DB에 아무것도 쓰지 않는다. 파일이 없으면 OperationalError."""
    uri = db_path.resolve().as_uri()
    try:
        con = sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_S)
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()   # WAL -shm 접근 실패를 여기서 드러낸다
        return con
    except sqlite3.OperationalError:
        if not db_path.is_file():
            raise
    # 읽기 전용 파일/디렉터리(WAL -shm 생성 불가): 변경되지 않는 파일로 취급
    return sqlite3.connect(f"{uri}?mode=ro&immutable=1", uri=True, timeout=BUSY_TIMEOUT_S)


@contextmanager
def _tx(db_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(_connect_rw(db_path)) as con:
        with con:  # commit / rollback
            yield con


@contextmanager
def _ro(db_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(connect_ro(db_path)) as con:
        con.execute("BEGIN")          # 읽기 트랜잭션 1개 = 일관된 시점(WAL 스냅샷)
        try:
            yield con
        finally:
            con.rollback()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _hash_rows(report_json: str, tables: Mapping[str, list]) -> str:
    """저장되는 값(행 JSON·item_stats 튜플) 기준 내용 해시. 행 순서는 무시한다. built_at·id는 포함하지 않는다."""
    h = hashlib.sha256(report_json.encode())
    for name in sorted(tables):
        h.update(f"\x00{name}\x00".encode())
        for row in sorted(_dumps(r) if not isinstance(r, str) else r for r in tables[name]):
            h.update(row.encode())
            h.update(b"\x01")
    return h.hexdigest()


def _rows_from_doc(doc: Mapping[str, Any]) -> dict[str, list]:
    return {
        "comps": [_dumps(c) for c in doc.get("comps", [])],
        "augment_tiers": [_dumps(t) for t in doc.get("augment_tiers", [])],
        "unit_stats": [_dumps(u) for u in doc.get("unit_stats", [])],
        "unit_items": [[k, _dumps(r)] for k, key in (("holds", "unit_item_stats"), ("build", "unit_build_stats"))
                       for r in doc.get(key, [])],
        "item_stats": [[r["item_id"], r.get("avg_place"), r.get("top4"), r.get("win_rate"), r.get("games")]
                       for r in doc.get("item_stats", [])],
    }


def _hash_stored(con: sqlite3.Connection, sid: int) -> str:
    """DB에 저장된 스냅샷의 내용 해시(`_rows_from_doc`와 같은 정규형)."""
    rep = con.execute("SELECT report_json FROM snapshots WHERE id=?", (sid,)).fetchone()[0]
    q = lambda sql: [r[0] for r in con.execute(sql, (sid,))]  # noqa: E731
    return _hash_rows(rep, {
        "comps": q("SELECT json FROM comps WHERE snapshot_id=?"),
        "augment_tiers": q("SELECT json FROM augment_tiers WHERE snapshot_id=?"),
        "unit_stats": q("SELECT json FROM unit_stats WHERE snapshot_id=?"),
        "unit_items": [[k, j] for k, j in con.execute(
            "SELECT kind, json FROM unit_items WHERE snapshot_id=?", (sid,))],
        "item_stats": [list(r) for r in con.execute(
            "SELECT item_id, avg_place, top4, win_rate, games FROM item_stats WHERE snapshot_id=?", (sid,))],
    })


def content_hash(doc: Mapping[str, Any]) -> str:
    """변환 산출물 dict의 내용 해시(`write_snapshot`이 중복 적재 판정에 쓴다)."""
    return _hash_rows(_dumps(dict(doc.get("report") or {})), _rows_from_doc(doc))


def write_snapshot(db_path: Path, doc: Mapping[str, Any], *, source: str = "metatft", set_number: int = 18,
                   keep: int = DEFAULT_KEEP) -> int:
    """변환 산출물(`metatft_convert.dump` 형식 dict)을 스냅샷 1개로 적재하고 snapshot id를 반환한다.

    출처의 최신 스냅샷과 내용 해시가 같으면 쓰지 않고(보존 정리도 하지 않고) 그 id를 반환한다.
    """
    report = dict(doc.get("report") or {})
    patch = report.get("patch")
    fetched = report.get("fetched_at") or next((c.get("fetched_at") for c in doc.get("comps", [])), None)
    built = datetime.now(UTC).isoformat(timespec="seconds")
    chash = content_hash(doc)
    with _tx(db_path) as con:
        latest = con.execute("SELECT id, content_hash FROM snapshots WHERE source=? "
                             "ORDER BY built_at DESC, id DESC LIMIT 1", (source,)).fetchone()
        if latest is not None and latest[1] == chash:
            return int(latest[0])
        con.execute("DELETE FROM snapshots WHERE source=? AND patch IS ? AND fetched_at IS ?", (source, patch, fetched))
        cur = con.execute(
            "INSERT INTO snapshots(source, patch, set_number, fetched_at, built_at, raw_dir, rank_filter_units,"
            " report_json, content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (source, patch, set_number, fetched, built, report.get("raw_dir"), report.get("rank_filter_units"),
             _dumps(report), chash))
        sid = cur.lastrowid
        con.executemany("INSERT INTO comps VALUES (?,?,?,?,?,?)", [
            (sid, c["comp_id"], c.get("source_cluster_id"), c.get("avg_place"), c.get("games"), _dumps(c))
            for c in doc.get("comps", [])])
        con.executemany("INSERT INTO augment_tiers VALUES (?,?,?,?,?)", [
            (sid, t["augment_id"], t.get("comp_id"), t["tier"], _dumps(t)) for t in doc.get("augment_tiers", [])])
        con.executemany("INSERT INTO unit_stats VALUES (?,?,?)", [
            (sid, u["unit_id"], _dumps(u)) for u in doc.get("unit_stats", [])])
        for kind, key in (("holds", "unit_item_stats"), ("build", "unit_build_stats")):
            con.executemany("INSERT INTO unit_items VALUES (?,?,?,?,?,?,?,?,?)", [
                (sid, kind, r["unit_id"], r.get("comp_id"), "|".join(r["item_ids"]), r.get("games"),
                 r.get("avg_place"), r.get("place_change"), _dumps(r)) for r in doc.get(key, [])])
        con.executemany("INSERT INTO item_stats VALUES (?,?,?,?,?,?)", [
            (sid, r["item_id"], r.get("avg_place"), r.get("top4"), r.get("win_rate"), r.get("games"))
            for r in doc.get("item_stats", [])])
        old = [r[0] for r in con.execute(
            "SELECT id FROM snapshots WHERE source=? ORDER BY built_at DESC, id DESC LIMIT -1 OFFSET ?",
            (source, keep))]
        con.executemany("DELETE FROM snapshots WHERE id=?", [(i,) for i in old])
    return sid


def _info(row: tuple) -> SnapshotInfo:
    return SnapshotInfo(id=row[0], source=row[1], patch=row[2], set_number=row[3], fetched_at=row[4],
                        built_at=row[5], raw_dir=row[6], rank_filter_units=row[7], report=json.loads(row[8] or "{}"))


_SNAP_COLS = "id, source, patch, set_number, fetched_at, built_at, raw_dir, rank_filter_units, report_json"


def list_snapshots(db_path: Path, source: str | None = None) -> list[SnapshotInfo]:
    """최신 순(읽기 전용 연결)."""
    if not db_path.is_file():
        return []
    with _ro(db_path) as con:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'").fetchone() is None:
            return []
        q = f"SELECT {_SNAP_COLS} FROM snapshots"
        args: tuple = ()
        if source:
            q += " WHERE source=?"
            args = (source,)
        return [_info(r) for r in con.execute(q + " ORDER BY built_at DESC, id DESC", args)]


def find_snapshot(db_path: Path, *, source: str = "metatft", patch: str | None = None,
                  snapshot_id: int | None = None) -> SnapshotInfo | None:
    """조건에 맞는 가장 최근 스냅샷(patch=None이면 패치 무관 최신)."""
    for s in list_snapshots(db_path, source):
        if snapshot_id is not None and s.id != snapshot_id:
            continue
        if patch is not None and s.patch != patch:
            continue
        return s
    return None


def read_snapshot(db_path: Path, snapshot_id: int) -> dict[str, Any]:
    """스냅샷 1개 -> `metatft_convert.dump`와 같은 모양의 dict(값은 JSON dict, 모델 검증은 호출자가).

    읽기 전용 연결 + 읽기 트랜잭션 1개(동시 refresh 중에도 커밋된 한 시점만 본다).
    """
    with _ro(db_path) as con:
        row = con.execute(f"SELECT {_SNAP_COLS} FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise KeyError(f"snapshot {snapshot_id} 없음: {db_path}")
        info = _info(row)

        def col(q: str) -> list[dict]:
            return [json.loads(r[0]) for r in con.execute(q, (snapshot_id,))]

        doc = {
            "snapshot": info,
            "report": info.report,
            "comps": col("SELECT json FROM comps WHERE snapshot_id=? ORDER BY comp_id"),
            "augment_tiers": col("SELECT json FROM augment_tiers WHERE snapshot_id=? ORDER BY rowid"),
            "unit_stats": col("SELECT json FROM unit_stats WHERE snapshot_id=? ORDER BY unit_id"),
            "unit_item_stats": col("SELECT json FROM unit_items WHERE snapshot_id=? AND kind='holds' ORDER BY rowid"),
            "unit_build_stats": col("SELECT json FROM unit_items WHERE snapshot_id=? AND kind='build' ORDER BY rowid"),
            "item_stats": [
                {k: v for k, v in zip(("item_id", "avg_place", "top4", "win_rate", "games"), r) if v is not None}
                for r in con.execute("SELECT item_id, avg_place, top4, win_rate, games FROM item_stats "
                                     "WHERE snapshot_id=? ORDER BY item_id", (snapshot_id,))],
        }
    doc["comp_ids"] = {c["source_cluster_id"]: c["comp_id"] for c in doc["comps"] if c.get("source_cluster_id")}
    return doc
