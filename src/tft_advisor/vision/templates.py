"""템플릿 준비 도구 (오프라인, 실시간 루프 밖).

    python -m tft_advisor.vision.templates fetch-items [--categories component,completed,...]
        CommunityDragon 아이템 아이콘 → data/templates/{set}/items/{apiName}.png (공개 CDN, 게임 클라이언트 무관)
    python -m tft_advisor.vision.templates fetch-augments [--no-alt] [--delay 0.5]
        CommunityDragon 증강 아이콘 → data/templates/{set}/augments/{apiName}.png (보유 증강 줄 판독용)
        + CDragon에 없는 세트 증강(`missing-*` 자리표시)은 tactics.tools 아이콘 → augments_alt/{apiName}.png + sources.json
        (요청 간격 --delay초, 이미 받은 파일은 다시 받지 않는다. 전부 gitignore 로컬 캐시)
    python -m tft_advisor.vision.templates harvest-items SCREENSHOT LABEL.json
        원본 캡처의 아이템 벤치 칸을 잘라 실화면 템플릿 data/templates/{set}/items_screen/{apiName}.png로 저장.
        LABEL.json의 "item_bench": [이름|apiName|null, ...] 10칸(위→아래). 이름은 게임에 보이는 한국어 그대로 쓴다.
        이름은 정적 데이터로 apiName(묶음 대표 ID)으로 바꾼다. 바꿀 수 없는 이름은 저장하지 않고 오류로 보고한다
        (라벨 글자를 ID로 쓰지 않는다, QA04-V1). 실화면 템플릿은 CDragon 아이콘을 대체하지 않고 ID별로 추가된다.
    python -m tft_advisor.vision.templates harvest-augments SCREENSHOT LABEL.json
        준비 화면 보유 증강 줄 칸 → data/templates/{set}/augments_screen/{apiName}.png. LABEL.json "augments_owned"(왼쪽부터 이름).
        CDragon 아이콘이 없거나 공유돼 식별할 수 없는 증강을 사용자 확인 라벨로 보충한다.
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
from collections.abc import Callable
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


USER_AGENT = "tft-advisor-research/0.1 (personal use)"
FETCH_DELAY_S = 0.5
"""내려받기 요청 사이 간격(초). 공개 CDN/사이트에 부담을 주지 않도록 한 번에 하나씩, 이 간격으로만 요청한다."""
CDRAGON_HEXCORE = "assets/maps/tft/icons/augments/hexcore/"
TACTICS_AUGMENT_URL = "https://ap.tft.tools/img/augments/{api}{tier}.png"
"""tactics.tools 증강 아이콘(사이트 번들 `ap.tft.tools/static/s18/data.js`의 ID + 등급 번호). webp로 온다(OpenCV로 읽힌다)."""
PLACEHOLDER_SIMILARITY = 0.9
"""대체 출처 아이콘이 CDragon `missing-t*` 자리표시와 글리프 정규화 후 이만큼 닮으면 자리표시로 보고 버린다."""


def _download(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_icons(records, out: Path, overwrite: bool, delay: float = FETCH_DELAY_S) -> tuple[int, list[str]]:
    """레코드들의 `icon` → `out/{apiName}.png`. 같은 아이콘을 쓰는 ID는 우선순위(`preference_key`)가 가장 높은 하나만 둔다."""
    import time

    out.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, []
    by_icon: dict[str, dict] = {}
    for rec in records:
        if not rec.get("icon"):
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
            dest.write_bytes(_download(url, timeout=15))
            ok += 1
        except Exception as e:  # noqa: BLE001 — 개별 실패는 목록으로 보고
            failed.append(f"{rec['apiName']}: {e}")
        if delay:
            time.sleep(delay)
    return ok, failed


def fetch_items(set_number: int, categories: tuple[str, ...], overwrite: bool = False) -> tuple[int, list[str]]:
    static = load_static(set_number)
    # 같은 아이콘을 쓰는 ID(DA_* 와 레거시 TFT_Item_*)는 하나만 둔다: static_data 우선순위(DA_·set_native)로.
    recs = [r for r in static.tables["items"] if r.get("category") in categories]
    return _fetch_icons(recs, template_dir(set_number, "items"), overwrite)


def _augment_cdragon_icon(icon: str) -> str:
    """정적 데이터 증강 `icon` → CDragon 게임 경로. OP.GG 전체 URL(4개)은 같은 파일명의 CDragon hexcore 아이콘으로 바꾼다
    (예: `https://c-tft-api.op.gg/.../calculatedloss2.png` → `assets/.../hexcore/calculatedloss2.png`). 예전에는 전체 URL에
    CDragon 주소를 덧붙여 404가 났다(07 보고서의 "404 4개"). OP.GG 이미지 서버는 직접 받으면 403이다."""
    if icon.lower().startswith(("http://", "https://")):
        return CDRAGON_HEXCORE + icon.rsplit("/", 1)[-1].lower()
    return icon


def fetch_augments(set_number: int, overwrite: bool = False, alt: bool = True,
                   delay: float = FETCH_DELAY_S) -> tuple[int, list[str]]:
    """증강 아이콘 → data/templates/{set}/augments/{apiName}.png (CDragon hexcore 글리프)
    + (alt) data/templates/{set}/augments_alt/{apiName}.png (CDragon에 없는 세트 증강의 대체 출처 아이콘).

    보드 왼쪽 위 보유 증강 줄(`Profile.augments_owned`)이 이 글리프를 그대로(작게) 그린다(1080p 실캡처로 확인).
    여러 증강이 한 아이콘을 공유하면 파일은 하나지만, 인식기가 정적 데이터로 공유 여부를 다시 확인해 모호하면 None으로 둔다.
    `missing-*` 자리표시 아이콘은 받지 않는다(여러 증강이 공유해 식별에 쓸 수 없다) — 그런 세트 증강은 `fetch_augment_alts`가
    대체 출처에서 채운다.
    """
    static = load_static(set_number)
    recs = [dict(r, icon=_augment_cdragon_icon(r["icon"])) for r in static.tables["augments"]
            if r.get("icon") and "/missing" not in r["icon"].lower()]
    ok, failed = _fetch_icons(recs, template_dir(set_number, "augments"), overwrite, delay=delay)
    if alt:
        ok2, failed2, manifest = fetch_augment_alts(set_number, overwrite=overwrite, delay=delay)
        alt_ok = set(manifest.get("entries", {}))
        # CDragon에서 못 받았어도 대체 출처로 채운 ID는 실패로 치지 않는다(예: DA_ForgeAFriend)
        failed = [f for f in failed if f.split(":", 1)[0] not in alt_ok] + failed2
        ok += ok2
    return ok, failed


def _placeholder_glyphs(delay: float) -> list:
    """CDragon `missing-t1..3` 자리표시 아이콘(글리프 정규화). 받지 못하면 빈 목록(자리표시 검사 생략)."""
    import time

    import cv2
    import numpy as np

    from .icons import glyph_normalize

    out = []
    for t in (1, 2, 3):
        try:
            raw = _download(CDRAGON_GAME + CDRAGON_HEXCORE + f"missing-t{t}.png")
            g = glyph_normalize(cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED))
            if g is not None:
                out.append(g)
        except Exception:  # noqa: BLE001
            pass
        if delay:
            time.sleep(delay)
    return out


def fetch_augment_alts(set_number: int, overwrite: bool = False, delay: float = FETCH_DELAY_S,
                       download: Callable[[str], bytes] | None = None) -> tuple[int, list[str], dict]:
    """CDragon 아이콘이 없는 **세트 증강**(`missing-*` 자리표시 또는 받지 못한 아이콘) → 대체 출처 아이콘.

    출처: tactics.tools(`TACTICS_AUGMENT_URL`). 저장: `augments_alt/{apiName}.png`(글리프 외곽으로 정규화한 64px BGR —
    인식기는 이 디렉터리를 글리프 정규화 경로로 비교한다, `icons.glyph_normalize`)와 `augments_alt/sources.json`
    (ID별 출처 URL, 같은 그림을 쓰는 ID 묶음 `group`). 같은 그림(바이트 동일)을 받은 ID들은 파일 하나(우선순위가 가장 높은 ID)만
    두고 나머지는 `group`으로 기록한다 → 인식기가 이름이 다른 공유 그림을 모호로 처리한다. 자리표시 그림은 버린다.
    반환 (저장한 ID 수, 실패 메시지, sources.json 내용).
    """
    import hashlib
    import time

    import cv2
    import numpy as np

    from .capture import save_image
    from .icons import glyph_normalize

    fetch = download or _download
    static = load_static(set_number)
    have = {p.stem for p in template_dir(set_number, "augments").glob("*.png")}
    by_icon: dict[str, list[dict]] = {}
    for r in static.tables["augments"]:
        by_icon.setdefault(_augment_cdragon_icon(r.get("icon") or "").lower(), []).append(r)
    covered = {r["apiName"] for icon, rs in by_icon.items() if "/missing" not in icon
               for r in rs if any(x["apiName"] in have for x in rs)}
    want = sorted((r for r in static.tables["augments"] if r.get("set_native") and r["apiName"] not in covered),
                  key=lambda r: (preference_key(r), r["apiName"]))
    out = template_dir(set_number, "augments_alt")
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "sources.json"
    old = {}
    if manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8")).get("entries", {})
        except (OSError, ValueError):
            old = {}
    placeholders = _placeholder_glyphs(delay) if download is None else []
    entries: dict[str, dict] = {}
    rep_by_hash: dict[str, str] = {}
    failed: list[str] = []
    for r in want:
        api = r["apiName"]
        url = TACTICS_AUGMENT_URL.format(api=api, tier=r.get("tier") or 2)
        prev = old.get(api)
        dest = out / f"{api}.png"
        if prev and not overwrite and prev.get("sha1") and (dest.exists() or prev["sha1"] in rep_by_hash):
            h = prev["sha1"]
            raw = None
        else:
            try:
                raw = fetch(url)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{api}: tactics.tools {e}")
                if delay and download is None:
                    time.sleep(delay)
                continue
            if delay and download is None:
                time.sleep(delay)
            h = hashlib.sha1(raw).hexdigest()
        if h in rep_by_hash:   # 같은 그림 → 파일은 대표 하나
            entries[api] = {"source": "tactics.tools", "url": url, "sha1": h, "group": rep_by_hash[h]}
            continue
        if raw is not None:
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
            g = glyph_normalize(img, 64) if img is not None else None
            if g is None:
                failed.append(f"{api}: tactics.tools 이미지를 읽지 못함")
                continue
            small = cv2.resize(g, (36, 36), interpolation=cv2.INTER_AREA)
            if any(float(cv2.matchTemplate(small, p, cv2.TM_CCOEFF_NORMED).max()) >= PLACEHOLDER_SIMILARITY
                   for p in placeholders):
                failed.append(f"{api}: tactics.tools도 자리표시 그림")
                continue
            if not save_image(dest, g):
                failed.append(f"{api}: 저장 실패")
                continue
        rep_by_hash[h] = api
        entries[api] = {"source": "tactics.tools", "url": url, "sha1": h, "group": api}
    keep = {a for a, e in entries.items() if e["group"] == a}
    for stale in out.glob("*.png"):
        if stale.stem not in keep:
            stale.unlink()
    manifest = {"set": set_number, "note": "Riot 아트(tactics.tools 경유) — 로컬 캐시, 커밋 금지", "entries": entries}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(entries), failed, manifest


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

    from .capture import save_image
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
        if not save_image(out_dir / f"{api}.png", tpl):
            errors.append(f"{j}번 칸 {label!r}: 저장 실패")
            continue
        saved.append(api)
    return saved, errors


def harvest_augments(image, labels: list[str], static, profile, out_dir: Path,
                     content: tuple[int, int, int, int] | None = None) -> tuple[list[str], list[str]]:
    """준비 화면의 보유 증강 줄 칸 → `out_dir/{apiName}.png`(실화면 글리프 템플릿). 반환 (저장한 ID들, 오류 메시지들).

    `labels`는 줄의 왼쪽부터 증강 이름(한국어 표시 이름 또는 apiName). 줄의 칸 수와 라벨 수가 다르면 아무것도 저장하지 않는다
    (순서가 어긋난 템플릿은 확신에 찬 오답을 만든다). CDragon 아이콘이 없거나(404·자리표시 아이콘) 다른 증강과 아이콘을 공유해
    식별할 수 없는 증강을 사용자 확인 라벨로 보충하는 용도다.
    """
    from .capture import save_image
    from .icons import augment_cell_template, find_icon_row
    from .regions import FrameMapper

    m = FrameMapper.for_image(image, content)
    crop = m.crop(image, profile.augments_owned)
    cells = find_icon_row(crop, m.box[3])
    if len(cells) != len(labels):
        return [], [f"화면의 증강 칸 {len(cells)}개와 라벨 {len(labels)}개가 다르다(준비 화면인지, 라벨 순서 확인)"]
    names = {}
    for rec in static.tables["augments"]:
        names.setdefault(rec.get("name_ko"), rec["apiName"])
        names.setdefault(rec["apiName"], rec["apiName"])
    saved: list[str] = []
    errors: list[str] = []
    for j, ((x1, y1, x2, y2), label) in enumerate(zip(cells, labels)):
        api = names.get(label) or names.get((label or "").strip())
        if api is None:
            errors.append(f"{j}번 칸 {label!r}: 정적 데이터에서 증강을 찾지 못했다(게임 표시 이름 그대로인지 확인)")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        if save_image(out_dir / f"{api}.png", augment_cell_template(crop[y1:y2, x1:x2])):
            saved.append(api)
        else:
            errors.append(f"{j}번 칸 {label!r}: 저장 실패")
    return saved, errors


def _main(argv: list[str] | None = None) -> int:
    from .capture import load_image, save_image
    from .ocr import DigitTemplateReader
    from .regions import FrameMapper, draw_rois, game_box_by_pixels, profile_for_frame

    ap = argparse.ArgumentParser(prog="tft_advisor.vision.templates")
    ap.add_argument("--set", type=int, default=18)
    ap.add_argument("--profile", default="auto",
                    help="ROI 프로파일. auto(기본)면 스크린샷 크기에서 비율을 재서 고른다")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch-items")
    f.add_argument("--categories", default=",".join(DEFAULT_ITEM_CATEGORIES))
    f.add_argument("--overwrite", action="store_true")
    fa = sub.add_parser("fetch-augments")
    fa.add_argument("--overwrite", action="store_true")
    fa.add_argument("--no-alt", action="store_true", help="대체 출처(tactics.tools) 아이콘을 받지 않는다")
    fa.add_argument("--delay", type=float, default=FETCH_DELAY_S, help="요청 사이 간격(초)")
    for name in ("harvest-items", "harvest-digits", "harvest-augments"):
        h = sub.add_parser(name)
        h.add_argument("screenshot", type=Path)
        h.add_argument("label", type=Path)
    d = sub.add_parser("debug-rois")
    d.add_argument("screenshot", type=Path)
    d.add_argument("out", type=Path, nargs="?")
    args = ap.parse_args(argv)

    if args.cmd in ("fetch-items", "fetch-augments"):
        if args.cmd == "fetch-items":
            ok, failed = fetch_items(args.set, tuple(args.categories.split(",")), args.overwrite)
        else:
            ok, failed = fetch_augments(args.set, args.overwrite, alt=not args.no_alt, delay=args.delay)
        print(f"ok {ok}, failed {len(failed)}")
        for line in failed:
            print("  ", line)
        return 0 if not failed else 1

    img = load_image(args.screenshot)
    box = game_box_by_pixels(img)   # 여러 모니터를 이어 붙인 캡처(Win+PrtSc)면 게임 모니터, 레터박스면 안쪽
    m = FrameMapper.for_image(img, box)
    _, _, bw, bh = m.box
    profile = profile_for_frame(bw, bh, args.profile)
    print(f"프로파일: {profile.name} (게임 영역 {m.box}, 캡처 {img.shape[1]}x{img.shape[0]})")
    if args.cmd == "debug-rois":
        out = args.out or args.screenshot.with_suffix(".rois.png")
        save_image(out, draw_rois(img, profile, m))
        print(out)
        return 0

    label = json.loads(args.label.read_text(encoding="utf-8"))
    if args.cmd == "harvest-items":
        out = template_dir(args.set, "items_screen")
        bench = label.get("item_bench") or label.get("note_item_bench") or []
        if not isinstance(bench, list):
            print("item_bench가 목록이 아니다", file=sys.stderr)
            return 2
        saved, errors = harvest_items(img, bench, load_static(args.set), profile, out, content=box)
        print(f"{out}: 저장 {len(saved)} {saved}")
        for e in errors:
            print("  오류:", e, file=sys.stderr)
        return 1 if errors else 0
    if args.cmd == "harvest-augments":
        out = template_dir(args.set, "augments_screen")
        owned = label.get("augments_owned")
        if not isinstance(owned, list) or not owned:
            print("augments_owned(왼쪽부터 증강 이름 목록)가 라벨에 없다", file=sys.stderr)
            return 2
        saved, errors = harvest_augments(img, owned, load_static(args.set), profile, out, content=box)
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
