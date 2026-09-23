"""fixture 상황별 end-to-end: GameState → Recommendation (mock Jev, 결정적, 네트워크 없음). 설계 §9."""
from __future__ import annotations

import os
import re

import pytest

from tft_advisor.contracts import FallbackReason, Recommendation
from tft_advisor.advisor import MockJevBackend

from .conftest import fixture_names, load_fixture, to_state

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def run_fixture(name: str, make_advisor, *, branch: bool = False):
    """[(expect, rec, advisor)]. steps가 있으면 순서대로 같은 advisor에 넣는다. branch=True면 steps[0] 뒤 branch 스텝."""
    fx = load_fixture(name)
    jev = fx.get("jev") or {}
    kw = {}
    if jev.get("fail"):
        kw["fail"] = FallbackReason(jev["fail"])
    adv = make_advisor(**kw)
    steps = fx.get("steps") or [{"state": fx["state"], "expect": fx.get("expect", {})}]
    if branch:
        steps = steps[:1] + fx["branch"]
    out = []
    for step in steps:
        rec = adv.advise(to_state(step["state"]))
        out.append((step.get("expect", {}), rec, adv))
    return out


def check_invariants(rec: Recommendation) -> None:
    Recommendation.model_validate(rec.model_dump())
    assert len(rec.target_comps) <= 3
    for t in rec.target_comps:
        assert 0 <= t.score <= 1
    for s in rec.shop:
        assert 0 <= s.score <= 1
    assert rec.state_hash and HEX64.match(rec.state_hash)
    assert rec.latency_ms is not None and rec.latency_ms >= 0
    assert rec.created_at is not None and rec.created_at.tzinfo is not None
    assert rec.jev_used == (rec.fallback_reason is None)
    d = rec.debug
    for key in ("jev_state", "candidates", "question_ids", "questions_version"):
        assert key in d
    # 질문 ID가 state에 없는 인덱스를 참조하지 않는다
    st = d["jev_state"]
    n_comps = len(st.get("candidate_comps", []))
    n_shop = len(st.get("shop", []))
    n_offer = len(st.get("augment_offer", []))
    for qid in d["question_ids"]:
        for m in re.finditer(r"(?:comp_\w+_fit|shop_now|shop_path|special_value|aug_standalone)_(\d+)$", qid):
            idx = int(m.group(1))
            lim = n_comps if qid.startswith("comp_") else n_shop if qid.startswith(("shop", "special")) else n_offer
            assert idx < lim, qid
        if m2 := re.match(r"aug_comp_fit_(\d+)_(\d+)$", qid):
            assert int(m2.group(1)) < n_offer and int(m2.group(2)) < n_comps
    # 로그/디버그에 API 키 문자열이 없다
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        assert key not in rec.model_dump_json()


