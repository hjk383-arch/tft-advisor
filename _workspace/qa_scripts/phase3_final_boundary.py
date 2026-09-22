"""QA Phase 3 최종 게이트: 경계면 E2E + 동시 읽기/refresh + 과금 안전 + 설정 키 전수.

    .venv/bin/python _workspace/qa_scripts/phase3_final_boundary.py [--json OUT]

1. 실제 `open_repository()` + `create_advisor("mock", stats=repo)`:
   - fixture 스크린 7장: recognize_file → GameState 왕복 검증 → advise → Recommendation 왕복 검증 → ID 전수 대조
   - state fixture 전부(steps + branch 포함): mock / off / create_advisor()(auto) 로 advise, 계약·ID 대조
2. 임시 DB 사본에서 writer 스레드가 write_snapshot 반복(보존 정리 포함)하는 동안 reader가 open_repository 반복
3. TYPESAFE_API_KEY(가짜) 설정 + 소켓 가드: create_advisor()/"auto" 기본 설정 → 연결 시도 0, 로그·출력에 키 없음
4. 설정 키 전수: Settings/Weights 모든 키가 src/(config.py 제외)에서 읽히는지
라이브 Jev 호출 없음(가드가 모든 외부 연결을 차단).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

FAKE_KEY = "tsk-QA-FAKE-KEY-9f3b2c71d0e4a5"
os.environ["TYPESAFE_API_KEY"] = FAKE_KEY   # 전 구간 키 설정 상태로 실행(과금 경로 차단 확인)

# ---- 소켓 가드(모든 외부 연결 + DNS 차단·기록) ----
ATTEMPTS: list = []
_orig_connect = socket.socket.connect
_orig_gai = socket.getaddrinfo


def _guard_connect(self, addr, *a, **k):
    if isinstance(addr, tuple) and addr[0] in ("127.0.0.1", "::1", "localhost") or isinstance(addr, str):
        return _orig_connect(self, addr, *a, **k)
    ATTEMPTS.append(("connect", addr))
    raise OSError(f"QA guard: blocked connect {addr!r}")


def _guard_gai(host, *a, **k):
    if host not in (None, "127.0.0.1", "::1", "localhost"):
        ATTEMPTS.append(("dns", host))
        raise OSError(f"QA guard: blocked DNS {host!r}")
    return _orig_gai(host, *a, **k)


socket.socket.connect = _guard_connect
socket.getaddrinfo = _guard_gai

# ---- 로그 전수 캡처(DEBUG) ----
LOG_BUF = io.StringIO()
_h = logging.StreamHandler(LOG_BUF)
_h.setLevel(logging.DEBUG)
logging.getLogger().addHandler(_h)
logging.getLogger().setLevel(logging.DEBUG)

from tft_advisor.advisor.engine import create_advisor  # noqa: E402
from tft_advisor.config import Settings, Weights, load_settings  # noqa: E402
from tft_advisor.contracts import GameState, Recommendation  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.stats import db as statsdb  # noqa: E402
from tft_advisor.stats.repository import open_repository  # noqa: E402

SCREENS = ROOT / "tests" / "fixtures" / "screens"
STATES = ROOT / "tests" / "fixtures" / "states"
PROBLEMS: list[str] = []
OUT: dict = {}

static = load_static()
STATIC_IDS: set[str] = set()
for kind in ("champions", "items", "augments", "traits", "shop_specials"):
    try:
        STATIC_IDS |= {r["apiName"] if isinstance(r, dict) else r.api_name for r in static._load(kind)}  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        PROBLEMS.append(f"static kind {kind} 로드 실패: {e}")
ID_RE = re.compile(r"^(TFT\d*_|DA_|Set\d+_|TFTSet)")


def walk_ids(obj, path="", out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "debug":
                continue
            walk_ids(v, f"{path}.{k}", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_ids(v, f"{path}[{i}]", out)
    elif isinstance(obj, str) and ID_RE.match(obj):
        out.append((path, obj))
    return out


def check_ids(tag: str, dump: dict, comp_ids: set[str]) -> int:
    n = 0
    for p, v in walk_ids(dump):
        n += 1
        if v not in STATIC_IDS:
            PROBLEMS.append(f"{tag}: ID {v} ({p}) static에 없음")
    for tc in dump.get("target_comps", []) or []:
        cid = tc.get("comp_id")
        if cid is not None and cid not in comp_ids:
            PROBLEMS.append(f"{tag}: comp_id {cid} 저장소에 없음")
    return n


def section1_e2e() -> None:
    t = time.perf_counter()
    repo = open_repository()
    t_repo = time.perf_counter() - t
    comp_ids = {c.comp_id for c in repo.comps()}
    OUT["repo"] = {"patch": repo.meta.patch if hasattr(repo, "meta") else None, "comps": len(comp_ids),
                   "load_s": round(t_repo, 2)}
    # --- screens ---
    from tft_advisor.vision.recognizer import Recognizer, recognize_file
    rec = Recognizer()
    adv = create_advisor("mock", stats=repo)
    assert adv.backend_name == "mock"
    rows = []
    for png in sorted(SCREENS.glob("*.png")):
        adv.reset()
        t0 = time.perf_counter()
        st = recognize_file(png, rec)
        tv = time.perf_counter() - t0
        GameState.model_validate(st.model_dump())
        n_state_ids = check_ids(f"screen {png.stem} state", st.model_dump(mode="json"), comp_ids)
        t1 = time.perf_counter()
        r = adv.advise(st)
        ta = time.perf_counter() - t1
        row = {"screen": png.stem, "mode": st.screen_mode.value, "t_vision_s": round(tv, 2),
               "t_advisor_ms": round(ta * 1000, 1), "state_ids": n_state_ids}
        if r is not None:
            d = r.model_dump(mode="json")
            Recommendation.model_validate(r.model_dump())
            row.update(rec_ids=check_ids(f"screen {png.stem} rec", d, comp_ids),
                       comps=[c["comp_id"] for c in d["target_comps"]], jev_used=r.jev_used,
                       shop_buy=[i for i, s in enumerate(d["shop"]) if s.get("buy")],
                       augment=(d["augment"] or {}).get("pick"))
            if not d["target_comps"]:
                PROBLEMS.append(f"screen {png.stem}: target_comps 비어 있음")
        else:
            row["rec"] = None
        rows.append(row)
    OUT["screens"] = rows
    # --- states ---
    srows = []
    advs = {m: create_advisor(m, stats=repo) for m in ("mock", "off")}
    advs["auto(default)"] = create_advisor(stats=repo)
    for p in sorted(STATES.glob("s*.json")):
        fx = json.loads(p.read_text(encoding="utf-8"))
        seqs = {"main": fx.get("steps") or [{"state": fx["state"]}]}
        if fx.get("branch"):
            seqs["branch"] = fx["steps"][:1] + fx["branch"]
        for mode, a in advs.items():
            for sname, steps in seqs.items():
                a.reset()
                for i, step in enumerate(steps):
                    st = GameState.model_validate(step["state"])
                    try:
                        r = a.advise(st)
                    except Exception as e:  # noqa: BLE001
                        PROBLEMS.append(f"state {p.stem}/{sname}/{i} {mode}: 예외 {e!r}")
                        continue
                    if r is None:
                        srows.append((p.stem, sname, i, mode, None))
                        continue
                    d = r.model_dump(mode="json")
                    Recommendation.model_validate(r.model_dump())
                    check_ids(f"state {p.stem}/{sname}/{i} {mode}", d, comp_ids)
                    if mode == "off" and r.jev_used:
                        PROBLEMS.append(f"state {p.stem} off인데 jev_used=True")
                    srows.append((p.stem, sname, i, mode, [c["comp_id"] for c in d["target_comps"]][:1]))
    OUT["states"] = {"runs": len(srows), "none": sum(1 for r in srows if r[4] is None),
                     "files": len(list(STATES.glob("s*.json"))),
                     "auto_backend": advs["auto(default)"].backend_name}
    if advs["auto(default)"].backend_name != "mock":
        PROBLEMS.append(f"create_advisor() 기본 백엔드 = {advs['auto(default)'].backend_name} (mock 기대)")


def section2_concurrency() -> None:
    src_db = Path(load_settings().stats.db_path)
    src_db = src_db if src_db.is_absolute() else ROOT / src_db
    mtime_before = src_db.stat().st_mtime_ns
    tmp = Path(tempfile.mkdtemp(prefix="qa_conc_"))
    try:
        db = tmp / "stats.sqlite"
        shutil.copy2(src_db, db)
        doc = json.loads(sorted((ROOT / "data" / "stats").glob("metatft_*.json"))[-1].read_text(encoding="utf-8"))
        stop = threading.Event()
        w_res = {"writes": 0, "err": [], "times": []}
        r_res = {"reads": 0, "err": [], "comps": set()}

        def writer():
            for k in range(6):
                d = dict(doc)
                d["report"] = dict(doc["report"], fetched_at=f"2026-09-22T12:{k:02d}:00+00:00")
                t = time.perf_counter()
                try:
                    statsdb.write_snapshot(db, d, keep=2)
                    w_res["writes"] += 1
                except Exception as e:  # noqa: BLE001
                    w_res["err"].append(repr(e))
                w_res["times"].append(round(time.perf_counter() - t, 2))
            stop.set()

        def reader():
            while not stop.is_set():
                try:
                    r = open_repository(db_path=db, allow_json_fallback=False)
                    r_res["comps"].add(len(r.comps()))
                    r_res["reads"] += 1
                except Exception as e:  # noqa: BLE001
                    r_res["err"].append(repr(e))

        ths = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(2)]
        for th in ths:
            th.start()
        for th in ths:
            th.join(timeout=600)
        snaps = statsdb.list_snapshots(db)
        OUT["concurrency"] = {"writes": w_res["writes"], "write_s": w_res["times"], "writer_errors": w_res["err"],
                              "reads": r_res["reads"], "reader_errors": r_res["err"][:5],
                              "comp_counts_seen": sorted(r_res["comps"]), "snapshots_left": len(snaps)}
        if w_res["err"] or r_res["err"] or w_res["writes"] != 6 or r_res["reads"] == 0 or len(snaps) != 2:
            PROBLEMS.append(f"동시성: {OUT['concurrency']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 실제 DB는 읽기만 했으므로 mtime 불변이어야 함
    if src_db.stat().st_mtime_ns != mtime_before:
        PROBLEMS.append("실제 stats.sqlite mtime 변경됨(읽기 경로가 씀)")


def section3_billing() -> None:
    from tft_advisor.advisor.engine import advise as module_advise
    before = len(ATTEMPTS)
    a1 = create_advisor()
    a2 = create_advisor("auto")
    st = GameState.model_validate(json.loads((STATES / "s03_2-1_shop_early.json").read_text("utf-8"))["state"])
    r1, r2 = a1.advise(st), a2.advise(st)
    r3 = module_advise(st, "auto")
    # live 백엔드 '생성'만(호출 없음) — 생성 시 로그/네트워크에 키가 새는지 확인
    a_live = create_advisor("live", stats=a1.stats)
    out_text = "\n".join(r.model_dump_json() for r in (r1, r2, r3) if r is not None)
    OUT["billing"] = {"backend_default": a1.backend_name, "backend_auto": a2.backend_name,
                      "jev_used": [getattr(r, "jev_used", None) for r in (r1, r2, r3)],
                      "live_constructed": a_live.backend_name, "net_attempts": len(ATTEMPTS) - before}
    for a in (a1, a2, a_live):
        a.close()
    if a1.backend_name != "mock" or a2.backend_name != "mock":
        PROBLEMS.append(f"과금: 기본 백엔드 {a1.backend_name}/{a2.backend_name}")
    if FAKE_KEY in out_text:
        PROBLEMS.append("과금: Recommendation 출력에 키 포함")


def section4_config_keys() -> None:
    src = {p: p.read_text(encoding="utf-8") for p in (ROOT / "src" / "tft_advisor").rglob("*.py")
           if p.name != "config.py"}
    alltext = "\n".join(src.values())
    unread = []
    for top, model in (("settings", Settings), ("weights", Weights)):
        for sec, f in model.model_fields.items():
            sub = f.annotation
            if not hasattr(sub, "model_fields"):
                continue
            for key in sub.model_fields:
                if not re.search(rf"\.{key}\b|[\"']{key}[\"']", alltext):
                    unread.append(f"{top}.{sec}.{key}")
    OUT["config_unread"] = unread
    # 삭제된 키가 코드/설정에서 '읽히는지'(주석·docstring 제외 대략 확인)
    hits = [f"{p.relative_to(ROOT)}" for p, t in src.items() if re.search(r"\.poll_interval_ms\b|capture\.stable_frames", t)]
    cfg_text = (ROOT / "config" / "settings.toml").read_text(encoding="utf-8")
    live_keys = [ln for ln in cfg_text.splitlines() if re.match(r"\s*(poll_interval_ms|stable_frames)\s*=", ln)]
    OUT["removed_capture_keys_refs"] = {"code_reads": hits, "toml_assignments": live_keys}
    if hits or live_keys:
        PROBLEMS.append(f"삭제된 [capture] 키 참조: {hits} {live_keys}")


def main() -> int:
    t = time.perf_counter()
    section1_e2e()
    section2_concurrency()
    section3_billing()
    section4_config_keys()
    OUT["net_attempts_total"] = ATTEMPTS
    logs = LOG_BUF.getvalue()
    OUT["key_in_logs"] = FAKE_KEY in logs
    if ATTEMPTS:
        PROBLEMS.append(f"네트워크 연결 시도 {len(ATTEMPTS)}: {ATTEMPTS[:5]}")
    if OUT["key_in_logs"]:
        PROBLEMS.append("로그에 API 키 노출")
    OUT["problems"] = PROBLEMS
    OUT["elapsed_s"] = round(time.perf_counter() - t, 1)
    print(json.dumps(OUT, ensure_ascii=False, indent=1, default=str))
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(
            json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"PROBLEMS: {len(PROBLEMS)}")
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    raise SystemExit(main())
