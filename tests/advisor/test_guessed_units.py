"""추정 이름 유닛은 판단하지 않는다(`_workspace/21_board_trust.md` §15).

vision이 뒷받침 없이 라이브러리 닮음 하나로 붙인 이름은 신뢰도 0.75로 묶인다("(추정)"). 필드 임계값 0.6은 넘지만
틀릴 수 있다(test.png 벤치 1 "세주아니 (추정)" = 실제로는 레오나). advisor는 `[board_plan] min_confidence`(0.8) 미만을
이름 미상처럼 다룬다: 판매·보드 교체·1성 쌍·상점 "보유 N"·목표 덱 보유 어디에도 쓰지 않는다.
뒷받침된 이름(0.85 등)은 그대로 쓴다.
"""
from __future__ import annotations

import pytest

from tft_advisor.advisor.board_plan import plan_board
from tft_advisor.advisor.features import build_view
from tft_advisor.advisor.sell import attach_sell
from tft_advisor.app.names import NameBook
from tft_advisor.app.report import board_plan_lines, units_lines
from tft_advisor.config import BoardPlanWeights, SellWeights
from tft_advisor.contracts import UNKNOWN_UNIT_ID, FieldSource, GameState, UnitOnBoard
from tft_advisor.unit_status import CONFIRMED_NAME_THRESHOLD, owned_units, units_note, units_reason

VISION = {"board": FieldSource.VISION, "bench": FieldSource.VISION}
COMP = "juggernaut-zyra-amumu"          # 최종: 자이라·요릭·세주아니·바이 … / 무관: 알리스타·쉔·케넨
GUESS = 0.75                            # vision.units.LIB_STRICT_CONF_CAP
UNIT_MIN = BoardPlanWeights().min_confidence


def U(uid: str, star: int = 1, conf: float = 0.9, slot: int | None = None, hex_=None) -> UnitOnBoard:
    return UnitOnBoard(id=uid, star=star, confidence=conf, bench_slot=slot, hex=hex_)


def gs(bench, *, stage="4-2", level=3, gold=10, board=None, shop=None, shop_odds=None) -> GameState:
    board = board if board is not None else [U(u, hex_=(0, i)) for i, u in
                                             enumerate(("DA_18_Zyra", "DA_18_Yorick", "DA_Vi18")[:level])]
    bench = [u if u.bench_slot is not None else u.model_copy(update={"bench_slot": i}) for i, u in enumerate(bench)]
    raw = {"screen_mode": "planning", "stage": stage, "level": level, "gold": gold, "board": board, "bench": bench,
           "confidence": {"board": 0.9, "bench": 0.9}, "field_source": VISION}
    if shop_odds is not None:
        raw["shop_odds"] = shop_odds
    if shop is not None:
        raw["shop"] = [{"kind": "champion", "id": i} for i in shop] + [{"kind": "empty"}] * (5 - len(shop))
    return GameState.model_validate(raw)


@pytest.fixture
def run(stats):
    comp = stats.comp(COMP)

    def _run(state: GameState, w: SellWeights | None = None):
        view = build_view(state, stats, 0.6, None, UNIT_MIN)
        plan = plan_board(view, stats, comp, state.level, {}, lambda i: stats.name(i, "ko") or i)
        return view, attach_sell(plan, view, stats, [comp], state.level, [], w or SellWeights())
    return _run


def test_defaults_sit_above_the_guess_cap():
    assert UNIT_MIN == SellWeights().min_confidence == CONFIRMED_NAME_THRESHOLD == 0.8
    assert UNIT_MIN > GUESS


def test_guessed_name_is_never_sold_but_corroborated_one_is(run):
    # 4-2(후반): 목표 덱 밖 알리스타·케넨은 판다. 0.75 추정 이름이면 팔라고 하지 않는다
    _, p = run(gs([U("DA_18_Alistar", conf=GUESS), U("DA_18_Kennen", conf=0.85), unknown(slot=5)]))
    sold = [s.unit_id for s in p.sell]
    assert "DA_18_Alistar" not in sold
    assert "DA_18_Kennen" in sold          # 뒷받침된 0.85는 그대로 판단
    assert any("미확인 유닛 2기(추정 이름 1기 포함)는 판단하지 않았습니다" in n for n in p.sell_notes)


def unknown(slot=None) -> UnitOnBoard:
    return UnitOnBoard(id=UNKNOWN_UNIT_ID, confidence=0.2, star=1, bench_slot=slot)


