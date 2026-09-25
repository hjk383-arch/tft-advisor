"""QA 27 W5: `--screenshot`(·`--test-view`)·설정 테스트 캡처는 유닛 사진 DB(`units_screen/_pending/`)에 쌓지 않는다.

수집기가 켜진 인식기를 넘겨도 `run_screenshot`이 끄고, 스스로 만들 때는 `unit_autolearn=False`로 만든다.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tft_advisor.app.no_collect import disable_unit_collector, make_recognizer_no_collect, no_collect_vision
from tft_advisor.app.screenshot import run_screenshot

from .conftest import SCREENS

TEST_PNG = SCREENS / "test" / "test.png"


def test_no_collect_vision_copies_without_autolearn(settings):
    on = settings.vision.model_copy(update={"unit_autolearn": True})
    off = no_collect_vision(on)
    assert off.unit_autolearn is False
    assert on.unit_autolearn is True            # 원본은 그대로
    assert off.unit_names == on.unit_names     # 이름 인식 자체는 켜 둔다


def test_disable_unit_collector_nulls_collector():
    namer = SimpleNamespace(collector=object(), autolearn=True, agree_frames=3)
    rec = SimpleNamespace(unit_namer=namer)
    assert disable_unit_collector(rec) is True
    assert namer.collector is None and namer.autolearn is False
    assert disable_unit_collector(rec) is False                 # 두 번째는 할 일 없음
    assert disable_unit_collector(SimpleNamespace(unit_namer=None)) is False
    assert disable_unit_collector(object()) is False


def test_make_recognizer_no_collect_passes_off_cfg(monkeypatch, settings):
    seen = {}

    class Fake:
        def __init__(self, cfg=None, **kw):
            seen["cfg"] = cfg
            self.unit_namer = SimpleNamespace(collector=None, autolearn=False)

    monkeypatch.setattr("tft_advisor.vision.recognizer.Recognizer", Fake)
    make_recognizer_no_collect(settings.vision.model_copy(update={"unit_autolearn": True}))
    assert seen["cfg"].unit_autolearn is False


def test_run_screenshot_builds_recognizer_without_autolearn(monkeypatch, settings, tmp_path):
    """인식기를 넘기지 않으면(= CLI `--screenshot`) 수집기 없는 설정으로 만든다."""
    seen = {}

    class Fake:
        def __init__(self, cfg=None, **kw):
            seen["cfg"] = cfg
            self.unit_namer = None

    monkeypatch.setattr("tft_advisor.vision.recognizer.Recognizer", Fake)
    s = settings.model_copy(update={"vision": settings.vision.model_copy(update={"unit_autolearn": True})})
    code = run_screenshot(tmp_path / "없음.png", settings=s, out=lambda _l: None, advisor=object())
    assert code == 2                             # 입력 없음 — 인식기는 만들기 전에 끝날 수도 있다
    # 입력이 있으면 만든다: 빈 이미지 1장을 주고 인식은 실패해도 된다(한 장 실패는 삼킨다)
    img = tmp_path / "blank.png"
    import cv2
    import numpy as np

    cv2.imwrite(str(img), np.zeros((10, 10, 3), np.uint8))
    run_screenshot(img, settings=s, out=lambda _l: None,
                   advisor=SimpleNamespace(advise=lambda st: None, backend_name="mock"))
    assert seen["cfg"].unit_autolearn is False


class SpyCollector:
    """`UnitCollector` 대역 — observe가 불렸는지만 센다."""

    def __init__(self, real):
        self.real = real
        self.db = real.db
        self.observed = 0

    def observe(self, *a, **kw):
        self.observed += 1
        return self.real.observe(*a, **kw)

    def reset(self):
        self.real.reset()

    def note_purchase(self, *a, **kw):
        self.real.note_purchase(*a, **kw)


@pytest.mark.skipif(not TEST_PNG.is_file(), reason="사용자 캡처 test.png 없음(gitignore)")
def test_screenshot_run_writes_nothing_to_pending(recognizer, settings, tmp_path):
    """수집기를 켜 둔 인식기로 test.png(2-6, QA가 `traits` 5장이 쌓인 그 이미지)를 돌려도 `_pending/`이 비어 있다."""
    from tft_advisor.advisor import create_advisor
    from tft_advisor.vision.unit_db import UnitCollector, UnitImageDB

    namer = recognizer.unit_namer
    if namer is None:
        pytest.skip("unit_names=false")
    before = (namer.collector, namer.autolearn, namer.agree_frames)
    spy = SpyCollector(UnitCollector(UnitImageDB(tmp_path / "units_screen")))
    try:
        # 대조: 수집기가 붙어 있으면 이 이미지에서 observe가 불린다(= 수집 경로가 실제로 켜진다)
        namer.collector = spy
        recognizer.recognize(_load(TEST_PNG), source_image=str(TEST_PNG))
        assert spy.observed >= 1
        for p in (tmp_path / "units_screen").rglob("*"):   # 대조에서 쌓인 것은 지운다
            if p.is_file():
                p.unlink()
        spy.observed = 0

        namer.collector = spy
        adv = create_advisor("mock", settings=settings)
        try:
            code = run_screenshot(TEST_PNG, settings=settings, recognizer=recognizer, advisor=adv,
                                  out=lambda _l: None, test_view=True)
        finally:
            adv.close()
        assert code == 0
        assert namer.collector is None
        assert spy.observed == 0
        assert not [p for p in (tmp_path / "units_screen").rglob("*") if p.is_file()]
    finally:
        namer.collector, namer.autolearn, namer.agree_frames = before


def _load(path: Path):
    from tft_advisor.vision.capture import load_image

    return load_image(path)
