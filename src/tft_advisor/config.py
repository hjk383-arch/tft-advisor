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
    state_dir: str = "_state"   # 세션·오버레이 위치 같은 실행 상태(gitignore). app/session.py, app/overlay.py
    # 새 판 판정(app/loop.py). game_over/loading 한 번의 오판으로 세션(수동 증강 포함)을 잃지 않게 한다.
    reset_strong_confidence: Unit = 0.95   # 화면 신뢰도가 이 이상(예: "최종 순위" + "나가기")이면 즉시 새 판
    reset_confirm_frames: int = Field(3, ge=1, le=30)   # 아니면 이 횟수 연속 관측되거나
    reset_confirm_s: float = Field(3.0, ge=0)           # 첫 관측 뒤 이 시간(초)이 지나 다시 관측되면 새 판
    reset_recheck_s: float = Field(1.0, gt=0)           # 확인 대기 중 화면 변화가 없어도 이 주기로 다시 판별한다
    session_archive_keep: int = Field(10, ge=0, le=500)  # 새 판 때 이전 session.json을 _state/sessions/에 보관할 개수


class CaptureCfg(_Cfg):
    """캡처 대상. 캡처 주기·안정 프레임은 `[vision] capture_fps` / `change_stable_frames`(Phase 3 config round에서
    옛 `poll_interval_ms`·`stable_frames`를 대체. 두 키는 아무 코드도 읽지 않았고, 같은 뜻의 키가 둘이면 어긋난다)."""

    # mss 모니터 번호(0 = 전체 가상 화면, 1 = 주 모니터, 2 = 두 번째 …) 또는 "auto"(기본: TFT 화면이 있는 모니터를 자동 선택,
    # `vision.capture.MssSource`). 듀얼 모니터에서 게임이 두 번째 모니터에 있어도 설정 없이 동작하게 하려는 것이다.
    monitor: Annotated[int, Field(ge=0)] | Literal["auto"] = "auto"


ContentBox = tuple[Unit, Unit, Unit, Unit]
"""프레임 안 게임 화면 영역 (x1, y1, x2, y2) — 캡처 프레임 크기에 대한 비율, 0 <= x1 < x2 <= 1, 0 <= y1 < y2 <= 1."""

# 화면 비율 이름 — `vision.regions.ASPECTS`의 키와 같아야 한다(test_vision이 검사한다).
# config는 vision(numpy·cv2)을 import하지 않으므로 여기에 따로 적는다.
AspectName = Literal["auto", "4:3", "16:10", "16:9", "21:9", "32:9"]
_RESOLUTION_RE = r"^(auto|\d{3,5}x\d{3,5})$"
_ASPECT_OF: dict[str, float] = {"4:3": 4 / 3, "16:10": 1.6, "16:9": 16 / 9, "21:9": 64 / 27, "32:9": 32 / 9}
_ASPECT_TOL = 0.035   # vision.regions.ASPECT_MATCH_TOL


