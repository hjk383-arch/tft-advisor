"""모듈 간 데이터 계약 — 단일 진실 원천 (소유자: app-integrator).

흐름:  vision → GameState → advisor(+ stats: CompStats/AugmentTier/UnitStats/UnitItemStats) → Recommendation → app/UI

규칙
- ID: 모든 챔피언/아이템/증강/특성/상점 특수 상품은 canonical ID 문자열(CDragon apiName, Set 18은 대부분 `DA_*`).
  통계 소스(MetaTFT, tactics.tools, OP.GG)도 같은 ID를 쓴다. 표시용 한국어 이름은 `name_ko` 별도 필드(선택).
  이름 → ID 변환은 `tft_advisor.static_data` 가 담당한다.
- 인식 불확실성: 값을 모르면 None. 필드별 신뢰도는 `GameState.confidence[필드명]`(0~1),
  슬롯/유닛 단위 신뢰도는 각 모델의 `confidence`. 낮은 신뢰도 필드는 advisor가 Jev state에서 제외한다.
- 필드명은 snake_case. 모든 계약 모델은 알 수 없는 필드를 거부한다(extra="forbid") — 경계면 오타를 즉시 드러내기 위해.
- 변경은 app-integrator만 한다. 변경 제안은 `_workspace/` 보고서로 전달.
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

CONTRACT_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# 공통 타입
# ---------------------------------------------------------------------------

_ID_PATTERN = r"^[A-Za-z0-9_]+$"
CanonicalId = Annotated[str, StringConstraints(min_length=1, pattern=_ID_PATTERN)]
"""CDragon apiName 형식 ID (예: `DA_18_Zyra`, `DA_ArchangelsStaff`, `DA_Hustler`)."""
ChampionId = CanonicalId
ItemId = CanonicalId
AugmentId = CanonicalId
TraitId = CanonicalId
"""특성 ID는 단계 접미사 없는 apiName(`DA_18_Elderwood`). MetaTFT `_3`, tactics.tools `__2` 접미사는 stats에서 제거."""

Stage = Annotated[str, StringConstraints(pattern=r"^\d+-\d+$")]
"""스테이지-라운드 문자열 (예: "3-2")."""

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Star = Annotated[int, Field(ge=1, le=4)]
Cost = Annotated[int, Field(ge=0, le=99)]

_STAGE_RE = re.compile(r"^(\d+)-(\d+)$")


def stage_tuple(stage: str) -> tuple[int, int]:
    """ "3-2" → (3, 2). 비교·정렬용."""
    m = _STAGE_RE.match(stage)
    if not m:
        raise ValueError(f"잘못된 stage 형식: {stage!r}")
    return int(m.group(1)), int(m.group(2))


class ContractModel(BaseModel):
    """모든 계약 모델의 기반. 알 수 없는 필드 거부, 할당 시 검증."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# 화면 인식 (vision → GameState)
# ---------------------------------------------------------------------------


class ScreenMode(StrEnum):
    """화면 상태 (vision 보고서 4절). 상점 추천의 주 트리거는 PLANNING."""

    LOADING = "loading"            # 로딩/비인게임(클라이언트, 대기열 등)
    PLANNING = "planning"          # 일반 준비 단계(상점 보임)
    COMBAT = "combat"              # 전투 중
    AUGMENT_SELECT = "augment_select"
    CAROUSEL = "carousel"          # 공동 선택(캐러셀)
    ITEM_SELECT = "item_select"    # 모루/포탈 등 세트 특수 선택 화면(미확인 메커닉)
    GAME_OVER = "game_over"
    UNKNOWN = "unknown"            # 판별 실패


class FieldSource(StrEnum):
    """GameState 필드 값의 출처. 보드처럼 인식이 어려운 값은 추적/수동 입력으로 채워질 수 있다."""

    VISION = "vision"      # 현재 프레임에서 인식
    TRACKED = "tracked"    # 이전 프레임/이벤트로 추적(예: 상점 구매 추적, 증강 선택 기록)
    MANUAL = "manual"      # 사용자가 오버레이에서 입력
    FIXTURE = "fixture"    # 테스트 정답 파일


