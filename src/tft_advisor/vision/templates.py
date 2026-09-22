"""템플릿 준비 도구 (오프라인, 실시간 루프 밖).

    python -m tft_advisor.vision.templates fetch-items [--categories component,completed,...]
        CommunityDragon 아이템 아이콘 → data/templates/{set}/items/{apiName}.png (공개 CDN, 게임 클라이언트 무관)
    python -m tft_advisor.vision.templates harvest-items SCREENSHOT LABEL.json
        원본 캡처의 아이템 벤치 칸을 잘라 실화면 템플릿 data/templates/{set}/items_screen/{apiName}.png로 저장.
        LABEL.json의 "item_bench": [이름|apiName|null, ...] 10칸(위→아래). 이름은 게임에 보이는 한국어 그대로 쓴다.
        이름은 정적 데이터로 apiName(묶음 대표 ID)으로 바꾼다. 바꿀 수 없는 이름은 저장하지 않고 오류로 보고한다
        (라벨 글자를 ID로 쓰지 않는다, QA04-V1). 실화면 템플릿은 CDragon 아이콘을 대체하지 않고 ID별로 추가된다.
    python -m tft_advisor.vision.templates harvest-digits SCREENSHOT LABEL.json
        골드/XP 등 숫자 ROI에서 글리프 저장 → data/templates/{set}/digits/
    python -m tft_advisor.vision.templates debug-rois SCREENSHOT [OUT.png]
        ROI 박스를 그린 PNG
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from ..static_data import PROJECT_ROOT, preference_key, load_static

CDRAGON_GAME = "https://raw.communitydragon.org/latest/game/"
DEFAULT_ITEM_CATEGORIES = ("component", "completed", "emblem", "artifact", "radiant", "consumable")


def template_dir(set_number: int, kind: str) -> Path:
    return PROJECT_ROOT / "data" / "templates" / str(set_number) / kind


def cdragon_png_url(tex_path: str) -> str:
    p = tex_path.lower()
    if p.endswith((".tex", ".dds")):
        p = p[:-4] + ".png"
    return CDRAGON_GAME + p


def fetch_items(set_number: int, categories: tuple[str, ...], overwrite: bool = False) -> tuple[int, list[str]]:
    static = load_static(set_number)
    out = template_dir(set_number, "items")
    out.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, []
    # 같은 아이콘을 쓰는 ID(DA_* 와 레거시 TFT_Item_*)는 하나만 둔다: static_data 우선순위(DA_·set_native)로.
    by_icon: dict[str, dict] = {}
    for rec in static.tables["items"]:
        if rec.get("category") not in categories or not rec.get("icon"):
            continue
        key = rec["icon"].lower()
        if key not in by_icon or preference_key(rec) < preference_key(by_icon[key]):
            by_icon[key] = rec
    keep = {r["apiName"] for r in by_icon.values()}
    for stale in out.glob("*.png"):
        if stale.stem not in keep:
            stale.unlink()
    for rec in by_icon.values():
        dest = out / f"{rec['apiName']}.png"
        if dest.exists() and not overwrite:
            ok += 1
            continue
        url = cdragon_png_url(rec["icon"])
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tft-advisor-research/0.1 (personal use)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                dest.write_bytes(r.read())
            ok += 1
        except Exception as e:  # noqa: BLE001 — 개별 실패는 목록으로 보고
            failed.append(f"{rec['apiName']}: {e}")
    return ok, failed


def harvest_items(image, labels: list[str | None], static, profile, out_dir: Path,
                  content: tuple[int, int, int, int] | None = None) -> tuple[list[str], list[str]]:
    """아이템 벤치 칸 → `out_dir/{대표 apiName}.png`. 반환 (저장한 ID들, 오류 메시지들).

    라벨은 표시 이름(한국어/영어, 마크업·공백 무시) 또는 apiName. 해석 불가·빈 칸 크롭은 저장하지 않는다.

    **저장 형태**: 칸 크롭을 그대로 저장하지 않고 `IconMatcher`가 비교하는 기하와 같게 맞춘다
    (SEARCH_SIZE로 키운 뒤 가운데 TEMPLATE_SIZE만 잘라 저장). `IconMatcher.match`는 크롭을 SEARCH_SIZE(38)로
    키우고 TEMPLATE_SIZE(32) 템플릿을 ±3px 움직이며 맞춰 보므로, 칸 전체(테두리 포함)를 32로 줄여 저장하면
    같은 아이콘인데도 점수가 0.5대로 떨어진다(실제 캡처에서 확인). 가운데만 잘라 저장하면 자기 자신과 1.0이 된다.
    """
    import cv2

    from .icons import SEARCH_SIZE, TEMPLATE_SIZE, slot_is_empty
    from .item_ids import ItemCatalog
    from .regions import FrameMapper

    off = (SEARCH_SIZE - TEMPLATE_SIZE) // 2

    cat = ItemCatalog(static)
    m = FrameMapper.for_image(image, content)
    saved: list[str] = []
    errors: list[str] = []
    if len(labels) > len(profile.item_slots):
        errors.append(f"item_bench가 {len(labels)}칸이다(최대 {len(profile.item_slots)}). 넘는 칸은 무시한다")
    for j, (r, label) in enumerate(zip(profile.item_slots, labels)):
        if not label:
            continue
        api = cat.resolve(label)
        if api is None:
            errors.append(f"{j}번 칸 {label!r}: 정적 데이터에서 아이템을 찾지 못했다(게임 표시 이름 그대로인지 확인)")
            continue
        crop = m.crop(image, r)
        if slot_is_empty(crop):
            errors.append(f"{j}번 칸 {label!r}: 화면의 칸이 비어 있다(라벨 순서 확인)")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        big = cv2.resize(crop, (SEARCH_SIZE, SEARCH_SIZE), interpolation=cv2.INTER_AREA)
        tpl = big[off:off + TEMPLATE_SIZE, off:off + TEMPLATE_SIZE]
        if not cv2.imwrite(str(out_dir / f"{api}.png"), tpl):
            errors.append(f"{j}번 칸 {label!r}: 저장 실패")
            continue
        saved.append(api)
    return saved, errors


def _main(argv: list[str] | None = None) -> int:
    import cv2

    from .capture import load_image
    from .ocr import DigitTemplateReader
    from .regions import FrameMapper, draw_rois, profile_for_frame

    ap = argparse.ArgumentParser(prog="tft_advisor.vision.templates")
    ap.add_argument("--set", type=int, default=18)
    ap.add_argument("--profile", default="auto",
                    help="ROI 프로파일. auto(기본)면 스크린샷 크기에서 비율을 재서 고른다")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch-items")
    f.add_argument("--categories", default=",".join(DEFAULT_ITEM_CATEGORIES))
    f.add_argument("--overwrite", action="store_true")
    for name in ("harvest-items", "harvest-digits"):
        h = sub.add_parser(name)
        h.add_argument("screenshot", type=Path)
        h.add_argument("label", type=Path)
    d = sub.add_parser("debug-rois")
    d.add_argument("screenshot", type=Path)
    d.add_argument("out", type=Path, nargs="?")
    args = ap.parse_args(argv)

    if args.cmd == "fetch-items":
        ok, failed = fetch_items(args.set, tuple(args.categories.split(",")), args.overwrite)
        print(f"ok {ok}, failed {len(failed)}")
        for line in failed:
            print("  ", line)
        return 0 if not failed else 1

    img = load_image(args.screenshot)
    m = FrameMapper.for_image(img)
    profile = profile_for_frame(img.shape[1], img.shape[0], args.profile)
    print(f"프로파일: {profile.name} ({img.shape[1]}x{img.shape[0]})")
    if args.cmd == "debug-rois":
        out = args.out or args.screenshot.with_suffix(".rois.png")
        cv2.imwrite(str(out), draw_rois(img, profile, m))
        print(out)
        return 0

    label = json.loads(args.label.read_text(encoding="utf-8"))
    if args.cmd == "harvest-items":
        out = template_dir(args.set, "items_screen")
        bench = label.get("item_bench") or label.get("note_item_bench") or []
        if not isinstance(bench, list):
            print("item_bench가 목록이 아니다", file=sys.stderr)
            return 2
        saved, errors = harvest_items(img, bench, load_static(args.set), profile, out)
        print(f"{out}: 저장 {len(saved)} {saved}")
        for e in errors:
            print("  오류:", e, file=sys.stderr)
        return 1 if errors else 0
    if args.cmd == "harvest-digits":
        out = template_dir(args.set, "digits")
        done = []
        for field, roi in (("gold", profile.gold), ("stage", profile.stage)):
            if field in label and DigitTemplateReader.harvest(m.crop(img, roi), str(label[field]), out):
                done.append(field)
        print(f"{out}: {done}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
