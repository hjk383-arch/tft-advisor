"""실행 전 설정(셋업) — 화면 해상도·모니터·게임 화면 영역 자동 감지와 `config/settings.toml` 저장.

이 모듈에는 **Qt가 없다**. 감지 규칙·설정 쓰기·상태 파일은 전부 여기 있고, PySide6 대화상자는
`app/setup_dialog.py`, 콘솔 대체는 이 파일의 `run_console_setup()`이 쓴다(같은 로직을 두 UI가 공유한다).

감지 순서
1. 모니터 목록(mss) — 번호·위치·크기·주 모니터 여부(+ Qt가 있으면 배율·이름).
2. 모니터마다 한 장 캡처해 채점한다(기본: 위쪽 스테이지 막대 픽셀. 인식기가 있으면 스테이지 OCR).
   가장 높은 점수의 모니터를 고른다. 동점이면 지금 설정 > 주 모니터 순.
3. 고른 프레임에서 게임 화면 영역(content box)을 찾는다 — 레터박스(검은 띠)·창모드 바탕화면.
4. 그 영역의 비율로 ROI 프로파일을 고른다. 16:9·16:10은 실측, 나머지는 유도(경고).

안전 제약(CLAUDE.md): 화면 픽셀만 읽는다. 게임 메모리·프로세스·입력에는 접근하지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ..config import DEFAULT_CONFIG_DIR, Settings, load_settings

log = logging.getLogger(__name__)

SETUP_FILE = "setup.json"
SETUP_VERSION = 1

BLACK_MEAN = 6.0
"""캡처 평균 밝기가 이보다 낮고 최댓값도 낮으면 "검은 화면"으로 본다(macOS 화면 기록 권한 없음 / 전용 전체화면)."""
BLACK_PEAK = 32
GAME_FOUND_SCORE = 0.5
"""인식기(OCR) 채점이 이 이상이면 TFT 화면으로 **확인**(스테이지 글자가 읽혔다).
`vision.capture.MONITOR_CONFIRMED`와 같은 기준이다. 픽셀 채점(`default_screen_score`)은 최댓값이 0.5라
이 기준에 닿지 못한다 → 픽셀 채점은 모니터끼리 **비교**하는 데만 쓰고 "찾았다"고 말하지 않는다."""

ASPECT_CHOICES = ("auto", "16:9", "16:10", "4:3", "21:9", "32:9")
JEV_CHOICES = ("mock", "live", "off")
JEV_KEY_ENV = "TYPESAFE_API_KEY"
JEV_NO_KEY_NOTE = ("TypeSafe API 키가 없어 live(실시간 판단)를 켤 수 없습니다 — "
                   "위 [TypeSafe API 키] 칸에 본인 키를 넣고 [저장]을 누르세요"
                   f" (또는 환경변수 {JEV_KEY_ENV} 를 설정하세요).")
JEV_MOCK_NOTE = ("끄면 mock으로 동작합니다 — 네트워크·과금 없음, Jev 판단은 가짜 고정값입니다"
                 "(추천 자체는 통계·규칙으로 계속 나옵니다).")
JEV_LABELS_SHORT = {"mock": "mock(가짜 판단, 무료)", "live": "live(실시간 판단, 과금)", "off": "off(통계 전용)"}

KEY_GROUP_TITLE = "TypeSafe API 키 — 본인 키를 넣으면 이 컴퓨터의 OS 키체인에 저장됩니다"
KEY_FRIEND_NOTE = (
    "Jev 실시간 판단을 쓰려면 <b>본인 TypeSafe API 키</b>가 필요합니다(남의 키를 받아 쓰지 않습니다). "
    "typesafe.ai 에서 키를 만들어 아래에 붙여 넣고 [저장]을 누르면, 키는 <b>이 컴퓨터의 OS 키체인</b>에만 들어갑니다 — "
    "프로그램 폴더·설정 파일·로그에는 저장되지 않고, 저장한 뒤로는 다시 보이지 않습니다(가린 힌트만 보입니다).")
KEY_ENV_NOTE = (f"환경변수 {JEV_KEY_ENV} 가 설정돼 있어 <b>그 키가 먼저 쓰입니다</b>. "
                "여기서 저장한 키는 환경변수를 지워야 쓰입니다.")


def jev_key_present() -> bool:
    """live를 고를 수 있는가(= 키가 어디에든 있는가).

    판정은 `credentials.key_present()` 한 곳에서만 한다 — 환경변수 `TYPESAFE_API_KEY` → OS 키체인 →
    폴백 파일(CLAUDE.md 고정 제약). 체크박스·트레이 토글·advisor가 모두 같은 답을 본다.
    """
    from .. import credentials

    return credentials.key_present()


def key_status_line() -> str:
    """키가 어디에 있는지 한 줄(콘솔 설정·진단용). **값은 나오지 않는다** — 가린 힌트만 나온다.

    콘솔 설정에는 키 입력란이 없다(키 입력은 설정 **화면**에서 하거나 환경변수를 쓴다).
    """
    from .. import credentials

    info = credentials.key_info()
    if info.present:
        return info.describe()
    return ("없습니다 — `python -m tft_advisor --setup`의 설정 화면에서 넣거나, "
            f"환경변수 {JEV_KEY_ENV} 를 설정하세요. live(실시간 판단)는 키가 있어야 켜집니다.")


def jev_checks(backend: str) -> tuple[bool, bool]:
    """`jev_backend` → (live 체크, off 체크). 체크박스 2개로 백엔드 3가지를 모두 고를 수 있다."""
    return backend == "live", backend == "off"


def jev_backend_from_checks(live: bool, off: bool) -> str:
    """(live 체크, off 체크) → `jev_backend`. off가 이긴다(추천에서 Jev를 통째로 뺀다)."""
    if off:
        return "off"
    return "live" if live else "mock"

PERMISSION_HELP_MAC = (
    "화면이 검게 잡힙니다. macOS 화면 기록 권한이 필요합니다:\n"
    "  시스템 설정 → 개인정보 보호 및 보안 → 화면 기록 → 이 앱(터미널 / Python)을 켜세요\n"
    "  → 앱(터미널)을 완전히 종료했다가 다시 실행한 뒤 다시 감지하세요.\n"
    "게임이 전용 전체화면(exclusive fullscreen)이면 테두리 없는 창 모드로 바꾸세요."
)
PERMISSION_HELP_OTHER = (
    "화면이 검게 잡힙니다. 게임이 전용 전체화면(exclusive fullscreen)이면 캡처가 검게 나옵니다 →\n"
    "  게임 설정에서 테두리 없는 창 모드로 바꾸고 다시 감지하세요."
)


def permission_help() -> str:
    """검은 캡처가 잡혔을 때 보여 줄 안내(플랫폼별)."""
    import sys

    return PERMISSION_HELP_MAC if sys.platform == "darwin" else PERMISSION_HELP_OTHER


# ---------------------------------------------------------------------------
# 모니터 목록
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitorInfo:
    """모니터 하나. `number`는 mss 번호(= `[capture] monitor` 값, 1 = 주 모니터)."""

    number: int
    left: int
    top: int
    width: int
    height: int
    primary: bool = False
    scale: float = 1.0        # 고DPI 배율(Qt devicePixelRatio). 모르면 1.0
    name: str = ""            # Qt 화면 이름(있으면)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height else 0.0

    def label(self) -> str:
        """대화상자·콘솔에 그대로 쓰는 한 줄 설명."""
        parts = [f"{self.number}번 모니터", f"{self.width}x{self.height}"]
        from ..vision.regions import aspect_name

        name = aspect_name(self.ratio)
        parts.append(name or f"비율 {self.ratio:.2f}")
        if self.primary:
            parts.append("주 모니터")
        if abs(self.scale - 1.0) > 0.01:
            parts.append(f"배율 {self.scale:g}x")
        if self.name:
            parts.append(self.name)
        return " · ".join(parts)


def monitors_from_mss(mons: Sequence[dict]) -> list[MonitorInfo]:
    """`mss.mss().monitors` → `MonitorInfo` 목록.

    mss는 0번에 "모든 모니터를 합친 가상 화면", 1번부터 물리 모니터를 준다. 물리 모니터만 돌려준다
    (`vision.capture.MssSource._pick`과 같은 번호 규칙: 모니터가 하나뿐인 목록이면 0번을 쓴다).
    """
    if not mons:
        return []
    physical = list(mons[1:]) or list(mons[:1])
    start = 1 if len(mons) > 1 else 0
    out: list[MonitorInfo] = []
    for i, m in enumerate(physical):
        left, top = int(m.get("left", 0)), int(m.get("top", 0))
        out.append(MonitorInfo(number=start + i, left=left, top=top,
                               width=int(m["width"]), height=int(m["height"]),
                               primary=(left == 0 and top == 0)))
    if out and not any(m.primary for m in out):
        out[0] = replace(out[0], primary=True)
    return out


@dataclass(frozen=True)
class QtScreenInfo:
    """Qt가 보고하는 화면(논리 좌표 + 배율). 배율·이름을 채우는 데만 쓴다."""

    left: int
    top: int
    width: int
    height: int
    dpr: float = 1.0
    name: str = ""
    primary: bool = False


def _qt_match(info: MonitorInfo, s: QtScreenInfo, *, physical: bool, with_position: bool) -> bool:
    """이 Qt 화면이 이 mss 모니터인가. `physical`이면 논리 크기에 배율을 곱해 비교한다."""
    k = s.dpr if physical else 1.0
    if round(s.width * k) != info.width or round(s.height * k) != info.height:
        return False
    return not with_position or (round(s.left * k) == info.left and round(s.top * k) == info.top)


def merge_qt_screens(infos: Sequence[MonitorInfo], screens: Sequence[QtScreenInfo]) -> list[MonitorInfo]:
    """mss 목록(캡처 기준, 물리 픽셀)에 Qt의 배율·이름을 얹는다.

    Qt 좌표는 논리 픽셀이라 고DPI에서는 mss와 어긋난다. 게다가 배율이 섞인 다중 모니터에서는
    **위치**를 한 모니터의 배율로 환산할 수 없다(예: 3024x1964@2x 옆의 1920x1080@1x는 mss left=3024,
    Qt left=1512). 그래서 다음 순서로 맞춘다 — 확실한 조건부터:
      1. 물리 크기 + 위치 → 2. 논리 크기 + 위치 → 3. 물리 크기만 → 4. 논리 크기만.
    끝까지 못 찾으면 그대로 둔다(배율 1.0, 이름 없음). 배율·이름은 **표시용**이라 틀려도 캡처에는 영향이 없다.
    """
    out: list[MonitorInfo] = []
    used: set[int] = set()
    phases = ((True, True), (False, True), (True, False), (False, False))
    for info in infos:
        found: QtScreenInfo | None = None
        for physical, with_position in phases:
            for j, s in enumerate(screens):
                if j not in used and _qt_match(info, s, physical=physical, with_position=with_position):
                    found = s
                    used.add(j)
                    break
            if found is not None:
                break
        out.append(info if found is None else replace(info, scale=found.dpr, name=found.name))
    return out


def qt_screens() -> list[QtScreenInfo]:
    """QApplication이 이미 있으면 Qt 화면 정보를, 없으면 빈 목록을 돌려준다(여기서 앱을 만들지 않는다)."""
    try:
        from PySide6.QtGui import QGuiApplication
    except ImportError:
        return []
    app = QGuiApplication.instance()
    if app is None:
        return []
    primary = QGuiApplication.primaryScreen()
    out = []
    for s in QGuiApplication.screens():
        g = s.geometry()
        out.append(QtScreenInfo(left=g.left(), top=g.top(), width=g.width(), height=g.height(),
                                dpr=float(s.devicePixelRatio()), name=s.name(), primary=(s is primary)))
    return out


class MonitorGrabber:
    """셋업 전용 캡처기 — mss를 한 번 열고 모니터별로 한 장씩 찍는다(컨텍스트 매니저).

    실시간 루프의 `MssSource`와 따로 두는 이유: 셋업은 **모든** 모니터를 한 번씩 찍어 비교해야 하고,
    대화상자가 열려 있는 동안만 열려 있으면 된다.
    """

    def __init__(self) -> None:
        self._sct = None

    def __enter__(self) -> MonitorGrabber:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _ensure(self):
        if self._sct is None:
            import mss

            self._sct = (getattr(mss, "MSS", None) or mss.mss)()   # mss>=10.2는 MSS, 옛 버전은 mss()
        return self._sct

    def monitors(self) -> list[MonitorInfo]:
        infos = monitors_from_mss(self._ensure().monitors)
        return merge_qt_screens(infos, qt_screens())

    def grab(self, info: MonitorInfo):
        import numpy as np

        box = {"left": info.left, "top": info.top, "width": info.width, "height": info.height}
        shot = self._ensure().grab(box)
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None


# ---------------------------------------------------------------------------
# 감지
# ---------------------------------------------------------------------------


def is_black_frame(image) -> bool:
    """캡처가 사실상 검다(권한 없음 / 전용 전체화면 / 꺼진 화면)."""
    if image is None or getattr(image, "size", 0) == 0:
        return True
    return float(image.mean()) < BLACK_MEAN and int(image.max()) <= BLACK_PEAK


@dataclass
class MonitorProbe:
    """모니터 한 대의 캡처 결과."""

    info: MonitorInfo
    score: float = 0.0
    black: bool = False
    frame: Any = None                 # np.ndarray | None (미리보기·테스트 캡처에 재사용)
    error: str | None = None

    @property
    def frame_size(self) -> tuple[int, int] | None:
        if self.frame is None:
            return None
        h, w = self.frame.shape[:2]
        return w, h


@dataclass
class SetupDetection:
    """자동 감지 결과 — 대화상자와 콘솔이 그대로 표시한다."""

    probes: list[MonitorProbe] = field(default_factory=list)
    chosen: int = 0                                   # probes의 인덱스
    frame_size: tuple[int, int] = (0, 0)              # 고른 모니터 캡처 크기
    content_box: tuple[float, float, float, float] | None = None   # 비율 (x1, y1, x2, y2)
    content_px: tuple[int, int, int, int] | None = None            # (left, top, w, h)
    game_size: tuple[int, int] = (0, 0)               # content_box 적용 후 = 실제 게임 화면 크기
    aspect: str | None = None                         # "16:9" 등. 지원 비율과 다르면 None
    profile_name: str = ""
    measured: bool = False                            # 실측 프로파일(16:9 / 16:10)인가
    game_found: bool = False                          # 스테이지 OCR로 TFT 화면을 확인했는가
    scorer_kind: str = "pixel"                        # "ocr"(인식기 채점) | "pixel"(빠른 예비 채점)
    permission_issue: bool = False                    # 모든 캡처가 검다
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def monitor(self) -> MonitorInfo | None:
        return self.probes[self.chosen].info if self.probes else None

    @property
    def ratio(self) -> float:
        w, h = self.game_size
        return w / h if h else 0.0

    def summary_lines(self) -> list[str]:
        """한국어 요약(대화상자·콘솔 공용)."""
        mon = self.monitor
        if mon is None:
            return ["모니터를 찾지 못했습니다."]
        gw, gh = self.game_size
        out = [f"모니터: {mon.label()}"]
        out.append(f"캡처 크기: {self.frame_size[0]}x{self.frame_size[1]}")
        if self.content_px is not None:
            left, top, w, h = self.content_px
            out.append(f"게임 화면 영역: ({left}, {top})에서 {w}x{h} — 바깥쪽(검은 띠/바탕화면)은 잘라냅니다")
        else:
            out.append("게임 화면 영역: 프레임 전체(레터박스·창 테두리 없음)")
        out.append(f"게임 해상도: {gw}x{gh}  ·  비율: {self.aspect or f'{self.ratio:.3f} (지원 목록에 없음)'}")
        kind = "실측 프로파일" if self.measured else "유도 프로파일(미검증)"
        out.append(f"ROI 프로파일: {self.profile_name}  ({kind})")
        if self.game_found:
            out.append("게임 화면 확인: 찾았습니다(스테이지 글자를 읽었습니다)")
        elif self.scorer_kind == "ocr":
            out.append("게임 화면 확인: 못 찾았습니다 — 게임이 실행 중인지 확인하세요")
        else:
            out.append("게임 화면 확인: 아직 안 함(빠른 예비 감지) — 다시 감지하면 스테이지 글자로 확인합니다")
        return out


def _default_scorer():
    from ..vision.capture import default_screen_score

    return default_screen_score


def detect(monitors: Sequence[MonitorInfo], grab: Callable[[MonitorInfo], Any], *,
           scorer: Callable[[Any], float] | None = None, prefer: int | str | None = None,
           content_box_auto: bool = True) -> SetupDetection:
    """모니터 목록 + 캡처 함수 → 감지 결과.

    `prefer`: 지금 설정된 `[capture] monitor`(동점일 때 그대로 둔다). `"auto"`면 무시한다.
    `scorer`: 프레임 → 0~1 점수. 기본은 픽셀 기반(OCR 없음, 빠름). 인식기가 이미 있으면
    `recognizer.screen_score`(스테이지 OCR)를 넘기면 더 정확하다.
    """
    from ..vision.regions import MEASURED_PROFILES, aspect_name, detect_content_box, profile_for_frame

    score_fn = scorer or _default_scorer()
    det = SetupDetection(scorer_kind="ocr" if scorer is not None else "pixel")
    for info in monitors:
        probe = MonitorProbe(info=info)
        try:
            probe.frame = grab(info)
        except Exception as e:   # noqa: BLE001 — 한 모니터의 캡처 실패가 셋업을 막지 않는다
            probe.error = f"{type(e).__name__}: {e}"
            log.debug("모니터 %d 캡처 실패", info.number, exc_info=True)
        if probe.frame is not None:
            probe.black = is_black_frame(probe.frame)
            if not probe.black:
                try:
                    probe.score = float(score_fn(probe.frame))
                except Exception:   # noqa: BLE001
                    log.debug("모니터 %d 채점 실패", info.number, exc_info=True)
        det.probes.append(probe)

    if not det.probes:
        det.warnings.append("모니터를 찾지 못했습니다. 화면 캡처(mss)가 동작하는지 확인하세요.")
        return det

    prefer_idx = next((i for i, p in enumerate(det.probes)
                       if isinstance(prefer, int) and p.info.number == prefer), None)
    best = max(range(len(det.probes)), key=lambda i: det.probes[i].score)
    if prefer_idx is not None and det.probes[prefer_idx].score >= det.probes[best].score:
        best = prefer_idx
    elif det.probes[best].score <= 0:
        best = next((i for i, p in enumerate(det.probes) if p.info.primary), 0)
    det.chosen = best

    probe = det.probes[best]
    det.game_found = det.scorer_kind == "ocr" and probe.score >= GAME_FOUND_SCORE
    det.permission_issue = all(p.black or p.frame is None for p in det.probes)
    if det.permission_issue:
        det.warnings.append("모든 모니터가 검게 잡혔습니다 — 화면 캡처 권한 또는 전용 전체화면 문제입니다.")
    elif probe.black:
        det.warnings.append(f"{probe.info.number}번 모니터가 검게 잡혔습니다.")

    size = probe.frame_size or probe.info.size
    det.frame_size = size
    if probe.frame is not None and content_box_auto and not probe.black:
        box = detect_content_box(probe.frame)
        if box is not None:
            left, top, w, h = box
            det.content_px = box
            det.content_box = (round(left / size[0], 6), round(top / size[1], 6),
                               round((left + w) / size[0], 6), round((top + h) / size[1], 6))
            det.notes.append(f"레터박스/창 테두리를 잘라냈습니다: {size[0]}x{size[1]} → {w}x{h}")
    det.game_size = (det.content_px[2], det.content_px[3]) if det.content_px else size

    gw, gh = det.game_size
    if gw > 0 and gh > 0:
        det.aspect = aspect_name(gw / gh)
        det.measured = det.aspect in MEASURED_PROFILES
        try:
            det.profile_name = profile_for_frame(gw, gh).name
        except ValueError as e:   # 너무 치우친 비율은 16:9에서 유도할 수 없다(ROI가 화면 밖으로 나간다)
            log.warning("비율 %dx%d 에 쓸 ROI 프로파일을 만들지 못했습니다: %s", gw, gh, e)
            det.profile_name = "set18_16x9"
            det.measured = False
            det.warnings.append(
                f"{gw}x{gh} 비율에는 쓸 수 있는 ROI 배치가 없습니다 — 16:9 배치로 대신합니다(인식이 크게 어긋납니다). "
                "게임을 16:9 또는 16:10 해상도로 띄우거나, 캡처 영역을 게임 화면에만 맞춰 주세요.")
    if det.aspect is None:
        det.warnings.append(
            f"비율 {det.ratio:.3f}은 지원 목록(16:9 · 16:10 · 4:3 · 21:9 · 32:9)에 없습니다 — "
            "캡처 영역이 잘못 잡혔을 수 있습니다. 테스트 캡처로 확인해 주세요.")
    elif not det.measured:
        det.warnings.append(
            f"{det.aspect}는 실제 캡처로 측정하지 않은 비율입니다(16:9에서 유도) — "
            "인식 위치가 조금 어긋날 수 있습니다. 테스트 캡처로 확인해 주세요.")
    if not det.game_found and not det.permission_issue and det.scorer_kind == "ocr":
        det.warnings.append("TFT 화면(스테이지 글자)을 찾지 못했습니다. 게임을 테두리 없는 창 모드로 띄운 뒤 "
                            "다시 감지하거나, 테스트 캡처로 확인해 주세요.")
    if probe.info.scale and abs(probe.info.scale - 1.0) > 0.01:
        det.notes.append(f"고DPI 화면(배율 {probe.info.scale:g}x) — 캡처는 실제 픽셀({size[0]}x{size[1]})로 합니다")
    if probe.frame_size is not None and probe.frame_size != probe.info.size:
        det.notes.append(f"모니터 크기({probe.info.width}x{probe.info.height})와 캡처 크기"
                         f"({size[0]}x{size[1]})가 다릅니다 — 화면 배율 때문일 수 있습니다")
    return det


def detect_with_recognizer(settings: Settings, *, out: Callable[[str], None] | None = None) -> SetupDetection:
    """인식기(스테이지 글자 OCR)로 채점하는 감지 — 어느 모니터에 TFT가 떠 있는지 **확인**한다.

    인식기 생성(OCR 모델 + 템플릿)에 1~3초 걸린다. 못 만들면 픽셀 채점으로 조용히 내려간다.
    """
    scorer = None
    try:
        from ..vision.recognizer import Recognizer

        if out is not None:
            out("화면 인식기를 준비하는 중… (처음에는 몇 초 걸립니다)")
        scorer = Recognizer(cfg=settings.vision).screen_score
    except Exception:   # noqa: BLE001 — 인식기가 없어도 화면 크기는 찾을 수 있다
        log.warning("인식기를 만들지 못했습니다 → 픽셀 채점으로 감지합니다", exc_info=True)
    return detect_live(settings, scorer=scorer)


def safe_detect(detect_fn: Callable[[], SetupDetection]) -> SetupDetection:
    """감지를 부르되 **예외를 내지 않는다** — 캡처가 통째로 실패해도(mss 없음·권한 거부) 설정 화면은 살아 있어야 한다.

    실패는 "권한 문제"로 취급한다(가장 흔한 원인이고, 안내가 그대로 맞는다).
    """
    try:
        return detect_fn()
    except Exception as e:   # noqa: BLE001 — 감지 실패가 설정 화면을 죽이지 않는다
        log.warning("자동 감지 실패", exc_info=True)
        det = SetupDetection(warnings=[f"화면 캡처에 실패했습니다: {type(e).__name__}: {e}"])
        det.permission_issue = True
        return det


def detect_live(settings: Settings, *, scorer: Callable[[Any], float] | None = None,
                grabber: MonitorGrabber | None = None) -> SetupDetection:
    """실제 화면에서 감지한다(대화상자의 [자동 감지] 버튼). 캡처기를 넘기면 그것을 쓴다."""
    own = grabber is None
    grabber = grabber or MonitorGrabber()
    try:
        monitors = grabber.monitors()
        return detect(monitors, grabber.grab, scorer=scorer, prefer=settings.capture.monitor,
                      content_box_auto=settings.vision.content_box_auto)
    finally:
        if own:
            grabber.close()


# ---------------------------------------------------------------------------
# 사용자가 확정한 값 → 설정
# ---------------------------------------------------------------------------


@dataclass
class SetupChoice:
    """대화상자/콘솔에서 확정한 값. `settings.toml`에 쓰는 키와 1:1이다."""

    monitor: int | str = "auto"
    aspect: str = "auto"
    resolution: str = "auto"
    profile: str = "auto"
    content_box: tuple[float, float, float, float] | None = None
    content_box_auto: bool = True
    jev_backend: str = "mock"
    overlay_opacity: float | None = None
    overlay_scale: float | None = None
    test_view: bool | None = None   # [ui] test_view — 인식 확인 창(None = 건드리지 않음)

    def updates(self) -> dict[str, dict[str, object]]:
        """`settings.toml`에 쓸 {섹션: {키: 값}}. 값이 None이면 그 키를 주석 처리한다."""
        out: dict[str, dict[str, object]] = {
            "capture": {"monitor": self.monitor},
            "vision": {
                "resolution": self.resolution,
                "aspect": self.aspect,
                "profile": self.profile,
                "content_box": list(self.content_box) if self.content_box else None,
                "content_box_auto": self.content_box_auto,
            },
            "advisor": {"jev_backend": self.jev_backend},
        }
        overlay = {k: v for k, v in (("opacity", self.overlay_opacity), ("scale", self.overlay_scale))
                   if v is not None}
        if overlay:
            out["overlay"] = overlay
        if self.test_view is not None:
            out["ui"] = {"test_view": bool(self.test_view)}
        return out

    def apply(self, settings: Settings) -> Settings:
        """검증까지 마친 새 `Settings`(저장하기 전에 값이 맞는지 보려고 쓴다)."""
        return apply_updates(settings, self.updates())


def choice_from_detection(det: SetupDetection, settings: Settings) -> SetupChoice:
    """감지 결과 → 기본 선택값. 모니터·게임 영역은 감지값으로 못박고, 비율/프로파일은 "auto"로 둔다
    (실측 비율이면 auto가 같은 결과를 내고, 해상도가 바뀌어도 따라간다). 실측이 아닌 비율만 이름을 적는다."""
    mon = det.monitor
    choice = SetupChoice(
        monitor=mon.number if mon is not None else "auto",
        content_box=det.content_box,
        content_box_auto=settings.vision.content_box_auto,
        jev_backend=settings.advisor.jev_backend,
        overlay_opacity=settings.overlay.opacity,
        overlay_scale=settings.overlay.scale,
        test_view=settings.ui.test_view,
    )
    if det.game_size[0] and det.game_size[1]:
        choice.resolution = f"{det.game_size[0]}x{det.game_size[1]}"
    if det.aspect is not None and not det.measured:
        choice.aspect = det.aspect
    return choice


def apply_updates(settings: Settings, updates: dict[str, dict[str, object]]) -> Settings:
    """`Settings`에 {섹션: {키: 값}}를 얹어 다시 검증한다. None 값은 그 키를 기본값(없음)으로 되돌린다."""
    raw = settings.model_dump()
    for section, keys in updates.items():
        raw.setdefault(section, {})
        for key, value in keys.items():
            raw[section][key] = value
    return Settings.model_validate(raw)


# ---------------------------------------------------------------------------
# settings.toml 쓰기 (주석·형식 보존)
# ---------------------------------------------------------------------------


def settings_path(config_dir: Path | None = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / "settings.toml"


def render_value(value: object) -> str:
    """파이썬 값 → TOML 표기."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        return repr(round(value, 6))
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(render_value(v) for v in value) + "]"
    raise TypeError(f"TOML로 쓸 수 없는 값: {value!r}")


