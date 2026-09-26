"""vision 16 (2026-09-23): 보드·벤치 유닛 판독(자리·성급·장착 아이템).

- 규칙은 **합성 프레임**으로 고정한다(사용자 원본 캡처는 공개 저장소에 올릴 수 없다).
  합성 유닛은 1080p 실측 기하 그대로 그린다: 테두리 2px + 초록 64x4 + 왼쪽 성급 배지 + 아래 아이템 칸 25px.
- `tests/fixtures/screens/raw/`(gitignore)가 있으면 실제 캡처 회귀도 돈다. 없으면 skip.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tft_advisor.contracts import ScreenMode  # noqa: E402
from tft_advisor.vision import board as B  # noqa: E402
from tft_advisor.vision.regions import (  # noqa: E402
    BAR_RISE, BOARD_COLS, BOARD_ROWS, SET18_16X9, SET18_16X10, FrameMapper, board_hex_px,
)
from tft_advisor.vision.screen_mode import ModeSignals, classify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "screens" / "raw"
GREEN = (16, 253, 16)
DARK = (16, 16, 16)


def blank(w: int = 1920, h: int = 1080) -> np.ndarray:
    img = np.empty((h, w, 3), np.uint8)
    img[:] = (120, 140, 160)          # 보드 흙바닥과 비슷한 밝기(테두리·배지와 확실히 다르다)
    return img


def paint_unit(img: np.ndarray, x: int, y: int, *, star: int | None = 1, items: int = 0,
               hp: float = 1.0, scale: float = 1.0) -> None:
    """(x, y) = 초록 체력바 왼쪽 위. 1080p 실측 기하로 유닛 하나를 그린다."""
    full = round(B.BAR_W * 1080 * scale)
    img[y - 2:y, x - 5:x + full + 5] = DARK                      # 위 테두리 2px
    img[y:y + max(2, round(4 * scale)), x:x + round(full * hp)] = GREEN
    if star is not None or items:
        bx1, bx2 = x + round(B.BADGE_DX1 * 1080), x + round(B.BADGE_DX2 * 1080)
        by1, by2 = y + round(B.BADGE_DY1 * 1080), y + round(B.BADGE_DY2 * 1080)
        img[by1:by2, bx1:bx2] = B.BADGE_OUTLINE                  # 배지 테두리(개수만 맞으면 된다)
        if star is not None:
            pal = B.STAR_PALETTE[star]
            for i in range(12):
                img[by1 + 2 + i // 4, bx1 + 3 + i % 4] = pal[i % len(pal)]
    left = x + round(B.ITEM_DX0 * 1080)
    pitch = B.ITEM_PITCH * 1080
    for i in range(items):
        x1, x2 = left + round(pitch * i), left + round(pitch * (i + 1))
        img[y + 11:y + 34, x1:x2] = (60, 90, 200)
        img[y + 11:y + 34, x2 - 1:x2 + 1] = (0, 0, 0)            # 칸 경계선
        img[y + 11:y + 34, x1:x1 + 1] = (0, 0, 0)


def mapper(img: np.ndarray) -> FrameMapper:
    return FrameMapper.for_image(img)


# ---------------------------------------------------------------- 기하(프로파일)
def test_board_and_bench_geometry_is_measured_and_derivable():
    P = SET18_16X9
    assert len(P.board_hexes) == BOARD_ROWS * BOARD_COLS == 28
    assert len(P.bench_cells) == 9
    for r in P.board_hexes + P.bench_cells + (P.board_area, P.bench_area):
        assert 0 <= r.x1 < r.x2 <= 1 and 0 <= r.y1 < r.y2 <= 1
    # 1080p 실측: 줄 0(내 쪽 맨 앞) y=446, 줄 3 y=671. 줄 3은 반 칸 어긋나 있다(육각 격자).
    assert board_hex_px(0, 3)[0] == pytest.approx(909, abs=1)
    assert board_hex_px(3, 0)[0] == pytest.approx(580, abs=1)
    assert board_hex_px(3, 6)[0] == pytest.approx(1348, abs=1)
    assert board_hex_px(0, 0)[1] == pytest.approx(453, abs=1)
    # 모든 칸이 탐색 영역 안에 있어야 한다(체력바 높이 오차 포함)
    for k, r in enumerate(P.board_hexes):
        assert P.board_area.x1 < r.cx < P.board_area.x2, k
        assert P.board_area.y1 < r.cy - BAR_RISE < P.board_area.y2, k
    for i, r in enumerate(P.bench_cells):
        assert P.bench_area.x1 < r.cx < P.bench_area.x2 and P.bench_area.y1 < r.cy < P.bench_area.y2, i
    # 16:10은 16:9에서 유도한다(가로만 중앙 기준으로 벌어지고 세로는 그대로)
    q = SET18_16X10
    assert len(q.board_hexes) == 28 and len(q.bench_cells) == 9
    assert q.board_hexes[0].cy == pytest.approx(P.board_hexes[0].cy)
    k = (16 / 9) / (16 / 10)
    assert q.board_hexes[0].cx - 0.5 == pytest.approx((P.board_hexes[0].cx - 0.5) * k)


def test_board_rows_are_ordered_front_to_back():
    """줄 0 = 내 쪽 맨 앞(화면에서 가장 위) … 줄 3 = 벤치에 가장 가까운 줄. 벤치는 그보다 더 아래다."""
    ys = [board_hex_px(r, 3)[1] for r in range(BOARD_ROWS)]
    assert ys == sorted(ys)
    assert SET18_16X9.bench_cells[0].cy > SET18_16X9.board_hexes[-1].cy - BAR_RISE


# ---------------------------------------------------------------- 체력바 찾기
def test_finds_ally_bar_with_frame_and_badge():
    img = blank()
    paint_unit(img, 800, 400, star=2, items=2)
    bars = B.find_ally_bars(img, mapper(img), SET18_16X9.board_area)
    assert len(bars) == 1
    assert (bars[0].x, bars[0].y) == (800, 400)
    assert bars[0].cx == pytest.approx(832)
    assert bars[0].hp_frac == pytest.approx(1.0)


def test_partial_health_bar_keeps_left_edge_and_center():
    img = blank()
    paint_unit(img, 800, 400, hp=0.45)
    (bar,) = B.find_ally_bars(img, mapper(img), SET18_16X9.board_area)
    assert bar.x == 800 and bar.cx == pytest.approx(832)     # 피가 깎여도 유닛 위치는 그대로다
    assert bar.hp_frac < 0.6


def test_green_blob_without_badge_is_not_a_bar():
    """초록 유닛 모델·특성 이펙트가 만든 가로 덩어리는 성급 배지가 없어 걸러진다."""
    img = blank()
    img[398:404, 780:900] = GREEN            # 두껍고 테두리·배지가 없는 초록 덩어리
    assert B.find_ally_bars(img, mapper(img), SET18_16X9.board_area) == []


def test_bar_in_shadow_without_bright_outer_row_is_rejected():
    img = blank()
    paint_unit(img, 800, 400)
    img[390:400, 780:880] = DARK             # 바 위가 계속 어둡다 = 그림자 속 초록 (테두리가 아니다)
    assert B.find_ally_bars(img, mapper(img), SET18_16X9.board_area) == []


# ---------------------------------------------------------------- 성급
@pytest.mark.parametrize("star", [1, 2])
def test_star_badge_palette(star):
    img = blank()
    paint_unit(img, 800, 400, star=star)
    (bar,) = B.find_ally_bars(img, mapper(img), SET18_16X9.board_area)
    got, conf = B.read_star(img, bar, 1080)
    assert got == star and conf == B.STAR_CONF


def test_unknown_star_badge_is_none_not_a_guess():
    """3성(금색) 배지는 표본이 없다 → 팔레트에 없는 배지는 star=None(모름). 지어내지 않는다."""
    img = blank()
    paint_unit(img, 800, 400, star=None, items=1)     # 배지 테두리만 있고 칠은 없다
    (bar,) = B.find_ally_bars(img, mapper(img), SET18_16X9.board_area)
    assert B.read_star(img, bar, 1080) == (None, 0.0)


def test_star_palettes_do_not_overlap():
    a = {tuple(c) for c in B.STAR_PALETTE[1]}
    b = {tuple(c) for c in B.STAR_PALETTE[2]}
    assert not a & b and B.BADGE_OUTLINE not in a | b


# ---------------------------------------------------------------- 장착 아이템
@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_item_cell_count(n):
    img = blank()
    paint_unit(img, 800, 400, items=n)
    (bar,) = B.find_ally_bars(img, mapper(img), SET18_16X9.board_area)
    assert B.item_cell_count(img, bar, 1080) == n


def test_read_items_reports_count_even_when_icon_unknown():
    """아이콘을 못 알아봐도 **칸 수**는 낸다 → app이 "아이템 3개 낀 유닛"이라는 사실을 잃지 않는다."""
    img = blank()
    paint_unit(img, 800, 400, items=3)
    (bar,) = B.find_ally_bars(img, mapper(img), SET18_16X9.board_area)
    ids, count, _ = B.read_items(img, bar, 1080, None, None)
    assert count == 3 and ids == ()


# ---------------------------------------------------------------- 자리 배정
def test_bars_at_hex_anchors_map_back_to_those_hexes():
    img = blank()
    P = SET18_16X9
    want = [(0, 0), (1, 3), (2, 6), (3, 4)]
    for r, c in want:
        hx = P.board_hexes[r * BOARD_COLS + c]
        paint_unit(img, round(hx.cx * 1920) - 32, round((hx.cy - BAR_RISE) * 1080), star=1)
    read = B.BoardReader().read(img, mapper(img), P)
    assert [u.hex for u in read.board] == want
    assert all(u.star == 1 and u.bench_slot is None for u in read.board)


def test_bench_slots_are_assigned_left_to_right():
    img = blank()
    P = SET18_16X9
    for i in (0, 4, 8):
        cell = P.bench_cells[i]
        paint_unit(img, round(cell.cx * 1920) - 32, round(cell.cy * 1080), star=2, items=1)
    read = B.BoardReader().read(img, mapper(img), P)
    assert [u.bench_slot for u in read.bench] == [0, 4, 8]
    assert [u.item_count for u in read.bench] == [1, 1, 1]
    assert read.board == ()


def test_one_unit_per_slot():
    """두 바가 같은 칸에 가장 가까워도 칸은 하나씩만 배정한다(가까운 쪽이 이긴다)."""
    img = blank()
    P = SET18_16X9
    hx = P.board_hexes[3 * BOARD_COLS + 3]
    x, y = round(hx.cx * 1920) - 32, round((hx.cy - BAR_RISE) * 1080)
    paint_unit(img, x, y, star=1)
    paint_unit(img, x + 30, y + 18, star=2)
    read = B.BoardReader().read(img, mapper(img), P)
    assert len(read.board) == 2
    assert len({u.hex for u in read.board}) == 2          # 같은 칸에 둘을 넣지 않는다


def test_unplaced_unit_keeps_star_and_items_with_lower_confidence():
    """칸에 못 붙여도 성급·아이템은 버리지 않는다(자리만 None, 신뢰도만 낮춘다)."""
    img = blank()
    paint_unit(img, 500, 260, star=2, items=2)            # 격자에서 먼 자리
    read = B.BoardReader().read(img, mapper(img), SET18_16X9)
    assert len(read.board) == 1
    u = read.board[0]
    assert u.hex is None and u.star == 2 and u.item_count == 2
    assert u.confidence == B.SLOT_CONF_LOOSE < B.SLOT_CONF


# ---------------------------------------------------------------- app 병합 계약
def test_board_read_matches_app_unit_merge_contract():
    """`app.unit_merge.board_obs_from`이 vision 판독을 그대로 받아들여야 한다(계약: 16 보고서 §6)."""
    from tft_advisor.app.unit_merge import BoardRead as AppBoardRead
    from tft_advisor.app.unit_merge import UnitSlotRead, board_obs_from

    img = blank()
    P = SET18_16X9
    hx = P.board_hexes[1 * BOARD_COLS + 2]
    paint_unit(img, round(hx.cx * 1920) - 32, round((hx.cy - BAR_RISE) * 1080), star=2, items=1)
    cell = P.bench_cells[3]
    paint_unit(img, round(cell.cx * 1920) - 32, round(cell.cy * 1080), star=1)
    read = B.BoardReader().read(img, mapper(img), P)

    assert isinstance(read, AppBoardRead) and isinstance(read.board[0], UnitSlotRead)
    obs = board_obs_from(read)
    assert obs is not None and obs.count == 2
    assert obs.board[0].hex == (1, 2) and obs.board[0].star == 2 and obs.board[0].on_bench is False
    assert obs.bench[0].bench_slot == 3 and obs.bench[0].star == 1 and obs.bench[0].on_bench is True
    # vision은 정체를 읽지 않는다 → unit_id는 언제나 None이고, app 장부가 채운다
    assert all(s.unit_id is None for s in obs.board + obs.bench)


def test_vision_never_guesses_unit_id():
    assert B.UnitSlot().unit_id is None
    assert "unit_id" not in {f for f in B.UnitSlot.__dataclass_fields__ if f == "champion"}


# ---------------------------------------------------------------- 묶음 배선
def test_board_group_is_wired_and_skippable():
    from tft_advisor.vision.change import roi_groups
    from tft_advisor.vision.recognizer import DEFAULT_GROUPS, FIELD_GROUP, GROUPS, READ_MODES, VISION_ONLY_GROUPS

    assert "board" in GROUPS and "board" in DEFAULT_GROUPS and "board" in roi_groups(SET18_16X9)
    assert READ_MODES["board"] == {ScreenMode.PLANNING, ScreenMode.ITEM_SELECT}
    # GameState 필드를 만들지 않는다 → app의 필드 병합 표에는 없다
    assert VISION_ONLY_GROUPS == {"board"} and "board" not in FIELD_GROUP.values()


def test_icon_matcher_fast_path_equals_opencv():
    """`IconMatcher.match`의 행렬곱 경로가 `cv2.matchTemplate(TM_CCOEFF_NORMED)`와 같은 값을 내야 한다."""
    from tft_advisor.vision.icons import IconMatcher

    rng = np.random.default_rng(7)
    tpls = [(f"t{i}", rng.integers(0, 255, (28, 28, 3), dtype=np.uint8)) for i in range(6)]
    m = IconMatcher(tpls, size=28, search=34)
    crop = rng.integers(0, 255, (25, 25, 3), dtype=np.uint8)
    got = m.match(crop)
    src = cv2.resize(crop, (34, 34), interpolation=cv2.INTER_AREA)
    ref = {a: float(cv2.matchTemplate(src, t, cv2.TM_CCOEFF_NORMED).max()) for a, t in tpls}
    best = max(ref.items(), key=lambda kv: kv[1])
    assert got.api_name == best[0]
    assert got.score == pytest.approx(best[1], abs=2e-5)


# ---------------------------------------------------------------- 화면 상태 신호
def test_bench_bars_confirm_planning():
    """전투가 시작되면 벤치 유닛 체력바가 사라진다 → 벤치 바가 보이면 준비 확정(한 방향 신호)."""
    base = dict(shop_hud=True, shop_hud_by_ocr=True, augment_title=False, augment_names_matched=0,
                stage="4-2", dark=False)
    mode, conf = classify(ModeSignals(**base, bench_bars=5, enemy_bars=6))
    assert (mode, conf) == (ScreenMode.PLANNING, 0.9)
    mode, conf = classify(ModeSignals(**base, bench_bars=0, enemy_bars=6))
    assert mode == ScreenMode.COMBAT


def test_spectated_combat_without_shop_hud():
    """원정 전투(상대 아레나): 상점 HUD가 없고 스테이지가 보이며 적 체력바가 여럿 → 예전 UNKNOWN → COMBAT."""
    sig = ModeSignals(shop_hud=False, shop_hud_by_ocr=False, augment_title=False, augment_names_matched=0,
                      stage="3-7", dark=False, enemy_bars=6)
    assert classify(sig) == (ScreenMode.COMBAT, 0.7)
    # 캐러셀(라운드 4)은 체력바가 없다 → 그대로 캐러셀
    assert classify(ModeSignals(shop_hud=False, shop_hud_by_ocr=False, augment_title=False,
                                augment_names_matched=0, stage="3-4", dark=False))[0] == ScreenMode.CAROUSEL


# ---------------------------------------------------------------- 실제 캡처(있을 때만)
RAW_LABELS = sorted(RAW.glob("*.expected.json")) if RAW.is_dir() else []


@pytest.fixture(scope="module")
def raw_board_results():
    if not RAW_LABELS:
        pytest.skip("원본 캡처 없음(tests/fixtures/screens/raw, gitignore)")
    from tft_advisor.static_data import load_static
    from tft_advisor.vision.ocr import RapidOcrEngine

    if RapidOcrEngine.available_backend() is None:
        pytest.skip("OCR 백엔드 없음")
    from tft_advisor.vision.evaluate import evaluate_dir
    from tft_advisor.vision.recognizer import Recognizer

    rec = Recognizer(static=load_static())
    if len(rec.item_matcher) == 0:
        pytest.skip("아이템 템플릿 없음(fetch-items)")
    return [r for r in evaluate_dir(RAW, rec) if r.board]


def test_raw_board_labels_match_read(raw_board_results):
    """초안 라벨(사용자 확인 전) 대비 회귀. 라벨이 바뀌면 여기서 드러난다."""
    bad = []
    for r in raw_board_results:
        for side in ("board", "bench"):
            b = r.board.get(side)
            if b is None:          # 그쪽 라벨이 없다(라이브 3 2-3 준비 끝의 보드)
                continue
            if b["extra"] or b["got"] != b["expected"] or b["star"]["ok"] != b["star"]["total"]:
                bad.append((r.name, side, b))
    assert bad == [], bad


def test_raw_board_counts_match_watermark(raw_board_results):
    """보드 인원은 **독립 신호**(보드 가운데 "N/M" 워터마크)로도 맞아야 한다 — 라벨 순환 논증을 깬다."""
    import json

    checked = 0
    for r in raw_board_results:
        wm = json.loads((RAW / f"{r.name}.expected.json").read_text(encoding="utf-8")).get("_board_count_watermark")
        if not wm:
            continue
        want = int(wm.split("/")[0])
        assert r.board["board"]["got"] == want, (r.name, wm, r.board["board"]["got"])
        checked += 1
    assert checked >= 7
