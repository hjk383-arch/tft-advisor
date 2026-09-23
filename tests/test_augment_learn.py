"""보유 증강: 대체 출처 아이콘(글리프 정규화 경로), 그림 동일성, 선택 순간 자동 학습(09 vision).

합성 글리프만 쓴다(Riot 아트·원본 캡처 없이 돈다). 실캡처 회귀는 tests/test_vision_1080p.py::test_raw_* 쪽.
"""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from tft_advisor.app.session import SessionTracker
from tft_advisor.contracts import AugmentRef, GameState, ScreenMode
from tft_advisor.static_data import load_static
from tft_advisor.vision.augment_learn import (
    LEARN_MIN, AugmentLearner, OwnedRow, augment_visual_keys, load_alt_manifest,
)
from tft_advisor.vision.icons import AUG_BG, AugmentIconMatcher, augment_cell_template, glyph_normalize

CM, ASC, PARTIAL = "DA_ClutteredMind", "DA_Ascension", "DA_PartialAscension"
PRIMAL, PRIMAL_S, PRIMAL_PLUS = "DA_18_PrimalAugment_Nidalee", "DA_18_PrimalAugment_Sivir", "DA_18_PrimalAugmentPlus_Nidalee"
VERT = "DA_VerticalityII"


@pytest.fixture(scope="module")
def static():
    return load_static()


def _glyph(seed: int, size: int = 128, fill: float = 0.92, alpha: bool = True) -> np.ndarray:
    """무작위 막대 글리프(금색). `fill`: 글리프가 차지하는 비율(대체 출처 아이콘은 약 0.81)."""
    rng = np.random.default_rng(seed)
    inner = np.zeros((100, 100, 4), np.uint8)
    for _ in range(7):
        x, y = rng.integers(0, 75, 2)
        w, h = rng.integers(12, 25, 2)
        cv2.rectangle(inner, (int(x), int(y)), (int(x + w), int(y + h)), (40, 200, 230, 255), -1)
    cv2.rectangle(inner, (0, 0), (99, 99), (40, 200, 230, 255), 6)   # 외곽(글리프 외곽 = 틀)
    inner = cv2.GaussianBlur(inner, (0, 0), 1.5)                       # 실제 아이콘처럼 가장자리를 부드럽게
    g = np.zeros((size, size, 4), np.uint8)
    s = int(round(size * fill))
    o = (size - s) // 2
    g[o:o + s, o:o + s] = cv2.resize(inner, (s, s), interpolation=cv2.INTER_AREA)
    return g if alpha else g[..., :3]


def _cell(glyph: np.ndarray) -> np.ndarray:
    """글리프 아이콘(128px, 글리프 비율 0.92 = CDragon 원본과 같은 기하) → 게임 칸(38x37, 배경 AUG_BG)."""
    a = glyph[..., 3:] / 255.0
    comp = (glyph[..., :3] * a + np.array(AUG_BG) * (1 - a)).astype(np.uint8)
    icon = cv2.resize(comp, (36, 36), interpolation=cv2.INTER_AREA)   # 칸 38px 안에 아이콘 36px(템플릿 기하와 같다)
    return cv2.copyMakeBorder(icon, 1, 0, 1, 1, cv2.BORDER_CONSTANT, value=AUG_BG)


def _manifest() -> dict:
    """대체 출처: 내면의 야수 4종은 같은 그림, 고단수 II는 따로."""
    return {PRIMAL: {"group": PRIMAL}, PRIMAL_S: {"group": PRIMAL}, PRIMAL_PLUS: {"group": PRIMAL},
            "DA_18_PrimalAugmentPlus_Sivir": {"group": PRIMAL}, VERT: {"group": VERT}}


def _learner(static, templates: dict[str, np.ndarray], glyphs: dict[str, np.ndarray] | None = None,
             save_dir=None) -> AugmentLearner:
    mt = AugmentIconMatcher(templates.items(), (glyphs or {}).items())
    return AugmentLearner(static, mt, augment_visual_keys(static, _manifest()), save_dir=save_dir)


