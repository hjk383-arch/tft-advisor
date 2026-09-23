"""프레임 소스 — 화면 캡처(mss)와 스크린샷 파일을 같은 인터페이스로 제공한다.

안전 원칙: 화면 픽셀만 읽는다. 게임 프로세스·메모리·입력에는 접근하지 않는다.
mss는 OS 화면 캡처 API(Windows GDI BitBlt / macOS CoreGraphics)만 호출한다.

모든 프레임은 BGR `np.ndarray`(H, W, 3, uint8)다. 인식 코드는 소스 종류를 모른다.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Frame:
    """캡처 한 장. `source`는 파일 경로 또는 "mss:monitor=N" 같은 설명."""

    image: np.ndarray
    captured_at: datetime
    source: str

    @property
    def size(self) -> tuple[int, int]:
        """(width, height)."""
        h, w = self.image.shape[:2]
        return w, h


@runtime_checkable
class FrameSource(Protocol):
    """프레임 공급자. 실시간 캡처와 파일이 같은 코드 경로를 타게 하는 경계."""

    def grab(self) -> Frame | None:
        """다음 프레임. 더 없으면 None."""
        ...

    def close(self) -> None: ...


def load_image(path: str | Path) -> np.ndarray:
    """이미지 파일 → BGR 배열. 한글 경로(Windows cv2.imread 실패)를 피하려고 imdecode를 쓴다."""
    import cv2

    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"이미지를 읽을 수 없음: {path}")
    return img


def save_image(path: str | Path, image: np.ndarray) -> bool:
    """BGR 배열 → PNG 등 파일. `cv2.imwrite`는 Windows에서 한글 경로를 깨뜨리므로(파일명이 "ëŒ€ê²€" 같은 모지바케가
    되거나 저장 실패) imencode + tofile을 쓴다. 성공하면 True."""
    import cv2

    path = Path(path)
    ok, buf = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        return False
    buf.tofile(str(path))
    return True


class FileSource:
    """스크린샷 파일 목록을 순서대로 내보낸다(테스트, `--screenshot` 모드)."""

    def __init__(self, paths: str | Path | Sequence[str | Path]) -> None:
        if isinstance(paths, (str, Path)):
            paths = [paths]
        self._paths = [Path(p) for p in paths]
        self._i = 0

    def grab(self) -> Frame | None:
        if self._i >= len(self._paths):
            return None
        p = self._paths[self._i]
        self._i += 1
        return Frame(image=load_image(p), captured_at=datetime.now(UTC), source=str(p))

    def close(self) -> None:
        self._i = len(self._paths)

    def __iter__(self) -> Iterator[Frame]:
        while (f := self.grab()) is not None:
            yield f


class ArraySource:
    """메모리 배열을 프레임으로(테스트용)."""

    def __init__(self, images: Sequence[np.ndarray], source: str = "array") -> None:
        self._images = list(images)
        self._source = source
        self._i = 0

    def grab(self) -> Frame | None:
        if self._i >= len(self._images):
            return None
        img = self._images[self._i]
        self._i += 1
        return Frame(image=img, captured_at=datetime.now(UTC), source=f"{self._source}[{self._i - 1}]")

    def close(self) -> None:
        self._i = len(self._images)


MONITOR_REPICK_S = 30.0
"""monitor="auto"일 때 몇 초마다 다른 모니터에 게임이 떴는지 다시 확인할지(게임 창을 옮긴 경우)."""
MONITOR_CONFIRMED = 0.5
"""점수 이 이상 = 게임 화면으로 확인(스테이지 글자가 읽힘 = 1.0). 이보다 낮으면 계속 다시 찾는다."""


def default_screen_score(image: np.ndarray) -> float:
    """OCR 없는 기본 채점: 위쪽 가운데 스테이지 막대가 어둡다(`regions.stage_bar_score`). 앱은 인식기의 OCR 채점을 넘긴다."""
    from .regions import profile_for_frame, stage_bar_score

    h, w = image.shape[:2]
    return 0.5 * stage_bar_score(image, (0, 0, w, h), profile_for_frame(w, h))


def pick_monitor(frames: Sequence[np.ndarray], scorer: Callable[[np.ndarray], float],
                 prefer: int | None = None) -> tuple[int, float]:
    """모니터별 프레임(모니터 1번부터 순서대로) → (고른 인덱스 0부터, 점수). 가장 높은 점수, 동점이면 `prefer`(지금 쓰는
    모니터 — 다른 모니터에 TFT 방송 등이 떠 같은 1.0이 나와도 옮기지 않는다, QA08), 없으면 앞(주 모니터 쪽)."""
    best, best_score = 0, -1.0
    scores: list[float] = []
    for i, img in enumerate(frames):
        try:
            sc = float(scorer(img))
        except Exception:   # noqa: BLE001 — 채점 실패는 0점(캡처 루프를 멈추지 않는다)
            log.debug("모니터 %d 채점 실패", i + 1, exc_info=True)
            sc = 0.0
        scores.append(sc)
        if sc > best_score:
            best, best_score = i, sc
    if prefer is not None and 0 <= prefer < len(scores) and scores[prefer] >= best_score:
        return prefer, scores[prefer]
    return best, best_score


class MssSource:
    """mss 모니터 캡처. `region`(left, top, width, height)을 주면 그 영역만(게임 창 위치를 사용자가 지정할 때).

    `monitor`: mss 번호(1 = 주 모니터, 2 = 두 번째 …, 0 = 모든 모니터를 합친 가상 화면) 또는 `"auto"`.
    `"auto"`는 첫 프레임에서 모든 모니터를 한 장씩 찍어 `scorer`(기본: 스테이지 막대 픽셀, 앱은 인식기의 OCR 채점)로
    **TFT 화면이 있는 모니터**를 고른다. 게임이 두 번째 모니터에 있는 듀얼 모니터 사용자를 위한 것이다.
    확인되지 않았으면(점수 < 0.5, 예: 아직 로비) `MONITOR_REPICK_S`마다 다시 찾고, 확인된 뒤에도 같은 주기로
    다른 모니터가 **확인 점수로 더 높을 때만** 옮긴다(게임 종료 화면 등에서 엉뚱한 모니터로 튀지 않게).

    전용 전체화면(exclusive fullscreen)에서는 검은 화면이 잡힐 수 있다 → 테두리 없는 창모드 권장.
    mss 인스턴스는 **처음 grab()을 부른 스레드에서** 만든다(Windows GDI 핸들은 스레드에 묶인다).
    """

    def __init__(self, monitor: int | str = 1, region: tuple[int, int, int, int] | None = None,
                 scorer: Callable[[np.ndarray], float] | None = None, repick_s: float = MONITOR_REPICK_S) -> None:
        import mss  # vision extra; 지연 import로 테스트·임포트 비용 회피

        self._mss = mss
        self._sct = None
        self._region = region
        self._auto = region is None and str(monitor).strip().lower() == "auto"
        # region과 "auto"를 함께 주면 region이 이긴다(예전엔 int("auto") ValueError, QA08)
        self._monitor = 1 if str(monitor).strip().lower() == "auto" else int(monitor)
        self._scorer = scorer or default_screen_score
        self._repick_s = repick_s
        self._picked_at = 0.0
        self._picked_score = 0.0
        self._box: dict | None = None
        self._desc = ""
        if region is not None:
            left, top, width, height = region
            self._box = {"left": left, "top": top, "width": width, "height": height}
            self._desc = f"mss:region={region}"

    @property
    def monitor(self) -> int:
        """지금 캡처 중인 mss 모니터 번호(auto면 고른 결과)."""
        return self._monitor

    def _ensure(self) -> None:
        if self._sct is None:
            self._sct = self._mss.mss()
        if self._box is not None and not (self._auto and time.monotonic() - self._picked_at >= self._repick_s):
            return
        mons = self._sct.monitors
        if self._auto:
            self._pick(mons)
            return
        if not 0 <= self._monitor < len(mons):
            raise ValueError(f"모니터 번호 {self._monitor} 없음 (사용 가능: 0~{len(mons) - 1}, 0=전체, 또는 \"auto\")")
        self._box = mons[self._monitor]
        self._desc = f"mss:monitor={self._monitor}"

    def _pick(self, mons: list[dict]) -> None:
        self._picked_at = time.monotonic()
        physical = mons[1:] or mons[:1]
        if len(physical) == 1:
            idx, score = 0, 1.0
        else:
            frames = [np.ascontiguousarray(np.asarray(self._sct.grab(m))[:, :, :3]) for m in physical]
            prefer = self._monitor - 1 if self._box is not None and 1 <= self._monitor <= len(physical) else None
            idx, score = pick_monitor(frames, self._scorer, prefer=prefer)
            if self._box is not None and score < max(MONITOR_CONFIRMED, self._picked_score):
                return   # 이미 고른 모니터 유지(더 확실한 후보가 없다)
        number = idx + 1 if len(mons) > 1 else 0
        if number != self._monitor or self._box is None:
            log.info("게임 모니터 자동 선택: %d번 (점수 %.2f, 모니터 %d대)", number, score, len(physical))
        self._monitor, self._picked_score = number, score
        self._box = mons[number]
        self._desc = f"mss:monitor={number}(auto)"

    def grab(self) -> Frame | None:
        self._ensure()
        shot = self._sct.grab(self._box)
        bgra = np.asarray(shot)            # (H, W, 4) BGRA
        img = np.ascontiguousarray(bgra[:, :, :3])
        return Frame(image=img, captured_at=datetime.now(UTC), source=self._desc)

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None
