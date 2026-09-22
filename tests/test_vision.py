"""vision 모듈 테스트 — 스크린샷/합성 이미지 기반(실시간 캡처 없음).

- 순수 로직(파싱, 자모 퍼지 매칭, ROI 사상, 화면 상태 규칙, 조립)은 가짜 OCR로 검사한다.
- fixture 회귀(`test_fixture_*`)는 실제 OCR 백엔드(rapidocr + onnxruntime/openvino)가 있을 때만 돈다.
  fixture는 방송 크롭이고 ROI를 같은 fixture에서 측정했으므로, 이 수치는 "정확도"가 아니라 회귀 방지선이다.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("rapidfuzz")

from tft_advisor.contracts import GameState, ScreenMode, ShopSlotKind  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision import parse  # noqa: E402
from tft_advisor.vision.capture import ArraySource, FileSource, FrameSource, load_image  # noqa: E402
from tft_advisor.vision.icons import IconMatcher, slot_is_empty  # noqa: E402
from tft_advisor.vision.matching import NameMatcher, to_jamo  # noqa: E402
from tft_advisor.vision.ocr import DigitTemplateReader, NullOcr, TextBox  # noqa: E402
from tft_advisor.vision.regions import PROFILES, SET18_16X9, FrameMapper, Rect, get_profile  # noqa: E402
from tft_advisor.vision.screen_mode import ModeSignals, classify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCREENS = ROOT / "tests" / "fixtures" / "screens"
VISION_SRC = ROOT / "src" / "tft_advisor" / "vision"


# ---------------------------------------------------------------- 안전: 화면 픽셀만
def test_vision_code_has_no_memory_or_input_access():
    forbidden = re.compile(
        r"ReadProcessMemory|OpenProcess|WriteProcessMemory|VirtualQueryEx|pymem|frida|"
        r"pyautogui|pynput|keyboard\.|mouse\.|SendInput|keybd_event|mouse_event|SetCursorPos|"
        r"ctypes\.windll|win32api|win32process|psutil"
    )
    hits = [f"{p.name}: {m.group(0)}" for p in VISION_SRC.glob("*.py") for m in forbidden.finditer(p.read_text("utf-8"))]
    assert hits == []


# ---------------------------------------------------------------- 파싱
@pytest.mark.parametrize("text,expected", [
    ("2-5", "2-5"), ("&2-1", "2-1"), ("i2-4 0", "2-4"), ("3 - 3", "3-3"), ("1—4", "1-4"), ("28", None), ("", None),
])
def test_parse_stage(text, expected):
    assert parse.parse_stage(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("4레벨", 4), ("10레벨", 10), ("Lv. 6", 6), ("Level 3", 3), ("레벨", None), ("0레벨", None), ("레벨 12", None),
])
def test_parse_level(text, expected):
    assert parse.parse_level(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("2/10", (2, 10)), ("4/36", (4, 36)), ("0/6", (0, 6)),
    ("216", (2, 6)), ("2110", (2, 10)), ("016", (0, 6)),   # "/" → "1" 오인식 복원(유일할 때만)
    ("7/6", None), ("2/11", None), ("abc", None),
])
def test_parse_xp(text, expected):
    assert parse.parse_xp(text) == expected


def test_level_from_xp_uses_observed_table():
    assert parse.level_from_xp((2, 36)) == 6
    assert parse.level_from_xp((0, 6)) == 3
    assert parse.level_from_xp(None) is None


@pytest.mark.parametrize("text,expected", [
    ("31", 31), ("①31", 31), ("1,000", None), ("62 ", 62), ("3 1", None), ("O", 0), ("", None),
])
def test_parse_int(text, expected):
    assert parse.parse_int(text, 0, 999) == expected


def test_parse_odds():
    assert parse.parse_odds("55% 30% 15% 0% 0%") == [55, 30, 15, 0, 0]
    assert parse.parse_odds("30% 40% 25% 5% 0%") == [30, 40, 25, 5, 0]
    assert parse.parse_odds("55% 30% 15% 0%") is None          # 4개
    assert parse.parse_odds("55% 30% 15% 9% 0%") is None       # 합 ≠ 100


# ---------------------------------------------------------------- 자모 퍼지 매칭
def test_to_jamo_splits_compound_vowels():
    assert to_jamo("워") == "ㅇㅜㅓ"
    assert to_jamo("B.F. 대검") == "bfㄷㅐㄱㅓㅁ"


@pytest.fixture(scope="module")
def static():
    return load_static()


def test_name_matcher_shop(static):
    m = NameMatcher(static, ("champions", "shop_specials"), 85)
    assert m.match("조약돌").api_name == "DA_18_Sentry"
    assert m.match("바위게").api_name == "DA_Scuttlecrab18"            # 공백 없는 OCR
    assert m.match("3단계와 함께").kind == "shop_specials"
    assert m.match("120") is None                                      # 숫자 잡음은 거부
    assert m.match("") is None


def test_name_matcher_augment_prefers_da_ids(static):
    m = NameMatcher(static, ("augments",), 85)
    hit = m.match("고위천사의지광이")                                     # 한 글자 OCR 오류
    assert hit is not None and hit.api_name == "DA_SeraphimsStaff"
    assert m.match("수완가").api_name == "DA_Hustler"


# ---------------------------------------------------------------- ROI
def test_profile_rois_are_normalised_and_complete():
    p = get_profile("1920x1080")
    assert p is SET18_16X9 and PROFILES["set18_16x9"] is p
    assert len(p.shop_cards) == len(p.shop_names) == len(p.shop_costs) == 5
    assert len(p.augment_names) == 3 and len(p.item_slots) == 10
    for name, r in p.all_rois().items():
        assert 0 <= r.x1 < r.x2 <= 1 and 0 <= r.y1 < r.y2 <= 1, name
    xs = [r.x1 for r in p.shop_cards]
    assert xs == sorted(xs) and all(a.x2 <= b.x1 for a, b in zip(p.shop_cards, p.shop_cards[1:]))


def test_rect_rejects_bad_coords():
    with pytest.raises(ValueError):
        Rect(0.5, 0.1, 0.4, 0.2)


def test_frame_mapper_scales_and_offsets():
    r = Rect(0.25, 0.5, 0.5, 1.0)
    assert FrameMapper(1920, 1080).to_px(r) == (480, 540, 960, 1080)
    assert FrameMapper(3840, 2160).to_px(r) == (960, 1080, 1920, 2160)
    # 게임 화면이 프레임 안 (100, 50)에서 1600x900 로 있는 경우(레터박스·방송 크롭 보정)
    m = FrameMapper(1920, 1080, content=(100, 50, 1600, 900))
    assert m.to_px(r) == (500, 500, 900, 950)
    assert m.to_rel(500, 500) == pytest.approx((0.25, 0.5))


# ---------------------------------------------------------------- 프레임 소스
def test_file_source_reads_korean_path(tmp_path):
    img = np.full((20, 30, 3), 7, np.uint8)
    p = tmp_path / "라운드 테스트.png"
    ok, buf = cv2.imencode(".png", img)
    assert ok
    buf.tofile(str(p))
    src = FileSource(p)
    assert isinstance(src, FrameSource)
    f = src.grab()
    assert f is not None and f.size == (30, 20) and f.source == str(p)
    assert src.grab() is None
    assert load_image(p).shape == (20, 30, 3)


def test_array_source():
    src = ArraySource([np.zeros((4, 4, 3), np.uint8)] * 2)
    assert src.grab() is not None and src.grab() is not None and src.grab() is None


# ---------------------------------------------------------------- 아이콘·글리프
def test_slot_is_empty():
    assert slot_is_empty(np.zeros((40, 40, 3), np.uint8))
    rng = np.random.default_rng(0)
    assert not slot_is_empty(rng.integers(0, 255, (40, 40, 3), dtype=np.uint8))


def _icon(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (6, 6, 3), dtype=np.uint8)
    return cv2.resize(small, (64, 64), interpolation=cv2.INTER_NEAREST)


def test_icon_matcher_picks_right_template_with_margin():
    templates = {f"DA_T{i}": cv2.resize(_icon(i), (32, 32)) for i in range(5)}
    m = IconMatcher(templates)
    # 실제 칸처럼 아이콘 둘레에 테두리가 있고(아이콘≈칸의 84%) 크기가 다른 크롭
    crop = cv2.resize(cv2.copyMakeBorder(_icon(3), 6, 6, 6, 6, cv2.BORDER_CONSTANT, value=(20, 20, 20)), (41, 43))
    hit = m.match(crop)
    assert hit.api_name == "DA_T3" and hit.score > 0.8 and hit.margin > 0.2
    grouped = IconMatcher({"A1": templates["DA_T3"], "A2": templates["DA_T3"], "B": templates["DA_T1"]},
                          group=lambda a: a[0])
    assert grouped.match(crop).margin > 0.2          # 같은 묶음(A1/A2)끼리는 margin 계산에서 제외


def _digits_image(text: str) -> np.ndarray:
    img = np.zeros((40, 26 * len(text) + 10, 3), np.uint8)
    cv2.putText(img, text, (4, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    return img


def test_digit_template_reader_harvest_and_read(tmp_path):
    assert DigitTemplateReader.harvest(_digits_image("0123456789"), "0123456789", tmp_path)
    reader = DigitTemplateReader.from_dir(tmp_path)
    assert reader.ready
    tb = reader.read_line(_digits_image("3170"))
    assert tb is not None and tb.text == "3170" and tb.score > 0.8
    assert not DigitTemplateReader.harvest(_digits_image("12"), "123", tmp_path / "x")   # 글자 수 불일치 → 저장 안 함


# ---------------------------------------------------------------- 화면 상태 규칙
def _sig(**kw) -> ModeSignals:
    base = dict(shop_hud=False, shop_hud_by_ocr=False, augment_title=False, augment_names_matched=0,
                stage=None, dark=False)
    base.update(kw)
    return ModeSignals(**base)


@pytest.mark.parametrize("kw,mode", [
    (dict(augment_title=True, augment_names_matched=3), ScreenMode.AUGMENT_SELECT),
    (dict(augment_names_matched=2), ScreenMode.AUGMENT_SELECT),
    (dict(shop_hud=True, shop_hud_by_ocr=True, stage="3-2"), ScreenMode.PLANNING),
    (dict(stage="2-4"), ScreenMode.CAROUSEL),
    (dict(stage="1-1"), ScreenMode.CAROUSEL),
    (dict(dark=True), ScreenMode.LOADING),
    (dict(stage="3-2"), ScreenMode.UNKNOWN),
    (dict(), ScreenMode.UNKNOWN),
])
def test_classify(kw, mode):
    got, conf = classify(_sig(**kw))
    assert got == mode
    assert (conf == 0.0) == (mode == ScreenMode.UNKNOWN)


# ---------------------------------------------------------------- 조립(가짜 OCR)
class ColorKeyedOcr:
    """ROI마다 고유 색으로 칠한 합성 프레임용 가짜 OCR: 크롭 중앙 픽셀 색 → 정해진 문자열."""

    name = "fake"

    def __init__(self, table: dict[tuple[int, int, int], str]) -> None:
        self.table = table

    def _lookup(self, image: np.ndarray) -> str | None:
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


def _synthetic_planning_frame(texts: dict[str, str], w: int = 1920, h: int = 1080):
    img = np.full((h, w, 3), 60, np.uint8)
    m = FrameMapper(w, h)
    rois = SET18_16X9.all_rois()
    table = {}
    for i, (name, text) in enumerate(texts.items()):
        color = (10 + i, 200, 30 + 3 * i)
        x1, y1, x2, y2 = m.to_px(rois[name])
        img[y1:y2, x1:x2] = color
        table[color] = text
    return img, ColorKeyedOcr(table)


PLANNING_TEXTS = {
    "shop_cards[2]": "",   # 이름 없는 카드 → 칠만 하고 텍스트 없음(아래에서 어둡게)
    "stage": "3-2", "level": "6레벨", "xp": "4/36", "gold": "①62", "shop_odds": "30% 40% 25% 5% 0%",
    "streak_value": "0", "xp_button": "경험치 구매", "refresh_button": "새로고침",
    "shop_names[0]": "카르마", "shop_names[1]": "니달리", "shop_names[3]": "라칸", "shop_names[4]": "3단계와 함께",
    "shop_costs[4]": "9",
}


def test_recognizer_assembles_planning_state(static):
    from tft_advisor.vision.recognizer import Recognizer

    img, ocr = _synthetic_planning_frame({k: v for k, v in PLANNING_TEXTS.items() if v})
    x1, y1, x2, y2 = FrameMapper(1920, 1080).to_px(SET18_16X9.shop_cards[2])
    img[y1:y2, x1:x2] = 20                                           # 구매 완료된 빈 칸
    rec = Recognizer(static=static, ocr=ocr, item_template_dir=Path("/nonexistent"))
    s = rec.recognize(img, source_image="synthetic")
    assert s.screen_mode == ScreenMode.PLANNING
    assert (s.stage, s.level, s.xp, s.gold, s.streak) == ("3-2", 6, (4, 36), 62, 0)
    assert s.shop_odds == [30, 40, 25, 5, 0]
    kinds = [sl.kind for sl in s.shop]
    assert kinds == [ShopSlotKind.CHAMPION, ShopSlotKind.CHAMPION, ShopSlotKind.EMPTY,
                     ShopSlotKind.CHAMPION, ShopSlotKind.SPECIAL]
    assert s.shop[4].id == "DA_ThreeMe18" and s.shop[4].cost == 9
    assert s.shop[1].cost == static.get("champions", s.shop[1].id)["cost"]
    assert s.confidence["level"] == s.confidence["xp"]              # 레벨↔XP 필요량 상호 확인
    assert s.items is None and s.hp is None and s.augment_offer is None and s.board is None
    assert set(s.confidence) <= set(s.model_dump(exclude_none=True))  # None 필드에는 신뢰도 키가 없다
    GameState.model_validate(s.model_dump())


def test_recognizer_level_inferred_from_xp_when_hidden(static):
    from tft_advisor.vision.recognizer import LEVEL_FROM_XP_FACTOR, Recognizer

    texts = {k: v for k, v in PLANNING_TEXTS.items() if v and k != "level"}
    img, ocr = _synthetic_planning_frame(texts)
    s = Recognizer(static=static, ocr=ocr, item_template_dir=Path("/nonexistent")).recognize(img)
    assert s.level == 6
    assert s.confidence["level"] == pytest.approx(s.confidence["xp"] * LEVEL_FROM_XP_FACTOR, abs=1e-3)


def test_recognizer_without_ocr_returns_unknowns(static):
    from tft_advisor.vision.recognizer import Recognizer

    rec = Recognizer(static=static, ocr=NullOcr(), item_template_dir=Path("/nonexistent"))
    s = rec.recognize(np.full((1080, 1920, 3), 90, np.uint8))
    assert s.screen_mode == ScreenMode.UNKNOWN
    assert s.stage is None and s.shop is None and s.gold is None
    assert s.confidence == {} and s.frame_size == (1920, 1080)
    dark = rec.recognize(np.zeros((1080, 1920, 3), np.uint8))
    assert dark.screen_mode == ScreenMode.LOADING


# ---------------------------------------------------------------- fixture 회귀 (실제 OCR)
@pytest.fixture(scope="module")
def fixture_results(static):
    from tft_advisor.vision.ocr import RapidOcrEngine

    if RapidOcrEngine.available_backend() is None:
        pytest.skip("OCR 백엔드 없음(rapidocr + onnxruntime/openvino)")
    from tft_advisor.vision.evaluate import evaluate_dir, summarize
    from tft_advisor.vision.recognizer import Recognizer

    try:
        rec = Recognizer(static=static)
    except Exception as e:  # noqa: BLE001 — 모델 첫 다운로드 실패(오프라인) 등
        pytest.skip(f"OCR 초기화 실패: {e}")
    results = evaluate_dir(SCREENS, rec)
    return results, summarize(results)


# 2026-09-22 기준선(PROVISIONAL, 방송 크롭 7장). 낮아지면 회귀. 원본 캡처가 들어오면 목표치(layout.md 3절)로 교체.
BASELINE = {"screen_mode": 7, "stage": 7, "level": 3, "xp": 5, "gold": 5, "shop_odds": 5, "streak": 5,
            "hp": 6, "augment_offer": 1, "shop_slot": 23}


def test_fixture_regression_baseline(fixture_results):
    _, agg = fixture_results
    low = {f: (agg.get(f, {}).get("ok", 0), n) for f, n in BASELINE.items() if agg.get(f, {}).get("ok", 0) < n}
    assert low == {}, f"기준선 미달(ok, 기준): {low}"


def test_fixture_no_confident_wrong_values(fixture_results):
    """틀린 값은 신뢰도가 state 임계(0.6) 미만이어야 한다 — '확신에 찬 오답' 금지."""
    results, _ = fixture_results
    bad = [(r.name, f, d["got"], d["conf"]) for r in results for f, d in r.fields.items()
           if d["status"] == "wrong" and not d["estimated_label"] and (d["conf"] or 0) >= 0.6 and f != "shop"]
    wrong_slots = [(r.name, s) for r in results for s in r.shop_slots
                   if not s["ok"] and s["got"] and s["got"][0] not in ("unknown",)]
    assert bad == [] and wrong_slots == []


# ================================================================ Fix round (QA04 V1~V7)
from tft_advisor.vision.change import ChangeDetector, changed_groups, roi_signatures  # noqa: E402
from tft_advisor.vision.item_ids import ItemCatalog  # noqa: E402
from tft_advisor.vision.matching import split_numerals, strip_markup  # noqa: E402


@pytest.mark.parametrize("text,base,sig", [
    ("판도라의 아이템 III", "판도라의 아이템 ", (3,)),
    ("영원한 브론즈 Il", "영원한 브론즈 ", (2,)),       # OCR: II → Il
    ("판도라의 아이템1I", "판도라의 아이템", (2,)),       # 공백 없음 + 1↔I
    ("5단계 집결", " 단계 집결", (5,)),
    ("Spell", "Spell", ()),                               # 영어 단어 끝의 l은 등급이 아니다
])
def test_split_numerals_query(text, base, sig):
    assert split_numerals(text, query=True) == (base, sig)


def test_name_matcher_numeral_tokens_must_match_exactly(static):
    shop = NameMatcher(static, ("champions", "shop_specials"), 85)
    assert shop.match("5단계 집결").api_name == "DA_18_AllFives"
    assert shop.match("6단계 집결") is None                          # 5개 후보 동점 → 기권
    assert shop.match("단계 집결") is None                           # 숫자 누락 → 기권
    aug = NameMatcher(static, ("augments",), 85)
    assert aug.match("영원한 브론즈 Il").api_name == "DA_BronzeForLifeII"
    assert aug.match("판도라의 아이템 III").api_name == "DA_PandorasItemsIII"
    assert aug.match("판도라의 아이템") is None                        # 등급 누락: 레거시 무등급 ID로 가지 않는다
    assert aug.match("판도라의 아이템 IIII") is None                   # 해석 불가 등급 → 기권


def test_name_matcher_high_score_still_needs_margin(static):
    m = NameMatcher(static, ("champions", "shop_specials"), 85)
    hit = m.best("엘리스")
    assert hit is not None and m.accepts(hit)
    fake = type(hit)(record=hit.record, kind=hit.kind, score=97.0, margin=2.0, query="x")
    assert not m.accepts(fake)                                        # ≥85여도 margin 미달이면 거부


def test_strip_markup():
    assert strip_markup("자석 제거기 <rules>(7회 사용 가능!)</rules>") == "자석 제거기"
    assert strip_markup("B.F. 대검") == "B.F. 대검"


def test_item_catalog_representative_and_names(static):
    cat = ItemCatalog(static)
    rep = cat.rep("TFT_Consumable_ItemRemover_UsesLeft7")
    assert rep == cat.rep("TFT_Consumable_ItemRemover_UsesLeft4") == cat.resolve("자석 제거기")
    assert "UsesLeft" not in rep and "<" not in (cat.display_name(rep) or "")
    assert cat.resolve("B.F. 대검") == cat.resolve("b.f.대검") == "DA_Component_BFSword"
    assert cat.resolve("DA_Component_BFSword") == "DA_Component_BFSword"
    assert cat.resolve("없는 아이템") is None and cat.resolve(None) is None
    # 찬란한 아이템은 아이콘을 공유해도 다른 아이템
    assert cat.rep("DA_SpiritVisage_Radiant") != cat.rep("DA_SpiritVisage")


def _frame_with_items(icons: list[np.ndarray | None]):
    img, ocr = _synthetic_planning_frame({k: v for k, v in PLANNING_TEXTS.items() if v})
    m = FrameMapper(1920, 1080)
    icons = list(icons) + [None] * (len(SET18_16X9.item_slots) - len(icons))   # 나머지 칸은 빈 칸(검정)
    for r, icon in zip(SET18_16X9.item_slots, icons):
        x1, y1, x2, y2 = m.to_px(r)
        if icon is None:
            img[y1:y2, x1:x2] = 5
        else:   # 실제 칸처럼 어두운 테두리 안에 아이콘(아이콘 ≈ 칸의 84%)
            framed = cv2.copyMakeBorder(icon, 6, 6, 6, 6, cv2.BORDER_CONSTANT, value=(20, 20, 20))
            img[y1:y2, x1:x2] = cv2.resize(framed, (x2 - x1, y2 - y1))
    return img, ocr


def test_harvest_items_maps_korean_labels_to_ids_and_supplements_cdragon(static, tmp_path, caplog):
    """QA04-V1 경로: 한국어 라벨 → 정적 ID로 저장, 모르는 이름은 오류. 실화면 템플릿 몇 개가 CDragon 아이콘 전체를 가리지 않는다."""
    from tft_advisor.vision.recognizer import Recognizer
    from tft_advisor.vision.templates import harvest_items

    ids = ["DA_Component_BFSword", "DA_Component_RecurveBow", "DA_Component_ChainVest"]
    cdragon = tmp_path / "items"
    cdragon.mkdir()
    for i, api in enumerate(ids):                                     # "CDragon" 원본 아이콘 3개
        cv2.imwrite(str(cdragon / f"{api}.png"), cv2.resize(_icon(10 + i), (32, 32)))
    img, ocr = _frame_with_items([_icon(10), _icon(11), None, _icon(12)])
    screen = tmp_path / "items_screen"
    saved, errors = harvest_items(img, ["B.F. 대검", "없는 아이템", None, None], static, SET18_16X9, screen)
    assert saved == ["DA_Component_BFSword"]
    assert len(errors) == 1 and "없는 아이템" in errors[0]
    assert sorted(p.stem for p in screen.glob("*.png")) == ["DA_Component_BFSword"]
    cv2.imwrite(str(screen / "B.F. 대검.png"), cv2.resize(_icon(10), (32, 32)))   # 옛 방식 한글 파일명(무시돼야 함)

    rec = Recognizer(static=static, ocr=ocr, item_template_dir=[cdragon, screen])
    assert rec.item_matcher.ids == set(ids)                          # 합집합, 한글 stem 제외
    assert "B.F. 대검" in caplog.text
    s = rec.recognize(img)                                            # ValidationError 없이
    got = [r.id for r in s.items.components]
    assert sorted(got) == sorted(ids)                                 # 실화면 템플릿이 없는 두 아이템도 인식


def test_item_output_uses_group_representative(static, tmp_path):
    from tft_advisor.vision.recognizer import Recognizer

    d = tmp_path / "items"
    d.mkdir()
    cv2.imwrite(str(d / "TFT_Consumable_ItemRemover_UsesLeft7.png"), cv2.resize(_icon(30), (32, 32)))
    cv2.imwrite(str(d / "DA_Component_BFSword.png"), cv2.resize(_icon(31), (32, 32)))
    img, ocr = _frame_with_items([_icon(30)])
    s = Recognizer(static=static, ocr=ocr, item_template_dir=d).recognize(img)
    ref = (s.items.others + s.items.components + s.items.completed + s.items.emblems)[0]
    assert "UsesLeft" not in ref.id and "<" not in (ref.name_ko or "")


class _ScoredOcr(ColorKeyedOcr):
    """특정 문자열만 낮은 점수로 읽는 가짜 OCR."""

    def __init__(self, table, low: dict[str, float]):
        super().__init__(table)
        self.low = low

    def read_line(self, image):
        tb = super().read_line(image)
        return TextBox(tb.text, self.low.get(tb.text, 0.95), tb.box) if tb else None


def test_shop_field_confidence_is_not_dragged_down_by_one_weak_slot(static):
    """QA04-V3: 약한 칸 하나(0.5)가 shop 필드 전체를 임계 아래로 끌어내리지 않는다. 칸 신뢰도는 그대로 남는다."""
    from tft_advisor.vision.recognizer import Recognizer

    img, ocr = _synthetic_planning_frame({k: v for k, v in PLANNING_TEXTS.items() if v})
    ocr = _ScoredOcr(ocr.table, {"니달리": 0.5})
    s = Recognizer(static=static, ocr=ocr, item_template_dir=Path("/nonexistent")).recognize(img)
    assert s.is_reliable("shop", 0.6)
    assert s.shop[1].confidence == pytest.approx(0.5) and s.shop[0].confidence >= 0.9


def test_streak_sign_factor_is_off_the_threshold():
    """QA04-V4: 부호 추정 streak 신뢰도가 advisor 임계(0.6)에 걸려 반올림으로 포함 여부가 갈리지 않게."""
    from tft_advisor.config import VisionCfg
    from tft_advisor.vision.recognizer import STREAK_SIGN_FACTOR

    thr = VisionCfg().state_min_confidence
    assert abs(STREAK_SIGN_FACTOR * 1.0 - thr) >= 0.05 and abs(STREAK_SIGN_FACTOR * 0.9 - thr) >= 0.05


class _CountingOcr(ColorKeyedOcr):
    def __init__(self, table):
        super().__init__(table)
        self.n_read = 0
        self.n_line = 0

    def read(self, image):
        self.n_read += 1
        return super().read(image)

    def read_line(self, image):
        self.n_line += 1
        return super().read_line(image)


def test_single_line_fields_use_fast_line_recognition_first(static):
    """QA04-V7: 한 줄 ROI는 한 줄 인식(rec만)으로 끝나고 검출+인식은 여러 줄 ROI(플레이어 목록·특성 패널)에만."""
    from tft_advisor.vision.recognizer import Recognizer

    img, base = _synthetic_planning_frame({k: v for k, v in PLANNING_TEXTS.items() if v})
    ocr = _CountingOcr(base.table)
    s = Recognizer(static=static, ocr=ocr, item_template_dir=Path("/nonexistent")).recognize(img)
    assert (s.stage, s.level, s.gold) == ("3-2", 6, 62)
    assert ocr.n_read <= 2, ocr.n_read


def test_recognize_groups_limits_fields(static):
    from tft_advisor.vision.recognizer import FIELD_GROUP, GROUPS, Recognizer

    img, ocr = _synthetic_planning_frame({k: v for k, v in PLANNING_TEXTS.items() if v})
    rec = Recognizer(static=static, ocr=ocr, item_template_dir=Path("/nonexistent"))
    s = rec.recognize(img, groups={"hud"})
    assert s.stage == "3-2" and s.gold == 62 and s.shop is None
    assert s.screen_mode == ScreenMode.PLANNING
    assert set(FIELD_GROUP.values()) - {"stage"} == set(GROUPS)
    with pytest.raises(ValueError):
        rec.recognize(img, groups={"nope"})


def test_changed_groups_and_stability():
    rng = np.random.default_rng(1)
    base = rng.integers(40, 200, (1080, 1920, 3), dtype=np.uint8)
    noisy = np.clip(base.astype(np.int16) + rng.integers(-5, 6, base.shape), 0, 255).astype(np.uint8)
    s0 = roi_signatures(base, SET18_16X9)
    assert changed_groups(s0, roi_signatures(noisy, SET18_16X9)) == set()      # 잡음은 변화 아님
    edited = base.copy()
    x1, y1, x2, y2 = FrameMapper(1920, 1080).to_px(SET18_16X9.gold)
    cv2.putText(edited, "88", (x1 + 40, y2 - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    assert changed_groups(s0, roi_signatures(edited, SET18_16X9)) == {"hud"}
    assert changed_groups(None, s0) == set(s0)

    det = ChangeDetector(SET18_16X9, stable_frames=2)
    assert det.update(base) == set()                   # 첫 프레임: 아직 안정 아님
    assert det.update(noisy) == set(s0)                # 2프레임 같음 → 전부 보고
    assert det.update(base) == set()                   # 변화 없음
    assert det.update(edited) == set()                 # 바뀌었지만 1프레임
    assert det.update(edited) == {"hud"}               # 2프레임 안정 → hud만


def test_default_groups_skip_traits_panel(static):
    from tft_advisor.vision.recognizer import DEFAULT_GROUPS, GROUPS

    assert set(DEFAULT_GROUPS) == set(GROUPS) - {"traits"}


def test_xp_table_comes_from_static_meta(static):
    """XP 필요량 표는 정적 데이터 meta.json `xp_to_next`(stats-researcher)에서 읽는다. 하드코딩 대체값과 같아야 한다."""
    from tft_advisor.vision.recognizer import Recognizer

    table = static.xp_to_next()
    assert table, "meta.json xp_to_next 없음"
    rec = Recognizer(static=static, ocr=NullOcr(), item_template_dir=Path("/nonexistent"))
    assert rec.xp_table == table
    assert parse.XP_TO_NEXT == table                                   # 대체값도 같은 출처 값으로 맞춰 둔다
    assert parse.parse_xp("10/56", table) == (10, 56)
    assert parse.parse_xp("10/48", table) is None                      # 옛 추정값(48)은 표에 없다
    assert parse.level_from_xp((3, 56), table) == 7
    assert parse.level_from_xp((3, 68), table) is None                 # 8·9레벨 필요량이 같아 추정 불가
    assert parse.level_from_xp((1, 2), table) is None                  # 1·2레벨도 같다
