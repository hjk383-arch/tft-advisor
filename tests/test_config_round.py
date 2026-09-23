"""Phase 3 config round: 새 설정 키(기본값·범위·제약), jev_backend 선택, [vision] 연결, fixtures items/item_bench."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tft_advisor.config import (
    AdvisorCfg, CaptureCfg, CompWeights, ItemWeights, Settings, StatsCfg, VisionCfg, Weights, load_settings,
    load_weights,
)

NEW_DEFAULTS = {
    ("settings", "stats", "keep_snapshots"): 5,
    ("settings", "advisor", "jev_backend"): "mock",
    ("settings", "vision", "content_box"): None,
    ("settings", "vision", "ocr_backend"): "auto",
    ("settings", "vision", "name_fuzzy_min_margin"): 10,
    ("settings", "vision", "name_fuzzy_relaxed_margin"): 15,
    ("settings", "vision", "item_match_margin"): 0.05,
    ("settings", "vision", "change_threshold"): 24,
    ("settings", "vision", "change_stable_frames"): 2,
    ("settings", "vision", "capture_fps"): 4,
    ("settings", "vision", "traits_every_s"): 3,
    ("settings", "vision", "resolution"): "auto",
    ("settings", "vision", "aspect"): "auto",
    ("settings", "vision", "profile"): "auto",
    ("settings", "vision", "content_box_auto"): True,
    ("weights", "comp", "hysteresis_other_share"): 0.25,
    ("weights", "item", "overall_stat_games_factor"): 0.25,
}


# --------------------------------------------------------------------------- 기본값


@pytest.mark.parametrize(("where", "section", "key"), list(NEW_DEFAULTS))
def test_new_key_defaults_in_model_and_files(where, section, key):
    want = NEW_DEFAULTS[(where, section, key)]
    model_default = getattr(getattr(Settings() if where == "settings" else Weights(), section), key)
    loaded = getattr(getattr(load_settings() if where == "settings" else load_weights(), section), key)
    assert model_default == want
    assert loaded == want, "config/*.toml 값이 기본값과 다르다"


def test_capture_superseded_keys_are_rejected():
    """[capture] poll_interval_ms / stable_frames 는 [vision] capture_fps / change_stable_frames 로 대체(중복 키 제거)."""
    for k in ("poll_interval_ms", "stable_frames"):
        with pytest.raises(ValidationError):
            CaptureCfg.model_validate({k: 1})
    assert CaptureCfg().monitor == "auto"   # vision 07: 듀얼 모니터 사용자를 위해 기본 자동 선택


def test_capture_monitor_accepts_auto_or_index():
    assert CaptureCfg.model_validate({"monitor": 2}).monitor == 2
    assert CaptureCfg.model_validate({"monitor": "auto"}).monitor == "auto"
    for bad in (-1, "second", 1.5):
        with pytest.raises(ValidationError):
            CaptureCfg.model_validate({"monitor": bad})


# --------------------------------------------------------------------------- 범위·제약 거부


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (StatsCfg, {"keep_snapshots": 0}),
        (StatsCfg, {"keep_snapshots": -3}),
        (CompWeights, {"hysteresis_other_share": 1.2}),
        (CompWeights, {"hysteresis_other_share": -0.1}),
        (ItemWeights, {"overall_stat_games_factor": 1.5}),
        (ItemWeights, {"overall_stat_games_factor": -0.01}),
        (AdvisorCfg, {"jev_backend": "auto"}),        # auto 는 설정값이 아니다(키 유무로 live 전환 금지)
        (AdvisorCfg, {"jev_backend": "LIVE"}),
        (AdvisorCfg, {"jev_backend": ""}),
        (VisionCfg, {"ocr_backend": "tesseract"}),
        (VisionCfg, {"name_fuzzy_min_margin": -1}),
        (VisionCfg, {"name_fuzzy_relaxed_margin": 101}),
        (VisionCfg, {"name_fuzzy_min_margin": 16}),  # min_margin > relaxed_margin(15)
        (VisionCfg, {"item_match_margin": 1.5}),
        (VisionCfg, {"item_match_margin": -0.1}),
        (VisionCfg, {"change_threshold": 256}),
        (VisionCfg, {"change_threshold": -1}),
        (VisionCfg, {"change_stable_frames": 0}),
        (VisionCfg, {"capture_fps": 0}),
        (VisionCfg, {"capture_fps": 60}),
        (VisionCfg, {"traits_every_s": 0}),
        (VisionCfg, {"aspect": "16:11"}),            # 지원하지 않는 비율 이름
        (VisionCfg, {"resolution": "1920"}),         # WxH 형식이 아님
        (VisionCfg, {"resolution": "1920x1080", "aspect": "16:10"}),   # 해상도와 비율이 어긋남
        (VisionCfg, {"poll_interval_ms": 500}),       # 모르는 키
    ],
    ids=lambda v: v.__name__ if isinstance(v, type) else json.dumps(v),
)
def test_new_keys_reject_out_of_range(model, kwargs):
    with pytest.raises(ValidationError):
        model.model_validate(kwargs)


@pytest.mark.parametrize(
    "box",
    [
        [0.5, 0, 0.4, 1],         # x1 > x2
        [0, 0.3, 1, 0.3],         # y1 == y2
        [0.2, 0.2, 0.2, 0.9],     # x1 == x2
        [0, 0, 1.2, 1],           # > 1
        [-0.1, 0, 1, 1],          # < 0
        [0, 0, 1],                # 3개
        [0, 0, 1, 1, 0],          # 5개
        "0,0,1,1",
    ],
)
def test_content_box_rejects_invalid(box):
    with pytest.raises(ValidationError):
        VisionCfg(content_box=box)


def test_content_box_full_frame_and_conversion():
    assert VisionCfg().content_box is None
    assert VisionCfg(content_box=[]).content_box is None                       # TOML에는 null이 없다
    assert VisionCfg(content_box=[0, 0, 1, 1]).content_px(1920, 1080) is None  # 명시적 전체 = None 경로
    assert VisionCfg().content_px(1920, 1080) is None
    # 2560x1440 모니터의 (200,150) 1600x900 창
    cfg = VisionCfg(content_box=[200 / 2560, 150 / 1440, 1800 / 2560, 1050 / 1440])
    assert cfg.content_px(2560, 1440) == (200, 150, 1600, 900)
    # 1920x1200 16:10 레터박스(위아래 60px)
    assert VisionCfg(content_box=[0, 60 / 1200, 1, 1140 / 1200]).content_px(1920, 1200) == (0, 60, 1920, 1080)
    # TOML 로드 경로
    s = Settings.model_validate({"vision": {"content_box": [0.1, 0.1, 0.9, 0.9]}})
    assert s.vision.content_box == (0.1, 0.1, 0.9, 0.9)


def test_margin_boundary_equal_is_allowed():
    assert VisionCfg(name_fuzzy_min_margin=15, name_fuzzy_relaxed_margin=15).name_fuzzy_min_margin == 15


# --------------------------------------------------------------------------- jev_backend


def test_create_advisor_auto_follows_setting_not_api_key(monkeypatch):
    """TYPESAFE_API_KEY가 있어도 기본(auto → settings.advisor.jev_backend = "mock")은 live가 아니다."""
    from tft_advisor.advisor import MockJevBackend, create_advisor

    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy-not-a-real-key")
    adv = create_advisor()
    try:
        assert adv.backend_name == "mock" and isinstance(adv.gateway.backend, MockJevBackend)
    finally:
        adv.close()


@pytest.mark.parametrize(("setting", "explicit", "want"), [
    ("off", "auto", "off"),       # --no-jev(Phase 4) = "off"
    ("live", "auto", "live"),     # 설정으로만 live
    ("live", "mock", "mock"),     # 명시 mode가 설정보다 우선
    ("mock", "off", "off"),
    ("mock", "live", "live"),
])
def test_create_advisor_setting_and_explicit_override(monkeypatch, setting, explicit, want):
    from tft_advisor.advisor import LiveJevBackend, create_advisor

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)   # live 여도 네트워크 호출은 없다(생성만)
    s = Settings.model_validate({"advisor": {"jev_backend": setting}})
    adv = create_advisor(explicit, settings=s)
    try:
        assert adv.backend_name == want
        assert (adv.gateway.backend is None) == (want == "off")
        assert isinstance(adv.gateway.backend, LiveJevBackend) == (want == "live")
    finally:
        adv.close()


# --------------------------------------------------------------------------- [vision] 연결


def test_vision_defaults_match_module_defaults():
    """설정 기본값 = vision 모듈 생성자 기본값(설정 없이 쓰는 경로와 동작이 같다)."""
    from tft_advisor.vision.change import ChangeDetector
    from tft_advisor.vision.matching import MIN_MARGIN, RELAXED_MARGIN
    from tft_advisor.vision.regions import SET18_16X9

    cfg = VisionCfg()
    assert (cfg.name_fuzzy_min_margin, cfg.name_fuzzy_relaxed_margin) == (MIN_MARGIN, RELAXED_MARGIN)
    d = ChangeDetector(SET18_16X9)
    assert (d.threshold, d.stable_frames) == (cfg.change_threshold, cfg.change_stable_frames)


def test_vision_reads_config(monkeypatch):
    import numpy as np

    from tft_advisor.vision import recognizer as rmod
    from tft_advisor.vision.change import ChangeDetector
    from tft_advisor.vision.ocr import NullOcr, create_ocr
    from tft_advisor.vision.regions import SET18_16X9

    cfg = VisionCfg(ocr_backend="none", name_fuzzy_min_margin=12, name_fuzzy_relaxed_margin=20,
                    item_match_margin=0.1, change_threshold=30, change_stable_frames=3,
                    content_box=[0, 60 / 1200, 1, 1140 / 1200])
    rec = rmod.Recognizer(cfg=cfg)
    assert isinstance(rec.ocr, NullOcr)
    for m in (rec.shop_matcher, rec.augment_matcher, rec.trait_matcher):
        assert (m.min_margin, m.relaxed_margin) == (12, 20)
    assert isinstance(create_ocr(backend="none"), NullOcr)

    det = ChangeDetector.from_cfg(SET18_16X9, cfg)
    assert (det.threshold, det.stable_frames) == (30, 3)

    seen = []
    real = rmod.FrameMapper.for_image

    def spy(image, content=None):
        seen.append(content)
        return real(image, content)

    monkeypatch.setattr(rmod.FrameMapper, "for_image", staticmethod(spy))
    frame = np.zeros((1200, 1920, 3), np.uint8)
    rec.recognize(frame)                                   # 설정 content_box 사용
    rec.recognize(frame, content=(0, 0, 1920, 1200))       # 명시 인자가 우선
    assert seen == [(0, 60, 1920, 1080), (0, 0, 1920, 1200)]


def test_item_match_margin_is_read_from_config():
    """ITEM_MIN_MARGIN 상수 대신 cfg.item_match_margin 을 쓴다."""
    import inspect

    from tft_advisor.vision import recognizer as rmod

    assert not hasattr(rmod, "ITEM_MIN_MARGIN")
    assert "self.cfg.item_match_margin" in inspect.getsource(rmod.Recognizer._read_items)


# --------------------------------------------------------------------------- fixtures items / item_bench


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "x.expected.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


BENCH = ["자석 제거기", "B.F. 대검", "연습용 장갑", "고속 연사포", None, None, None, None, None, None]


def test_fixture_item_bench_to_ids_and_items(tmp_path):
    from tft_advisor.fixtures import load_expected

    e = load_expected(_write(tmp_path, {"stage": "3-2", "item_bench": BENCH}))
    assert e.extras["item_bench"][:4] == ["DA_Consumable_ItemRemover", "DA_Component_BFSword",
                                          "DA_Component_SparringGloves", "DA_Artifact_RapidFireCannon"]
    assert e.extras["item_bench"][4:] == [None] * 6
    assert "items" in e.fields and "item_bench" not in e.fields
    assert [i.id for i in e.state.items.components] == ["DA_Component_BFSword", "DA_Component_SparringGloves"]
    assert sorted(i.id for i in e.state.items.others) == ["DA_Artifact_RapidFireCannon", "DA_Consumable_ItemRemover"]


def test_fixture_items_buckets_and_flat_list(tmp_path):
    from tft_advisor.fixtures import load_expected

    e = load_expected(_write(tmp_path, {"items": {"components": ["B.F. 대검"], "others": ["자석 제거기"]}}))
    assert e.state.items.all_ids() == ["DA_Component_BFSword", "DA_Consumable_ItemRemover"]
    assert e.extras == {}
    flat = load_expected(_write(tmp_path, {"items": ["자석 제거기", "DA_Component_BFSword"]}))
    assert sorted(flat.state.items.all_ids()) == sorted(e.state.items.all_ids())
    # items + item_bench 가 같으면 통과
    both = load_expected(_write(tmp_path, {"items": ["B.F. 대검", "연습용 장갑", "자석 제거기", "고속 연사포"],
                                           "item_bench": BENCH}))
    assert len(both.state.items.all_ids()) == 4


@pytest.mark.parametrize(("data", "exc"), [
    ({"items": {"completed": ["B.F. 대검"]}}, ValueError),        # 라벨 버킷 ≠ 정적 데이터 category
    ({"items": {"weapons": ["B.F. 대검"]}}, ValueError),          # 모르는 버킷
    ({"items": ["없는 아이템"]}, KeyError),
    ({"item_bench": ["없는 아이템"]}, KeyError),
    ({"item_bench": [None] * 11}, ValueError),
    ({"items": ["B.F. 대검"], "item_bench": BENCH}, ValueError),  # 서로 다름
])
def test_fixture_items_rejects(tmp_path, data, exc):
    from tft_advisor.fixtures import load_expected

    with pytest.raises(exc):
        load_expected(_write(tmp_path, data))


def test_evaluate_compares_items_by_ids():
    from tft_advisor.contracts import ItemRef, ItemState
    from tft_advisor.vision.evaluate import _norm

    a = ItemState(components=[ItemRef(id="DA_Component_BFSword", confidence=1.0)])
    b = ItemState(components=[ItemRef(id="DA_Component_BFSword", confidence=0.83, name_ko="B.F. 대검")])
    assert _norm("items", a) == _norm("items", b)


# --------------------------------------------------------------------------- 화면 크기/비율 설정


def test_screen_size_settings_resolve_in_priority_order():
    """profile > aspect > resolution > "auto"(프레임에서 자동 판별)."""
    assert VisionCfg().aspect_setting() == "auto"
    assert VisionCfg(resolution="1280x800").aspect_setting() == "1280x800"
    assert VisionCfg(aspect="16:10", resolution="1280x800").aspect_setting() == "16:10"
    assert VisionCfg(profile="set18_16x9", aspect="16:10").aspect_setting() == "set18_16x9"
    assert VisionCfg(profile="1920x1080").aspect_setting() == "1920x1080"   # 옛 기본값도 그대로 동작


def test_resolution_size_and_matching_aspect_is_allowed():
    assert VisionCfg().resolution_size() is None
    assert VisionCfg(resolution="1920x1200").resolution_size() == (1920, 1200)
    assert VisionCfg(resolution="1920x1200", aspect="16:10").aspect == "16:10"   # 일치 → 통과
    assert VisionCfg(resolution="1279x797", aspect="16:10").aspect == "16:10"    # 허용 오차 안(실제 캡처)


def test_settings_toml_screen_keys_are_documented():
    """settings.toml 에 새 키가 주석과 함께 있어야 한다(사용자가 고칠 값이다)."""
    text = (Path(__file__).resolve().parents[1] / "config" / "settings.toml").read_text(encoding="utf-8")
    for key in ("resolution", "aspect", "profile", "content_box_auto"):
        assert f"\n{key} = " in text, key
