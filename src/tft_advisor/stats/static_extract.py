"""Community Dragon TFT JSON -> data/static/{set}/ 정적 데이터 추출.

재실행 가능:  python src/tft_advisor/stats/static_extract.py [--set 18] [--raw data/static/raw_cdragon]
입력: data/static/raw_cdragon/{ko_kr,en_us}.json  (raw.communitydragon.org/latest/cdragon/tft/{locale}.json)
출력: data/static/{set}/champions.json, traits.json, items.json, augments.json,
      shop_specials.json, name_check.json, meta.json

주의(추정 사항은 코드 주석과 meta.json에 명시):
- 증강 티어는 CDragon에 명시 필드가 없다. tags 해시 {d11fd6d5}/{ce1fd21c}/{cf1fd3af}가
  아이콘 파일명 접미사 -i/-ii/-iii 와 일치하는 것으로 보아 실버/골드/프리즘으로 해석했다(교차검증 결과는 meta.json).
- 세트 증강 풀에는 같은 이름의 TFT*_Augment_* 와 DA_* 가 중복으로 들어 있다. 어느 쪽이 라이브인지
  CDragon만으로는 알 수 없으므로 둘 다 보존하고 alias 로 묶는다(통계 소스 ID로 확정 필요).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

TIER_TAGS = {"{d11fd6d5}": 1, "{ce1fd21c}": 2, "{cf1fd3af}": 3}  # 추정: silver/gold/prismatic
TIER_NAMES = {1: "silver", 2: "gold", 3: "prismatic"}
TAG_COMPLETED = "{7ea41d13}"
TAG_TACTICIAN = "{ec243f6b}"
TAG_ARTIFACT = "{44ace175}"
TAG_RADIANT = "{6ef5c598}"
TAG_SET_MECHANIC = "{5b609ae2}"  # 상점 특수 상품(set18 'wands' 아이콘)

# 사용자 스크린샷에서 확인된 이름 (2026-09-21)
SCREENSHOT_NAMES = {
    "champions": ["카밀", "워윅", "카르마", "아칼리", "조약돌", "자야", "바루스", "오른", "티모", "니달리",
                  "람머스", "라칸", "요릭", "헤카림", "알리스타", "바위 게", "렉사이"],
    "traits": ["검은 가시", "엄호대", "주문술사", "나무정령", "날렵이", "악의 여단", "약탈자", "개화", "지옥불",
               "적응가", "싸움꾼", "치명적인 꽃", "선봉대", "햇빛", "협곡야수", "기원자", "요정", "속사포",
               "전쟁기계", "소환사", "원시"],
    "augments": ["고위천사의 지팡이", "출정", "수완가"],
    "shop_specials": ["3단계와 함께"],
}
# 화면에서 관측된 레벨별 상점 확률(%) — 관측된 레벨만. 나머지는 채우지 않는다.
OBSERVED_SHOP_ODDS = {"3": [75, 25, 0, 0, 0], "4": [55, 30, 15, 0, 0], "6": [30, 40, 25, 5, 0]}
# 레벨별 상점 확률(%) 1~5코스트. CDragon에 없음 -> 공개 표 3곳 교차 확인(2026-09-22 조회, Set 18 / 18.2b 표기).
# 관측값(3/4/6)과 모두 일치. 7레벨만 출처가 갈린다: tftflow.com·esportstales.com = 16/30/43/10/1,
# metabot.gg = 19/30/40/10/1 -> 다수(2/3)를 채택하고 대안을 SHOP_ODDS_CONFLICTS에 병기.
SHOP_ODDS_PCT = {
    "1": [100, 0, 0, 0, 0], "2": [100, 0, 0, 0, 0], "3": [75, 25, 0, 0, 0], "4": [55, 30, 15, 0, 0],
    "5": [45, 33, 20, 2, 0], "6": [30, 40, 25, 5, 0], "7": [16, 30, 43, 10, 1], "8": [15, 20, 32, 30, 3],
    "9": [10, 17, 25, 33, 15], "10": [5, 10, 20, 40, 25],
}
SHOP_ODDS_SOURCES = ["https://tftflow.com/tables/set18/shop-odds-pool-size-xp-table",
                     "https://www.esportstales.com/teamfight-tactics/champion-pool-size-and-draw-chances",
                     "https://metabot.gg/en/TFT/rolldown-odds"]
SHOP_ODDS_CONFLICTS = {"7": {"metabot.gg": [19, 30, 40, 10, 1]}}

_TAG_RE = re.compile(r"<[^>]+>")
_VAR_RE = re.compile(r"@([A-Za-z0-9_{}.:]+)(\*100)?@")


def load(raw: Path, locale: str) -> dict:
    return json.loads((raw / f"{locale}.json").read_text(encoding="utf-8"))


def find_set(d: dict, set_no: int | None) -> dict:
    cands = [s for s in d["setData"] if s["mutator"] == f"TFTSet{s['number']}"]
    if set_no is None:
        set_no = max(s["number"] for s in cands)
    return next(s for s in cands if s["number"] == set_no)


def render_desc(desc: str | None, effects: dict | None) -> str:
    """@Var@ / @Var*100@ 를 effects 값으로 치환하고 태그를 정리한다(치환 불가 변수는 '?')."""
    if not desc:
        return ""
    eff = {k.lower(): v for k, v in (effects or {}).items()}

    def sub(m: re.Match) -> str:
        v = eff.get(m.group(1).lower())
        if not isinstance(v, (int, float)):
            return "?"
        v = v * 100 if m.group(2) else v
        return f"{v:.0f}" if abs(v - round(v)) < 1e-6 else f"{v:.2f}".rstrip("0").rstrip(".")

    s = desc.replace("<br>", "\n")
    s = _VAR_RE.sub(sub, s)
    s = _TAG_RE.sub("", s)
    return re.sub(r"[ \t]+", " ", s).strip()


OPGG_TIER = {"silver": 1, "gold": 2, "prism": 3, "prismatic": 3}


def trait_breakpoints(effects: list[dict]) -> tuple[list[int], bool]:
    """CDragon effects -> (breakpoints, unit_less).

    minUnits가 null인 특성(Set 18 `DA_18_Eclipse`: 보유 챔피언 0명, effects 1개, maxUnits 25000)은 인원으로
    켜지지 않는다. 이때 breakpoints는 단계 번호(1..n)로 채우고 unit_less=True로 표시한다.
    MetaTFT `DA_18_Eclipse_1`의 `_1`은 단계 번호이므로 breakpoints[0]=1과 모순되지 않는다.
    수집기는 unit_less 특성을 TraitReq(목표 인원)로 만들지 않는다.
    """
    mins = [e.get("minUnits") for e in effects]
    if mins and all(m is None for m in mins):
        return list(range(1, len(mins) + 1)), True
    if any(m is None for m in mins):
        raise ValueError(f"일부만 minUnits=null인 특성: {mins}")
    return mins, False


def load_opgg_table(path: Path) -> dict[str, dict]:
    """OP.GG MCP tools/call 응답(또는 내부 표) -> {apiName: {헤더: 값}}."""
    env = json.loads(path.read_text(encoding="utf-8"))
    tbl = json.loads(env["result"]["content"][0]["text"]) if "result" in env else env
    return {r[0]: dict(zip(tbl["headers"], r)) for r in tbl["rows"]}


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def icon_tier_suffix(icon: str) -> int | None:
    m = re.search(r"[-_](i{1,3})(?:[-_.])", icon.rsplit("/", 1)[-1])
    return len(m.group(1)) if m else None


def item_category(it: dict) -> str:
    a, tags = it["apiName"], it.get("tags") or []
    if "component" in tags:
        return "component"
    if "Emblem" in a and "_18_" in a:
        return "emblem"
    if TAG_SET_MECHANIC in tags:
        return "shop_special"
    if TAG_RADIANT in tags or "Radiant" in a:
        return "radiant"
    if TAG_ARTIFACT in tags or "Artifact" in a or "Ornn" in a:
        return "artifact"
    if TAG_TACTICIAN in tags and it.get("composition"):
        return "tactician"
    if TAG_COMPLETED in tags and len(it.get("composition") or []) == 2:
        return "completed"
    if "Consumable" in tags or "Consumable" in a:
        return "consumable"
    if a.startswith("TFT_Assist") or a.startswith("TFT14_Cypher"):
        return "assist_reward"
    return "other"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, default=None, help="세트 번호 (기본: 최신)")
    ap.add_argument("--raw", default=str(ROOT / "data/static/raw_cdragon"))
    ap.add_argument("--opgg-augments", default=None,
                    help="OP.GG MCP tft_list_augments(lang=ko_KR) 응답 JSON(선택). 라이브 증강 목록·티어 교차검증용")
    ap.add_argument("--opgg-augments-en", default=None,
                    help="같은 도구 lang=en_US 응답(선택). desc_en_opgg / OP.GG 전용 증강의 영어 이름·설명")
    args = ap.parse_args()
    raw = Path(args.raw)
    ko, en = load(raw, "ko_kr"), load(raw, "en_us")
    sko, sen = find_set(ko, args.set), find_set(en, args.set)
    set_no = sko["number"]
    out = ROOT / "data/static" / str(set_no)
    out.mkdir(parents=True, exist_ok=True)

    idx_ko = {i["apiName"]: i for i in ko["items"]}
    idx_en = {i["apiName"]: i for i in en["items"]}
    en_champ = {c["apiName"]: c for c in sen["champions"]}
    en_trait = {t["apiName"]: t for t in sen["traits"]}

    # traits
    traits = []
    trait_name_to_api = {}
    for t in sko["traits"]:
        trait_name_to_api[t["name"]] = t["apiName"]
        bps, unit_less = trait_breakpoints(t["effects"])
        traits.append({
            "apiName": t["apiName"], "name_ko": t["name"],
            "name_en": en_trait.get(t["apiName"], {}).get("name"),
            "breakpoints": bps,
            "styles": [e.get("style") for e in t["effects"]],
            "unique": len(t["effects"]) == 1 and bps[0] == 1 and not unit_less,
            # 보유 챔피언이 없어 인원으로 켤 수 없는 특성(예: DA_18_Eclipse). breakpoints는 단계 번호 자리표시(1..n)
            "unit_less": unit_less,
            # 단일 효과 특성만 변수 치환(다단계 특성은 <row>마다 값이 달라 1단계 값으로 채우면 오해 소지)
            "desc_ko": render_desc(t.get("desc"), t["effects"][0].get("variables") if len(t["effects"]) == 1 else {}),
            "icon": t.get("icon"),
        })
    trait_en_by_api = {t["apiName"]: t["name_en"] for t in traits}

    # champions
    champs = []
    for c in sko["champions"]:
        tr_api = [trait_name_to_api.get(n, n) for n in c["traits"]]
        champs.append({
            "apiName": c["apiName"], "name_ko": c["name"],
            "name_en": en_champ.get(c["apiName"], {}).get("name"),
            "cost": c["cost"], "traits": tr_api, "traits_ko": c["traits"],
            "traits_en": [trait_en_by_api.get(a) for a in tr_api],
            # 상점 풀 추정: 특성이 있고 코스트 1~5. 크립/모루/훈련봇 등은 false.
            "shop_pool": bool(c["traits"]) and 1 <= c["cost"] <= 5,
            "role": c.get("role"),
            "ability_ko": {"name": c["ability"].get("name"),
                           "desc": render_desc(c["ability"].get("desc"),
                                               {v["name"]: (v.get("value") or [None, None])[1]
                                                for v in c["ability"].get("variables") or []})},
            "icon": c.get("icon"), "squareIcon": c.get("squareIcon"), "tileIcon": c.get("tileIcon"),
        })

    # items
    items, specials = [], []
    for a in sko["items"]:
        it, ie = idx_ko[a], idx_en.get(a, {})
        row = {
            "apiName": a, "name_ko": it["name"], "name_en": ie.get("name"),
            "category": item_category(it),
            "composition": it.get("composition") or [],
            "unique": it.get("unique", False),
            "desc_ko": render_desc(it.get("desc"), it.get("effects")),
            "icon": it.get("icon"),
            # DA_* 가 세트 18 자체 ID. TFT_Item_* 는 같은 이름의 범용 ID (소스가 이쪽으로 보고할 수 있음)
            "set_native": a.startswith("DA_"),
        }
        if row["category"] == "shop_special":  # jev state(영어)용
            row["desc_en"] = render_desc(ie.get("desc"), ie.get("effects"))
        (specials if row["category"] == "shop_special" else items).append(row)
    # 같은 한국어 이름 alias
    by_name: dict[str, list[str]] = {}
    for r in items:
        by_name.setdefault(r["name_ko"], []).append(r["apiName"])
    for r in items:
        r["aliases"] = [x for x in by_name[r["name_ko"]] if x != r["apiName"]]

    # augments
    augs, tier_check = [], {"agree": 0, "disagree": 0, "no_suffix": 0}
    for a in sko["augments"]:
        it, ie = idx_ko[a], idx_en.get(a, {})
        tiers = [TIER_TAGS[t] for t in it.get("tags") or [] if t in TIER_TAGS]
        tier = tiers[0] if len(tiers) == 1 else None
        suf = icon_tier_suffix(it.get("icon") or "")
        if suf is None:
            tier_check["no_suffix"] += 1
        elif suf == tier:
            tier_check["agree"] += 1
        else:
            tier_check["disagree"] += 1
        augs.append({
            "apiName": a, "name_ko": it["name"], "name_en": ie.get("name"),
            "tier": tier, "tier_name": TIER_NAMES.get(tier),
            "desc_ko": render_desc(it.get("desc"), it.get("effects")),
            "desc_en": render_desc(ie.get("desc"), ie.get("effects")),
            "associated_traits": it.get("associatedTraits") or [],
            "icon": it.get("icon"),
            "set_native": a.startswith("DA_"),
        })
    # OP.GG 증강 목록(라이브 풀로 추정)과 교차검증
    opgg_check = None
    if args.opgg_augments:
        tbl = load_opgg_table(Path(args.opgg_augments))
        tbl_en = load_opgg_table(Path(args.opgg_augments_en)) if args.opgg_augments_en else {}
        og = {k: OPGG_TIER.get(r["tier"]) for k, r in tbl.items()}
        mism = []
        for r in augs:
            r["opgg_listed"] = r["apiName"] in og
            if r["opgg_listed"] and og[r["apiName"]] != r["tier"]:
                mism.append(r["apiName"])
            if r["opgg_listed"]:  # CDragon 변수 치환 실패('?') 보완용 — OP.GG 라이브 설명 병기(원문 desc_ko는 유지)
                r["desc_ko_opgg"] = tbl[r["apiName"]]["desc"]
                if r["apiName"] in tbl_en:
                    r["desc_en_opgg"] = tbl_en[r["apiName"]]["desc"]
        mine = {r["apiName"] for r in augs}
        opgg_only = sorted(k for k in og if k not in mine)
        added = []
        for k in opgg_only:  # CDragon 스냅샷에 없는 라이브 DA_ 증강만 OP.GG로 보충(ID는 소스들이 쓰는 그대로)
            if not k.startswith("DA_"):
                continue
            ko_r, en_r = tbl[k], tbl_en.get(k, {})
            augs.append({
                "apiName": k, "name_ko": ko_r["name"], "name_en": en_r.get("name"),
                "tier": og[k], "tier_name": TIER_NAMES.get(og[k]),
                "desc_ko": ko_r["desc"], "desc_en": en_r.get("desc", ""),
                "associated_traits": [], "icon": ko_r.get("imageUrl"), "set_native": True,
                "opgg_listed": True, "desc_ko_opgg": ko_r["desc"], "desc_en_opgg": en_r.get("desc"),
                "source": "opgg_mcp",
            })
            added.append(k)
        opgg_check = {"opgg_rows": len(og), "listed_in_cdragon_pool": len(og) - len(opgg_only),
                      "tier_mismatch": mism, "opgg_only": opgg_only, "added_from_opgg": added}
    by_name = {}
    for r in augs:
        by_name.setdefault(r["name_ko"], []).append(r["apiName"])
    for r in augs:
        r["aliases"] = [x for x in by_name[r["name_ko"]] if x != r["apiName"]]

    # 스크린샷 이름 검증
    def names(rows, key="name_ko"):
        return {r[key] for r in rows}
    pools = {"champions": names([c for c in champs if c["shop_pool"]]), "traits": names(traits),
             "augments": names(augs), "shop_specials": names(specials)}
    check = {k: {n: (n in pools[k]) for n in v} for k, v in SCREENSHOT_NAMES.items()}

    meta = {
        "set": set_no, "mutator": sko["mutator"], "source": "raw.communitydragon.org latest cdragon/tft",
        "raw_files": [_rel(raw / "ko_kr.json"), _rel(raw / "en_us.json")],
        "counts": {"champions": len(champs), "shop_pool_champions": sum(c["shop_pool"] for c in champs),
                   "traits": len(traits), "items": len(items), "augments": len(augs),
                   "shop_specials": len(specials)},
        "augment_tier_tag_vs_icon_suffix": tier_check,
        "augment_opgg_check": opgg_check,
        "observed_shop_odds_pct": OBSERVED_SHOP_ODDS,
        "shop_odds_pct": SHOP_ODDS_PCT,
        "shop_odds_sources": SHOP_ODDS_SOURCES,
        "shop_odds_conflicts": SHOP_ODDS_CONFLICTS,
        "notes": ["증강 tier는 tags 해시 기반 추정", "TFT_* 와 DA_* 동명 중복은 aliases 로 연결"],
    }
    for name, obj in [("champions", champs), ("traits", traits), ("items", items), ("augments", augs),
                      ("shop_specials", specials), ("name_check", check), ("meta", meta)]:
        (out / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta["counts"], ensure_ascii=False), tier_check)
    for k, v in check.items():
        miss = [n for n, ok in v.items() if not ok]
        print(k, f"{sum(v.values())}/{len(v)}", "missing:", miss)


if __name__ == "__main__":
    main()
