"""fixture 정확도 측정: 인식 결과 vs `tests/fixtures/screens/*.expected.json` (필드별).

    python -m tft_advisor.vision.evaluate [DIR] [--json OUT]

비교 규칙
- 정답 파일에 있는 필드만 비교(`ExpectedScreen.fields`). `note_level` 등 추정 라벨 필드는 "(추정)"으로 표시한다.
- 인식이 None이면 "none"(미인식, 틀림과 구분). 값이 있는데 다르면 "wrong".
- shop은 칸 단위(kind+id)로도 센다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts import GameState
from ..fixtures import load_expected
from ..static_data import PROJECT_ROOT
from .capture import load_image
from .recognizer import GROUPS, Recognizer

SCREENS_DIR = PROJECT_ROOT / "tests" / "fixtures" / "screens"
COMPARED = ("screen_mode", "stage", "level", "xp", "gold", "streak", "hp", "shop_odds", "shop",
            "augment_offer", "augments_owned", "items", "active_traits")


def _norm(field_name: str, v: object) -> object:
    if v is None:
        return None
    if field_name == "shop":
        return [(s.kind.value, s.id) for s in v]  # type: ignore[attr-defined]
    if field_name in ("augment_offer", "augments_owned"):
        return [a.id for a in v]  # type: ignore[attr-defined]
    if field_name == "items":
        return sorted(v.all_ids())  # type: ignore[attr-defined]  # 대표 ID 다중집합(칸 순서·신뢰도 무시)
    if field_name == "xp":
        return tuple(v)  # type: ignore[arg-type]
    if hasattr(v, "value"):
        return v.value  # type: ignore[attr-defined]
    return v


@dataclass
class ScreenResult:
    name: str
    fields: dict[str, dict] = field(default_factory=dict)   # field → {expected, got, status, conf}
    shop_slots: list[dict] = field(default_factory=list)


def compare(expected: GameState, fields: frozenset[str], got: GameState, estimated: set[str]) -> dict[str, dict]:
    out = {}
    for f in COMPARED:
        if f not in fields:
            continue
        e, g = _norm(f, getattr(expected, f)), _norm(f, getattr(got, f))
        status = "ok" if e == g else ("none" if g is None else "wrong")
        out[f] = {"expected": e, "got": g, "status": status, "conf": got.confidence.get(f),
                  "estimated_label": f in estimated}
    return out


def evaluate_dir(screens_dir: Path = SCREENS_DIR, recognizer: Recognizer | None = None) -> list[ScreenResult]:
    rec = recognizer or Recognizer()
    results = []
    for exp_path in sorted(screens_dir.glob("*.expected.json")):
        raw = json.loads(exp_path.read_text(encoding="utf-8"))
        estimated = {k.removeprefix("note_") for k in raw if k.startswith("note_")}
        exp = load_expected(exp_path, rec.static)
        img_path = Path(exp.state.source_image or "")
        got = rec.recognize(load_image(img_path), source_image=str(img_path), groups=GROUPS)   # 진단용: traits 포함 전부
        r = ScreenResult(name=img_path.stem, fields=compare(exp.state, exp.fields, got, estimated))
        if exp.state.shop is not None:
            gs = got.shop or [None] * 5
            for i, (es, g) in enumerate(zip(exp.state.shop, gs)):
                r.shop_slots.append({
                    "slot": i, "expected": (es.kind.value, es.id),
                    "got": None if g is None else (g.kind.value, g.id),
                    "ok": g is not None and (g.kind, g.id) == (es.kind, es.id),
                })
        results.append(r)
    return results


def summarize(results: list[ScreenResult]) -> dict[str, dict[str, int]]:
    """필드별 {ok, wrong, none, total} (추정 라벨 제외)."""
    agg: dict[str, dict[str, int]] = {}
    for r in results:
        for f, d in r.fields.items():
            if d["estimated_label"]:
                continue
            a = agg.setdefault(f, {"ok": 0, "wrong": 0, "none": 0, "total": 0})
            a[d["status"]] += 1
            a["total"] += 1
    slots = [s for r in results for s in r.shop_slots]
    if slots:
        agg["shop_slot"] = {"ok": sum(s["ok"] for s in slots),
                            "wrong": sum(1 for s in slots if not s["ok"] and s["got"] and s["got"][0] != "unknown"),
                            "none": sum(1 for s in slots if not s["ok"] and (not s["got"] or s["got"][0] == "unknown")),
                            "total": len(slots)}
    return agg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tft_advisor.vision.evaluate")
    ap.add_argument("dir", type=Path, nargs="?", default=SCREENS_DIR)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    rec = Recognizer()
    print(f"OCR: {rec.ocr.name}, item templates: {len(rec.item_matcher)}")
    results = evaluate_dir(args.dir, rec)
    for r in results:
        print(f"== {r.name}")
        for f, d in r.fields.items():
            mark = {"ok": "OK  ", "wrong": "FAIL", "none": "NONE"}[d["status"]]
            est = " (추정 라벨)" if d["estimated_label"] else ""
            print(f"  {mark} {f:14} exp={d['expected']!s:40.40} got={d['got']!s:40.40} conf={d['conf']}{est}")
    agg = summarize(results)
    print("\n필드별 (ok/total, wrong, none):")
    for f, a in agg.items():
        print(f"  {f:14} {a['ok']}/{a['total']}  wrong={a['wrong']} none={a['none']}")
    if args.json:
        args.json.write_text(json.dumps({"results": [r.__dict__ for r in results], "summary": agg},
                                        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
