"""실제 TypeSafe Jev 스모크(기본 skip). 요청 형식·SDK 통합·지연 확인용, 호출 수 최소.

실행: TFT_LIVE_JEV=1 .venv/bin/python -m pytest tests/advisor/test_advisor_live.py -m live -rxs -s
키는 환경변수 TYPESAFE_API_KEY(SDK가 직접 읽음). 호출 수: 4~5회.
"""
from __future__ import annotations

import pytest

from tft_advisor.advisor import Advisor, LiveJevBackend, load_stats

from .conftest import load_fixture, to_state

pytestmark = pytest.mark.live


def _live(stats, settings, weights, **cfg):
    s = settings.model_copy(update={"advisor": settings.advisor.model_copy(update=cfg)}) if cfg else settings
    return Advisor(stats=stats, settings=s, weights=weights, backend=LiveJevBackend(s.advisor))


def _report(tag, rec):
    j = rec.debug.get("jev") or {}
    print(f"\n[live] {tag}: jev_used={rec.jev_used} fb={rec.fallback_reason} q={rec.debug['n_questions']} "
          f"call_ms={j.get('call_ms')} total_ms={rec.latency_ms:.0f} in_tok={j.get('input_tokens')} "
          f"model={j.get('model')} top={[t.comp_id for t in rec.target_comps]}")


def test_live_planning_and_warm_call(stats, settings, weights):
    # 첫 호출은 TLS 연결 수립이 포함되므로 시도 타임아웃을 넉넉히 둔다(지연 측정 목적)
    adv = _live(stats, settings, weights, jev_timeout_s=8.0, jev_retry_budget_s=9.0, timeout_s=10.0)
    st = to_state(load_fixture("s01_board_ap_items")["state"])
    cold = adv.advise(st)
    _report("s01 cold", cold)
    warm = adv.advise(st.model_copy(update={"gold": st.gold + 1}))
    _report("s01 warm", warm)
    adv.close()
    for rec in (cold, warm):
        assert rec.jev_used, rec.debug.get("fallback_detail")
        assert rec.debug["jev"]["input_tokens"] > 0
        assert set(rec.debug["jev"]["scores"]) | set(rec.debug["jev"]["choices"]) == set(rec.debug["question_ids"])


def test_live_augment_select_mvp(stats, settings, weights):
    adv = _live(stats, settings, weights, jev_timeout_s=8.0, jev_retry_budget_s=9.0, timeout_s=10.0)
    rec = adv.advise(to_state(load_fixture("s05_augment_trait")["state"]))
    _report("s05 augment", rec)
    assert rec.jev_used and rec.augment is not None
    rec2 = adv.advise(to_state(load_fixture("s11_mvp_no_hp_board")["state"]))
    _report("s11 mvp", rec2)
    assert rec2.jev_used
    adv.close()


def test_live_full_stats_default_budget(settings, weights):
    """전체 통계(N=8) + 기본 예산(settings.toml 그대로)에서 1회: 예산 안이면 jev_used, 아니면 timeout 폴백."""
    adv = Advisor(stats=load_stats(), settings=settings, weights=weights, backend="live")
    rec = adv.advise(to_state(load_fixture("s01_board_ap_items")["state"]))
    _report("full-stats default budget", rec)
    adv.close()
    assert rec.target_comps
