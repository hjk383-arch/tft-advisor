"""fixture 정확도 측정: 인식 결과 vs `tests/fixtures/screens/*.expected.json` (필드별).

    python -m tft_advisor.vision.evaluate [DIR] [--json OUT]

비교 규칙
- 정답 파일에 있는 필드만 비교(`ExpectedScreen.fields`). `note_level` 등 추정 라벨 필드는 "(추정)"으로 표시한다.
- 인식이 None이면 "none"(미인식, 틀림과 구분). 값이 있는데 다르면 "wrong".
- shop은 칸 단위(kind+id)로도 센다.
- board_slots / bench_slots(보드·벤치 유닛 라벨)는 **자리 단위**로 센다: 자리(occupancy) · 성급 · 장착 아이템을 따로.
  챔피언 이름(19 보고, `vision.units`)은 **확인된 라벨(`name`)이 있는 자리만** 센다: ok · wrong(다른 이름) · none(모름).
  추정 라벨(`name_unconfirmed`) 자리에 이름을 냈으면 `unverified`로 따로 센다(맞는지 알 수 없다).
- 여러 모니터를 이어 붙인 캡처(Windows Win+PrtSc, 예: 4480x1440)는 인식기가 게임 모니터를 자동으로 고른다
  (`Recognizer.content_for` → `regions.screen_candidates` + 스테이지 OCR). 크롭 사본을 만들 필요가 없다.
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
    board: dict | None = None
    """보드·벤치 라벨이 있을 때만: {occupancy: {ok, expected, got}, star: {ok, total}, items: {ok, total}, …}"""


def _slot_key(slot: dict, on_bench: bool) -> object:
    return slot["slot"] if on_bench else slot["hex"]


def compare_board(expected: list[dict], got: list, on_bench: bool) -> dict:
    """라벨 자리 목록 vs 판독 자리 목록 → {occupancy, star, items}. 자리를 모르는 라벨(None)은 개수만 센다."""
    exp_by = {_slot_key(e, on_bench): e for e in expected if _slot_key(e, on_bench) is not None}
    got_by = {(u.bench_slot if on_bench else u.hex): u for u in got
              if (u.bench_slot if on_bench else u.hex) is not None}
    same = set(exp_by) & set(got_by)
    star_total = star_ok = item_total = item_ok = 0
    names = {"ok": 0, "wrong": 0, "none": 0, "total": 0, "unverified": 0}
    for k in sorted(same, key=str):
        e, g = exp_by[k], got_by[k]
        got_id = getattr(g, "unit_id", None)
        if e.get("unit_id"):
            names["total"] += 1
            names["ok" if got_id == e["unit_id"] else ("none" if got_id is None else "wrong")] += 1
        elif got_id is not None:
            names["unverified"] += 1
        if e.get("star") is not None:
            star_total += 1
            star_ok += int(e["star"] == g.star)
        if e.get("items") is not None:
            item_total += 1
            item_ok += int(sorted(e["items"]) == sorted(g.items))
    return {
        "expected": len(expected), "got": len(got),
        "placed_ok": len(same), "missing": sorted(map(str, set(exp_by) - set(got_by))),
        "extra": sorted(map(str, set(got_by) - set(exp_by))),
        "star": {"ok": star_ok, "total": star_total},
        "items": {"ok": item_ok, "total": item_total},
        "names": names,
    }


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
        read = rec.last_board_read
        if read is not None and ("board_slots" in exp.extras or "bench_slots" in exp.extras):
            r.board = {
                "board": compare_board(exp.extras.get("board_slots") or [], list(read.board), False),
                "bench": compare_board(exp.extras.get("bench_slots") or [], list(read.bench), True),
                "unresolved_items": read.unresolved_items,
            }
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
    boards = [r.board for r in results if r.board]
    if boards:
        for side in ("board", "bench"):
            agg[f"{side}_slot"] = {
                "ok": sum(b[side]["placed_ok"] for b in boards),
                "wrong": sum(len(b[side]["extra"]) for b in boards),
                "none": sum(len(b[side]["missing"]) for b in boards),
                "total": sum(b[side]["expected"] for b in boards)}
            agg[f"{side}_name"] = {k: sum(b[side]["names"][k] for b in boards)
                                   for k in ("ok", "wrong", "none", "total")}
            for what in ("star", "items"):
                agg[f"{side}_{what}"] = {
                    "ok": sum(b[side][what]["ok"] for b in boards), "wrong": 0,
                    "none": sum(b[side][what]["total"] - b[side][what]["ok"] for b in boards),
                    "total": sum(b[side][what]["total"] for b in boards)}
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
        if r.board:
            for side in ("board", "bench"):
                b = r.board[side]
                print(f"  ---- {side:9} 자리 {b['placed_ok']}/{b['expected']} (판독 {b['got']}) "
                      f"성급 {b['star']['ok']}/{b['star']['total']} 아이템 {b['items']['ok']}/{b['items']['total']}"
                      + f" 이름 {b['names']['ok']}/{b['names']['total']}(틀림 {b['names']['wrong']}, 미확인 칸에 낸 이름 {b['names']['unverified']})"
                      + (f" 놓침={b['missing']}" if b["missing"] else "")
                      + (f" 잘못={b['extra']}" if b["extra"] else ""))
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
