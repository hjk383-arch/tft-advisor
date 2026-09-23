"""통계 조회 API — advisor가 런타임에 쓰는 유일한 stats 진입점. 소유: stats-researcher.

사용
    from tft_advisor.stats.repository import open_repository, StatsRepository
    repo = open_repository()                  # settings.stats.db_path(SQLite)의 최신 metatft 스냅샷
    repo.comps(min_games=1000)                # CompStats 목록(avg_place 오름차순)
    repo.augment_tier_for("DA_PandorasBench", "juggernaut-zyra-amumu")   # 덱별 등급 → 없으면 전체 등급
    repo.unit_item_stat("DA_18_Zyra", "DA_ArchangelsStaff", comp_id=c.comp_id, fallback_overall=True)
    repo.is_craftable("DA_Artifact_Dawncore")  # False (유물)
    repo.shop_odds(7)                          # [16, 30, 43, 10, 1]

설계 원칙
- 로드 후 모든 조회는 메모리 dict 조회(I/O 없음). 반환 모델은 계약(contracts.py) 타입이고 공유 객체이므로 수정 금지.
- 조회 실패는 예외가 아니라 None / 빈 컬렉션(정적 데이터·통계는 불완전할 수 있다). 예외: shop_odds(범위 밖 레벨).
- `advisor`는 `StatsRepository` Protocol에만 의존한다. 테스트용 가짜 구현은 `InMemoryStatsRepository.from_doc`
  (변환 산출물 dict 또는 그 부분집합)로 만든다.

통계 의미(세부 근거: `_workspace/03_stats-researcher_fixes.md`, `04_stats-researcher_impl.md`)
- CompStats/AugmentTier(덱별)/UnitItemStats(덱 한정)는 comps_data 기준 **전 티어**, UnitStats/item_stats는
  **마스터 이상**(`meta.rank_filter_units`). 두 표본을 섞어 비교하지 말 것.
- unit_item_stat(unit, item, comp_id) = "그 덱에서 unit이 item을 (다른 아이템과 함께) 든 판"(MetaTFT itemNames).
  unit_builds(unit, comp_id) = "아이템 구성이 정확히 이 1~3개인 판"(MetaTFT builds).
  comp_id=None(전체) 행은 소스에 없어서 덱 한정 "holds" 행을 games 가중 평균한 **파생값**이다.
- AugmentTier는 Set 18에서 편집자 등급(source_kind="editorial", games=None)뿐이다.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from tft_advisor.contracts import (
    AugmentId,
    AugmentTier,
    ChampionId,
    CompStats,
    ItemCategory,
    ItemId,
    PlacementStats,
    StatSource,
    TraitId,
    UnitItemStats,
    UnitStats,
)
from tft_advisor.static_data import PROJECT_ROOT, StaticData, load_static

Record = dict[str, Any]

CRAFTABLE_CATEGORIES: frozenset[str] = frozenset({"completed", "emblem", "tactician"})
"""조합표(재료 2개)로 만들 수 있는 카테고리. 실제 판정은 `composition`이 재료 2개인지까지 본다."""
NON_CRAFTABLE_CATEGORIES: frozenset[str] = frozenset({"artifact", "radiant", "consumable", "assist_reward", "other"})
"""유물·찬란한 아이템 등: 보유하면 쓰지만 조합으로 목표할 수 없다(QA recheck 03 N5a)."""


@dataclass(frozen=True)
class StatsMeta:
    """로드된 스냅샷의 출처 정보."""

    source: StatSource
    patch: str | None
    set_number: int
    fetched_at: datetime | None = None
    built_at: datetime | None = None
    rank_filter_units: str | None = None   # UnitStats/item_stats 표본의 티어 필터(마스터+)
    rank_filter_comps: str | None = None   # comps_data/comp_details는 None(전 티어)
    snapshot_id: int | None = None         # SQLite 스냅샷 id(JSON/메모리 로드면 None)
    counts: dict[str, int] = field(default_factory=dict)


@runtime_checkable
class StatsRepository(Protocol):
    """읽기 전용·메모리 상주 통계 + 정적 데이터 조회."""

    meta: StatsMeta

    # ---- 덱 -------------------------------------------------------------------------------------
    def comps(self, *, min_games: int | None = None) -> list[CompStats]:
        """전체 덱. avg_place 오름차순(None은 뒤), 같으면 games 내림차순. min_games 미만 제외."""
        ...

    def comp(self, comp_id: str) -> CompStats | None: ...

    def comp_by_cluster(self, cluster_id: str) -> CompStats | None:
        """MetaTFT 덱(클러스터) ID(예 "424000") → CompStats."""
        ...

    # ---- 증강 등급 (Set 18: 편집자 등급뿐) --------------------------------------------------------
    def augment_tier(self, augment_id: AugmentId, comp_id: str | None = None) -> AugmentTier | None:
        """정확한 범위만: comp_id=None → 전체 등급, comp_id → 그 덱 등급(없으면 None, 폴백 없음)."""
        ...

    def augment_tier_for(self, augment_id: AugmentId, comp_id: str | None) -> AugmentTier | None:
        """설계 ed(a)/t(a,c): 덱별 등급 → 전체 등급 → None(= 미평가, advisor가 중립값)."""
        ...

    def augment_tiers(self, comp_id: str | None = None) -> dict[AugmentId, AugmentTier]:
        """범위 하나의 전체 등급표(comp_id=None → 전체)."""
        ...

    def comps_with_augment_tiers(self) -> frozenset[str]:
        """덱별 등급이 있는 comp_id(현재 32/57)."""
        ...

    # ---- 유닛 / 유닛+아이템 / 아이템 --------------------------------------------------------------
    def unit_stats(self, unit_id: ChampionId) -> UnitStats | None: ...

    def all_unit_stats(self) -> dict[ChampionId, UnitStats]: ...

    def unit_item_stat(self, unit_id: ChampionId, item_id: ItemId, comp_id: str | None = None, *,
                       fallback_overall: bool = False) -> UnitItemStats | None:
        """unit이 item을 든 판의 성적(place_change 음수 = 좋음). comp_id=None → 파생 전체값.
        fallback_overall=True면 덱 한정 행이 없을 때 전체값."""
        ...

    def unit_item_stats(self, unit_id: ChampionId, comp_id: str | None = None) -> list[UnitItemStats]:
        """unit의 아이템 1개 행 전부(games 내림차순). comp_id=None → 파생 전체값."""
        ...

    def unit_builds(self, unit_id: ChampionId, comp_id: str) -> list[UnitItemStats]:
        """덱 한정 정확한 1~3아이템 구성 행(games 내림차순)."""
        ...

    def item_stats(self, item_id: ItemId) -> PlacementStats | None:
        """아이템 전체 등수 통계(마스터+, 보유 유닛 무관)."""
        ...

    # ---- 정적 데이터 --------------------------------------------------------------------------------
    def champion(self, champion_id: ChampionId) -> Record | None: ...

    def is_champion(self, unit_id: str) -> bool:
        """상점 풀 챔피언인지(소환물·허수아비·크립은 False)."""
        ...

    def champion_cost(self, champion_id: ChampionId) -> int | None: ...

    def champion_traits(self, champion_id: ChampionId) -> list[TraitId]: ...

    def trait(self, trait_id: TraitId) -> Record | None: ...

    def trait_breakpoints(self, trait_id: TraitId) -> list[int]: ...

    def item(self, item_id: ItemId) -> Record | None: ...

    def item_category(self, item_id: ItemId) -> ItemCategory | None:
        """items.json category. 정적 데이터에 없는 ID(예 DA_Artifact_Hullcrusher)는 None."""
        ...

    def is_component(self, item_id: ItemId) -> bool: ...

    def is_craftable(self, item_id: ItemId) -> bool:
        """재료 2개 조합으로 만들 수 있는가(DA_*·set_native의 completed/emblem/tactician 중 composition 2개).
        유물·찬란한·레거시 TFT_Item_*=False."""
        ...

    def recipe(self, item_id: ItemId) -> tuple[ItemId, ItemId] | None:
        """조합 재료 2개(정렬). 조합 불가면 None. `craft`와 같은 범위(DA_*·set_native)라 craft(*recipe(x)) == x."""
        ...

    def craft(self, component_a: ItemId, component_b: ItemId) -> ItemId | None:
        """재료 2개(순서 무관) → 결과 아이템(DA_*·set_native만)."""
        ...

    def components(self) -> list[ItemId]:
        """현 세트 재료 아이템 ID(DA_Component_*)."""
        ...

    def emblem_trait(self, item_id: ItemId) -> TraitId | None:
        """상징 아이템 → 특성 ID."""
        ...

    def augment(self, augment_id: AugmentId) -> Record | None: ...

    def augment_traits(self, augment_id: AugmentId) -> list[TraitId]: ...

    def shop_special(self, special_id: str) -> Record | None: ...

    def shop_odds(self, level: int) -> list[int]:
        """레벨별 상점 확률(%) [1~5코스트]. 1~10 밖이면 ValueError."""
        ...

    def name(self, api_id: str, lang: str = "ko") -> str | None:
        """아무 종류 ID → 표시 이름(lang "ko" | "en")."""
        ...


# =================================================================================================
# 구현
# =================================================================================================


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime) or v is None:
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _sort_key_comp(c: CompStats) -> tuple:
    return (c.avg_place is None, c.avg_place or 0.0, -(c.games or 0), c.comp_id)


class _StaticView:
    """StaticData 위의 파생 조회표(조합표, 상징→특성, 카테고리). StatsRepository의 정적 부분 구현."""

    def __init__(self, static: StaticData) -> None:
        self.static = static
        self._recipes: dict[str, tuple[str, str]] = {}
        self._craft: dict[tuple[str, str], str] = {}
        comps = {r["apiName"] for r in static.tables["items"] if r.get("category") == "component"}
        for r in static.tables["items"]:
            comp = r.get("composition") or []
            # recipe/is_craftable/craft 모두 현 세트(DA_*·set_native)만 대상으로 한다(QA 04 W4). 레거시 TFT_Item_* 39개는
            # 레거시 재료(TFT_Item_BFSword 등)로 조합돼 현 세트 재료로 만들 수 없고 통계에도 나오지 않는다 → 조합 불가(None).
            if (r.get("category") in CRAFTABLE_CATEGORIES and len(comp) == 2 and all(c in comps for c in comp)
                    and r["apiName"].startswith("DA_") and r.get("set_native")):
                key = tuple(sorted(comp))
                self._recipes[r["apiName"]] = key  # type: ignore[assignment]
                prev = self._craft.get(key)  # type: ignore[arg-type]
                if prev is None or r["apiName"] < prev:   # 결정적(중복은 현재 0건)
                    self._craft[key] = r["apiName"]  # type: ignore[index]
        self._components = sorted(c for c in comps if c.startswith("DA_"))
        trait_by_en = {t.get("name_en"): t["apiName"] for t in static.tables["traits"] if t.get("name_en")}
        self._emblem_trait: dict[str, str] = {}
        for r in static.tables["items"]:
            if r.get("category") == "emblem":
                t = trait_by_en.get((r.get("name_en") or "").removesuffix(" Emblem").strip())
                if t:
                    self._emblem_trait[r["apiName"]] = t
        self._odds = {int(k): list(v) for k, v in static.meta.get("shop_odds_pct", {}).items()}

    def champion(self, champion_id: str) -> Record | None:
        return self.static.get("champions", champion_id)

    def is_champion(self, unit_id: str) -> bool:
        r = self.champion(unit_id)
        return bool(r and r.get("shop_pool"))

    def champion_cost(self, champion_id: str) -> int | None:
        r = self.champion(champion_id)
        return r.get("cost") if r else None

    def champion_traits(self, champion_id: str) -> list[str]:
        r = self.champion(champion_id)
        return list(r.get("traits") or []) if r else []

    def trait(self, trait_id: str) -> Record | None:
        return self.static.get("traits", trait_id)

    def trait_breakpoints(self, trait_id: str) -> list[int]:
        r = self.trait(trait_id)
        return [b for b in (r.get("breakpoints") or []) if b is not None] if r else []

    def item(self, item_id: str) -> Record | None:
        return self.static.get("items", item_id)

    def item_category(self, item_id: str) -> ItemCategory | None:
        r = self.item(item_id)
        return r.get("category") if r else None

    def is_component(self, item_id: str) -> bool:
        return self.item_category(item_id) == "component"

    def is_craftable(self, item_id: str) -> bool:
        return item_id in self._recipes

    def recipe(self, item_id: str) -> tuple[str, str] | None:
        return self._recipes.get(item_id)

    def craft(self, component_a: str, component_b: str) -> str | None:
        return self._craft.get(tuple(sorted((component_a, component_b))))  # type: ignore[arg-type]

    def components(self) -> list[str]:
        return list(self._components)

    def emblem_trait(self, item_id: str) -> str | None:
        return self._emblem_trait.get(item_id)

    def augment(self, augment_id: str) -> Record | None:
        return self.static.get("augments", augment_id)

    def augment_traits(self, augment_id: str) -> list[str]:
        r = self.augment(augment_id)
        return list(r.get("associated_traits") or []) if r else []

    def shop_special(self, special_id: str) -> Record | None:
        return self.static.get("shop_specials", special_id)

    def shop_odds(self, level: int) -> list[int]:
        if level not in self._odds:
            raise ValueError(f"상점 확률 없는 레벨: {level} (1~10)")
        return list(self._odds[level])

    def name(self, api_id: str, lang: str = "ko") -> str | None:
        kind = self.static.kind_of(api_id)
        return self.static.get(kind, api_id).get(f"name_{lang}") if kind else None  # type: ignore[union-attr]


class InMemoryStatsRepository(_StaticView):
    """StatsRepository 구현. 스냅샷 dict(변환 산출물/SQLite 읽은 값) 하나를 메모리에 올린다."""

    def __init__(self, *, comps: Iterable[CompStats], augment_tiers: Iterable[AugmentTier] = (),
                 unit_stats: Iterable[UnitStats] = (), unit_item_rows: Iterable[Mapping[str, Any]] = (),
                 unit_build_rows: Iterable[Mapping[str, Any]] = (), item_stats: Mapping[str, PlacementStats] | None = None,
                 static: StaticData | None = None, meta: StatsMeta | None = None, preload: bool = True) -> None:
        """preload=True(기본): 유닛+아이템 행을 로드 시점에 모두 검증·색인한다(+~0.4s, 이후 첫 조회 지연 없음).
        False면 유닛별로 첫 조회 때 한다(테스트·일회성 스크립트용)."""
        super().__init__(static or load_static(18))
        self._comps = {c.comp_id: c for c in comps}
        self._comp_list = sorted(self._comps.values(), key=_sort_key_comp)
        self._by_cluster = {c.source_cluster_id: c for c in self._comps.values() if c.source_cluster_id}
        self._aug: dict[str | None, dict[str, AugmentTier]] = defaultdict(dict)
        for t in augment_tiers:
            self._aug[t.comp_id][t.augment_id] = t
        self._units = {u.unit_id: u for u in unit_stats}
        self._item_stats = dict(item_stats or {})
        # 유닛+아이템: 원본 dict를 unit_id로 묶어 두고 처음 조회할 때 검증(로드 시간 절약)
        self._raw_holds: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in unit_item_rows:
            self._raw_holds[r["unit_id"]].append(r)
        self._raw_builds: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in unit_build_rows:
            self._raw_builds[r["unit_id"]].append(r)
        self._holds_cache: dict[str, dict[str | None, dict[str, UnitItemStats]]] = {}
        self._builds_cache: dict[str, dict[str | None, list[UnitItemStats]]] = {}
        if preload:
            for u in list(self._raw_holds):
                self._holds(u)
            for u in list(self._raw_builds):
                self._builds(u)
        any_comp = next(iter(self._comps.values()), None)
        self.meta = meta or StatsMeta(
            source=any_comp.source if any_comp else StatSource.METATFT,
            patch=any_comp.patch if any_comp else None, set_number=self.static.set_number,
            fetched_at=any_comp.fetched_at if any_comp else None)
        if not self.meta.counts:
            self.meta.counts.update({
                "comps": len(self._comps), "augment_tiers": sum(len(v) for v in self._aug.values()),
                "unit_stats": len(self._units),
                "unit_item_stats": sum(len(v) for v in self._raw_holds.values()),
                "unit_build_stats": sum(len(v) for v in self._raw_builds.values()),
                "item_stats": len(self._item_stats)})

    # ---- 생성 -------------------------------------------------------------------------------------
    @classmethod
    def from_doc(cls, doc: Mapping[str, Any], static: StaticData | None = None,
                 meta: StatsMeta | None = None, preload: bool = True) -> InMemoryStatsRepository:
        """`metatft_convert.dump` 형식 dict(또는 그 부분집합: comps만 있어도 됨) → 저장소."""
        comps = [CompStats.model_validate(c) for c in doc.get("comps", [])]
        report = doc.get("report") or {}
        if meta is None:
            snap = doc.get("snapshot")
            first = comps[0] if comps else None
            meta = StatsMeta(
                source=StatSource(getattr(snap, "source", None) or (first.source if first else "metatft")),
                patch=report.get("patch") or (first.patch if first else None),
                set_number=(static.set_number if static else getattr(snap, "set_number", 18)),
                fetched_at=_dt(report.get("fetched_at")) or (first.fetched_at if first else None),
                built_at=_dt(getattr(snap, "built_at", None)),
                rank_filter_units=report.get("rank_filter_units"),
                rank_filter_comps=None,
                snapshot_id=getattr(snap, "id", None))
        return cls(
            comps=comps,
            augment_tiers=[AugmentTier.model_validate(t) for t in doc.get("augment_tiers", [])],
            unit_stats=[UnitStats.model_validate(u) for u in doc.get("unit_stats", [])],
            unit_item_rows=doc.get("unit_item_stats", []),
            unit_build_rows=doc.get("unit_build_stats", []),
            item_stats={r["item_id"]: PlacementStats.model_validate({k: v for k, v in r.items() if k != "item_id"})
                        for r in doc.get("item_stats", [])},
            static=static, meta=meta, preload=preload)

    @classmethod
    def from_json(cls, path: Path, static: StaticData | None = None) -> InMemoryStatsRepository:
        return cls.from_doc(json.loads(Path(path).read_text(encoding="utf-8")), static)

    # ---- 덱 -------------------------------------------------------------------------------------
    def comps(self, *, min_games: int | None = None) -> list[CompStats]:
        if min_games is None:
            return list(self._comp_list)
        return [c for c in self._comp_list if (c.games or 0) >= min_games]

    def comp(self, comp_id: str) -> CompStats | None:
        return self._comps.get(comp_id)

    def comp_by_cluster(self, cluster_id: str) -> CompStats | None:
        return self._by_cluster.get(str(cluster_id))

    # ---- 증강 -------------------------------------------------------------------------------------
    def augment_tier(self, augment_id: str, comp_id: str | None = None) -> AugmentTier | None:
        return self._aug.get(comp_id, {}).get(augment_id)

    def augment_tier_for(self, augment_id: str, comp_id: str | None) -> AugmentTier | None:
        if comp_id is not None:
            t = self.augment_tier(augment_id, comp_id)
            if t is not None:
                return t
        return self.augment_tier(augment_id, None)

    def augment_tiers(self, comp_id: str | None = None) -> dict[str, AugmentTier]:
        return dict(self._aug.get(comp_id, {}))

    def comps_with_augment_tiers(self) -> frozenset[str]:
        return frozenset(k for k, v in self._aug.items() if k is not None and v)

    # ---- 유닛 -------------------------------------------------------------------------------------
    def unit_stats(self, unit_id: str) -> UnitStats | None:
        return self._units.get(unit_id)

    def all_unit_stats(self) -> dict[str, UnitStats]:
        return dict(self._units)

    def _holds(self, unit_id: str) -> dict[str | None, dict[str, UnitItemStats]]:
        got = self._holds_cache.get(unit_id)
        if got is not None:
            return got
        by_comp: dict[str | None, dict[str, UnitItemStats]] = defaultdict(dict)
        agg: dict[str, list[UnitItemStats]] = defaultdict(list)
        for r in self._raw_holds.get(unit_id, []):
            m = UnitItemStats.model_validate(r)
            item = m.item_ids[0]
            prev = by_comp[m.comp_id].get(item)
            if prev is None or (m.games or 0) > (prev.games or 0):
                by_comp[m.comp_id][item] = m
            if m.comp_id is not None:
                agg[item].append(m)
        for item, rows in agg.items():
            if item not in by_comp[None]:
                by_comp[None][item] = _aggregate(unit_id, item, rows)
        self._holds_cache[unit_id] = got = dict(by_comp)
        return got

    def unit_item_stat(self, unit_id: str, item_id: str, comp_id: str | None = None, *,
                       fallback_overall: bool = False) -> UnitItemStats | None:
        h = self._holds(unit_id)
        row = h.get(comp_id, {}).get(item_id)
        if row is None and fallback_overall and comp_id is not None:
            row = h.get(None, {}).get(item_id)
        return row

    def unit_item_stats(self, unit_id: str, comp_id: str | None = None) -> list[UnitItemStats]:
        return sorted(self._holds(unit_id).get(comp_id, {}).values(), key=lambda r: (-(r.games or 0), r.item_ids))

    def _builds(self, unit_id: str) -> dict[str | None, list[UnitItemStats]]:
        cache = self._builds_cache.get(unit_id)
        if cache is None:
            cache = defaultdict(list)
            for r in self._raw_builds.get(unit_id, []):
                m = UnitItemStats.model_validate(r)
                cache[m.comp_id].append(m)
            for rows in cache.values():
                rows.sort(key=lambda r: (-(r.games or 0), r.item_ids))
            self._builds_cache[unit_id] = cache = dict(cache)
        return cache

    def unit_builds(self, unit_id: str, comp_id: str) -> list[UnitItemStats]:
        return list(self._builds(unit_id).get(comp_id, []))

    def item_stats(self, item_id: str) -> PlacementStats | None:
        return self._item_stats.get(item_id)


def _aggregate(unit_id: str, item_id: str, rows: list[UnitItemStats]) -> UnitItemStats:
    """덱 한정 "holds" 행들 → 전체(comp_id=None) 파생 행. avg_place·place_change는 games 가중 평균."""
    g = sum(r.games or 0 for r in rows)

    def wavg(attr: str) -> float | None:
        pairs = [(getattr(r, attr), r.games or 0) for r in rows if getattr(r, attr) is not None and r.games]
        w = sum(n for _, n in pairs)
        return round(sum(v * n for v, n in pairs) / w, 4) if w else None

    first = rows[0]
    avg = wavg("avg_place")
    return UnitItemStats(unit_id=unit_id, item_ids=[item_id], comp_id=None, games=g,
                         avg_place=min(8.0, max(1.0, avg)) if avg is not None else None,
                         place_change=wavg("place_change"), source=first.source, patch=first.patch,
                         rank_filter=first.rank_filter, fetched_at=first.fetched_at)


# =================================================================================================
# 열기
# =================================================================================================


class StatsNotFound(FileNotFoundError):
    """DB/JSON 어디에도 스냅샷이 없다(→ `python -m tft_advisor.stats refresh` 필요)."""


def default_db_path() -> Path:
    from tft_advisor.config import load_settings

    p = Path(load_settings().stats.db_path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def latest_json(stats_dir: Path | None = None, source: str = "metatft") -> Path | None:
    """최신 `{source}_{패치}.json`: 패치 번호를 숫자로 비교하고(`18.10` > `18.9`), 같은 패치끼리만 수정 시각으로 가른다.

    수정 시각만 보면 git checkout·복사 뒤 오래된 패치가 최신으로 뽑힌다(08 stats §3).
    """
    from tft_advisor.patch_version import latest_snapshot

    return latest_snapshot(stats_dir or PROJECT_ROOT / "data" / "stats", f"{source}_")


def open_repository(*, db_path: Path | None = None, source: str = "metatft", patch: str | None = None,
                    static: StaticData | None = None, allow_json_fallback: bool = True) -> InMemoryStatsRepository:
    """최신 스냅샷을 메모리에 올린다. DB는 읽기 전용(`mode=ro`)으로만 연다 — 쓰지 않으며 refresh와 경합하지 않는다.

    1순위 SQLite(`db_path`, 기본 settings.stats.db_path)의 (source, patch) 최신 스냅샷.
    DB가 없으면(`allow_json_fallback`) data/stats/{source}_*.json 최신 파일. 둘 다 없으면 StatsNotFound.
    프로세스당 1회 호출하고 결과를 재사용할 것(로드 ~0.5s).
    """
    from tft_advisor.stats import db as statsdb

    path = db_path or default_db_path()
    for _ in range(3):   # 읽기 전용. find→read 사이에 동시 refresh가 보존 정리로 지우면 다시 찾는다
        snap = statsdb.find_snapshot(path, source=source, patch=patch) if path.is_file() else None
        if snap is None:
            break
        try:
            return InMemoryStatsRepository.from_doc(statsdb.read_snapshot(path, snap.id), static)
        except KeyError:
            continue
    if allow_json_fallback:
        js = latest_json(path.parent, source) or latest_json(None, source)
        if js is not None:
            doc = json.loads(js.read_text(encoding="utf-8"))
            if patch is None or (doc.get("report") or {}).get("patch") == patch:
                return InMemoryStatsRepository.from_doc(doc, static)
    raise StatsNotFound(f"통계 스냅샷 없음(db={path}, source={source}, patch={patch}). "
                        "실행: python -m tft_advisor.stats refresh")
