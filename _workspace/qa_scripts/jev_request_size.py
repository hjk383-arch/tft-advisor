"""Phase 2 QA: jev-strategist 설계 3.2절 요청 크기 추정(질문 28~54개, 7~12k 토큰) 재현 — Jev 호출 없음.

실행: .venv\\Scripts\\python _workspace/qa_scripts/jev_request_size.py
- fixture 7개를 load_expected로 GameState로 만든 뒤, 설계 4.2절 스키마를 흉내 낸 영어 state를 만든다.
- 후보 덱 N=8: comps_data 에서 games>=1000 상위 8개(표본 순). buildup은 comp_details가 424001만 캐시돼 있어
  모든 덱에 424001의 레벨별 1위 보드(현재·다음 레벨)를 크기 대용으로 쓴다.
- 질문은 설계 3절 문구 길이를 그대로 복사해 모드별 조건(C1: 아이템 >=1, C2: 보유 증강 >=1, C3: 보드 신뢰)을 적용한다.
- 토큰은 토크나이저가 없어 문자 수 / 4(하한) ~ / 3(상한)로 범위만 낸다.
- 'enriched' 행: 설계 가정(재료 4개 + 보유 증강 1개)을 fixture에 추가한 경우.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tft_advisor.contracts import AugmentRef, ItemRef, ItemState  # noqa: E402
from tft_advisor.fixtures import load_expected  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402

st = load_static(18)
RAW = max((ROOT / "data/raw/metatft").glob("????-??-??"))  # 최신 날짜 캐시
comps = json.loads((RAW / "comps_data.json").read_text(encoding="utf-8"))["results"]["data"]["cluster_details"]
det = json.loads((RAW / "comp_details_424001.json").read_text(encoding="utf-8"))["results"]
N = 8


def en(i: str) -> str:
    r = st.get("champions", i) or st.get("items", i) or st.get("traits", i) or st.get("augments", i)
    return (r or {}).get("name_en") or i


def trait_req(t: str) -> str:
    base, n = re.match(r"(.+)_(\d+)$", t.strip()).groups()
    bp = st.get("traits", base)["breakpoints"]
    cnt = bp[int(n) - 1] if bp and bp[int(n) - 1] is not None else "?"
    return f"{en(base)} {cnt}"


def best_board(level: int) -> list[str]:
    key = str(level)
    src = det["early_options"] if key in det["early_options"] else det["options"]
    boards = src.get(key) or []
    if not boards:
        return []
    b = max(boards, key=lambda x: x["count"])
    ul = b.get("unit_list") or b.get("units_list")
    return [en(u) for u in ul.split("&") if st.get("champions", u)]


def comp_state(c: dict, level: int) -> dict:
    units = [u.strip() for u in c["units_string"].split(",")]
    builds = {}
    for b in c["builds"]:
        builds.setdefault(b["unit"], b["buildName"])
    carry = c["builds"][0]["unit"]
    return {
        "name": " ".join(en(n["name"]) for n in c["name"][:2]),
        "plan": c["levelling"],
        "main_carry": en(carry),
        "main_carry_items": [en(i) for i in builds.get(carry, [])],
        "tanks": [en(u) for u in units[:2]],
        "key_traits": [trait_req(t) for t in c["traits_string"].split(",")[:3]],
        "final_board": [
            {"unit": en(u), "cost": (st.get("champions", u) or {}).get("cost"), "role": "support",
             **({"items": [en(i) for i in builds[u]]} if u in builds else {})}
            for u in units
        ],
        "buildup": {f"level {lv}": best_board(lv) for lv in (min(level, 9), min(level, 9) + 1)},
    }


def game_block(s) -> dict:
    g = {}
    if s.stage:
        g["stage"] = s.stage
        g["stage_phase"] = "stage 3 (mid game: stabilize and level toward 7-8)"
    if s.level:
        g["level"] = s.level
    if s.xp:
        g["xp_to_next"] = s.xp[1] - s.xp[0]
    if s.gold is not None:
        g["gold"], g["gold_status"] = s.gold, "can afford a few buys and still keep interest"
    if s.hp is not None:
        g["health"], g["health_status"] = s.hp, "moderate"
    if s.streak is not None:
        g["streak"] = "2-round win streak"
    if s.shop_odds:
        g["shop_odds"] = {f"{i+1}-cost": "common" for i in range(5)}
    return g


INSTR = {  # 설계 3절 문구(길이 측정용)
    "C1": "How well do the player's items (resources.completed_items, resources.emblems, resources.item_components, resources.craftable_items) fit the team comp candidate_comps[0] (\"Lunar Aphelios\")? Compare them with that comp's main_carry_items and the items listed on its final_board units.",
    "C2": "How well do the player's augments (resources.augments, read their descriptions) support playing the team comp candidate_comps[0] (\"Lunar Aphelios\")?",
    "C3": "How close are the player's current units (board and bench) to the team comp candidate_comps[0] (\"Lunar Aphelios\"), using that comp's final_board and buildup boards?",
    "S1": "How much would buying the unit in shop[0] (\"Camille\") strengthen the player's team for the fights of the current stage (game.stage_phase), right now?",
    "S2": "Does the unit in shop[0] (\"Camille\") belong to the player's plan toward the comps in candidate_comps (their final_board or their buildup boards)? candidate_comps is ordered from most to least likely.",
    "S3": "How valuable is buying the special shop offer shop[4] (\"With Tier 3\": \"Gain a random 2-star tier 3 champion.\") for this player right now?",
    "A1": "If the player takes the augment augment_offer[0] (\"Seraphim's Staff\"), how well would it support playing the team comp candidate_comps[0] (\"Lunar Aphelios\")?",
    "A2": "Regardless of which comp the player ends up playing, how much does the augment augment_offer[0] (\"Seraphim's Staff\") help this player, given their stage, health, gold and items?",
    "C4": "Which team comp in candidate_comps should the player aim for as their final comp, given their items, augments and units?",
    "A3": "Which augment in augment_offer should the player take?",
    "I1": "The player can combine two item components now (resources.item_components). Which completed item should they build first, considering the carries of candidate_comps, the current team, and the player's health (game.health_status)?",
}
LEVEL4 = [  # 레벨 문구 평균 길이 대용(C1 문구)
    "None of the player's items or components is used by any unit of this comp; they would go to filler units or be wasted.",
    "Some of the player's items suit this comp's tanks or support units, but none is an item its main carry uses.",
    "One core item of this comp's main carry is already completed or appears in resources.craftable_items.",
    "Two or more core items of this comp's main carry are completed or craftable, or the player holds an emblem of this comp's key trait.",
]


def q_score(kind):
    return {"type": "score", "instructions": INSTR[kind], "levels": LEVEL4}


def build(s, enriched: bool):
    if enriched:
        s = s.model_copy(update={
            "items": ItemState(components=[ItemRef(id=i) for i in ("DA_Component_BFSword", "DA_Component_NeedlesslyLargeRod", "DA_Component_TearOfTheGoddess", "DA_Component_ChainVest")]),
            "augments_owned": [AugmentRef(id="DA_Hustler")],
        })
    level = s.level or 4
    ranked = sorted((c for c in comps.values() if c["overall"]["count"] >= 1000), key=lambda c: -c["overall"]["count"])[:N]
    state = {"game": game_block(s)}
    res = {}
    if s.items and s.items.all_ids():
        res["item_components"] = [en(i.id) for i in s.items.components]
        res["craftable_items"] = [{"item": "Archangel's Staff", "from": ["Needlessly Large Rod", "Tear of the Goddess"]}] * 3
    if s.augments_owned:
        res["augments"] = [{"name": en(a.id), "description": (st.get("augments", a.id) or {}).get("desc_en", "")} for a in s.augments_owned]
    if res:
        state["resources"] = res
    state["candidate_comps"] = [comp_state(c, level) for c in ranked]
    if s.shop:
        shop = []
        for i, sl in enumerate(s.shop):
            if sl.kind == "champion":
                r = st.get("champions", sl.id)
                shop.append({"slot": i, "unit": r["name_en"], "cost": r["cost"], "traits": r["traits_en"], "copies_owned": 0, "buy_makes_2star": False})
            elif sl.kind == "special":
                r = st.get("shop_specials", sl.id)
                shop.append({"slot": i, "special": r["name_en"], "description": r.get("desc_ko", "")})
            else:
                shop.append({"slot": i, "status": str(sl.kind)})
        state["shop"] = shop
    if s.augment_offer:
        state["augment_offer"] = [{"name": en(a.id), "description": (st.get("augments", a.id) or {}).get("desc_en", "")} for a in s.augment_offer]

    qs = {}
    mode = str(s.screen_mode)
    if mode not in ("planning", "augment_select"):
        return state, qs
    has_items = bool(s.items and s.items.all_ids())
    for k in range(N):
        if has_items:
            qs[f"comp_item_fit_{k}"] = q_score("C1")
        if s.augments_owned:
            qs[f"comp_augment_fit_{k}"] = q_score("C2")
        if s.board and s.is_reliable("board", 0.6):
            qs[f"comp_board_fit_{k}"] = q_score("C3")
    qs["comp_pick"] = {"type": "choice", "instructions": INSTR["C4"],
                       "criteria": {c["name"]: f"candidate_comps[{k}]: main carry {c['main_carry']}, key traits {c['key_traits']}" for k, c in enumerate(state["candidate_comps"])} | {"undecided": "It is too early to tell: the player's items, augments and units do not point to any one of these comps."}}
    if mode == "planning" and s.shop:
        for i, sl in enumerate(s.shop):
            if sl.kind == "champion":
                qs[f"shop_now_{i}"] = q_score("S1")
                qs[f"shop_path_{i}"] = q_score("S2")
            elif sl.kind == "special":
                qs[f"special_value_{i}"] = {"type": "score", "instructions": INSTR["S3"], "levels": LEVEL4[:3]}
    if mode == "augment_select" and s.augment_offer:
        for a in range(len(s.augment_offer)):
            for k in range(N):
                qs[f"aug_comp_fit_{a}_{k}"] = q_score("A1")
            qs[f"aug_standalone_{a}"] = q_score("A2")
        qs["aug_pick"] = {"type": "choice", "instructions": INSTR["A3"], "criteria": {x["name"]: x["description"] for x in state["augment_offer"]}}
    if has_items:
        qs["item_pick"] = {"type": "choice", "instructions": INSTR["I1"], "criteria": {f"item{m}": {"from": ["A", "B"], "used_by": []} for m in range(6)} | {"hold_components": "Do not combine yet."}}
    return state, qs


def main():
    rows = []
    for p in sorted((ROOT / "tests/fixtures/screens").glob("*.expected.json")):
        s = load_expected(p).state
        for enriched in (False, True):
            state, qs = build(s, enriched)
            sc = len(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            qc = len(json.dumps(qs, ensure_ascii=False, separators=(",", ":")))
            longest = max((len(json.dumps(q)) for q in qs.values()), default=0)
            rows.append((p.stem.replace(".expected", ""), "enriched" if enriched else "as-is", str(s.screen_mode),
                         len(qs), sc, qc, f"{(sc+qc)//4}-{(sc+qc)//3}", f"{(sc+longest)//3}"))
    hdr = ("fixture", "variant", "mode", "n_questions", "state_chars", "questions_chars", "total_tok(est)", "state+longest_tok(max)")
    print(" | ".join(hdr))
    for r in rows:
        print(" | ".join(map(str, r)))


if __name__ == "__main__":
    main()