# ---------------------------------------------------------------- 글리프 정규화 경로
def test_glyph_path_matches_smaller_glyph_that_fixed_geometry_misses():
    """대체 출처 아이콘(글리프가 작고 둘레가 빈)은 고정 기하로는 안 맞고 글리프 정규화로 맞는다."""
    small = {f"A{i}": _glyph(i, fill=0.72) for i in range(5)}
    cell = _cell(_glyph(3))
    fixed = AugmentIconMatcher(small.items()).match(cell)
    glyph = AugmentIconMatcher([], small.items()).match(cell)
    assert glyph.api_name == "A3" and glyph.via_glyph and glyph.score > 0.85
    assert glyph.score - fixed.score > 0.1 or fixed.api_name != "A3"


def test_glyph_normalize_is_scale_and_offset_invariant():
    a = glyph_normalize(_glyph(7, size=256, fill=0.6))
    b = glyph_normalize(_glyph(7, size=64, fill=0.95))
    assert a.shape == b.shape == (36, 36, 3)
    assert float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED).max()) > 0.9
    assert glyph_normalize(np.full((20, 20, 3), AUG_BG, np.uint8)) is None


# ---------------------------------------------------------------- 그림 동일성
def test_visual_keys_group_alt_pictures_and_keep_unknown_missing_apart(static):
    keys = augment_visual_keys(static, _manifest())
    assert keys[PRIMAL] == keys[PRIMAL_S] == keys[PRIMAL_PLUS]          # 대체 출처 같은 그림
    assert keys[VERT] != keys[PRIMAL]
    no_alt = augment_visual_keys(static)
    assert no_alt[PRIMAL] != no_alt[PRIMAL_S]                          # 그림을 모르면 묶지 않는다
    assert no_alt[CM] == keys[CM] and "missing" not in keys[CM]
    # OP.GG 전체 URL 아이콘은 같은 파일명의 CDragon 경로와 같은 키
    rec = static.get("augments", "DA_CalculatedLoss")
    assert rec["icon"].startswith("http") and keys["DA_CalculatedLoss"].endswith("hexcore/calculatedloss2")


def test_alt_manifest_missing_is_empty(tmp_path):
    assert load_alt_manifest(tmp_path) == {} and load_alt_manifest(None) == {}
    (tmp_path / "sources.json").write_text("{broken", encoding="utf-8")
    assert load_alt_manifest(tmp_path) == {}


# ---------------------------------------------------------------- decide
def test_decide_picks_offered_candidate_with_lower_threshold(static):
    tpl = {CM: _glyph(1), ASC: _glyph(2), VERT: _glyph(3)}
    lr = _learner(static, tpl)
    d = lr.decide(_cell(_glyph(2)), [CM, ASC, VERT])
    assert d is not None and d.api == ASC and d.reason == "match" and d.score >= LEARN_MIN
    # 제시되지 않은 증강의 그림이면 후보 안에서 억지로 고르지 않는다(점수가 낮다)
    assert lr.decide(_cell(_glyph(9)), [CM, ASC, VERT]) is None


def test_decide_refuses_when_same_picture_different_names_offered_together(static):
    """내면의 야수 / 내면의 야수+ 는 그림이 같다 → 둘 다 제시되면 모른다. 하나만 제시되면 확정."""
    lr = _learner(static, {CM: _glyph(1)}, {PRIMAL: _glyph(5, fill=0.8)})
    cell = _cell(_glyph(5))
    assert lr.decide(cell, [CM, PRIMAL, PRIMAL_PLUS]) is None
    d = lr.decide(cell, [CM, PRIMAL_S, VERT])     # 같은 그림(Nidalee 템플릿) → 제시된 Sivir ID로 확정
    assert d is not None and d.api == PRIMAL_S and d.via_glyph


def test_decide_elimination_only_when_templated_candidates_clearly_fail(static):
    lr = _learner(static, {CM: _glyph(1), ASC: _glyph(2)})
    d = lr.decide(_cell(_glyph(8)), [CM, ASC, VERT])          # VERT: 템플릿 없음, 나머지 둘은 낮은 점수
    assert d is not None and d.api == VERT and d.reason == "elimination"
    lr2 = _learner(static, {CM: _glyph(1)})
    assert lr2.decide(_cell(_glyph(8)), [CM, ASC, VERT]) is None   # 템플릿 없는 후보가 둘 → 모른다


