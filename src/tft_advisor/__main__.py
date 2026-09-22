"""실행 진입점: `python -m tft_advisor --screenshot PATH` | `--live`."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import load_settings, load_weights
from .static_data import load_static

log = logging.getLogger("tft_advisor")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tft_advisor", description="TFT 실시간 추천 (개인용, 화면 픽셀만 사용)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--screenshot", type=Path, metavar="PATH", help="스크린샷 파일 1장을 인식·추천(디버깅/QA)")
    mode.add_argument("--live", action="store_true", help="실시간 화면 캡처 + 오버레이")
    p.add_argument("--config", type=Path, default=None, metavar="DIR", help="설정 디렉터리(기본: config/)")
    p.add_argument("--no-jev", action="store_true", help="Jev 호출 없이 통계 전용 추천(= create_advisor(\"off\"), 설정 [advisor] jev_backend보다 우선)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def run_screenshot(path: Path, no_jev: bool) -> int:
    if not path.is_file():
        log.error("스크린샷 파일 없음: %s", path)
        return 2
    raise NotImplementedError(
        "--screenshot: vision 인식(tft_advisor.vision)과 추천(tft_advisor.advisor)이 아직 구현되지 않았다 "
        "(Phase 3 예정). 현재는 설정·정적 데이터 로드까지만 동작한다."
    )


def run_live(no_jev: bool) -> int:
    raise NotImplementedError(
        "--live: 실시간 캡처 루프와 오버레이(tft_advisor.app)가 아직 구현되지 않았다 (Phase 3 예정)."
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    load_weights(args.config)  # 설정 오류를 시작 시점에 드러낸다
    logging.basicConfig(level=settings.logging.level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    static = load_static(settings.app.set_number)
    log.info("tft_advisor %s / Set %s 정적 데이터 로드", __version__, static.set_number)
    try:
        if args.screenshot is not None:
            return run_screenshot(args.screenshot, args.no_jev)
        return run_live(args.no_jev)
    except NotImplementedError as e:
        print(f"미구현: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