def test_guessed_name_is_not_benched_or_fielded(run):
    # 보드의 추정 이름 칸은 자리만 차지한 채 그대로(교체 대상이 아니다), 벤치의 추정 이름은 올리지 않는다
    board = [U("DA_18_Zyra", hex_=(0, 0)), U("DA_18_Kennen", conf=GUESS, hex_=(0, 1)), U("DA_Vi18", hex_=(0, 2))]
    view, p = run(gs([U("DA_18_Yorick", conf=GUESS), U("DA_18_Sejuani")], board=board))
    ids = {u.id for u in view.units}
    assert "DA_18_Kennen" not in ids and "DA_18_Yorick" not in ids
    assert p.unknown_on_board == 1 and p.unknown_on_bench == 1
    assert all(e.unit_id != "DA_18_Yorick" for e in p.lineup)
    assert all(sw.field_unit_id != "DA_18_Kennen" for sw in p.swaps)
    assert "DA_18_Kennen" not in [s.unit_id for s in p.sell]
    assert any("보드 미확인 1기(추정 이름 1기 포함)는 그대로 두었습니다" in n for n in p.notes)
    assert any("벤치 미확인 1기(추정 이름 1기 포함)는 판단하지 않았습니다" in n for n in p.notes)
    text = "\n".join(board_plan_lines(p, NameBook()))
    assert "케넨" not in text and "요릭" not in text.split("벤치:")[1].split("\n")[0]


def test_guessed_copy_does_not_make_a_pair(run):
    # 3-2, 2코스트 33%: 확인된 쉔 2기는 2성 가능성이 있는 쌍이라 지킨다.
    # 확인된 쉔 1기 + 추정 쉔 1기 = 쌍이 아니다(사본 수에 넣지 않는다) → 확인된 쉔은 싱글로 판다, 추정 쉔은 판단하지 않는다
    odds = [45, 33, 20, 2, 0]
    _, p = run(gs([U("DA_18_Shen"), U("DA_18_Shen", conf=GUESS)], stage="3-2", shop_odds=odds))
    assert [(s.unit_id, s.bench_slot) for s in p.sell] == [("DA_18_Shen", 0)]
    view, p2 = run(gs([U("DA_18_Shen"), U("DA_18_Shen", conf=0.85)], stage="3-2", shop_odds=odds))
    assert [s.unit_id for s in p2.sell] == []                  # 둘 다 확인 → 2성 가능성 있는 쌍, 지킨다
    assert sum(1 for u in view.units if u.id == "DA_18_Shen") == 2


def test_owned_units_counts_guesses_as_hidden():
    st = gs([U("DA_18_Sejuani", conf=GUESS), U("DA_18_Shen", conf=0.85), unknown()])
    o = owned_units(st, 0.6, UNIT_MIN)
    assert [u.id for u in o.bench] == ["DA_18_Shen"]
    assert o.bench_hidden == 2 and o.bench_guessed == 1 and not o.complete
    assert "추정 이름 1기 포함" in o.gap_text()
    old = owned_units(st, 0.6)                                  # unit_threshold 없으면 예전과 같다
    assert {u.id for u in old.bench} == {"DA_18_Sejuani", "DA_18_Shen"} and old.bench_guessed == 0
    assert "이름 미상 2기(추정 이름 1기 포함)" in (units_reason(st, 0.6, UNIT_MIN) or "")
    assert "이름 미상 2기(추정 이름 1기 포함)" in (units_note(st, 0.6) or "")


def test_shop_owned_count_and_comp_ownership_ignore_guesses(make_advisor):
    # 상점 세주아니: 추정 세주아니 2기는 "보유 2"가 아니다. 목표 덱 보유에도 들어가지 않는다
    adv = make_advisor()
    st = gs([U("DA_18_Sejuani", conf=GUESS), U("DA_18_Sejuani", conf=GUESS)], stage="3-2", level=3,
            shop=["DA_18_Sejuani"])
    rec = adv.advise(st)
    sej = next(a for a in rec.shop if a.offer_id == "DA_18_Sejuani")
    assert "보유 2" not in (sej.reason or "")
    assert all("DA_18_Sejuani" not in t.owned_units for t in rec.target_comps)
    rec2 = make_advisor().advise(gs([U("DA_18_Sejuani", conf=0.85), U("DA_18_Sejuani", conf=0.85)], stage="3-2",
                                    level=3, shop=["DA_18_Sejuani"]))
    sej2 = next(a for a in rec2.shop if a.offer_id == "DA_18_Sejuani")
    assert "보유 2" in (sej2.reason or "")                     # 뒷받침된 이름은 그대로 센다


def test_report_marks_guessed_names():
    st = gs([U("DA_18_Sejuani", conf=GUESS), U("DA_18_Shen", conf=0.85)])
    line = units_lines(st, NameBook())[1]
    assert "세주아니 (추정)" in line and "쉔 (추정)" not in line