def test_decide_needs_margin_between_candidates(static):
    base = _glyph(4)
    near = base.copy()
    near[60:70, 60:70] = 0                                     # 거의 같은 그림(초월 / 불완전한 초월처럼)
    lr = _learner(static, {ASC: base, PARTIAL: near, CM: _glyph(1)})
    cell = _cell(base)
    assert lr.decide(cell, [ASC, PARTIAL, CM]) is None
    assert lr.decide(cell, [ASC, CM, VERT]) is not None        # 비슷한 것이 제시되지 않았으면 확정


def test_commit_saves_screen_template_and_updates_matcher(static, tmp_path):
    lr = _learner(static, {CM: _glyph(1)}, save_dir=tmp_path / "augments_screen")
    cell = _cell(_glyph(6))
    path = lr.commit(cell, VERT)
    assert path is not None and path.name == f"{VERT}.png" and path.exists()
    m = lr.matcher.match(cell)
    assert m.api_name == VERT and m.score > 0.99
    before = path.read_bytes()
    lr.commit(_cell(_glyph(1)), VERT)                          # 이미 있으면 덮어쓰지 않는다
    assert path.read_bytes() == before


# ---------------------------------------------------------------- 세션 흐름(제시 → 새 칸 → 확정·저장)
def _offer(stage: str, ids: list[str]) -> GameState:
    return GameState(screen_mode=ScreenMode.AUGMENT_SELECT, stage=stage,
                     augment_offer=[AugmentRef(id=i, confidence=0.9) for i in ids])


def _planning(stage: str) -> GameState:
    return GameState(screen_mode=ScreenMode.PLANNING, stage=stage)


def _row(ids: list[str | None], glyph_seeds: list[int]) -> OwnedRow:
    return OwnedRow(cells=[_cell(_glyph(s)) for s in glyph_seeds], ids=list(ids), scores=[0.9] * len(ids))


def test_session_learns_new_cell_from_offer_including_reroll(static, tmp_path):
    lr = _learner(static, {CM: _glyph(1), ASC: _glyph(2), VERT: _glyph(3)}, save_dir=tmp_path / "screen")
    t = SessionTracker(tmp_path / "session.json")
    t.learner = lr
    t.observe(_planning("3-1"), {"owned"}, owned_row=_row([CM], [1]))          # 선택 전 1칸
    t.observe(_offer("3-2", [PRIMAL, "DA_18_FOURcing", ASC]), {"augment"})
    t.observe(_offer("3-2", [PRIMAL, "DA_18_FOURcing", VERT]), {"augment"})     # 리롤: VERT가 새로 제시
    assert t.data.offer_pool == [PRIMAL, "DA_18_FOURcing", ASC, VERT] and t.data.offer_base == 1
    # 고른 증강이 아직 줄에 없다 → 기다린다
    t.observe(_planning("3-2"), {"owned"}, owned_row=_row([CM], [1]))
    assert t.data.offer_pool and not t.data.learned
    m = t.observe(_planning("3-2"), {"owned"}, owned_row=_row([CM, None], [1, 3]))
    assert [a.id for a in m.augments_owned] == [CM, VERT]
    assert t.data.augments_source == "tracked" and t.data.offer_pool == [] and t.data.owned_count == 2
    rec = t.data.learned[-1]
    assert rec["api"] == VERT and rec["reason"] == "match" and (tmp_path / "screen" / f"{VERT}.png").exists()
    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert saved["learned"][-1]["api"] == VERT and saved["owned_count"] == 2
    # 저장된 템플릿은 이 판에서 바로 전체 목록 매칭에도 쓰인다
    assert lr.matcher.match(_cell(_glyph(3))).api_name == VERT


def test_session_does_not_learn_without_offer_or_with_two_new_cells(static, tmp_path):
    lr = _learner(static, {CM: _glyph(1), ASC: _glyph(2), VERT: _glyph(3)})
    t = SessionTracker()
    t.learner = lr
    t.observe(_planning("3-1"), {"owned"}, owned_row=_row([CM], [1]))
    t.observe(_planning("3-2"), {"owned"}, owned_row=_row([CM, None], [1, 3]))   # 제시 목록 없음
    assert not t.data.learned and t.data.augments_owned == []
    t2 = SessionTracker()
    t2.learner = lr
    t2.observe(_planning("3-1"), {"owned"}, owned_row=_row([CM], [1]))
    t2.observe(_offer("3-2", [ASC, VERT, "DA_18_FOURcing"]), {"augment"})
    t2.observe(_planning("4-2"), {"owned"}, owned_row=_row([CM, None, None], [1, 3, 2]))  # 새 칸 2개
    assert not t2.data.learned and t2.data.offer_pool == []