_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]")


def _key_re(key: str) -> re.Pattern[str]:
    return re.compile(rf"^(\s*)(#\s*)?{re.escape(key)}\s*=")


def _trailing_comment(line: str) -> str:
    """값 뒤의 인라인 주석(`# …`). 문자열/배열 안의 #은 건너뛴다."""
    body = line.split("=", 1)[1] if "=" in line else line
    depth, quote = 0, ""
    for i, ch in enumerate(body):
        if quote:
            if ch == quote and body[i - 1:i] != "\\":
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "#" and depth == 0:
            return body[i:].rstrip()
    return ""


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m is None:
            continue
        if start is not None:
            return start, i
        if m.group(1).strip() == section:
            start = i + 1
    return (start, len(lines)) if start is not None else None


def _set_key(lines: list[str], section: str, key: str, value: object) -> list[str]:
    bounds = _section_bounds(lines, section)
    pattern = _key_re(key)
    if bounds is None:
        if value is None:
            return lines
        tail = lines + ([] if lines and not lines[-1].strip() else [])
        return tail + ["", f"[{section}]", f"{key} = {render_value(value)}"]
    start, end = bounds
    hit = commented = None
    for i in range(start, end):
        m = pattern.match(lines[i])
        if m is None:
            continue
        if m.group(2):
            commented = i if commented is None else commented
        else:
            hit = i
            break
    out = list(lines)
    idx = hit if hit is not None else commented
    if value is None:                       # 값 없음 → 주석 처리(설명은 남긴다)
        if hit is not None:
            out[hit] = "# " + out[hit].lstrip()
        return out
    if idx is None:                         # 섹션에 없던 키 → 섹션 끝에 추가
        at = end
        while at > start and not lines[at - 1].strip():
            at -= 1
        out.insert(at, f"{key} = {render_value(value)}")
        return out
    indent = pattern.match(lines[idx]).group(1)
    comment = _trailing_comment(lines[idx])
    out[idx] = f"{indent}{key} = {render_value(value)}" + (f"   {comment}" if comment else "")
    return out


