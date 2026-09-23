"""배치 갱신(refresh CLI) + diff + 수집기 설정 연동 테스트. 네트워크 없음(수집 함수는 가짜로 바꾼다)."""
from __future__ import annotations

import copy
import json
import time

import pytest

from tft_advisor.config import Settings
from tft_advisor.stats import __main__ as cli
from tft_advisor.stats import db as statsdb
from tft_advisor.stats import diff as statsdiff
from tft_advisor.stats import refresh as rf
from tft_advisor.stats.collectors import metatft as collector

RAW = collector.latest_raw_dir()
needs_raw = pytest.mark.skipif(RAW is None, reason="MetaTFT 원본 캐시 없음(data/raw/metatft)")

CUSTOM = Settings.model_validate({"stats": {"days": 5, "rank_filter": "MASTER,CHALLENGER", "request_interval_s": 2.5,
                                            "user_agent": "test-ua/1.0", "db_path": "ignored.sqlite"}})


@pytest.fixture(scope="module")
def first(tmp_path_factory):
    d = tmp_path_factory.mktemp("refresh")
    r = rf.refresh(fetch=False, raw_dir=RAW, db_path=d / "s.sqlite", json_dir=d, diff_out=None) if RAW else None
    return d, r


# --------------------------------------------------------------------------- 수집기 ↔ settings.stats


def test_collect_kwargs_come_from_settings():
    assert rf.collect_kwargs(CUSTOM) == {"days": 5, "rank_filter": "CHALLENGER,MASTER", "interval_s": 2.5,
                                        "user_agent": "test-ua/1.0"}


@needs_raw
def test_refresh_fetch_passes_settings_to_collector(monkeypatch, tmp_path):
    seen = {}

    def fake_collect(out, **kw):
        seen.update(kw, out=out)
        return {"failures": {}, "comp_details": 57}

    monkeypatch.setattr(collector, "collect_raw", fake_collect)
    r = rf.refresh(fetch=True, raw_dir=RAW, db_path=tmp_path / "s.sqlite", json_dir=tmp_path, diff_out=None,
                   settings=CUSTOM)
    assert seen["out"] == RAW and seen["interval_s"] == 2.5 and seen["user_agent"] == "test-ua/1.0"
    assert seen["days"] == 5 and seen["rank_filter"] == "CHALLENGER,MASTER" and seen["refresh"] is False
    assert r.manifest == {"failures": {}, "comp_details": 57}


def test_collector_main_defaults_from_settings(monkeypatch):
    import tft_advisor.config as config

    seen = {}
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: CUSTOM)
    monkeypatch.setattr(collector, "collect_raw", lambda out, **kw: seen.update(kw) or {"ok": True})
    collector.main(["--date", "2000-01-01"])
    assert (seen["interval_s"], seen["user_agent"], seen["days"], seen["rank_filter"]) == (
        2.5, "test-ua/1.0", 5, "CHALLENGER,MASTER")


def test_fetcher_enforces_min_interval():
    assert collector.Fetcher(interval_s=0.1).interval_s == 1.0


# --------------------------------------------------------------------------- refresh (--no-fetch)


@needs_raw
def test_refresh_no_fetch_writes_json_and_db(first):
    d, r = first
    assert r.json_path.parent == d and r.json_path.name == f"metatft_{r.report['patch']}.json"
    snaps = statsdb.list_snapshots(r.db_path)
    assert [s.id for s in snaps] == [r.snapshot_id] and snaps[0].patch == r.report["patch"]
    assert r.previous_snapshot_id is None and r.diff["comps"]["added"]   # 첫 적재 = 전부 추가
    assert r.report["non_champion_units_dropped"] == []


@needs_raw
def test_refresh_same_snapshot_skips_and_diff_is_empty(first):
    d, r = first
    r2 = rf.refresh(fetch=False, raw_dir=RAW, db_path=r.db_path, json_dir=d, diff_out=None)
    assert r2.previous_snapshot_id == r.snapshot_id
    assert r2.skipped_identical and r2.snapshot_id == r.snapshot_id   # 내용 동일 → 쓰지 않음(QA 04 W2)
    assert len(statsdb.list_snapshots(r.db_path)) == 1
    dd = r2.diff
    assert not dd["patch_changed"] and not dd["comps"]["added"] and not dd["comps"]["removed"]
    assert not dd["comps"]["changed"] and not dd["augment_tiers"]["changed"]


