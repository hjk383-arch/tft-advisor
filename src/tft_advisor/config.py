"""config/settings.toml, config/weights.toml 로더. 키 이름의 단일 정의 장소.

모든 키에 기본값이 있으므로 파일이 없거나 키가 빠져도 동작한다. 모르는 키는 오류(오타 방지).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import StatSource
from .static_data import PROJECT_ROOT

DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    jev_enabled: bool = True
    timeout_s: float = Field(2.0, gt=0)
    max_candidate_comps: int = Field(8, ge=1)
    cache_size: int = Field(64, ge=0)


class StatsCfg(_Cfg):
    primary: StatSource = StatSource.METATFT
    fallbacks: list[StatSource] = Field(default_factory=lambda: [StatSource.TACTICS_TOOLS, StatSource.OPGG_MCP])
    db_path: str = "data/stats/stats.sqlite"
    days: int = 3
    rank_filter: str = "CHALLENGER,DIAMOND,GRANDMASTER,MASTER"
    request_interval_s: float = Field(1.2, ge=1.0)
    user_agent: str = "tft-advisor-research/0.1 (personal use)"


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
    wi: float = 0.45
    wa: float = 0.25
    wb: float = 0.30
    wt: float = Field(0.2, ge=0, le=1)
    show_ratio: float = Field(0.75, ge=0, le=1)
    max_shown: int = Field(3, ge=1, le=3)
    hysteresis_bonus: float = Field(0.05, ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> CompWeights:
        if abs(self.wi + self.wa + self.wb - 1.0) > 1e-6:
            raise ValueError("comp.wi + wa + wb 는 1이어야 한다")
        return self


class StageWeight(_Cfg):
    ws: float = Field(ge=0, le=1)
    wp: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> StageWeight:
        if abs(self.ws + self.wp - 1.0) > 1e-6:
            raise ValueError("shop.stage_weights 의 ws + wp 는 1이어야 한다")
        return self


class ShopWeights(_Cfg):
    buy_threshold: float = Field(0.5, ge=0, le=1)
    stage_weights: dict[int, StageWeight] = Field(
        default_factory=lambda: {
            1: StageWeight(ws=0.8, wp=0.2),
            2: StageWeight(ws=0.7, wp=0.3),
            3: StageWeight(ws=0.5, wp=0.5),
            4: StageWeight(ws=0.3, wp=0.7),
            5: StageWeight(ws=0.2, wp=0.8),
        }
    )

    def for_stage(self, stage_number: int) -> StageWeight:
        """해당 스테이지 가중치. 표에 없으면 가장 가까운 아래 스테이지(없으면 최소 스테이지) 값."""
        keys = sorted(self.stage_weights)
        below = [k for k in keys if k <= stage_number]
        return self.stage_weights[below[-1] if below else keys[0]]


class ShrinkageWeights(_Cfg):
    k: float = Field(200, ge=0)
    prior_avg_place: float = Field(4.5, ge=1, le=8)

    def adjust(self, x: float, games: int | None) -> float:
        """표본 수축: (games*x + k*prior)/(games + k). games=None이면 prior."""
        g = games or 0
        if g + self.k == 0:
            return x
        return (g * x + self.k * self.prior_avg_place) / (g + self.k)


class JevWeights(_Cfg):
    min_confidence: float = Field(0.5, ge=0, le=1)
    low_confidence_scale: float = Field(0.5, ge=0, le=1)


class AugmentWeights(_Cfg):
    w_jev: float = 0.7
    w_editorial: float = 0.3
    editorial_tier_score: dict[Literal["S", "A", "B", "C", "D"], float] = Field(
        default_factory=lambda: {"S": 1.0, "A": 0.75, "B": 0.5, "C": 0.25, "D": 0.0}
    )


class Weights(_Cfg):
    """weights.toml 전체."""

    comp: CompWeights = CompWeights()
    shop: ShopWeights = ShopWeights()
    shrinkage: ShrinkageWeights = ShrinkageWeights()
    jev: JevWeights = JevWeights()
    augment: AugmentWeights = AugmentWeights()


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_settings(config_dir: Path | None = None) -> Settings:
    return Settings.model_validate(_read_toml((config_dir or DEFAULT_CONFIG_DIR) / "settings.toml"))


def load_weights(config_dir: Path | None = None) -> Weights:
    return Weights.model_validate(_read_toml((config_dir or DEFAULT_CONFIG_DIR) / "weights.toml"))