def update_toml_text(text: str, updates: dict[str, dict[str, object]]) -> str:
    """TOML 원문에 값만 갈아 끼운다(주석·순서·빈 줄 보존). 없는 키는 섹션 끝에, 없는 섹션은 파일 끝에 추가한다."""
    lines = text.splitlines()
    for section, keys in updates.items():
        for key, value in keys.items():
            lines = _set_key(lines, section, key, value)
    return "\n".join(lines) + "\n"


def save_settings(updates: dict[str, dict[str, object]], *, config_dir: Path | None = None,
                  path: Path | None = None) -> Path:
    """`settings.toml`에 저장한다. 쓰기 전에 **검증**하고, 덮어쓰기 전에 `.bak`으로 백업한다.

    주석·형식을 유지한 채 값만 바꾼다(파일이 없으면 새로 만든다). 결과가 설정 검증을 통과하지 못하면
    아무것도 쓰지 않고 예외를 낸다(깨진 설정으로 앱이 못 뜨는 일을 막는다).
    """
    target = path or settings_path(config_dir)
    old = target.read_text(encoding="utf-8") if target.is_file() else ""
    new = update_toml_text(old, updates)
    import tomllib

    Settings.model_validate(tomllib.loads(new))   # 실패하면 여기서 멈춘다(파일은 그대로)
    target.parent.mkdir(parents=True, exist_ok=True)
    if old:
        target.with_suffix(target.suffix + ".bak").write_text(old, encoding="utf-8")
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(target)
    log.info("설정 저장: %s", target)
    return target


