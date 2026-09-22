"""QA 04 (vision): 경계면 회귀 — vision → GameState → advisor(mock), ROI content-box, 이름 매칭 안전성, 템플릿 ID.

- 실제 OCR이 필요한 테스트는 백엔드(rapidocr + onnxruntime/openvino)가 없으면 skip.
- Jev는 mock만. 라이브 호출 없음.
- `xfail(strict=True)`는 QA가 보고한 결함(_workspace/04_qa_vision.md)이다. 수정되면 XPASS → strict 실패로 알려 주므로
  담당자가 xfail 표시를 지운다.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("rapidfuzz")

from tft_advisor.contracts import (  # noqa: E402
    GAME_STATE_FIELDS, GameState, Recommendation, ScreenMode,
)
from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision.matching import NameMatcher  # noqa: E402
from tft_advisor.vision.ocr import RapidOcrEngine  # noqa: E402
from tft_advisor.vision.regions import SET18_16X9, FrameMapper  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCREENS = ROOT / "tests" / "fixtures" / "screens"
HAS_OCR = RapidOcrEngine.available_backend() is not None
needs_ocr = pytest.mark.skipif(not HAS_OCR, reason="OCR 백엔드 없음(rapidocr + onnxruntime/openvino)")


@pytest.fixture(scope="module")
def static():
    return load_static()


@pytest.fixture(scope="module")
def recognizer(static):
    from tft_advisor.vision.recognizer import Recognizer

    return Recognizer(static=static)


# ---------------------------------------------------------------- 기하(OCR 불필요)
@pytest.mark.parametrize("W,H,cb", [
    (1920, 1080, None), (2560, 1440, None), (3840, 2160, None),
    (1920, 1200, (0, 60, 1920, 1080)), (2560, 1440, (200, 150, 1600, 900)),
])
def test_rois_map_consistently_under_scale_and_offset(W, H, cb):
    base = FrameMapper(1920, 1080)
    m = FrameMapper(W, H, cb)
    left, top, w, h = m.box
    sx, sy = w / 1920, h / 1080
    for name, r in SET18_16X9.all_rois().items():
        b = base.to_px(r)
        exp = (left + b[0] * sx, top + b[1] * sy, left + b[2] * sx, top + b[3] * sy)
        got = m.to_px(r)
        assert all(abs(g - e) <= max(1.0, sx) for g, e in zip(got, exp)), (name, got, exp)
        assert 0 <= got[0] < got[2] <= W and 0 <= got[1] < got[3] <= H, name


def test_item_template_stems_are_static_item_ids(static):
    """템플릿 파일명(stem)이 곧 ItemRef.id가 된다 → 정적 데이터 apiName이어야 한다(한글 파일명이면 recognize가 ValidationError)."""
    base = ROOT / "data" / "templates" / str(static.set_number)
    stems = [p.stem for d in ("items", "items_screen") for p in (base / d).glob("*.png")]
    bad = [s for s in stems if static.get("items", s) is None]
    assert bad == []


# ---------------------------------------------------------------- 이름 매칭 안전성
def test_champion_names_exact_match_themselves(static):
    m = NameMatcher(static, ("champions", "shop_specials"), 85)
    for rec in static.tables["champions"]:
        if not rec.get("shop_pool") or not rec.get("name_ko"):
            continue
        got = m.match(rec["name_ko"])
        assert got is not None and got.record["name_ko"] == rec["name_ko"], rec["name_ko"]


# QA04-V2 수정됨(vision-engineer Fix round): 숫자·등급 토큰 정확 일치 + 모든 구간 margin → strict xfail 해제
def test_equidistant_shop_special_is_not_accepted(static):
    m = NameMatcher(static, ("champions", "shop_specials"), 85)
    # "N단계 집결" 5종은 숫자 한 글자만 다르다. OCR이 숫자를 잘못 읽은 "6단계 집결"은 모든 후보와 같은 거리 → 기권해야 한다.
    assert m.match("6단계 집결") is None


def test_augment_tier_numeral_ambiguity_is_not_confident(static):
    m = NameMatcher(static, ("augments",), 85)
    # "II"를 OCR이 "Il"로 읽은 경우(l→소문자 비교): I 과 III 사이에서 한쪽을 확신하면 안 된다.
    got = m.match("판도라의 아이템 Ill")
    assert got is None or got.margin >= 8


# ---------------------------------------------------------------- 실제 OCR: vision → advisor
@pytest.fixture(scope="module")
def e2e_results(recognizer):
    os.environ.pop("TYPESAFE_API_KEY", None)
    from tft_advisor.advisor.engine import create_advisor
    from tft_advisor.vision.recognizer import recognize_file

    adv = create_advisor("mock")
    out = []
    try:
        for png in sorted(SCREENS.glob("*.png")):
            adv.reset()
            st = recognize_file(png, recognizer)
            out.append((png.stem, st, adv.advise(st), adv.settings.vision.state_min_confidence))
    finally:
        adv.close()
    return out


@needs_ocr
def test_fixture_states_are_contract_valid_and_confidence_keyed(e2e_results):
    for name, st, _, _ in e2e_results:
        GameState.model_validate(st.model_dump())
        for f in GAME_STATE_FIELDS:
            if f == "screen_mode":
                continue
            v = getattr(st, f)
            assert (v is None) == (f not in st.confidence), (name, f)


@needs_ocr
def test_fixture_vision_to_mock_advisor_end_to_end(e2e_results):
    for name, st, rec, _ in e2e_results:
        assert st.screen_mode != ScreenMode.UNKNOWN, name
        assert rec is not None, name
        Recommendation.model_validate(rec.model_dump())
        assert rec.target_comps, name
        if st.screen_mode == ScreenMode.AUGMENT_SELECT and st.augment_offer:
            assert rec.augment is not None and rec.augment.pick in {a.id for a in st.augment_offer}, name


@needs_ocr
def test_reliable_fields_have_reliable_sub_items(e2e_results):
    """필드가 advisor 임계 이상이면 그 안의 증강/아이템도 임계 이상(advisor의 항목 필터가 인덱스를 밀지 않게).

    shop은 예외(QA04-V3 수정): 필드 신뢰도 = "상점 줄을 읽었는가"이고 칸별 차단은 advisor가 인덱스를 유지한 채 한다
    (`test_advisor_filters_weak_shop_slot_individually`)."""
    for name, st, _, mc in e2e_results:
        if st.augment_offer and st.is_reliable("augment_offer", mc):
            assert all(a.confidence >= mc for a in st.augment_offer), name
        if st.items and st.is_reliable("items", mc):
            refs = st.items.components + st.items.completed + st.items.emblems + st.items.others
            assert all(r.confidence >= mc for r in refs), name


@needs_ocr
@pytest.mark.parametrize("variant", ["1080p", "lb_16x10", "window"])
def test_content_box_variants_match_native(recognizer, variant):
    import cv2

    from tft_advisor.vision.capture import load_image

    img = load_image(SCREENS / "라운드 3-3.png")
    if variant == "1080p":
        frame, cb = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_AREA), None
    elif variant == "lb_16x10":
        frame = np.zeros((1200, 1920, 3), np.uint8)
        frame[60:1140] = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_AREA)
        cb = (0, 60, 1920, 1080)
    else:
        frame = np.full((1440, 2560, 3), 90, np.uint8)
        frame[150:1050, 200:1800] = cv2.resize(img, (1600, 900), interpolation=cv2.INTER_AREA)
        cb = (200, 150, 1600, 900)
    a, b = recognizer.recognize(img), recognizer.recognize(frame, content=cb)
    for f in ("screen_mode", "stage", "level", "xp", "gold", "shop_odds"):
        assert getattr(a, f) == getattr(b, f), f
    assert [(s.kind, s.id) for s in a.shop] == [(s.kind, s.id) for s in b.shop]


@needs_ocr
def test_window_without_content_box_fails_safe(recognizer):
    """창모드인데 content-box가 없으면 틀린 값이 아니라 None/UNKNOWN이어야 한다."""
    import cv2

    from tft_advisor.vision.capture import load_image

    img = load_image(SCREENS / "라운드 3-3.png")
    frame = np.full((1440, 2560, 3), 90, np.uint8)
    frame[150:1050, 200:1800] = cv2.resize(img, (1600, 900), interpolation=cv2.INTER_AREA)
    st = recognizer.recognize(frame)
    assert st.screen_mode in (ScreenMode.UNKNOWN, ScreenMode.LOADING) or st.shop is None
    for f in ("stage", "gold", "level", "xp", "shop"):
        v = getattr(st, f)
        assert v is None or not st.is_reliable(f, 0.6), f


def test_advisor_filters_weak_shop_slot_individually(static):
    """QA04-V3: shop 필드는 신뢰(≥임계)이고 한 칸만 약하면, advisor는 그 칸만 '인식 불확실'로 두고 나머지는 평가한다."""
    os.environ.pop("TYPESAFE_API_KEY", None)
    from tft_advisor.advisor.engine import create_advisor
    from tft_advisor.contracts import ShopSlot, ShopSlotKind

    champs = [r for r in static.tables["champions"] if r.get("shop_pool") and r.get("cost") == 1][:5]
    shop = [ShopSlot(kind=ShopSlotKind.CHAMPION, id=r["apiName"], name_ko=r.get("name_ko"), cost=1,
                     confidence=(0.45 if i == 2 else 0.95)) for i, r in enumerate(champs)]
    st = GameState(screen_mode=ScreenMode.PLANNING, stage="2-1", level=4, gold=10, shop=shop,
                   confidence={"screen_mode": 0.7, "stage": 0.95, "level": 0.95, "gold": 0.95, "shop": 0.95},
                   set_number=static.set_number)
    adv = create_advisor("mock")
    try:
        rec = adv.advise(st)
    finally:
        adv.close()
    by_slot = {a.slot: a for a in rec.shop}
    assert set(by_slot) == {0, 1, 2, 3, 4}
    assert by_slot[2].buy is False and by_slot[2].reason == "인식 불확실"
    assert all(by_slot[i].reason != "인식 불확실" for i in (0, 1, 3, 4))
