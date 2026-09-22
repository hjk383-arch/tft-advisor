"""프레임 소스 — 화면 캡처(mss)와 스크린샷 파일을 같은 인터페이스로 제공한다.

안전 원칙: 화면 픽셀만 읽는다. 게임 프로세스·메모리·입력에는 접근하지 않는다.
mss는 OS 화면 캡처 API(Windows GDI BitBlt / macOS CoreGraphics)만 호출한다.

모든 프레임은 BGR `np.ndarray`(H, W, 3, uint8)다. 인식 코드는 소스 종류를 모른다.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np


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


class MssSource:
    """mss 모니터 캡처. `region`(left, top, width, height)을 주면 그 영역만(게임 창 위치를 사용자가 지정할 때).

    전용 전체화면(exclusive fullscreen)에서는 검은 화면이 잡힐 수 있다 → 테두리 없는 창모드 권장.
    """

    def __init__(self, monitor: int = 1, region: tuple[int, int, int, int] | None = None) -> None:
        import mss  # vision extra; 지연 import로 테스트·임포트 비용 회피

        self._sct = mss.mss()
        if region is not None:
            left, top, width, height = region
            self._box = {"left": left, "top": top, "width": width, "height": height}
            self._desc = f"mss:region={region}"
        else:
            mons = self._sct.monitors
            if not 0 <= monitor < len(mons):
                raise ValueError(f"모니터 번호 {monitor} 없음 (사용 가능: 0~{len(mons) - 1}, 0=전체)")
            self._box = mons[monitor]
            self._desc = f"mss:monitor={monitor}"

    def grab(self) -> Frame | None:
        shot = self._sct.grab(self._box)
        bgra = np.asarray(shot)            # (H, W, 4) BGRA
        img = np.ascontiguousarray(bgra[:, :, :3])
        return Frame(image=img, captured_at=datetime.now(UTC), source=self._desc)

    def close(self) -> None:
        self._sct.close()