class ShopSlotKind(StrEnum):
    """상점 칸 종류. Set 18은 챔피언이 아닌 특수 상품(shop_specials.json)이 올 수 있다."""

    CHAMPION = "champion"
    SPECIAL = "special"    # 예: "3단계와 함께" = DA_ThreeMe18
    EMPTY = "empty"        # 구매 완료 등으로 빈 칸
    UNKNOWN = "unknown"    # 무언가 있지만 식별 실패


class ShopSlot(ContractModel):
    """상점 한 칸. kind=CHAMPION이면 id는 champions.json apiName, SPECIAL이면 shop_specials.json apiName."""

    kind: ShopSlotKind
    id: CanonicalId | None = None
    name_ko: str | None = None
    cost: Cost | None = None
    confidence: Confidence = 1.0

    @model_validator(mode="after")
    def _check_kind(self) -> ShopSlot:
        if self.kind in (ShopSlotKind.CHAMPION, ShopSlotKind.SPECIAL) and self.id is None:
            raise ValueError(f"kind={self.kind} 인 칸은 id가 필요하다")
        if self.kind in (ShopSlotKind.EMPTY, ShopSlotKind.UNKNOWN) and self.id is not None:
            raise ValueError(f"kind={self.kind} 인 칸은 id를 가질 수 없다")
        return self


class UnitOnBoard(ContractModel):
    """보드 또는 벤치의 유닛 1기. 보드는 `hex`(행, 열; 행0=내 쪽 맨 앞, 4x7), 벤치는 `bench_slot`(0~8)."""

    id: ChampionId
    name_ko: str | None = None
    star: Star | None = None
    items: list[ItemId] = Field(default_factory=list, max_length=3)
    hex: tuple[Annotated[int, Field(ge=0, le=3)], Annotated[int, Field(ge=0, le=6)]] | None = None
    bench_slot: Annotated[int, Field(ge=0, le=8)] | None = None
    confidence: Confidence = 1.0


ItemCategory = Literal[
    "component", "completed", "emblem", "tactician", "artifact", "radiant",
    "consumable", "assist_reward", "other",
]
"""items.json `category` 값과 동일."""


class ItemRef(ContractModel):
    """아이템 하나(아이템 벤치 칸 또는 유닛 장착). category는 items.json 기준."""

    id: ItemId
    name_ko: str | None = None
    category: ItemCategory | None = None
    confidence: Confidence = 1.0


class ItemState(ContractModel):
    """보유 아이템(아이템 벤치, 유닛 미장착분). 장착 아이템은 UnitOnBoard.items.

    - components: 재료(프라이팬 포함)
    - completed: 일반 완성 아이템
    - emblems: 상징
    - others: 전략가/유물/찬란한/소모품 등 나머지
    """

    components: list[ItemRef] = Field(default_factory=list)
    completed: list[ItemRef] = Field(default_factory=list)
    emblems: list[ItemRef] = Field(default_factory=list)
    others: list[ItemRef] = Field(default_factory=list)

    def all_ids(self) -> list[str]:
        """모든 보유 아이템 ID(중복 포함)."""
        return [i.id for group in (self.components, self.completed, self.emblems, self.others) for i in group]


class AugmentRef(ContractModel):
    """증강 하나. rarity: 1 실버 / 2 골드 / 3 프리즘(augments.json의 `tier` 필드와 같은 값).

    설명 텍스트는 계약에 싣지 않는다 — advisor가 static_data에서 `desc_ko`를 조회한다.
    """

    id: AugmentId
    name_ko: str | None = None
    rarity: Annotated[int, Field(ge=1, le=3)] | None = None
    picked_stage: Stage | None = None   # 보유 증강일 때 선택한 스테이지
    confidence: Confidence = 1.0


class ActiveTrait(ContractModel):
    """활성 특성. MVP에서는 보드 유닛 + 정적 데이터로 계산(패널 판독은 교차검증용)."""

    id: TraitId
    name_ko: str | None = None
    count: Annotated[int, Field(ge=0)]
    active_breakpoint: int | None = None   # 현재 달성한 구간(없으면 None)
    next_breakpoint: int | None = None


