"""`--screenshot` 모드 — 파일(또는 폴더) → 인식 → 추천 → 한국어 요약 출력.

실시간 루프 없이 같은 부품(Recognizer, Advisor, report)을 쓴다. 베타 테스트·QA·회귀 확인의 기본 경로다.

    python -m tft_advisor --screenshot "tests/fixtures/screens/라운드 3-5.png"
    python -m tft_advisor --screenshot tests/fixtures/screens/raw    # 폴더의 이미지를 순서대로

폴더 입력은 **추천기 하나를 이어서** 쓴다(히스테리시스·직전 추천 유지 규칙이 실제 스트림처럼 동작한다).

보드 판독(`Recognizer.last_board_read`: 자리·성급·장착 아이템)은 `app.unit_merge.apply_board_read`로
`GameState.board`/`bench`/`items.equipped`에 넣는다. 한 장짜리 입력에는 **구매 장부가 없으므로**
챔피언 정체는 알 수 없고 모든 칸이 `UNKNOWN_UNIT_ID`(신뢰도 0)다 — 자리·성급·아이템만 남는다.
실시간(`--live`)에서는 같은 병합을 `SessionTracker`가 장부와 함께 수행한다(`app.session._apply_units`).

`--test-view`를 함께 주면 이미지마다 인식 확인 내용(보드·벤치·장착/미사용 아이템, `app.recog_view`)을 요약 뒤에
출력하고, PySide6가 있으면(`--no-overlay`가 아니면) 마지막에 인식 확인 창을 띄워 ←/→ 로 이미지를 넘겨 본다.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ..config import Settings, load_settings
from ..contracts import GameState, Recommendation
from .names import NameBook
from .no_collect import disable_unit_collector, make_recognizer_no_collect
from .report import format_report, kept_view
from .session import KEEP_MODES

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def collect_images(path: Path) -> list[Path]:
    """파일 1장 또는 폴더 안의 이미지들(이름순). 폴더는 재귀하지 않는다."""
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    return [path] if path.is_file() else []


CROP_TOL_PX = 2
"""content_box로 자른 크기가 [vision] resolution과 이 픽셀 안이면 '모니터 전체 캡처'로 본다."""


def content_box_applies(size: tuple[int, int], settings: Settings,
                        frame_size: tuple[int, int] | None = None) -> bool:
    """이 이미지에 `[vision] content_box`를 적용해야 하는가.

    content_box는 **실시간 캡처 프레임(모니터 전체)** 기준 비율이다. 게임 영역만 잘라 저장한 스크린샷에 또 적용하면
    두 번 잘려 전부 "알 수 없음"이 된다(2026-09-23 사용자 보고). 규칙:
    1. 셋업이 기록한 캡처 프레임 크기(`_state/setup.json` detected.frame_size)를 알면 → 이미지 크기가 같을 때만.
    2. 모르면 `resolution`(게임 화면 크기)을 본다 → content_box로 자른 크기가 그 해상도(±2px)일 때만.
    3. 둘 다 모르면(해상도 auto) 예전처럼 적용한다.
    """
    v = settings.vision
    if v.content_box is None:
        return True
    w, h = size
    if frame_size is not None:
        return (w, h) == tuple(frame_size)
    res = v.resolution_size()
    if res is None:
        return True
    box = v.content_px(w, h)
    if box is None:
        return True
    return abs(box[2] - res[0]) <= CROP_TOL_PX and abs(box[3] - res[1]) <= CROP_TOL_PX


def _capture_frame_size(settings: Settings) -> tuple[int, int] | None:
    try:
        import json

        from .setup import resolve_state_dir, setup_state_path

        raw = json.loads(setup_state_path(resolve_state_dir(settings)).read_text(encoding="utf-8"))
        size = (raw.get("detected") or {}).get("frame_size")
        if size and len(size) == 2 and all(int(v) > 0 for v in size):
            return int(size[0]), int(size[1])
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def content_without_box(recognizer, image, settings: Settings):
    """content_box를 뺀 설정으로 게임 영역을 고른다(레터박스·여러 모니터 이어 붙인 캡처 자동 탐지는 그대로)."""
    cfg = getattr(recognizer, "cfg", None)
    if cfg is None:
        return None
    try:
        recognizer.cfg = cfg.model_copy(update={"content_box": None})
        content = recognizer.content_for(image, None)
    finally:
        recognizer.cfg = cfg
    h, w = image.shape[:2]
    return content if content is not None else (0, 0, w, h)


def single_frame_names(recognizer) -> None:
    """스크린샷은 한 장씩이라 뒷받침 없는 이름(모델 닮음 하나만)이 '연속 N프레임 일치'를 채울 수 없다 →
    `unit_namer.agree_frames = 1`(임계·신뢰도 상한 0.75는 그대로, 인식 확인에는 "(추정)"으로 표시). vision 23 보고 §6-4."""
    namer = getattr(recognizer, "unit_namer", None)
    if namer is not None and hasattr(namer, "agree_frames"):
        namer.agree_frames = 1


def run_screenshot(path: Path, *, settings: Settings | None = None, jev: str = "mock",
                   debug_dir: Path | None = None, out: Callable[[str], None] = print,
                   recognizer: object | None = None, advisor: object | None = None,
                   paths: Sequence[Path] | None = None, test_view: bool = False,
                   test_window: bool = False) -> int:
    """스크린샷 모드 본체. 0 = 성공, 2 = 입력 없음.

    `test_view`: 이미지마다 인식 확인 줄을 출력한다. `test_window`: 끝나면 인식 확인 창을 띄워 닫을 때까지 기다린다.
    """
    settings = settings or load_settings()
    if out is print:
        from .report import ensure_utf8_stdio

        ensure_utf8_stdio()   # 파이프(cp1252)로 출력해도 한국어에서 죽지 않게

    images = list(paths) if paths is not None else collect_images(path)
    if not images:
        out(f"이미지를 찾지 못했습니다: {path}")
        return 2

    own_advisor = advisor is None
    if recognizer is None:
        recognizer = make_recognizer_no_collect(settings.vision)
    disable_unit_collector(recognizer)   # 스크린샷은 사용자 사진 DB(_pending/)에 쌓지 않는다(QA 27 W5)
    single_frame_names(recognizer)
    if advisor is None:
        from ..advisor import create_advisor

        advisor = create_advisor(jev, settings=settings)

    names = NameBook()
    frame_size = _capture_frame_size(settings)
    patch = _patch_of(advisor)
    backend = getattr(advisor, "backend_name", jev)
    out(f"# TFT Advisor — 스크린샷 모드 · 이미지 {len(images)}장 · Jev {backend} · 패치 {patch or '?'}")
    snaps: list = []
    try:
        for image_path in images:
            snap = _one(image_path, recognizer, advisor, names, settings, patch, backend, out, debug_dir,
                        frame_size=frame_size)
            if snap is not None and (test_view or test_window):
                snaps.append(snap)
                if test_view:
                    from .recog_view import view_lines

                    out("\n".join(view_lines(snap, names, threshold=settings.vision.state_min_confidence,
                                              with_age=False)))
    finally:
        if own_advisor:
            close = getattr(advisor, "close", None)
            if close:
                close()
    if test_window and snaps:
        _open_window(settings, snaps, names, out)
    return 0


def _open_window(settings: Settings, snaps: list, names: NameBook, out: Callable[[str], None]) -> None:
    """인식 확인 창(일반 창)을 띄우고 닫을 때까지 기다린다. PySide6가 없으면 알리고 넘어간다."""
    try:
        from .recog_window import show_snapshots
        from .setup import resolve_state_dir
    except ImportError:
        out("PySide6가 없어 인식 확인 창을 띄우지 못했습니다(위 콘솔 출력을 확인해 주세요).")
        return
    out(f"인식 확인 창을 띄웠습니다({len(snaps)}장, ←/→ 로 넘기고 창을 닫으면 끝납니다).")
    show_snapshots(settings, snaps, names=names, state_dir=resolve_state_dir(settings))


def _one(image_path: Path, recognizer, advisor, names: NameBook, settings: Settings,
         patch: str | None, backend: str, out: Callable[[str], None], debug_dir: Path | None,
         frame_size: tuple[int, int] | None = None):
    """이미지 1장. 반환: 인식 확인용 스냅숏(`recog_view.RecogSnapshot`, 인식 실패면 None)."""
    from ..vision.capture import load_image

    out("")
    try:
        image = load_image(image_path)
    except (OSError, ValueError) as e:
        out(f"[{image_path.name}] 이미지를 읽지 못했습니다: {e}")
        return None
    h, w = image.shape[:2]
    content = None
    if not content_box_applies((w, h), settings, frame_size):
        content = content_without_box(recognizer, image, settings)
        log.info("%s: 실시간 캡처 프레임과 크기가 다른 이미지(%dx%d) — [vision] content_box 대신 게임 영역을 자동으로 찾습니다", image_path.name, w, h)
    t0 = time.perf_counter()
    try:
        state: GameState = recognizer.recognize(image, content=content, source_image=str(image_path))
    except Exception as e:                     # 한 장이 실패해도 나머지를 계속 본다
        log.exception("인식 실패: %s", image_path)
        out(f"[{image_path.name}] 인식 실패: {e}")
        return None
    t_recog = (time.perf_counter() - t0) * 1000
    state = _with_board(state, recognizer)
    t0 = time.perf_counter()
    rec: Recommendation | None = advisor.advise(state)
    t_advise = (time.perf_counter() - t0) * 1000
    kept = None
    if rec is not None and state.screen_mode in KEEP_MODES:   # 직전 이미지의 추천: 산·바뀐 상점 칸을 빼고 표시
        rec, kept = kept_view(rec, state)

    title = f"{image_path.name}  {w}x{h}  (인식 {t_recog:.0f}ms · 추천 {t_advise:.0f}ms)"
    # 상태줄(패치·백엔드·경과)은 실시간 오버레이용이다. 스크린샷 요약은 머리글에 이미 패치·백엔드가 있다.
    out(format_report(state, rec, names=names, threshold=settings.vision.state_min_confidence,
                      title=title, max_comps=settings.ui.max_target_comps, kept=kept))
    if debug_dir is not None:
        _dump(debug_dir, image_path, image, state, recognizer, content)
    from .recog_view import RecogSnapshot

    return RecogSnapshot(state=state, board_read=getattr(recognizer, "last_board_read", None),
                         recog_ms=t_recog, groups=(), kind="screenshot", label=f"{image_path.name}  {w}x{h}")


def _with_board(state: GameState, recognizer) -> GameState:
    """vision 보드 판독 → `GameState.board`/`bench`/`items.equipped`(정체는 미상, `unit_merge`와 같은 규칙).

    실시간 경로와 같은 `merge_units`를 쓴다(장부만 비어 있다). 실패해도 한 장 처리가 죽지 않는다.
    """
    read = getattr(recognizer, "last_board_read", None)
    if read is None:
        return state
    try:
        from .unit_merge import apply_board_read

        return apply_board_read(state, read)
    except Exception:
        log.warning("보드 판독 병합 실패", exc_info=True)
        return state


def _dump(debug_dir: Path, image_path: Path, image, state: GameState, recognizer, content=None) -> None:
    """`--debug`: 인식된 GameState(JSON)와 ROI를 그린 PNG."""
    try:
        from ..vision.capture import save_image
        from ..vision.regions import FrameMapper, draw_rois

        debug_dir.mkdir(parents=True, exist_ok=True)
        stem = image_path.stem
        (debug_dir / f"{stem}.state.json").write_text(
            state.model_dump_json(indent=1, exclude_none=True), encoding="utf-8")
        content = recognizer.content_for(image, content)
        mapper = FrameMapper.for_image(image, content)
        _, _, bw, bh = mapper.box
        save_image(debug_dir / f"{stem}.rois.png", draw_rois(image, recognizer.profile_for(bw, bh), mapper))
        log.info("디버그 덤프: %s", debug_dir)
    except Exception:
        log.warning("디버그 덤프 실패", exc_info=True)


def _patch_of(advisor) -> str | None:
    meta = getattr(getattr(advisor, "stats", None), "meta", None)
    return getattr(meta, "patch", None)