def test_session_does_not_learn_when_base_count_unknown(static):
    """앱을 증강 선택 화면에서 켜서 선택 전 칸 수를 모르면 학습하지 않는다."""
    t = SessionTracker()
    t.learner = _learner(static, {CM: _glyph(1), VERT: _glyph(3)})
    t.observe(_offer("3-2", [CM, VERT, ASC]), {"augment"})
    assert t.data.offer_base is None
    t.observe(_planning("3-2"), {"owned"}, owned_row=_row([None], [3]))
    assert not t.data.learned


def test_session_rejects_vision_id_that_was_not_offered(static):
    t = SessionTracker()
    t.learner = _learner(static, {CM: _glyph(1), VERT: _glyph(3)})
    t.observe(_planning("3-1"), {"owned"}, owned_row=_row([CM], [1]))
    t.observe(_offer("3-2", [ASC, VERT, "DA_18_FOURcing"]), {"augment"})
    t.observe(_planning("3-2"), {"owned"}, owned_row=_row([CM, "DA_BonusGift"], [1, 9]))
    assert not t.data.learned and t.data.offer_pool == []
    # vision이 제시된 증강으로 읽었으면 그대로 기록(템플릿 저장 없음)
    t.observe(_planning("3-5"), {"owned"}, owned_row=_row([CM, VERT], [1, 3]))
    t.observe(_offer("4-2", [ASC, "DA_18_FOURcing", PARTIAL]), {"augment"})
    t.observe(_planning("4-2"), {"owned"}, owned_row=_row([CM, VERT, ASC], [1, 3, 2]))
    assert t.data.learned[-1] == {"stage": "4-2", "offered": [ASC, "DA_18_FOURcing", PARTIAL],
                                  "api": ASC, "reason": "vision"}


def test_new_augment_round_drops_unconsumed_pool_and_reset_clears(static, tmp_path):
    t = SessionTracker(tmp_path / "session.json")
    t.learner = _learner(static, {CM: _glyph(1)})
    t.observe(_planning("2-1"), {"owned"}, owned_row=_row([], []))
    t.observe(_offer("2-1", [CM, ASC, VERT]), {"augment"})
    t.observe(_offer("3-2", [PARTIAL, "DA_18_FOURcing", "DA_BonusGift"]), {"augment"})
    assert t.data.offer_pool == [PARTIAL, "DA_18_FOURcing", "DA_BonusGift"] and t.data.offer_pool_stage == "3-2"
    t.save()
    t2 = SessionTracker(tmp_path / "session.json")
    assert t2.load() and t2.data.offer_pool == t.data.offer_pool and t2.data.offer_base == 0
    t2.reset("new game")
    assert t2.data.offer_pool == [] and t2.data.owned_count is None and t2.data.learned == []


def test_live_loop_hands_recognizer_learner_to_tracker():
    from tft_advisor.app.loop import LiveLoop

    class _Rec:
        augment_learner = object()
        profile = None

    loop = LiveLoop(source=object(), recognizer=_Rec(), detector=object())
    assert loop.tracker.learner is _Rec.augment_learner