GAME_STATE_FIELDS = (
    "screen_mode", "stage", "level", "xp", "gold", "streak", "hp", "shop_odds", "shop",
    "board", "bench", "items", "augments_owned", "augment_offer", "active_traits",
)
"""confidence/field_source 키로 쓸 수 있는 GameState 관측 필드 이름."""


class GameState(ContractModel):
    """한 시점의 게임 상태(vision 출력 + 세션 추적). 모르는 값은 None.

    - xp: (현재, 다음 레벨까지 필요량) 예 (2, 6)
    - streak: 부호 있는 정수. +N 연승, -N 연패, 0 없음
    - shop_odds: 코스트 1~5 확률(%) 5개, 화면 표기 그대로(예 [75, 25, 0, 0, 0])
    - shop: 정확히 5칸(인식 전이면 None)
    - confidence: {필드명: 0~1}. 값이 있는데 키가 없으면 1.0으로 간주(`confidence_of`)
    - field_source: {필드명: FieldSource}. 없으면 vision
    """

    screen_mode: ScreenMode = ScreenMode.UNKNOWN
    stage: Stage | None = None
    level: Annotated[int, Field(ge=1, le=10)] | None = None
    xp: tuple[Annotated[int, Field(ge=0)], Annotated[int, Field(ge=0)]] | None = None
    gold: Annotated[int, Field(ge=0)] | None = None
    streak: int | None = None
    hp: Annotated[int, Field(ge=0, le=100)] | None = None
    shop_odds: list[Annotated[int, Field(ge=0, le=100)]] | None = Field(default=None, min_length=5, max_length=5)
    shop: list[ShopSlot] | None = Field(default=None, min_length=5, max_length=5)
    board: list[UnitOnBoard] | None = None
    bench: list[UnitOnBoard] | None = Field(default=None, max_length=9)
    items: ItemState | None = None
    augments_owned: list[AugmentRef] | None = Field(default=None, max_length=4)
    augment_offer: list[AugmentRef] | None = Field(default=None, max_length=3)
    active_traits: list[ActiveTrait] | None = None

    confidence: dict[str, Confidence] = Field(default_factory=dict)
    field_source: dict[str, FieldSource] = Field(default_factory=dict)

    set_number: int | None = None
    captured_at: datetime | None = None
    source_image: str | None = None   # 캡처 파일 경로(--screenshot 모드, 에러 로그용)
    frame_size: tuple[int, int] | None = None   # (width, height) px

    @field_validator("confidence", "field_source")
    @classmethod
    def _known_keys(cls, v: dict) -> dict:
        unknown = set(v) - set(GAME_STATE_FIELDS)
        if unknown:
            raise ValueError(f"알 수 없는 필드 키: {sorted(unknown)} (허용: GAME_STATE_FIELDS)")
        return v

    def confidence_of(self, field: str) -> float:
        """필드 신뢰도. 값이 None이면 0.0, 명시가 없으면 1.0."""
        if getattr(self, field) is None:
            return 0.0
        return self.confidence.get(field, 1.0)

    def is_reliable(self, field: str, threshold: float) -> bool:
        """advisor가 Jev state 포함 여부를 판단할 때 쓴다."""
        return self.confidence_of(field) >= threshold


# ---------------------------------------------------------------------------
# 통계 (stats → advisor)
# ---------------------------------------------------------------------------


class StatSource(StrEnum):
    """통계 출처. 수치는 출처별로 따로 저장하고 합치지 않는다."""

    METATFT = "metatft"
    TACTICS_TOOLS = "tactics_tools"
    OPGG_MCP = "opgg_mcp"
    LOLCHESS = "lolchess"


class Provenance(ContractModel):
    """통계 행 공통 출처 정보."""

    source: StatSource
    patch: str | None = None          # 예 "18.2b"
    rank_filter: str | None = None    # 소스 고유 표기 그대로(예 "CHALLENGER,DIAMOND,GRANDMASTER,MASTER")
    fetched_at: datetime | None = None


class PlacementStats(ContractModel):
    """등수 통계. top4/win_rate는 0~1 비율. games는 표본(참가자 행) 수."""

    avg_place: Annotated[float, Field(ge=1.0, le=8.0)] | None = None
    top4: Confidence | None = None
    win_rate: Confidence | None = None
    games: Annotated[int, Field(ge=0)] | None = None


