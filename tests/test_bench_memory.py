"""벤치 체력바가 사라진 프레임(준비 끝·전환)에서 벤치를 잃지 않기 — `vision.bench_memory`(30 보고).

라이브 3: `live3 2-3 준비 끝.png`는 벤치 6기가 있는데 체력바가 하나도 없어 벤치 0칸으로 읽혔다(사용자: "벤치 기물을 전혀
못 읽는다"). 빈 칸 기준 그림(체력바가 보이던 프레임에서 배움) + 칸별 직전 유닛 그림으로 채운다. 틀린 칸(빈 칸을 유닛으로)은 0이어야 한다.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tft_advisor.vision import bench_memory as BM
from tft_advisor.vision.board import BoardRead, UnitSlot
from tft_advisor.vision.regions import FrameMapper, profile_for_frame

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "screens" / "raw"
LIVE2 = RAW / "live2 2-3 준비.png"
L3A = RAW / "live3 2-2 준비.png"
L3B = RAW / "live3 2-3 준비 끝.png"


# ---------------------------------------------------------------- 합성
def _frame(seed: int = 0) -> np.ndarray:
    """모래색 바닥 + 줄무늬(칸마다 같은 배경)."""
    img = np.full((1080, 1920, 3), (90, 170, 220), np.uint8)
    for x in range(0, 1920, 60):
        img[700:900, x:x + 20] = (200, 220, 240)
    return img


def _put_unit(img: np.ndarray, i: int, color=(120, 40, 160)) -> None:
    cx = int(416 + 121 * i)
    img[730:800, cx - 15:cx + 15] = color
    img[750:760, cx - 30:cx + 30] = (30, 30, 30)


def _read(bench_slots: list[int], board: tuple = ()) -> BoardRead:
    prof = profile_for_frame(1920, 1080)
    bench = tuple(UnitSlot(star=1, bench_slot=i, anchor=(prof.bench_cells[i].cx, prof.bench_cells[i].cy))
                  for i in bench_slots)
    return BoardRead(board=board, bench=bench, bars=len(bench) + len(board))


def _setup():
    img = _frame()
    return FrameMapper.for_image(img), profile_for_frame(1920, 1080)


def test_identical_patch_has_no_new_edges():
    m, prof = _setup()
    img = _frame()
    _put_unit(img, 2)
    p = BM.cell_patch(img, m, prof, 2)
    assert BM.new_edges(p, p) == 0.0
    empty = BM.cell_patch(_frame(), m, prof, 2)
    assert BM.new_edges(empty, p) >= BM.OCCUPIED_MIN     # 빈 칸 기준 대비 유닛
    assert BM.new_edges(p, empty) <= BM.EMPTY_MAX         # 유닛 → 빈 칸: 빈 칸 쪽에 새 윤곽이 없다


def test_bars_hidden_keeps_same_units_and_finds_new_ones():
    m, prof = _setup()
    mem = BM.BenchMemory()
    a = _frame()
    for i in (0, 1):
        _put_unit(a, i)
    named = _read([0, 1])
    named = replace(named, bench=(replace(named.bench[0], unit_id="DA_18_Akali_AD", unit_conf=0.9,
                                          name_source="traits"), named.bench[1]))
    assert mem.apply(a, m, prof, named) is named          # 체력바 보이는 프레임: 배우기만
    b = a.copy()
    _put_unit(b, 5, color=(20, 160, 40))                  # 체력바 없이 새 유닛
    out = mem.apply(b, m, prof, _read([]))
    assert out.bench_held
    got = {u.bench_slot: u for u in out.bench}
    assert set(got) == {0, 1, 5}
    assert got[0].unit_id == "DA_18_Akali_AD" and got[0].name_source == "held" and got[0].star == 1
    assert got[5].unit_id is None and got[5].star is None


def test_unit_left_its_cell_is_dropped_when_empty_reference_exists():
    m, prof = _setup()
    mem = BM.BenchMemory()
    a = _frame()
    for i in (0, 1):
        _put_unit(a, i)
    mem.apply(a, m, prof, _read([0, 1]))       # 칸 1 빈 칸 기준을 먼저 배운다
    c = _frame()
    _put_unit(c, 0)
    mem.apply(c, m, prof, _read([0]))           # 칸 1이 비어 있는 프레임(바 있음) → 칸 1 빈 칸 기준
    d = a.copy()                                # 다시 칸 1에 유닛 → 바 있음
    mem.apply(d, m, prof, _read([0, 1]))
    e = c.copy()                                # 체력바 사라짐 + 칸 1 비었음
    out = mem.apply(e, m, prof, _read([]))
    assert {u.bench_slot for u in out.bench} == {0}


def test_sudden_drop_without_any_evidence_keeps_previous_bench_unnamed():
    """빈 칸 기준이 없어도 직전 벤치가 2기 이상이면(한 프레임에 둘을 팔 수 없다) 직전 판독으로 둔다(이름이 없던 칸은 이름 없이)."""
    m, prof = _setup()
    mem = BM.BenchMemory()
    a = _frame()
    for i in range(9):
        _put_unit(a, i)
    mem.apply(a, m, prof, _read(list(range(9))))
    b = _frame()
    for i in range(9):
        _put_unit(b, i, color=(10, 200, 200))
        b[735:745, 416 + 121 * i - 25:416 + 121 * i + 25] = (250, 250, 250)
    out = mem.apply(b, m, prof, _read([]))
    # 그림이 같으면 "그대로"(held, 성급 유지), 다르면 자리만(none, 성급 모름) — 어느 쪽도 이름은 없다
    assert len(out.bench) == 9 and all(u.unit_id is None for u in out.bench)
    assert all((u.name_source == "held" and u.star == 1) or (u.name_source == "none" and u.star is None)
               for u in out.bench)


def test_other_arena_is_not_compared():
    m, prof = _setup()
    mem = BM.BenchMemory()
    a = _frame()
    _put_unit(a, 0)
    mem.apply(a, m, prof, _read([0]))
    other = np.full_like(a, (160, 60, 60))           # 다른 맵(원정 등)
    out = mem.apply(other, m, prof, _read([]))
    assert out.bench == () and not out.bench_held


def test_empty_bench_stays_empty():
    m, prof = _setup()
    mem = BM.BenchMemory()
    a = _frame()
    _put_unit(a, 0)
    mem.apply(a, m, prof, _read([0]))
    mem.apply(_frame(), m, prof, _read([]))          # 유일한 유닛을 팔았다(바 0) — 1기였으니 직전 판독으로 잡지 않는다
    out = mem.apply(_frame(), m, prof, _read([]))
    assert out.bench == ()


# ---------------------------------------------------------------- 실제 캡처(있을 때만)
@pytest.fixture(scope="module")
def recognizer():
    if not (L3A.is_file() and L3B.is_file() and LIVE2.is_file()):
        pytest.skip("라이브 캡처 없음(raw/, gitignore)")
    from tft_advisor.static_data import load_static
    from tft_advisor.vision.ocr import RapidOcrEngine
    from tft_advisor.vision.recognizer import Recognizer

    if RapidOcrEngine.available_backend() is None:
        pytest.skip("OCR 백엔드 없음")
    return Recognizer(static=load_static(), unit_template_dir=Path("/nonexistent"))


def _run(rec, *paths):
    from tft_advisor.vision.capture import load_image

    rec.bench_memory.reset()
    for p in paths:
        rec.recognize(load_image(p))
    return rec.last_board_read


def test_raw_live3_bench_without_bars(recognizer):
    """같은 맵의 앞선 캡처(라이브 2 → 라이브 3 2-2)로 배운 뒤 준비 끝 프레임: 벤치 6기 전부, 빈 칸 5·6·8은 비움."""
    read = _run(recognizer, LIVE2, L3A, L3B)
    assert read.bench_held
    assert {u.bench_slot for u in read.bench} == {0, 1, 2, 3, 4, 7}
    kept = {u.bench_slot: u for u in read.bench}
    assert kept[0].star == 1 and kept[1].star == 1 and kept[2].star == 1     # 그대로 선 유닛(성급 유지)
    assert kept[7].star is None                                               # 새로 찾은 칸(성급 모름)
    assert len(read.board) == 4                                               # 보드는 직전 판독(전투 자리로 옮기는 중)


def test_raw_live3_bench_without_bars_no_empty_reference(recognizer):
    """앱을 이 판 중간에 켜서 빈 칸 기준이 적을 때: 유닛이 떠난 칸 5는 이름 없는 직전 판독으로 남을 수 있다(이름은 절대 없음)."""
    read = _run(recognizer, L3A, L3B)
    slots = {u.bench_slot: u for u in read.bench}
    assert {0, 1, 2, 3, 4, 7} <= set(slots) <= {0, 1, 2, 3, 4, 5, 7}
    assert slots.get(5) is None or (slots[5].unit_id is None and slots[5].star is None)


def test_raw_live3_end_frame_alone_has_no_memory(recognizer):
    read = _run(recognizer, L3B)
    assert read.bench == () and not read.bench_held


# ---------------------------------------------------------------- QA 32 추가(합성)
def _put_tactician(img: np.ndarray, i: int) -> None:
    """전략가(꼬마 전설이): 벤치 줄 위 두꺼운 초록 이름표 막대(12px) + 칸 위의 몸."""
    cx = int(416 + 121 * i)
    img[660:672, cx - 40:cx + 40] = (40, 200, 40)          # BGR 초록, 두께 12px(유닛 체력바 4~5px보다 두껍다)
    img[725:790, cx - 25:cx + 25] = (200, 200, 60)


def _put_other_unit(img: np.ndarray, i: int) -> None:
    """다른 유닛: 윤곽이 다른 모양(넓고 낮은 몸 + 머리). 색만 바꾼 같은 모양은 새 윤곽이 거의 없어 '같은 그림'으로 본다
    (새 윤곽 비교는 색을 보지 않는다 — 실측 다른 유닛 0.086~0.092)."""
    cx = int(416 + 121 * i)
    img[770:805, cx - 40:cx + 40] = (20, 60, 200)
    img[735:760, cx - 8:cx + 8] = (230, 230, 30)


def test_qa32_tactician_is_never_a_bench_unit_nor_an_empty_reference():
    m, prof = _setup()
    mem = BM.BenchMemory()
    a = _frame()
    for i in (0, 1):
        _put_unit(a, i)
    _put_tactician(a, 3)
    assert 3 in BM.tactician_cells(a, m, prof)
    mem.apply(a, m, prof, _read([0, 1]))                  # 배우기: 전략가가 선 칸 3은 빈 칸 기준으로 배우지 않는다
    assert not mem.cells.get(3, BM._Cell()).empty
    b = _frame()
    for i in (0, 1):
        _put_unit(b, i)
    mem.apply(b, m, prof, _read([0, 1]))                  # 칸 3 빈 칸 기준(전략가 없음)
    c = b.copy()
    _put_tactician(c, 3)                                  # 체력바 사라짐 + 전략가가 칸 3 위에
    out = mem.apply(c, m, prof, _read([]))
    assert {u.bench_slot for u in out.bench} == {0, 1}


def test_qa32_changed_picture_never_keeps_the_name_and_reset_forgets():
    m, prof = _setup()
    mem = BM.BenchMemory()
    a = _frame()
    for i in (0, 1):
        _put_unit(a, i)
    named = _read([0, 1])
    named = replace(named, bench=tuple(replace(u, unit_id="DA_18_Akali_AD", unit_conf=0.9, name_source="traits")
                                       for u in named.bench))
    mem.apply(a, m, prof, named)
    b = _frame()
    for i in (0, 1):
        _put_other_unit(b, i)                             # 같은 칸, 다른 모양(다른 유닛)
    out = mem.apply(b, m, prof, _read([]))
    assert out.bench_held and len(out.bench) == 2
    assert all(u.unit_id is None for u in out.bench)      # 직전 이름을 새 그림에 붙이지 않는다
    mem.reset()                                           # 새 판
    out = mem.apply(b, m, prof, _read([]))
    assert out.bench == () and not out.bench_held


def test_qa32_changed_picture_without_empty_reference_drops_star_and_items():
    """QA 32 W1(35에서 고침): 기준 없음/애매 칸은 자리만 — 이름·성급·아이템을 이어 쓰지 않는다."""
    m, prof = _setup()
    mem = BM.BenchMemory()
    a = _frame()
    for i in (0, 1):
        _put_unit(a, i)
    r = _read([0, 1])
    r = replace(r, bench=(replace(r.bench[0], star=2, items=("DA_Deathblade",), item_count=1), r.bench[1]))
    mem.apply(a, m, prof, r)                              # 칸 0·1은 늘 차 있었다 → 빈 칸 기준 없음
    b = _frame()
    for i in (0, 1):
        _put_other_unit(b, i)
    out = mem.apply(b, m, prof, _read([]))
    s0 = next(u for u in out.bench if u.bench_slot == 0)
    assert s0.unit_id is None and s0.star is None and not s0.items
