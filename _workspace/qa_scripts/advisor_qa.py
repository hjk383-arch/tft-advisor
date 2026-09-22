"""QA 04 advisor: 실제 open_repository() 연동, 결측/폴백 매트릭스, 모드 계약, 히스테리시스, 결정성.

실행: .venv/bin/python _workspace/qa_scripts/advisor_qa.py [--json out.json]
네트워크 없음(mock/off 백엔드만). TYPESAFE_API_KEY 값은 읽지 않는다.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tft_advisor.advisor import Advisor, JsonStatsAdapter, MockJevBackend  # noqa: E402
from tft_advisor.advisor.jev_client import JevGateway  # noqa: E402
from tft_advisor.config import load_settings, load_weights  # noqa: E402
from tft_advisor.contracts import FallbackReason, GameState, Recommendation, ScreenMode  # noqa: E402
from tft_advisor.stats.repository import open_repository  # noqa: E402

STATES = ROOT / "tests" / "fixtures" / "states"
MINI = ROOT / "tests" / "fixtures" / "stats" / "mini_18.json"
settings, weights = load_settings(), load_weights()
OUT: dict = {}
FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)
        print("  FAIL:", msg)


def invariants(rec: Recommendation, tag: str) -> None:
    Recommendation.model_validate(rec.model_dump())
    check(rec.jev_used == (rec.fallback_reason is None), f"{tag}: jev_used/fallback 불일치")
    check(1 <= len(rec.target_comps) <= 3, f"{tag}: target_comps 수 {len(rec.target_comps)}")
    for t in rec.target_comps:
        check(0 <= t.score <= 1, f"{tag}: score 범위")


def fixture_steps(name: str) -> list[dict]:
    fx = json.loads((STATES / f"{name}.json").read_text())
    return fx.get("steps") or [{"state": fx["state"], "expect": fx.get("expect", {})}]


def names() -> list[str]:
    return sorted(p.stem for p in STATES.glob("s*.json"))


# ---------------------------------------------------------------------------
# 1. 실제 저장소
# ---------------------------------------------------------------------------
def real_repo_section() -> None:
    print("== 1. open_repository() 전 fixture ==")
    t0 = time.perf_counter()
    repo = open_repository()
    OUT["repo_load_s"] = round(time.perf_counter() - t0, 3)
    OUT["repo_patch"] = getattr(repo.meta, "patch", None)
    OUT["repo_n_comps"] = len(repo.comps())
    rows = []
    non_craftable_marked = []
    for backend in ("mock", "off"):
        for nm in names():
            adv = Advisor(stats=repo, settings=settings, weights=weights, backend=backend)
            for i, step in enumerate(fixture_steps(nm)):
                st = GameState.model_validate(step["state"])
                rec = adv.advise(st)
                tag = f"real/{backend}/{nm}#{i}"
                if rec is None:
                    check(False, f"{tag}: None")
                    continue
                invariants(rec, tag)
                for t in rec.target_comps:
                    for r in t.items_ready:
                        if r.status == "craftable" and not repo.is_craftable(r.item_id):
                            non_craftable_marked.append((tag, t.comp_id, r.item_id))
                for cid in rec.component_priority:
                    check(repo.is_component(cid), f"{tag}: component_priority 비재료 {cid}")
                if backend == "mock":
                    rows.append({
                        "fixture": f"{nm}#{i}", "mode": st.screen_mode.value,
                        "targets": [(t.comp_id, round(t.score, 3)) for t in rec.target_comps],
                        "shop_top": (max(rec.shop, key=lambda s: s.score).offer_id if rec.shop else None),
                        "aug_pick": rec.augment.pick if rec.augment else None,
                        "item_top": (rec.item.suggestions[0].item_id if rec.item and rec.item.suggestions else None),
                        "hold": rec.item.hold if rec.item else None,
                        "latency_ms": round(rec.latency_ms, 1), "fallback": rec.fallback_reason,
                        "n_q": rec.debug["n_questions"],
                    })
            adv.close()
    check(not non_craftable_marked, f"제작 불가 BIS가 craftable: {non_craftable_marked[:5]}")
    OUT["real_rows"] = rows
    OUT["non_craftable_marked"] = non_craftable_marked
    for r in rows:
        print(" ", r["fixture"], r["targets"], "shop:", r["shop_top"], "aug:", r["aug_pick"], "item:", r["item_top"],
              "hold:", r["hold"], f'{r["latency_ms"]}ms', "q", r["n_q"])


# ---------------------------------------------------------------------------
# 2. 결측/폴백 매트릭스
# ---------------------------------------------------------------------------
def base_state() -> dict:
    return json.loads((STATES / "s01_board_ap_items.json").read_text())["state"]


def variants() -> dict[str, dict]:
    b = base_state()
    out = {"base": b}
    for f in ("hp", "board", "bench", "items", "shop", "level", "stage", "gold", "augments_owned", "active_traits"):
        v = copy.deepcopy(b)
        if f in v:
            v[f] = None
        out[f"{f}=None"] = v
    v = copy.deepcopy(b); v["hp"] = v["board"] = v["bench"] = v["active_traits"] = None; out["mvp(hp,board,bench)"] = v
    v = copy.deepcopy(b)
    for f in ("hp", "board", "bench", "items", "shop", "level", "stage", "gold", "xp", "streak", "shop_odds",
              "active_traits", "augments_owned"):
        v[f] = None
    out["all_None"] = v
    v = copy.deepcopy(b); v.setdefault("confidence", {}); v["confidence"].update({"items": 0.1, "board": 0.1, "hp": 0.1})
    out["low_conf(items,board,hp)"] = v
    # 장착 아이템만 있는 보드(벤치 아이템 없음)
    v = copy.deepcopy(b)
    if v.get("board"):
        v["board"][0]["items"] = ["DA_ArchangelsStaff"]
    v["items"] = {"completed": [], "components": []}
    out["equipped_only"] = v
    a = json.loads((STATES / "s05_augment_trait.json").read_text())["state"]
    out["augment_select"] = a
    v = copy.deepcopy(a); v["hp"] = v["board"] = v["bench"] = v["items"] = None; out["augment_select_mvp"] = v
    return out


def matrix_section(stats) -> None:
    print("== 2. 결측 × 폴백 매트릭스 ==")
    reasons = [None] + list(FallbackReason)
    results = {}
    for vname, raw in variants().items():
        try:
            st = GameState.model_validate(raw)
        except Exception as e:  # noqa: BLE001
            check(False, f"{vname}: GameState 검증 실패 {e}")
            continue
        for rsn in reasons:
            if rsn is FallbackReason.JEV_DISABLED:
                adv = Advisor(stats=stats, settings=settings, weights=weights, backend="off")
            elif rsn is FallbackReason.CIRCUIT_OPEN:
                adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
                adv.gateway._open_until = adv.gateway.clock() + 999
            else:
                adv = Advisor(stats=stats, settings=settings, weights=weights,
                              backend=MockJevBackend(fail=rsn) if rsn else MockJevBackend())
            tag = f"{vname}/{rsn.value if rsn else 'jev'}"
            try:
                rec = adv.advise(st)
            except Exception as e:  # noqa: BLE001
                check(False, f"{tag}: 예외 {type(e).__name__}: {e}")
                continue
            if rec is None:
                check(False, f"{tag}: None")
                continue
            invariants(rec, tag)
            want = rsn
            check(rec.fallback_reason == want, f"{tag}: reason {rec.fallback_reason} != {want}")
            if rsn is not None:
                check(all(any("Jev 미사용" in r for r in t.reasons) or len(t.reasons) == 5 for t in rec.target_comps),
                      f"{tag}: 'Jev 미사용' reason 누락")
            results[tag] = "ok"
    OUT["matrix_n"] = len(results)
    print("  combos ok:", len(results))


# ---------------------------------------------------------------------------
# 2b. 타임아웃·예산
# ---------------------------------------------------------------------------
def timeout_section(stats) -> None:
    print("== 2b. 타임아웃/예산 ==")
    st = GameState.model_validate(base_state())
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend(delay_s=5.0))
    t0 = time.perf_counter()
    rec = adv.advise(st)
    el = time.perf_counter() - t0
    OUT["timeout_elapsed_s"] = round(el, 3)
    check(rec.fallback_reason == FallbackReason.TIMEOUT, f"timeout reason {rec.fallback_reason}")
    check(el < settings.advisor.timeout_s, f"타임아웃 경로 {el:.2f}s ≥ timeout_s {settings.advisor.timeout_s}")
    print(f"  delay 5s → {rec.fallback_reason} in {el:.2f}s (budget {settings.advisor.jev_retry_budget_s}s)")
    # 서킷: 연속 3회 → circuit_open, 이후 호출 안 함
    be = MockJevBackend(fail=FallbackReason.SERVER_ERROR)
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=be)
    rs = []
    for g in range(5):
        rs.append(adv.advise(st.model_copy(update={"gold": 10 + g})).fallback_reason.value)
    OUT["circuit_seq"] = rs
    check(rs[:3] == ["server_error"] * 3 and rs[3:] == ["circuit_open"] * 2 and be.calls == 3, f"서킷 {rs} calls={be.calls}")
    print("  circuit:", rs, "calls", be.calls)


# ---------------------------------------------------------------------------
# 3. 모드 계약
# ---------------------------------------------------------------------------
def mode_section(stats) -> None:
    print("== 3. 모드 계약 ==")
    be = MockJevBackend()
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=be)
    base = base_state()
    st = GameState.model_validate(base)
    # 직전 추천 없을 때
    for m in ("combat", "item_select", "unknown"):
        check(adv.advise(st.model_copy(update={"screen_mode": ScreenMode(m)})) is None, f"{m} (no prev) != None")
    car0 = adv.advise(st.model_copy(update={"screen_mode": ScreenMode.CAROUSEL}))
    check(car0 is not None and car0.shop == [], "carousel(no prev): planning 경로(상점 제외) 아님")
    calls0 = be.calls
    adv.reset()
    first = adv.advise(st)
    for m in ("combat", "item_select", "unknown"):
        check(adv.advise(st.model_copy(update={"screen_mode": ScreenMode(m)})) is first, f"{m} != previous object")
    c1 = be.calls
    car = adv.advise(st.model_copy(update={"screen_mode": ScreenMode.CAROUSEL}))
    check(be.calls == c1, "carousel이 Jev 호출")
    check(car.target_comps == first.target_comps and car.shop == first.shop, "carousel이 덱/상점 변경")
    check(car.state_hash == first.state_hash and car.jev_used == first.jev_used, "carousel 메타 불일치")
    # 캐러셀 후 combat → 캐러셀 사본을 돌려준다?
    after = adv.advise(st.model_copy(update={"screen_mode": ScreenMode.COMBAT}))
    OUT["combat_after_carousel_is_first"] = after is first
    for m in ("loading", "game_over"):
        adv.advise(st)
        r = adv.advise(st.model_copy(update={"screen_mode": ScreenMode(m)}))
        check(r is None and adv.session.last is None and adv.session.prev_shown == [] and not adv.gateway._cache,
              f"{m}: reset 안 됨")
    # augment_select → shop=[] and augment set
    a = GameState.model_validate(json.loads((STATES / "s05_augment_trait.json").read_text())["state"])
    ra = adv.advise(a)
    check(ra.shop == [] and ra.augment is not None, "augment_select 출력 형태")
    print("  ok; combat after carousel returns first:", OUT["combat_after_carousel_is_first"], "calls", calls0)


# ---------------------------------------------------------------------------
# 1b. 히스테리시스 / s09 / 상징
# ---------------------------------------------------------------------------
def hysteresis_section(stats) -> None:
    print("== 1b. 히스테리시스 ==")
    steps = json.loads((STATES / "s09_hysteresis.json").read_text())
    s1 = GameState.model_validate(steps["steps"][0]["state"])
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
    r1 = adv.advise(s1)
    shown = [(t.comp_id, round(t.score, 4)) for t in r1.target_comps]
    OUT["s09_step1_shown"] = shown
    print("  s09 step1 shown:", shown)
    # 단일 BIS 추가 브랜치 (설계 문구: 완성템 1개)
    single = {}
    for extra in ("DA_StrikersFlail", "DA_18_EmblemInvoker"):
        raw = copy.deepcopy(steps["steps"][0]["state"])
        raw["items"]["completed"].append({"id": extra})
        a2 = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
        a2.advise(s1)
        r = a2.advise(GameState.model_validate(raw))
        single[extra] = [(t.comp_id, round(t.score, 4)) for t in r.target_comps]
    OUT["s09_single_item"] = single
    print("  single item branch:", single)

    # 1위 흔들림 실험: prev 1·2위 모두 표시된 상태에서, 시그니처 불변 변화로 2위가 근소 추월하면?
    # 합성: Scorer 결과를 직접 조작하지 않고, mock override로 C3 board_fit만 흔든다.
    raw = copy.deepcopy(steps["steps"][0]["state"])
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
    ra = adv.advise(GameState.model_validate(raw))
    top2 = [t.comp_id for t in ra.target_comps[:2]]
    rows = {c["comp_id"]: c for c in ra.debug["candidates"]}
    OUT["hyst_prev_shown"] = top2
    if len(top2) == 2:
        gap = rows[top2[0]]["final"] - rows[top2[1]]["final"]
        k2 = rows[top2[1]]
        # 2위 덱의 item_fit 답을 살짝 올려 1위를 추월시킨다(시그니처는 불변: 같은 state, gold만 +1)
        idx = next(i for i, c in enumerate(ra.debug["candidates"]) if c["comp_id"] == top2[1])
        base_norm = k2["terms"]["item"]["norm"]
        bump = min(1.0, base_norm + (gap + 0.02) / (weights.comp.wi * (1 - weights.comp.wt)))
        adv.gateway.backend.overrides = {f"comp_item_fit_{idx}": bump}
        rb = adv.advise(GameState.model_validate({**raw, "gold": (raw.get("gold") or 0) + 1}))
        OUT["hyst_flip"] = {"gap": round(gap, 4), "bump_to": round(bump, 3),
                            "new_order": [(t.comp_id, round(t.score, 4)) for t in rb.target_comps],
                            "sig_unchanged": rb.debug["sig_unchanged"],
                            "H": {c["comp_id"]: c["H"] for c in rb.debug["candidates"] if c["H"]}}
        print("  flip test:", OUT["hyst_flip"])


# ---------------------------------------------------------------------------
# 5. 결정성
# ---------------------------------------------------------------------------
def determinism_digest(stats) -> str:
    h = hashlib.sha256()
    for nm in names():
        adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
        for step in fixture_steps(nm):
            rec = adv.advise(GameState.model_validate(step["state"]))
            d = rec.model_dump(mode="json", exclude={"latency_ms", "created_at", "debug"})
            h.update(json.dumps(d, sort_keys=True).encode())
            h.update(json.dumps(rec.debug.get("question_ids")).encode())
    return h.hexdigest()


def real_expect_section() -> None:
    """tests의 expect 규칙을 실제 저장소 결과에 적용(정보용: mini 기준 기대값이 실데이터에서도 성립하는가)."""
    print("== 4b. fixture expect on real repo ==")
    sys.path.insert(0, str(ROOT))
    from tests.advisor.test_advisor_fixtures import check_expect  # noqa: E402
    repo = open_repository()
    all_recs, res = {}, {}
    for nm in names():
        fx = json.loads((STATES / f"{nm}.json").read_text())
        if "state" in fx and not fx.get("jev"):
            a = Advisor(stats=repo, settings=settings, weights=weights, backend=MockJevBackend())
            all_recs[nm] = a.advise(GameState.model_validate(fx["state"]))
    for nm in names():
        fx = json.loads((STATES / f"{nm}.json").read_text())
        kw = {"fail": FallbackReason(fx["jev"]["fail"])} if fx.get("jev") else {}
        a = Advisor(stats=repo, settings=settings, weights=weights, backend=MockJevBackend(**kw))
        for i, step in enumerate(fixture_steps(nm)):
            rec = a.advise(GameState.model_validate(step["state"]))
            bad = []
            for k, v in (step.get("expect") or {}).items():
                try:
                    check_expect({k: v}, rec, a, all_recs)
                except AssertionError as e:  # noqa: PERF203
                    bad.append(f"{k}: {str(e)[:120]}")
                except Exception as e:  # noqa: BLE001
                    bad.append(f"{k}: {type(e).__name__} {str(e)[:80]}")
            res[f"{nm}#{i}"] = bad
            print(" ", f"{nm}#{i}", "OK" if not bad else bad)
    OUT["real_expect"] = res


def main() -> None:
    mini = JsonStatsAdapter.from_file(MINI)
    if "--digest" in sys.argv:
        print(determinism_digest(mini))
        return
    real_repo_section()
    real_expect_section()
    matrix_section(mini)
    timeout_section(mini)
    mode_section(mini)
    hysteresis_section(mini)
    OUT["digest_mini"] = determinism_digest(mini)
    OUT["fails"] = FAILS
    print("== FAILS:", len(FAILS))
    for f in FAILS:
        print("  ", f)
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
