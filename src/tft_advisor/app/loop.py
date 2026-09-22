"""실시간 루프 — 캡처 → 변화 감지 → (바뀐 묶음만) 인식 → 세션 병합 → 추천.

    캡처(capture_fps)
      → ChangeDetector.update  : 바뀌었고 change_stable_frames 연속 같은 ROI 묶음만
      → Recognizer.recognize(groups=…)  : 그 묶음만 읽는다(전체 인식은 0.4~0.9s, 부분은 0.2s 안팎)
      → SessionTracker.observe : 직전 상태에 병합 + 보유 증강·구매 추적
      → AdviceRunner.submit    : 별도 스레드에서 advisor.advise (UI 스레드·캡처 스레드를 막지 않는다)
      → on_update 콜백         : 오버레이/콘솔이 그린다

화면 모드 계약(advisor와 같다, `session.RESET_MODES`/`KEEP_MODES`)
- loading / game_over : 세션·advisor 세션을 초기화하고 표시를 비운다.
- combat / item_select / unknown : **추천을 다시 계산하지 않는다**(직전 추천 유지). 상태 병합은 계속한다.
- carousel : advisor가 Jev 없이 재료 우선순위만 갱신한다.
- planning / augment_select : 정상 추천.

알려진 제약: vision이 **전투 화면을 planning으로 오분류**한다(같은 라운드의 준비/전투 쌍 캡처가 있어야 고칠 수 있다,
05_vision_aspect_and_labels.md §7). 그래서 루프는 애매한 화면에서 **상태를 버리지 않고**(`session.merge_state`),
추천이 흔들리지 않도록 **자원 시그니처가 같으면 advisor가 직전 결과를 유지**하는 구조에 의존한다. 전투 중에도
상점 구매는 가능하므로 planning으로 처리해도 해롭지 않다.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from ..config import Settings, load_settings
from ..contracts import GameState, Recommendation
from .session import KEEP_MODES, RESET_MODES, SessionTracker

log = logging.getLogger(__name__)

UpdateKind = Literal["recognized", "kept", "reset", "advice", "error"]


@dataclass
class LoopUpdate:
    """루프가 UI에 보내는 한 번의 갱신."""

    kind: UpdateKind
    state: GameState | None = None
    recommendation: Recommendation | None = None
    recognized: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    message: str | None = None


# ---------------------------------------------------------------------------
# 추천 실행기 — 인식 스레드도 UI 스레드도 막지 않는다
# ---------------------------------------------------------------------------


class AdviceRunner(Protocol):
    def submit(self, state: GameState) -> None: ...

    def close(self) -> None: ...


class InlineAdviceRunner:
    """같은 스레드에서 즉시 추천(테스트·`--screenshot`)."""

    def __init__(self, advisor: Any, on_result: Callable[[GameState, Recommendation | None], None]) -> None:
        self.advisor = advisor
        self.on_result = on_result

    def submit(self, state: GameState) -> None:
        try:
            rec = self.advisor.advise(state)
        except Exception:   # 추천 실패로 루프가 죽지 않는다
            log.exception("추천 실패 (state stage=%s mode=%s)", state.stage, state.screen_mode)
            return
        self.on_result(state, rec)

    def close(self) -> None:
        pass


class ThreadAdviceRunner:
    """추천 전용 스레드. **가장 최근 상태만** 계산한다(뒤이어 새 상태가 오면 앞의 것은 버린다).

    `Advisor.advise()`는 자기 전용 이벤트 루프를 재사용하므로(Jev 연결 유지) 한 스레드에 고정해야 한다.
    """

    def __init__(self, advisor: Any, on_result: Callable[[GameState, Recommendation | None], None],
                 name: str = "tft-advisor") -> None:
        self.advisor = advisor
        self.on_result = on_result
        self._lock = threading.Lock()
        self._pending: GameState | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, state: GameState) -> None:
        with self._lock:
            self._pending = state
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.2)
            self._wake.clear()
            with self._lock:
                state, self._pending = self._pending, None
            if state is None:
                continue
            try:
                rec = self.advisor.advise(state)
            except Exception:
                log.exception("추천 실패 (stage=%s mode=%s)", state.stage, state.screen_mode)
                continue
            self.on_result(state, rec)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=3.0)
        try:
            self.advisor.close()
        except Exception:
            log.debug("advisor.close 실패", exc_info=True)


# ---------------------------------------------------------------------------
# 루프
# ---------------------------------------------------------------------------


class LiveLoop:
    """캡처 → 인식 → 추천. 이 객체 자체는 UI를 모른다(콜백만 부른다).

    테스트를 위해 프레임 소스·시계·인식기·추천 실행기를 전부 주입할 수 있다.
    """

    def __init__(self, *, source: Any, recognizer: Any, settings: Settings | None = None,
                 advisor: Any = None, runner: AdviceRunner | None = None,
                 tracker: SessionTracker | None = None, detector: Any = None,
                 on_update: Callable[[LoopUpdate], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 debug_dir: Path | None = None) -> None:
        self.settings = settings or load_settings()
        self.source = source
        self.recognizer = recognizer
        self.tracker = tracker if tracker is not None else SessionTracker()
        self.on_update = on_update or (lambda u: None)
        self.clock = clock
        self.sleep = sleep
        self.debug_dir = debug_dir
        self.detector = detector if detector is not None else self._make_detector()
        if runner is not None:
            self.runner = runner
        elif advisor is not None:
            self.runner = ThreadAdviceRunner(advisor, self._on_advice)
        else:
            self.runner = _NullRunner()
        self.advisor = advisor
        self.last_recommendation: Recommendation | None = None
        self.last_state: GameState | None = None
        self.last_advice_at: datetime | None = None
        self.source_exhausted = False
        self._last_traits = -1e9
        self._frames = 0
        self._debug_dumps = 0

    def _make_detector(self):
        from ..vision.change import ChangeDetector

        return ChangeDetector.from_cfg(self.recognizer.profile, self.settings.vision)

    # ------------------------------------------------------------------
    def step(self) -> LoopUpdate | None:
        """프레임 1장 처리. 변화가 없으면 None(= 아무 일도 하지 않음)."""
        frame = self.source.grab()
        if frame is None:
            self.source_exhausted = True
            return None
        self._frames += 1
        image = frame.image
        content = self._content(image)
        changed = set(self.detector.update(image, content=content))
        now = self.clock()
        if not changed:
            return None
        groups = self._groups(changed, now)
        try:
            state = self.recognizer.recognize(
                image, content=content, source_image=frame.source,
                captured_at=frame.captured_at, groups=sorted(groups))
        except Exception as e:
            log.exception("인식 실패: %s", frame.source)
            return self._emit(LoopUpdate(kind="error", message=f"인식 실패: {e}"))
        self.tracker.data.recognitions += 1
        if self.debug_dir is not None:
            self._dump_debug(image, state)
        return self._handle(state, groups)

    def _content(self, image) -> tuple[int, int, int, int] | None:
        fn = getattr(self.recognizer, "content_for", None)
        if fn is not None:
            return fn(image, None)
        h, w = image.shape[:2]
        return self.settings.vision.content_px(w, h)

    def _groups(self, changed: set[str], now: float) -> set[str]:
        """이번에 읽을 인식 묶음. "stage"가 바뀌면 화면 자체가 바뀐 것이라 전체를 다시 읽는다."""
        from ..vision.recognizer import DEFAULT_GROUPS, GROUPS

        groups = set(changed) & set(GROUPS)
        if "stage" in changed:
            groups |= set(DEFAULT_GROUPS)
        if groups and now - self._last_traits >= self.settings.vision.traits_every_s:
            groups.add("traits")
            self._last_traits = now
        return groups or set(DEFAULT_GROUPS)

    def _handle(self, state: GameState, groups: Collection[str]) -> LoopUpdate:
        mode = state.screen_mode
        if mode in RESET_MODES:
            self.tracker.reset(f"화면 {mode.value}")
            if self.advisor is not None:
                self.advisor.reset()
            self.last_recommendation = None
            self.last_state = None
            self.last_advice_at = None
            return self._emit(LoopUpdate(kind="reset", state=state, recognized=tuple(sorted(groups))))

        merged = self.tracker.observe(state, groups)
        self.last_state = merged
        if mode in KEEP_MODES:
            # 직전 추천을 그대로 둔다(advisor 계약). 전투·알 수 없는 화면에서 추천이 흔들리지 않게.
            return self._emit(LoopUpdate(kind="kept", state=merged, recommendation=self.last_recommendation,
                                         recognized=tuple(sorted(groups))))
        self.runner.submit(merged)
        return self._emit(LoopUpdate(kind="recognized", state=merged, recommendation=self.last_recommendation,
                                     recognized=tuple(sorted(groups))))

    def _on_advice(self, state: GameState, rec: Recommendation | None) -> None:
        """추천 스레드에서 호출된다. 오버레이는 이 콜백을 Qt 시그널로 UI 스레드에 넘긴다."""
        if rec is not None:
            self.last_recommendation = rec
            self.last_advice_at = datetime.now(UTC)
        self._emit(LoopUpdate(kind="advice", state=state, recommendation=rec))

    def _emit(self, update: LoopUpdate) -> LoopUpdate:
        try:
            self.on_update(update)
        except Exception:
            log.exception("UI 갱신 콜백 실패")
        return update

    def _dump_debug(self, image, state: GameState) -> None:
        """`--debug`: 인식 결과 JSON + ROI를 그린 PNG를 남긴다(최대 200장)."""
        if self._debug_dumps >= 200:
            return
        self._debug_dumps += 1
        try:
            from ..vision.regions import FrameMapper, draw_rois
            import cv2

            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%H%M%S_%f")[:-3]
            (self.debug_dir / f"{stamp}.json").write_text(
                state.model_dump_json(indent=1, exclude_none=True), encoding="utf-8")
            content = self._content(image)
            mapper = FrameMapper.for_image(image, content)
            _, _, bw, bh = mapper.box
            profile = self.recognizer.profile_for(bw, bh)
            cv2.imwrite(str(self.debug_dir / f"{stamp}_rois.png"), draw_rois(image, profile, mapper))
        except Exception:
            log.debug("디버그 덤프 실패", exc_info=True)

    # ------------------------------------------------------------------
    def run(self, stop: threading.Event | None = None, max_frames: int | None = None) -> None:
        """`capture_fps` 주기로 step()을 반복한다. 별도 스레드에서 돌린다(UI 스레드 금지)."""
        interval = 1.0 / self.settings.vision.capture_fps
        while stop is None or not stop.is_set():
            if max_frames is not None and self._frames >= max_frames:
                return
            started = self.clock()
            try:
                self.step()
                if self.source_exhausted:   # 파일 소스(리플레이)가 끝났다
                    return
            except Exception:
                log.exception("루프 오류 — 계속 진행한다")
            wait = interval - (self.clock() - started)
            if wait > 0:
                self.sleep(wait)

    def close(self) -> None:
        self.runner.close()
        try:
            self.source.close()
        except Exception:
            log.debug("프레임 소스 닫기 실패", exc_info=True)
        self.tracker.save()


class _NullRunner:
    def submit(self, state: GameState) -> None:
        pass

    def close(self) -> None:
        pass
