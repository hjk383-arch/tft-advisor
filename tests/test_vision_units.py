"""vision 19 (2026-09-23): 보드·벤치 유닛 **챔피언 이름** 식별(`vision.units`).

- 규칙(특성 패널 풀이·구속 배정·중복 판정·패널 인원 보충·병합)은 합성 입력으로 고정한다.
- `tests/fixtures/screens/raw/`(gitignore, 사용자 원본 캡처)가 있으면 실제 캡처로 **남겨 둔(held-out) 평가**를 한다:
  라이브러리는 2-2·2-5 캡처의 확인 라벨로만 만들고, 2-6 캡처(= 사용자가 준 test.png와 같은 파일)에서 이름을 맞힌다.
  라벨 근거는 각 `*.expected.json`의 `_unit_evidence_*` 메모와 `_workspace/19_unit_naming.md` §2.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tft_advisor.contracts import UNKNOWN_UNIT_ID, FieldSource, GameState  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision import units as U  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "screens" / "raw"
USER_TEST = ROOT / "tests" / "fixtures" / "screens" / "test" / "test.png"
HELDOUT_TRAIN = ("2-2 전투 전", "2-5 전투 전")
HELDOUT_TEST = "2-6 전투 전"


@pytest.fixture(scope="module")
def static():
    return load_static()


@pytest.fixture(scope="module")
def table(static):
    return U.TraitTable.from_static(static)


def ids(static, *names: str) -> list[str]:
    return [static.champion_by_name(n)["apiName"] for n in names]


def panel(static, counts: dict[str, int], complete: bool = True) -> U.TraitPanel:
    return U.TraitPanel({static.trait_by_name(k)["apiName"]: v for k, v in counts.items()}, complete=complete)


PANEL_2_6 = {"부식": 1, "악의 여단": 3, "적응가": 2, "약탈자": 2, "기원자": 1, "선봉대": 1, "주문술사": 1, "지옥불": 1}
PANEL_2_2 = {"속사포": 1, "약탈자": 1, "엄호대": 1, "지옥불": 1, "나무정령": 1, "악의 여단": 1}


# ---------------------------------------------------------------- 특성 패널 풀이
def test_trait_panel_solves_the_user_board_uniquely(static, table):
    sols = U.solve_board_sets(panel(static, PANEL_2_6), 5, table)
    assert sols == [frozenset(ids(static, "아칼리", "엘리스", "카밀", "코그모", "카시오페아"))]


def test_trait_panel_2_2_identifies_the_coven_unit_as_camille(static, table):
    """2-2 패널에는 악의 여단 1·약탈자 1이 있고 선봉대가 없다 → 악의 여단 유닛은 엘리스가 아니라 카밀이다
    (사용자 초안과 엘리스/카밀 자리가 바뀐 근거, 19 보고 §2)."""
    sols = U.solve_board_sets(panel(static, PANEL_2_2), 3, table)
    assert sols == [frozenset(ids(static, "카밀", "오른", "바루스"))]


def test_solver_refuses_incomplete_or_unsolvable_panels(static, table):
    assert U.solve_board_sets(panel(static, PANEL_2_6, complete=False), 5, table) is None   # "N+" 넘침
    assert U.solve_board_sets(panel(static, {"부식": 2}), 5, table) is None                 # 풀이 없음(상징·증강?)
    assert U.solve_board_sets(panel(static, PANEL_2_6), 4, table) is None                   # 유닛 수보다 챔피언이 많다
    assert U.solve_board_sets(U.TraitPanel({}), 3, table) is None


def test_duplicates_count_traits_once(static, table):
    # 아칼리 2기 = 지옥불1·적응가1·약탈자1 (같은 챔피언은 특성을 한 번만 센다)
    sols = U.solve_board_sets(panel(static, {"지옥불": 1, "적응가": 1, "약탈자": 1}), 2, table)
    assert frozenset(ids(static, "아칼리")) in sols


def test_emblem_is_subtracted_before_solving(static, table):
    emblem = next(i for i, t in table.emblem_trait.items() if t == static.trait_by_name("약탈자")["apiName"])
    # 코그모 + 약탈자 상징: 패널은 부식1·적응가1·기원자1·약탈자1
    p = panel(static, {"부식": 1, "적응가": 1, "기원자": 1, "약탈자": 1})
    assert U.solve_board_sets(p, 1, table, emblems=[emblem]) == [frozenset(ids(static, "코그모"))]


def test_too_many_solutions_is_not_a_constraint(static, table):
    # 약탈자 2(유닛 2기) → 약탈자를 가진 챔피언 쌍이 많다 → 풀이 상한을 넘거나 여럿
    sols = U.solve_board_sets(panel(static, {"약탈자": 2}), 2, table, max_solutions=2)
    assert sols is None


# ---------------------------------------------------------------- 구속 배정
def test_constrained_assignment_uses_similarity_and_reports_margins():
    S = np.array([[0.8, 0.2, 0.1], [0.3, 0.7, 0.2], [0.1, 0.3, 0.6]])
    total, names = U._place_board(S, ["a", "b", "c"], 1.0)
    assert [n.unit_id for n in names] == ["a", "b", "c"] and total == pytest.approx(2.1)
    assert all(n.source == "traits" and 0.6 < n.confidence <= U.NAME_CONF_CAP for n in names)


def test_no_similarity_means_set_only_never_a_guess():
    total, names = U._place_board(np.zeros((3, 3)), ["a", "b", "c"], 1.0)
    assert [n.unit_id for n in names] == [None, None, None]


def test_single_champion_board_is_forced_only_for_one_slot():
    _, names = U._place_board(np.zeros((1, 1)), ["a"], 1.0)
    assert [(n.unit_id, n.source) for n in names] == [("a", "forced")]
    # 칸 둘 · 챔피언 하나: 서로 닮은 칸(같은 모델 2기)이면 이름을 붙이지만 forced(디스크 저장 대상)는 아니다
    alike = np.array([[1.0, 0.8], [0.8, 1.0]])
    _, names = U._place_board(np.zeros((2, 1)), ["a"], 1.0, alike)
    assert [(n.unit_id, n.source) for n in names] == [("a", "traits"), ("a", "traits")]
    # 서로 닮지 않았고 표본도 없으면 둘 다 모름(한쪽은 특성 없는 유닛일 수 있다)
    _, names = U._place_board(np.zeros((2, 1)), ["a"], 1.0, np.eye(2))
    assert [n.unit_id for n in names] == [None, None]


def test_every_champion_in_the_set_is_used_at_least_once():
    # 칸 1이 a와 조금 더 닮았어도, 칸 0이 a를 **확실히** 가지면(0.9 vs 0.5) b가 한 번은 나와야 한다
    S = np.array([[0.9, 0.1], [0.5, 0.45]])
    _, names = U._place_board(S, ["a", "b"], 1.0)
    assert [n.unit_id for n in names] == ["a", "b"]


def test_contradicted_swap_leaves_both_slots_unknown():
    # 30 보고(라이브 3 2-2): 두 칸 모두 a(아칼리)를 더 닮았고 차가 작다 → "어느 쪽이 a를 더 닮았나"로 가르지 않는다
    for S in (np.array([[0.563, 0.299], [0.454, 0.271]]),     # 실측 닮음(아칼리, 바루스)
              np.array([[0.9, 0.1], [0.8, 0.5]]),             # 옛 테스트: 칸 1이 a를 0.3 더 닮음
              np.array([[0.563, 0.0], [0.454, 0.0]])):        # b 표본 없음 + 소거법이어도 같다
        _, names = U._place_board(S, ["a", "b"], 1.0)
        assert [n.unit_id for n in names] == [None, None], S
    # 칸마다 자기 챔피언을 더 닮았으면 그대로 이름
    _, names = U._place_board(np.array([[0.56, 0.30], [0.27, 0.45]]), ["a", "b"], 1.0)
    assert [n.unit_id for n in names] == ["a", "b"]


def test_contradicted_swap_goes_to_unplaced(static, table):
    """집합은 확실(풀이 1개)하지만 두 칸이 어긋나면 이름 대신 unplaced(자리 미상)로 나간다."""
    rng = np.random.default_rng(7)
    shape = U.descriptor(np.zeros((112, 112, 3), np.uint8)).shape
    d = [rng.random(shape).astype(np.float32) for _ in range(2)]
    lib = U.UnitLibrary()
    orig = U.UnitLibrary.scores
    table_scores = [{"DA_18_Akali_AD": 0.563, "DA_18_Varus": 0.299}, {"DA_18_Akali_AD": 0.454, "DA_18_Varus": 0.271}]
    it = iter(table_scores)
    try:
        U.UnitLibrary.scores = lambda self, desc, extra=(): dict(next(it)) if not extra else orig(self, desc, extra)
        panel = U.TraitPanel({"DA_18_Inferno": 2, "DA_18_Adaptor": 1, "DA_18_Slayer": 1, "DA_18_Rapidfire": 1})
        res = U.name_units(d, [], lib, panel, table)
    finally:
        U.UnitLibrary.scores = orig
    assert res.board_set == frozenset({"DA_18_Akali_AD", "DA_18_Varus"})
    assert [b.unit_id for b in res.board] == [None, None]
    assert sorted(res.unplaced) == ["DA_18_Akali_AD", "DA_18_Varus"]


def test_name_units_board_set_known_but_unplaced_without_library(static, table):
    d = [np.random.default_rng(i).random(U.descriptor(np.zeros((112, 112, 3), np.uint8)).shape).astype(np.float32)
         for i in range(5)]
    res = U.name_units(d, [], U.UnitLibrary(), panel(static, PANEL_2_6), table)
    assert all(s.unit_id is None for s in res.board)
    assert res.board_set == frozenset(ids(static, "아칼리", "엘리스", "카밀", "코그모", "카시오페아"))
    assert sorted(res.unplaced) == sorted(res.board_set)


def _img(color) -> np.ndarray:
    img = np.full((112, 112, 3), (90, 120, 150), np.uint8)
    cv2.rectangle(img, (40, 20), (72, 100), color, -1)
    return img


def test_library_names_and_unknown_stays_unknown(table):
    red, blue, yellow = _img((0, 0, 255)), _img((255, 0, 0)), _img((0, 255, 255))
    board = [U.descriptor(red), U.descriptor(blue)]
    res = U.name_units(board, [U.descriptor(red)], U.UnitLibrary(), None, table)
    assert [s.unit_id for s in (*res.board, *res.bench)] == [None, None, None]     # 구속도 표본도 없으면 모름
    lib = U.library_from([("RED", red), ("BLUE", blue)])
    res = U.name_units(board, [U.descriptor(red), U.descriptor(yellow)], lib, None, table)
    assert [s.unit_id for s in res.board] == ["RED", "BLUE"]
    assert res.bench[0].unit_id == "RED" and res.bench[1].unit_id is None          # 노랑은 아무와도 닮지 않았다


def test_bench_duplicate_of_board_units_named_by_traits(static, table):
    """표본이 없어도, 특성 구속으로 이름이 정해진 보드 유닛과 같은 모델인 벤치 유닛은 같은 챔피언이다."""
    red, blue = _img((0, 0, 255)), _img((255, 0, 0))
    ak, ko = ids(static, "아칼리", "코그모")
    lib = U.library_from([(ak, red)])               # 아칼리 표본 하나 → 구속 배정으로 코그모 칸도 정해진다
    p = panel(static, {"지옥불": 1, "적응가": 2, "약탈자": 1, "부식": 1, "기원자": 1})
    res = U.name_units([U.descriptor(red), U.descriptor(blue)], [U.descriptor(blue)], U.library_from([]), p, table)
    assert [s.unit_id for s in res.board] == [None, None] and set(res.unplaced) == {ak, ko}
    res = U.name_units([U.descriptor(red), U.descriptor(blue)], [U.descriptor(blue)], lib, p, table)
    assert [s.unit_id for s in res.board] == [ak, ko]
    assert res.bench[0].unit_id == ko and res.bench[0].source == "duplicate"


# ---------------------------------------------------------------- 패널 인원 보충
@pytest.mark.parametrize("text,want", [
    ("1/2", (1, 2)), ("112", (1, 2)), ("|/|", (1, 1)), ("113", (1, 3)), ("1/1", (1, 1)), ("2 / 3", (2, 3)),
    ("부식", None), ("3", None), ("2>4>6", None),
])
def test_ladder_count(text, want):
    assert U.ladder_count(text) == want


def test_thin_one_glyph():
    cell = np.full((30, 22, 3), 60, np.uint8)
    cell[8:22, 10:12] = 230
    assert U.thin_one_glyph(cell, 24)
    three = np.full((30, 22, 3), 60, np.uint8)
    cv2.putText(three, "3", (4, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2)
    assert not U.thin_one_glyph(three, 24)
    assert not U.thin_one_glyph(np.full((30, 22, 3), 60, np.uint8), 24)


# ---------------------------------------------------------------- app 병합
def _slot(**kw):
    from tft_advisor.vision.board import UnitSlot
    return UnitSlot(**kw)


def test_merge_uses_vision_names_and_reports_board_and_bench_separately(static):
    from tft_advisor.app.unit_merge import apply_board_read
    from tft_advisor.vision.board import BoardRead

    ak, ca = ids(static, "아칼리", "카밀")
    read = BoardRead(
        board=(_slot(star=1, hex=(0, 0), unit_id=ak, unit_conf=0.9, items=("DA_Item_BFSword",)),
               _slot(star=1, hex=(0, 1), unit_id=ca, unit_conf=0.95)),
        bench=(_slot(star=1, bench_slot=0, unit_id=ak, unit_conf=0.9), _slot(star=1, bench_slot=1)),
        confidence=0.85, bars=4)
    st = apply_board_read(GameState(), read)
    assert [u.id for u in st.board] == [ak, ca] and [u.hex for u in st.board] == [(0, 0), (0, 1)]
    assert [u.id for u in st.bench] == [ak, UNKNOWN_UNIT_ID]
    assert st.field_source["board"] == FieldSource.VISION
    assert st.confidence["board"] == pytest.approx(0.85)            # 판독 신뢰도 상한
    assert st.confidence["bench"] == pytest.approx(0.85 * 0.5)      # 2기 중 1기만 이름을 안다
    assert [r.holder for r in st.items.equipped] == [ak]


def test_merge_places_unplaced_set_members_without_position_or_item_owner(static):
    from tft_advisor.app.unit_merge import apply_board_read
    from tft_advisor.vision.board import BoardRead

    ak, ca = ids(static, "아칼리", "카밀")
    read = BoardRead(board=(_slot(star=1, hex=(0, 0), items=("DA_Item_BFSword",)), _slot(star=1, hex=(0, 1))),
                     bench=(), confidence=0.85, bars=2, board_set=tuple(sorted((ak, ca))),
                     unplaced=tuple(sorted((ak, ca))))
    st = apply_board_read(GameState(), read)
    assert sorted(u.id for u in st.board) == sorted([ak, ca])
    assert all(u.hex is None and u.items == [] for u in st.board)       # 자리·아이템 소유자를 지어내지 않는다
    assert [r.holder for r in st.items.equipped] == [None]
    assert st.confidence["board"] == pytest.approx(0.8)


def test_merge_vision_name_consumes_the_ledger_copy(static):
    from tft_advisor.app.ledger import UnitLedger
    from tft_advisor.app.unit_merge import board_obs_from, merge_units
    from tft_advisor.vision.board import BoardRead

    ak, xa = ids(static, "아칼리", "자야")
    led = UnitLedger()
    led.add(ak, 1, source="manual")
    led.add(xa, 1, source="manual")
    read = BoardRead(board=(_slot(star=1, hex=(0, 0), unit_id=ak, unit_conf=0.9),),
                     bench=(_slot(star=1, bench_slot=0),), confidence=0.85, bars=2)
    res = merge_units(led, board_obs_from(read))
    assert [u.id for u in res.board] == [ak] and [u.id for u in res.bench] == [xa]   # 아칼리는 두 번 쓰이지 않는다
    assert res.dropped == 0 and res.source != FieldSource.VISION


def test_report_lists_named_units(static):
    from tft_advisor.app.names import NameBook
    from tft_advisor.app.report import units_lines
    from tft_advisor.contracts import UnitOnBoard

    ak, = ids(static, "아칼리")
    st = GameState(board=[UnitOnBoard(id=ak, star=2, hex=(0, 0), confidence=0.9),
                          UnitOnBoard(id=ak, hex=None, confidence=0.8)],
                   bench=[UnitOnBoard(id=UNKNOWN_UNIT_ID, bench_slot=2, confidence=0.2)])
    lines = units_lines(st, NameBook(static), 0.6)
    # 성급 미상은 "★?"(★1로 보이지 않게, QA 36 W2)
    assert lines == ["보드 2기: 아칼리 2성 (0,0) · 아칼리 ★? (자리 미상)", "벤치 1기: 3 이름 미상"]


def test_fixture_labels_keep_unconfirmed_names_apart(static, tmp_path):
    from tft_advisor.fixtures import load_expected

    p = tmp_path / "x.expected.json"
    p.write_text(json.dumps({"board_slots": [{"hex": [0, 0], "name": "아칼리"}],
                             "bench_slots": [{"slot": 0, "name_unconfirmed": "레오나"}, {"slot": 1}]},
                            ensure_ascii=False), encoding="utf-8")
    ex = load_expected(p, static).extras
    assert ex["board_slots"][0]["unit_id"] == ids(static, "아칼리")[0]
    assert ex["bench_slots"][0]["unit_id"] is None and ex["bench_slots"][0]["unit_guess"] == ids(static, "레오나")[0]


# ---------------------------------------------------------------- 실제 캡처(있을 때만)
def _raw_ready():
    if not (RAW / f"{HELDOUT_TEST}.png").is_file() or not (RAW / f"{HELDOUT_TEST}.expected.json").is_file():
        pytest.skip("원본 캡처 없음(tests/fixtures/screens/raw, gitignore)")
    labels = json.loads((RAW / f"{HELDOUT_TEST}.expected.json").read_text(encoding="utf-8"))
    if "board_slots" not in labels:
        pytest.skip("원본 캡처에 유닛 이름 라벨이 없음")
    from tft_advisor.vision.ocr import RapidOcrEngine

    if RapidOcrEngine.available_backend() is None:
        pytest.skip("OCR 백엔드 없음")


def _load(name: str) -> np.ndarray:
    from tft_advisor.vision.capture import load_image
    return load_image(RAW / f"{name}.png")


@pytest.fixture(scope="module")
def _heldout_pairs(static):
    """(인식기, 2-2·2-5 확인 라벨 크롭) — 만드는 비용이 커서 모듈 범위로 한 번만."""
    _raw_ready()
    from tft_advisor.fixtures import load_expected
    from tft_advisor.vision.recognizer import Recognizer

    from tft_advisor.vision.unit_db import arena_signature

    rec = Recognizer(static=static, unit_template_dir=Path("/nonexistent"))
    pairs = []
    arenas = set()
    for name in HELDOUT_TRAIN:
        img = _load(name)
        exp = load_expected(RAW / f"{name}.expected.json", static)
        box = rec.content_for(img, None)
        got, errors = U.labeled_crops(img, exp.extras, rec.profile_for(box[2], box[3]), box)
        assert errors == []
        pairs += got
        arenas.add(arena_signature(img, box))
    assert len(arenas) == 1                         # 같은 판·같은 맵(돌 맵)
    rec._heldout_arena = arenas.pop()               # 30: 표본의 맵 서명(뒷받침 없는 이름은 같은 맵 표본이 있어야 한다)
    return rec, pairs


def _reset_library(rec, pairs) -> None:
    """라이브러리를 2-2·2-5 표본만으로 되돌린다. `UnitNamer._learn`이 인식한 프레임(2-6)의 크롭을 메모리 표본으로
    더하므로, 되돌리지 않으면 다음 채점이 **채점 대상 자신의 크롭**으로 맞히는 순환 평가가 된다(QA 19)."""
    rec.unit_namer.library = U.library_from(pairs, arena=getattr(rec, "_heldout_arena", None))
    rec.unit_namer._session = 0
    rec.unit_namer._base = len(rec.unit_namer.library)
    rec.unit_namer.agree_frames = 1      # 스크린샷 한 장 평가: 여러 프레임 일치 요구를 끈다(23 보고)
    rec.unit_namer.reset()


@pytest.fixture
def heldout(_heldout_pairs):
    """라이브러리 = 2-2·2-5 확인 라벨만(디스크 라이브러리 안 씀). 대상 = 2-6(test.png와 같은 파일).
    테스트마다 라이브러리를 새로 만든다(앞 테스트가 2-6 크롭을 학습해 두지 않게)."""
    rec, pairs = _heldout_pairs
    _reset_library(rec, pairs)
    rec._pairs = pairs
    return rec


def _truth(static, name):
    from tft_advisor.fixtures import load_expected
    return load_expected(RAW / f"{name}.expected.json", static).extras


@pytest.mark.parametrize("name", list(HELDOUT_TRAIN) + [HELDOUT_TEST, "악의 여단", "수호령"])
def test_raw_trait_panel_is_read_completely(static, name):
    _raw_ready()
    from tft_advisor.vision.recognizer import Recognizer
    from tft_advisor.vision.regions import FrameMapper

    rec = Recognizer(static=static, unit_template_dir=Path("/nonexistent"))
    img = _load(name)
    m = FrameMapper.for_image(img, rec.content_for(img, None))
    traits, _, complete = rec.read_trait_panel(img, m, rec.profile_for(m.box[2], m.box[3]))
    board = [s["unit_id"] for s in _truth(static, name)["board_slots"]]
    want = Counter()
    for cid in set(board):
        want.update(static.get("champions", cid)["traits"])
    assert complete and {t.id: t.count for t in traits} == dict(want)


def test_raw_heldout_board_and_bench_names(static, heldout):
    """사용자가 준 test.png(= raw/2-6 전투 전.png): 보드 5기 전부, 벤치는 **확인된 칸만** 맞히고 나머지는 모른다고 한다."""
    img = _load(HELDOUT_TEST)
    heldout.recognize(img)
    read = heldout.last_board_read
    truth = _truth(static, HELDOUT_TEST)
    got_board = {u.hex: u.unit_id for u in read.board}
    assert got_board == {s["hex"]: s["unit_id"] for s in truth["board_slots"]}
    # 23: 테두리 군집 3 → 4(다른 맵 견고성)로 아칼리/코그모 배정 여유가 0.19 → 0.19~0.13대로 줄어 0.79가 나온다
    assert all(u.unit_conf >= 0.75 for u in read.board), [(u.hex, u.unit_conf, u.name_source) for u in read.board]
    got_bench = {u.bench_slot: u.unit_id for u in read.bench}
    for s in truth["bench_slots"]:
        if s["unit_id"]:
            assert got_bench[s["slot"]] == s["unit_id"], s
        else:
            # 라이브러리에 없는 챔피언(추정 라벨 칸)은 이름을 지어내지 않는다
            assert got_bench[s["slot"]] is None, (s, got_bench[s["slot"]])


def test_raw_empty_library_still_knows_the_board_set(static):
    _raw_ready()
    from tft_advisor.vision.recognizer import Recognizer

    rec = Recognizer(static=static, unit_template_dir=Path("/nonexistent"))
    rec.recognize(_load(HELDOUT_TEST))
    read = rec.last_board_read
    truth = {s["unit_id"] for s in _truth(static, HELDOUT_TEST)["board_slots"]}
    assert set(read.board_set) == truth and set(read.unplaced) == truth
    assert all(u.unit_id is None for u in (*read.board, *read.bench))       # 칸 배정은 추측하지 않는다


def test_raw_screenshot_state_has_named_board(static, heldout):
    from tft_advisor.app.unit_merge import apply_board_read

    for path in [RAW / f"{HELDOUT_TEST}.png"] + ([USER_TEST] if USER_TEST.is_file() else []):
        from tft_advisor.vision.capture import load_image
        _reset_library(heldout, heldout._pairs)          # test.png = 2-6과 같은 파일 → 앞 경로의 학습분을 쓰지 않는다
        st = heldout.recognize(load_image(path))
        st = apply_board_read(st, heldout.last_board_read)
        assert sorted(static.name_ko(u.id) for u in st.board) == sorted(["아칼리", "카밀", "엘리스", "코그모", "카시오페아"])
        assert st.confidence["board"] >= 0.6 and st.field_source["board"] == FieldSource.VISION
        assert Counter(static.name_ko(u.id) for u in st.bench if u.id != UNKNOWN_UNIT_ID) == \
            Counter({"자야": 2, "아칼리": 1, "카밀": 1})


def test_raw_naming_cost(static, heldout):
    from tft_advisor.vision.regions import FrameMapper

    img = _load(HELDOUT_TEST)
    m = FrameMapper.for_image(img, heldout.content_for(img, None))
    P = heldout.profile_for(m.box[2], m.box[3])
    read = heldout.board_reader.read(img, m, P)
    heldout.cached_trait_panel(img, m, P)
    t = time.perf_counter()
    for _ in range(3):
        heldout.cached_trait_panel(img, m, P)            # 캐시 적중(OCR 0회)
        heldout.unit_namer.name(img, m, read, None)
    assert (time.perf_counter() - t) / 3 < 0.15


# ---------------------------------------------------------------- QA 19: 오답 0 정책 공격(qa-validator)
def test_qa_incomplete_or_missing_panel_names_nothing_without_library(static, table):
    """라이브러리가 비고 패널이 불완전("N+")하거나 없으면 이름도 집합도 내지 않는다."""
    rng = np.random.default_rng(0)
    shape = U.descriptor(np.zeros((112, 112, 3), np.uint8)).shape
    d = [rng.random(shape).astype(np.float32) for _ in range(5)]
    for p in (panel(static, PANEL_2_6, complete=False), None, U.TraitPanel({})):
        res = U.name_units(d, d[:3], U.UnitLibrary(), p, table)
        assert all(s.unit_id is None for s in (*res.board, *res.bench))
        assert res.board_set is None and res.unplaced == ()


@pytest.mark.parametrize("which", ["2-2", "2-5", "2-6"])
def test_qa_one_row_panel_misread_never_gives_a_wrong_unique_set(static, table, which):
    """실제 캡처 패널에서 행 하나를 빠뜨리거나 인원을 ±1 잘못 읽어도, **틀린 집합 하나**로 풀리지 않는다
    (풀이 없음 또는 여럿 → 구속을 쓰지 않거나 신뢰도를 깎는다). 무작위 보드에서는 이 보장이 없다(QA 19 §3)."""
    counts, n, truth = {
        "2-2": (PANEL_2_2, 3, ("카밀", "오른", "바루스")),
        "2-5": ({"악의 여단": 3, "약탈자": 1, "선봉대": 1, "주문술사": 1, "부식": 1, "적응가": 1, "기원자": 1}, 4,
                ("카밀", "엘리스", "카시오페아", "코그모")),
        "2-6": (PANEL_2_6, 5, ("아칼리", "엘리스", "카밀", "코그모", "카시오페아")),
    }[which]
    base = {static.trait_by_name(k)["apiName"]: v for k, v in counts.items()}
    assert U.solve_board_sets(U.TraitPanel(base), n, table) == [frozenset(ids(static, *truth))]
    for t in base:
        for q in ({k: v for k, v in base.items() if k != t}, {**base, t: base[t] - 1}, {**base, t: base[t] + 1}):
            sols = U.solve_board_sets(U.TraitPanel({k: v for k, v in q.items() if v > 0}), n, table)
            assert not (sols and len(sols) == 1), (t, q, sols)


def test_qa_autolearn_writes_only_forced_names_to_disk(static, table, tmp_path):
    """구속 배정·라이브러리로 붙인 이름(신뢰도가 높아도)은 디스크에 쓰지 않는다. 메모리 표본만 는다."""
    red, blue = _img((0, 0, 255)), _img((255, 0, 0))
    ak, ko = ids(static, "아칼리", "코그모")
    lib = U.UnitLibrary(save_dir=tmp_path)
    lib.samples.append((ak, U.descriptor(red)))
    namer = U.UnitNamer(library=lib, table=table, autolearn=True, _base=1)
    p = panel(static, {"지옥불": 1, "적응가": 2, "약탈자": 1, "부식": 1, "기원자": 1})
    res = U.name_units([U.descriptor(red), U.descriptor(blue)], [U.descriptor(blue)], lib, p, table)
    assert [s.unit_id for s in res.board] == [ak, ko] and {s.source for s in res.board} == {"traits"}
    namer._learn([red, blue], [U.descriptor(red), U.descriptor(blue)], res.board)
    namer._learn([blue], [U.descriptor(blue)], res.bench)
    assert list(tmp_path.rglob("*.png")) == []


def test_qa_non_champion_unit_is_not_forced_nor_persisted(static, table, tmp_path):
    """보드 = 코그모 1기 + 특성 없는 유닛 1기(체력바 있음). 패널은 코그모 특성만 센다 → 풀이 {코그모}, 칸 2개.
    모델이 전혀 닮지 않은 칸까지 코그모로 '강제'하면 안 되고, 그 크롭이 디스크 라이브러리에 들어가면 안 된다."""
    ko, = ids(static, "코그모")
    kog, dummy = _img((0, 0, 255)), _img((255, 255, 255))
    namer = U.UnitNamer(library=U.UnitLibrary(save_dir=tmp_path), table=table, autolearn=True)
    res = U.name_units([U.descriptor(kog), U.descriptor(dummy)], [], namer.library,
                       U.TraitPanel(dict(table.contrib[ko])), table)
    namer._learn([kog, dummy], [U.descriptor(kog), U.descriptor(dummy)], res.board)
    assert res.board[1].unit_id is None
    assert len(list(tmp_path.rglob("*.png"))) <= 1


def test_qa_disk_library_folders_are_canonical_champion_ids(static):
    d = U.units_dir(static.set_number)
    if not d.is_dir():
        pytest.skip("유닛 라이브러리 없음(gitignore)")
    bad = [p.name for p in d.iterdir()
           if p.is_dir() and not p.name.startswith("_") and static.get("champions", p.name) is None]
    assert bad == []


# ---------------------------------------------------------------- 19 수정 라운드(QA FAIL-1 · 캐시 키 · 오독 완화)
def test_one_slot_forced_name_is_not_collected_board_crops_are_never_collected(static, table, tmp_path):
    """25 → 30: 디스크에 **바로** 쓰는 자동 학습은 없고(승인 폴더 그대로), 30 수집 정책(벤치에서만)으로 보드 칸은 이름이
    강제(forced)로 확실해도 검토 대기에도 넣지 않는다."""
    from tft_advisor.vision.board import BoardRead, UnitSlot
    from tft_advisor.vision.unit_db import FrameContext, UnitCollector, UnitImageDB

    ko, = ids(static, "코그모")
    kog = _img((0, 0, 255))
    root = tmp_path / "a"
    col = UnitCollector(UnitImageDB(root))
    p = U.TraitPanel(dict(table.contrib[ko]), confidence=0.8)
    res = U.name_units([U.descriptor(kog)], [], U.UnitLibrary(), p, table)
    assert [(s.unit_id, s.source) for s in res.board] == [(ko, "forced")]
    read = BoardRead(board=(UnitSlot(star=1, hex=(0, 0), unit_id=res.board[0].unit_id),))
    assert col.observe(FrameContext(at=1.0), read, res, [kog], [], p.confidence) == []
    assert list(root.rglob("*.png")) == []


def test_library_fallback_stays_inside_the_solution_union(static, table):
    """풀이가 여럿이라 구속을 못 쓸 때도, 라이브러리 이름은 **어느 풀이에도 없는** 챔피언이면 받지 않는다."""
    red, blue = _img((0, 0, 255)), _img((255, 0, 0))
    p = panel(static, {"개화": 1, "주문술사": 1, "지옥불": 1, "적응가": 1, "약탈자": 1})   # {아리|카르마, 아칼리}
    assert len(U.solve_board_sets(p, 2, table)) == 2
    lib = U.library_from([("NOT_A_SOLUTION", red), ("ALSO_NOT", blue)])
    res = U.name_units([U.descriptor(red), U.descriptor(blue)], [], lib, p, table)
    assert [s.unit_id for s in res.board] == [None, None]
    res = U.name_units([U.descriptor(red), U.descriptor(blue)], [], lib, None, table)
    assert [s.unit_id for s in res.board] == ["NOT_A_SOLUTION", "ALSO_NOT"]      # 구속이 없으면 라이브러리 그대로


def test_trait_panel_cache_key_sees_count_and_ladder_digits():
    from tft_advisor.vision.recognizer import panel_text_region, panel_unchanged

    rng = np.random.default_rng(1)
    base = np.full((546, 195, 3), 70, np.uint8)
    base[:, 160:] = rng.integers(170, 230, (546, 35, 3), dtype=np.uint8)   # 오른쪽 하늘(글자 영역 밖, 프레임마다 흔들림)
    cv2.putText(base, "1", (57, 300), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 2)
    cv2.putText(base, "1/2", (84, 318), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
    k0 = panel_text_region(base)
    same = base.copy()
    same[:, 160:] = rng.integers(170, 230, (546, 35, 3), dtype=np.uint8)   # 하늘만 바뀐 다음 프레임
    same = cv2.add(same, np.full_like(same, 3))                             # 밝기 흔들림
    assert panel_unchanged(k0, panel_text_region(same))
    count = base.copy()
    count[285:305, 55:75] = 70
    cv2.putText(count, "2", (57, 300), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 2)
    assert not panel_unchanged(k0, panel_text_region(count))
    ladder = base.copy()
    ladder[308:320, 84:110] = 70
    cv2.putText(ladder, "1/3", (84, 318), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
    assert not panel_unchanged(k0, panel_text_region(ladder))


def test_raw_trait_panel_cache_hits_on_the_same_panel_and_misses_on_a_new_one(static):
    _raw_ready()
    from tft_advisor.vision.recognizer import Recognizer
    from tft_advisor.vision.regions import FrameMapper

    rec = Recognizer(static=static, unit_template_dir=Path("/nonexistent"))
    calls = []
    real = rec.read_trait_panel
    rec.read_trait_panel = lambda *a, **k: calls.append(1) or real(*a, **k)

    def read(name):
        img = _load(name)
        m = FrameMapper.for_image(img, rec.content_for(img, None))
        return rec.cached_trait_panel(img, m, rec.profile_for(m.box[2], m.box[3]))

    a = read("2-6 전투 전")
    b = read("악의 여단")           # 같은 패널(몇 초 뒤 다른 프레임) → 캐시
    assert len(calls) == 1 and a == b
    read("2-5 전투 전")             # 다른 패널 → 다시 읽는다
    assert len(calls) == 2


# ---------------------------------------------------------------- QA 20: FAIL-1 재검증(공격 변형)
@pytest.mark.parametrize("case", ["kog+dummy/lib-kog", "kog+dummy+dummy2", "dummy+dummy2/panel-kog",
                                  "kog+ak+dummy/lib-both", "kog+ak+dummy/lib-kog", "kog+ak+dummy/empty",
                                  "kog+dummy/lib-kog-is-dummy"])
def test_qa20_traitless_unit_variants_never_named_nor_persisted(static, table, tmp_path, case):
    """특성 없는 유닛(훈련 봇·골렘)이 섞인 보드: 그 칸은 이름을 받지 않고(마지막 변형 제외), 어떤 경우에도
    디스크에 쓰지 않는다(칸이 둘 이상이면 `_persist_ok`가 거짓)."""
    ko, ak = ids(static, "코그모", "아칼리")
    kog, dummy, dummy2, akali = (_img((0, 0, 255)), _img((255, 255, 255)), _img((250, 255, 255)),
                                 _img((255, 0, 0)))
    kog_p = {t: 1 for t in table.contrib[ko]}
    both = dict(kog_p)
    for t in table.contrib[ak]:
        both[t] = both.get(t, 0) + 1
    lib_kog, lib_both = [(ko, kog)], [(ko, kog), (ak, akali)]
    board, counts, lib, dummy_at = {
        "kog+dummy/lib-kog": ([kog, dummy], kog_p, lib_kog, [1]),
        "kog+dummy+dummy2": ([kog, dummy, dummy2], kog_p, [], [1, 2]),
        "dummy+dummy2/panel-kog": ([dummy, dummy2], kog_p, [], [0, 1]),
        "kog+ak+dummy/lib-both": ([kog, akali, dummy], both, lib_both, [2]),
        "kog+ak+dummy/lib-kog": ([kog, akali, dummy], both, lib_kog, [2]),
        "kog+ak+dummy/empty": ([kog, akali, dummy], both, [], [2]),
        "kog+dummy/lib-kog-is-dummy": ([kog, dummy], kog_p, [(ko, dummy)], []),   # 라이브러리 자체가 틀린 경우
    }[case]
    library = U.UnitLibrary(save_dir=tmp_path)
    for c, im in lib:
        library.samples.append((c, U.descriptor(im)))
    namer = U.UnitNamer(library=library, table=table, autolearn=True, _base=len(library))
    descs = [U.descriptor(x) for x in board]
    p = U.TraitPanel(counts, confidence=0.9)
    res = U.name_units(descs, [], library, p, table)
    assert all(res.board[i].unit_id is None for i in dummy_at), res.board
    assert all(s.source != "forced" for s in res.board)
    ok = namer._persist_ok(res, descs, p)
    namer._learn(board, descs, res.board, persist_ok=ok)
    assert not ok and list(tmp_path.rglob("*.png")) == []


# ---------------------------------------------------------------- 23: 라이브 2(다른 맵) 실패 수정
LIVE2 = "live2 2-3 준비"
PANEL_LIVE2 = {"개화": 2, "소환사": 1, "전쟁기계": 1, "주문술사": 1, "처형자": 1, "나무정령": 1}


def test_library_only_name_needs_strict_scores_or_corroboration():
    """라이브 2 실패: 표본 없는 금색 갑옷 유닛이 아칼리(0.514 / 차 0.103)로 이름 붙었다. 이제 뒷받침 없는 이름은
    엄격 임계(0.62 / 0.20)를, 뒷받침된 이름은 0.55 / 0.12를 넘어야 한다."""
    live_fail = {"AKALI": 0.514, "ORNN": 0.411}
    assert U._tiered_library_name(live_fail, frozenset()).unit_id is None
    assert U._tiered_library_name(live_fail, frozenset({"AKALI"})).unit_id is None     # 뒷받침돼도 0.55 미만
    mid = {"ORNN": 0.589, "AKALI": 0.32}                                               # 라이브 2 오른 칸 실측
    assert U._tiered_library_name(mid, frozenset()).unit_id is None
    got = U._tiered_library_name(mid, frozenset({"ORNN"}))
    assert got.unit_id == "ORNN" and got.corroborated and got.confidence <= U.LIB_CONF_CAP
    strong = {"ORNN": 0.70, "AKALI": 0.40}
    got = U._tiered_library_name(strong, frozenset())
    assert got.unit_id == "ORNN" and not got.corroborated and got.confidence <= U.LIB_STRICT_CONF_CAP
    # 1위가 허용 집합 밖이면 모름(후보를 먼저 줄여 표본 있는 후보가 공짜로 1위가 되는 일도 없다)
    assert U._tiered_library_name(strong, frozenset({"AKALI"}), frozenset({"AKALI"})).unit_id is None


def test_uncorroborated_names_need_consecutive_agreement(table):
    from tft_advisor.vision.board import UnitSlot

    namer = U.UnitNamer(library=U.UnitLibrary(), table=table)
    slots = [UnitSlot(bench_slot=2), UnitSlot(bench_slot=3)]
    weak = [U.SlotName("A", 0.7, "library", 0.7, 0.3, corroborated=False),
            U.SlotName("B", 0.9, "library", 0.8, 0.4)]                  # 뒷받침된 이름은 바로 나간다
    first = namer._agree(slots, weak, "bench")
    assert [n.unit_id for n in first] == [None, "B"]
    second = namer._agree(slots, weak, "bench")
    assert [n.unit_id for n in second] == ["A", "B"]
    # 다른 이름이 나오면 처음부터, 칸이 한 번 비면(연속 끊김) 처음부터
    other = [U.SlotName("C", 0.7, "library", 0.7, 0.3, corroborated=False), weak[1]]
    assert namer._agree(slots, other, "bench")[0].unit_id is None
    namer._agree(slots[1:], weak[1:], "bench")
    assert namer._agree(slots, weak, "bench")[0].unit_id is None
    namer.agree_frames = 1
    assert namer._agree(slots, weak, "bench")[0].unit_id == "A"


def test_missed_board_unit_still_uses_the_trait_solutions(static, table):
    """찾은 보드 칸(2) < 특성 풀이 크기(3): 풀이를 버리지 않고 쓴다. 모든 풀이에 든 요릭은 '보드에 있다'."""
    d = [np.random.default_rng(i).random(U.descriptor(np.zeros((112, 112, 3), np.uint8)).shape).astype(np.float32)
         for i in range(2)]
    res = U.name_units(d, [], U.UnitLibrary(), panel(static, PANEL_LIVE2), table)
    assert res.solutions == 3 and res.missed == 1
    assert res.common == frozenset(ids(static, "요릭"))
    assert all(s.unit_id is None for s in res.board) and res.board_set is None and res.unplaced == ()
    # 풀이가 하나면 집합은 알되, 가려진 유닛 때문에 칸 수가 안 맞으므로 unplaced(칸 수와 같을 때만)는 비운다
    one = {"개화": 1, "전쟁기계": 1, "소환사": 1, "나무정령": 1, "엄호대": 1}           # {요릭, 오른}
    res = U.name_units(d[:1], [], U.UnitLibrary(), panel(static, one), table)
    assert res.board_set == frozenset(ids(static, "요릭", "오른")) and res.missed == 1 and res.unplaced == ()


def test_missed_unit_placement_names_real_slots_by_similarity(static, table):
    red, blue = _img((0, 0, 255)), _img((255, 0, 0))
    yo, orn = ids(static, "요릭", "오른")
    lib = U.library_from([(yo, red), (orn, blue)])
    one = {"개화": 1, "전쟁기계": 1, "소환사": 1, "나무정령": 1, "엄호대": 1}
    res = U.name_units([U.descriptor(red)], [], lib, panel(static, one), table)
    assert res.missed == 1 and res.board[0].unit_id == yo and res.board[0].source == "traits"


def test_hints_corroborate_but_never_name_alone(table):
    red, grey = _img((0, 0, 255)), _img((128, 128, 128))
    lib = U.library_from([("RED", red)])
    res = U.name_units([], [U.descriptor(grey)], lib, None, table, hints={"RED"})
    assert res.bench[0].unit_id is None                       # 힌트만으로는 이름을 붙이지 않는다
    res = U.name_units([], [U.descriptor(red)], lib, None, table, hints={"RED"})
    assert res.bench[0].unit_id == "RED" and res.bench[0].corroborated


def _live2_ready():
    if not (RAW / f"{LIVE2}.png").is_file():
        pytest.skip("라이브 2 원본 캡처 없음(tests/fixtures/screens/raw, gitignore)")
    _raw_ready()


def test_raw_live2_board_unit_under_the_tactician_label_is_found(static):
    """전략가 이름표 '<내 소환사명> [4]'가 체력바를 가린 유닛도 찾는다(보드 3 = 워터마크 3/4). 이름표 자체는 유닛이 아니다."""
    _live2_ready()
    from tft_advisor.vision.board import BoardReader
    from tft_advisor.vision.recognizer import Recognizer
    from tft_advisor.vision.regions import FrameMapper

    rec = Recognizer(static=static, unit_template_dir=Path("/nonexistent"))
    img = _load(LIVE2)
    m = FrameMapper.for_image(img, rec.content_for(img, None))
    read = BoardReader().read(img, m, rec.profile_for(m.box[2], m.box[3]))
    assert len(read.board) == 3 and len(read.bench) == 5
    assert [u.star for u in read.bench] == [1, 1, 1, 1, 2]
    hidden = [u for u in read.board if abs(u.anchor[0] - 0.373) < 0.01]
    assert len(hidden) == 1 and hidden[0].star == 1


def _live2_read(rec, frames: int = 1):
    for _ in range(frames):
        rec.recognize(_load(LIVE2))
    return rec.last_board_read


def test_raw_live2_names_nothing_wrong(static, heldout):
    """라이브러리 = 옛 판(돌 맵) 2-2·2-5 확인 라벨. 새 맵(모래)에서 **틀린 이름 0**: 벤치 2(아칼리 아님)는 모름,
    벤치 3(오른)은 오른이거나 모름. 보드는 풀이 3개 → 칸 이름 없음, 공통 챔피언 요릭만 확실."""
    _live2_ready()
    heldout.unit_namer.agree_frames = U.AGREE_FRAMES
    read = _live2_read(heldout, frames=3)                        # 여러 프레임 일치까지 충분히
    truth = _truth(static, LIVE2)
    bench = {u.bench_slot: u.unit_id for u in read.bench}
    for s in truth["bench_slots"]:
        assert bench[s["slot"]] in (None, s["unit_id"]), (s, bench[s["slot"]])
    assert bench[2] is None and bench[3] in (None, ids(static, "오른")[0])
    # 풀이 3개 중 4코스트(이즈리얼·아리)가 없는 풀이는 {요릭, 유나라, 르블랑} 하나 — 상점 확률 4코스트 0%(30 보고)
    assert read.trait_solutions == 3 and read.missed_board == 0
    assert set(read.board_set) == set(ids(static, "요릭", "유나라", "르블랑"))
    assert read.board_common == tuple(ids(static, "요릭"))
    assert all(u.unit_id is None or u.unit_id in set(read.board_set) for u in read.board)


def test_raw_live2_purchase_hint_names_the_ornn_copy(static, heldout):
    """장부가 '오른을 샀다'고 알려 주면(힌트) 뒷받침된 이름으로 한 프레임에 붙는다. 금색 갑옷 칸은 여전히 모름."""
    _live2_ready()
    heldout.unit_namer.agree_frames = U.AGREE_FRAMES
    heldout.unit_namer.set_hints(ids(static, "오른", "아칼리"))    # 아칼리 힌트가 있어도 닮음이 모자라면 이름 없음
    try:
        read = _live2_read(heldout)
    finally:
        heldout.unit_namer.reset()
    bench = {u.bench_slot: (u.unit_id, u.name_source) for u in read.bench}
    assert bench[3] == (ids(static, "오른")[0], "library")
    assert bench[2][0] is None


def test_raw_live2_disk_library_names_nothing_wrong(static):
    """사용자 디스크 라이브러리(`data/templates/18/units_screen`, gitignore)로도 틀린 이름 0 — 라이브에서 실패한 설정 그대로."""
    _live2_ready()
    from tft_advisor.vision.recognizer import Recognizer

    if not any(U.units_dir(static.set_number).rglob("*.png")):
        pytest.skip("디스크 유닛 라이브러리 없음")
    rec = Recognizer(static=static)
    rec.unit_namer.autolearn = False
    rec.unit_namer.library.save_dir = None
    read = _live2_read(rec, frames=3)
    truth = {s["slot"]: s["unit_id"] for s in _truth(static, LIVE2)["bench_slots"]}
    assert all(u.unit_id in (None, truth[u.bench_slot]) for u in read.bench), \
        [(u.bench_slot, u.unit_id, u.unit_conf) for u in read.bench]


def test_named_slots_carry_corroborated_flag_for_recog_view(table, monkeypatch):
    """QA 27 W1: `UnitSlot.corroborated`가 이름 판정의 뒷받침 여부를 그대로 싣는다(이름 없으면 None).
    `app.recog_view.is_guess`는 이 값이 있으면 그것으로 '(추정)'을 정한다."""
    from tft_advisor.vision.board import BoardRead, UnitSlot
    from tft_advisor.vision.regions import FrameMapper

    names = U.BoardNames(board=(U.SlotName("A", 0.7, "library", 0.7, 0.3, corroborated=False),),
                         bench=(U.SlotName("B", 0.9, "traits"), U.SlotName(None, 0.0, "none")))
    monkeypatch.setattr(U, "name_units", lambda *a, **k: names)
    namer = U.UnitNamer(library=U.UnitLibrary(), table=table, agree_frames=1)
    img = np.zeros((1080, 1920, 3), np.uint8)
    read = BoardRead(board=(UnitSlot(star=1, hex=(0, 0), anchor=(0.5, 0.5)),),
                     bench=(UnitSlot(star=1, bench_slot=0, anchor=(0.3, 0.8)),
                            UnitSlot(star=1, bench_slot=1, anchor=(0.35, 0.8))))
    out = namer.name(img, FrameMapper.for_image(img), read, None)
    assert [u.corroborated for u in (*out.board, *out.bench)] == [False, True, None]
    from tft_advisor.app.recog_view import is_guess
    assert [is_guess(u) for u in (*out.board, *out.bench)] == [True, False, False]


# ---------------------------------------------------------------- 30 보고: 다른 맵 표본만으로는 뒷받침 없는 이름을 붙이지 않는다
def test_same_arena_signature_rule():
    assert U.same_arena("0a080a", "0b080a") and U.same_arena("0a080a", "09080a")      # 밝기 한 단계는 같은 맵
    assert not U.same_arena("0a080a", "090809")                                          # 모래 vs 돌: 색이 다르다
    assert not U.same_arena(None, "0a080a") and not U.same_arena("0a080a", "zz")


def test_uncorroborated_name_needs_a_same_arena_sample():
    """test.png(돌 맵) 벤치 0의 레오나를 모래 맵 세주아니 표본이 0.75로 불렀다 → 같은 맵 표본이 없으면 이름 없음."""
    red = _img((0, 0, 255))
    beach = U.library_from([("SEJ", red)], arena="0a080a")
    d = [U.descriptor(red)]
    assert U.name_units([], d, beach, None, None).bench[0].unit_id == "SEJ"                     # 맵을 모르면 예전 규칙
    assert U.name_units([], d, beach, None, None, arena="0a080a").bench[0].unit_id == "SEJ"     # 같은 맵
    assert U.name_units([], d, beach, None, None, arena="090809").bench[0].unit_id is None      # 다른 맵
    unknown = U.library_from([("SEJ", red)])                                                     # 맵 모르는 표본(옛 label_*)
    assert U.name_units([], d, unknown, None, None, arena="090809").bench[0].unit_id is None
    # 뒷받침(힌트)이 있으면 맵과 상관없이 예전 완화 임계
    assert U.name_units([], d, beach, None, None, arena="090809", hints=["SEJ"]).bench[0].unit_id == "SEJ"


def test_library_arena_list_follows_samples(tmp_path):
    import json

    root = tmp_path / "lib"
    (root / "DA_18_Akali_AD").mkdir(parents=True)
    img = _img((0, 0, 255))
    cv2.imencode(".png", img)[1].tofile(str(root / "DA_18_Akali_AD" / "purchase_a.png"))
    (root / "DA_18_Akali_AD" / "purchase_a.json").write_text(json.dumps({"arena": "0a080a"}), encoding="utf-8")
    cv2.imencode(".png", _img((255, 0, 0)))[1].tofile(str(root / "DA_18_Akali_AD" / "label_b.png"))
    lib = U.UnitLibrary.load(root)
    assert sorted(a or "" for a in lib.arenas) == ["", "0a080a"] and len(lib.arenas) == len(lib.samples)
    lib.add("DA_18_Akali_AD", img, arena="090809")
    lib.remove_at(0)
    assert len(lib.arenas) == len(lib.samples) == 2
    assert set(lib.scores_in_arena(U.descriptor(img), "090809")) == {"DA_18_Akali_AD"}
