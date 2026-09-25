"""스테이지별 실제 보드 통계(MetaTFT Early Comps) → 보드 배치(`advisor.stage_boards`, `board_plan`). 21 §11.

픽스처: tests/fixtures/stats/metatft_early/(2026-09-24 녹화 응답, 스테이지당 클러스터 3개) + mini 통계. 네트워크 없음.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tft_advisor.advisor import Advisor, MockJevBackend
from tft_advisor.advisor.board_plan import _next_note, plan_board
from tft_advisor.advisor.features import build_view
from tft_advisor.advisor.stage_boards import BoardPick, MetaTftStageBoards, NextHint, UnitSignal, vs_baseline
from tft_advisor.config import BoardPlanWeights
from tft_advisor.contracts import GameState
from tft_advisor.static_data import load_static
from tft_advisor.stats import early_convert as ec
from tft_advisor.stats.repository import InMemoryStatsRepository
from tft_advisor.stats.stage_stats import StageStats

TESTS = Path(__file__).resolve().parents[1]
FIX = TESTS / "fixtures" / "stats" / "metatft_early"
MINI = TESTS / "fixtures" / "stats" / "mini_18.json"

SPELL = ["DA_18_Alistar", "DA_18_LeBlanc", "DA_18_Ornn", "DA_18_RekSai", "DA_18_Veigar"]     # 3스테이지 1위 보드
COVEN = ["DA_18_Caitlyn", "DA_18_Camille", "DA_18_Cassiopeia", "DA_18_Elise", "DA_18_Rakan"]  # 3스테이지 다른 보드
LUNAR = "lunar-aphelios-nidalee_ap"   # COVEN 보드의 연결 0.72, SPELL 보드는 0


@pytest.fixture(scope="module")
def repo():
    static = load_static(18)
    r = InMemoryStatsRepository.from_json(MINI, static)
    r.attach_stage_doc(ec.build_early(FIX, static))
    return r


@pytest.fixture(scope="module")
def src(repo):
    s = MetaTftStageBoards.from_stats(repo, BoardPlanWeights())
    assert s is not None
    return s


def gs(stage: str, level: int, board: list[str], bench: list[str], star: int = 1) -> GameState:
    def us(ids):
        return [{"id": u, "star": star, "confidence": 0.9} for u in ids]
    return GameState.model_validate({
        "screen_mode": "planning", "stage": stage, "level": level, "gold": 20, "hp": 60,
        "board": us(board), "bench": us(bench), "confidence": {"board": 0.9, "bench": 0.9}})


# ---------------------------------------------------------------------------
# 신호
# ---------------------------------------------------------------------------


def test_unit_signal_is_shrunk_stage_delta(repo, src):
    st = repo.stage_stats
    row = st.unit_stage_stat("DA_18_Veigar", 3)
    sig = src.unit_signal("DA_18_Veigar", None, "3-2", 5)
    assert isinstance(sig, UnitSignal) and sig.stage == 3 and sig.games == row.games
    w = BoardPlanWeights()
    shrunk = row.delta * row.games / (row.games + w.stage_shrink_k)
    assert sig.value == pytest.approx(max(-1, min(1, -shrunk / w.stage_delta_span)))
    # delta 음수 = 좋다 → "평균보다 …등 높음"(절대 등수로 읽히는 "평균 등수 −0.39"가 아니다, 27 W4)
    assert sig.value > 0 and sig.reason() == f"통계: 3스테이지 · 평균보다 {-row.delta:.2f}등 높음({row.games:,}판)"
    assert "평균 등수" not in sig.reason() and "−" not in sig.reason()
    # 표본 수축: 같은 delta라도 표본이 적으면 신호가 약하다
    small = src._quality(row.delta, 50)
    assert abs(small) < abs(sig.value)
    # 성급별 행을 먼저 쓰고, 표본이 모자라면 전 성급 행
    s2 = src.unit_signal("DA_18_Veigar", 2, "3-2", 5)
    r2 = st.unit_stage_stat("DA_18_Veigar", 3, star=2)
    if r2 is not None and r2.games >= w.stage_unit_min_games:
        assert s2.star == 2 and s2.games == r2.games
    else:
        assert s2.star is None
    assert src.unit_signal("DA_NoSuchUnit", 1, "3-2", 5) is None


def test_best_board_reachable_with_owned_units(src):
    owned = {u: 1 for u in SPELL[:4] + ["DA_18_Shen"]}             # 베이가만 없다
    pick = src.best_board(owned, "3-2", 5, None, 1.0)
    assert isinstance(pick, BoardPick) and pick.kind == "variation"
    assert list(pick.units) == sorted(SPELL) and set(pick.owned) == set(SPELL[:4])
    assert pick.games == 7973 and pick.delta < 0
    # 아무것도 겹치지 않으면(허용 없는 유닛 수 초과) None
    assert src.best_board({"DA_18_Zyra": 1}, "3-2", 5, None, 1.0) is None
    assert src.best_board(owned, None, None, None, 1.0) is None


def test_best_board_prefers_target_deck_link_when_committed(src):
    owned = {u: 1 for u in SPELL + COVEN}
    early = src.best_board(owned, "3-2", 5, LUNAR, 0.0)             # 초반: 목표 덱 연결 무시 → 지금 가장 강한 보드
    assert set(early.units) == set(SPELL)
    strong_link = MetaTftStageBoards(src.st, BoardPlanWeights(board_link=3.0))
    late = strong_link.best_board(owned, "3-2", 5, LUNAR, 1.0)
    assert len(set(late.units) & set(COVEN)) >= 4 and late.link > 0.5   # 목표 덱으로 이어지는 COVEN 계열 보드


def test_next_hint_follows_transitions(repo, src):
    lineup = ["DA_18_Kobuko", "DA_18_RekSai", "DA_18_Teemo", "DA_18_Veigar"]   # 2스테이지 클러스터 15
    nh = src.next_hint(lineup, "2-5", 4, None)
    assert isinstance(nh, NextHint) and nh.stage == 3
    assert nh.units == repo.stage_stats.cluster_board(3, "1").units and nh.share == pytest.approx(0.676, abs=1e-3)
    assert nh.match == 1.0 and nh.close
    assert src.next_hint(lineup, "5-1", 9, None) is None                      # 스테이지 5 다음은 없다


def test_vs_baseline_wording():
    """delta는 기준선 대비 차이(음수가 좋다). 절대 평균 등수로 읽히지 않게 '평균보다'를 붙이고 방향을 말로 쓴다."""
    assert vs_baseline(-0.11) == "평균보다 0.11등 높음"
    assert vs_baseline(0.27) == "평균보다 0.27등 낮음"
    assert vs_baseline(0.001) == "평균과 비슷함"
    sig = UnitSignal(value=0.2, delta=-0.11, games=1234, stage=2, star=1)
    assert sig.reason() == "통계: 2스테이지 1성 · 평균보다 0.11등 높음(1,234판)"
    assert UnitSignal(value=-0.3, delta=0.2, games=80, stage=4, star=None).reason() == "통계: 4스테이지 · 평균보다 0.20등 낮음(80판)"


def test_next_hint_wording_depends_on_how_well_the_lineup_matches(src):
    """27 W4: 유닛 1기만 겹친 클러스터로 "이 보드는 보통…"이라 말하지 않는다."""
    ko = lambda u: u   # noqa: E731
    close = src.next_hint(["DA_18_Kobuko", "DA_18_RekSai", "DA_18_Teemo", "DA_18_Zyra", "DA_18_Ahri"], "2-5", 4, None)
    assert close is not None and close.match == pytest.approx(0.5) and close.close
    assert "이 보드는 보통" in _next_note(close, [], ko)
    loose = src.next_hint(["DA_18_Kobuko", "DA_18_RekSai", "DA_18_Zyra"], "2-5", 4, None)   # 2/5 = 0.4
    assert loose is not None and loose.match == pytest.approx(0.4) and not loose.close
    note = _next_note(loose, [], ko)
    assert "비슷한 보드는 보통" in note and "이 보드는" not in note
    # 1기만 겹침(1/7): 힌트 없음
    assert src.next_hint(["DA_18_Kobuko", "DA_18_Zyra", "DA_18_Ahri", "DA_18_Jinx"], "2-5", 4, None) is None
    # 임계값은 설정으로 조절된다
    lax = MetaTftStageBoards(src.st, BoardPlanWeights(trans_similar_min=0.1, trans_match_min=0.1))
    one = lax.next_hint(["DA_18_Kobuko", "DA_18_Zyra", "DA_18_Ahri", "DA_18_Jinx"], "2-5", 4, None)
    assert one is not None and one.close


def test_empty_stage_stats_degrade_to_none():
    class S:
        stage_stats = StageStats.empty()
    assert MetaTftStageBoards.from_stats(S(), BoardPlanWeights()) is None
    assert MetaTftStageBoards.from_stats(object(), BoardPlanWeights()) is None
    empty = MetaTftStageBoards(StageStats.empty(), BoardPlanWeights())
    assert empty.unit_signal("DA_18_Veigar", 1, "3-2", 5) is None
    assert empty.best_board({"DA_18_Veigar": 1}, "3-2", 5, None, 1.0) is None
    assert empty.next_hint(["DA_18_Veigar"], "3-2", 5, None) is None


# ---------------------------------------------------------------------------
# 보드 배치
# ---------------------------------------------------------------------------


def _plan(repo, src, weights, state: GameState, comp_id: str, level: int):
    view = build_view(state, repo, 0.6, None)
    return plan_board(view, repo, repo.comp(comp_id), level, {}, lambda i: repo.name(i, "ko") or i,
                      weights.board_plan, stage=weights.unit_stage, stage_board=src)


def test_plan_fields_the_real_stage_board_from_bench(repo, src, weights):
    """보드엔 COVEN(3스테이지 평균 수준), 벤치엔 SPELL(3스테이지 1위 실제 보드) → SPELL로 바꾸라고 한다."""
    st = gs("3-2", 5, COVEN, SPELL)
    p = _plan(repo, src, weights, st, "juggernaut-zyra-amumu", 5)
    assert {e.unit_id for e in p.lineup} == set(SPELL)
    assert {s.field_unit_id for s in p.swaps} == set(SPELL)
    hint = p.stage_board
    assert hint is not None and hint.stage == 3 and set(hint.units) == set(SPELL) and hint.games == 7973
    note = next(n for n in p.notes if n.startswith("지금 이 스테이지 추천 보드: "))
    assert "7,973판" in note and "보유 5/5" in note and f"평균보다 {-hint.delta:.2f}등 높음" in note
    assert "평균 등수" not in note
    assert any("추천 스테이지 보드" in (e.reason or "") or "통계: 3스테이지" in (e.reason or "") for e in p.lineup)


def test_plan_without_stage_stats_is_unchanged(repo, weights):
    st = gs("3-2", 5, COVEN, SPELL)
    view = build_view(st, repo, 0.6, None)
    a = plan_board(view, repo, repo.comp("juggernaut-zyra-amumu"), 5, {}, lambda i: i, weights.board_plan,
                   stage=weights.unit_stage, stage_board=None)
    b = plan_board(view, repo, repo.comp("juggernaut-zyra-amumu"), 5, {}, lambda i: i, weights.board_plan,
                   stage=weights.unit_stage, stage_board=MetaTftStageBoards(StageStats.empty(), weights.board_plan))
    assert a.stage_board is None and b.stage_board is None
    assert [e.unit_id for e in a.lineup] == [e.unit_id for e in b.lineup]
    assert not any(n.startswith("지금 이 스테이지 추천 보드") for n in a.notes)


def test_engine_wires_stage_boards(repo, settings, weights):
    adv = Advisor(stats=repo, settings=settings, weights=weights, backend=MockJevBackend())
    assert isinstance(adv.stage_boards, MetaTftStageBoards)
    rec = adv.advise(gs("3-2", 5, COVEN, SPELL))
    assert rec.board_plan is not None and rec.board_plan.stage_board is not None
    assert rec.board_plan.stage_board.stage == 3


def test_engine_without_stage_stats_has_no_source(make_advisor):
    assert make_advisor().stage_boards is None      # mini JSON 어댑터: stage_stats 없음
