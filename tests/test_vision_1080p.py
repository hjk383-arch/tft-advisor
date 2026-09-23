"""vision 07 (2026-09-22): 1080p 원본 · 듀얼 모니터 캡처 · 화면 상태(전투/특수 선택/게임 종료) · 보유 증강 줄.

- 합성 이미지 + 가짜 OCR로 규칙을 고정한다(원본 캡처는 공개 저장소에 올릴 수 없다).
- `tests/fixtures/screens/raw/`(gitignore, 사용자 원본 캡처)가 있으면 실제 캡처 회귀도 돈다. 없으면 skip.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("rapidfuzz")

from tft_advisor.contracts import ScreenMode  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision.capture import load_image, pick_monitor, save_image  # noqa: E402
from tft_advisor.vision.icons import (  # noqa: E402
    AUG_BG, AugmentIconMatcher, augment_cell_template, find_icon_row,
)
from tft_advisor.vision.ocr import TextBox  # noqa: E402
from tft_advisor.vision.regions import (  # noqa: E402
    SET18_16X9, SET18_16X10, FrameMapper, Rect, find_screens, game_box_by_pixels, profile_for_frame,
    screen_candidates, stage_bar_score,
)
from tft_advisor.vision.screen_mode import (  # noqa: E402
    ModeSignals, classify, count_enemy_bars, parse_board_count,
)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "screens" / "raw"


@pytest.fixture(scope="module")
def static():
    return load_static()


class ColorKeyedOcr:
    """크롭 중앙 픽셀 색 → 문자열(합성 프레임용 가짜 OCR)."""

    name = "fake"

    def __init__(self, table: dict[tuple[int, int, int], str]) -> None:
        self.table = table

    def _lookup(self, image: np.ndarray) -> str | None:
        if image.size == 0:
            return None
        h, w = image.shape[:2]
        return self.table.get(tuple(int(v) for v in image[h // 2, w // 2]))

    def read(self, image):
        t = self._lookup(image)
        h, w = image.shape[:2]
        return [TextBox(t, 0.95, (0, 0, w, h))] if t else []

    def read_line(self, image):
        t = self._lookup(image)
        h, w = image.shape[:2]
        return TextBox(t, 0.95, (0, 0, w, h)) if t else None


def _paint(img: np.ndarray, box, rois: dict[str, str], table: dict, start: int = 0) -> None:
    m = FrameMapper.for_image(img, box)
    all_rois = SET18_16X9.all_rois()
    for i, (name, text) in enumerate(rois.items(), start):
        color = (10 + i, 200, 30 + 3 * i)
        x1, y1, x2, y2 = m.to_px(all_rois[name])
        img[y1:y2, x1:x2] = color
        table[color] = text


def _dual_monitor_frame(game: np.ndarray) -> np.ndarray:
    """사용자 캡처와 같은 배치: 왼쪽 2560x1440(밝은 창) + 오른쪽 1920x1080 게임, 오른쪽 아래는 순수 검정."""
    frame = np.zeros((1440, 4480, 3), np.uint8)
    frame[:, :2560] = 245
    frame[:1080, 2560:] = game
    return frame


# ---------------------------------------------------------------- 듀얼 모니터 캡처
def test_find_screens_splits_virtual_desktop():
    game = np.full((1080, 1920, 3), 90, np.uint8)
    game[50:60, 100:200] = 0          # 화면 안의 순수 검정 조각은 모니터 경계로 보지 않는다
    frame = _dual_monitor_frame(game)
    assert find_screens(frame) == [(0, 0, 2560, 1440), (2560, 0, 1920, 1080)]
    assert find_screens(game) == [(0, 0, 1920, 1080)]                      # 모니터 하나 = 빠른 길


def test_screen_candidates_split_equal_height_side_by_side():
    wide = np.full((1080, 3840, 3), 120, np.uint8)                         # 1920x1080 두 대, 경계 없음
    cands = screen_candidates(wide)
    assert cands[0] == (0, 0, 3840, 1080)
    assert (0, 0, 1920, 1080) in cands and (1920, 0, 1920, 1080) in cands


def test_stage_bar_score_prefers_game_over_bright_window():
    game = np.full((1080, 1920, 3), 90, np.uint8)
    x1, y1, x2, y2 = FrameMapper(1920, 1080).to_px(Rect(SET18_16X9.stage.x1, 0, SET18_16X9.round_icons.x2, 0.045))
    game[y1:y2, x1:x2] = 20                                                # 어두운 스테이지 막대
    frame = _dual_monitor_frame(game)
    left = stage_bar_score(frame, (0, 0, 2560, 1440), profile_for_frame(2560, 1440))
    right = stage_bar_score(frame, (2560, 0, 1920, 1080), profile_for_frame(1920, 1080))
    assert right > 0.5 > left
    assert game_box_by_pixels(frame) == (2560, 0, 1920, 1080)


def test_recognizer_picks_game_monitor_by_stage_text(static):
    from tft_advisor.vision.recognizer import Recognizer

    game = np.full((1080, 1920, 3), 90, np.uint8)
    table: dict = {}
    _paint(game, None, {"stage": "2-5", "xp_button": "경험치 구매", "refresh_button": "새로고침",
                        "gold": "25", "board_count": "4/4"}, table)
    frame = _dual_monitor_frame(game)
    rec = Recognizer(static=static, ocr=ColorKeyedOcr(table), item_template_dir=Path("/nonexistent"))
    assert rec.content_for(frame, None) == (2560, 0, 1920, 1080)
    s = rec.recognize(frame, groups={"hud"})
    assert s.stage == "2-5" and s.gold == 25 and s.screen_mode == ScreenMode.PLANNING
    assert s.confidence["screen_mode"] >= 0.9                             # 워터마크로 준비 확정
    # 스테이지가 없는 화면(게임 종료 등)도 같은 배치면 기억한 모니터를 쓴다
    over = np.full((1080, 1920, 3), 150, np.uint8)
    assert rec.content_for(_dual_monitor_frame(over), None) == (2560, 0, 1920, 1080)


def test_pick_monitor_takes_highest_score_and_survives_scorer_errors():
    frames = [np.zeros((10, 10, 3), np.uint8), np.ones((10, 10, 3), np.uint8), np.full((10, 10, 3), 2, np.uint8)]

    def scorer(img):
        v = int(img[0, 0, 0])
        if v == 2:
            raise RuntimeError("boom")
        return 0.2 + v

    assert pick_monitor(frames, scorer) == (1, pytest.approx(1.2))
    assert pick_monitor(frames[:1], lambda img: 0.0) == (0, 0.0)


def test_save_image_handles_korean_path(tmp_path):
    img = np.full((8, 8, 3), 77, np.uint8)
    p = tmp_path / "한글 이름.png"
    assert save_image(p, img) and p.is_file()
    assert (load_image(p) == img).all()


# ---------------------------------------------------------------- 화면 상태 규칙
def _sig(**kw) -> ModeSignals:
    base = dict(shop_hud=False, shop_hud_by_ocr=False, augment_title=False, augment_names_matched=0,
                stage=None, dark=False)
    base.update(kw)
    return ModeSignals(**base)


HUD = dict(shop_hud=True, shop_hud_by_ocr=True)


@pytest.mark.parametrize("kw,mode,conf", [
    (dict(HUD, stage="2-2", board_count=True), ScreenMode.PLANNING, 0.9),
    (dict(HUD, stage="2-7", prep_banner=True, enemy_bars=5), ScreenMode.PLANNING, 0.9),
    (dict(HUD, stage="2-2", enemy_bars=3), ScreenMode.COMBAT, 0.8),
    (dict(HUD, stage="2-2", enemy_bars=1), ScreenMode.PLANNING, 0.7),     # 1개는 상대 이름표일 수 있다
    (dict(HUD, stage="1-4", enemy_bars=4), ScreenMode.PLANNING, 0.7),     # PvE 준비 단계의 크립
    (dict(HUD, stage="3-7", enemy_bars=4), ScreenMode.PLANNING, 0.7),
    (dict(HUD, stage="2-2"), ScreenMode.PLANNING, 0.7),                   # 애매(준비 또는 전투)
    (dict(stage="5-1", select_title=True), ScreenMode.ITEM_SELECT, 0.8),  # 모루 / 특성 선택
    (dict(stage="2-7", select_title=True, augment_title=True), ScreenMode.ITEM_SELECT, 0.8),
    (dict(game_over_title=True, exit_button=True), ScreenMode.GAME_OVER, 0.95),
    (dict(exit_button=True), ScreenMode.GAME_OVER, 0.8),
    (dict(exit_button=True, stage="3-2"), ScreenMode.UNKNOWN, 0.0),        # 스테이지가 보이면 게임 종료가 아니다
    (dict(augment_title=True, augment_names_matched=3, stage="2-1"), ScreenMode.AUGMENT_SELECT, 0.95),
    (dict(stage="2-4"), ScreenMode.CAROUSEL, 0.65),
])
def test_classify_1080p_rules(kw, mode, conf):
    got, c = classify(_sig(**kw))
    assert got == mode and c == pytest.approx(conf)


@pytest.mark.parametrize("text,want", [
    ("3/3", (3, 3)), ("9 / 9", (9, 9)), ("/5", (None, 5)), ("0/5", (0, 5)), ("25", None), ("", None),
    ("3/30", None), ("하나 선택", None),
])
def test_parse_board_count(text, want):
    assert parse_board_count(text) == want


def _bar(img, x, y, bgr_top):
    """1080p 체력바: 짙은 남색 테두리 안 64x4 그라데이션."""
    img[y - 2:y + 6, x - 2:x + 66] = (35, 19, 3)
    for k in range(4):
        f = 1.0 - 0.25 * k
        img[y + k, x:x + 64] = tuple(int(c * f) for c in bgr_top)


def test_count_enemy_bars_counts_only_red_bars():
    img = np.full((1080, 1920, 3), (60, 90, 110), np.uint8)
    for i in range(3):
        _bar(img, 300 + 200 * i, 200, (17, 24, 200))        # 적(빨강)
    _bar(img, 300, 500, (16, 253, 16))                        # 아군(초록)
    _bar(img, 700, 500, (10, 120, 230))                       # 내 전략가(주황)
    img[700:740, 900:940] = (10, 10, 220)                     # 빨간 아이콘(정사각) — 막대가 아니다
    assert count_enemy_bars(img, 1080) == 3


# ---------------------------------------------------------------- 보유 증강 줄
def _glyph(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    g = np.zeros((128, 128, 4), np.uint8)
    for _ in range(6):
        x, y = rng.integers(10, 90, 2)
        cv2.rectangle(g, (int(x), int(y)), (int(x) + 30, int(y) + 12), (40, 200, 230, 255), -1)
    return g


def _row_frame(glyphs: list[np.ndarray]) -> np.ndarray:
    """보유 증강 줄을 그린 1080p 프레임(칸 38x37, 가운데 x≈510)."""
    img = np.full((1080, 1920, 3), (80, 110, 125), np.uint8)
    n = len(glyphs)
    x0 = 510 - 19 * n
    img[228:265, x0:x0 + 38 * n] = AUG_BG
    for i, g in enumerate(glyphs):
        a = g[..., 3:] / 255.0
        comp = (g[..., :3] * a + np.array(AUG_BG) * (1 - a)).astype(np.uint8)
        img[228:265, x0 + 38 * i:x0 + 38 * i + 38] = cv2.resize(comp, (38, 37), interpolation=cv2.INTER_AREA)
    return img


def test_find_icon_row_counts_cells():
    for n in (1, 3):
        img = _row_frame([_glyph(i) for i in range(n)])
        crop = FrameMapper.for_image(img).crop(img, SET18_16X9.augments_owned)
        cells = find_icon_row(crop, 1080)
        assert len(cells) == n
        assert all(abs((x2 - x1) - 38) <= 1 and abs((y2 - y1) - 37) <= 1 for x1, y1, x2, y2 in cells)
    empty = np.full((1080, 1920, 3), (80, 110, 125), np.uint8)
    assert find_icon_row(FrameMapper.for_image(empty).crop(empty, SET18_16X9.augments_owned), 1080) == []


def test_augment_matcher_identifies_glyphs_and_harvested_cells_self_match():
    glyphs = {f"A{i}": _glyph(i) for i in range(4)}
    mt = AugmentIconMatcher(glyphs.items())
    img = _row_frame([glyphs["A2"], glyphs["A0"]])
    crop = FrameMapper.for_image(img).crop(img, SET18_16X9.augments_owned)
    cells = find_icon_row(crop, 1080)
    got = [mt.match(crop[y1:y2, x1:x2]) for x1, y1, x2, y2 in cells]
    assert [g.api_name for g in got] == ["A2", "A0"] and all(g.score > 0.8 for g in got)
    x1, y1, x2, y2 = cells[0]
    cell = crop[y1:y2, x1:x2]
    self_mt = AugmentIconMatcher([("S", augment_cell_template(cell))])
    assert self_mt.match(cell).score > 0.99


def test_recognizer_reads_augments_owned_only_when_unambiguous(static, tmp_path):
    from tft_advisor.vision.recognizer import Recognizer

    recs = {r["apiName"]: r for r in static.tables["augments"]}
    unique, shared = "DA_ClutteredMind", "DA_18_BranchingOut"   # shared: "가지 뻗기"와 "가지 뻗기+"가 아이콘을 공유
    assert unique in recs and shared in recs
    tdir = tmp_path / "aug"
    tdir.mkdir()
    save_image(tdir / f"{unique}.png", _glyph(1))
    save_image(tdir / f"{shared}.png", _glyph(2))
    table: dict = {}
    base = np.full((1080, 1920, 3), (80, 110, 125), np.uint8)
    img = _row_frame([_glyph(1)])
    _paint(img, None, {"stage": "3-1", "xp_button": "경험치 구매", "board_count": "5/5"}, table)
    rec = Recognizer(static=static, ocr=ColorKeyedOcr(table), item_template_dir=Path("/nonexistent"),
                     augment_template_dir=tdir)
    s = rec.recognize(img, groups={"owned"})
    assert s.screen_mode == ScreenMode.PLANNING
    assert [a.id for a in s.augments_owned] == [unique] and s.confidence["augments_owned"] > 0.8
    img2 = _row_frame([_glyph(1), _glyph(2)])
    _paint(img2, None, {"stage": "3-1", "xp_button": "경험치 구매", "board_count": "5/5"}, {})
    assert rec.recognize(img2, groups={"owned"}).augments_owned is None      # 모호한 칸 하나 → 전체 None
    # 줄이 없으면: 스테이지 1은 [](확실히 없음), 그 밖은 None(가렸을 수 있다)
    for stage, want in (("1-3", []), ("3-1", None)):
        t2: dict = {}
        b = base.copy()
        _paint(b, None, {"stage": stage, "xp_button": "경험치 구매", "board_count": "2/2"}, t2)
        r2 = Recognizer(static=static, ocr=ColorKeyedOcr(t2), item_template_dir=Path("/nonexistent"),
                        augment_template_dir=tdir)
        assert r2.recognize(b, groups={"owned"}).augments_owned == want


# ---------------------------------------------------------------- 모드 분기 = READ_MODES (app 계약)
def test_read_modes_cover_groups_and_match_app_table():
    from tft_advisor.app.session import GROUP_READ_MODES
    from tft_advisor.vision.change import roi_groups
    from tft_advisor.vision.recognizer import GROUPS, READ_MODES, VISION_ONLY_GROUPS

    assert set(GROUPS) | {"stage"} == set(READ_MODES)
    # 모드 분기를 바꾸면 app 표도 같이 바꿔야 한다. 단 GameState 필드를 만들지 않는 묶음(VISION_ONLY_GROUPS)은
    # app의 **필드 병합** 표에 들어가지 않는다(app은 Recognizer.last_board_read로 받는다, vision 16).
    assert GROUP_READ_MODES == {k: v for k, v in READ_MODES.items() if k not in VISION_ONLY_GROUPS}
    assert set(GROUPS) | {"stage"} == set(roi_groups(SET18_16X9))


@pytest.mark.parametrize("mode", list(ScreenMode))
def test_recognize_reads_groups_exactly_in_read_modes(static, monkeypatch, mode):
    """`recognize()`가 READ_MODES 표대로만 묶음을 읽는다(분기와 표가 어긋나면 app 병합이 틀어진다)."""
    from tft_advisor.vision import recognizer as R

    table: dict = {}
    img = np.full((1080, 1920, 3), 60, np.uint8)
    _paint(img, None, {"stage": "3-2", "xp_button": "경험치 구매", "gold": "62", "shop_names[0]": "카르마"}, table)
    monkeypatch.setattr(R, "classify", lambda sig: (mode, 0.9))
    called: list[str] = []
    for name, group in (("_read_hud_numbers", "hud"), ("_read_shop", "shop"), ("_read_items", "items"),
                        ("_read_traits", "traits"), ("_read_hp", "players"), ("_read_augments_owned", "owned")):
        monkeypatch.setattr(R.Recognizer, name, lambda self, *a, _g=group, **k: called.append(_g))
    rec = R.Recognizer(static=static, ocr=ColorKeyedOcr(table), item_template_dir=Path("/nonexistent"))
    rec.recognize(img, groups=R.GROUPS)
    want = {g for g in ("hud", "shop", "items", "traits", "players", "owned") if mode in R.READ_MODES[g]}
    assert set(called) == want


# ---------------------------------------------------------------- 프로파일 (1080p 보정)
def test_1080p_profile_calibration_and_16x10_regression():
    p = SET18_16X9
    # 1080p 실측: 아이템 칸 바깥 테두리 x 10~55px, 0번 칸 y 261px, 간격 54px
    assert p.item_slots[0].x1 * 1920 == pytest.approx(10, abs=0.5)
    assert p.item_slots[0].y1 * 1080 == pytest.approx(261, abs=0.5)
    assert (p.item_slots[1].y1 - p.item_slots[0].y1) * 1080 == pytest.approx(54, abs=0.1)
    assert p.level.y1 == pytest.approx(0.809)                      # 방송 크롭 0.806 + 1080p 보정 0.003
    for name, r in p.all_rois().items():
        assert 0 <= r.x1 < r.x2 <= 1 and 0 <= r.y1 < r.y2 <= 1, name
    # 16:10(Mac 6장으로 검증된 값)은 수치가 그대로다: 하단 HUD 0.806 + 0.006, 아이템 칸 실측 override
    q = SET18_16X10
    assert q.level.y1 == pytest.approx(0.812) and q.shop_names[0].y1 == pytest.approx(0.963)
    assert (q.item_slots[0].x1, q.item_slots[0].y1) == (0.0055, 0.2428)
    assert q.traits_panel == Rect(0.036, 0.230, 0.128, 0.740)


# ---------------------------------------------------------------- 실제 캡처(있을 때만)
RAW_LABELS = sorted(RAW.glob("*.expected.json")) if RAW.is_dir() else []


@pytest.fixture(scope="module")
def raw_results(static):
    if not RAW_LABELS:
        pytest.skip("원본 캡처 없음(tests/fixtures/screens/raw, gitignore)")
    from tft_advisor.vision.ocr import RapidOcrEngine

    if RapidOcrEngine.available_backend() is None:
        pytest.skip("OCR 백엔드 없음")
    from tft_advisor.vision.evaluate import evaluate_dir
    from tft_advisor.vision.recognizer import Recognizer

    rec = Recognizer(static=static)
    if len(rec.item_matcher) == 0 or len(rec.augment_icons) == 0:
        pytest.skip("아이템/증강 템플릿 없음(fetch-items / fetch-augments)")
    return evaluate_dir(RAW, rec)


def test_raw_captures_have_no_confident_wrong_values(raw_results):
    wrong = [(r.name, f, d["expected"], d["got"]) for r in raw_results for f, d in r.fields.items()
             if d["status"] == "wrong"]
    assert wrong == []


def test_raw_captures_screen_mode_all_correct(raw_results):
    modes = {r.name: r.fields["screen_mode"]["status"] for r in raw_results if "screen_mode" in r.fields}
    assert modes and all(v == "ok" for v in modes.values()), modes


def test_raw_labels_are_valid_json_with_known_modes():
    for p in RAW_LABELS:
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d.get("screen_mode") in {m.value for m in ScreenMode}, p.name
