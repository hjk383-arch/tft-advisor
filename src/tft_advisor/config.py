"""config/settings.toml, config/weights.toml 로더. 키 이름의 단일 정의 장소.

모든 키에 기본값이 있으므로 파일이 없거나 키가 빠져도 동작한다. 모르는 키는 오류(오타 방지).
키·기본값·범위·제약의 기준: `_workspace/02_jev-strategist_design.md` §10a "최종 설정 키 표"(2026-09-22).
범위는 `Field(ge/gt/le)`, 키 사이 제약(합=1, 단조 사다리, 순서)은 model_validator로 검증한다.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import StatSource, normalize_rank_filter
from .static_data import PROJECT_ROOT

DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def _require_sum_one(self, section: str, *names: str) -> None:
        total = sum(getattr(self, n) for n in names)
        if abs(total - 1.0) > _SUM_TOL:
            raise ValueError(f"{section}: {' + '.join(names)} 는 1이어야 한다(현재 {total:g})")

    def _require_non_increasing(self, section: str, *names: str) -> None:
        vals = [getattr(self, n) for n in names]
        if any(a < b for a, b in zip(vals, vals[1:])):
            raise ValueError(f"{section}: {' >= '.join(names)} 이어야 한다(현재 {vals})")


_SUM_TOL = 1e-6
Unit = Annotated[float, Field(ge=0, le=1)]
"""[0, 1] 범위 float."""


# --- settings.toml ---


class AppCfg(_Cfg):
    set_number: int = 18
    data_dir: str = "data"
    log_dir: str = "logs"


class CaptureCfg(_Cfg):
    monitor: int = 1
    poll_interval_ms: int = Field(500, ge=50)
    stable_frames: int = Field(3, ge=1)


class VisionCfg(_Cfg):
    profile: str = "1920x1080"
    shop_fuzzy_min: float = Field(85, ge=0, le=100)
    icon_match_min: float = Field(0.8, ge=0, le=1)
    state_min_confidence: float = Field(0.6, ge=0, le=1)


class AdvisorCfg(_Cfg):
    jev_enabled: bool = True                 # false → fallback_reason=jev_disabled
    timeout_s: float = Field(2.0, gt=0)      # 추천 1회 전체 예산
    max_candidate_comps: int = Field(8, ge=1, le=20)
    cache_size: int = Field(64, ge=0)        # 0 = 캐시 끔
    jev_model: str = Field("jev-latest", pattern=r"^jev-(latest|\d+\.\d+\.\d+)$")
    jev_timeout_s: float = Field(1.2, gt=0)          # 시도 1회 타임아웃
    jev_retry_budget_s: float = Field(1.5, gt=0)     # RetryPolicy(timeout=) 총 예산
    jev_max_retries: int = Field(1, ge=0, le=3)
    circuit_fail_threshold: int = Field(3, ge=1)
    circuit_cooldown_s: float = Field(60, ge=0)

    @model_validator(mode="after")
    def _budget_order(self) -> AdvisorCfg:
        if not (self.jev_timeout_s <= self.jev_retry_budget_s < self.timeout_s):
            raise ValueError(
                "advisor: jev_timeout_s <= jev_retry_budget_s < timeout_s 이어야 한다"
                f"(현재 {self.jev_timeout_s} / {self.jev_retry_budget_s} / {self.timeout_s})"
            )
        return self


class StatsCfg(_Cfg):
    primary: StatSource = StatSource.METATFT
    fallbacks: list[StatSource] = Field(default_factory=lambda: [StatSource.TACTICS_TOOLS, StatSource.OPGG_MCP])
    db_path: str = "data/stats/stats.sqlite"
    days: int = 3
    rank_filter: str = "CHALLENGER,DIAMOND,GRANDMASTER,MASTER"   # 저장 시 정렬·정규화(normalize_rank_filter)
    request_interval_s: float = Field(1.2, ge=1.0)
    user_agent: str = "tft-advisor-research/0.1 (personal use)"

    @field_validator("rank_filter")
    @classmethod
    def _norm_rank_filter(cls, v: str) -> str:
        return normalize_rank_filter(v)


class UiCfg(_Cfg):
    backend: Literal["auto", "tkinter", "pyside6"] = "auto"
    opacity: float = Field(0.85, gt=0, le=1)
    click_through: bool = True
    max_target_comps: int = Field(3, ge=1, le=3)


class LoggingCfg(_Cfg):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    recommendation_log: str = "logs/recommendations.jsonl"
    save_frame_on_error: bool = True


class Settings(_Cfg):
    """settings.toml 전체."""

    app: AppCfg = AppCfg()
    capture: CaptureCfg = CaptureCfg()
    vision: VisionCfg = VisionCfg()
    advisor: AdvisorCfg = AdvisorCfg()
    stats: StatsCfg = StatsCfg()
    ui: UiCfg = UiCfg()
    logging: LoggingCfg = LoggingCfg()


# --- weights.toml ---


class CompWeights(_Cfg):
    """최종 덱 합성(설계 5.1·5.2)."""

    wi: Unit = 0.45
    wa: Unit = 0.25
    wb: Unit = 0.30
    wt: Unit = 0.2
    show_ratio: Unit = 0.75
    max_shown: int = Field(3, ge=1, le=3)
    hysteresis_bonus: Unit = 0.05
    stat_avg_best: float = Field(4.0, ge=1, le=8)
    stat_avg_worst: float = Field(5.2, ge=1, le=8)
    show_ratio_undecided: Unit = 0.6
    undecided_min_p: Unit = 0.5
    tie_eps: float = Field(0.02, ge=0, le=0.2)

    @model_validator(mode="after")
    def _constraints(self) -> CompWeights:
        self._require_sum_one("comp", "wi", "wa", "wb")
        if not self.stat_avg_best < self.stat_avg_worst:
            raise ValueError(f"comp: stat_avg_best({self.stat_avg_best}) < stat_avg_worst({self.stat_avg_worst}) 이어야 한다")
        if not self.show_ratio_undecided <= self.show_ratio:
            raise ValueError(
                f"comp: show_ratio_undecided({self.show_ratio_undecided}) <= show_ratio({self.show_ratio}) 이어야 한다"
            )
        return self


class PrefilterWeights(_Cfg):
    """덱 후보 1차 필터(설계 2.1~2.3)."""

    min_games: int = Field(1000, ge=0)
    dedupe_jaccard: float = Field(0.75, gt=0, le=1)
    w_item: Unit = 0.40
    w_aug: Unit = 0.20
    w_unit: Unit = 0.25
    w_stat: Unit = 0.15
    item_saturation: float = Field(2.0, gt=0)
    unit_saturation: float = Field(4.0, gt=0)
    craftable_factor: Unit = 0.5
    aug_neutral: Unit = 0.5
    unit_w_core: Unit = 1.0
    unit_w_final: Unit = 0.6
    unit_w_buildup: Unit = 0.3
    unit_star_mult: float = Field(1.5, ge=1, le=3)
    stat_quota: int = Field(2, ge=0, le=8)

    @model_validator(mode="after")
    def _constraints(self) -> PrefilterWeights:
        self._require_sum_one("prefilter", "w_item", "w_aug", "w_unit", "w_stat")
        self._require_non_increasing("prefilter", "unit_w_core", "unit_w_final", "unit_w_buildup")
        return self


class ItemFitWeights(_Cfg):
    """아이템-덱 적합 b(x,c)(설계 2.2, 6.2, I1). usage_min_pcnt 단위는 덱당 평균 개수(비율 아님)."""

    carry_bis: Unit = 1.0
    core_unit: Unit = 0.7
    usage: Unit = 0.4
    emblem_key_trait: Unit = 1.0
    emblem_other: Unit = 0.2
    usage_min_pcnt: float = Field(0.3, ge=0, le=3)
    used_by_min: Unit = 0.7

    @model_validator(mode="after")
    def _constraints(self) -> ItemFitWeights:
        self._require_non_increasing("item_fit", "carry_bis", "core_unit", "usage")
        self._require_non_increasing("item_fit", "emblem_key_trait", "emblem_other")
        return self


class StageWeight(_Cfg):
    ws: Unit
    wp: Unit

    @model_validator(mode="after")
    def _sum_to_one(self) -> StageWeight:
        self._require_sum_one("shop.stage_weights", "ws", "wp")
        return self


def _stage_lookup[V](table: dict[int, V], stage_number: int | None) -> V:
    """스테이지 번호 이하 키 중 최댓값의 값. 해당 키가 없거나 stage_number=None이면 최소 키의 값."""
    keys = sorted(table)
    below = [k for k in keys if stage_number is not None and k <= stage_number]
    return table[below[-1] if below else keys[0]]


class ShopWeights(_Cfg):
    """상점 합성(설계 5.3)."""

    buy_threshold: Unit = 0.5
    stage_weights: dict[Annotated[int, Field(ge=1)], StageWeight] = Field(
        default_factory=lambda: {
            1: StageWeight(ws=0.8, wp=0.2),
            2: StageWeight(ws=0.7, wp=0.3),
            3: StageWeight(ws=0.5, wp=0.5),
            4: StageWeight(ws=0.3, wp=0.7),
            5: StageWeight(ws=0.2, wp=0.8),
        },
        min_length=1,
    )
    jev_share_now: Unit = 0.7
    jev_share_path: Unit = 0.5
    two_star_bonus: Unit = 0.15
    three_star_bonus: Unit = 0.25
    hp_danger_shift: float = Field(0.15, ge=0, le=0.5)
    mu_core: Unit = 1.0
    mu_final: Unit = 0.7
    mu_next_buildup: Unit = 0.5
    mu_cur_buildup: Unit = 0.25
    special_fallback_score: Unit = 0.3

    @model_validator(mode="after")
    def _constraints(self) -> ShopWeights:
        self._require_non_increasing("shop", "mu_core", "mu_final", "mu_next_buildup", "mu_cur_buildup")
        return self

    def for_stage(self, stage_number: int | None) -> StageWeight:
        """해당 스테이지 가중치. 표에 없으면 가장 가까운 아래 스테이지(없거나 None이면 최소 스테이지) 값."""
        return _stage_lookup(self.stage_weights, stage_number)


class ShrinkageWeights(_Cfg):
    k: float = Field(200, ge=0)
    prior_avg_place: float = Field(4.5, ge=1, le=8)

    def adjust(self, x: float, games: int | None, prior: float | None = None) -> float:
        """표본 수축 (g*x + k*p)/(g + k).  g = games or 0,  p = prior_avg_place if prior is None else prior.

        g + k == 0 이면 x. prior 범위는 검증하지 않는다(place_change처럼 avg_place가 아닌 값에도 쓴다:
        `adjust(place_change, games, prior=0.0)`). games가 None/0이면(k>0) 결과는 p.
        """
        g = games or 0
        p = self.prior_avg_place if prior is None else prior
        if g + self.k == 0:
            return x
        return (g * x + self.k * p) / (g + self.k)


class JevWeights(_Cfg):
    min_confidence: Unit = 0.5
    low_confidence_scale: Unit = 0.5


_EDITORIAL_DEFAULTS = {"S": 1.0, "A": 0.75, "B": 0.5, "C": 0.25, "D": 0.0}


class AugmentWeights(_Cfg):
    """증강 합성(설계 7)."""

    w_jev: Unit = 0.7
    w_editorial: Unit = 0.3
    editorial_tier_score: dict[Literal["S", "A", "B", "C", "D"], Unit] = Field(
        default_factory=lambda: dict(_EDITORIAL_DEFAULTS)
    )
    w_comp: Unit = 0.65
    unlisted_score: Unit = 0.5
    tie_eps: float = Field(0.03, ge=0, le=0.2)
    commit_by_stage: dict[Annotated[int, Field(ge=1)], Unit] = Field(
        default_factory=lambda: {2: 0.3, 3: 0.6, 4: 0.9}, min_length=1
    )

    @field_validator("editorial_tier_score", mode="before")
    @classmethod
    def _fill_missing_tiers(cls, v: object) -> object:
        """누락 등급 키는 기본값으로 채운다."""
        if isinstance(v, dict):
            return {**_EDITORIAL_DEFAULTS, **v}
        return v

    @model_validator(mode="after")
    def _constraints(self) -> AugmentWeights:
        self._require_sum_one("augment", "w_jev", "w_editorial")
        return self

    def commit_for_stage(self, stage_number: int | None) -> float:
        """스테이지 번호 이하 키 중 최댓값의 값. 없거나 None이면 최소 키의 값(기본: 1→0.3, 5+→0.9). for_stage와 같은 규칙."""
        return _stage_lookup(self.commit_by_stage, stage_number)


class ItemWeights(_Cfg):
    """아이템 추천 합성(설계 6)."""

    w_bis: Unit = 0.5
    w_jev: Unit = 0.35
    w_stat: Unit = 0.15
    place_change_span: float = Field(1.0, gt=0)
    hold_bis_max: Unit = 0.4
    hold_until_stage: int = Field(4, ge=1, le=10)   # stage 번호 < 이 값일 때만 hold

    @model_validator(mode="after")
    def _constraints(self) -> ItemWeights:
        self._require_sum_one("item", "w_bis", "w_jev", "w_stat")
        return self


class Weights(_Cfg):
    """weights.toml 전체."""

    comp: CompWeights = CompWeights()
    prefilter: PrefilterWeights = PrefilterWeights()
    item_fit: ItemFitWeights = ItemFitWeights()
    shop: ShopWeights = ShopWeights()
    shrinkage: ShrinkageWeights = ShrinkageWeights()
    jev: JevWeights = JevWeights()
    augment: AugmentWeights = AugmentWeights()
    item: ItemWeights = ItemWeights()


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings(config_dir: Path | None = None) -> Settings:
    return Settings.model_validate(_read_toml((config_dir or DEFAULT_CONFIG_DIR) / "settings.toml"))


def load_weights(config_dir: Path | None = None) -> Weights:
    return Weights.model_validate(_read_toml((config_dir or DEFAULT_CONFIG_DIR) / "weights.toml"))
