"""스테이지별 보드 통계(MetaTFT Early Comps): 수집기·변환·DB·조회 API. 네트워크 없음.

픽스처: tests/fixtures/stats/metatft_early/ — 2026-09-24 실제 응답(comps_overview + comps_full)을 스테이지당
클러스터 3개로 줄인 녹화본(variations 4개, units 8개, 다음 스테이지 전이는 남긴 클러스터 + 1개).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tft_advisor.patch_version import latest_snapshot
from tft_advisor.static_data import load_static
from tft_advisor.stats import db as statsdb
from tft_advisor.stats import early_convert as ec
from tft_advisor.stats import refresh as rf
from tft_advisor.stats.collectors import metatft_early as col
from tft_advisor.stats.repository import InMemoryStatsRepository, open_repository
from tft_advisor.stats.stage_stats import StageStats, jaccard

FIX = Path(__file__).parent / "fixtures" / "stats" / "metatft_early"
MINI = Path(__file__).parent / "fixtures" / "stats" / "mini_18.json"


@pytest.fixture(scope="module")
def static():
    return load_static(18)


@pytest.fixture(scope="module")
def doc(static):
    return ec.build_early(FIX, static)


@pytest.fixture(scope="module")
def repo(static, doc):
    r = InMemoryStatsRepository.from_json(MINI, static)
    r.attach_stage_doc(doc)
    return r


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ---- ID 매핑 -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("src,want", [
    ("TFT18_Ahri", "DA_18_Ahri"), ("TFT18_Pebbles", "DA_18_Sentry"), ("TFT18_MamaBeak", "DA_CrimsonRaptor18"),
    ("TFT18_Lux_Base", "DA_Lux18_Base"), ("TFT18_Lux_Coven", "DA_18_Lux_Coven"), ("TFT18_Gnar", "DA_18_GnarSmall"),
    ("TFT18_Gromp", "DA_Gromp18_AP"), ("TFT18_Nidalee", "DA_Nidalee18_AP"), ("TFT18_ElderDragon", "DA_18_ElderDragon"),
    ("TFT18_Krug", "DA_Krug18"), ("TFT18_KogMaw", "DA_KogMaw18_AD"), ("DA_18_Zyra", "DA_18_Zyra"),
    ("TFT18_NotAUnit", None),
])
def test_map_unit(static, src, want):
    assert ec.map_unit(src, ec.build_id_map(static), static) == want


def test_fixture_ids_all_map_to_shop_champions(static, doc):
    assert doc["report"]["unmapped_ids"] == []
    ids = {u for b in doc["stage_boards"] for u in b["units"]} | {r["unit_id"] for r in doc["unit_stage_stats"]}
    assert ids and all(static.get("champions", u)["shop_pool"] for u in ids)


# ---- 변환 -------------------------------------------------------------------------------------------
def test_report_and_patch(doc):
    rep = doc["report"]
    assert rep["source"] == "metatft_early" and rep["unit_coverage"] == "full"
    assert rep["clusters"] == 12 and rep["comps_full"] == 12
    # 집계(18:07Z)가 18.3b 시작(18:22Z)보다 앞 → 직전 패치 18.3
    assert rep["patch"] == "18.3" and "before 18.3b" in rep["patch_note"]


@pytest.mark.parametrize("upd,want,note", [
    ("2026-09-24T18:00:00Z", "18.3", True), ("2026-09-24T19:00:00Z", "18.3b", False)])
def test_patch_for_source(upd, want, note):
    import datetime as dt

    ms = dt.datetime.fromisoformat(upd.replace("Z", "+00:00")).timestamp() * 1000
    got, n = ec.patch_for_source({"patch": "18.3", "b_patch_version": "b", "start": "2026-09-24T18:22:05Z"}, ms)
    assert got == want and (n is not None) == note
    got, n = ec.patch_for_source({"patch": "18.4", "b_patch_version": "", "start": "2026-09-24T18:22:05Z"},
                                 dt.datetime(2026, 9, 24, 1, tzinfo=dt.UTC).timestamp() * 1000)
    assert got == "18.4" and "unknown" in n


def test_cluster_rows_use_latest_stats(doc):
    ov = _load("comps_overview.json")["comps_overview"]
    c = ov["stage-3"]["comps"][0]
    row = next(b for b in doc["stage_boards"] if b["stage"] == 3 and b["kind"] == "cluster" and b["cluster"] == c["cluster"])
    ls = c["latest_stats"]
    assert row["games"] == ls["final_place_count"] and row["rounds"] == ls["matchup_count"]
    assert row["avg_place"] == pytest.approx(ls["final_place_avg"], abs=1e-4)
    assert row["win_rate"] == pytest.approx(ls["final_place_winrate"], abs=1e-4)
    assert row["share"] == pytest.approx(ls["matchup_count"] / ov["stage-3"]["latest_stats"]["matchup_count"], abs=1e-4)
    assert row["units"] == sorted(row["units"])


def test_variations_filtered_by_min_games(doc):
    vs = [b for b in doc["stage_boards"] if b["kind"] == "variation"]
    assert vs and all(b["games"] >= ec.VARIATION_MIN_GAMES for b in vs)
    raw = [v for st in (2, 3, 4, 5) for c in _load("comps_overview.json")["comps_overview"][f"stage-{st}"]["comps"]
           for v in _load(f"comps_full_{st}_{c['cluster']}.json")["comps_full"]["variations"]]
    assert len(vs) == sum(round(v["final_place_count"]) >= ec.VARIATION_MIN_GAMES for v in raw)


def test_unit_stage_stats_are_weighted_sums(doc, static):
    """스테이지 3 베이가 = 모든 클러스터 comps_full.units의 성급별 합(가중 평균)."""
    rows = {(r["star"]): r for r in doc["unit_stage_stats"] if r["unit_id"] == "DA_18_Veigar" and r["stage"] == 3}
    g = place = 0.0
    per_star: dict[int, float] = {}
    for c in _load("comps_overview.json")["comps_overview"]["stage-3"]["comps"]:
        for sk, sv in _load(f"comps_full_3_{c['cluster']}.json")["comps_full"]["units"].get("TFT18_Veigar", {}).items():
            n = sv["final_place_count"]
            if n and sv["final_place_avg"] is not None:
                g += n
                place += n * sv["final_place_avg"]
                per_star[int(sk[0])] = per_star.get(int(sk[0]), 0) + n
    assert rows[None]["games"] == round(g)
    assert rows[None]["avg_place"] == pytest.approx(place / g, abs=1e-4)
    assert {s: rows[s]["games"] for s in per_star} == {s: round(v) for s, v in per_star.items()}
    assert 0 < rows[None]["pick_rate"] < 1


def test_transitions_normalized(doc):
    by: dict[tuple, float] = {}
    for t in doc["stage_transitions"]:
        assert t["stage"] in (2, 3, 4) and 0 < t["share"] <= 1
        by[(t["stage"], t["cluster"])] = by.get((t["stage"], t["cluster"]), 0) + t["share"]
    assert by and all(v == pytest.approx(1.0, abs=1e-3) for v in by.values())


def test_round_sizes(doc):
    rs = doc["stage_round_sizes"]
    assert rs and all("-" in r["round"] and r["num_units"] >= 1 for r in rs)


# ---- DB ---------------------------------------------------------------------------------------------
def test_db_roundtrip_dedupe_and_main_hash_unchanged(tmp_path, doc):
    db = tmp_path / "s.sqlite"
    mini = json.loads(MINI.read_text(encoding="utf-8"))
    h_main = statsdb.content_hash(mini)
    m_id = statsdb.write_snapshot(db, mini, source="metatft")
    sid = statsdb.write_snapshot(db, doc, source="metatft_early")
    assert statsdb.write_snapshot(db, doc, source="metatft_early") == sid          # 같은 내용 → 쓰지 않음
    back = statsdb.read_snapshot(db, sid)
    for t in statsdb.STAGE_TABLES:
        assert back[t] == doc[t], t
    assert back["report"]["patch"] == "18.3" and back["comps"] == []
    # 본 스냅샷: 스테이지 테이블이 비어 있으므로 해시 정규형이 v2와 같다(재적재 시 dedupe 유지)
    import sqlite3

    with sqlite3.connect(db) as con:
        assert statsdb._hash_stored(con, m_id) == h_main
    assert "stage_boards" not in statsdb.read_snapshot(db, m_id)


# ---- 조회 API ----------------------------------------------------------------------------------------
def test_stage_of_and_level_mapping(repo):
    st = repo.stage_stats
    assert st.stage_of("3-2") == 3 and st.stage_of(3) == 3 and st.stage_of("1-3") == 2 and st.stage_of(7) == 5
    assert st.stage_of("x") is None and st.stage_of() is None
    assert st.stage_of(level=4) == 2 and st.stage_of(level=6) == 3 and st.stage_of(level=8) in (4, 5)
    assert st.stage_baseline("4-1").stage == 4


def test_level_stage_is_mode_of_board_size():
    """레벨 L → 보드 인원 L이 가장 많이 관측된 스테이지(관측 1000전투 미만이면 기본값)."""
    rows = [{"stage": 4, "round": "4-2", "num_units": 8, "rounds": 5000},
            {"stage": 5, "round": "5-1", "num_units": 8, "rounds": 3000},
            {"stage": 3, "round": "3-2", "num_units": 9, "rounds": 10}]
    st = StageStats.from_doc({"stage_round_sizes": rows, "stage_boards": [
        {"stage": 4, "kind": "cluster", "cluster": "0", "units": ["DA_18_Veigar"], "games": 1}]})
    assert st.stage_of(level=8) == 4 and st.stage_of(level=9) == 5 and st.stage_of(level=11) == 5


def test_boards_for_sort_and_filters(repo):
    bs = repo.boards_for(3, min_games=0)
    assert bs and [b.avg_place for b in bs] == sorted(b.avg_place for b in bs)
    assert all(b.stage == 3 for b in bs)
    assert {b.kind for b in repo.boards_for(3, kind="cluster", min_games=0)} == {"cluster"}
    big = repo.boards_for(3, min_games=5000)
    assert all(b.games >= 5000 for b in big)
    six = repo.boards_for(level=6, min_games=0)
    assert six and all(b.size == 6 and b.stage == 3 for b in six)
    assert repo.boards_for(3, limit=1, min_games=0) == bs[:1]
    b = bs[0]
    assert b.delta == pytest.approx(b.avg_place - repo.stage_stats.baseline[3].avg_place, abs=1e-4)


def test_comp_links(repo):
    st = repo.stage_stats
    # 스테이지 5 베이가 클러스터 → spellweaver-veigar (Jaccard 최대)
    s5 = st.cluster_board(5, "11")
    assert s5.comp_links and s5.comp_links[0][0] == "spellweaver-veigar"
    # 스테이지 2 베이가 보드는 전이(2→3→4→5)로 spellweaver-veigar에 연결된다
    s2 = st.cluster_board(2, "15")
    assert s2.link("spellweaver-veigar") > 0.15
    ids = {(b.stage, b.cluster) for b in repo.boards_for(2, comp_id="spellweaver-veigar", min_games=0)}
    assert (2, "15") in ids
    assert all(b.link("spellweaver-veigar") >= 0.15 for b in repo.boards_for(4, comp_id="spellweaver-veigar",
                                                                               min_games=0))
    for bs in st.boards.values():
        for b in bs:
            assert all(0 < p <= 1.0001 for _, p in b.comp_links)
            assert sum(p for _, p in b.comp_links) <= 1.0001


def test_unit_stage_stat_and_rank_units(repo):
    v = repo.unit_stage_stat("DA_18_Veigar", "3-2")
    v2 = repo.unit_stage_stat("DA_18_Veigar", 3, star=2)
    assert v and v2 and v.stage == 3 and v2.star == 2 and v.games >= v2.games
    assert repo.unit_stage_stat("DA_18_Veigar", level=6) == v
    assert repo.unit_stage_stat("DA_Nope", 3) is None
    ranked = repo.stage_stats.rank_units(["DA_18_Ornn", "DA_18_Veigar", "DA_Nope", "DA_18_Ornn"], 3,
                                         stars={"DA_18_Veigar": 2})
    assert [u for u, _ in ranked][-1] == "DA_Nope" and ranked[-1][1] is None
    assert len(ranked) == 3                                     # 중복 제거
    known = [r for _, r in ranked if r is not None]
    assert [r.delta for r in known] == sorted(r.delta for r in known)
    assert next(r for u, r in ranked if u == "DA_18_Veigar").star == 2
    tab = repo.stage_stats.unit_stage_stats(3, min_games=0)
    assert tab and all(u.star is None and u.stage == 3 for u in tab)


def test_cluster_for_and_transitions(repo):
    st = repo.stage_stats
    board = ["DA_18_Alistar", "DA_18_LeBlanc", "DA_18_Ornn", "DA_18_RekSai", "DA_18_Veigar"]
    b, j = st.cluster_for(board, 3)
    assert b.cluster == "1" and j == pytest.approx(jaccard(board, b.units))
    ts = st.transitions(3, "1", min_games=0)
    assert ts and [t.share for t in ts] == sorted((t.share for t in ts), reverse=True)
    assert st.cluster_for([], 3) is None


def test_empty_stage_stats_is_safe():
    st = StageStats.empty()
    assert not st and st.boards_for(3) == [] and st.unit_stage_stat("DA_18_Veigar", 3) is None
    assert st.cluster_for(["DA_18_Veigar"], 3) is None and st.rank_units(["DA_18_Veigar"], 3) == [("DA_18_Veigar", None)]


# ---- refresh / open_repository / 수집기 --------------------------------------------------------------
def test_refresh_stages_no_fetch_and_open_repository(tmp_path, monkeypatch, static):
    db = tmp_path / "s.sqlite"
    mini = json.loads(MINI.read_text(encoding="utf-8"))
    statsdb.write_snapshot(db, mini, source="metatft")
    r = rf.refresh_stages(fetch=False, raw_dir=FIX, db_path=db, json_dir=tmp_path, static=static)
    assert r.json_path.name == "stage_boards_18.3.json" and not r.skipped_identical and r.notes == []
    assert rf.refresh_stages(fetch=False, raw_dir=FIX, db_path=db, json_dir=tmp_path, static=static).skipped_identical
    # 본 통계 폴백·load가 쓰는 "metatft_" 최신 파일 선택에 stage_boards 파일이 끼지 않는다
    shutil.copy(MINI, tmp_path / "metatft_18.3.json")
    assert latest_snapshot(tmp_path, "metatft_").name == "metatft_18.3.json"
    repo = open_repository(db_path=db, static=static, allow_json_fallback=False)
    assert repo.meta.patch and repo.stage_stats and repo.boards_for(3, min_games=0)
    assert repo.stage_stats.cluster_board(5, "11").comp_links[0][0] == "spellweaver-veigar"
    # 스테이지 스냅샷이 없으면 빈 객체(예외 없음)
    db2 = tmp_path / "only_main.sqlite"
    statsdb.write_snapshot(db2, mini, source="metatft")
    repo2 = open_repository(db_path=db2, static=static, allow_json_fallback=False)
    assert not repo2.stage_stats and repo2.boards_for(3) == []
    assert not open_repository(db_path=db2, static=static, allow_json_fallback=False, stage_stats=False).stage_stats


def test_cli_refresh_stages_no_fetch(tmp_path, monkeypatch, capsys):
    from tft_advisor.stats import __main__ as cli

    monkeypatch.setattr(rf, "STATS_DIR", tmp_path)
    db = tmp_path / "s.sqlite"
    assert cli.main(["refresh-stages", "--no-fetch", "--raw", str(FIX), "--db", str(db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["report"]["unit_coverage"] == "full" and (tmp_path / "stage_boards_18.3.json").is_file()
    assert statsdb.find_snapshot(db, source="metatft_early") is not None


class _FakeFetcher:
    def __init__(self) -> None:
        self.hosts = col.EARLY_HOSTS
        self.interval_s = 1.2
        self.requests = 0
        self.calls: list[tuple[str, dict | None, tuple]] = []

    def get_json(self, path, query=None):
        self.requests += 1
        self.calls.append((path, query, self.hosts))
        if path.endswith("/patch"):
            return _load("patch.json")
        if path == col.OVERVIEW:
            return _load("comps_overview.json")
        return _load(f"comps_full_{query['stage']}_{query['cluster_id']}.json")


def test_collect_early_raw_top_n_and_cache(tmp_path):
    f = _FakeFetcher()
    m = col.collect_early_raw(tmp_path, full=1, fetcher=f)
    assert m["wanted_full"] == 4 and m["comps_full"] == 4 and not m["failures"]
    fulls = [c for c in f.calls if c[0] == col.FULL]
    assert len(fulls) == 4 and {c[1]["stage"] for c in fulls} == {"2", "3", "4", "5"}
    assert all(c[1]["clustering_id"] == "2694" for c in fulls)
    patch_call = next(c for c in f.calls if c[0].endswith("/patch"))
    assert patch_call[2][0].startswith("https://api-hc")            # 패치는 api-hc 호스트
    assert all(c[2] == col.EARLY_HOSTS for c in f.calls if c[0] != patch_call[0])
    # 스테이지별 표본 최대 클러스터가 뽑힌다
    ov = _load("comps_overview.json")["comps_overview"]
    top3 = max(ov["stage-3"]["comps"], key=lambda c: c["latest_stats"]["matchup_count"])["cluster"]
    assert (tmp_path / col.full_name(3, top3)).is_file()
    n = f.requests
    col.collect_early_raw(tmp_path, full=1, fetcher=f)
    assert f.requests == n                                          # 캐시 재사용


def test_fetcher_hosts_and_min_interval():
    from tft_advisor.stats.collectors.metatft import HOSTS, Fetcher

    assert Fetcher().hosts == HOSTS
    f = Fetcher(interval_s=0.2, hosts=col.EARLY_HOSTS)
    assert f.hosts == col.EARLY_HOSTS and f.interval_s == 1.0
