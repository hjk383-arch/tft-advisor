"""OCR 엔진 래퍼 — RapidOCR(한국어 PP-OCRv5 rec) + 숫자 글리프 템플릿 매칭 대안.

백엔드 선택(`RapidOcrEngine.create`)
- onnxruntime 이 import 되면 onnxruntime (Windows 대상 기본)
- 없으면 openvino (개발 머신 macOS x86_64 + Python 3.14 에는 onnxruntime 휠이 없다 → openvino 대체)
- 둘 다 없으면 None → `NullOcr` (문자 필드는 모두 None, 숫자는 글리프 템플릿이 있으면 그것으로)

모델 파일은 첫 사용 시 rapidocr가 내려받는다(네트워크 필요, 약 20MB). 이후에는 로컬 캐시.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextBox:
    """인식된 텍스트 한 덩어리. box는 입력 이미지 픽셀 (x1, y1, x2, y2)."""

    text: str
    score: float
    box: tuple[float, float, float, float]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def cx(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def cy(self) -> float:
        return (self.box[1] + self.box[3]) / 2


class OcrEngine(Protocol):
    name: str

    def read(self, image: np.ndarray) -> list[TextBox]:
        """검출+인식. 이미지 안의 텍스트 덩어리 전부."""
        ...

    def read_line(self, image: np.ndarray) -> TextBox | None:
        """이미지 전체를 한 줄로 보고 인식만(검출 없음). 빈 결과면 None."""
        ...

    # 선택(있으면 recognizer가 쓴다, 없으면 read_line()/read()로 대체한다):
    # def read_lines(self, images) -> list[TextBox | None]
    #     여러 한 줄 이미지를 한 번에(배치) 인식. read_line을 여러 번 부르는 것보다 빠르다.
    # def read_boxes(self, image, keep: Callable[[tuple[float, float, float, float]], bool]) -> list[TextBox]
    #     검출 후 keep(box)인 박스만 인식(배치). 필요 없는 박스의 인식 비용을 아낀다.


def _upscale(image: np.ndarray, min_h: int = 48) -> tuple[np.ndarray, float]:
    """작은 ROI를 키운다(rapidocr 검출기는 30px 미만 글자에 약하다). 반환: (이미지, 배율)."""
    import cv2

    h = image.shape[0]
    if h == 0 or h >= min_h:
        return image, 1.0
    f = min(4.0, max(2.0, min_h / h))
    return cv2.resize(image, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC), f


class NullOcr:
    """OCR 백엔드가 없을 때. 항상 빈 결과."""

    name = "none"

    def read(self, image: np.ndarray) -> list[TextBox]:
        return []

    def read_line(self, image: np.ndarray) -> TextBox | None:
        return None

    def read_boxes(self, image: np.ndarray, keep: Callable[[tuple[float, float, float, float]], bool]) -> list[TextBox]:
        return []

    def read_lines(self, images: list[np.ndarray]) -> list[TextBox | None]:
        return [None] * len(images)


class RapidOcrEngine:
    """rapidocr 3.x 래퍼. det/cls는 기본(중국어 PP-OCRv6 det = 언어 무관), rec는 한국어."""

    def __init__(self, backend: str, lang: str = "korean") -> None:
        from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR

        engine = {"onnxruntime": EngineType.ONNXRUNTIME, "openvino": EngineType.OPENVINO}[backend]
        params = {
            "Det.engine_type": engine,
            "Cls.engine_type": engine,
            "Rec.engine_type": engine,
            "Rec.lang_type": LangRec(lang),
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            # 기본값(limit_type=min, 736)은 작은 ROI를 최소변 736px까지 확대해 검출이 실패한다 → 최대변 제한으로.
            "Det.limit_type": "max",
            "Det.limit_side_len": 1280,
            "Global.log_level": "error",   # "detection result is empty" 경고가 프레임마다 나오지 않게
        }
        self._ocr = RapidOCR(params=params)
        self.name = f"rapidocr[{backend},{lang}]"

    @staticmethod
    def available_backend() -> str | None:
        try:
            import rapidocr  # noqa: F401
        except ImportError:
            return None
        for mod in ("onnxruntime", "openvino"):
            try:
                __import__(mod)
                return mod
            except ImportError:
                continue
        return None

    @classmethod
    def create(cls, lang: str = "korean") -> RapidOcrEngine | None:
        backend = cls.available_backend()
        if backend is None:
            log.warning("OCR 백엔드 없음(rapidocr + onnxruntime/openvino). 문자 인식을 끕니다.")
            return None
        return cls(backend, lang)

    def read(self, image: np.ndarray) -> list[TextBox]:
        if image.size == 0:
            return []
        img, f = _upscale(image)
        # use_* 인자는 rapidocr 내부에 남는다(read_line 호출 뒤 검출이 꺼진 채로 유지됨) → 매번 명시
        res = self._ocr(img, use_det=True, use_cls=False, use_rec=True)
        boxes = getattr(res, "boxes", None)   # 검출 결과가 없으면 rapidocr가 boxes 없는 객체를 돌려준다
        if boxes is None or res.txts is None:
            return []
        out = []
        for text, score, pts in zip(res.txts, res.scores, boxes):
            pts = np.asarray(pts, dtype=float) / f
            x1, y1 = pts.min(axis=0)
            x2, y2 = pts.max(axis=0)
            out.append(TextBox(str(text), float(score), (float(x1), float(y1), float(x2), float(y2))))
        return out

    def read_boxes(self, image: np.ndarray, keep: Callable[[tuple[float, float, float, float]], bool]) -> list[TextBox]:
        """검출만 돌린 뒤 keep(box)인 박스만 한 번에(배치) 인식. box는 입력 이미지 픽셀 (x1, y1, x2, y2).

        rapidocr 기본 파이프라인은 검출된 모든 박스를 인식한다(플레이어 목록이면 이름 8개 + 숫자 8개). 숫자만 필요할 때
        인식 비용을 절반 이하로 줄인다(QA04-V7). 박스는 축 정렬 사각형으로 자른다(HUD 글자는 기울지 않음)."""
        if image.size == 0:
            return []
        img, f = _upscale(image)
        res = self._ocr(img, use_det=True, use_cls=False, use_rec=False)
        boxes = getattr(res, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        picked: list[tuple[tuple[float, float, float, float], np.ndarray]] = []
        H, W = img.shape[:2]
        for pts in boxes:
            pts = np.asarray(pts, dtype=float)
            x1, y1 = pts.min(axis=0)
            x2, y2 = pts.max(axis=0)
            box = (float(x1 / f), float(y1 / f), float(x2 / f), float(y2 / f))
            if not keep(box):
                continue
            xi1, yi1 = max(0, int(x1)), max(0, int(y1))
            xi2, yi2 = min(W, int(np.ceil(x2))), min(H, int(np.ceil(y2)))
            if xi2 - xi1 >= 2 and yi2 - yi1 >= 2:
                picked.append((box, img[yi1:yi2, xi1:xi2]))
        if not picked:
            return []
        txts, scores = self._rec_batch([c for _, c in picked])
        return [TextBox(str(t), float(sc), b) for (b, _), t, sc in zip(picked, txts, scores) if str(t).strip()]

    def read_lines(self, images: list[np.ndarray]) -> list[TextBox | None]:
        """한 줄 이미지 여러 장을 한 번의 배치 인식으로(검출 없음). 각 결과는 read_line과 같은 형식."""
        idx = [i for i, im in enumerate(images) if im.size > 0]
        out: list[TextBox | None] = [None] * len(images)
        if not idx:
            return out
        txts, scores = self._rec_batch([_upscale(images[i])[0] for i in idx])
        for i, t, sc in zip(idx, txts, scores):
            if str(t).strip():
                h, w = images[i].shape[:2]
                out[i] = TextBox(str(t), float(sc), (0.0, 0.0, float(w), float(h)))
        return out

    def _rec_batch(self, crops: list[np.ndarray]) -> tuple[list[str], list[float]]:
        text_rec = getattr(self._ocr, "text_rec", None)
        if text_rec is not None:
            try:
                from rapidocr.ch_ppocr_rec.typings import TextRecInput

                out = text_rec(TextRecInput(img=crops))
                return list(out.txts or []), [float(v) for v in (out.scores or [])]
            except Exception as e:  # noqa: BLE001 — rapidocr 내부 API가 바뀌면 한 줄 인식으로 대체
                log.debug("배치 인식 실패, 한 줄 인식으로 대체: %s", e)
        res = [self.read_line(c) for c in crops]
        return [r.text if r else "" for r in res], [r.score if r else 0.0 for r in res]

    def read_line(self, image: np.ndarray) -> TextBox | None:
        if image.size == 0:
            return None
        img, _ = _upscale(image)
        res = self._ocr(img, use_det=False, use_cls=False, use_rec=True)
        if not res.txts or not res.txts[0].strip():
            return None
        h, w = image.shape[:2]
        return TextBox(str(res.txts[0]), float(res.scores[0]), (0.0, 0.0, float(w), float(h)))


def create_ocr(lang: str = "korean", backend: str = "auto") -> OcrEngine:
    """OCR 엔진. backend(`[vision] ocr_backend`): "auto" = 사용 가능한 최선(없으면 NullOcr), "none" = NullOcr,
    "onnxruntime"/"openvino" = 그 런타임 강제(import 불가면 경고 후 NullOcr — 조용히 다른 런타임으로 바꾸지 않는다)."""
    if backend == "none":
        return NullOcr()
    if backend == "auto":
        return RapidOcrEngine.create(lang) or NullOcr()
    try:
        import rapidocr  # noqa: F401
        __import__(backend)
    except ImportError:
        log.warning("ocr_backend=%s 를 쓸 수 없습니다(rapidocr 또는 %s 미설치). 문자 인식을 끕니다.", backend, backend)
        return NullOcr()
    return RapidOcrEngine(backend, lang)


# ---------------------------------------------------------------------------
# 숫자 글리프 템플릿 매칭 (OCR 대안, layout.md 2절 "1순위")
# ---------------------------------------------------------------------------

GLYPH_SIZE = (16, 24)   # (w, h) 정규화 크기
GLYPH_CHARS = "0123456789-/%"
_GLYPH_FILE = {"-": "dash", "/": "slash", "%": "percent"}


def binarize_light_text(image: np.ndarray) -> np.ndarray:
    """밝은 글자(HUD 숫자는 흰색/연노랑) → 255, 배경 → 0. Otsu."""
    import cv2

    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return bw


def segment_glyphs(bw: np.ndarray, min_h_ratio: float = 0.35) -> list[tuple[int, int, int, int]]:
    """이진 이미지에서 글자 후보 박스(x, y, w, h)를 왼쪽→오른쪽으로. 너무 작은 잡음은 버린다."""
    import cv2

    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    H = bw.shape[0]
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if h < H * min_h_ratio or area < 4:
            continue
        boxes.append((int(x), int(y), int(w), int(h)))
    boxes.sort(key=lambda b: b[0])
    return boxes


def _norm_glyph(bw: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    import cv2

    x, y, w, h = box
    g = bw[y:y + h, x:x + w]
    return cv2.resize(g, GLYPH_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0


class DigitTemplateReader:
    """숫자·기호 글리프 템플릿으로 짧은 숫자 문자열을 읽는다. 폰트가 고정인 HUD 숫자용.

    템플릿: `{glyph_dir}/{char}.png` (char: 0-9, dash, slash, percent). 흰 글자/검은 배경 이진 이미지.
    원본 1920x1080 캡처에서 `harvest()`로 만든다. 템플릿이 없으면 `ready=False`.
    """

    name = "digit-template"

    def __init__(self, templates: dict[str, np.ndarray]) -> None:
        self.templates = templates

    @classmethod
    def from_dir(cls, glyph_dir: str | Path) -> DigitTemplateReader:
        from .capture import load_image
        import cv2

        glyph_dir = Path(glyph_dir)
        templates: dict[str, np.ndarray] = {}
        for ch in GLYPH_CHARS:
            p = glyph_dir / f"{_GLYPH_FILE.get(ch, ch)}.png"
            if p.is_file():
                img = cv2.cvtColor(load_image(p), cv2.COLOR_BGR2GRAY)
                templates[ch] = cv2.resize(img, GLYPH_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        return cls(templates)

    @property
    def ready(self) -> bool:
        return all(d in self.templates for d in "0123456789")

    def read_line(self, image: np.ndarray) -> TextBox | None:
        if not self.templates or image.size == 0:
            return None
        bw = binarize_light_text(image)
        boxes = segment_glyphs(bw)
        if not boxes:
            return None
        chars, scores = [], []
        for b in boxes:
            g = _norm_glyph(bw, b)
            best_ch, best = "", -1.0
            for ch, t in self.templates.items():
                s = _ncc(g, t)
                if s > best:
                    best_ch, best = ch, s
            chars.append(best_ch)
            scores.append(best)
        h, w = image.shape[:2]
        return TextBox("".join(chars), float(max(0.0, min(scores))), (0.0, 0.0, float(w), float(h)))

    def read(self, image: np.ndarray) -> list[TextBox]:
        tb = self.read_line(image)
        return [tb] if tb else []

    @staticmethod
    def harvest(image: np.ndarray, label: str, out_dir: str | Path) -> bool:
        """정답 문자열을 아는 숫자 ROI에서 글리프를 잘라 저장. 글자 수가 맞지 않으면 False(저장 안 함)."""
        from .capture import save_image

        label = label.replace(" ", "")
        bw = binarize_light_text(image)
        boxes = segment_glyphs(bw)
        if len(boxes) != len(label):
            return False
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for ch, (x, y, w, h) in zip(label, boxes):
            if ch not in GLYPH_CHARS:
                continue
            save_image(out / f"{_GLYPH_FILE.get(ch, ch)}.png", bw[y:y + h, x:x + w])
        return True


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    """정규화 상호상관(-1~1)."""
    a = a - a.mean()
    b = b - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-9 else 0.0
