"""화면 캡처·인식 (소유: vision-engineer). 화면 픽셀만 사용한다(메모리 읽기·입력 자동화 금지).

출력 계약: contracts.GameState

    from tft_advisor.vision import Recognizer, FileSource
    rec = Recognizer()                       # OCR 백엔드 자동 선택(onnxruntime → openvino → 없음)
    state = rec.recognize(frame.image)       # BGR ndarray → GameState(+confidence)

무거운 의존성(cv2, rapidocr, mss)은 하위 모듈에서만 import 한다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["Recognizer", "recognize", "recognize_file", "FileSource", "MssSource", "ArraySource", "Frame"]

if TYPE_CHECKING:
    from .capture import ArraySource, FileSource, Frame, MssSource
    from .recognizer import Recognizer, recognize, recognize_file


def __getattr__(name: str):
    if name in ("Recognizer", "recognize", "recognize_file"):
        from . import recognizer
        return getattr(recognizer, name)
    if name in ("FileSource", "MssSource", "ArraySource", "Frame"):
        from . import capture
        return getattr(capture, name)
    raise AttributeError(name)