class CompUnit(ContractModel):
    """덱 최종 보드의 유닛 1기(권장 아이템 포함)."""

    id: ChampionId
    items: list[ItemId] = Field(default_factory=list, max_length=3)
    star: Star | None = None
    is_core: bool = False


class TraitReq(ContractModel):
    """덱 핵심 특성과 목표 인원."""

    id: TraitId
    count: Annotated[int, Field(ge=1)]


class BuildupBoard(PlacementStats):
    """특정 레벨의 빌드업 보드 1안(MetaTFT early_options/options). 소환물 ID는 제외하고 저장."""

    level: Annotated[int, Field(ge=1, le=10)]
    units: list[ChampionId]


BUILDUP_LEVELS = range(4, 11)
"""buildup 키로 허용되는 레벨: 4/5/6(early), 7~10(최종 보드 옵션)."""


class CompStats(PlacementStats, Provenance):
    """메타 덱 1개. 정체성은 클러스터 ID가 아니라 comp_id(핵심 유닛 집합 기반 안정 키)로 둔다.

    - buildup: {레벨: [보드안...]} — 스테이지가 아니라 레벨 키(소스가 레벨 단위로 제공)
    - level_timing: {레벨: 전형적 레벨업 라운드} 예 {5: "2-5", 6: "3-2", 8: "4-2"} — 스테이지 ↔ 레벨 변환용
    - item_conditional: {아이템 ID: 그 아이템을 가진 경우의 덱 성적} (MetaTFT comp_details.itemNames, 게임 종료 시점 기준)
    """

    comp_id: str
    name: str                      # 표시 이름(한국어 우선)
    name_en: str | None = None
    source_cluster_id: str | None = None   # 수집 시점 MetaTFT 클러스터/덱 ID(재계산 때 바뀜)
    final_board: list[CompUnit]
    carry: ChampionId | None = None
    carry_bis_items: list[ItemId] = Field(default_factory=list)
    key_traits: list[TraitReq] = Field(default_factory=list)
    buildup: dict[int, list[BuildupBoard]] = Field(default_factory=dict)
    level_timing: dict[int, Stage] = Field(default_factory=dict)
    item_conditional: dict[ItemId, PlacementStats] = Field(default_factory=dict)
    levelling: str | None = None   # 예 "Fast 8", "Reroll 6"

    @field_validator("buildup")
    @classmethod
    def _buildup_levels(cls, v: dict[int, list[BuildupBoard]]) -> dict[int, list[BuildupBoard]]:
        for lv, boards in v.items():
            if lv not in BUILDUP_LEVELS:
                raise ValueError(f"buildup 레벨 키는 4~10: {lv}")
            if any(b.level != lv for b in boards):
                raise ValueError(f"buildup[{lv}] 안의 보드 level 불일치")
        return v


EditorialTier = Literal["S", "A", "B", "C", "D"]


class AugmentTier(Provenance):
    """증강 등급 1행. Set 18은 증강 성적 통계가 없어 source_kind="editorial"(사람이 매긴 등급), games=None.

    - comp_id=None: 전체 티어리스트(MetaTFT augments_tiers), 값 있음: 덱별 등급(comp_augment_tiers)
    - source_kind="stats"는 통계가 다시 공개될 때를 대비한 자리(avg_place/games 채움)
    """

    augment_id: AugmentId
    tier: EditorialTier
    source_kind: Literal["editorial", "stats"]
    comp_id: str | None = None
    source_title: str | None = None     # 예 "ZYRA > Juggernaut > Lvl 8 push"
    avg_place: Annotated[float, Field(ge=1.0, le=8.0)] | None = None
    games: Annotated[int, Field(ge=0)] | None = None


class UnitStats(PlacementStats, Provenance):
    """유닛 전체 성적. star/level이 있으면 해당 조건 한정(소스가 제공할 때만)."""

    unit_id: ChampionId
    star: Star | None = None
    level: Annotated[int, Field(ge=1, le=10)] | None = None