class VisionCfg(_Cfg):
    """vision 인식 설정. 키별 연결 위치: `vision.recognizer.Recognizer`(cfg), `vision.change.ChangeDetector.from_cfg`,
    `vision.ocr.create_ocr(backend=)`, app 루프(capture_fps, traits_every_s — Phase 4).

    화면 크기·비율 (2026-09-22 추가)
    - `resolution`: 게임 화면 크기. "auto"(기본)면 캡처한 프레임 크기를 그대로 믿는다. "1280x800"처럼 적으면
      비율을 그 값에서 정하고, 실제 프레임 크기가 다르면 경고한다(캡처 영역 설정 실수 조기 발견).
    - `aspect`: ROI 배치를 고를 화면 비율. "auto"(기본)면 프레임(또는 content_box) 비율에서 자동 판별한다.
      16:9와 16:10은 실제 캡처로 측정했고, 나머지는 16:9에서 유도한다(미검증 → 경고).
    - `profile`: 고급. ROI 프로파일을 이름으로 못박는다("set18_16x9", "1920x1080" 같은 옛 값도 그대로 동작).
      "auto"(기본)가 아니면 `aspect`·`resolution`보다 우선한다.
    - `content_box` / `content_box_auto`: 프레임 안에서 게임 화면이 차지하는 영역. 창모드·레터박스용.
    """

    profile: str = "auto"
    resolution: str = Field("auto", pattern=_RESOLUTION_RE)
    aspect: AspectName = "auto"
    content_box_auto: bool = True   # content_box가 없을 때 레터박스(검은 띠)를 자동으로 잘라낸다
    shop_fuzzy_min: float = Field(85, ge=0, le=100)
    icon_match_min: float = Field(0.8, ge=0, le=1)
    state_min_confidence: float = Field(0.6, ge=0, le=1)
    # 게임 화면 영역(창모드·레터박스·테두리 보정). None(키 생략 또는 []) = 프레임 전체 = (0, 0, 1, 1).
    # 비율인 이유: 창 크기·DPI가 바뀌어도 같은 값이 유효하고, 원본 캡처가 없는 지금 픽셀 값을 정할 근거가 없다.
    content_box: ContentBox | None = None
    ocr_backend: Literal["auto", "onnxruntime", "openvino", "none"] = "auto"   # none = OCR 끔(진단용)
    name_fuzzy_min_margin: float = Field(10, ge=0, le=100)      # 점수 >= shop_fuzzy_min 수락에도 필요한 2위와의 차
    name_fuzzy_relaxed_margin: float = Field(15, ge=0, le=100)  # 점수 60~85 구간 수락에 필요한 2위와의 차
    item_match_margin: float = Field(0.05, ge=0, le=1)          # 아이콘 1위와 다른 아이템 1위의 점수 차 하한
    change_threshold: float = Field(24, ge=0, le=255)           # ROI 서명 픽셀 절대차 최댓값이 이보다 크면 "변화"
    change_stable_frames: int = Field(2, ge=1, le=30)           # 변화 후 이 프레임 수 연속 같아야 재인식
    capture_fps: float = Field(4, gt=0, le=30)                  # 앱 루프 캡처 주기(Phase 4)
    traits_every_s: float = Field(3, gt=0)                      # 앱 루프: 특성 패널("traits" 묶음)을 읽는 주기(초)

    @field_validator("content_box", mode="before")
    @classmethod
    def _empty_box_is_full_frame(cls, v: object) -> object:
        """TOML에는 null이 없다 → `content_box = []`도 "프레임 전체"(None)로 받는다."""
        return None if isinstance(v, (list, tuple)) and len(v) == 0 else v

    @model_validator(mode="after")
    def _constraints(self) -> VisionCfg:
        if self.content_box is not None:
            x1, y1, x2, y2 = self.content_box
            if not (x1 < x2 and y1 < y2):
                raise ValueError(f"vision: content_box는 (x1, y1, x2, y2), x1 < x2 · y1 < y2 여야 한다(현재 {self.content_box})")
        if not self.name_fuzzy_min_margin <= self.name_fuzzy_relaxed_margin:
            raise ValueError(
                f"vision: name_fuzzy_min_margin({self.name_fuzzy_min_margin}) <= "
                f"name_fuzzy_relaxed_margin({self.name_fuzzy_relaxed_margin}) 이어야 한다"
            )
        res = self.resolution_size()
        if res is not None and self.aspect != "auto":
            ratio = res[0] / res[1]
            want = _ASPECT_OF[self.aspect]
            if abs(ratio - want) / want > _ASPECT_TOL:
                raise ValueError(
                    f"vision: resolution({self.resolution}, 비율 {ratio:.4f})과 aspect({self.aspect}, "
                    f"{want:.4f})가 어긋난다. 둘 중 하나를 \"auto\"로 두거나 맞춰라"
                )
        return self

    def resolution_size(self) -> tuple[int, int] | None:
        """`resolution` → (width, height). "auto"면 None."""
        if self.resolution == "auto":
            return None
        w, h = self.resolution.split("x")
        return int(w), int(h)

    def aspect_setting(self) -> str:
        """ROI 프로파일 선택값 → `vision.regions.profile_for_frame(setting=)`에 넘길 문자열.

        우선순위: `profile`(고급, 이름 고정) > `aspect` > `resolution` > "auto"(프레임에서 자동 판별).
        """
        if self.profile != "auto":
            return self.profile
        if self.aspect != "auto":
            return self.aspect
        if self.resolution != "auto":
            return self.resolution
        return "auto"

    def content_px(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int] | None:
        """content_box(비율) → vision `FrameMapper(content=)`/`recognize(content=)` 형식 (left, top, width, height) px.

        None 또는 (0, 0, 1, 1)이면 None(= 프레임 전체, vision의 기존 기본 경로).
        """
        if self.content_box is None or self.content_box == (0.0, 0.0, 1.0, 1.0):
            return None
        x1, y1, x2, y2 = self.content_box
        left, top = round(x1 * frame_w), round(y1 * frame_h)
        return left, top, max(1, round(x2 * frame_w) - left), max(1, round(y2 * frame_h) - top)


JevBackendName = Literal["mock", "live", "off"]


