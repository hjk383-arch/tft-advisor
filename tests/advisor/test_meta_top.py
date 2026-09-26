"""메타 상위 N(목표 덱 후보 풀) — 2026-09-25 사용자 규칙 "추천 메타 덱은 상위 5개만". 21 §16.

mini 통계(tests/fixtures/stats/mini_18.json) + fixture 상태, mock Jev(네트워크 없음).
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass

import pytest

from tft_advisor.advisor import Advisor, MockJevBackend
from tft_advisor.advisor.candidates import meta_rank_key, meta_top, prefilter
from tft_advisor.advisor.features import build_view
from tft_advisor.advisor.stage_boards import MetaTftStageBoards
from tft_advisor.config import BoardPlanWeights

from .conftest import fixture_names, load_fixture, to_state, with_overrides

# mini 평균 등수 순(모두 games >= 5000): 4.173 / 4.3028 / 4.3032 / 4.3396 / 4.3411 — 6위 elderdragon 4.4034
MINI_TOP5 = ["executioner-khazix", "spellweaver-veigar", "lunar-aphelios-nidalee_ap", "invoker-ahri",
             "juggernaut-zyra-amumu"]
OUTSIDE = "juggernaut-elderdragon"


def top_n(weights, n: int, floor: int | None = None):
    comp = {"meta_top_n": n} if floor is None else {"meta_top_n": n, "meta_min_games": floor}
    return with_overrides(weights, {"comp": comp})


def with_comps(stats, comps):
    """mini 어댑터 사본(정적 색인·아이템 통계 그대로) + 덱 목록만 바꿈."""
    return dataclasses.replace(stats, _comps=list(comps))


def gs_state(**kw):
    kw.setdefault("screen_mode", "planning")
    return to_state(kw)


# ---------------------------------------------------------------------------
# 순위·표본 하한·N
# ---------------------------------------------------------------------------


def test_default_config_is_top5_with_floor(weights):
    assert weights.comp.meta_top_n == 5 and weights.comp.meta_min_games >= weights.prefilter.min_games


def test_meta_top_ranks_by_avg_place(stats, weights):
    m = meta_top(stats, weights)
    assert [c.comp_id for c in m.comps] == MINI_TOP5 and m.limited and not m.filled
    assert [c.comp_id for c in m.comps] == [c.comp_id for c in sorted(m.comps, key=meta_rank_key)]
    assert OUTSIDE not in m.ids and m.rank(MINI_TOP5[0]) == 1 and m.rank(OUTSIDE) is None
    lines = m.describe()
    assert len(lines) == 5 and "평균 4.173등" in lines[0] and "41,803판" in lines[0]


def test_tiebreak_top4_then_win_rate(stats, weights):
    a = stats.comp("executioner-khazix")
    b = stats.comp("spellweaver-veigar").model_copy(update={"avg_place": a.avg_place, "top4": 0.6})
    a2 = a.model_copy(update={"top4": 0.55})
    assert sorted([a2, b], key=meta_rank_key)[0].comp_id == b.comp_id
    c = a.model_copy(update={"top4": 0.6, "win_rate": 0.2})
    assert sorted([b, c], key=meta_rank_key)[0].comp_id == c.comp_id


def test_min_games_floor_keeps_tiny_samples_out(stats, weights):
    # 표본 3,000판(하한 5,000 미만)인데 평균 3.9등 — 1위처럼 보이지만 순위에 들지 않는다
    lucky = stats.comp(OUTSIDE).model_copy(update={"avg_place": 3.9, "games": 3000})
    s2 = with_comps(stats, [c for c in stats.comps() if c.comp_id != OUTSIDE] + [lucky])
    assert OUTSIDE not in meta_top(s2, weights).ids
    # 하한을 넘으면 1위로 들어온다
    big = lucky.model_copy(update={"games": 6000})
    s3 = with_comps(stats, [c for c in stats.comps() if c.comp_id != OUTSIDE] + [big])
    assert [c.comp_id for c in meta_top(s3, weights).comps][0] == OUTSIDE
    # 하한은 설정값: 2,000으로 낮추면 3,000판 덱도 들어온다
    assert OUTSIDE in meta_top(s2, top_n(weights, 5, floor=2000)).ids


def test_fill_when_too_few_pass_floor(stats, weights, caplog):
    # 하한 50,000판을 넘는 mini 덱은 4개(veigar 60,217 · aphelios 93,343 · ahri 87,650 · elderdragon 111,635)
    w = top_n(weights, 5, floor=50_000)
    m = meta_top(stats, w)
    passed = [c.comp_id for c in m.comps if (c.games or 0) >= 50_000]
    assert len(m.comps) == 5 and len(m.filled) == 5 - len(passed) and set(m.filled).isdisjoint(passed)
    with caplog.at_level(logging.WARNING, logger="tft_advisor.advisor.engine"):
        Advisor(stats=stats, weights=w, backend=MockJevBackend())
    assert any("표본 하한" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("n", [1, 3, 5, 8])
def test_n_is_configurable(stats, weights, settings, n):
    w = top_n(weights, n)
    m = meta_top(stats, w)
    assert len(m.comps) == min(n, len([c for c in stats.comps() if (c.games or 0) >= w.comp.meta_min_games]))
    adv = Advisor(stats=stats, settings=settings, weights=w, backend=MockJevBackend())
    rec = adv.advise(to_state(load_fixture("s01_board_ap_items")["state"]))
    assert {t.comp_id for t in rec.target_comps} <= m.ids
    assert len(rec.debug["jev_state"]["candidate_comps"]) <= n


def test_zero_disables_limit(stats, weights):
    m = meta_top(stats, top_n(weights, 0))
    assert not m.limited and OUTSIDE in m.ids
    assert "lunar-aphelios-kayle" not in m.ids   # prefilter.min_games(1000)는 그대로


# ---------------------------------------------------------------------------
# 후보 풀 = 상위 N (Jev에도 그 목록만)
# ---------------------------------------------------------------------------


def test_prefilter_only_top_n_even_for_previous_shown(stats, weights):
    v = build_view(gs_state(stage="3-2", level=6), stats, 0.6, None)
    cands, pool = prefilter(v, stats, weights, 8, prev_shown=[OUTSIDE])
    assert {c.comp_id for c in cands} == set(MINI_TOP5)
    assert OUTSIDE in {c.comp_id for c in pool}   # S_now·전역 레벨용 전체 풀은 그대로


@pytest.mark.parametrize("name", fixture_names())
def test_fixtures_recommend_only_top_n(name, make_advisor):
    fx = load_fixture(name)
    if fx.get("weights"):
        pytest.skip("fixture가 메타 제한을 바꾼다")
    adv = make_advisor()
    steps = fx.get("steps") or [{"state": fx["state"]}]
    for step in steps:
        rec = adv.advise(to_state(step["state"]))
        if rec is None:
            continue
        assert {t.comp_id for t in rec.target_comps} <= set(MINI_TOP5), name
        if rec.debug.get("mode") != "carousel" and "candidate_comps" in rec.debug.get("jev_state", {}):
            assert len(rec.debug["jev_state"]["candidate_comps"]) <= 5
        assert rec.debug["meta_top"] == MINI_TOP5


def test_no_extra_jev_calls(make_advisor):
    be = MockJevBackend()
    adv = make_advisor(backend=be)
    adv.advise(to_state(load_fixture("s01_board_ap_items")["state"]))
    assert be.calls == 1


# ---------------------------------------------------------------------------
# 고정 덱
# ---------------------------------------------------------------------------


def test_pin_outside_top_n_is_honored_with_note(make_advisor):
    adv = make_advisor()
    adv.set_pinned_comp(OUTSIDE)
    rec = adv.advise(to_state(load_fixture("s01_board_ap_items")["state"]))
    t0 = rec.target_comps[0]
    assert t0.comp_id == OUTSIDE and rec.pinned_comp_id == OUTSIDE
    assert t0.reasons[:2] == ["사용자 고정 덱", "메타 상위 5 밖"]
    assert {t.comp_id for t in rec.target_comps[1:]} <= set(MINI_TOP5)


def test_pin_inside_top_n_has_no_note(make_advisor):
    adv = make_advisor()
    adv.set_pinned_comp(MINI_TOP5[3])
    rec = adv.advise(to_state(load_fixture("s01_board_ap_items")["state"]))
    assert rec.target_comps[0].comp_id == MINI_TOP5[3]
    assert not any("메타 상위" in r for r in rec.target_comps[0].reasons)


def test_pin_kept_when_stats_refresh_drops_it(stats, settings, weights):
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
    pinned = MINI_TOP5[4]
    adv.set_pinned_comp(pinned)
    state = to_state(load_fixture("s01_board_ap_items")["state"])
    rec = adv.advise(state)
    assert rec.target_comps[0].comp_id == pinned and "메타 상위 5 밖" not in rec.target_comps[0].reasons
    # 통계 갱신: 고정 덱 성적이 떨어져 상위 5 밖으로
    worse = stats.comp(pinned).model_copy(update={"avg_place": 4.9})
    adv.stats = with_comps(stats, [worse if c.comp_id == pinned else c for c in stats.comps()])
    assert pinned not in adv.meta.ids and OUTSIDE in adv.meta.ids   # 다시 계산됐다
    rec = adv.advise(state)
    assert rec.target_comps[0].comp_id == pinned and rec.pinned_comp_id == pinned
    assert rec.target_comps[0].reasons[1] == "메타 상위 5 밖"


# ---------------------------------------------------------------------------
# 스테이지 보드: 메타 상위 N으로 이어지는 보드·경로 우선
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Board:
    cluster: str
    units: tuple[str, ...]
    comp_links: tuple[tuple[str, float], ...]
    games: int = 1000
    delta: float | None = 0.0
    avg_place: float | None = 4.5
    kind: str = "cluster"

    def link(self, comp_id: str) -> float:
        return next((p for c, p in self.comp_links if c == comp_id), 0.0)


@dataclass(frozen=True)
class _Trans:
    next_cluster: str
    share: float
    games: float = 500
    avg_place: float | None = 4.5


class _FakeStage:
    def __init__(self, boards3, boards4, trans):
        self.b = {3: boards3, 4: boards4}
        self.trans = trans

    def __bool__(self):
        return True

    def stage_of(self, stage=None, level=None):
        return int(str(stage).split("-")[0]) if stage is not None else 3

    def boards_for(self, s, level=None, kind=None, min_games=0):
        return [b for b in self.b.get(s, []) if b.kind == kind and len(b.units) == level and b.games >= min_games]

    def cluster_for(self, units, s):
        return (self.b[s][0], 1.0)

    def transitions(self, s, cluster, min_games=0):
        return self.trans

    def cluster_board(self, s, cluster):
        return next(b for b in self.b[s] if b.cluster == cluster)


def test_next_hint_prefers_meta_linked_path():
    start = _Board("a", ("U1", "U2"), ())
    popular = _Board("p", ("U1", "U3"), (("off-meta", 0.9),))
    meta = _Board("m", ("U1", "U4"), (("meta-comp", 0.6),))
    st = _FakeStage([start], [popular, meta], [_Trans("p", 0.6), _Trans("m", 0.3)])
    src = MetaTftStageBoards(st, BoardPlanWeights())
    assert src.next_hint(["U1", "U2"], "3-2", 2, None).units == ("U1", "U3")   # 메타 모름 → 가장 많이 간 경로
    src.set_meta(frozenset({"meta-comp"}))
    assert src.next_hint(["U1", "U2"], "3-2", 2, None).units == ("U1", "U4")   # 메타 상위 N으로 이어지는 경로
    # 목표 덱으로 이어지는 경로가 있으면 그게 먼저
    assert src.next_hint(["U1", "U2"], "3-2", 2, "off-meta").units == ("U1", "U3")


def test_best_board_meta_link_breaks_even():
    b1 = _Board("x", ("U1", "U2"), (("off-meta", 1.0),))
    b2 = _Board("y", ("U1", "U3"), (("meta-comp", 1.0),))
    st = _FakeStage([b1, b2], [], [])
    src = MetaTftStageBoards(st, BoardPlanWeights())
    owned = {"U1": 1, "U2": 1, "U3": 1}
    src.set_meta(frozenset({"meta-comp"}))
    assert src.best_board(owned, "3-2", 2, None, 1.0).cluster == "y"
    src.set_meta(frozenset({"off-meta"}))
    assert src.best_board(owned, "3-2", 2, None, 1.0).cluster == "x"


def test_advisor_passes_meta_to_stage_boards(stats, settings, weights):
    class Src(MetaTftStageBoards):
        pass
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend())
    fake = Src(_FakeStage([], [], []), weights.board_plan)
    adv.stage_boards = fake
    adv._meta = None   # 다음 접근 때 다시 계산 → set_meta 호출
    _ = adv.meta
    assert fake.meta == frozenset(MINI_TOP5)
