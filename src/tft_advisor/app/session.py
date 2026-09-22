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
- vision이 언젠가 `augments_owned`를 채우면 그대로 받아 `field_source="vision"`으로 둔다.
- 수동 입력(`set_augments_owned`)은 `field_source="manual"`, 세션 추적값은 `"tracked"`다.
- 확정 전에는 None이다(advisor가 "모름"으로 다룬다).
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..contracts import AugmentRef, FieldSource, GameState, ScreenMode, ShopSlotKind

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
    "stage": _ALL_MODES,
    "players": _ALL_MODES,
    "hud": frozenset({ScreenMode.PLANNING}),
    "shop": frozenset({ScreenMode.PLANNING}),
    "traits": frozenset({ScreenMode.PLANNING}),
    "items": frozenset({ScreenMode.PLANNING, ScreenMode.AUGMENT_SELECT}),
    "augment": frozenset({ScreenMode.AUGMENT_SELECT}),
}
RESET_MODES = frozenset({ScreenMode.LOADING, ScreenMode.GAME_OVER})
"""이 화면을 보면 새 판으로 보고 세션을 비운다(advisor 계약과 같다)."""
KEEP_MODES = frozenset({ScreenMode.COMBAT, ScreenMode.ITEM_SELECT, ScreenMode.UNKNOWN})
"""직전 추천을 그대로 두는 화면. 새로 추천하지 않는다."""

TRANSIENT_FIELDS = frozenset({"augment_offer"})
"""그 화면에서만 존재하는 값. 화면이 바뀌면 직전 값을 이어쓰지 않는다(증강 후보가 준비 화면에 남으면 안 된다)."""

_CARRY_FIELDS = ("board", "bench", "augments_owned")
"""FIELD_GROUP에 없는(= vision이 묶음으로 읽지 않는) 관측 필드. 새 값이 있으면 쓰고 없으면 유지한다."""


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
    purchases: Counter[str] = field(default_factory=Counter)  # 상점 칸이 사라진 횟수(추정 구매)
    frames: int = 0
    recognitions: int = 0

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
            "purchases": dict(self.purchases),
            "frames": self.frames,
            "recognitions": self.recognitions,
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
            frames=int(raw.get("frames") or 0),
            recognitions=int(raw.get("recognitions") or 0),
        )
        d.purchases = Counter({str(k): int(v) for k, v in (raw.get("purchases") or {}).items()})
        return d


class SessionTracker:
    """루프의 기억. 부분 인식 병합 + 보유 증강·구매 추적 + `session.json` 영속."""

    def __init__(self, path: Path | None = None, *, max_age_s: float = SESSION_MAX_AGE_S,
                 clock: object = None) -> None:
        self.path = Path(path) if path is not None else None
        self.max_age_s = max_age_s
        self._now = clock or time.time
        self.data = SessionData()
        self.state: GameState | None = None      # 마지막으로 합친 GameState
        self._prev_shop: list[tuple[str, str] | None] | None = None

    # ------------------------------------------------------------------ 영속
    def load(self) -> bool:
        """저장된 세션을 복원한다. 파일이 없거나 오래됐으면 False."""
        if self.path is None or not self.path.is_file():
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("세션 파일을 읽지 못했다(%s): %s", self.path, e)
            return False
        if raw.get("version") != SESSION_VERSION:
            log.info("세션 파일 버전이 달라 무시한다: %s", raw.get("version"))
            return False
        data = SessionData.from_json(raw)
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(data.updated_at)).total_seconds()
        except ValueError:
            age = self.max_age_s + 1
        if age > self.max_age_s:
            log.info("세션 파일이 오래됐다(%.0f분) → 새 세션", age / 60)
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

    # ------------------------------------------------------------------ 추적
    def reset(self, reason: str = "new_game") -> None:
        """새 판: 합친 상태와 추적값을 비운다(loading/game_over)."""
        log.info("세션 초기화 (%s)", reason)
        self.data = SessionData()
        self.state = None
        self._prev_shop = None
        self.save()

    def observe(self, recognized: GameState, groups: Collection[str]) -> GameState:
        """부분 인식 결과를 받아 합친 GameState를 돌려준다(advisor 입력)."""
        prev = self.state
        merged = merge_state(prev, recognized, groups)
        self.data.frames += 1
        if merged.stage:
            self.data.stage = merged.stage
        self._track_shop(prev, merged)
        self._track_augments(merged)
        merged = self._apply_augments(merged)
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

    def _track_augments(self, cur: GameState) -> None:
        if cur.augment_offer:
            self.data.last_offer = [a.id for a in cur.augment_offer]
            self.data.last_offer_stage = cur.stage
        if cur.augments_owned:   # vision이 HUD 보유 증강을 읽으면 그대로 믿는다
            self.data.augments_owned = [a.id for a in cur.augments_owned]
            self.data.augments_source = str(cur.field_source.get("augments_owned", FieldSource.VISION))

    def set_augments_owned(self, ids: Iterable[str], source: str = "manual") -> None:
        """사용자 수동 입력(또는 외부 확인). advisor에 `field_source=manual`로 전달된다."""
        self.data.augments_owned = [str(i) for i in ids]
        self.data.augments_source = source
        self.save()

    def _apply_augments(self, state: GameState) -> GameState:
        """세션이 알고 있는 보유 증강을 상태에 얹는다(vision이 이미 채웠으면 그대로 둔다)."""
        if state.augments_owned is not None or not self.data.augments_owned:
            return state
        source = FieldSource.MANUAL if self.data.augments_source == "manual" else FieldSource.TRACKED
        refs = [AugmentRef(id=i) for i in self.data.augments_owned[:4]]
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
        return " · ".join(bits)


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
