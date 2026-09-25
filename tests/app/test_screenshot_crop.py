"""`--screenshot`: 이미 게임 영역만 잘린 이미지에 [vision] content_box를 또 적용하지 않는다(두 번 잘림 → "알 수 없음")."""
from __future__ import annotations

import numpy as np

from tft_advisor.app import setup as core
from tft_advisor.app.screenshot import content_box_applies, content_without_box
from tft_advisor.config import load_settings

BOX = [0.22093, 0.108333, 0.77907, 0.858333]


def windowed():
    return core.apply_updates(load_settings(), {"vision": {"content_box": BOX, "resolution": "1920x1080",
                                                           "aspect": "auto", "profile": "auto"}})


def test_frame_size_rule():
    s = windowed()
    assert content_box_applies((3440, 1440), s, frame_size=(3440, 1440))
    assert not content_box_applies((1920, 1080), s, frame_size=(3440, 1440))


def test_resolution_rule_without_frame_size():
    s = windowed()
    assert content_box_applies((3440, 1440), s)          # 자르면 1920x1080 = 해상도 → 모니터 전체 캡처
    assert not content_box_applies((1920, 1080), s)      # 자르면 1070x810 → 이미 잘린 이미지


def test_no_box_or_unknown_resolution_keeps_old_behavior():
    s = load_settings()
    s = core.apply_updates(s, {"vision": {"content_box": None}})
    assert content_box_applies((1920, 1080), s)
    s = core.apply_updates(windowed(), {"vision": {"resolution": "auto"}})
    assert content_box_applies((1920, 1080), s)


def test_content_without_box_restores_cfg_and_uses_full_frame():
    class Rec:
        def __init__(self, cfg):
            self.cfg = cfg

        def content_for(self, image, content):
            return self.cfg.content_px(image.shape[1], image.shape[0])

    s = windowed()
    rec = Rec(s.vision)
    img = np.zeros((1080, 1920, 3), np.uint8)
    assert content_without_box(rec, img, s) == (0, 0, 1920, 1080)
    assert rec.cfg is s.vision
