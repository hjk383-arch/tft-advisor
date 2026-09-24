"""실시간 루프 — 캡처 → 변화 감지 → (바뀐 묶음만) 인식 → 세션 병합 → 추천.

    캡처(capture_fps)
      → ChangeDetector.update  : 바뀌었고 change_stable_frames 연속 같은 ROI 묶음만
      → Recognizer.recognize(groups=…)  : 그 묶음만 읽는다(전체 인식은 0.4~0.9s, 부분은 0.2s 안팎)
      → SessionTracker.observe : 직전 상태에 병합 + 보유 증강·보유 유닛(구매 추적) 갱신
      → AdviceRunner.submit    : 별도 스레드에서 advisor.advise (UI 스레드·캡처 스레드를 막지 않는다)
      → on_update 콜백         : 오버레이/콘솔이 그린다

화면 모드 계약(advisor와 같다, `session.RESET_MODES`/`KEEP_MODES`)
- loading / game_over : 세션·advisor 세션을 초기화하고 표시를 비운다. 단 **한 번의 판정으로는 초기화하지 않는다**
  (10 app, QA08 A3: "나가기" 글자 하나로 game_over 0.8 → 수동 증강까지 잃었다). 화면 신뢰도가
  `app.reset_strong_confidence`(0.95, "최종 순위"+"나가기") 이상이면 즉시, 아니면 `reset_confirm_frames`번 연속 또는
  첫 관측 후 `reset_confirm_s`초 뒤 다시 관측될 때 초기화한다. 확인 대기 중에는 화면이 안 바뀌어도
  `reset_recheck_s`마다 다시 판별한다(정지 화면은 변화 감지에 안 걸린다). 초기화 전 세션은 `_state/sessions/`에 보관한다.
  스테이지가 1-x로 되돌아간 것(`SessionTracker.looks_like_new_game`)도 같은 확인을 거쳐 새 판으로 본다.
- combat / item_select / unknown : **추천을 다시 계산하지 않는다**(직전 추천 유지, 목표 덱 고정). 상태 병합은 계속하고,
  표시용 사본에서 이미 산(빈 칸이 된)·바뀐 상점 칸을 빼며 "직전 추천(전투 중)" 표시를 단다(`report.kept_view`).
- carousel : advisor가 Jev 없이 재료 우선순위만 갱신한다.
- planning / augment_select : 정상 추천.

전투 판별(vision 07)이 생기면서 전투 중(라운드의 절반쯤)에는 새 추천이 없다. 전투 중 상점 새로고침에 대한 가벼운
재계산은 advisor 계약(COMBAT → 직전 추천) 변경이 필요해 하지 않았다(10 app 보고서).
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
from .report import KeptInfo, kept_view
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
    kept: KeptInfo | None = None
    """직전 추천을 보여 주는 화면이면 표시 정보(`recommendation`은 이미 거른 표시용 사본이다)."""
    board_read: Any = None
    """마지막 vision 보드 판독(`Recognizer.last_board_read`, 이번 프레임에 못 읽었으면 직전 것). 인식 확인 창용."""
    recog_ms: float | None = None
    """이번 프레임 인식에 걸린 시간(ms). 인식이 없었던 갱신(추천 결과 등)은 None."""


# ---------------------------------------------------------------------------
# 추천 실행기 — 인식 스레드도 UI 스레드도 막지 않는다
# ---------------------------------------------------------------------------


class AdviceRunner(Protocol):
    def submit(self, state: GameState) -> None: ...

    def set_advisor(self, advisor: Any) -> None: ...

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

    def set_advisor(self, advisor: Any) -> None:
        old, self.advisor = self.advisor, advisor
        if old is not advisor:
            _close_advisor(old)

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
        self._next_advisor: Any = None      # 교체 요청(실제 교체는 추천 스레드 안에서)
        self.advisor_swapped = threading.Event()   # 교체가 끝날 때마다 set (테스트·전환기 대기용)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, state: GameState) -> None:
        with self._lock:
            self._pending = state
        self._wake.set()

    def set_advisor(self, advisor: Any) -> None:
        """실행 중 advisor 교체(트레이 "Jev 실시간 판단" 토글).

        교체는 **추천 스레드 안에서** 일어난다: 계산 중인 호출을 끊지 않고, 옛 advisor의 전용
        이벤트 루프(`Advisor.close()`)도 그 루프를 만든 스레드에서 닫는다. 교체만으로는 Jev를
        부르지 않는다 — 새 백엔드는 다음 `submit()`부터 쓰인다.
        """
        with self._lock:
            self._next_advisor = advisor
        self.advisor_swapped.clear()
        self._wake.set()

    def _swap_if_requested(self) -> None:
        with self._lock:
            new, self._next_advisor = self._next_advisor, None
        if new is None:
            return
        if new is not self.advisor:
            old, self.advisor = self.advisor, new
            _close_advisor(old)
            log.info("Jev 백엔드 교체: %s → %s", getattr(old, "backend_name", "?"),
                     getattr(new, "backend_name", "?"))
        self.advisor_swapped.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.2)
            self._wake.clear()
            self._swap_if_requested()
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
        _close_advisor(self.advisor)


def _close_advisor(advisor: Any) -> None:
    if advisor is None:
        return
    try:
        advisor.close()
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
        if self.tracker.learner is None:   # 보유 증강 선택 순간 학습(vision 09). 인식기에 없으면 학습하지 않는다.
            self.tracker.learner = getattr(recognizer, "augment_learner", None)
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
        self._pending_reset: dict[str, Any] | None = None   # 새 판 확인 대기: mode, first, count, recheck
        self.last_board_read: Any = None      # 마지막 보드 판독(인식 확인 창이 칸별 이름 출처를 보려고 쓴다)
        self.last_recog_ms: float | None = None

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
            if not self._recheck_due(now):
                return None
            changed = {"stage"}   # 새 판 확인: 정지 화면이라도 화면 판별만 다시 한다
            groups = {"stage"}
        else:
            groups = self._groups(changed, now)
        t0 = time.perf_counter()
        try:
            state = self.recognizer.recognize(
                image, content=content, source_image=frame.source,
                captured_at=frame.captured_at, groups=sorted(groups))
        except Exception as e:
            log.exception("인식 실패: %s", frame.source)
            return self._emit(LoopUpdate(kind="error", message=f"인식 실패: {e}"))
        self.last_recog_ms = (time.perf_counter() - t0) * 1000.0
        self.tracker.data.recognitions += 1
        if self.debug_dir is not None:
            self._dump_debug(image, state)
        board_read = getattr(self.recognizer, "last_board_read", None)
        if board_read is not None:
            self.last_board_read = board_read
        update = self._handle(state, groups, owned_row=getattr(self.recognizer, "last_owned_row", None),
                              board_read=board_read)
        self.last_recog_ms = None   # 다음 갱신(추천 결과 등)에 이번 인식 시간이 묻어가지 않게 한다
        return update

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

    def _recheck_due(self, now: float) -> bool:
        p = self._pending_reset
        if p is None or now - p["recheck"] < self.settings.app.reset_recheck_s:
            return False
        p["recheck"] = now
        return True

    def _reset_confirmed(self, state: GameState, reason: str, now: float) -> bool:
        """새 판 신호 → 지금 초기화할지. 강한 신호면 즉시, 아니면 연속 관측·경과 시간으로 확인한다."""
        cfg = self.settings.app
        if state.screen_mode in RESET_MODES and state.confidence_of("screen_mode") >= cfg.reset_strong_confidence:
            return True
        p = self._pending_reset
        if p is None or p["reason"] != reason:
            self._pending_reset = {"reason": reason, "first": now, "count": 1, "recheck": now}
            log.info("새 판 신호(%s, 신뢰도 %.2f) — 확인 대기", reason, state.confidence_of("screen_mode"))
            return cfg.reset_confirm_frames <= 1
        p["count"] += 1
        return p["count"] >= cfg.reset_confirm_frames or now - p["first"] >= cfg.reset_confirm_s

    def _do_reset(self, state: GameState, groups: Collection[str], reason: str) -> LoopUpdate:
        self._pending_reset = None
        self.tracker.reset(reason)
        if self.advisor is not None:
            self.advisor.reset()
        self.last_recommendation = None
        self.last_state = None
        self.last_advice_at = None
        self.last_board_read = None
        note = f"보관: {self.tracker.last_archive.name}" if self.tracker.last_archive else None
        return self._emit(LoopUpdate(kind="reset", state=state, recognized=tuple(sorted(groups)), message=note))

    def _handle(self, state: GameState, groups: Collection[str], owned_row: Any = None,
                board_read: Any = None) -> LoopUpdate:
        mode = state.screen_mode
        now = self.clock()
        reason = None
        if mode in RESET_MODES:
            reason = f"화면 {mode.value}"
        elif self.tracker.looks_like_new_game(state):
            reason = f"스테이지 {state.stage}로 되돌아감"
        if reason is not None:
            if self._reset_confirmed(state, reason, now):
                return self._do_reset(state, groups, reason)
            # 확인 대기: 세션 상태를 바꾸지 않고(스테이지도 병합하지 않는다) 직전 추천을 유지한다
            return self._emit(self._kept_update(state, groups, message=f"새 판 확인 중({reason})"))
        else:
            self._pending_reset = None

        merged = self.tracker.observe(state, groups, owned_row=owned_row, board_read=board_read)
        self.last_state = merged
        if mode in KEEP_MODES:
            # 직전 추천을 그대로 둔다(advisor 계약, 목표 덱 고정). 표시용 사본에서 산·바뀐 상점 칸만 뺀다.
            return self._emit(self._kept_update(merged, groups))
        self.runner.submit(merged)
        return self._emit(LoopUpdate(kind="recognized", state=merged, recommendation=self.last_recommendation,
                                     recognized=tuple(sorted(groups))))

    def _kept_update(self, state: GameState, groups: Collection[str], message: str | None = None) -> LoopUpdate:
        rec, kept = self.last_recommendation, None
        if rec is not None:
            rec, kept = kept_view(rec, state)
        return LoopUpdate(kind="kept", state=state, recommendation=rec, recognized=tuple(sorted(groups)),
                          kept=kept, message=message)

    def _on_advice(self, state: GameState, rec: Recommendation | None) -> None:
        """추천 스레드에서 호출된다. 오버레이는 이 콜백을 Qt 시그널로 UI 스레드에 넘긴다.

        계산하는 사이 화면이 전투 등으로 넘어갔으면 그 화면 기준의 표시용 사본(산 칸 제외)을 보낸다.
        """
        if rec is not None:
            self.last_recommendation = rec
            self.last_advice_at = datetime.now(UTC)
        shown, kept, cur = rec, None, self.last_state
        if rec is not None and cur is not None and cur.screen_mode in KEEP_MODES:
            shown, kept = kept_view(rec, cur)
        self._emit(LoopUpdate(kind="advice", state=state, recommendation=shown, kept=kept))

    def _emit(self, update: LoopUpdate) -> LoopUpdate:
        if update.kind != "advice":   # 인식 확인 창: 판독·인식 시간을 같은 갱신에 싣는다(추가 인식 없음)
            if update.board_read is None:
                update.board_read = self.last_board_read
            if update.recog_ms is None:
                update.recog_ms = self.last_recog_ms
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
            from ..vision.capture import save_image
            from ..vision.regions import FrameMapper, draw_rois

            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%H%M%S_%f")[:-3]
            (self.debug_dir / f"{stamp}.json").write_text(
                state.model_dump_json(indent=1, exclude_none=True), encoding="utf-8")
            content = self._content(image)
            mapper = FrameMapper.for_image(image, content)
            _, _, bw, bh = mapper.box
            profile = self.recognizer.profile_for(bw, bh)
            save_image(self.debug_dir / f"{stamp}_rois.png", draw_rois(image, profile, mapper))
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
                log.exception("루프 오류 — 계속 진행합니다")
            wait = interval - (self.clock() - started)
            if wait > 0:
                self.sleep(wait)

    def set_advisor(self, advisor: Any) -> None:
        """실행 중 advisor를 갈아 끼운다(트레이 Jev 토글 → `app/jev_toggle.py`).

        표시 중인 추천은 그대로 두고 **다음 추천부터** 새 백엔드를 쓴다. 교체 자체는 Jev를 부르지 않는다.
        """
        self.advisor = advisor
        setter = getattr(self.runner, "set_advisor", None)
        if setter is None:   # 외부에서 넣은 러너
            self.runner.advisor = advisor
        else:
            setter(advisor)

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

    def set_advisor(self, advisor: Any) -> None:
        pass

    def close(self) -> None:
        pass
