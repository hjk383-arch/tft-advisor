"""QA(Phase 3) stats 경계 회귀: 원본 MetaTFT ↔ repository(R16 holds/builds 분리), 읽기 전용 DB, advisor 어댑터 동등성.

네트워크 없음. data/raw/metatft 최신 캐시를 tmp DB로 변환해 쓴다(캐시 없으면 skip).
"""
from __future__ import annotations

import json
import os
import stat
from collections import Counter

import pytest

from tft_advisor.stats import refresh as rf
from tft_advisor.stats.collectors.metatft import latest_raw_dir
from tft_advisor.stats.repository import open_repository

RAW = latest_raw_dir()
pytestmark = pytest.mark.skipif(RAW is None, reason="MetaTFT 원본 캐시 없음(data/raw/metatft)")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("qa_stats")
    return rf.refresh(fetch=False, raw_dir=RAW, db_path=d / "stats.sqlite", json_dir=d, diff_out=None)


@pytest.fixture(scope="module")
def repo(built):
    return open_repository(db_path=built.db_path, allow_json_fallback=False)


def _details():
    for p in sorted(RAW.glob("comp_details_*.json")):
        yield json.loads(p.read_text(encoding="utf-8"))["results"]


def test_holds_rows_equal_raw_itemnames_units(repo):
    """unit_item_stat(u, x, comp) == 원본 itemNames[x].units[u] (games/avg/place_change), 행 수 일치, 키 중복 0."""
    by_cluster = {c.source_cluster_id: c.comp_id for c in repo.comps()}
    n = 0
    for det in _details():
        cid = by_cluster[str(det["cluster"])]
        seen = Counter()
        for it in det.get("itemNames") or []:
            for u in it.get("units") or []:
                n += 1
                seen[(u["units"], it["itemNames"])] += 1
                r = repo.unit_item_stat(u["units"], it["itemNames"], cid)
                assert r is not None and r.comp_id == cid, (cid, u["units"], it["itemNames"])
                assert (r.games, r.avg_place, r.place_change) == (u.get("count"), u.get("avg"), u.get("place_change"))
        assert max(seen.values(), default=1) == 1
    assert n == repo.meta.counts["unit_item_stats"]


def test_build_rows_equal_raw_builds(repo):
    by_cluster = {c.source_cluster_id: c.comp_id for c in repo.comps()}
    n = 0
    for det in _details():
        cid = by_cluster[str(det["cluster"])]
        for b in det.get("builds") or []:
            items = list(b.get("buildName") or [])
            if not items or len(items) > 3 or not b.get("unit"):
                continue
            n += 1
            rows = [r for r in repo.unit_builds(b["unit"], cid) if r.item_ids == items]
            assert len(rows) == 1 and rows[0].games == b.get("count") and rows[0].place_change == b.get("place_change")
    assert n == repo.meta.counts["unit_build_stats"]


def test_holds_are_single_item_and_scoped(built):
    doc = json.loads(built.json_path.read_text(encoding="utf-8"))
    assert all(len(r["item_ids"]) == 1 and r.get("comp_id") for r in doc["unit_item_stats"])
    assert all(r.get("comp_id") for r in doc["unit_build_stats"])


def test_open_repository_on_readonly_db(built, tmp_path):
    ro = tmp_path / "ro.sqlite"
    ro.write_bytes(built.db_path.read_bytes())
    os.chmod(ro, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        r = open_repository(db_path=ro, allow_json_fallback=False)
        assert len(r.comps()) == 57
    finally:
        os.chmod(ro, stat.S_IRUSR | stat.S_IWUSR)


def test_repository_matches_advisor_json_adapter(repo, built):
    """advisor 테스트는 JsonStatsAdapter, 실행은 repository를 쓴다 → advisor가 부르는 조회는 두 구현이 같아야 한다."""
    sm = pytest.importorskip("tft_advisor.advisor.stats_source")
    ad = sm.JsonStatsAdapter.from_file(built.json_path, repo.static)
    assert isinstance(repo, sm.AdvisorStats)
    assert {c.comp_id for c in repo.comps(min_games=1000)} == {c.comp_id for c in ad.comps(min_games=1000)}
    doc = json.loads(built.json_path.read_text(encoding="utf-8"))
    for t in doc["augment_tiers"][:500]:
        assert repo.augment_tier(t["augment_id"], t.get("comp_id")) == ad.augment_tier(t["augment_id"], t.get("comp_id"))
    for r in doc["unit_item_stats"][::7]:
        x = (r["unit_id"], r["item_ids"][0], r["comp_id"])
        assert repo.unit_item_stat(*x) == ad.unit_item_stat(*x)
    comps = repo.components()
    for i, a in enumerate(comps):
        for b in comps[i:]:
            assert repo.craft(a, b) == ad.craft(a, b)
    for it in repo.static.tables["items"]:
        x = it["apiName"]
        if x.startswith("DA_"):
            ra, rb = repo.recipe(x), ad.recipe(x)
            assert (ra and sorted(ra)) == (rb and sorted(rb)), x
        if it.get("category") == "emblem":
            assert repo.emblem_trait(x) == ad.emblem_trait(x)
    assert all(repo.shop_odds(lv) == ad.shop_odds(lv) for lv in range(1, 11))
