"""보드 배치 추천(`advisor.board_plan.plan_board`, `_workspace/21_board_trust.md` §6).

가짜 GameState로: 레벨 칸 제한, 교체(벤치 X ↔ 보드 Y), 빈 칸, 부분 확인(이름 미상), 중복, 레벨 미인식, 엔진·리포트 연결.
"""
from __future__ import annotations

import pytest

from tft_advisor.advisor.board_plan import plan_board
from tft_advisor.advisor.features import build_view
from tft_advisor.contracts import UNKNOWN_UNIT_ID, FieldSource, GameState, UnitOnBoard

VISION = {"board": FieldSource.VISION, "bench": FieldSource.VISION}
COMP = "juggernaut-zyra-amumu"      # carry Zyra, core Yorick; Sejuani = final(비핵심)
OFF = ("DA_18_Alistar", "DA_18_Shen", "DA_18_Kennen")   # 이 덱과 무관한 유닛


def U(uid: str, star: int = 1, conf: float = 0.9, items: list[str] | None = None) -> UnitOnBoard:
    return UnitOnBoard(id=uid, star=star, confidence=conf, items=items or [])


def unknown() -> UnitOnBoard:
    return UnitOnBoard(id=UNKNOWN_UNIT_ID, confidence=0.2, star=1)


def gs(board, bench, level=4, conf=None, **kw) -> GameState:
    return GameState.model_validate({
        "screen_mode": "planning", "stage": "3-2", "level": level, "board": board, "bench": bench,
        "confidence": conf or {"board": 0.9, "bench": 0.9}, "field_source": VISION, **kw})


@pytest.fixture
def plan(stats):
    comp = stats.comp(COMP)

    def _plan(state: GameState, level: int | None = 4):
        view = build_view(state, stats, 0.6, None)
        return plan_board(view, stats, comp, level, {}, lambda i: stats.name(i, "ko") or i)
    return _plan


def ids(entries):
    return [e.unit_id for e in entries]


def test_level_cap_and_swap_brings_carry_in(plan):
    st = gs(board=[U("DA_18_Alistar"), U("DA_18_Shen"), U("DA_18_Kennen"), U("DA_18_Sejuani")],
            bench=[U("DA_18_Zyra"), U("DA_18_Yorick")], level=4)
    p = plan(st)
    assert p.slots == 4 and len(p.lineup) == 4 and p.free_slots == 0
    assert {"DA_18_Zyra", "DA_18_Yorick"} <= set(ids(p.lineup))
    fields = {s.field_unit_id for s in p.swaps}
    assert fields == {"DA_18_Zyra", "DA_18_Yorick"}
    assert all(s.bench_unit_id in OFF for s in p.swaps)                # 무관한 보드 유닛을 내린다
    zy = next(e for e in p.lineup if e.unit_id == "DA_18_Zyra")
    assert zy.action == "field" and "목표 덱 캐리" in zy.reason
    assert {e.action for e in p.bench if e.on_board} == {"bench"}


def test_no_swap_when_board_already_best(plan):
    st = gs(board=[U("DA_18_Zyra"), U("DA_18_Yorick")], bench=[U("DA_18_Alistar")], level=2)
    p = plan(st)
    assert p.swaps == [] and {e.action for e in p.lineup} == {"keep"}
    assert p.bench[0].action == "stay"


def test_free_slot_is_reported_and_filled(plan):
    st = gs(board=[U("DA_18_Zyra")], bench=[U("DA_18_Alistar")], level=4)
    p = plan(st)
    assert set(ids(p.lineup)) == {"DA_18_Zyra", "DA_18_Alistar"}    # 빈 칸이 있으면 무관한 유닛이라도 올린다
    assert p.swaps and p.swaps[0].field_unit_id == "DA_18_Alistar" and p.swaps[0].bench_unit_id is None
    assert p.free_slots == 2


def test_partial_unknowns_keep_slots_and_are_never_named(plan):
    st = gs(board=[U("DA_18_Zyra"), unknown(), unknown(), U("DA_18_Alistar")],
            bench=[U("DA_18_Yorick"), unknown()], level=4, conf={"board": 0.45, "bench": 0.3})
    p = plan(st)
    assert p.unknown_on_board == 2 and p.unknown_on_bench == 1
    assert len(p.lineup) == 2                                           # 4칸 − 미상 2칸
    everything = ids(p.lineup) + ids(p.bench) + [s.field_unit_id for s in p.swaps] + \
        [s.bench_unit_id for s in p.swaps if s.bench_unit_id]
    assert UNKNOWN_UNIT_ID not in everything
    assert set(ids(p.lineup)) == {"DA_18_Zyra", "DA_18_Yorick"}
    assert [(s.field_unit_id, s.bench_unit_id) for s in p.swaps] == [("DA_18_Yorick", "DA_18_Alistar")]
    joined = " ".join(p.notes)
    assert "보드 미확인 2기" in joined and "벤치 미확인 1기" in joined
    assert not any(bad in joined for bad in ("한다", "하라", "해요", "이다"))


def test_duplicate_champion_stays_on_bench(plan):
    st = gs(board=[U("DA_18_Zyra"), U("DA_18_Alistar")], bench=[U("DA_18_Zyra")], level=2)
    p = plan(st)
    assert ids(p.lineup).count("DA_18_Zyra") == 1 and p.swaps == []
    assert "합성 대기" in p.bench[0].reason


def test_higher_star_and_items_win(plan):
    st = gs(board=[U("DA_18_Alistar")], bench=[U("DA_18_Shen", star=2, items=["DA_Deathblade"])], level=1)
    p = plan(st)
    assert ids(p.lineup) == ["DA_18_Shen"] and "2성" in p.lineup[0].reason
    assert [(s.field_unit_id, s.bench_unit_id) for s in p.swaps] == [("DA_18_Shen", "DA_18_Alistar")]


def test_level_unknown_uses_current_board_size(stats):
    st = GameState.model_validate({"screen_mode": "planning", "stage": "3-2",
                                   "board": [U("DA_18_Alistar"), U("DA_18_Shen")], "bench": [U("DA_18_Zyra")],
                                   "field_source": VISION})
    view = build_view(st, stats, 0.6, None)
    p = plan_board(view, stats, stats.comp(COMP), None, {}, lambda i: i)
    assert p.slots == 2 and "레벨 미인식" in p.notes[0]
    assert "DA_18_Zyra" in ids(p.lineup)


def test_no_plan_without_board_or_named_units(plan, stats):
    assert plan(GameState.model_validate({"screen_mode": "planning", "level": 3})) is None
    blind = gs(board=[unknown(), unknown()], bench=[unknown()], conf={"board": 0.0, "bench": 0.0})
    assert plan(blind) is None


def test_engine_and_report_carry_the_plan(make_advisor):
    from tft_advisor.app.names import NameBook
    from tft_advisor.app.report import format_report

    st = gs(board=[U("DA_18_Alistar"), U("DA_18_Zyra")], bench=[U("DA_18_Yorick"), unknown()], level=3,
            conf={"board": 0.85, "bench": 0.38}, gold=20,
            shop=[{"kind": "champion", "id": "DA_18_Sejuani"}] + [{"kind": "empty"}] * 4)
    rec = make_advisor().advise(st)
    assert rec.board_plan is not None and rec.board_plan.comp_id == rec.target_comps[0].comp_id
    assert "DA_18_Yorick" in ids(rec.board_plan.lineup)
    text = format_report(st, rec, names=NameBook())
    head = text.index("[목표 덱]")
    assert head < text.index("[보드 배치]") < text.index("[상점]")
    assert "벤치 미확인 1기" in text
