"""세션 상태 — 부분 인식 결과 병합, 게임 1판 동안의 추적, `_state/session.json` 영속.

vision은 무상태다(프레임 1장 → GameState, 모르면 None). 실시간 루프는 **바뀐 ROI 묶음만** 다시 읽으므로
(`vision.change.ChangeDetector`), 이번 프레임에서 읽지 않은 필드는 직전 값을 이어써야 한다. 그 병합과,
프레임 한 장으로는 알 수 없는 것(보유 증강, 구매)의 추적이 여기 있다.

병합 규칙(보수적 — 02_app-integrator_report.md C3, 04_qa_phase3_final.md)
1. 새 값이 있으면 새 값을 쓴다(신뢰도·출처도 함께).
2. 새 값이 없고 그 묶음을 **이번에 실제로 읽었으면**(요청했고, 현재 화면이 그 묶음을 읽는 화면이면) 지운다.
   상점이 낡은 채로 남아 이미 산 유닛을 계속 추천하는 쪽이 "모름"보다 나쁘다.
3. 그 밖에는 직전 값을 유지한다. 전투·unknown 화면에서 HUD를 못 읽었다고 상태를 잃지 않는다.
   (vision이 전투를 planning으로 오분류하는 문제가 남아 있어, 애매한 화면에서 상태를 버리지 않는 쪽으로 붙였다.)
4. `screen_mode`는 **절대 상속하지 않는다**. 지금 화면이 무엇인지는 항상 이번 프레임의 판정이다.

보유 증강(`augments_owned`)
- 증강 선택 화면의 `augment_offer`만으로는 사용자가 무엇을 골랐는지 알 수 없다. **1위 추천을 골랐다고 가정하지
  않는다.** 확정 신호는 HUD 보유 증강 판독(vision 미구현, 캡처 필요) 또는 사용자 수동 입력뿐이다.
- vision이 `augments_owned`를 채우면(보유 증강 줄의 모든 칸을 전체 목록으로 식별) **한 판 안에서는 늘기만 한다** 규칙에
  맞을 때만 받는다(10 app, QA08 A2 — 준비 단계에 상대 보드를 관전하면 상대 증강 줄이 같은 자리에 보인다):
  1. 기존 칸의 값은 바뀌지 않는다: 세션이 아는 칸과 앞부분이 모두 같아야 한다(같은 그림이면 같은 것으로 보고,
     이름은 세션 값을 유지한다 — 선택 순간 학습으로 확정한 이름이 더 구체적이다).
  2. 칸 수는 줄지 않는다: 세션보다 짧으면 버린다.
  3. 칸 수는 증강 선택 시점(2-1/3-2/4-2) 이후에만 는다: 현재 스테이지에서 가능한 개수(`augments_allowed_at`)를
     넘으면 버린다. 스테이지를 모르면 늘리지 않는다.
  4. 수동 입력이 우선한다: 수동 목록과 어긋나는 vision 값은 버리고, 수동 목록의 **확장**(뒤에 칸이 붙음)만 받는다.
  버린 값은 advisor에 가지 않는다(세션 값이 대신 간다). 선택 순간 학습(`_learn_owned`)도 같은 규칙으로 줄을 거른다.
- 수동 입력(`set_augments_owned`)은 `field_source="manual"`, 세션 추적값은 `"tracked"`다. 수동 입력은 언제나 덮어쓴다.
- 확정 전에는 None이다(advisor가 "모름"으로 다룬다).

보유 유닛 장부(`board`/`bench`) — 2026-09-23
- vision은 보드의 3D 모델로 챔피언을 식별하지 못한다. 그래서 **정체는 상점 구매 추적**(`app.ledger`)으로 안다:
  상점 칸이 사라지면서 그 코스트만큼 골드가 줄면 구매, 늘면 판매다. 리롤(2)·경험치(4)·라운드 수입은 같은 수식으로 가른다.
  맞아떨어지지 않으면 **추측하지 않고** 애매(`ambiguous`)로 세고, 그만큼 `board`/`bench` 신뢰도를 내린다.
- 상점 밖 획득(공동 선택·증강·모루/구슬)은 상점 이벤트가 없다 → `add_unit()`로 넣는다.
- vision이 보드 판독(자리·성급·장착 아이템)을 주면 `app.unit_merge.merge_units`가 장부의 정체와 합친다.
  개수가 어긋나면 **vision의 개수를 믿고** 남는 칸은 `UNKNOWN_UNIT_ID`로 둔다(ID를 지어내지 않는다).
- 수동 교정: `add_unit`/`remove_unit`/`set_unit_star`/`set_units`/`clear_units`/`confirm_units`(`app.units_cmd`가
  문자열 명령으로 감싼다). 장부는 `session.json`에 남고 새 판에서 비워진다.

선택 순간 자동 학습(09 vision, `vision.augment_learn`)
- 증강 선택 화면에서 읽은 후보(`augment_offer`, 3개 모두 인식된 경우만)를 `offer_pool`에 모은다. 같은 스테이지의 리롤로
  바뀐 후보도 더한다. 스테이지가 다른 새 증강 라운드가 오면 소비되지 않은 이전 목록은 버린다.
  이때 **선택 전 칸 수**(`offer_base` = 마지막 준비 화면의 보유 증강 칸 수)를 같이 기억한다.
- 준비 화면에서 보유 증강 줄이 `offer_base + 1`칸이 되면(새 칸 정확히 1개, 오른쪽 끝) 그 칸을 **후보 안에서만** 비교해
  확정한다(`AugmentLearner.decide`). 확정되면 칸 그림을 실화면 템플릿으로 저장하고(`augments_screen/`, 다음 판부터 전체
  목록에서도 인식) 보유 증강 목록을 갱신한다(`tracked`).
- 학습하지 않는 경우: 제시 목록이 없음 / 선택 전 칸 수를 모름(앱을 증강 선택 중에 켬) / 새 칸이 2개 이상이거나 줄어듦 /
  후보 사이 점수 차가 작음 / 같은 그림의 서로 다른 증강이 함께 제시됨 / vision이 제시되지 않은 증강으로 읽음.
- 영속: `offer_pool`·`offer_base`·`owned_count`·`learned`가 `session.json`에 남는다. 새 판(loading/game_over)이면 비운다
  (저장된 실화면 템플릿은 판과 무관하므로 남는다).
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..contracts import AugmentRef, FieldSource, GameState, ScreenMode, ShopSlotKind
from .ledger import (
    CostBook, FrameObs, LedgerCfg, PurchaseTracker, UnitLedger, bodies_for, copies_for_star,
)
from .unit_merge import (
    BoardObs, MergeResult, board_obs_from, board_obs_from_state, merge_units, state_with_units, with_equipped,
)

log = logging.getLogger(__name__)

_FIELD_GROUP: dict[str, str] | None = None


def field_group() -> dict[str, str]:
    """필드 → ROI 묶음 이름(`vision.recognizer.FIELD_GROUP`). 지연 import — 이 모듈은 vision 없이도 쓴다."""
    global _FIELD_GROUP
    if _FIELD_GROUP is None:
        from ..vision.recognizer import FIELD_GROUP

        _FIELD_GROUP = dict(FIELD_GROUP)
    return _FIELD_GROUP

SESSION_VERSION = 1
SESSION_MAX_AGE_S = 2 * 3600.0
"""이보다 오래된 `session.json`은 다른 판으로 보고 복원하지 않는다."""

_ALL_MODES = frozenset(ScreenMode)
GROUP_READ_MODES: dict[str, frozenset[ScreenMode]] = {
    # `Recognizer.recognize`가 그 묶음을 실제로 읽는 화면(그 밖의 화면에서는 요청해도 값이 안 나온다)
    # `vision.recognizer.READ_MODES`와 같아야 한다(tests/app/test_session.py가 고정). 2026-09-22 vision 07: 전투(COMBAT)를
    # 따로 판별하게 되면서 전투 중에도 HUD·상점·특성·아이템을 읽고, 캐러셀·특수 선택 화면에서도 아이템 벤치를 읽는다.
    "stage": _ALL_MODES,
    "players": _ALL_MODES,
    "hud": frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT}),
    "shop": frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT}),
    "traits": frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT}),
    "items": frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT, ScreenMode.AUGMENT_SELECT,
                        ScreenMode.ITEM_SELECT, ScreenMode.CAROUSEL}),
    "augment": frozenset({ScreenMode.AUGMENT_SELECT}),
    # 보유 증강 줄(augments_owned). 병합은 _CARRY_FIELDS 규칙(새 값이 있을 때만 덮어씀)이라 이 표는 문서·일치 검사용이다.
    "owned": frozenset({ScreenMode.PLANNING}),
}
# vision의 "board" 묶음(자리·성급·장착 아이템)은 이 표에 **없다**: GameState 필드를 직접 만들지 않고
# `Recognizer.last_board_read`로 나와 `_apply_units`에서 구매 장부(정체)와 합쳐지기 때문이다
# (`vision.recognizer.VISION_ONLY_GROUPS`). 두 표의 나머지는 같아야 한다(tests/app/test_session.py).
RESET_MODES = frozenset({ScreenMode.LOADING, ScreenMode.GAME_OVER})
"""이 화면을 보면 새 판으로 보고 세션을 비운다(advisor 계약과 같다)."""
KEEP_MODES = frozenset({ScreenMode.COMBAT, ScreenMode.ITEM_SELECT, ScreenMode.UNKNOWN})
"""직전 추천을 그대로 두는 화면. 새로 추천하지 않는다."""

TRANSIENT_FIELDS = frozenset({"augment_offer"})
"""그 화면에서만 존재하는 값. 화면이 바뀌면 직전 값을 이어쓰지 않는다(증강 후보가 준비 화면에 남으면 안 된다)."""

_CARRY_FIELDS = ("board", "bench", "augments_owned")
"""FIELD_GROUP에 없는(= vision이 묶음으로 읽지 않는) 관측 필드. 새 값이 있으면 쓰고 없으면 유지한다."""
AUGMENT_STAGES = ("2-1", "3-2", "4-2")
"""증강 선택 라운드. 보유 증강 칸 수는 이 시점을 지날 때만 는다."""
SESSION_ARCHIVE_DIR = "sessions"
"""새 판 때 이전 `session.json`을 보관하는 하위 폴더(`{state_dir}/sessions/session_YYYYmmdd_HHMMSS_ffffff.json`)."""


def stage_key(stage: str | None) -> tuple[int, int] | None:
    """스테이지 "3-2" → (3, 2). 읽을 수 없으면 None."""
    if not stage:
        return None
    try:
        a, b = stage.split("-", 1)
        return int(a), int(b)
    except ValueError:
        return None


def augments_allowed_at(stage: str | None) -> int | None:
    """이 스테이지까지 고를 수 있었던 증강 수(2-1 → 1, 3-2 → 2, 4-2 → 3). 스테이지를 모르면 None."""
    key = stage_key(stage)
    if key is None:
        return None
    return sum(1 for s in AUGMENT_STAGES if stage_key(s) <= key)


def merge_state(prev: GameState | None, new: GameState, groups: Collection[str]) -> GameState:
    """직전 상태 + 이번 부분 인식 결과 → 합친 GameState. 규칙은 모듈 docstring 참고."""
    if prev is None:
        return new
    read = set(groups) | {"stage"}
    mode = new.screen_mode
    values: dict[str, object] = {}
    conf: dict[str, float] = {}
    src: dict[str, FieldSource] = {}

    def take(from_state: GameState, name: str) -> None:
        value = getattr(from_state, name)
        if value is None:
            return
        values[name] = value
        if name in from_state.confidence:
            conf[name] = from_state.confidence[name]
        if name in from_state.field_source:
            src[name] = from_state.field_source[name]

    take(new, "screen_mode")   # 화면 상태와 그 신뢰도는 항상 이번 프레임 것이다(상속하지 않는다)
    for name, group in field_group().items():
        if name == "screen_mode":
            continue
        if name in TRANSIENT_FIELDS and mode not in GROUP_READ_MODES.get(group, _ALL_MODES):
            continue   # 그 화면에서만 존재하는 값(증강 후보) — 화면을 벗어나면 버린다
        if getattr(new, name) is not None:
            take(new, name)
        elif group in read and mode in GROUP_READ_MODES.get(group, _ALL_MODES):
            continue   # 이번에 읽었는데 값이 없다 → 낡은 값을 버린다
        else:
            take(prev, name)
    for name in _CARRY_FIELDS:
        take(new if getattr(new, name) is not None else prev, name)

    values.pop("screen_mode", None)   # 아래에서 따로 넘긴다(신뢰도·출처는 conf/src에 남는다)
    return GameState(
        screen_mode=mode,
        **values,
        confidence=conf,
        field_source=src,
        set_number=new.set_number or prev.set_number,
        captured_at=new.captured_at,
        source_image=new.source_image,
        frame_size=new.frame_size or prev.frame_size,
    )


@dataclass
class SessionData:
    """게임 1판 동안 쌓이는 추적값(영속 대상)."""

    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    stage: str | None = None
    augments_owned: list[str] = field(default_factory=list)
    augments_source: str | None = None          # "vision" | "manual" | "tracked"
    last_offer: list[str] = field(default_factory=list)      # 마지막으로 본 증강 후보(선택 확인 대기)
    last_offer_stage: str | None = None
    offer_pool: list[str] = field(default_factory=list)      # 이번 증강 라운드에 제시된 후보 전부(리롤 포함, 학습 대기)
    offer_pool_stage: str | None = None
    offer_base: int | None = None                            # 선택 전 보유 증강 칸 수(모르면 None → 학습 안 함)
    owned_count: int | None = None                           # 마지막으로 본 보유 증강 칸 수(준비 화면)
    learned: list[dict] = field(default_factory=list)        # 선택 순간 학습 기록(ID, 스테이지, 점수, 근거)
    purchases: Counter[str] = field(default_factory=Counter)  # 상점 칸이 사라진 횟수(추정 구매)
    units: UnitLedger = field(default_factory=UnitLedger)     # 보유 유닛 장부(챔피언 → 1성 등가 사본 수)
    frames: int = 0
    recognitions: int = 0
    pinned_comp_id: str | None = None      # 사용자가 고정한 목표 덱(오버레이 클릭, 31 보고). 새 판에서 비워진다
    pinned_comp_name: str | None = None    # 표시용 이름(고정한 덱이 목표 덱 목록에서 빠져도 해제 버튼에 쓴다)

    def to_json(self) -> dict:
        return {
            "version": SESSION_VERSION,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "stage": self.stage,
            "augments_owned": list(self.augments_owned),
            "augments_source": self.augments_source,
            "last_offer": list(self.last_offer),
            "last_offer_stage": self.last_offer_stage,
            "offer_pool": list(self.offer_pool),
            "offer_pool_stage": self.offer_pool_stage,
            "offer_base": self.offer_base,
            "owned_count": self.owned_count,
            "learned": list(self.learned),
            "purchases": dict(self.purchases),
            "units": self.units.to_json(),
            "frames": self.frames,
            "recognitions": self.recognitions,
            "pinned_comp_id": self.pinned_comp_id,
            "pinned_comp_name": self.pinned_comp_name,
        }

    @classmethod
    def from_json(cls, raw: dict) -> SessionData:
        d = cls(
            started_at=str(raw.get("started_at") or datetime.now(UTC).isoformat()),
            updated_at=str(raw.get("updated_at") or datetime.now(UTC).isoformat()),
            stage=raw.get("stage"),
            augments_owned=[str(a) for a in raw.get("augments_owned") or []],
            augments_source=raw.get("augments_source"),
            last_offer=[str(a) for a in raw.get("last_offer") or []],
            last_offer_stage=raw.get("last_offer_stage"),
            offer_pool=[str(a) for a in raw.get("offer_pool") or []],
            offer_pool_stage=raw.get("offer_pool_stage"),
            offer_base=_opt_int(raw.get("offer_base")),
            owned_count=_opt_int(raw.get("owned_count")),
            learned=[dict(x) for x in raw.get("learned") or [] if isinstance(x, dict)],
            frames=int(raw.get("frames") or 0),
            recognitions=int(raw.get("recognitions") or 0),
            pinned_comp_id=str(raw["pinned_comp_id"]) if raw.get("pinned_comp_id") else None,
            pinned_comp_name=str(raw["pinned_comp_name"]) if raw.get("pinned_comp_name") else None,
        )
        d.purchases = Counter({str(k): int(v) for k, v in (raw.get("purchases") or {}).items()})
        d.units = UnitLedger.from_json(raw.get("units"))
        return d


class SessionTracker:
    """루프의 기억. 부분 인식 병합 + 보유 증강·구매 추적 + `session.json` 영속."""

    def __init__(self, path: Path | None = None, *, max_age_s: float = SESSION_MAX_AGE_S,
                 clock: object = None, archive_keep: int = 10, ledger_cfg: LedgerCfg | None = None,
                 costs: CostBook | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.max_age_s = max_age_s
        self.archive_keep = archive_keep
        self.last_archive: Path | None = None
        self.augments_rejected = 0
        """이번 판에 '늘기만 한다' 규칙에 걸려 버린 vision 보유 증강 판독 수(표시·로그용)."""
        self._aug_accepted = False
        self._now = clock or time.time
        self.data = SessionData()
        self.state: GameState | None = None      # 마지막으로 합친 GameState
        self.ledger_cfg = ledger_cfg or LedgerCfg()
        self.costs = costs if costs is not None else CostBook()
        self.purchases = PurchaseTracker(self.ledger_cfg, self.costs)
        """상점 칸 + 골드 변화 → 구매/판매 추론(`app.ledger`). 장부는 `data.units`다."""
        self.last_merge: MergeResult | None = None
        """마지막 보드 병합 결과(표시·로그용)."""
        self.last_events: list = []
        """마지막 `observe()`가 장부에 반영한 이벤트(`LedgerEvent`). 루프가 구매를 유닛 사진 수집기에 알린다."""
        self._board_obs: BoardObs | None = None
        """마지막으로 vision이 읽은 보드 판독. **`board_read=None`은 "보드가 비었다"가 아니라 "이번
        프레임에서 안 읽었다"** 이다(`_workspace/16_board_vision.md` §6.1) — 보드 묶음을 읽지 않는 프레임에서
        자리·성급·장착 아이템을 잃지 않도록 직전 값을 이어 쓴다. 새 판에서 비운다."""
        self._lock = threading.RLock()
        """수동 교정은 다른 스레드(콘솔·오버레이)에서 들어온다 — 장부 변경을 직렬화한다."""
        self._prev_shop: list[tuple[str, str] | None] | None = None
        self.learner: Any = None
        """선택 순간 학습기(`vision.augment_learn.AugmentLearner`: decide/commit/same_picture). None이면 학습하지 않는다.
        LiveLoop가 인식기의 것을 넣는다."""

    # ------------------------------------------------------------------ 영속
    def load(self) -> bool:
        """저장된 세션을 복원한다. 파일이 없거나 오래됐으면 False."""
        if self.path is None or not self.path.is_file():
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("세션 파일을 읽지 못했습니다(%s): %s", self.path, e)
            return False
        if raw.get("version") != SESSION_VERSION:
            log.info("세션 파일 버전이 달라 무시합니다: %s", raw.get("version"))
            return False
        data = SessionData.from_json(raw)
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(data.updated_at)).total_seconds()
        except ValueError:
            age = self.max_age_s + 1
        if age > self.max_age_s:
            log.info("세션 파일이 오래됐습니다(%.0f분) → 새 세션", age / 60)
            return False
        self.data = data
        return True

    def save(self) -> None:
        if self.path is None:
            return
        self.data.updated_at = datetime.now(UTC).isoformat()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:   # 저장 실패로 앱이 죽지 않는다
            log.warning("세션 저장 실패(%s): %s", self.path, e)

    def set_pinned_comp(self, comp_id: str | None, name: str | None = None) -> None:
        """목표 덱 고정/해제를 기록하고 바로 저장한다(앱을 다시 켜도 이번 판 동안 유지). 아무 스레드에서나 부른다."""
        with self._lock:
            self.data.pinned_comp_id = comp_id or None
            self.data.pinned_comp_name = (name or None) if comp_id else None
            self.save()

    # ------------------------------------------------------------------ 추적
    def reset(self, reason: str = "new_game") -> None:
        """새 판: 합친 상태와 추적값을 비운다(loading/game_over). 지우기 전에 이전 세션을 보관한다."""
        log.info("세션 초기화 (%s)", reason)
        self.last_archive = self.archive()
        self.data = SessionData()
        self.state = None
        self._prev_shop = None
        self.augments_rejected = 0
        self.purchases.reset()
        self.last_merge = None
        self._board_obs = None
        self.save()

    def archive(self) -> Path | None:
        """현재 세션을 `{state_dir}/sessions/session_<시각>.json`으로 보관하고 보관본은 `archive_keep`개만 남긴다.

        오판으로 초기화돼도 수동 입력 증강 등을 되살릴 수 있게 한다. 쌓인 것이 없는 세션(프레임 0, 증강 없음)은 보관하지 않는다.
        """
        if self.path is None or self.archive_keep <= 0:
            return None
        if self.data.frames == 0 and not self.data.augments_owned:
            return None
        self.save()
        folder = self.path.parent / SESSION_ARCHIVE_DIR
        try:
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")   # 이름순 = 시간순
            dest = folder / f"session_{stamp}.json"
            n = 1
            while dest.exists():
                n += 1
                dest = folder / f"session_{stamp}_{n}.json"
            if self.path.is_file():
                shutil.copy2(self.path, dest)
            else:
                dest.write_text(json.dumps(self.data.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
            kept = sorted(folder.glob("session_*.json"), key=lambda p: p.name)
            for old in kept[:-self.archive_keep]:
                old.unlink(missing_ok=True)
            log.info("이전 세션 보관: %s", dest)
            return dest
        except OSError as e:   # 보관 실패로 앱이 죽지 않는다
            log.warning("세션 보관 실패(%s): %s", folder, e)
            return None

    def looks_like_new_game(self, state: GameState) -> bool:
        """스테이지가 1-x로 되돌아갔다(세션은 2-1 이상) → 새 판 후보. 로딩 화면을 놓쳤을 때의 보조 신호.

        OCR 오독일 수 있으므로 루프는 이것도 game_over와 같은 연속 확인을 거친다.
        """
        cur, known = stage_key(state.stage), stage_key(self.data.stage)
        return cur is not None and known is not None and cur[0] == 1 and known >= (2, 1)

    def observe(self, recognized: GameState, groups: Collection[str], owned_row: Any = None,
                board_read: Any = None) -> GameState:
        """부분 인식 결과를 받아 합친 GameState를 돌려준다(advisor 입력).

        `owned_row`: 이번 프레임에 읽은 보유 증강 줄(`vision.augment_learn.OwnedRow`, 칸 그림 + 칸별 ID). 선택 순간 학습용.
        `board_read`: 이번 프레임의 보드 판독(자리·성급·장착 아이템, `app.unit_merge.BoardRead`). 정체는 장부가 채운다.
        """
        with self._lock:
            prev = self.state
            merged = merge_state(prev, recognized, groups)
            self.data.frames += 1
            self.last_events = []
            if merged.stage:
                self.data.stage = merged.stage
            self._track_shop(prev, merged)
            self._track_units(merged)
            self._track_augments(recognized, merged)
            self._track_offer_pool(recognized)
            if owned_row is not None:
                self._learn_owned(owned_row)
            merged = self._apply_augments(merged)
            merged = self._apply_units(merged, board_read)
            self.state = merged
            return merged

    def _track_shop(self, prev: GameState | None, cur: GameState) -> None:
        """상점 칸이 '챔피언 → 빈 칸'으로 바뀌면 구매로 본다(새로고침은 여러 칸이 한꺼번에 바뀌므로 제외)."""
        cur_slots = _shop_key(cur)
        if cur_slots is None:
            self._prev_shop = None
            return
        prev_slots = self._prev_shop
        self._prev_shop = cur_slots
        if prev_slots is None or len(prev_slots) != len(cur_slots):
            return
        changed = [i for i, (a, b) in enumerate(zip(prev_slots, cur_slots)) if a != b]
        if not changed or len(changed) > 2:   # 새로고침·라운드 전환은 여러 칸이 동시에 바뀐다
            return
        for i in changed:
            before, after = prev_slots[i], cur_slots[i]
            if before is not None and after is None:
                self.data.purchases[before[1]] += 1

    def owned_champions(self) -> list[str]:
        """장부에 있는 보유 챔피언 ID(이름 뒷받침 힌트용, `vision.units.UnitNamer.set_hints`)."""
        with self._lock:
            return sorted(cid for cid, n in self.data.units.copies.items() if cid and n > 0)

    # ------------------------------------------------------------ 보유 유닛 장부
    def _track_units(self, merged: GameState) -> None:
        """상점 칸 + 골드 변화 → 구매/판매 이벤트를 장부에 반영한다(`app.ledger`)."""
        if not self.ledger_cfg.enabled:
            return
        try:
            obs = FrameObs.of(merged, float(self._now()), self.costs)
            events = self.purchases.observe(obs, self.data.units)
        except Exception:   # 추론 실패로 루프가 죽지 않는다(장부는 보조 수단이다)
            log.exception("보유 유닛 추론 실패")
            return
        if events:
            self.data.units.apply(events)
            self.last_events = list(events)

    def _apply_units(self, state: GameState, board_read: Any = None) -> GameState:
        """장부(정체) + vision 판독(자리·성급·아이템) → `board`/`bench`/`items.equipped`. 둘 다 모르면 그대로 둔다.

        이번 프레임에 보드 묶음을 읽지 않았으면(`board_read is None`) **직전 판독을 이어 쓴다** — 보드 묶음은
        화면이 바뀐 프레임에서만 다시 읽히므로(`change.roi_groups`), 매번 지우면 자리·성급·장착 아이템이
        한 프레임마다 사라졌다 나타난다.
        """
        ledger = self.data.units
        obs = board_obs_from(board_read)
        if obs is not None:
            self._board_obs = obs
        if obs is None and state.field_source.get("board") == FieldSource.VISION:
            obs = board_obs_from_state(state)   # vision이 정체까지 채운 경우(장부보다 우선한다)
        if obs is None:
            obs = self._board_obs
        if obs is None and not ledger.copies:
            # 아는 것이 없다 → "보드 미인식". 직전 프레임에서 장부로 만들어 둔 값이 이어져 있으면 지운다
            # (장부를 비웠는데 옛 보드가 남아 advisor에 가면 안 된다).
            if state.board is None or state.field_source.get("board") == FieldSource.VISION:
                return state
            state = with_equipped(state, [])
            return state.model_copy(update={
                "board": None, "bench": None,
                "confidence": {k: v for k, v in state.confidence.items() if k not in ("board", "bench")},
                "field_source": {k: v for k, v in state.field_source.items() if k not in ("board", "bench")},
            })
        result = merge_units(ledger, obs, level=state.level, cfg=self.ledger_cfg, costs=self.costs)
        self.last_merge = result
        return state_with_units(state, result, obs, costs=self.costs)

    # 수동 교정 API — 오버레이·트레이·콘솔이 그대로 부른다(`app.units_cmd`가 문자열 명령을 여기로 옮긴다).
    def add_unit(self, champion_id: str, copies: int = 1, *, star: int | None = None,
                 source: str = "manual") -> None:
        """유닛을 장부에 더한다. 상점 밖 획득(공동 선택·증강·모루)도 이 경로를 쓴다.

        `star`를 주면 그 성급의 등가 사본 수로 더한다(2성 = 3사본).
        """
        with self._lock:
            n = copies * (copies_for_star(star) if star else 1)
            self.data.units.add(champion_id, n, source=source, at=float(self._now()))
            self._refresh_units()

    def remove_unit(self, champion_id: str, copies: int = 1, *, star: int | None = None) -> None:
        """유닛을 장부에서 뺀다(잘못 추적했거나 판매를 놓쳤을 때). `copies=0`이면 전부 지운다."""
        with self._lock:
            n = (copies * copies_for_star(star)) if star else copies
            if copies <= 0:
                n = self.data.units.copies.get(champion_id, 0)
            self.data.units.remove(champion_id, n, source="manual", at=float(self._now()))
            self._refresh_units()

    def set_unit_star(self, champion_id: str, star: int) -> None:
        """"이 챔피언은 N성입니다" → 사본 수를 맞춘다."""
        with self._lock:
            self.data.units.set_star(champion_id, star, source="manual")
            self._refresh_units()

    def set_units(self, mapping: Mapping[str, int] | Iterable[str], *, source: str = "manual") -> None:
        """장부를 통째로 정한다(챔피언 → 사본 수, 또는 ID 목록)."""
        with self._lock:
            self.data.units.clear(source=source)
            items = mapping.items() if isinstance(mapping, Mapping) else ((c, 1) for c in mapping)
            for cid, n in items:
                self.data.units.set_copies(str(cid), int(n), source=source)
            self._refresh_units()

    def clear_units(self) -> None:
        with self._lock:
            self.data.units.clear(source="manual")
            self._refresh_units()

    def confirm_units(self) -> None:
        """"지금 장부가 맞습니다" — 쌓인 '설명되지 않은 거래' 수를 지워 신뢰도를 되돌린다."""
        with self._lock:
            self.data.units.confirm()
            self._refresh_units()

    def units_rows(self) -> list[tuple[str, int, int, str]]:
        """(챔피언 ID, 사본 수, 성급, 출처) 목록 — 표시·수동 교정 UI용."""
        return self.data.units.summary_rows()

    def _refresh_units(self) -> None:
        """장부가 바뀌었다 → 마지막 상태의 board/bench를 다시 만들고 저장한다(다음 추천부터 반영)."""
        if self.state is not None:
            self.state = self._apply_units(self.state)
        self.save()

    def _same_augment(self, a: str | None, b: str | None) -> bool:
        """같은 증강인가. 이름이 다르더라도 같은 그림이면 같다고 본다(학습기가 있을 때)."""
        if a is None or b is None:
            return False
        if a == b:
            return True
        try:
            return bool(self.learner is not None and self.learner.same_picture(a, b))
        except Exception:
            return False

    def _track_augments(self, recognized: GameState, merged: GameState) -> None:
        """증강 후보 기록 + 이번 프레임 vision 보유 증강 판독을 '늘기만 한다' 규칙으로 걸러 세션에 반영."""
        self._aug_accepted = False
        if merged.augment_offer:
            self.data.last_offer = [a.id for a in merged.augment_offer]
            self.data.last_offer_stage = merged.stage
        if not recognized.augments_owned:
            return
        ids = [a.id for a in recognized.augments_owned]
        verdict = self._augment_verdict(ids, merged.stage or self.data.stage)
        if isinstance(verdict, str):
            self.augments_rejected += 1
            log.info("보유 증강 판독을 버립니다(%s): vision %s / 세션 %s(%s)", verdict, ids,
                     self.data.augments_owned, self.data.augments_source)
            return
        new_ids, source = verdict
        self.data.augments_owned = new_ids
        self.data.augments_source = source
        self._aug_accepted = source == "vision" and new_ids == ids

    def _augment_verdict(self, ids: list[str], stage: str | None) -> tuple[list[str], str] | str:
        """(새 목록, 출처) 또는 버리는 이유(str). 규칙은 모듈 docstring '보유 증강' 참고."""
        known = list(self.data.augments_owned)
        src = self.data.augments_source or "vision"
        if len(ids) < len(known):
            return f"칸 수 감소 {len(known)}->{len(ids)}"
        for i, k in enumerate(known):
            if not self._same_augment(ids[i], k):
                why = "수동 입력 우선" if src == "manual" else "한 판 안에서 기존 칸은 바뀌지 않습니다"
                return f"{i + 1}번째 칸이 다름({why})"
        if len(ids) > len(known):
            cap = augments_allowed_at(stage)
            if cap is None:
                return "스테이지를 몰라 칸 수를 늘리지 않습니다"
            if len(ids) > cap:
                return f"{stage}에는 증강이 최대 {cap}개"
        new_ids = known + ids[len(known):]
        if len(ids) == len(known):
            return new_ids, src
        if not known or src == "vision":
            return new_ids, "vision"
        return new_ids, "tracked"   # 수동/추적 목록 + vision이 읽은 새 칸

    def _track_offer_pool(self, recognized: GameState) -> None:
        """증강 선택 화면의 후보 → 학습 대기 목록(리롤 포함). 새 증강 라운드면 이전 목록을 버린다."""
        offer = recognized.augment_offer
        if not offer:
            return
        d = self.data
        stage = recognized.stage or d.stage
        if d.offer_pool and d.offer_pool_stage != stage:
            log.info("증강 학습: 이전 제시 목록(%s)을 쓰지 못하고 새 라운드(%s)", d.offer_pool_stage, stage)
            d.offer_pool = []
        if not d.offer_pool:
            d.offer_pool_stage = stage
            d.offer_base = d.owned_count
        for a in offer:
            if a.id and a.id not in d.offer_pool:
                d.offer_pool.append(a.id)

    def _clear_offer_pool(self) -> None:
        self.data.offer_pool = []
        self.data.offer_pool_stage = None
        self.data.offer_base = None

    def _learn_owned(self, row: Any) -> None:
        """준비 화면 보유 증강 줄 → 칸 수 추적 + (제시 목록이 있고 새 칸이 정확히 1개면) 그 칸을 후보 안에서 확정."""
        d = self.data
        ids = list(getattr(row, "ids", []) or [])
        n = len(ids)
        known = d.augments_owned
        cap = augments_allowed_at(d.stage)
        if (n < len(known) or (cap is not None and n > cap)
                or any(ids[i] is not None and not self._same_augment(ids[i], known[i]) for i in range(len(known)))):
            # 내 줄이 아니다(상대 보드 관전 등). 칸 수 추적·학습에 쓰지 않는다.
            log.info("보유 증강 줄이 세션과 맞지 않아 학습에 쓰지 않습니다: 칸 %d, 판독 %s / 세션 %s", n, ids, known)
            return
        d.owned_count = n
        if not d.offer_pool or d.offer_base is None or n == d.offer_base:
            return   # 학습할 것이 없거나, 고른 증강이 아직 줄에 나타나지 않았다
        pool = list(d.offer_pool)
        if n != d.offer_base + 1:
            log.info("증강 학습 안 함: 칸 수 %s -> %d (새 칸이 정확히 1개가 아닙니다)", d.offer_base, n)
            self._clear_offer_pool()
            return
        learner = self.learner
        same = learner.same_picture if learner is not None else (lambda a, b: a == b)
        new_id = ids[-1]
        record: dict[str, Any] = {"stage": d.stage, "offered": pool}
        if new_id is not None:
            if not any(same(new_id, c) for c in pool):
                log.warning("증강 학습 안 함: 새 칸을 제시되지 않은 증강(%s)으로 읽었습니다(후보 %s)", new_id, pool)
                self._clear_offer_pool()
                return
            api = new_id
            record.update(api=api, reason="vision")
        else:
            if learner is None:
                return
            cells = list(getattr(row, "cells", []) or [])
            if len(cells) != n:
                return
            decision = learner.decide(cells[-1], pool)
            if decision is None:
                return   # 다음에 줄이 다시 읽힐 때 재시도. 새 라운드가 오면 목록은 버려진다.
            api = decision.api
            saved = learner.commit(cells[-1], api)
            record.update(api=api, reason=decision.reason, score=decision.score, margin=decision.margin,
                          template=str(saved) if saved else None)
            log.info("증강 학습: %d칸째 = %s (%s, 점수 %.3f, 차 %.3f)", n, api, decision.reason,
                     decision.score, decision.margin)
        d.learned.append(record)
        prev = d.augments_owned if len(d.augments_owned) == n - 1 else None
        known = [ids[i] or (prev[i] if prev else None) for i in range(n - 1)]
        if all(known):
            d.augments_owned = [*known, api]
            d.augments_source = "tracked"
        self._clear_offer_pool()
        self.save()

    def set_augments_owned(self, ids: Iterable[str], source: str = "manual") -> None:
        """사용자 수동 입력(또는 외부 확인). advisor에 `field_source=manual`로 전달된다."""
        self.data.augments_owned = [str(i) for i in ids]
        self.data.augments_source = source
        self.save()

    def _apply_augments(self, state: GameState) -> GameState:
        """advisor에 가는 보유 증강 = 세션 값. 이번 프레임 vision 값을 그대로 받았으면 vision 신뢰도를 살린다.

        규칙에 걸려 버린 vision 값이나 직전 프레임에서 이어진 값은 세션 값으로 바꾼다. 세션이 모르면 None.
        """
        ids = self.data.augments_owned
        if not ids:
            if state.augments_owned is None:
                return state
            return state.model_copy(update={
                "augments_owned": None,
                "field_source": {k: v for k, v in state.field_source.items() if k != "augments_owned"},
                "confidence": {k: v for k, v in state.confidence.items() if k != "augments_owned"},
            })
        if self._aug_accepted and state.augments_owned is not None and [a.id for a in state.augments_owned] == ids:
            return state
        source = {"manual": FieldSource.MANUAL, "vision": FieldSource.VISION}.get(
            self.data.augments_source or "", FieldSource.TRACKED)
        refs = [AugmentRef(id=i) for i in ids[:4]]
        return state.model_copy(update={
            "augments_owned": refs,
            "field_source": {**state.field_source, "augments_owned": source},
            "confidence": {**state.confidence, "augments_owned": 1.0},
        })

    # ------------------------------------------------------------------ 표시
    def summary(self) -> str:
        d = self.data
        bits = [f"프레임 {d.frames}", f"인식 {d.recognitions}"]
        if d.stage:
            bits.insert(0, f"스테이지 {d.stage}")
        if d.augments_owned:
            bits.append(f"증강 {len(d.augments_owned)}개({d.augments_source})")
        if d.purchases:
            bits.append(f"구매추정 {sum(d.purchases.values())}")
        if d.units.copies:
            bodies = sum(len(bodies_for(n)) for n in d.units.copies.values())
            bits.append(f"보유 유닛 {bodies}기({len(d.units.copies)}종)")
        if d.units.ambiguous:
            bits.append(f"미확인 거래 {d.units.ambiguous}건")
        return " · ".join(bits)


def _opt_int(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _shop_key(state: GameState) -> list[tuple[str, str] | None] | None:
    """상점 칸 비교용 키. 빈 칸/인식 실패는 None."""
    if state.shop is None:
        return None
    out: list[tuple[str, str] | None] = []
    for slot in state.shop:
        if slot.kind in (ShopSlotKind.CHAMPION, ShopSlotKind.SPECIAL) and slot.id:
            out.append((str(slot.kind), slot.id))
        else:
            out.append(None)
    return out


def session_path(state_dir: str | Path, root: Path | None = None) -> Path:
    """`{app.state_dir}/session.json`의 절대 경로."""
    from ..static_data import PROJECT_ROOT

    base = Path(state_dir)
    if not base.is_absolute():
        base = (root or PROJECT_ROOT) / base
    return base / "session.json"
