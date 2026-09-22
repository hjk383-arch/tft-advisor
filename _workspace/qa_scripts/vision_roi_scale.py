"""QA(vision): ROI 정규화 + content-box(scale/offset) 검증 — 합성 1920x1080/레터박스/창모드 프레임.

    .venv/bin/python _workspace/qa_scripts/vision_roi_scale.py

fixture(방송 크롭 ≈2000x1125)를 다음 프레임으로 바꿔 인식 결과가 원본과 같은지 본다.
  native     : 원본 그대로
  1080p      : 1920x1080으로 리사이즈(실제 1080p 전체 화면 가정)
  1440p      : 2560x1440 업스케일
  lb_16x10   : 1920x1200 캔버스에 1920x1080 게임 화면(y=60) + content=(0,60,1920,1080)
  window     : 2560x1440 데스크톱에 1600x900 창(200,150) + content
  window_nocb: 같은 창, content 없이(보정 없으면 실패해야 정상 — 자동 탐지 부재 확인)
또 기하 검사: 모든 ROI에 대해 content 사상 픽셀 == (1080p 사상 픽셀 × scale + offset) ± 1px.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tft_advisor.vision.capture import load_image  # noqa: E402
from tft_advisor.vision.recognizer import Recognizer  # noqa: E402
from tft_advisor.vision.regions import SET18_16X9, FrameMapper  # noqa: E402

SCREENS = ROOT / "tests" / "fixtures" / "screens"
FIELDS = ("screen_mode", "stage", "level", "xp", "gold", "streak", "hp", "shop_odds", "shop", "augment_offer", "items")


def norm(st, f):
    v = getattr(st, f)
    if v is None:
        return None
    if f == "shop":
        return tuple((s.kind.value, s.id) for s in v)
    if f == "augment_offer":
        return tuple(a.id for a in v)
    if f == "items":
        return tuple(sorted(r.id for g in (v.components, v.completed, v.emblems, v.others) for r in g))
    return getattr(v, "value", v if not isinstance(v, list) else tuple(v))


def variants(img):
    out = {"native": (img, None)}
    out["1080p"] = (cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_AREA), None)
    out["1440p"] = (cv2.resize(img, (2560, 1440), interpolation=cv2.INTER_CUBIC), None)
    c = np.zeros((1200, 1920, 3), np.uint8)
    c[60:1140] = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_AREA)
    out["lb_16x10"] = (c, (0, 60, 1920, 1080))
    d = np.full((1440, 2560, 3), 90, np.uint8)
    d[150:1050, 200:1800] = cv2.resize(img, (1600, 900), interpolation=cv2.INTER_AREA)
    out["window"] = (d, (200, 150, 1600, 900))
    out["window_nocb"] = (d, None)
    return out


def geometry() -> list[str]:
    bad = []
    base = FrameMapper(1920, 1080)
    for (W, H, cb) in ((1920, 1200, (0, 60, 1920, 1080)), (2560, 1440, (200, 150, 1600, 900)), (3840, 2160, None)):
        m = FrameMapper(W, H, cb)
        left, top, w, h = m.box
        sx, sy = w / 1920, h / 1080
        for name, r in SET18_16X9.all_rois().items():
            bx = base.to_px(r)
            exp = (left + bx[0] * sx, top + bx[1] * sy, left + bx[2] * sx, top + bx[3] * sy)
            got = m.to_px(r)
            if any(abs(g - e) > max(1.0, sx) for g, e in zip(got, exp)):
                bad.append(f"{W}x{H} {cb} {name}: got {got} exp {tuple(round(e) for e in exp)}")
            # 역사상 확인
            rx, ry = m.to_rel(got[0], got[1])
            if abs(rx - r.x1) > 1.5 / w or abs(ry - r.y1) > 1.5 / h:
                bad.append(f"to_rel mismatch {name}")
    return bad


def main() -> int:
    g = geometry()
    print("geometry mismatches:", g or "none")
    rec = Recognizer()
    total_diff = {}
    for name in ("라운드 3-3", "라운드 3-5", "라운드 2-1 증강선택", "라운드 1-4"):
        img = load_image(SCREENS / f"{name}.png")
        print(f"\n== {name} {img.shape[1]}x{img.shape[0]}")
        base = None
        for vname, (frame, cb) in variants(img).items():
            st = rec.recognize(frame, content=cb)
            vals = {f: norm(st, f) for f in FIELDS}
            if base is None:
                base = vals
                print(f"  native: " + ", ".join(f"{f}={'-' if v is None else 'ok'}" for f, v in vals.items()))
                continue
            diffs = [f"{f}: {base[f]!s:.40} -> {vals[f]!s:.40}" for f in FIELDS if vals[f] != base[f]]
            total_diff[(name, vname)] = diffs
            print(f"  {vname:12} {'SAME' if not diffs else 'DIFF ' + str(len(diffs))}")
            for d in diffs:
                print("      ", d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
