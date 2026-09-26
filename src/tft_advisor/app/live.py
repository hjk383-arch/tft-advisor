"""`--live` 모드 배선 — 캡처 소스·인식기·추천기·세션을 만들고 루프를 오버레이(또는 콘솔)에 붙인다.

스레드 구성
- **메인 스레드**: Qt 이벤트 루프(오버레이). `--no-overlay`면 여기서 루프를 직접 돌린다.
- **캡처/인식 스레드**: `LiveLoop.run()` — mss 캡처와 OCR(프레임당 0.2~0.9s)이 전부 여기서 돈다.
- **추천 스레드**: `ThreadAdviceRunner` — `Advisor.advise()`(mock 5~10ms, live 최대 timeout_s)가 여기서 돈다.
UI 스레드에서는 인식도 Jev 호출도 하지 않는다.

종료: Ctrl+C(콘솔) 또는 트레이 메뉴 "종료". 둘 다 루프를 멈추고 세션을 저장한다.

Jev 백엔드: 시작값은 **CLI(--jev/--no-jev) > `[advisor] jev_backend`**. 오버레이 모드에서는 트레이 메뉴
"Jev 실시간 판단 (과금)" 체크로 실행 중에 live ↔ mock을 바꾼다(`jev_toggle.JevSwitcher`). CLI로 못박은
실행에서는 그 토글이 잠긴다.

인식 확인 창(`app/recog_window.py`): 시작값은 **CLI(--test-view/--no-test-view) > `[ui] test_view`**, 실행 중에는
트레이 "인식 확인 창" 체크로 켜고 끈다. `--no-overlay`(콘솔)에서는 같은 내용을 콘솔에 출력한다(`recog_view.ConsolePrinter`).
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from ..config import Settings, load_settings
from ..contracts import GameState, ScreenMode
from .loop import LiveLoop, LoopUpdate
from .names import NameBook
from .report import StatusInfo, format_report, status_line
from .session import SessionTracker, session_path
from .units_cmd import PROMPT, apply_command

log = logging.getLogger(__name__)


def overlay_available() -> bool:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


def choose_ui(settings: Settings, want_overlay: bool) -> str:
    """"pyside6" | "console". 조용히 다른 툴킷으로 바꾸지 않는다 — 안 되면 콘솔이라고 말한다."""
    if not want_overlay or not settings.overlay.enabled or settings.ui.backend == "console":
        return "console"
    if settings.ui.backend == "tkinter":
        log.warning("[ui] backend = tkinter 는 구현하지 않았습니다(Phase 4에서 PySide6로 확정) → 콘솔 모드")
        return "console"
    if not overlay_available():
        log.warning('PySide6가 없습니다 → 콘솔 모드. 설치: uv pip install --python .venv/bin/python -e ".[ui]"')
        return "console"
    return "pyside6"


def build(settings: Settings, jev: str, debug_dir: Path | None = None,
          source: object | None = None, config_dir: Path | None = None) -> tuple[LiveLoop, object]:
    """루프와 advisor를 만든다(오버레이/콘솔 공용). 반환: (loop, advisor)."""
    from ..advisor import create_advisor
    from ..vision.recognizer import Recognizer

    from .ledger import LedgerCfg

    recognizer = Recognizer(cfg=settings.vision)
    advisor = create_advisor(jev, settings=settings)
    tracker = SessionTracker(session_path(settings.app.state_dir), archive_keep=settings.app.session_archive_keep,
                             ledger_cfg=LedgerCfg.from_settings(settings))
    if tracker.load():
        log.info("이전 세션을 이어받았습니다: %s", tracker.summary())
    live_capture = source is None
    if source is None:
        from ..vision.capture import MssSource

        # monitor="auto"면 인식기의 스테이지 OCR로 게임 모니터를 고른다(듀얼 모니터, vision 07 보고)
        source = MssSource(monitor=settings.capture.monitor, scorer=recognizer.screen_score)
    loop = LiveLoop(source=source, recognizer=recognizer, settings=settings, tracker=tracker,
                    advisor=advisor, debug_dir=debug_dir)   # advisor= 면 ThreadAdviceRunner가 붙는다
    if live_capture:   # 실제 화면 캡처일 때만: "게임 화면 다시 찾기" + 게임 창 따라가기(캡처 스레드에서 돈다)
        loop.screen_hook = make_redetector(settings, config_dir)
    return loop, advisor


def unit_review_opener(recognizer):
    """"유닛 사진 검토" 창을 여는 함수(UI 스레드에서 부른다). 유닛 이름 인식이 꺼져 있으면 None.

    창을 닫을 때 바뀐 것이 있으면 `unit_namer.request_reload` → 인식 스레드가 다음 프레임에 승인 사진을 다시 읽는다.
    """
    namer = getattr(recognizer, "unit_namer", None)
    static = getattr(recognizer, "static", None)
    if namer is None or static is None:
        return None

    def open_window():
        from .unit_review import open_review_window

        return open_review_window(static, on_changed=namer.request_reload)

    return open_window


def make_redetector(settings: Settings, config_dir: Path | None = None):
    """`game_window.ScreenRedetector`(실패하면 None — 없어도 앱은 돈다)."""
    try:
        from .game_window import ScreenRedetector
        from .setup import resolve_state_dir

        red = ScreenRedetector(settings, config_dir=config_dir, state_dir=resolve_state_dir(settings))
        log.info("게임 창 따라가기: %s", f"켬({red.interval_s:g}초마다)" if red.follow else "끔")
        return red
    except Exception:   # noqa: BLE001
        log.exception("게임 화면 다시 찾기를 준비하지 못했습니다")
        return None


def warm_up(advisor) -> None:
    """live 백엔드의 첫 호출 지연(SDK import + TLS, 약 1.2s)을 앱 시작 때 미리 치른다.

    mock/off에서는 네트워크가 없으므로 그냥 파이프라인을 한 번 돌린다(정적 데이터·통계 캐시 예열).
    **추천 스레드가 뜨기 전에** 동기로 부른다 — `Advisor`는 전용 이벤트 루프를 하나 쓰므로 두 스레드에서
    동시에 부르면 안 된다.
    """
    try:
        advisor.advise(GameState(screen_mode=ScreenMode.PLANNING, stage="2-1", level=4, gold=10))
        advisor.reset()
    except Exception:
        log.debug("워밍업 실패(무시)", exc_info=True)


def run_live(*, settings: Settings | None = None, jev: str = "auto", overlay: bool = True,
             debug_dir: Path | None = None, source: object | None = None,
             max_frames: int | None = None, config_dir: Path | None = None,
             test_view: bool | None = None) -> int:
    """`test_view`: CLI 값(None = 플래그 없음 → `[ui] test_view`)."""
    settings = settings or load_settings()
    ui = choose_ui(settings, overlay)
    loop, advisor = build(settings, jev, debug_dir, source, config_dir=config_dir)
    patch = getattr(getattr(advisor, "stats", None), "meta", None)
    patch = getattr(patch, "patch", None)
    backend = getattr(advisor, "backend_name", jev)
    log.info("실시간 모드 시작 — UI %s · Jev %s · 패치 %s · %s FPS", ui, backend, patch or "?",
             settings.vision.capture_fps)
    warm_up(advisor)

    if ui == "console":
        show_recog = settings.ui.test_view if test_view is None else bool(test_view)
        return _run_console(loop, settings, backend, patch, max_frames, test_view=show_recog)
    # CLI가 백엔드를 못박은 실행(--jev/--no-jev)이면 트레이 토글을 잠근다: CLI > 토글 > 설정 파일
    return _run_overlay(loop, settings, backend, patch, max_frames, config_dir,
                        jev_cli=jev if jev in ("mock", "live", "off") else None, test_view_cli=test_view)


# ---------------------------------------------------------------------------


def start_unit_console(tracker, stop: threading.Event, stream: object | None = None) -> threading.Thread | None:
    """표준 입력에서 보유 유닛 수동 교정 명령을 읽는 데몬 스레드(`app.units_cmd`).

    대화형 터미널이 아니면(파이프·서비스 실행·테스트) 켜지 않는다. 입력을 기다리는 동안 게임 루프는
    영향을 받지 않는다 — 장부 변경은 `SessionTracker`의 잠금으로 직렬화된다.
    """
    src = stream if stream is not None else sys.stdin
    try:
        if src is None or not src.isatty():
            return None
    except Exception:
        return None

    def pump() -> None:
        print(f"[{PROMPT}]")
        while not stop.is_set():
            try:
                line = src.readline()
            except Exception:
                return
            if not line:
                return
            answer = apply_command(tracker, line)
            if answer:
                print(answer)

    thread = threading.Thread(target=pump, name="tft-units", daemon=True)
    thread.start()
    return thread


def _run_console(loop: LiveLoop, settings: Settings, backend: str, patch: str | None,
                 max_frames: int | None, test_view: bool = False) -> int:
    names = NameBook()
    last_key: tuple | None = None
    recog = None
    if test_view:   # 인식 확인: 창 대신 콘솔에 보드·벤치·아이템을 찍는다(내용이 바뀔 때만)
        from .recog_view import ConsolePrinter

        recog = ConsolePrinter(names, settings.vision.state_min_confidence)

    def on_update(u: LoopUpdate) -> None:
        nonlocal last_key
        if recog is not None:
            try:
                recog.feed(u)
            except Exception:   # 확인용 출력 실패로 루프가 멈추지 않는다
                log.exception("인식 확인 출력 실패")
        if u.kind == "error":
            print(f"[오류] {u.message}")
            return
        if u.kind == "reset":
            print("[새 판] 세션을 초기화했습니다" + (f" (이전 세션 {u.message})" if u.message else ""))
            last_key = None
            return
        if u.recommendation is None:
            return
        # 같은 추천 + 같은 직전 추천 표시면 다시 찍지 않는다(전투 중 표시용 사본은 매번 새 객체다)
        key = (id(loop.last_recommendation), u.kept, u.message)
        if key == last_key:
            return
        last_key = key
        status = StatusInfo(patch=patch, backend=backend, rec=u.recommendation, updated_at=u.at,
                            warnings=[u.message] if u.message else [])
        print("\n" + format_report(u.state, u.recommendation, names=names, status=status,
                                   threshold=settings.vision.state_min_confidence,
                                   max_comps=settings.ui.max_target_comps, kept=u.kept))

    loop.on_update = on_update
    hook = loop.screen_hook
    if hook is not None and hasattr(hook, "on_follow"):
        hook.on_follow = lambda res: print(f"[게임 화면] {res.text()}")
    stop = threading.Event()
    start_unit_console(loop.tracker, stop)
    try:
        loop.run(stop, max_frames=max_frames)
    except KeyboardInterrupt:
        print("중단됨")
    finally:
        stop.set()
        loop.close()
    return 0


def _run_overlay(loop: LiveLoop, settings: Settings, backend: str, patch: str | None,
                 max_frames: int | None, config_dir: Path | None = None,
                 jev_cli: str | None = None, test_view_cli: bool | None = None) -> int:
    from .jev_toggle import JevSwitcher
    from .overlay import make_overlay
    from .setup import resolve_state_dir

    # 트레이 메뉴 "설정"이 같은 설정 파일을 고치도록 config_dir도 넘긴다
    switcher = JevSwitcher(loop=loop, settings=settings, backend=backend, config_dir=config_dir,
                           locked_by_cli=jev_cli)
    app, window = make_overlay(settings, state_dir=resolve_state_dir(settings), config_dir=config_dir,
                               jev=switcher)
    window.status = StatusInfo(patch=patch, backend=backend)
    if loop.screen_hook is not None:
        window.attach_redetector(loop.screen_hook)   # 트레이 "게임 화면 다시 찾기" + 따라가기 알림
    window.attach_unit_review(unit_review_opener(loop.recognizer))   # 트레이 "유닛 사진 검토"(없으면 None)
    window.attach_pin(make_pin(loop, settings))
    ensure_champion_icons(settings, on_done=window.icons_ready.emit)   # 첫 실행: 유닛 아이콘을 뒤에서 받는다   # 목표 덱 고정: 오버레이 옆 띠 + 트레이 하위 메뉴 + 인식 확인 창 버튼
    recog = _make_recog(settings, window, config_dir, test_view_cli)
    loop.on_update = window.on_loop_update
    window.show_overlay()
    if recog is not None:
        recog.start()
    log.info("오버레이: %s", status_line(window.status))

    stop = threading.Event()
    thread = threading.Thread(target=loop.run, args=(stop,), kwargs={"max_frames": max_frames},
                              name="tft-capture", daemon=True)
    thread.start()
    start_unit_console(loop.tracker, stop)   # 터미널에서 보유 유닛을 고칠 수 있다(오버레이 패널 전까지)

    done = []

    def shutdown() -> None:
        """모든 종료 경로가 한 번만 여기를 지난다(트레이 "종료"·Ctrl+Q·✕ 손잡이·인식 확인 창 [앱 종료] → app.quit())."""
        if done:
            return
        done.append(True)
        stop.set()
        thread.join(timeout=3.0)
        loop.close()               # 추천 스레드 정지 + 캡처 닫기 + 세션 저장
        for w in (recog.window if recog is not None else None, window.quit_handle, window.deck_chooser, window):
            try:
                if w is not None:
                    w.hide()
            except RuntimeError:
                pass
        log.info("종료했습니다(세션 저장)")

    app.aboutToQuit.connect(shutdown)
    try:
        return int(app.exec())
    except KeyboardInterrupt:
        shutdown()
        return 0


def ensure_champion_icons(settings: Settings, on_done=None, *, fetch=None) -> threading.Thread | None:
    """목표 덱 유닛 아이콘(`data/templates/{set}/champions/`)이 없으면 **백그라운드 스레드**에서 CommunityDragon에서 받는다.
    받는 동안 오버레이는 이름 글자로 그린다. 끝나면 `on_done(ok_count)`(오버레이가 아이콘 캐시를 새로 읽는다). 실패해도 앱은 돈다."""
    if not settings.overlay.unit_icons:
        return None
    from ..vision.templates import champion_icons_missing, fetch_champions

    set_number = settings.app.set_number
    if not champion_icons_missing(set_number):
        return None
    fetch = fetch or fetch_champions

    def run() -> None:
        try:
            ok, failed = fetch(set_number)
            log.info("챔피언 아이콘 %d개 받음%s", ok, f" (실패 {len(failed)})" if failed else "")
        except Exception:   # noqa: BLE001 — 네트워크 없음 등: 이름 글자로 그린다
            log.warning("챔피언 아이콘을 받지 못했습니다 — 이름 글자로 표시합니다", exc_info=True)
            return
        if on_done is not None:
            on_done(ok)

    thread = threading.Thread(target=run, name="tft-icons", daemon=True)
    thread.start()
    return thread


def make_pin(loop: LiveLoop, settings: Settings):
    """목표 덱 고정 컨트롤러(UI 스레드). 누르면 `loop.request_pin` → 세션 기록 + 추천 스레드에서 다시 추천.
    세션에 남은 고정(판 중간 재시작)으로 시작한다. 실패하면 None(고정 기능 없이 오버레이는 뜬다)."""
    try:
        from .deck_chooser import PinController

        return PinController(loop.request_pin, pinned=loop.pinned_comp_id, pinned_name=loop.pinned_comp_name,
                             limit=settings.ui.max_target_comps)
    except Exception:   # noqa: BLE001
        log.exception("목표 덱 고정을 준비하지 못했습니다")
        return None


def _make_recog(settings: Settings, window, config_dir: Path | None, cli: bool | None):
    """인식 확인 창 컨트롤러를 오버레이에 붙인다(트레이 메뉴 "인식 확인 창"). 실패해도 오버레이는 뜬다."""
    try:
        from .recog_window import RecogController

        controller = RecogController(settings, state_dir=window.state_dir, config_dir=config_dir, cli=cli,
                                     names=window.names,
                                     on_redetect=window.request_redetect if window.redetector is not None else None,
                                     on_quit=window.quit,
                                     on_review=window.open_unit_review if window.unit_review_opener else None,
                                     pin=window.pin)
        window.attach_recog(controller)
        return controller
    except Exception:   # noqa: BLE001
        log.exception("인식 확인 창을 준비하지 못했습니다")
        return None


__all__ = ["run_live", "build", "make_redetector", "choose_ui", "overlay_available", "start_unit_console"]
