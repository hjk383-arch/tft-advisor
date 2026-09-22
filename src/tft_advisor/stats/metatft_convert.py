"""MetaTFT 원본(data/raw/metatft/{date}/) -> 계약 모델(CompStats/AugmentTier/UnitStats/UnitItemStats) 변환.

변환 규칙은 모두 이 파일의 순수 함수에 있다(네트워크·파일 I/O 없음, `build_all`과 `main`만 파일을 읽고 쓴다).
규칙의 근거와 관측 수치: `_workspace/03_stats-researcher_fixes.md` 2절.

    python -m tft_advisor.stats.metatft_convert [--raw data/raw/metatft/2026-09-22] [--out data/stats]

요약
- R1 특성 `_N`       : N은 인원이 아니라 구간 번호 -> TraitReq.count = breakpoints[N-1]. unit_less 특성(Eclipse)은 제외
- R2 levels          : stage/round가 빈 문자열인 행은 건너뛴다. 같은 레벨이 여러 행이면 count 최대 행
- R3 itemNames.units : 키가 없을 수 있다(.get). 없으면 UnitItemStats를 만들지 않는다(덱 조건부 통계는 유지).
                       itemNames[].units[](아이템 1개)와 builds[](1~3개)를 UnitItemStats(comp_id=덱)로 만든다
- R4 보드 키         : early_options는 `unit_list`, options는 `units_list`
- R5 레벨 키         : early_options의 `level`(float, 예 4.028)은 그 보드를 가진 참가자들의 평균 레벨이다.
                       buildup 키는 dict 키 문자열의 int를 쓴다(float 반올림 금지). 4~10 밖 키, 빈 목록 버림
- R6 early `win`     : 1등 비율이 아니라 **top4 비율**로 해석한다(평균등수 3.5~4.0 보드의 평균 win이 0.63).
                       BuildupBoard.top4에 넣고 win_rate는 None
- R7 carry           : 최종 보드 유닛 중 대표 빌드(R8)가 방어 위주가 아닌 유닛에서 "이 덱에서 아이템 3개를 든 판 수"
                       (unit_stats.num_items[3].count) 최대. 동률이면 builds score. 배열 순서는 쓰지 않는다
- R8 carry_bis_items : carry의 3아이템 빌드 중 count >= BIS_MIN_COUNT인 것에서 score 최대. 없으면 count 최대
- R9 is_core         : 덱 안 유닛 출현율 unit_stats.pcnt >= CORE_MIN_PICK(0.75)
- R10 role           : carry면 "carry". 아니면 대표 빌드(R8 규칙) 아이템의 재료 구성으로 공격/방어를 세어
                       공격 >= 2 "carry", 방어 >= 2 "tank", 그 외 "support"(hybrid 재료는 0.5씩). 3아이템 빌드가
                       없으면 1~2아이템 빌드 중 count 최대로 판정, 빌드 정보가 아예 없으면 None
- R11 star           : unit_stats.tiers의 pcnt 최대 성급
- R12 comp_id        : MetaTFT 표시 헤드라인 name[](특성·유닛)을 짧은 slug로. 이전 스냅샷과 최종 보드 Jaccard >=
                       COMP_ID_REUSE_JACCARD면 이전 comp_id를 재사용(클러스터 재계산 대비)
- R13 comp_augment_tiers: 키는 덱(클러스터) ID. 제목 첫 구간의 챔피언이 덱 최종 보드에 있고 distance <= 0.5일 때만 채택
- R14 rank_filter    : 문자열이 아니라 집합으로 비교. 저장은 정렬 정규화 문자열
- R15 item_usage     : comps_data build_items[].pcnt(덱당 평균 보유 개수, 0~1.38 관측) — 비율이 아니다. float >= 0
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from tft_advisor.contracts import (
    BUILDUP_LEVELS,
    AugmentTier,
    BuildupBoard,
    CompStats,
    CompUnit,
    PlacementStats,
    StatSource,
    TraitReq,
    UnitItemStats,
    UnitStats,
)
from tft_advisor.contracts import normalize_rank_filter as _norm_rank
from tft_advisor.contracts import same_rank_filter as _same_rank
from tft_advisor.static_data import PROJECT_ROOT, StaticData, load_static

# --------------------------------------------------------------------------- 상수(근거는 보고서 2절)

SUMMON_IDS = frozenset({"DA_Elderwood18_Lifeblossom", "DA_Elderwood18_StonebarkTree"})
"""빌드업 보드에 섞여 나오는 나무정령 소환물. 챔피언이 아니므로 보드에서 제거."""
CORE_MIN_PICK = 0.75
BIS_MIN_COUNT = 50
COMP_ID_REUSE_JACCARD = 0.75
AUG_TIER_MAX_DISTANCE = 0.5
BUILDUP_TOP_N = 10
EARLY_LEVELS = (4, 5, 6, 7)      # early_options에서 가져오는 레벨(빌드업, win=top4 있음)
OPTION_LEVELS = (7, 8, 9, 10)   # options(최종 보드)에서 가져오는 레벨. 7은 early에 없을 때만

OFFENSE_COMPONENTS = frozenset({
    "DA_Component_BFSword", "DA_Component_RecurveBow", "DA_Component_NeedlesslyLargeRod",
    "DA_Component_SparringGloves", "TFT_Item_BFSword", "TFT_Item_RecurveBow",
    "TFT_Item_NeedlesslyLargeRod", "TFT_Item_SparringGloves",
})
DEFENSE_COMPONENTS = frozenset({
    "DA_Component_ChainVest", "DA_Component_NegatronCloak", "DA_Component_GiantsBelt",
    "TFT_Item_ChainVest", "TFT_Item_NegatronCloak", "TFT_Item_GiantsBelt",
})

Role = Literal["carry", "tank", "support"]
ItemClass = Literal["offense", "defense", "hybrid", "utility"]


# --------------------------------------------------------------------------- 공통 파싱


def split_ids(s: str | None, sep: str = ",") -> list[str]:
    """"A, B, " -> ["A", "B"] (MetaTFT units_string/traits_string은 끝에 ", "가 붙는다)."""
    return [x.strip() for x in (s or "").split(sep) if x.strip()]


def normalize_rank_filter(s: str | None) -> str | None:
    """R14: 순서·공백·대소문자를 무시한 정규화 문자열(정렬). 저장용. 빈 값은 None(contracts 헬퍼에 위임)."""
    return _norm_rank(s) if s and s.strip(" ,") else None


def same_rank_filter(a: str | None, b: str | None) -> bool:
    """R14: 집합 비교(contracts.same_rank_filter와 같되 빈 문자열을 None으로 본다)."""
    return _same_rank(normalize_rank_filter(a), normalize_rank_filter(b))


def patch_string(patch_json: Mapping[str, Any]) -> str:
    """/tft-stat-api/patch -> "18.2b"."""
    return f"{patch_json['patch']}{patch_json.get('b_patch_version') or ''}"


def placement_from_places(places: list[int]) -> PlacementStats:
    """1~8등 횟수 -> avg/top4/win/games."""
    g = sum(places)
    if g == 0:
        return PlacementStats(games=0)
    return PlacementStats(
        games=g,
        avg_place=sum((i + 1) * n for i, n in enumerate(places)) / g,
        top4=sum(places[:4]) / g,
        win_rate=places[0] / g,
    )


# --------------------------------------------------------------------------- R1 특성


def trait_tier(key: str) -> tuple[str, int]:
    """"DA_18_Lunar_2" -> ("DA_18_Lunar", 2)."""
    m = re.match(r"^(.+)_(\d+)$", key.strip())
    if not m:
        raise ValueError(f"특성 단계 접미사 없음: {key!r}")
    return m.group(1), int(m.group(2))


def trait_count(trait_rec: Mapping[str, Any], tier_no: int) -> int | None:
    """R1: 구간 번호 -> 목표 인원. unit_less 특성, 범위 밖 번호, null 구간은 None."""
    bps = trait_rec.get("breakpoints") or []
    if trait_rec.get("unit_less") or not 1 <= tier_no <= len(bps):
        return None
    return bps[tier_no - 1]


def key_traits(traits_string: str, static: StaticData, unmapped: set[str] | None = None) -> list[TraitReq]:
    out = []
    for key in split_ids(traits_string):
        base, n = trait_tier(key)
        rec = static.get("traits", base)
        if rec is None:
            if unmapped is not None:
                unmapped.add(base)
            continue
        c = trait_count(rec, n)
        if c is not None:
            out.append(TraitReq(id=base, count=c))
    return out


# --------------------------------------------------------------------------- R2 레벨업 타이밍


def level_timing(levels: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    """R2: {레벨: "스테이지-라운드"}. 빈 stage/round 행은 건너뛰고, 같은 레벨 중복은 count 최대."""
    best: dict[int, tuple[int, str]] = {}
    for row in levels:
        stage, rnd = str(row.get("stage") or "").strip(), str(row.get("round") or "").strip()
        if not stage or not rnd:
            continue
        lv = int(row["level"])
        cnt = int(row.get("count") or 0)
        if lv not in best or cnt > best[lv][0]:
            best[lv] = (cnt, f"{stage}-{rnd}")
    return {lv: s for lv, (_, s) in sorted(best.items())}


# --------------------------------------------------------------------------- R4~R6 빌드업


def board_units(board: Mapping[str, Any], summons: frozenset[str] = SUMMON_IDS) -> list[str]:
    """R4: early_options=`unit_list`, options=`units_list`. '&' 구분, 소환물 제거."""
    raw = board.get("unit_list")
    if raw is None:
        raw = board.get("units_list")
    return [u for u in split_ids(raw, "&") if u not in summons]


def to_buildup_board(level: int, board: Mapping[str, Any], summons: frozenset[str] = SUMMON_IDS) -> BuildupBoard | None:
    """R5/R6. early의 `win`은 top4 비율로 넣는다. 소환물 제거 후 빈 보드는 None."""
    units = board_units(board, summons)
    if not units:
        return None
    win = board.get("win")
    return BuildupBoard(level=level, units=units, avg_place=board.get("avg"), games=board.get("count"),
                        top4=win if isinstance(win, (int, float)) and 0 <= win <= 1 else None)


def buildup(early: Mapping[str, list], options: Mapping[str, list], top_n: int = BUILDUP_TOP_N,
            summons: frozenset[str] = SUMMON_IDS) -> dict[int, list[BuildupBoard]]:
    """R5: 레벨 키 = dict 키의 int. 4~7은 early_options, 8~10은 options, 7은 early가 없을 때 options.

    각 레벨은 count 내림차순 상위 top_n. 계약 범위(4~10) 밖 키(options '11'은 11기 보드 등)와 빈 목록은 버린다.
    """
    out: dict[int, list[BuildupBoard]] = {}

    def add(lv: int, boards: list) -> None:
        rows = [b for b in (to_buildup_board(lv, x, summons) for x in boards or []) if b is not None]
        rows.sort(key=lambda b: -(b.games or 0))
        if rows:
            out[lv] = rows[:top_n]

    for k, boards in (early or {}).items():
        lv = int(k)
        if lv in EARLY_LEVELS and lv in BUILDUP_LEVELS:
            add(lv, boards)
    for k, boards in (options or {}).items():
        lv = int(k)
        if lv in OPTION_LEVELS and lv in BUILDUP_LEVELS and lv not in out:
            add(lv, boards)
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- R7~R11 유닛 파생


def unit_stats_index(det_unit_stats: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {u["unit"]: u for u in det_unit_stats or []}


def three_item_count(us: Mapping[str, Any] | None) -> int:
    for n in (us or {}).get("num_items") or []:
        if n.get("num_items") == 3:
            return int(n.get("count") or 0)
    return 0


def representative_items(builds: Iterable[Mapping[str, Any]], unit: str) -> list[str]:
    """R10용: 3아이템 BIS(R8), 없으면 해당 유닛의 1~2아이템 빌드 중 count 최대. 없으면 []."""
    builds = list(builds or [])
    items = pick_bis(builds, unit)
    if items:
        return items
    cands = [b for b in builds if b.get("unit") == unit and b.get("buildName")]
    return list(max(cands, key=lambda b: b.get("count") or 0)["buildName"]) if cands else []


def pick_bis(builds: Iterable[Mapping[str, Any]], unit: str, min_count: int = BIS_MIN_COUNT) -> list[str]:
    """R8: unit의 3아이템 빌드 중 count>=min_count에서 score 최대, 없으면 count 최대. 없으면 []."""
    cands = [b for b in builds or [] if b.get("unit") == unit and len(b.get("buildName") or []) == 3]
    if not cands:
        return []
    ok = [b for b in cands if (b.get("count") or 0) >= min_count]
    best = (max(ok, key=lambda b: (b.get("score") or 0, b.get("count") or 0)) if ok
            else max(cands, key=lambda b: (b.get("count") or 0, b.get("score") or 0)))
    return list(best["buildName"])


def pick_carry(final_units: list[str], us_index: Mapping[str, Mapping[str, Any]],
               builds: Iterable[Mapping[str, Any]], static: StaticData | None = None) -> str | None:
    """R7: 3아이템 보유 판 수 최대, 동률은 해당 유닛 빌드 score 최대. 모두 0이면 None.

    static이 주어지면 대표 빌드(R8)가 방어 위주(방어 > 공격, item_class 기준)인 유닛은 후보에서 뺀다
    (탱커가 워모그·가고일 3개를 드는 덱에서 탱커가 carry로 뽑히는 것을 막음). 후보가 없으면 제외 없이 다시 고른다.
    """
    builds = list(builds or [])
    best_score: dict[str, float] = {}
    for b in builds:
        best_score[b["unit"]] = max(best_score.get(b["unit"], 0.0), b.get("score") or 0.0)

    def rank(cands: list[str]) -> str | None:
        ranked = sorted(cands, key=lambda u: (three_item_count(us_index.get(u)), best_score.get(u, 0.0)), reverse=True)
        if not ranked or three_item_count(us_index.get(ranked[0])) == 0:
            return None
        return ranked[0]

    if static is not None:
        dmg = [u for u in final_units if not _defense_build(pick_bis(builds, u), static)]
        c = rank(dmg)
        if c is not None:
            return c
    return rank(final_units)


def _off_def(items: list[str], static: StaticData) -> tuple[float, float]:
    off = de = 0.0
    for i in items:
        c = item_class(i, static)
        if c == "offense":
            off += 1
        elif c == "defense":
            de += 1
        elif c == "hybrid":
            off += 0.5
            de += 0.5
    return off, de


def _defense_build(items: list[str], static: StaticData) -> bool:
    off, de = _off_def(items, static)
    return de > off


def is_core(us: Mapping[str, Any] | None, threshold: float = CORE_MIN_PICK) -> bool:
    """R9."""
    return float((us or {}).get("pcnt") or 0.0) >= threshold


def star_of(us: Mapping[str, Any] | None) -> int | None:
    """R11."""
    tiers = (us or {}).get("tiers") or []
    if not tiers:
        return None
    t = max(tiers, key=lambda x: x.get("pcnt") or 0)["tier"]
    return int(t) if 1 <= int(t) <= 4 else None


def item_class(item_id: str, static: StaticData) -> ItemClass | None:
    """재료 구성 기반 분류(CDragon DA_ 아이템 effects가 비어 있어 수치로는 분류 불가).

    공격 재료(검/활/지팡이/장갑)와 방어 재료(조끼/망토/허리띠) 수를 비교. 여신의 눈물·뒤집개·프라이팬은 중립.
    """
    rec = static.get("items", item_id)
    if rec is None:
        return None
    comp = rec.get("composition") or []
    if rec.get("category") == "component":
        comp = [item_id]
    off = sum(c in OFFENSE_COMPONENTS for c in comp)
    de = sum(c in DEFENSE_COMPONENTS for c in comp)
    if off > de:
        return "offense"
    if de > off:
        return "defense"
    return "hybrid" if off else "utility"


def unit_role(unit: str, carry: str | None, items: list[str], static: StaticData) -> Role | None:
    """R10. hybrid(예 스테락: 검+허리띠)는 양쪽에 0.5씩."""
    if unit == carry:
        return "carry"
    if not items:
        return None
    off, de = _off_def(items, static)
    if off >= 2:
        return "carry"
    if de >= 2:
        return "tank"
    return "support"


# --------------------------------------------------------------------------- R12 comp_id


def short_id(api: str) -> str:
    """"DA_Juggernaut18" -> "juggernaut", "DA_18_Zyra" -> "zyra", "DA_Nidalee18_AP" -> "nidalee_ap"."""
    s = re.sub(r"^(DA|TFT\d*)_", "", api)
    s = re.sub(r"(^|_)18(_|$)", r"\1", s)
    s = re.sub(r"18(?=_|$)", "", s)
    return s.strip("_").lower()


def headline_ids(cd: Mapping[str, Any]) -> list[str]:
    """MetaTFT 표시 헤드라인 `name[]`(특성 1 + 유닛 1~2, score 기반). 없으면 name_string."""
    items = cd.get("name")
    if isinstance(items, list) and items:
        return [x["name"] for x in items]
    return split_ids(cd.get("name_string"))


def comp_id_from_name(ids: Iterable[str] | str) -> str:
    """R12 기본 키: 헤드라인 ID들의 짧은 slug. 예 ["DA_Juggernaut18","DA_18_Zyra","DA_Amumu18"] -> "juggernaut-zyra-amumu"."""
    ids = split_ids(ids) if isinstance(ids, str) else list(ids)
    return "-".join(short_id(x) for x in ids) or "comp"


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def assign_comp_ids(clusters: Mapping[str, Mapping[str, Any]], previous: Mapping[str, list[str]] | None = None,
                    threshold: float = COMP_ID_REUSE_JACCARD) -> dict[str, str]:
    """R12: {클러스터 ID: comp_id}.

    previous({comp_id: 최종 보드 유닛})가 있으면 최종 보드 Jaccard 최대이고 threshold 이상인 이전 comp_id를 재사용한다
    (1:1, Jaccard 높은 쌍부터 탐욕 배정). 나머지는 name_string slug, 충돌 시 `~2`, `~3` 접미사.
    """
    out: dict[str, str] = {}
    used: set[str] = set()
    if previous:
        pairs = sorted(((jaccard(split_ids(c["units_string"]), units), cid, pid)
                        for cid, c in clusters.items() for pid, units in previous.items()), reverse=True)
        for j, cid, pid in pairs:
            if j < threshold:
                break
            if cid not in out and pid not in used:
                out[cid] = pid
                used.add(pid)
    for cid in sorted(clusters):
        if cid in out:
            continue
        base = comp_id_from_name(headline_ids(clusters[cid]))
        cand, n = base, 1
        while cand in used:
            n += 1
            cand = f"{base}~{n}"
        out[cid] = cand
        used.add(cand)
    return out


def display_name(ids: Iterable[str], static: StaticData, lang: str = "ko") -> str:
    """헤드라인 ID들 -> 표시 이름(정적 이름, 없으면 ID)."""
    parts = []
    for i in ids:
        kind = static.kind_of(i)
        rec = static.get(kind, i) if kind else None
        parts.append((rec or {}).get(f"name_{lang}") or i)
    return " ".join(parts)


# --------------------------------------------------------------------------- R13 덱별 증강 등급


def augment_tier_title_units(title: str, static: StaticData) -> list[str]:
    """"XAYAH & KAYLE > Solar > ..." -> 첫 구간 챔피언 ID들(상점 풀 영어 이름 대소문자 무시)."""
    head = (title or "").split(">")[0]
    out = []
    for n in re.split(r"[&/]", head):
        rec = static.champion_by_name(n.strip())
        if rec:
            out.append(rec["apiName"])
    return out


def accept_comp_augment_tiers(entry: Mapping[str, Any], final_units: Iterable[str], static: StaticData,
                              max_distance: float = AUG_TIER_MAX_DISTANCE) -> tuple[bool, str]:
    """R13: (채택 여부, 사유). distance만으로는 구분이 안 된다(관측: 일치 0.17~0.50, 불일치 0.43~0.50)."""
    d = float(entry.get("distance") or 0)
    if d > max_distance:
        return False, f"distance {d:.3f} > {max_distance}"
    heads = augment_tier_title_units(entry.get("source_title") or "", static)
    if not heads:
        return False, "title champion not resolved"
    if not set(heads) & set(final_units):
        return False, f"title champions {heads} not in final board"
    return True, "ok"


# --------------------------------------------------------------------------- R3 아이템 조건부 / 유닛+아이템


def item_conditional(item_names: Iterable[Mapping[str, Any]], static: StaticData,
                     unmapped: set[str] | None = None) -> dict[str, PlacementStats]:
    out = {}
    for it in item_names or []:
        iid = it["itemNames"]
        if static.get("items", iid) is None:
            if unmapped is not None:
                unmapped.add(iid)
            continue
        out[iid] = PlacementStats(avg_place=it.get("avg"), games=it.get("count"))
    return out


def unit_item_rows(item_names: Iterable[Mapping[str, Any]], prov: Mapping[str, Any], comp_id: str | None = None,
                   builds: Iterable[Mapping[str, Any]] | None = None) -> list[UnitItemStats]:
    """R3: 덱 한정 UnitItemStats(comp_id 채움).

    - itemNames[].units[] -> 유닛 + 아이템 1개. `units` 키가 없는 행(관측 11/137)은 건너뛴다.
    - builds[] -> 유닛 + 아이템 1~3개 조합(buildName). 빈 buildName은 건너뛴다.
    """
    rows = []
    for it in item_names or []:
        for u in it.get("units") or []:
            rows.append(UnitItemStats(unit_id=u["units"], comp_id=comp_id, item_ids=[it["itemNames"]],
                                      place_change=u.get("place_change"), avg_place=u.get("avg"),
                                      games=u.get("count"), **prov))
    for b in builds or []:
        items = list(b.get("buildName") or [])
        if not items or len(items) > 3 or not b.get("unit"):
            continue
        rows.append(UnitItemStats(unit_id=b["unit"], comp_id=comp_id, item_ids=items,
                                  place_change=b.get("place_change"), avg_place=b.get("avg"),
                                  games=b.get("count"), **prov))
    return rows


def item_usage(build_items: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    """R15: 덱당 평균 보유 개수(1 초과 가능)."""
    return {k: float(v.get("pcnt") or 0.0) for k, v in (build_items or {}).items()}


# --------------------------------------------------------------------------- 덱 1개 조립


def convert_comp(cluster_id: str, cd: Mapping[str, Any], det: Mapping[str, Any] | None, static: StaticData,
                 comp_id: str, prov: Mapping[str, Any], unmapped: set[str] | None = None) -> tuple[CompStats, dict]:
    """comps_data 1덱 + comp_details -> (CompStats, extra). extra: 덱 한정 UnitItemStats 목록(comp_id 채움)."""
    det = det or {}
    final_units = split_ids(cd["units_string"])
    us = unit_stats_index(det.get("unit_stats"))
    builds = det.get("builds") or cd.get("builds") or []
    carry = pick_carry(final_units, us, builds, static)
    board, roles = [], {}
    for u in final_units:
        items = pick_bis(builds, u)
        roles[u] = unit_role(u, carry, items or representative_items(builds, u), static)
        board.append(CompUnit(id=u, items=items, star=star_of(us.get(u)), is_core=is_core(us.get(u)), role=roles[u]))
    comp = CompStats(
        comp_id=comp_id,
        name=display_name(headline_ids(cd), static, "ko"),
        name_en=display_name(headline_ids(cd), static, "en"),
        source_cluster_id=str(cluster_id),
        final_board=board,
        carry=carry,
        carry_bis_items=pick_bis(builds, carry) if carry else [],
        key_traits=key_traits(cd.get("traits_string") or "", static, unmapped),
        buildup=buildup(det.get("early_options") or {}, det.get("options") or {}),
        level_timing=level_timing(det.get("levels") or []),
        item_conditional=item_conditional(det.get("itemNames") or [], static, unmapped),
        levelling=cd.get("levelling"),
        item_usage=item_usage(cd.get("build_items") or {}),
        avg_place=cd["overall"]["avg"], games=cd["overall"]["count"],
        **prov,
    )
    extra = {"unit_item": unit_item_rows(det.get("itemNames") or [], prov, comp_id, det.get("builds")),
             "has_details": bool(det)}
    return comp, extra


# --------------------------------------------------------------------------- 스냅샷 전체


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def build_all(raw: Path, static: StaticData | None = None, previous: Mapping[str, list[str]] | None = None) -> dict:
    """raw 디렉터리 전체 변환. 반환: comps/extras/augment_tiers/unit_stats/report."""
    static = static or load_static(18)
    patch = patch_string(_load(raw / "patch.json")) if (raw / "patch.json").is_file() else None
    units_raw = _load(raw / "units.json") if (raw / "units.json").is_file() else None
    rank = normalize_rank_filter((units_raw or {}).get("filter_adjustment", {}).get("rank_filter"))
    fetched = dt.datetime.fromtimestamp((raw / "comps_data.json").stat().st_mtime, dt.UTC)
    prov = {"source": StatSource.METATFT, "patch": patch, "rank_filter": rank, "fetched_at": fetched}
    comp_prov = {**prov, "rank_filter": None}   # comps_data/comp_details는 rank 파라미터 없이 호출(전 티어 추정)

    clusters = _load(raw / "comps_data.json")["results"]["data"]["cluster_details"]
    ids = assign_comp_ids(clusters, previous)
    unmapped: set[str] = set()
    comps, extras, missing_details = [], {}, []
    for cid in sorted(clusters):
        p = raw / f"comp_details_{cid}.json"
        det = _load(p)["results"] if p.is_file() else None
        if det is None:
            missing_details.append(cid)
        comp, extra = convert_comp(cid, clusters[cid], det, static, ids[cid], comp_prov, unmapped)
        comps.append(comp)
        extras[comp.comp_id] = extra

    tiers: list[AugmentTier] = []
    aug_unmapped: set[str] = set()
    if (raw / "augments_tiers.json").is_file():
        tl = _load(raw / "augments_tiers.json")["content"]["content"]["tierList"]
        for t in tl:
            for x in t["content"]:
                if static.get("augments", x["id"]) is None:
                    aug_unmapped.add(x["id"])
                tiers.append(AugmentTier(augment_id=x["id"], tier=t["label"], source_kind="editorial",
                                         source=StatSource.METATFT, patch=patch, fetched_at=fetched))
    cat_report = {}
    if (raw / "comp_augment_tiers.json").is_file():
        cat = _load(raw / "comp_augment_tiers.json")["results"]
        for cid, v in cat.items():
            if cid not in clusters:
                cat_report[cid] = "cluster not in comps_data"
                continue
            ok, why = accept_comp_augment_tiers(v, split_ids(clusters[cid]["units_string"]), static)
            cat_report[cid] = why
            if not ok:
                continue
            for a in v["augments"]:
                if static.get("augments", a["id"]) is None:
                    aug_unmapped.add(a["id"])
                tiers.append(AugmentTier(augment_id=a["id"], tier=a["tier"], source_kind="editorial",
                                         source=StatSource.METATFT, patch=patch, fetched_at=fetched,
                                         comp_id=ids[cid], source_title=v.get("source_title")))
    unit_rows = []
    for r in (units_raw or {}).get("results") or []:
        unit_rows.append(UnitStats(unit_id=r["unit"], **placement_from_places(r["places"]).model_dump(), **prov))

    usage = [x for c in comps for x in c.item_usage.values()]
    report = {
        "patch": patch, "rank_filter_units": rank, "clusters": len(clusters),
        "with_details": len(clusters) - len(missing_details), "missing_details": missing_details,
        "comp_augment_tiers": {"total": len(cat_report), "accepted": sum(v == "ok" for v in cat_report.values()),
                               "decisions": cat_report},
        "item_usage_range": [min(usage), max(usage)] if usage else None,
        "item_usage_gt1": sum(x > 1 for x in usage),
        "unmapped_ids": sorted(unmapped), "unmapped_augments": sorted(aug_unmapped),
        "carry_none": [c.comp_id for c in comps if c.carry is None],
    }
    return {"comps": comps, "extras": extras, "augment_tiers": tiers, "unit_stats": unit_rows,
            "comp_ids": ids, "report": report}


def dump(result: dict, out_dir: Path) -> Path:
    """JSON 1파일(data/stats/metatft_{patch}.json). SQLite 적재는 app 쪽 로더가 이 파일을 읽어 한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    patch = result["report"]["patch"] or "unknown"
    p = out_dir / f"metatft_{patch}.json"
    doc = {
        "report": result["report"],
        "comp_ids": result["comp_ids"],
        "comps": [c.model_dump(mode="json") for c in result["comps"]],
        "unit_item_stats": [r.model_dump(mode="json", exclude_none=True)
                            for v in result["extras"].values() for r in v["unit_item"]],
        "augment_tiers": [t.model_dump(mode="json", exclude_none=True) for t in result["augment_tiers"]],
        "unit_stats": [u.model_dump(mode="json", exclude_none=True) for u in result["unit_stats"]],
    }
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=0), encoding="utf-8")
    return p


def main() -> None:
    from tft_advisor.stats.collectors.metatft import latest_raw_dir

    ap = argparse.ArgumentParser(description="MetaTFT 원본 -> 계약 모델 JSON")
    ap.add_argument("--raw", default=None, help="기본: data/raw/metatft/ 최신 날짜")
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "stats"))
    ap.add_argument("--previous", default=None, help="이전 metatft_*.json(comp_id 재사용용)")
    a = ap.parse_args()
    raw = Path(a.raw) if a.raw else latest_raw_dir()
    if raw is None:
        raise SystemExit("원본 없음: python -m tft_advisor.stats.collectors.metatft 먼저 실행")
    prev = None
    if a.previous:
        d = _load(Path(a.previous))
        prev = {c["comp_id"]: [u["id"] for u in c["final_board"]] for c in d["comps"]}
    res = build_all(raw, previous=prev)
    p = dump(res, Path(a.out))
    r = dict(res["report"])
    r["comp_augment_tiers"] = {k: v for k, v in r["comp_augment_tiers"].items() if k != "decisions"}
    print(p)
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