class AdvisorCfg(_Cfg):
    # Jev 백엔드. 기본 "mock"(네트워크·과금 없음). "live"는 사용자가 명시할 때만 — TYPESAFE_API_KEY가 있다는 이유로
    # 자동 전환하지 않는다. "off" = Jev 없이 통계 전용(fallback_reason=jev_disabled). CLI `--no-jev`(Phase 4) → "off".
    # `create_advisor(mode)`의 명시 mode가 이 값보다 우선한다.
    jev_backend: JevBackendName = "mock"
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
    keep_snapshots: int = Field(5, ge=1)     # 출처별 보존 스냅샷 수(stats/refresh.py)
    user_agent: str = "tft-advisor-research/0.1 (personal use)"

    @field_validator("rank_filter")
    @classmethod
    def _norm_rank_filter(cls, v: str) -> str:
        return normalize_rank_filter(v)


class UiCfg(_Cfg):
    """공통 UI 설정. 오버레이 창의 위치·크기·투명도는 `[overlay]`(OverlayCfg)에 있다.

    `backend`: Phase 4에서 **PySide6로 확정**했다(사용자 결정). "auto"는 PySide6가 설치돼 있으면 오버레이,
    없으면 콘솔이다. "console"은 오버레이를 만들지 않는다(= CLI `--no-overlay`). "tkinter"는 구현하지 않았고
    지정하면 경고 후 콘솔로 내려간다(조용히 다른 툴킷으로 바꾸지 않는다).
    """

    backend: Literal["auto", "tkinter", "pyside6", "console"] = "auto"
    opacity: float = Field(0.85, gt=0, le=1)
    click_through: bool = True
    max_target_comps: int = Field(3, ge=1, le=3)


class OverlayCfg(_Cfg):
    """오버레이 창(PySide6). 위치는 `anchor`(모서리) + `x`/`y`(그 모서리로부터의 여백 px)로 정한다.

    - `opacity`/`click_through`가 None이면 `[ui]`의 같은 이름 값을 쓴다(한 곳에서만 고치면 되게).
    - 사용자가 창을 끌어 옮긴 뒤 트레이 메뉴 "위치 저장"을 누르면 `{app.state_dir}/overlay.json`에 픽셀 좌표가
      저장되고, 다음 실행부터 그 값이 이 설정보다 우선한다(설정 파일은 앱이 고치지 않는다).
    """

    enabled: bool = True
    anchor: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_right"
    x: int = Field(24, ge=0)          # anchor 모서리로부터 가로 여백 px
    y: int = Field(24, ge=0)          # anchor 모서리로부터 세로 여백 px
    width: int = Field(380, ge=200, le=1600)
    scale: float = Field(1.0, ge=0.5, le=3.0)       # 글꼴·여백 배율(고DPI·큰 화면)
    opacity: float | None = Field(None, gt=0, le=1)  # None = [ui] opacity
    click_through: bool | None = None                # None = [ui] click_through
    locked: bool = True               # True = 클릭 통과(이동 불가). False = 일반 창처럼 드래그 가능
    always_on_top: bool = True
    screen: int = Field(0, ge=0)      # 여러 모니터일 때 오버레이를 띄울 Qt 화면 번호
    remember_position: bool = True    # overlay.json의 저장된 위치를 읽을지


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
    overlay: OverlayCfg = OverlayCfg()
    logging: LoggingCfg = LoggingCfg()

    def overlay_opacity(self) -> float:
        """오버레이 불투명도: `[overlay] opacity` > `[ui] opacity`."""
        return self.ui.opacity if self.overlay.opacity is None else self.overlay.opacity

    def overlay_click_through(self) -> bool:
        """클릭 통과: `[overlay] click_through` > `[ui] click_through`."""
        return self.ui.click_through if self.overlay.click_through is None else self.overlay.click_through


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
    hysteresis_other_share: Unit = 0.25   # 직전 표시 2·3위 덱의 H(c) 비율(직전 1위 = 1). advisor Scorer.hysteresis_weight
    stat_avg_best: float = Field(4.0, ge=1, le=8)
    stat_avg_worst: float = Field(5.2, ge=1, le=8)
    show_ratio_undecided: Unit = 0.6
    undecided_min_p: Unit = 0.5
    tie_eps: float = Field(0.02, ge=0, le=0.2)
    # 09 J1 후반 처리(advisor/candidates.py `late_cfg`)
    undecided_until_stage: int = Field(4, ge=1, le=9)   # 이 스테이지 이상은 '초반: 방향 미정' 금지, 보드 미인식이면 템포 항
    w_tempo: Unit = 0.30                                # 레벨 템포 항 가중(보드 항 wb를 대신하므로 같은 척도)
    tempo_span: float = Field(2.0, gt=0, le=9)          # 레벨 차가 이 값 이상이면 템포 적합 0

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
    w_tempo: Unit = 0.25   # 09 J1: 후반·보드 미인식일 때 p(c)에 더하는 템포 가중(합=1 제약 밖의 가산 항)

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
    overall_stat_games_factor: Unit = 0.25   # §6.3 st(x): 덱 한정 행 없이 전체(파생) 행을 쓸 때 표본 수 할인

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