# ---------------------------------------------------------------- fetch-augments 대체 출처
def test_fetch_augment_alts_groups_identical_pictures(static, tmp_path, monkeypatch):
    from tft_advisor.vision import templates as T

    monkeypatch.setattr(T, "template_dir", lambda s, kind: tmp_path / kind)
    primal = {PRIMAL, PRIMAL_S, PRIMAL_PLUS, "DA_18_PrimalAugmentPlus_Sivir"}

    def fake(url: str) -> bytes:
        api = url.rsplit("/", 1)[-1][:-5]            # "{api}{tier}.png"
        seed = 5 if api in primal else sum(map(ord, api))
        ok, buf = cv2.imencode(".png", _glyph(seed, fill=0.8))
        return buf.tobytes()

    n, failed, manifest = T.fetch_augment_alts(18, download=fake)
    e = manifest["entries"]
    assert not failed and n == len(e) and PRIMAL_S in e
    groups = {e[a]["group"] for a in primal}
    assert len(groups) == 1                               # 같은 그림 → 파일 하나
    rep = groups.pop()
    files = {p.stem for p in (tmp_path / "augments_alt").glob("*.png")}
    assert rep in files and not (primal - {rep}) & files
    assert json.loads((tmp_path / "augments_alt" / "sources.json").read_text(encoding="utf-8"))["entries"] == e
    keys = augment_visual_keys(static, e)
    assert len({keys[a] for a in primal}) == 1


# ---------------------------------------------------------------- 인식기: 칸별 결과(last_owned_row) + 학습 후 전체 목록
def test_recognizer_exposes_row_cells_and_learned_template_completes_field(static, tmp_path):
    from pathlib import Path

    from test_vision_1080p import ColorKeyedOcr, _paint, _row_frame

    from tft_advisor.vision.capture import save_image
    from tft_advisor.vision.recognizer import Recognizer

    tdir = tmp_path / "aug"
    tdir.mkdir()
    save_image(tdir / f"{CM}.png", _glyph(1))
    img = _row_frame([_glyph(1), _glyph(3)])
    table: dict = {}
    _paint(img, None, {"stage": "3-2", "xp_button": "경험치 구매", "board_count": "6/6"}, table)
    rec = Recognizer(static=static, ocr=ColorKeyedOcr(table), item_template_dir=Path("/nonexistent"),
                     augment_template_dir=tdir)
    s = rec.recognize(img, groups={"owned"})
    row = rec.last_owned_row
    assert s.augments_owned is None and row is not None and row.ids == [CM, None] and len(row.cells) == 2
    assert rec.augment_learner.save_dir is None           # 지정 디렉터리(테스트)면 저장하지 않는다
    rec.augment_learner.commit(row.cells[1], VERT)         # 학습 → 같은 판에서 바로 전체 목록 인식
    s2 = rec.recognize(img, groups={"owned"})
    assert [a.id for a in s2.augments_owned] == [CM, VERT]
    rec.recognize(img, groups={"hud"})
    assert rec.last_owned_row is None                      # 줄을 읽지 않은 프레임은 None


# ---------------------------------------------------------------- 실캡처 회귀(5-5 가운데 칸, 파일·캐시 없으면 skip)
def test_raw_5_5_middle_cell_is_beast_within_family(static):
    from pathlib import Path

    from tft_advisor.vision.capture import load_image
    from tft_advisor.vision.recognizer import Recognizer, augment_alt_dir, augment_template_dirs

    raw = Path(__file__).parent / "fixtures" / "screens" / "raw" / "5-5 전투 전.png"
    if not raw.is_file() or not (augment_alt_dir(18) / "sources.json").is_file() or \
            not any(augment_template_dirs(18)[0].glob("*.png")):
        pytest.skip("원본 캡처 또는 증강 아이콘 캐시 없음(gitignore)")
    rec = Recognizer(static=static)
    s = rec.recognize(load_image(raw), groups={"owned"})
    row = rec.last_owned_row
    assert row is not None and len(row) == 3 and row.ids[0] == CM and row.ids[2] == ASC
    assert row.ids[1] is None and s.augments_owned is None     # 내면의 야수 / 내면의 야수+ 그림이 같다 → 모름
    m = rec.augment_icons.match(row.cells[1])
    assert m.via_glyph and m.score >= 0.8 and m.margin >= 0.10
    assert static.get("augments", m.api_name)["name_ko"].startswith("내면의 야수")
    # 선택 순간 학습: 내면의 야수만 제시됐다면 확정된다
    d = rec.augment_learner.decide(row.cells[1], [PRIMAL_S, "DA_18_RiftbeastTraitAugment", "DA_18_FOURcing"])
    assert d is not None and d.api == PRIMAL_S
    assert rec.augment_learner.decide(row.cells[1], [PRIMAL_S, PRIMAL_PLUS, CM]) is None
