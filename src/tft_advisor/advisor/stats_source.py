"""advisor가 쓰는 통계·정적 데이터 조회 인터페이스와 교체 가능한 어댑터.

- `AdvisorStats`: advisor가 실제로 호출하는 메서드만 모은 Protocol. 시그니처는
  `tft_advisor.stats.repository.StatsRepository`(stats-researcher 소유)의 부분집합이라,
  그쪽 구현체를 그대로 넘겨도 된다.
- `JsonStatsAdapter`: `data/stats/metatft_*.json` + `data/static/{set}/`를 직접 읽는 대체 구현.
  stats 저장소 구현(`open_repository`)이 아직 없을 때와 테스트(mini 통계 파일)에서 쓴다.
- `load_stats()`: 저장소 구현이 있으면 그것을, 없으면 어댑터를 돌려준다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..contracts import AugmentTier, CompStats, UnitItemStats
from ..static_data import PROJECT_ROOT, StaticData, load_static

log = logging.getLogger(__name__)

Record = dict[str, Any]

# 조합표 대상 카테고리(설계 §6-1): 일반 완성템, 상징(뒤집개/프라이팬 조합), 전략가 아이템
_CRAFT_CATEGORIES = frozenset({"completed", "emblem", "tactician"})


@runtime_checkable
class AdvisorStats(Protocol):
    """advisor 런타임 조회 계약(StatsRepository의 부분집합). 모든 조회는 메모리 상주, I/O 없음."""

    def comps(self, *, min_games: int | None = None) -> list[CompStats]: ...
    def comp(self, comp_id: str) -> CompStats | None: ...
    def augment_tier(self, augment_id: str, comp_id: str | None = None) -> AugmentTier | None: ...
    def unit_item_stat(self, unit_id: str, item_id: str, comp_id: str | None = None, *,
                       fallback_overall: bool = False) -> UnitItemStats | None: ...

    def champion(self, champion_id: str) -> Record | None: ...
    def champion_cost(self, champion_id: str) -> int | None: ...
    def champion_traits(self, champion_id: str) -> list[str]: ...
    def trait(self, trait_id: str) -> Record | None: ...
    def trait_breakpoints(self, trait_id: str) -> list[int]: ...
    def item(self, item_id: str) -> Record | None: ...
    def item_category(self, item_id: str) -> str | None: ...
    def recipe(self, item_id: str) -> tuple[str, str] | None: ...
    def craft(self, component_a: str, component_b: str) -> str | None: ...
    def emblem_trait(self, item_id: str) -> str | None: ...
    def augment(self, augment_id: str) -> Record | None: ...
    def augment_traits(self, augment_id: str) -> list[str]: ...
    def shop_special(self, special_id: str) -> Record | None: ...
    def shop_odds(self, level: int) -> list[int]: ...
    def name(self, api_id: str, lang: str = "ko") -> str | None: ...


def default_stats_path(data_dir: Path | None = None) -> Path | None:
    """최신 `data/stats/metatft_*.json`(파일명 사전순 마지막)."""
    d = (data_dir or PROJECT_ROOT / "data") / "stats"
    return max(d.glob("metatft_*.json"), default=None)


@dataclass
class JsonStatsAdapter:
    """변환 산출물 JSON + StaticData 기반 AdvisorStats 구현."""

    static: StaticData
    _comps: list[CompStats]
    _patch: str | None = None
    _aug_tiers: dict[tuple[str, str | None], AugmentTier] = field(default_factory=dict)
    _unit_item: dict[tuple[str, str, str | None], UnitItemStats] = field(default_factory=dict)
    _craft: dict[tuple[str, str], str] = field(default_factory=dict)
    _recipe: dict[str, tuple[str, str]] = field(default_factory=dict)
    _emblem_trait: dict[str, str] = field(default_factory=dict)
    _unit_item_overall: dict[tuple[str, str], UnitItemStats] | None = None

    @classmethod
    def from_file(cls, path: str | Path, static: StaticData | None = None) -> JsonStatsAdapter:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw, static)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], static: StaticData | None = None) -> JsonStatsAdapter:
        static = static or load_static()
        comps = [CompStats.model_validate(c) for c in raw.get("comps", [])]
        self = cls(static=static, _comps=comps, _patch=(raw.get("report") or {}).get("patch"))
        for row in raw.get("augment_tiers", []):
            t = AugmentTier.model_validate(row)
            self._aug_tiers.setdefault((t.augment_id, t.comp_id), t)
        for row in raw.get("unit_item_stats", []):
            if len(row.get("item_ids", [])) != 1:
                continue   # advisor는 단일 아이템 place_change만 쓴다(설계 §6-3)
            u = UnitItemStats.model_validate(row)
            key = (u.unit_id, u.item_ids[0], u.comp_id)
            prev = self._unit_item.get(key)
            if prev is None or (u.games or 0) > (prev.games or 0):
                self._unit_item[key] = u
        self._build_static_indexes()
        return self

    def _build_static_indexes(self) -> None:
        trait_by_name = {(t.get("name_en") or "").lower(): t["apiName"] for t in self.static.tables["traits"]}
        for it in self.static.tables["items"]:
            api = it["apiName"]
            comp = it.get("composition") or []
            if (
                len(comp) == 2
                and it.get("category") in _CRAFT_CATEGORIES
                and api.startswith("DA_")
                and it.get("set_native", True)
            ):
                key = tuple(sorted(comp))
                self._craft.setdefault(key, api)   # type: ignore[arg-type]
                self._recipe[api] = (comp[0], comp[1])
            if it.get("category") == "emblem":
                nm = (it.get("name_en") or "").lower().removesuffix(" emblem").strip()
                if nm in trait_by_name:
                    self._emblem_trait[api] = trait_by_name[nm]

    # ---- 통계 ----
    @property
    def patch(self) -> str | None:
        return self._patch

    def comps(self, *, min_games: int | None = None) -> list[CompStats]:
        if min_games is None:
            return list(self._comps)
        return [c for c in self._comps if (c.games or 0) >= min_games]

    def comp(self, comp_id: str) -> CompStats | None:
        return next((c for c in self._comps if c.comp_id == comp_id), None)

    def augment_tier(self, augment_id: str, comp_id: str | None = None) -> AugmentTier | None:
        return self._aug_tiers.get((augment_id, comp_id))

    def unit_item_stat(self, unit_id: str, item_id: str, comp_id: str | None = None, *,
                       fallback_overall: bool = False) -> UnitItemStats | None:
        """StatsRepository와 같은 의미: comp_id=None → 전체(파일에 없으면 덱 한정 행의 games 가중 파생값).
        fallback_overall=True면 덱 한정 행이 없을 때 전체값."""
        if comp_id is not None:
            row = self._unit_item.get((unit_id, item_id, comp_id))
            if row is not None or not fallback_overall:
                return row
        return self._unit_item.get((unit_id, item_id, None)) or self._overall_item().get((unit_id, item_id))

    def _overall_item(self) -> dict[tuple[str, str], UnitItemStats]:
        if self._unit_item_overall is None:
            groups: dict[tuple[str, str], list[UnitItemStats]] = {}
            for (u, x, c), row in self._unit_item.items():
                if c is not None:
                    groups.setdefault((u, x), []).append(row)
            self._unit_item_overall = {k: _aggregate_rows(k[0], k[1], rows) for k, rows in groups.items()}
        return self._unit_item_overall

    # ---- 정적 ----
    def champion(self, champion_id: str) -> Record | None:
        return self.static.get("champions", champion_id)

    def champion_cost(self, champion_id: str) -> int | None:
        rec = self.champion(champion_id)
        return rec.get("cost") if rec else None

    def champion_traits(self, champion_id: str) -> list[str]:
        rec = self.champion(champion_id)
        return list(rec.get("traits") or []) if rec else []

    def trait(self, trait_id: str) -> Record | None:
        return self.static.get("traits", trait_id)

    def trait_breakpoints(self, trait_id: str) -> list[int]:
        rec = self.trait(trait_id)
        return [b for b in (rec.get("breakpoints") or []) if isinstance(b, int)] if rec else []

    def item(self, item_id: str) -> Record | None:
        return self.static.get("items", item_id)

    def item_category(self, item_id: str) -> str | None:
        rec = self.item(item_id)
        return rec.get("category") if rec else None

    def recipe(self, item_id: str) -> tuple[str, str] | None:
        return self._recipe.get(item_id)

    def craft(self, component_a: str, component_b: str) -> str | None:
        return self._craft.get(tuple(sorted((component_a, component_b))))   # type: ignore[arg-type]

    def emblem_trait(self, item_id: str) -> str | None:
        return self._emblem_trait.get(item_id)

    def augment(self, augment_id: str) -> Record | None:
        return self.static.get("augments", augment_id)

    def augment_traits(self, augment_id: str) -> list[str]:
        rec = self.augment(augment_id)
        return list(rec.get("associated_traits") or []) if rec else []

    def shop_special(self, special_id: str) -> Record | None:
        return self.static.get("shop_specials", special_id)

    def shop_odds(self, level: int) -> list[int]:
        table = self.static.meta.get("shop_odds_pct") or self.static.meta.get("observed_shop_odds_pct") or {}
        return list(table.get(str(level), []))

    def name(self, api_id: str, lang: str = "ko") -> str | None:
        kind = self.static.kind_of(api_id)
        if kind is None:
            return None
        return self.static.get(kind, api_id).get(f"name_{lang}")   # type: ignore[union-attr]


def _aggregate_rows(unit_id: str, item_id: str, rows: list[UnitItemStats]) -> UnitItemStats:
    """덱 한정 행들 → 전체(comp_id=None) 파생 행(games 가중 평균). stats.repository._aggregate와 같은 규칙."""
    g = sum(r.games or 0 for r in rows)

    def wavg(attr: str) -> float | None:
        pairs = [(getattr(r, attr), r.games or 0) for r in rows if getattr(r, attr) is not None and r.games]
        n = sum(k for _, k in pairs)
        return round(sum(v * k for v, k in pairs) / n, 4) if n else None

    first = rows[0]
    avg = wavg("avg_place")
    return first.model_copy(update={"comp_id": None, "games": g, "place_change": wavg("place_change"),
                                    "avg_place": min(8.0, max(1.0, avg)) if avg is not None else None})


def load_stats(path: str | Path | None = None, static: StaticData | None = None) -> AdvisorStats:
    """통계 조회 객체를 만든다.

    path를 주면 그 JSON을 어댑터로 읽는다(테스트·mini 파일). 아니면 stats 저장소의 `open_repository()`가
    있으면 그것을 쓰고, 없으면(Phase 3 병렬 개발 중) 최신 metatft JSON을 어댑터로 읽는다.
    """
    if path is None:
        try:
            from ..stats import repository as repo_mod   # stats-researcher 소유

            opener = getattr(repo_mod, "open_repository", None)
            if opener is not None:
                repo = opener()
                if isinstance(repo, AdvisorStats):
                    return repo
                log.warning("stats.repository.open_repository() 결과가 AdvisorStats를 만족하지 않아 JSON 어댑터 사용")
        except Exception as e:   # noqa: BLE001 - 저장소 미완성/오류 시 어댑터로 대체
            log.warning("stats 저장소 사용 불가(%s) → JSON 어댑터 사용", e)
        path = default_stats_path()
        if path is None:
            raise FileNotFoundError("data/stats/metatft_*.json 없음 (stats 변환 필요)")
    return JsonStatsAdapter.from_file(path, static)


def comps_by_id(stats: AdvisorStats) -> dict[str, CompStats]:
    return {c.comp_id: c for c in stats.comps()}


def stats_patch(stats: object) -> str | None:
    """패치 문자열. 어댑터는 `.patch`, StatsRepository는 `.meta.patch`."""
    p = getattr(stats, "patch", None)
    if isinstance(p, str):
        return p
    meta = getattr(stats, "meta", None)
    return getattr(meta, "patch", None)