@needs_raw
def test_snapshot_retention(first, tmp_path):
    _, r = first
    doc = json.loads(r.json_path.read_text(encoding="utf-8"))
    db = tmp_path / "k.sqlite"
    for i in range(4):
        doc["report"]["fetched_at"] = f"2026-01-0{i + 1}T00:00:00+00:00"
        statsdb.write_snapshot(db, doc, keep=2)
    assert len(statsdb.list_snapshots(db)) == 2


@needs_raw
def test_auto_diff_file_on_patch_change(first, monkeypatch, tmp_path):
    d, r = first
    db = tmp_path / "p.sqlite"
    old = json.loads(r.json_path.read_text(encoding="utf-8"))
    old["report"]["patch"] = "18.1"
    old["report"]["fetched_at"] = "2026-09-01T00:00:00+00:00"
    statsdb.write_snapshot(db, old)
    monkeypatch.setattr(rf, "WORKSPACE", tmp_path / "ws")
    r2 = rf.refresh(fetch=False, raw_dir=RAW, db_path=db, json_dir=tmp_path, diff_out="auto")
    assert r2.diff["patch_changed"] and r2.diff_path == tmp_path / "ws" / f"patch_{r2.report['patch']}_diff.md"
    assert r2.diff_path.read_text(encoding="utf-8").startswith("# 통계 변경 목록: 18.1 →")


# --------------------------------------------------------------------------- diff


@needs_raw
def test_diff_detects_changes(first):
    _, r = first
    new = json.loads(r.json_path.read_text(encoding="utf-8"))
    old = copy.deepcopy(new)
    gone = old["comps"].pop(0)["comp_id"]                 # new에만 있음 → added
    old["comps"][0]["avg_place"] += 0.5                   # 평균 등수 변화
    old["comps"][1]["carry"] = "DA_18_Nobody"             # carry 변화
    t = next(t for t in old["augment_tiers"] if not t.get("comp_id"))
    t["tier"] = "D" if t["tier"] != "D" else "S"
    old["report"]["patch"] = "18.2"
    d = statsdiff.diff_docs(old, new)
    assert d["patch_changed"] and [x["comp_id"] for x in d["comps"]["added"]] == [gone]
    changed = {x["comp_id"]: x for x in d["comps"]["changed"]}
    assert "avg_place" in changed[old["comps"][0]["comp_id"]] and "carry" in changed[old["comps"][1]["comp_id"]]
    assert t["augment_id"] in d["augment_tiers"]["changed"]
    md = statsdiff.render_markdown(d)
    assert "## 덱 (추가 1" in md and t["augment_id"] in md


# --------------------------------------------------------------------------- CLI