class UnitItemStats(PlacementStats, Provenance):
    """유닛 + 아이템 1~3개 조합 성적. place_change: 유닛 평균 대비 등수 변화(음수가 좋음)."""

    unit_id: ChampionId
    item_ids: list[ItemId] = Field(min_length=1, max_length=3)
    place_change: float | None = None


# ---------------------------------------------------------------------------
# 추천 출력 (advisor → app/UI)
# ---------------------------------------------------------------------------


class ReasonTag(StrEnum):
    """상점 구매 이유 태그. 값은 ASCII(로그/직렬화 안정), 표시는 `label`."""

    NOW_POWER = "now_power"
    FINAL_COMP = "final_comp"
    BUILDUP = "buildup"
    TWO_STAR = "two_star"

    @property
    def label(self) -> str:
        return _REASON_LABELS[self]


_REASON_LABELS = {
    ReasonTag.NOW_POWER: "지금 전력",
    ReasonTag.FINAL_COMP: "최종 덱",
    ReasonTag.BUILDUP: "빌드업",
    ReasonTag.TWO_STAR: "2성 가능",
}


class ItemReadiness(ContractModel):
    """목표 덱 캐리 BIS 아이템 하나의 준비 상태."""

    item_id: ItemId
    status: Literal["owned", "craftable", "missing"]
    holder_unit_id: ChampionId | None = None   # 누구에게 줄 아이템인지(보통 carry)


class TargetComp(ContractModel):
    """오버레이에 상시 표시하는 최종 목표 덱 1개."""

    comp_id: str
    name: str
    score: Confidence
    carry: ChampionId | None = None
    reasons: list[str] = Field(default_factory=list)
    owned_units: list[ChampionId] = Field(default_factory=list)
    missing_units: list[ChampionId] = Field(default_factory=list)
    items_ready: list[ItemReadiness] = Field(default_factory=list)
    next_buildup_board: BuildupBoard | None = None


class ShopAdvice(ContractModel):
    """상점 한 칸에 대한 추천. slot은 GameState.shop 인덱스(0~4)."""

    slot: Annotated[int, Field(ge=0, le=4)]
    kind: ShopSlotKind
    offer_id: CanonicalId | None = None
    buy: bool
    score: Confidence
    reason_tag: ReasonTag | None = None
    reason: str | None = None


class AugmentChoice(ContractModel):
    """증강 후보 1개 평가."""

    augment_id: AugmentId
    score: Confidence
    editorial_tier: EditorialTier | None = None
    reasons: list[str] = Field(default_factory=list)


class AugmentAdvice(ContractModel):
    """증강 선택 화면에서만 채운다. pick은 choices 중 하나의 ID."""

    choices: list[AugmentChoice] = Field(max_length=3)
    pick: AugmentId | None = None


class ItemSuggestion(ContractModel):
    """완성템 조합 제안 1개."""

    item_id: ItemId
    components: list[ItemId] = Field(default_factory=list, max_length=2)
    holder_unit_id: ChampionId | None = None
    score: Confidence
    reason: str | None = None


class ItemAdvice(ContractModel):
    """아이템 조합 추천. hold=True면 '재료 보관'(Jev Choice의 no-match)."""

    suggestions: list[ItemSuggestion] = Field(default_factory=list)
    hold: bool = False


class Recommendation(ContractModel):
    """추천 1회 결과. target_comps는 보통 1~3개(인식/추천 실패 시 0개일 수 있음).

    - jev_used=False: 통계 전용 폴백(UI에 "Jev 미사용" 표시). fallback_reason에 사유
    - debug: Jev 원시 답·통계 원값·합성 중간값 저장(가중치만 바꿔 재계산 가능하도록). UI는 읽지 않는다
    """

    target_comps: list[TargetComp] = Field(default_factory=list, max_length=3)
    shop: list[ShopAdvice] = Field(default_factory=list, max_length=5)
    augment: AugmentAdvice | None = None
    item: ItemAdvice | None = None
    jev_used: bool
    fallback_reason: str | None = None
    latency_ms: Annotated[float, Field(ge=0)] | None = None
    state_hash: str | None = None
    created_at: datetime | None = None
    debug: dict[str, Any] = Field(default_factory=dict)
