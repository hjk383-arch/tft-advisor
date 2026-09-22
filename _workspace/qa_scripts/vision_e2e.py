"""QA(vision): fixture 스크린샷 → recognize_file → GameState 재검증 → create_advisor("mock").advise → Recommendation 검증.

    .venv/bin/python _workspace/qa_scripts/vision_e2e.py [--json OUT]

- Jev는 mock만(라이브 호출 없음). TYPESAFE_API_KEY는 무시한다.
- 필드별: 값, confidence, advisor 신뢰 여부(state.is_reliable(f, state_min_confidence)).
- 경계면 체크: vision이 채운 값 필드는 confidence 키가 있어야 하고, 값이 None인 필드는 confidence 키가 없어야 한다.
  서브 모델(shop 칸, augment, item) confidence가 필드 confidence 이상인지(필드가 신뢰되면 칸도 신뢰되는지).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.pop("TYPESAFE_API_KEY", None)

from tft_advisor.advisor.engine import create_advisor  # noqa: E402
from tft_advisor.contracts import GAME_STATE_FIELDS, GameState, Recommendation, ShopSlotKind  # noqa: E402
from tft_advisor.vision.recognizer import Recognizer, recognize_file  # noqa: E402

SCREENS = ROOT / "tests" / "fixtures" / "screens"


def main() -> int:
    out_json = Path(sys.argv[sys.argv.index("--json") + 1]) if "--json" in sys.argv else None
    rec = Recognizer()
    print(f"OCR={rec.ocr.name} item_templates={len(rec.item_matcher)}")
    adv = create_advisor("mock")
    min_conf = adv.settings.vision.state_min_confidence
    problems: list[str] = []
    rows = []
    # 워밍업 1회(모델 로드 시간 분리)
    first = sorted(SCREENS.glob("*.png"))[0]
    t = time.perf_counter(); recognize_file(first, rec); warm = time.perf_counter() - t
    print(f"warm-up recognize: {warm:.2f}s")
    for png in sorted(SCREENS.glob("*.png")):
        adv.reset()
        t0 = time.perf_counter()
        st = recognize_file(png, rec)
        t_vis = time.perf_counter() - t0
        # 1) 계약 재검증(우회 없음 확인): dump → validate 왕복
        GameState.model_validate(st.model_dump())
        # 2) confidence 키 규칙
        for f in GAME_STATE_FIELDS:
            v = getattr(st, f)
            if f == "screen_mode":
                continue
            if v is None and f in st.confidence:
                problems.append(f"{png.stem}: {f}=None 인데 confidence 키 존재")
            if v is not None and f not in st.confidence:
                problems.append(f"{png.stem}: {f} 값 있는데 confidence 키 없음(→ confidence_of=1.0)")
        # 3) 서브 모델 confidence ≥ 필드 confidence (필드가 신뢰되면 칸도 신뢰)
        if st.shop is not None and st.is_reliable("shop", min_conf):
            for i, s in enumerate(st.shop):
                if s.kind in (ShopSlotKind.CHAMPION, ShopSlotKind.SPECIAL) and s.confidence < min_conf:
                    problems.append(f"{png.stem}: shop 필드 신뢰인데 칸 {i} conf {s.confidence} < {min_conf}")
        if st.augment_offer and st.is_reliable("augment_offer", min_conf):
            if any(a.confidence < min_conf for a in st.augment_offer):
                problems.append(f"{png.stem}: augment_offer 필드 신뢰인데 일부 증강 conf 미달 → advisor 인덱스 이동 위험")
        t1 = time.perf_counter()
        r = adv.advise(st)
        t_adv = time.perf_counter() - t1
        if r is not None:
            Recommendation.model_validate(r.model_dump())
        fields = {}
        for f in GAME_STATE_FIELDS:
            v = getattr(st, f)
            if v is None:
                continue
            fields[f] = {"conf": st.confidence.get(f), "reliable": st.is_reliable(f, min_conf)}
        row = {
            "screen": png.stem, "t_vision_s": round(t_vis, 2), "t_advisor_s": round(t_adv, 3),
            "screen_mode": st.screen_mode.value, "mode_conf": st.confidence.get("screen_mode"),
            "fields": fields,
            "shop": None if st.shop is None else [(s.kind.value, s.id, s.confidence) for s in st.shop],
            "items": None if st.items is None else st.items.model_dump(include={"components", "completed", "emblems", "others"}),
            "rec": None if r is None else {
                
                "jev_used": r.jev_used,
                "fallback_reason": getattr(r.fallback_reason, "value", r.fallback_reason),
                "n_target_comps": len(r.target_comps),
                "shop_buys": [a.slot for a in r.shop if a.buy],
                "augment": None if r.augment is None else r.augment.model_dump(mode="json"),
            },
        }
        rows.append(row)
        rel = [f for f, d in fields.items() if d["reliable"]]
        unrel = [f"{f}({d['conf']})" for f, d in fields.items() if not d["reliable"]]
        print(f"== {png.stem}: vision {t_vis:.2f}s advisor {t_adv*1000:.0f}ms mode={st.screen_mode.value}({row['mode_conf']})")
        print(f"   reliable: {rel}")
        print(f"   excluded(<{min_conf}): {unrel}")
        print(f"   rec: {row['rec']}")
    adv.close()
    print("\nboundary problems:", problems or "none")
    if out_json:
        out_json.write_text(json.dumps({"rows": rows, "problems": problems, "warmup_s": warm}, ensure_ascii=False,
                                       indent=1, default=str), encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
