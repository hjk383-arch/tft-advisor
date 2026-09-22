"""`--screenshot` 모드 — 파일(또는 폴더) → 인식 → 추천 → 한국어 요약 출력.

실시간 루프 없이 같은 부품(Recognizer, Advisor, report)을 쓴다. 베타 테스트·QA·회귀 확인의 기본 경로다.

    python -m tft_advisor --screenshot "tests/fixtures/screens/라운드 3-5.png"
    python -m tft_advisor --screenshot tests/fixtures/screens/raw    # 폴더의 이미지를 순서대로

폴더 입력은 **추천기 하나를 이어서** 쓴다(히스테리시스·직전 추천 유지 규칙이 실제 스트림처럼 동작한다).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ..config import Settings, load_settings
from ..contracts import GameState, Recommendation
from .names import NameBook
from .report import format_report

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def collect_images(path: Path) -> list[Path]:
    """파일 1장 또는 폴더 안의 이미지들(이름순). 폴더는 재귀하지 않는다."""
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    return [path] if path.is_file() else []


def run_screenshot(path: Path, *, settings: Settings | None = None, jev: str = "mock",
                   debug_dir: Path | None = None, out: Callable[[str], None] = print,
                   recognizer: object | None = None, advisor: object | None = None,
                   paths: Sequence[Path] | None = None) -> int:
    """스크린샷 모드 본체. 0 = 성공, 2 = 입력 없음."""
    settings = settings or load_settings()
    images = list(paths) if paths is not None else collect_images(path)
    if not images:
        out(f"이미지를 찾지 못했다: {path}")
        return 2

    own_advisor = advisor is None
    if recognizer is None:
        from ..vision.recognizer import Recognizer

        recognizer = Recognizer(cfg=settings.vision)
    if advisor is None:
        from ..advisor import create_advisor

        advisor = create_advisor(jev, settings=settings)

    names = NameBook()
    patch = _patch_of(advisor)
    backend = getattr(advisor, "backend_name", jev)
    out(f"# TFT Advisor — 스크린샷 모드 · 이미지 {len(images)}장 · Jev {backend} · 패치 {patch or '?'}")
    try:
        for image_path in images:
            _one(image_path, recognizer, advisor, names, settings, patch, backend, out, debug_dir)
    finally:
        if own_advisor:
            close = getattr(advisor, "close", None)
            if close:
                close()
    return 0


def _one(image_path: Path, recognizer, advisor, names: NameBook, settings: Settings,
         patch: str | None, backend: str, out: Callable[[str], None], debug_dir: Path | None) -> None:
    from ..vision.capture import load_image

    out("")
    try:
        image = load_image(image_path)
    except (OSError, ValueError) as e:
        out(f"[{image_path.name}] 이미지를 읽지 못했다: {e}")
        return
    h, w = image.shape[:2]
    t0 = time.perf_counter()
    try:
        state: GameState = recognizer.recognize(image, source_image=str(image_path))
    except Exception as e:                     # 한 장이 실패해도 나머지를 계속 본다
        log.exception("인식 실패: %s", image_path)
        out(f"[{image_path.name}] 인식 실패: {e}")
        return
    t_recog = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    rec: Recommendation | None = advisor.advise(state)
    t_advise = (time.perf_counter() - t0) * 1000

    title = f"{image_path.name}  {w}x{h}  (인식 {t_recog:.0f}ms · 추천 {t_advise:.0f}ms)"
    # 상태줄(패치·백엔드·경과)은 실시간 오버레이용이다. 스크린샷 요약은 머리글에 이미 패치·백엔드가 있다.
    out(format_report(state, rec, names=names, threshold=settings.vision.state_min_confidence,
                      title=title, max_comps=settings.ui.max_target_comps))
    if debug_dir is not None:
        _dump(debug_dir, image_path, image, state, recognizer)


def _dump(debug_dir: Path, image_path: Path, image, state: GameState, recognizer) -> None:
    """`--debug`: 인식된 GameState(JSON)와 ROI를 그린 PNG."""
    try:
        import cv2

        from ..vision.regions import FrameMapper, draw_rois

        debug_dir.mkdir(parents=True, exist_ok=True)
        stem = image_path.stem
        (debug_dir / f"{stem}.state.json").write_text(
            state.model_dump_json(indent=1, exclude_none=True), encoding="utf-8")
        content = recognizer.content_for(image, None)
        mapper = FrameMapper.for_image(image, content)
        _, _, bw, bh = mapper.box
        cv2.imwrite(str(debug_dir / f"{stem}.rois.png"), draw_rois(image, recognizer.profile_for(bw, bh), mapper))
        log.info("디버그 덤프: %s", debug_dir)
    except Exception:
        log.warning("디버그 덤프 실패", exc_info=True)


def _patch_of(advisor) -> str | None:
    meta = getattr(getattr(advisor, "stats", None), "meta", None)
    return getattr(meta, "patch", None)