@needs_raw
def test_cli_info_diff_load(first, capsys, tmp_path):
    d, r = first
    assert cli.main(["info", "--db", str(r.db_path)]) == 0
    assert "patch=" in capsys.readouterr().out
    out = tmp_path / "diff.md"
    assert cli.main(["diff", "--db", str(r.db_path), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# 통계 변경 목록")
    db2 = tmp_path / "l.sqlite"
    assert cli.main(["load", str(r.json_path), "--db", str(db2)]) == 0
    assert len(statsdb.list_snapshots(db2)) == 1


def test_cli_info_empty_db(tmp_path, capsys):
    assert cli.main(["info", "--db", str(tmp_path / "none.sqlite")]) == 1


# --------------------------------------------------------------------------- QA 04 fix round (W1/W2/keep)


@needs_raw
def test_identical_rerun_keeps_previous_distinct_snapshot(first, tmp_path):
    """다른 내용의 이전 스냅샷 A, 현재 B → B를 다시 적재해도 A가 남아 CLI diff 기준이 된다."""
    _, r = first
    doc = json.loads(r.json_path.read_text(encoding="utf-8"))
    db = tmp_path / "w2.sqlite"
    a = copy.deepcopy(doc)
    a["report"]["fetched_at"] = "2026-09-01T00:00:00+00:00"
    a["comps"][0]["avg_place"] += 0.5
    sa = statsdb.write_snapshot(db, a, keep=2)
    sb = statsdb.write_snapshot(db, doc, keep=2)
    for _ in range(3):
        assert statsdb.write_snapshot(db, doc, keep=2) == sb
    assert [s.id for s in statsdb.list_snapshots(db)] == [sb, sa]
    # 같은 fetched_at이라도 내용이 다르면(변환기 변경) 교체한다
    c = copy.deepcopy(doc)
    c["comps"][1]["carry"] = "DA_18_Nobody"
    sc = statsdb.write_snapshot(db, c, keep=2)
    assert sc != sb and [s.id for s in statsdb.list_snapshots(db)] == [sc, sa]


@needs_raw
def test_v1_db_migrates_content_hash(first, tmp_path):
    import sqlite3
    _, r = first
    doc = json.loads(r.json_path.read_text(encoding="utf-8"))
    db = tmp_path / "v1.sqlite"
    sid = statsdb.write_snapshot(db, doc)
    with sqlite3.connect(db) as con:   # v1 흉내: 해시 없음
        con.execute("UPDATE snapshots SET content_hash=NULL")
    assert statsdb.write_snapshot(db, doc) == sid   # 마이그레이션이 해시를 채워 중복으로 판정


@needs_raw
def test_reads_do_not_write_and_do_not_block_writer(first, tmp_path):
    """읽기 경로는 mode=ro(파일 mtime·스키마 불변), 열린 읽기 트랜잭션이 있어도 refresh 쓰기가 막히지 않는다(WAL)."""
    import sqlite3
    _, r = first
    doc = json.loads(r.json_path.read_text(encoding="utf-8"))
    db = tmp_path / "c.sqlite"
    s1 = statsdb.write_snapshot(db, doc)
    with sqlite3.connect(db) as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con = statsdb.connect_ro(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("CREATE TABLE x(a)")
    con.execute("BEGIN")
    n_before = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]   # 읽기 트랜잭션 유지
    d2 = copy.deepcopy(doc)
    d2["report"]["fetched_at"] = "2027-01-01T00:00:00+00:00"
    t0 = time.monotonic()
    s2 = statsdb.write_snapshot(db, d2)      # 읽기가 열려 있어도 즉시 커밋
    assert time.monotonic() - t0 < 5 and s2 != s1
    assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == n_before   # 읽기는 일관된 시점
    con.rollback()
    con.close()
    assert statsdb.find_snapshot(db).id == s2 and statsdb.read_snapshot(db, s1)["comps"]


def test_diff_without_baseline_and_per_comp_tiers():
    new = {"report": {"patch": "18.2b"}, "comps": [{"comp_id": "c1", "name": "C1"}],
           "augment_tiers": [{"augment_id": "A", "tier": "S"}, {"augment_id": "A", "comp_id": "c1", "tier": "S"},
                             {"augment_id": "B", "comp_id": "c1", "tier": "B"}]}
    d0 = statsdiff.diff_docs(None, new)
    assert d0["patch_changed"] is None and not d0["has_baseline"]
    assert "비교 대상 없음" in statsdiff.render_markdown(d0)
    old = copy.deepcopy(new)
    old["augment_tiers"][1]["tier"] = "C"
    old["augment_tiers"].append({"augment_id": "Z", "comp_id": "c1", "tier": "A"})
    d = statsdiff.diff_docs(old, new)
    assert d["patch_changed"] is False and d["augment_tiers"]["changed"] == {}
    assert d["augment_tiers"]["by_comp"] == [{"comp_id": "c1", "name": "C1", "added": [], "removed": ["Z"],
                                              "changed": {"A": ["C", "S"]}}]
    assert "## 덱별 증강 등급 (변경된 덱 1)" in statsdiff.render_markdown(d)


def test_keep_snapshots_from_settings_or_default():
    from tft_advisor.config import Settings
    assert rf.keep_snapshots(Settings()) == 5
    assert rf.keep_snapshots(Settings.model_validate({"stats": {"keep_snapshots": 9}})) == 9


def test_latest_raw_dir_prefers_newest_complete_cache(tmp_path):
    """stats 08 (c): 최신 날짜 캐시가 불완전(comp_details 누락)이면 더 오래된 완전한 캐시를 고른다."""
    def make(day, details):
        d = tmp_path / day
        d.mkdir()
        (d / "comps_data.json").write_text(json.dumps(
            {"results": {"data": {"cluster_details": {"1": {}, "2": {}}}}}), encoding="utf-8")
        for f in ("units.json", "patch.json"):
            (d / f).write_text("{}", encoding="utf-8")
        for cid in details:
            (d / f"comp_details_{cid}.json").write_text("{}", encoding="utf-8")
        return d

    old = make("2026-01-01", ["1", "2"])
    new = make("2026-01-02", ["1"])
    assert collector.raw_dir_complete(old) and not collector.raw_dir_complete(new)
    assert collector.latest_raw_dir(tmp_path) == old
    assert collector.latest_raw_dir(tmp_path, require_complete=False) == new
    (old / "units.json").unlink()   # 완전한 캐시가 없으면 최신으로 폴백
    assert collector.latest_raw_dir(tmp_path) == new