# ---------------------------------------------------------------------------
# "셋업 완료" 상태 (_state/setup.json)
# ---------------------------------------------------------------------------


def resolve_state_dir(settings: Settings, root: Path | None = None) -> Path:
    from ..static_data import PROJECT_ROOT

    base = Path(settings.app.state_dir)
    return base if base.is_absolute() else (root or PROJECT_ROOT) / base


def setup_state_path(state_dir: Path) -> Path:
    return Path(state_dir) / SETUP_FILE


def setup_completed(state_dir: Path) -> bool:
    """첫 실행인가(= 셋업을 한 번도 끝내지 않았는가)의 반대."""
    path = setup_state_path(state_dir)
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(raw.get("completed_at"))


def mark_setup_done(state_dir: Path, choice: SetupChoice, det: SetupDetection | None = None) -> Path:
    """셋업을 끝냈다고 기록한다(다음 실행부터 자동으로 열리지 않는다)."""
    path = setup_state_path(state_dir)
    data = {
        "version": SETUP_VERSION,
        "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "monitor": choice.monitor,
        "resolution": choice.resolution,
        "aspect": choice.aspect,
        "profile": choice.profile,
        "content_box": list(choice.content_box) if choice.content_box else None,
        "detected": None if det is None else {
            "frame_size": list(det.frame_size), "game_size": list(det.game_size),
            "aspect": det.aspect, "profile": det.profile_name, "measured": det.measured,
        },
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("셋업 상태 저장 실패: %s", e)
    return path


# ---------------------------------------------------------------------------
# 테스트 캡처 (감지한 위치에 ROI가 맞는지 바로 보여 준다)
# ---------------------------------------------------------------------------


FIELD_LABELS: list[tuple[str, str]] = [
    ("screen_mode", "화면"), ("stage", "스테이지"), ("level", "레벨"), ("xp", "경험치"),
    ("gold", "골드"), ("streak", "연승/연패"), ("hp", "체력"), ("shop_odds", "상점 확률"),
    ("shop", "상점 5칸"), ("items", "아이템 벤치"), ("augments_owned", "보유 증강"),
    ("augment_offer", "증강 후보"),
]
"""테스트 캡처 결과표에 보여 줄 필드(순서 그대로)."""


@dataclass
class TestCapture:
    """[테스트 캡처] 결과."""

    ok: bool = False
    message: str = ""
    rows: list[tuple[str, str, float]] = field(default_factory=list)   # (한국어 라벨, 값, 신뢰도)
    image: Any = None            # ROI를 그린 BGR 프레임(미리보기)
    elapsed_ms: float = 0.0
    state: Any = None            # GameState


def _value_text(state, field_name: str) -> str:
    from ..contracts import ScreenMode

    value = getattr(state, field_name, None)
    if value is None:
        return "—"
    if field_name == "screen_mode":
        return {ScreenMode.PLANNING: "준비", ScreenMode.COMBAT: "전투", ScreenMode.CAROUSEL: "캐러셀",
                ScreenMode.AUGMENT_SELECT: "증강 선택", ScreenMode.ITEM_SELECT: "특수 선택",
                ScreenMode.GAME_OVER: "게임 종료", ScreenMode.LOADING: "로딩",
                ScreenMode.UNKNOWN: "판별 실패"}.get(value, str(value))
    if field_name == "shop":
        return " / ".join((s.id or ("빈 칸" if s.kind.value == "empty" else s.kind.value)) for s in value)
    if field_name == "items":
        owned = list(getattr(value, "bench", None) or [])
        return f"{len(owned)}개: " + ", ".join(owned[:6]) if owned else "0개"
    if field_name == "xp":
        return f"{value[0]}/{value[1]}"
    if isinstance(value, list):
        return ", ".join(str(getattr(v, "id", v)) for v in value) or "—"
    return str(value)


def run_test_capture(image, recognizer, *, draw: bool = True) -> TestCapture:
    """프레임 1장 → 인식 결과표 + ROI를 그린 미리보기. 실패해도 예외를 내지 않는다."""
    import time

    out = TestCapture()
    if image is None:
        out.message = "캡처하지 못했습니다."
        return out
    if is_black_frame(image):
        out.message = "화면이 검게 잡혔습니다.\n" + permission_help()
        return out
    t0 = time.perf_counter()
    try:
        state = recognizer.recognize(image)
    except Exception as e:   # noqa: BLE001 — 인식 실패가 대화상자를 죽이지 않는다
        log.warning("테스트 캡처 인식 실패", exc_info=True)
        out.message = f"인식 중 오류: {type(e).__name__}: {e}"
        return out
    out.elapsed_ms = (time.perf_counter() - t0) * 1000
    out.state = state
    out.rows = [(label, _value_text(state, name), state.confidence_of(name)) for name, label in FIELD_LABELS]
    out.ok = True
    read = sum(1 for name, _ in FIELD_LABELS if getattr(state, name, None) is not None)
    if state.stage is None and state.level is None and state.gold is None:
        out.message = ("게임 HUD를 읽지 못했습니다 — TFT 게임 중(준비 단계) 화면이 아니거나, "
                       "모니터/게임 화면 영역이 잘못 잡혔습니다.")
    else:
        out.message = f"{read}/{len(FIELD_LABELS)}개 필드를 읽었습니다 · 인식 {out.elapsed_ms:.0f}ms"
    if draw:
        try:
            from ..vision.regions import FrameMapper, draw_rois

            content = recognizer.content_for(image, None)
            mapper = FrameMapper.for_image(image, content)
            _, _, bw, bh = mapper.box
            out.image = draw_rois(image, recognizer.profile_for(bw, bh), mapper)
        except Exception:   # noqa: BLE001
            log.debug("ROI 렌더 실패", exc_info=True)
            out.image = image
    return out


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------


SetupAction = Literal["start", "saved", "cancelled"]


@dataclass
class SetupOutcome:
    """셋업 종료 결과. `start` = 저장하고 실시간 앱으로 넘어간다, `saved` = 저장만, `cancelled` = 취소."""

    action: SetupAction = "cancelled"
    settings: Settings | None = None
    path: Path | None = None
    choice: SetupChoice | None = None


def commit(choice: SetupChoice, *, settings: Settings, config_dir: Path | None = None,
           det: SetupDetection | None = None, action: SetupAction = "start",
           state_dir: Path | None = None) -> SetupOutcome:
    """선택값을 저장하고(settings.toml + _state/setup.json) 새 Settings를 돌려준다."""
    path = save_settings(choice.updates(), config_dir=config_dir)
    fresh = load_settings(config_dir)
    mark_setup_done(state_dir or resolve_state_dir(fresh), choice, det)
    return SetupOutcome(action=action, settings=fresh, path=path, choice=choice)


# ---------------------------------------------------------------------------
# 콘솔 셋업 (PySide6가 없거나 --no-overlay)
# ---------------------------------------------------------------------------


_CONSOLE_MENU = """
무엇을 할까요?
  [1] 이대로 저장하고 시작        [2] 모니터 다시 고르기
  [3] 화면 비율 직접 지정         [4] 다시 감지
  [5] 저장만 하고 끝내기          [6] 테스트 캡처(인식 결과 보기)
  [0] 취소(저장하지 않음)
선택> """


def console_test_capture(choice: SetupChoice, settings: Settings, det: SetupDetection,
                         out: Callable[[str], None]) -> TestCapture:
    """콘솔 [테스트 캡처] — 마지막 감지 때 찍어 둔 화면을 **지금 고른 설정으로** 인식해 표로 보여 준다."""
    probe = det.probes[det.chosen] if det.probes else None
    if probe is None or probe.frame is None:
        out("  찍어 둔 화면이 없습니다 — 먼저 [4] 다시 감지하세요.")
        return TestCapture()
    try:
        applied = choice.apply(settings)
    except Exception as e:   # noqa: BLE001 — 값이 서로 어긋나면 알려만 준다
        out(f"  설정 값이 서로 어긋납니다: {e}")
        return TestCapture()
    out("  인식하는 중… (처음에는 몇 초 걸립니다)")
    try:
        from ..vision.recognizer import Recognizer

        result = run_test_capture(probe.frame, Recognizer(cfg=applied.vision), draw=False)
    except Exception as e:   # noqa: BLE001
        log.warning("콘솔 테스트 캡처 실패", exc_info=True)
        out(f"  테스트 캡처 실패: {type(e).__name__}: {e}")
        return TestCapture()
    for line in result.message.splitlines():
        out("  " + line)
    for label, value, conf in result.rows:
        out(f"    {label:<8} {value}" + ("" if value == "—" else f"   ({conf:.2f})"))
    return result


def run_console_setup(settings: Settings, *, config_dir: Path | None = None,
                      input_fn: Callable[[str], str] = input,
                      out: Callable[[str], None] = print,
                      detect_fn: Callable[[], SetupDetection] | None = None,
                      state_dir: Path | None = None) -> SetupOutcome:
    """대화형 콘솔 셋업 — 대화상자와 같은 감지·저장을 글자로 한다."""
    detect_fn = detect_fn or (lambda: detect_with_recognizer(settings, out=out))
    out("\n=== TFT Advisor 설정 (화면 자동 감지) ===")
    out("  TypeSafe API 키: " + key_status_line())
    det = safe_detect(detect_fn)
    choice = choice_from_detection(det, settings)

    def show() -> None:
        out("")
        for line in det.summary_lines():
            out("  " + line)
        for note in det.notes:
            out("  · " + note)
        for warn in det.warnings:
            out("  ⚠ " + warn)
        if det.permission_issue:
            out("\n" + permission_help())
        out(f"\n저장할 값: [capture] monitor = {choice.monitor} · [vision] resolution = {choice.resolution}"
            f" · aspect = {choice.aspect} · profile = {choice.profile}"
            f" · content_box = {list(choice.content_box) if choice.content_box else '없음(전체)'}")

    show()
    while True:
        try:
            answer = input_fn(_CONSOLE_MENU).strip()
        except (EOFError, KeyboardInterrupt):
            out("\n취소했습니다(저장하지 않음).")
            return SetupOutcome(action="cancelled", settings=settings)
        if answer in ("1", ""):
            return commit(choice, settings=settings, config_dir=config_dir, det=det, action="start",
                          state_dir=state_dir)
        if answer == "5":
            return commit(choice, settings=settings, config_dir=config_dir, det=det, action="saved",
                          state_dir=state_dir)
        if answer == "0":
            out("취소했습니다(저장하지 않음).")
            return SetupOutcome(action="cancelled", settings=settings)
        if answer == "2":
            for i, probe in enumerate(det.probes, start=1):
                mark = " ←지금" if i - 1 == det.chosen else ""
                state = "검은 화면" if probe.black else f"점수 {probe.score:.2f}"
                out(f"  [{i}] {probe.info.label()} · {state}{mark}")
            out("  [0] 자동(실행할 때마다 TFT 화면을 찾습니다)")
            pick = input_fn("모니터 번호> ").strip()
            if pick == "0":
                choice.monitor = "auto"
            elif pick.isdigit() and 1 <= int(pick) <= len(det.probes):
                det.chosen = int(pick) - 1
                choice = choice_from_detection(det, settings)
            else:
                out("  잘못된 입력 — 그대로 둡니다.")
            show()
            continue
        if answer == "3":
            out("  비율: " + " / ".join(ASPECT_CHOICES))
            pick = input_fn("비율> ").strip()
            if pick in ASPECT_CHOICES:
                choice.aspect = pick
                if pick != "auto":
                    choice.resolution = "auto"   # 비율을 못박으면 해상도 검증 충돌을 피한다
            else:
                out("  잘못된 입력 — 그대로 둡니다.")
            show()
            continue
        if answer == "4":
            det = safe_detect(detect_fn)
            choice = choice_from_detection(det, settings)
            show()
            continue
        if answer == "6":
            console_test_capture(choice, settings, det, out)
            continue
        out("  1 / 2 / 3 / 4 / 5 / 6 / 0 중에서 골라 주세요.")


def needs_setup(settings: Settings, *, state_dir: Path | None = None) -> bool:
    """첫 실행이면 True(= 셋업을 열어야 한다)."""
    return not setup_completed(state_dir or resolve_state_dir(settings))


def run_setup(settings: Settings, *, config_dir: Path | None = None, gui: bool = True,
              state_dir: Path | None = None, **kwargs: Any) -> SetupOutcome:
    """셋업을 연다. `gui=True`면 PySide6 대화상자, 없으면 콘솔로 내려간다."""
    if gui:
        try:
            from .setup_dialog import run_setup_dialog
        except ImportError:
            log.warning("PySide6가 없습니다 → 콘솔 설정으로 내려갑니다")
        else:
            return run_setup_dialog(settings, config_dir=config_dir, state_dir=state_dir)
    return run_console_setup(settings, config_dir=config_dir, state_dir=state_dir, **kwargs)


__all__ = [
    "MonitorInfo", "MonitorProbe", "MonitorGrabber", "QtScreenInfo", "SetupChoice", "SetupDetection",
    "SetupOutcome", "TestCapture", "apply_updates", "choice_from_detection", "commit", "detect", "detect_live",
    "detect_with_recognizer", "is_black_frame", "mark_setup_done", "merge_qt_screens", "monitors_from_mss",
    "needs_setup", "jev_key_present", "jev_checks", "jev_backend_from_checks",
    "KEY_GROUP_TITLE", "KEY_FRIEND_NOTE", "KEY_ENV_NOTE", "key_status_line",
    "permission_help", "render_value", "resolve_state_dir", "run_console_setup", "run_setup",
    "console_test_capture", "run_test_capture", "safe_detect", "save_settings", "settings_path", "setup_completed", "setup_state_path",
    "update_toml_text",
]
