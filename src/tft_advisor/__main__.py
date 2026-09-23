"""실행 진입점.

    python -m tft_advisor --setup               # 설정 화면(해상도·모니터 자동 감지) → 저장 후 시작
    python -m tft_advisor --screenshot PATH     # 스크린샷 1장(또는 폴더) 인식 + 추천 출력
    python -m tft_advisor                       # 실시간(= --live). 오버레이 + 백그라운드 인식/추천
    python -m tft_advisor --live --no-overlay   # 오버레이 없이 콘솔에만 출력

첫 실행(= `_state/setup.json`이 없을 때)에는 실시간 모드가 설정 화면을 먼저 연다. `--no-setup`으로 건너뛴다.

안전 제약(CLAUDE.md): 화면 픽셀만 읽는다. 게임 메모리·프로세스·입력에는 접근하지 않는다.
TypeSafe 키는 환경변수 `TYPESAFE_API_KEY` → OS 키체인 → `~/.config/tft-advisor/credentials.toml` 순서로
읽는다(설정 화면에서 넣는다). 기본 Jev 백엔드는 `mock`(네트워크·과금 없음)이다.
Jev 백엔드 우선순위: **CLI(--jev/--no-jev) > 오버레이 트레이 토글 "Jev 실시간 판단" > `[advisor] jev_backend`**.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Settings, VisionCfg, load_settings, load_weights
from .static_data import load_static

log = logging.getLogger("tft_advisor")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tft_advisor", description="TFT 실시간 추천 (개인용, 화면 픽셀만 사용)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--screenshot", type=Path, metavar="PATH",
                      help="스크린샷 파일 1장 또는 폴더를 인식·추천(디버깅/QA/베타 테스트)")
    mode.add_argument("--live", action="store_true", help="실시간 화면 캡처 + 오버레이 (인자를 주지 않으면 기본)")
    p.add_argument("--config", type=Path, default=None, metavar="DIR", help="설정 디렉터리(기본: config/)")
    p.add_argument("--jev", choices=("mock", "live", "off"), default=None,
                   help="Jev 백엔드를 이번 실행에 못박는다(트레이 토글이 잠긴다). 기본은 설정 "
                        "[advisor] jev_backend(= mock). 우선순위: CLI > 트레이 토글 > 설정 파일. live는 과금된다")
    p.add_argument("--no-jev", action="store_true", help="--jev off 와 같다(통계 전용 추천)")
    p.add_argument("--profile", default=None, metavar="NAME",
                   help="ROI 프로파일 고정: set18_16x9 | set18_16x10 | 1920x1080 …  (설정 [vision] profile 덮어씀)")
    p.add_argument("--aspect", default=None, choices=("auto", "4:3", "16:10", "16:9", "21:9", "32:9"),
                   help="화면 비율 고정(설정 [vision] aspect 덮어씀)")
    p.add_argument("--resolution", default=None, metavar="WxH",
                   help="게임 해상도 명시(예 1920x1080). 설정 [vision] resolution 덮어씀")
    p.add_argument("--no-overlay", action="store_true", help="오버레이 없이 콘솔에만 출력")
    setup = p.add_mutually_exclusive_group()
    setup.add_argument("--setup", action="store_true",
                       help="설정 화면을 연다(화면 해상도·모니터 자동 감지 → settings.toml 저장). 첫 실행에는 자동으로 열린다")
    setup.add_argument("--no-setup", action="store_true", help="첫 실행이어도 설정 화면을 열지 않는다")
    p.add_argument("--debug", action="store_true",
                   help="로그 DEBUG + 인식 결과(JSON)와 ROI 렌더를 {log_dir}/debug 에 저장")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def jev_backend(args: argparse.Namespace) -> str:
    """CLI → advisor 백엔드. --no-jev > --jev > 설정("auto"는 설정값을 따른다).

    "auto"가 아니면 이번 실행에 **고정**이다 — 오버레이 트레이의 "Jev 실시간 판단" 토글도 잠근다
    (우선순위: CLI > 트레이 토글 > settings.toml).
    """
    if args.no_jev:
        return "off"
    return args.jev or "auto"


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """`--profile/--aspect/--resolution`을 설정에 얹는다(검증을 그대로 통과해야 한다)."""
    over = {k: v for k, v in (("profile", args.profile), ("aspect", args.aspect),
                              ("resolution", args.resolution)) if v is not None}
    if not over:
        return settings
    vision = VisionCfg.model_validate({**settings.vision.model_dump(), **over})
    return settings.model_copy(update={"vision": vision})


def wants_setup(settings: Settings, args: argparse.Namespace) -> bool:
    """설정 화면을 열어야 하는가. `--setup`은 언제나, 실시간 모드는 **첫 실행**일 때만(`--no-setup`이면 안 연다).

    첫 실행 자동 열기는 **대화형일 때만** 한다: 오버레이(PySide6 대화상자)를 쓸 수 없으면 콘솔 대체 UI가
    `input()`으로 묻는데, 파이프·CI·테스트에서는 그 자리에서 멈춰 버린다. `--setup`은 사용자가 직접 부른
    것이므로 이 검사를 건너뛴다.
    """
    from .app.live import overlay_available
    from .app.setup import needs_setup

    if args.setup:
        return True
    if args.no_setup or args.screenshot is not None:
        return False
    if not needs_setup(settings):
        return False
    if not args.no_overlay and overlay_available():
        return True
    return bool(getattr(sys.stdin, "isatty", bool)())


def run_setup_step(settings: Settings, args: argparse.Namespace) -> tuple[Settings, int | None]:
    """설정 화면을 연다. 반환: (이어서 쓸 설정, 종료 코드 또는 None=계속 진행).

    오버레이를 쓸 수 있으면 PySide6 대화상자, 아니면(`--no-overlay`·PySide6 없음) 콘솔 대화형으로 내려간다.
    """
    from .app.live import overlay_available
    from .app.setup import run_setup

    gui = not args.no_overlay and overlay_available()
    outcome = run_setup(settings, config_dir=args.config, gui=gui)
    if outcome.action == "cancelled":
        print("설정을 취소했다(저장하지 않음).", file=sys.stderr)
        return settings, 0
    print(f"설정을 저장했다: {outcome.path}")
    fresh = apply_overrides(outcome.settings or settings, args)
    return fresh, (0 if outcome.action == "saved" else None)


def debug_dir(settings: Settings, args: argparse.Namespace) -> Path | None:
    if not args.debug:
        return None
    base = Path(settings.app.log_dir)
    if not base.is_absolute():
        from .static_data import PROJECT_ROOT

        base = PROJECT_ROOT / base
    return base / "debug"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    load_weights(args.config)  # 설정 오류를 시작 시점에 드러낸다
    level = "DEBUG" if args.debug else settings.logging.level
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = apply_overrides(settings, args)
    static = load_static(settings.app.set_number)
    log.info("tft_advisor %s / Set %s 정적 데이터 로드", __version__, static.set_number)

    try:
        if wants_setup(settings, args):
            settings, code = run_setup_step(settings, args)
            if code is not None:
                return code
        if args.screenshot is not None:
            from .app.screenshot import run_screenshot

            if not args.screenshot.exists():
                log.error("경로 없음: %s", args.screenshot)
                return 2
            return run_screenshot(args.screenshot, settings=settings, jev=jev_backend(args),
                                  debug_dir=debug_dir(settings, args))
        from .app.live import run_live

        return run_live(settings=settings, jev=jev_backend(args), overlay=not args.no_overlay,
                        debug_dir=debug_dir(settings, args), config_dir=args.config)
    except KeyboardInterrupt:
        print("중단됨", file=sys.stderr)
        return 130
    except NotImplementedError as e:
        print(f"미구현: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