def _get(d: dict, path: str):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def check_expect(expect: dict, rec: Recommendation, adv, all_recs: dict) -> None:
    tc = rec.target_comps
    top = tc[0].comp_id if tc else None
    st = rec.debug["jev_state"]
    qids = set(rec.debug["question_ids"])
    q = getattr(adv.gateway.backend, "last_questions", None) or {}
    for key, val in expect.items():
        if key == "top_comp":
            assert top == val, (top, [t.comp_id for t in tc])
        elif key == "top_comp_in":
            assert top in val, top
        elif key == "top_comp_differs_from":
            assert top != all_recs[val].target_comps[0].comp_id
        elif key == "top_comp_same_as":
            assert top == all_recs[val].target_comps[0].comp_id
        elif key == "items_ready_status":
            for item, status in val.items():
                got = next(r.status for r in tc[0].items_ready if r.item_id == item)
                assert got == status, (item, got)
        elif key == "all_items_missing":
            assert all(r.status == "missing" for t in tc for r in t.items_ready)
        elif key == "jev_used":
            assert rec.jev_used is val
        elif key == "fallback_reason":
            assert rec.fallback_reason == FallbackReason(val)
        elif key == "latency_ms_max":
            assert rec.latency_ms < val
        elif key == "n_target_min":
            assert len(tc) >= val
        elif key == "n_target_max":
            assert len(tc) <= val
        elif key == "item_is_none":
            assert (rec.item is None) is val
        elif key == "augment_is_none":
            assert (rec.augment is None) is val
        elif key == "shop_empty":
            assert (rec.shop == []) is val
        elif key == "targets_sorted_by_stat":
            cand = {c["comp_id"]: c for c in rec.debug["candidates"]}
            s = [cand[t.comp_id]["S"] for t in tc]
            assert s == sorted(s, reverse=True)
            best_s = max(c["S"] for c in rec.debug["candidates"])
            assert s[0] == best_s
        elif key == "undecided":
            assert (rec.debug["p_undecided"] >= adv.w.comp.undecided_min_p) is val
            assert any("방향 미정" in r for r in tc[0].reasons) is val
        elif key == "hold":
            assert rec.item is not None and rec.item.hold is val
        elif key == "suggestions_min":
            assert rec.item is not None and len(rec.item.suggestions) >= val
        elif key == "slot_score_gt":
            sc = {s.slot: s.score for s in rec.shop}
            for a, b in val:
                assert sc[a] > sc[b], (a, b, sc)
        elif key == "top_slot":
            best = max(rec.shop, key=lambda s: s.score)
            assert best.slot == val, [(s.slot, s.offer_id, s.score) for s in rec.shop]
        elif key == "top_slot_reason_in":
            best = max(rec.shop, key=lambda s: s.score)
            assert best.reason_tag is not None and best.reason_tag.value in val, best
        elif key == "slot_buy":
            for slot, b in val.items():
                assert rec.shop[int(slot)].buy is b
        elif key == "augment_pick":
            assert rec.augment is not None and rec.augment.pick == val, rec.debug.get("augment")
        elif key == "augment_cf_key_trait":
            row = next(r for r in rec.debug["augment"] if r["id"] == rec.augment.pick)
            comp = adv.stats.comp(row["counterfactual_top"])
            assert val in {t.id for t in comp.key_traits}, row
        elif key == "question_absent":
            for qid in val:
                assert qid not in qids, qid
        elif key == "question_absent_prefix":
            for pre in val:
                assert not any(x.startswith(pre) for x in qids), pre
        elif key == "question_text_contains":
            for qid, text in val.items():
                assert text in q[qid]["instructions"], (qid, q[qid]["instructions"])
        elif key == "question_text_absent":
            for qid, text in val.items():
                assert text not in q[qid]["instructions"] and all(
                    text not in str(c) for c in q[qid]["criteria"].values()), qid
        elif key == "state_absent_keys":
            for path in val:
                assert _get(st, path) is None, path
        elif key == "shop_state_absent_keys":
            for e in st["shop"]:
                for k in val:
                    assert k not in e
        elif key == "state_completed_items_contains":
            assert val in st["resources"]["completed_items"], st["resources"]
        elif key == "no_reason_tag":
            assert all(s.reason_tag is None or s.reason_tag.value != val for s in rec.shop)
        elif key == "owned_missing_empty":
            assert all(t.owned_units == [] and t.missing_units == [] for t in tc)
        elif key == "reason_contains":
            assert all(any(val in r for r in t.reasons) for t in tc), [t.reasons for t in tc]
        elif key == "next_buildup_level":
            assert tc[0].next_buildup_board is not None and tc[0].next_buildup_board.level == val
        elif key == "sig_unchanged":
            assert rec.debug["sig_unchanged"] is val
        elif key == "blind_late":
            assert rec.debug["blind_late"] is val
        else:
            raise AssertionError(f"알 수 없는 expect 키: {key}")


@pytest.fixture(scope="module")
def all_recs(stats, settings, weights):
    """단일 스텝 fixture들의 결과(교차 비교용)."""
    from tft_advisor.advisor import Advisor

    out = {}
    for name in fixture_names():
        fx = load_fixture(name)
        if "state" not in fx or fx.get("jev"):
            continue
        adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
        out[name] = adv.advise(to_state(fx["state"]))
    return out


@pytest.mark.parametrize("name", fixture_names())
def test_fixture(name, make_advisor, all_recs):
    results = run_fixture(name, make_advisor)
    for expect, rec, adv in results:
        assert rec is not None
        check_invariants(rec)
        check_expect(expect, rec, adv, all_recs)


def test_s09_branch_new_bis_switches_comp(make_advisor, all_recs):
    for expect, rec, adv in run_fixture("s09_hysteresis", make_advisor, branch=True):
        check_invariants(rec)
        check_expect(expect, rec, adv, all_recs)


def test_same_board_different_items_different_comp(all_recs):
    """설계 §9 핵심 규칙: 같은 보드 + 다른 핵심 아이템 → 다른 최종 덱."""
    a = all_recs["s01_board_ap_items"].target_comps[0]
    b = all_recs["s02_board_ad_items"].target_comps[0]
    assert a.comp_id != b.comp_id
    assert a.carry != b.carry


def test_fixtures_are_deterministic(make_advisor):
    for name in ("s01_board_ap_items", "s05_augment_trait", "s11_mvp_no_hp_board"):
        r1 = run_fixture(name, make_advisor)[-1][1]
        r2 = run_fixture(name, make_advisor)[-1][1]
        strip = lambda r: r.model_dump(exclude={"latency_ms", "created_at", "debug"})   # noqa: E731
        assert strip(r1) == strip(r2)
        assert r1.state_hash == r2.state_hash
