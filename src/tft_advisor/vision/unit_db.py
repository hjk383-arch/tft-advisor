"""유닛 사진 DB — 챔피언별 보드·벤치 모델 크롭을 **모으고(대기)**, 사람이 **검토해 승인**한 것만 이름 판정에 쓴다.

근거·결정: `_workspace/25_unit_image_db.md`. 안전 원칙은 그대로다 — 화면 픽셀만 본다(메모리·입력 없음).

## 왜
특성 패널 풀이(`vision.units`)는 보드에만 통하고 상징·증강·가려진 유닛에 약하다. 모델 크롭 라이브러리는 표본이 있어야
하는데, 표본을 **자동으로** 확정하면 틀린 표본이 스스로 강해진다(QA 19 FAIL-1). 그래서

1. 실시간으로 **증거가 강한** 크롭만 **대기**(pending)에 모은다(아래 증거 종류). 대기 크롭은 이름 판정에 쓰지 않는다
   (설정으로 낮은 가중치를 줄 수는 있다, 기본 0 = 안 씀).
2. 사용자가 검토 창(`app.unit_review`, `python -m tft_advisor review-units`)에서 승인·삭제·다른 챔피언으로 고치기·성급을 한다.
3. **승인**(approved) 크롭만 라이브러리 표본이 된다. 예전 사용자 확인 라벨로 만든 `label_*`도 승인으로 친다.

## 디스크 배치 (`data/templates/{set}/units_screen/`, gitignore — Riot 아트워크, 커밋 금지)
```
units_screen/{apiName}/{id}.png (+ {id}.json)            승인. 옛 `label_*.png`(json 없음)도 여기
units_screen/_pending/{apiName}/{id}.png + {id}.json     대기
units_screen/_trash/{apiName}/...                        삭제(되살릴 수 있게 옮기기만 한다)
```
`{id}.json`(`CropMeta`): 챔피언, 성급, 증거 종류·점수, 판(game) ID, 스테이지, 맵 서명, 시각, 원본 프레임 해시, 칸.

## 증거 종류(`Evidence`)
| 종류 | 언제 | 강도 |
|---|---|---|
| `purchase` | 상점 칸 X가 빈 칸이 되고(= 구매, 상점·장부 보고는 한 건), 같은 정산 창 안에 **직전 2프레임 비어 있던 벤치 칸 하나**가 생겼고 창 안에 다른 벤치·보드 변화가 없다(1성) | 가장 강함 |
| `duplicate` | 같은 프레임에서 이름이 확정된 보드 유닛과 같은 모델인 벤치 유닛(라이브러리는 아직 모름) | 보통 |
| `library` | 벤치 칸의 라이브러리 이름이 **뒷받침**(보드 확정·특성 집합·장부 구매 힌트)되었지만 아직 확실하지는 않다(신뢰도 0.6~0.8) | 보통 |
| `traits` | (예전) 특성 풀이로 이름 받은 **보드** 칸 — 30 보고 뒤로 모으지 않는다(보드 크롭은 효과·피해 숫자·겹친 유닛·잘린 머리가 많았다) | — |

## 수집 정책(사용자, 2026-09-25): "인식이 되는 캐릭터는 더 안 찍어도 된다. 사진은 대기석에 있을 때 찍어"
- **벤치 칸만** 모은다(보통 사서 벤치에 놓는다). 보드 크롭은 모으지 않는다.
- 그 챔피언·성급의 **승인** 사진이 `collect_until`장(설정 `[vision] unit_collect_until`, 기본 3) 이상이면 더 모으지 않는다.
  성급 모름(옛 `label_*`)은 1성으로 센다. 새 성급(★2·★3)은 처음 보이면 모은다.
- 이번 크롭이 이미 라이브러리로 **알아본** 칸(같은 챔피언, 뒷받침, 신뢰도 >= `RECOGNIZED_CONF`)이면 모으지 않는다.
- 품질(`crop_quality`): 전략가가 그 칸 위 · 강한 효과(밝고 진한 빛이 모델의 30% 이상) · 선택 윤곽(청록 빛 8% 이상)은 버린다.
  옆 칸 모델 침범·약한 효과·청록 조금·머리 잘림은 **표시**(note "품질: …")하고 점수를 낮춘다(검토 창에서 뒤로).
- 스테이지는 app이 알려 준 **세션 스테이지**(오버레이 표시값, `set_stage`)를 쓴다. 없으면 프레임 판독값을 쓰되 이번 판에서 본
  가장 늦은 스테이지보다 앞서면 비운다(오독).
| `legacy_forced` | 옛 자동 학습(`auto_*`, 승인 폴더에 바로 저장되던 것)을 대기로 옮긴 것 | 검토 필요 |
| `label` / `manual` | 사용자 확인 라벨 수확 / 검토 창에서 사람이 준 이름 | 승인 |
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

PENDING_DIR = "_pending"
TRASH_DIR = "_trash"
APPROVED = "approved"
PENDING = "pending"

EVIDENCE_PURCHASE = "purchase"
EVIDENCE_TRAITS = "traits"
EVIDENCE_DUPLICATE = "duplicate"
EVIDENCE_LEGACY = "legacy_forced"
EVIDENCE_LABEL = "label"
EVIDENCE_MANUAL = "manual"
EVIDENCE_LIBRARY = "library"
EVIDENCE_UNKNOWN = "unknown"
EVIDENCE_USER = "user"
UNKNOWN_CHAMPION = "_unknown"
"""이름 미상 크롭의 챔피언 자리(폴더 `_pending/_unknown/`). 사용자가 검토 창에서 이름을 주면 그 챔피언 승인 폴더로 옮긴다."""
EVIDENCE_KO = {
    EVIDENCE_UNKNOWN: "이름 미상(벤치)", EVIDENCE_USER: "사용자 이름",
    EVIDENCE_PURCHASE: "상점 구매", EVIDENCE_TRAITS: "특성 풀이", EVIDENCE_DUPLICATE: "같은 모델(보드)",
    EVIDENCE_LIBRARY: "사진 비교(뒷받침)",
    EVIDENCE_LEGACY: "옛 자동 학습", EVIDENCE_LABEL: "확인 라벨", EVIDENCE_MANUAL: "직접 지정",
}

PENDING_CAP = 8           # (챔피언, 성급, 증거)마다 대기 크롭 상한 — 넘으면 **맵 서명이 새로운** 크롭만 받는다
NEAR_DUP = 0.95           # 같은 챔피언의 기존 크롭(대기·승인)과 이보다 닮으면 거의 같은 그림 → 저장하지 않는다
PURCHASE_WINDOW_S = 3.0   # 상점 구매와 벤치 새 칸이 이 시간 안에 함께 보여야 짝을 짓는다(장부 정산 창 2초 + 여유)
TRAITS_MIN_CONF = 0.85    # (예전) 특성 풀이 증거로 모을 칸의 최소 이름 신뢰도 — 30 뒤로 보드 크롭은 모으지 않는다
DUPLICATE_MIN_CONF = 0.8
PANEL_SURE = 0.75         # (units.PANEL_SURE와 같은 값) 패널 판독이 이보다 애매하면 특성 증거로 모으지 않는다
MIN_EMPTY_FRAMES = 2      # 구매 새 칸은 직전 이 프레임 수만큼 연속으로 비어 있던 칸이어야 한다(판독 깜빡임 거르기)
CONTRADICT_MIN = 0.8      # 구매 크롭이 다른 챔피언 승인 크롭을 이만큼 이상 닮고
CONTRADICT_MARGIN = 0.1   # 산 챔피언 승인 크롭보다 이만큼 더 닮으면 모순 -> 저장하지 않는다
COLLECT_UNTIL = 3         # 챔피언·성급별 승인 사진이 이만큼 있으면 더 모으지 않는다(설정 `unit_collect_until`)
RECOGNIZED_CONF = 0.8     # 라이브러리가 이 신뢰도 이상(뒷받침)으로 이미 알아본 칸은 모으지 않는다
LIBRARY_EVIDENCE_MIN = 0.6   # 뒷받침된 라이브러리 이름을 증거로 모으는 최소 신뢰도(그 위 RECOGNIZED_CONF부터는 이미 안다)
# 품질(모델 픽셀 = `units.model_mask` 기준). 실측: 사용자 검토(2026-09-25) 승인 47장 · 삭제 6장
GLOW_REJECT = 0.30        # 밝고 진한 빛(V>=235, S>=100)이 모델의 이만큼 이상: 금화·폭발(삭제 쉔 0.70 / 승인 최대 0.15)
GLOW_FLAG = 0.10          # 표시만(삭제: 연기 0.14 · 피해 숫자 0.15 · 스킬 효과 0.11 / 승인 대부분 < 0.09)
CYAN_REJECT = 0.08        # 선택 윤곽(청록 H 80~100) 크롭 전체 비율(삭제 0.093 / 승인 최대 0.054)
CYAN_FLAG = 0.04
SIDE_FLAG = 0.05          # 가운데와 이어지지 않고 크롭 좌우 끝에 닿은 모델 조각 = 옆 칸 모델(삭제 0.055~0.068, 승인 대부분 < 0.03)
HEAD_CUT_FLAG = 0.45      # 크롭 위쪽 줄(체력바 바로 아래) 가운데에 모델이 이만큼 차 있으면 머리가 잘렸을 수 있다
FLAG_SCORE_MULT = 0.7
UNKNOWN_MIN_FRAMES = 2    # 이름 없는 벤치 칸이 준비 프레임 연속 이만큼이면 이름 미상 크롭으로 저장한다
UNKNOWN_CAP = 20          # 판마다 이름 미상 크롭 상한
UNKNOWN_SAME = 0.70       # 같은 판에서 이 이상 닮으면 같은 유닛(실측 다른 챔피언 최대 0.59)
UNKNOWN_PER_UNIT = 2      # 한 유닛당 크롭 수(처음 + 나중 한 장)
UNKNOWN_SECOND_S = 20.0   # 두 번째 크롭은 첫 크롭보다 이만큼 뒤(다른 자세·조명)
UNKNOWN_SUGGEST_MAX = 8


# ---------------------------------------------------------------------------
# 메타데이터
# ---------------------------------------------------------------------------


@dataclass
class CropMeta:
    """크롭 한 장의 메타데이터. `{id}.json`에 저장한다(`path`·`status`는 파일 위치에서 정해진다)."""

    id: str
    champion: str
    status: str = PENDING
    star: int | None = None
    evidence: str = EVIDENCE_LABEL
    score: float | None = None
    game: str | None = None
    stage: str | None = None
    arena: str | None = None
    at: str | None = None
    frame: str | None = None
    slot: str | None = None
    note: str | None = None
    reviewed_at: str | None = None
    suggestions: list[str] | None = None
    """이름 미상 크롭의 이름 후보(챔피언 ID, 앞일수록 유력): 같은 판에서 나중에 붙은 이름 > 장부 보유(자리 미상) >
    특성 풀이 자리 미상 > 이번 판 상점에 나온 챔피언. 검토 창이 빠른 선택 버튼으로 보여 준다(자동 승인 없음)."""
    path: Path | None = field(default=None, compare=False, repr=False)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("path", None)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any], path: Path) -> CropMeta:
        keys = {f for f in cls.__dataclass_fields__ if f != "path"}
        return cls(**{k: v for k, v in raw.items() if k in keys}, path=path)

    @property
    def json_path(self) -> Path | None:
        return self.path.with_suffix(".json") if self.path is not None else None


@dataclass(frozen=True)
class ChampionCoverage:
    champion: str
    approved: Mapping[int | None, int]
    """성급(1/2/3, None = 모름) → 승인 크롭 수."""
    pending: int

    @property
    def approved_total(self) -> int:
        return sum(self.approved.values())


@dataclass(frozen=True)
class Coverage:
    rows: tuple[ChampionCoverage, ...]

    @property
    def covered(self) -> int:
        return sum(1 for r in self.rows if r.approved_total > 0)

    @property
    def missing(self) -> list[str]:
        return [r.champion for r in self.rows if r.approved_total == 0]

    unknown: int = 0
    """이름 미상 대기 크롭 수(`_pending/_unknown/`)."""

    @property
    def pending(self) -> int:
        return sum(r.pending for r in self.rows)

    def summary(self, names: Mapping[str, str] | None = None) -> str:
        """한국어 요약 한 줄."""
        unk = f" · 이름 미상 {self.unknown}장" if self.unknown else ""
        return (f"승인된 챔피언 {self.covered}/{len(self.rows)} · 검토 대기 {self.pending}장{unk} · "
                f"사진 없는 챔피언 {len(self.missing)}명")

    def table(self, names: Mapping[str, str] | None = None) -> str:
        """콘솔용 표(챔피언별 승인 ★1/★2/★3/성급 모름, 대기)."""
        names = names or {}
        lines = [self.summary(names), "", f"{'챔피언':<14}{'★1':>4}{'★2':>4}{'★3':>4}{'?':>4}{'대기':>6}"]
        for r in sorted(self.rows, key=lambda r: (r.approved_total > 0, names.get(r.champion, r.champion))):
            a = r.approved
            lines.append(f"{names.get(r.champion, r.champion):<14}{a.get(1, 0):>4}{a.get(2, 0):>4}{a.get(3, 0):>4}"
                         f"{a.get(None, 0):>4}{r.pending:>6}" + ("   ← 없음" if r.approved_total == 0 else ""))
        return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def crop_digest(crop: np.ndarray) -> str:
    return hashlib.sha1(np.ascontiguousarray(crop).tobytes()).hexdigest()[:12]


def frame_digest(image: np.ndarray) -> str:
    """원본 프레임 해시(작게 줄인 회색조 — 같은 프레임을 다시 저장해도 같은 값)."""
    import cv2

    small = cv2.resize(image[..., :3] if image.ndim == 3 else image, (64, 36), interpolation=cv2.INTER_AREA)
    return hashlib.sha1(small.tobytes()).hexdigest()[:12]


@dataclass(frozen=True)
class CropQuality:
    """벤치 크롭 품질. `reject`가 있으면 저장하지 않는다. `flags`는 저장하되 note에 적고 점수를 낮춘다."""

    reject: str | None = None
    flags: tuple[str, ...] = ()
    glow: float = 0.0
    cyan: float = 0.0
    side: float = 0.0
    head: float = 0.0


def crop_quality(crop: np.ndarray) -> CropQuality:
    """모델 크롭(`units.unit_crop`) → 품질. 기준은 모듈 상단 상수(사용자 검토 승인 47 · 삭제 6장 실측)."""
    import cv2

    from .units import model_mask

    m = model_mask(crop).astype(bool)
    h, w = m.shape
    n = max(1, int(m.sum()))
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    glow = float(((val >= 235) & (sat >= 100) & m).sum()) / n
    cyan = float(((hue >= 80) & (hue <= 100) & (sat >= 120) & (val >= 170)).mean())
    num, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    center = set(np.unique(lab[:, w // 2 - w // 14:w // 2 + w // 14]).tolist()) - {0}
    edge = max(2, w // 10)      # 테두리 몇 줄은 배경 군집으로 잡혀 모델에서 빠지므로 "끝에 닿음"을 조금 넓게 본다
    side = sum(int(st[k][4]) for k in range(1, num)
               if k not in center and (st[k][0] <= edge or st[k][0] + st[k][2] >= w - edge)) / float(h * w)
    head = float(m[2:8, w // 4:3 * w // 4].mean()) if h > 8 else 0.0
    flags: list[str] = []
    reject = None
    if glow >= GLOW_REJECT:
        reject = "강한 효과(밝은 빛)"
    elif cyan >= CYAN_REJECT:
        reject = "선택 윤곽(청록)"
    if glow >= GLOW_FLAG:
        flags.append("효과")
    if cyan >= CYAN_FLAG:
        flags.append("청록 윤곽")
    if side >= SIDE_FLAG:
        flags.append("옆 칸 침범")
    if head >= HEAD_CUT_FLAG:
        flags.append("머리 잘림")
    return CropQuality(reject, tuple(flags), round(glow, 3), round(cyan, 3), round(side, 3), round(head, 3))


def arena_signature(image: np.ndarray, box: tuple[int, int, int, int]) -> str:
    """맵(바닥) 서명: 게임 화면 보드 가운데 줄무늬 영역의 중앙값 Lab 색을 16단계로 줄인 6자리 16진수.
    같은 맵이면 같고, 모래·돌·푸른 돌처럼 다른 맵이면 대개 다르다(대기 상한에서 '새로운 맵' 판단에만 쓴다)."""
    import cv2

    left, top, w, h = box
    y1, y2, x1, x2 = top + int(0.22 * h), top + int(0.60 * h), left + int(0.28 * w), left + int(0.72 * w)
    patch = image[max(0, y1):y2, max(0, x1):x2]
    if patch.size == 0:
        return "000000"
    lab = cv2.cvtColor(patch[..., :3], cv2.COLOR_BGR2LAB).reshape(-1, 3)
    med = np.median(lab, axis=0).astype(int) // 16
    return "".join(f"{int(v):02x}" for v in med)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


class UnitImageDB:
    """디스크의 유닛 사진 DB(대기·승인·삭제). 스레드 하나에서 쓴다(실시간 루프 또는 검토 창)."""

    def __init__(self, root: str | Path, valid: Callable[[str], bool] | None = None, *,
                 pending_cap: int = PENDING_CAP, near_dup: float = NEAR_DUP) -> None:
        self.root = Path(root)
        self.valid = valid
        self.pending_cap = pending_cap
        self.near_dup = near_dup
        self._index: dict[str, tuple[str, str | None, str, np.ndarray]] | None = None
        """크롭 id → (챔피언, 맵 서명, 상태, 기술자). 중복·상한 판정용. 처음 저장할 때 만든다."""
        self._approved: dict[tuple[str, int], int] | None = None
        """(챔피언, 성급) → 승인 사진 수 캐시(`approved_count`)."""

    # ------------------------------------------------------------------ 읽기
    def _dirs(self, status: str) -> Path:
        return self.root if status == APPROVED else self.root / PENDING_DIR

    def entries(self, status: str | None = None, champion: str | None = None) -> list[CropMeta]:
        """디스크의 크롭 목록(승인 → 대기 순, 챔피언·파일 이름 순)."""
        out: list[CropMeta] = []
        for st in ((APPROVED, PENDING) if status is None else (status,)):
            base = self._dirs(st)
            if not base.is_dir():
                continue
            for sub in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
                if champion is not None and sub.name != champion:
                    continue
                for png in sorted(sub.glob("*.png")):
                    out.append(self._meta_for(png, sub.name, st))
        return out

    def _meta_for(self, png: Path, champion: str, status: str) -> CropMeta:
        jp = png.with_suffix(".json")
        if jp.is_file():
            try:
                meta = CropMeta.from_json(json.loads(jp.read_text(encoding="utf-8")), png)
            except (OSError, ValueError, TypeError):
                log.warning("유닛 사진 DB: 메타데이터를 읽지 못했습니다 %s", jp)
                meta = CropMeta(id=png.stem, champion=champion, path=png)
        else:
            ev = EVIDENCE_LABEL if png.stem.startswith("label_") else (
                EVIDENCE_LEGACY if png.stem.startswith("auto_") else EVIDENCE_MANUAL)
            meta = CropMeta(id=png.stem, champion=champion, evidence=ev, path=png)
        # 위치가 진실이다(파일을 손으로 옮겨도 맞게 읽는다)
        meta.champion, meta.status, meta.path = champion, status, png
        return meta

    @staticmethod
    def load_image(meta: CropMeta) -> np.ndarray:
        from .capture import load_image

        img = load_image(meta.path)
        return img[..., :3] if img.ndim == 3 and img.shape[2] == 4 else img

    # ------------------------------------------------------------------ 쓰기
    def _write(self, meta: CropMeta, crop: np.ndarray | None = None) -> CropMeta:
        import cv2

        assert meta.path is not None
        meta.path.parent.mkdir(parents=True, exist_ok=True)
        if crop is not None:
            ok, buf = cv2.imencode(".png", crop)
            if not ok:
                raise OSError(f"PNG 인코딩 실패: {meta.path}")
            buf.tofile(str(meta.path))
        meta.json_path.write_text(json.dumps(meta.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
        return meta

    def _move(self, meta: CropMeta, champion: str, status: str | None) -> CropMeta:
        """크롭(+json)을 (챔피언, 상태) 폴더로 옮긴다. status None = 휴지통."""
        assert meta.path is not None
        base = self.root / TRASH_DIR if status is None else self._dirs(status)
        dest = base / champion / meta.path.name
        if dest != meta.path:
            if dest.exists():
                dest = dest.with_name(f"{meta.path.stem}_{uuid.uuid4().hex[:4]}.png")
            dest.parent.mkdir(parents=True, exist_ok=True)
            old_json = meta.json_path
            shutil.move(str(meta.path), str(dest))
            if old_json is not None and old_json.is_file():
                old_json.unlink()
        new = replace(meta, champion=champion, status=status or meta.status, path=dest, id=dest.stem)
        self._write(new)
        self._index = None
        self._approved = None
        return new

    def _ensure_index(self) -> dict[str, tuple[str, str | None, str, np.ndarray]]:
        if self._index is None:
            from .units import descriptor

            idx: dict[str, tuple[str, str | None, str, np.ndarray]] = {}
            for meta in self.entries():
                try:
                    idx[meta.id] = (meta.champion, meta.arena, meta.status, descriptor(self.load_image(meta)))
                except Exception:                                   # 읽지 못하는 파일은 건너뛴다
                    log.warning("유닛 사진 DB: 읽기 실패 %s", meta.path)
            self._index = idx
        return self._index

    def add_pending(self, champion: str, crop: np.ndarray, *, evidence: str, star: int | None = None,
                    score: float | None = None, game: str | None = None, stage: str | None = None,
                    arena: str | None = None, frame: str | None = None, slot: str | None = None,
                    approve: bool = False, note: str | None = None) -> CropMeta | None:
        """증거가 있는 크롭을 대기에 넣는다(`approve`면 바로 승인 — 설정으로만 켠다). 저장하지 않았으면 None.

        저장하지 않는 경우: 알 수 없는 챔피언 · 같은 그림(해시) · 거의 같은 그림(같은 챔피언 기존 크롭과 닮음 >= `NEAR_DUP`) ·
        (챔피언, 성급, 증거) 대기 상한을 넘었고 그 맵 서명이 이미 있다.
        """
        from .units import descriptor, similarity

        if self.valid is not None and not self.valid(champion):
            log.warning("유닛 사진 DB: 알 수 없는 챔피언 %s — 저장하지 않음", champion)
            return None
        idx = self._ensure_index()
        digest = crop_digest(crop)
        cid = f"{evidence}_{digest}"
        if any(k.endswith(digest) for k in idx):
            return None
        d = descriptor(crop)
        same = [(a, st, ref) for c, a, st, ref in idx.values() if c == champion]
        if any(similarity(d, ref) >= self.near_dup for _, _, ref in same):
            return None
        if not approve:
            peers = [m for m in self.entries(PENDING, champion) if m.star == star and m.evidence == evidence]
            if len(peers) >= self.pending_cap and any(m.arena == arena for m in peers):
                return None
        status = APPROVED if approve else PENDING
        meta = CropMeta(id=cid, champion=champion, status=status, star=star, evidence=evidence,
                        score=None if score is None else round(float(score), 3), game=game, stage=stage,
                        arena=arena, at=_now_iso(), frame=frame, slot=slot, note=note,
                        reviewed_at=_now_iso() if approve else None,
                        path=self._dirs(status) / champion / f"{cid}.png")
        self._write(meta, crop)
        idx[cid] = (champion, arena, status, d)
        if approve:
            self._approved = None
        log.info("유닛 사진 %s: %s ★%s (%s %.2f)", "승인" if approve else "대기", champion, star, evidence,
                 score if score is not None else -1)
        return meta

    def approve(self, meta: CropMeta) -> CropMeta:
        new = self._move(meta, meta.champion, APPROVED)
        new.reviewed_at = _now_iso()
        return self._write(new)

    def unapprove(self, meta: CropMeta) -> CropMeta:
        return self._move(meta, meta.champion, PENDING)

    def delete(self, meta: CropMeta) -> None:
        """휴지통(`_trash/`)으로 옮긴다(되살릴 수 있다)."""
        self._move(meta, meta.champion, None)

    def relabel(self, meta: CropMeta, champion: str) -> CropMeta:
        """다른 챔피언으로 고친다(파일과 메타데이터를 그 챔피언 폴더로 옮긴다, 상태는 그대로)."""
        if self.valid is not None and not self.valid(champion):
            raise ValueError(f"알 수 없는 챔피언: {champion}")
        new = self._move(meta, champion, meta.status)
        new.evidence, new.reviewed_at = EVIDENCE_MANUAL if meta.status == APPROVED else new.evidence, _now_iso()
        new.note = (f"{meta.champion} → {champion} " + (new.note or "")).strip()
        return self._write(new)

    def set_star(self, meta: CropMeta, star: int | None) -> CropMeta:
        if star not in (None, 1, 2, 3):
            raise ValueError(f"성급은 1~3: {star!r}")
        meta.star = star
        meta.reviewed_at = _now_iso()
        return self._write(meta)

    def migrate_legacy(self) -> int:
        """옛 자동 학습 크롭(승인 폴더의 `auto_*.png`)을 대기로 옮긴다(검토 전에는 믿지 않는다). 옮긴 수."""
        moved = 0
        for meta in self.entries(APPROVED):
            if meta.path is not None and meta.path.stem.startswith("auto_") and meta.evidence == EVIDENCE_LEGACY:
                self._move(meta, meta.champion, PENDING)
                moved += 1
        if moved:
            log.info("유닛 사진 DB: 옛 자동 학습 크롭 %d장을 검토 대기로 옮겼습니다", moved)
        return moved

    def approved_count(self, champion: str, star: int | None) -> int:
        """승인 사진 수(그 챔피언·성급). 성급 모름(옛 `label_*`)은 1성으로 센다. 디스크 목록은 캐시한다(옮기면 비운다)."""
        if self._approved is None:
            counts: dict[tuple[str, int], int] = {}
            for meta in self.entries(APPROVED):
                key = (meta.champion, meta.star or 1)
                counts[key] = counts.get(key, 0) + 1
            self._approved = counts
        return self._approved.get((champion, star or 1), 0)

    # ------------------------------------------------------------------ 적용 범위
    def coverage(self, champions: Iterable[str]) -> Coverage:
        by: dict[str, dict[int | None, int]] = {}
        pend: dict[str, int] = {}
        for meta in self.entries():
            if meta.status == APPROVED:
                row = by.setdefault(meta.champion, {})
                row[meta.star] = row.get(meta.star, 0) + 1
            else:
                pend[meta.champion] = pend.get(meta.champion, 0) + 1
        return Coverage(tuple(ChampionCoverage(c, dict(by.get(c, {})), pend.get(c, 0)) for c in champions),
                        unknown=len(self.unknown_entries()))

    # ------------------------------------------------------------------ 이름 미상(벤치)
    def unknown_entries(self) -> list[CropMeta]:
        """이름 미상 대기 크롭(`_pending/_unknown/`), 오래된 것부터."""
        d = self.root / PENDING_DIR / UNKNOWN_CHAMPION
        if not d.is_dir():
            return []
        return [self._meta_for(png, UNKNOWN_CHAMPION, PENDING) for png in sorted(d.glob("*.png"))]

    def add_unknown(self, crop: np.ndarray, *, star: int | None = None, game: str | None = None,
                    stage: str | None = None, arena: str | None = None, frame: str | None = None,
                    slot: str | None = None, suggestions: Sequence[str] = (), note: str | None = None,
                    score: float | None = None) -> CropMeta | None:
        """이름 미상 벤치 크롭을 `_pending/_unknown/`에 넣는다. 같은 그림(해시)은 한 번만."""
        digest = crop_digest(crop)
        d = self.root / PENDING_DIR / UNKNOWN_CHAMPION
        if d.is_dir() and any(p.stem.endswith(digest) for p in d.glob("*.png")):
            return None
        cid = f"{EVIDENCE_UNKNOWN}_{digest}"
        meta = CropMeta(id=cid, champion=UNKNOWN_CHAMPION, status=PENDING, star=star, evidence=EVIDENCE_UNKNOWN,
                        score=score, game=game, stage=stage, arena=arena, at=_now_iso(), frame=frame, slot=slot,
                        note=note, suggestions=list(dict.fromkeys(suggestions)) or None, path=d / f"{cid}.png")
        self._write(meta, crop)
        log.info("유닛 사진 이름 미상: %s ★%s (후보 %s)", slot, star, meta.suggestions)
        return meta

    def suggest(self, meta: CropMeta, champion: str, note: str | None = None) -> CropMeta:
        """이름 미상 크롭에 이름 후보를 **맨 앞에** 더한다(같은 판에서 그 유닛에 나중에 이름이 붙었을 때). 승인하지 않는다."""
        meta.suggestions = [champion, *[c for c in (meta.suggestions or []) if c != champion]]
        if note:
            meta.note = note
        return self._write(meta)

    def label_unknown(self, meta: CropMeta, champion: str) -> CropMeta:
        """사용자가 이름을 준 이름 미상 크롭 → 그 챔피언 **승인** 폴더(사용자의 이름이 확인이다), 근거 `user`."""
        if self.valid is not None and not self.valid(champion):
            raise ValueError(f"알 수 없는 챔피언: {champion}")
        new = self._move(meta, champion, APPROVED)
        new.evidence, new.reviewed_at = EVIDENCE_USER, _now_iso()
        new.note = ("이름 미상 → 사용자 이름 " + (new.note or "")).strip()
        return self._write(new)


def roster(static: Any) -> list[str]:
    """적용 범위 대상 = 특성이 있는 챔피언(훈련 봇·골렘·모루 같은 특성 없는 유닛은 뺀다)."""
    return sorted(c["apiName"] for c in static._load("champions") if [t for t in (c.get("traits") or []) if t])


def open_db(static: Any, directory: str | Path | None = None) -> UnitImageDB:
    from .units import units_dir

    root = units_dir(static.set_number) if directory is None else Path(directory)
    return UnitImageDB(root, valid=lambda c: static.get("champions", c) is not None)


# ---------------------------------------------------------------------------
# 실시간 수집기
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameContext:
    """이름 판정과 같은 프레임의 다른 판독(인식기가 채운다). 모르면 None."""

    at: float
    stage: str | None = None
    shop: tuple[str | None, ...] | None = None
    """상점 5칸: 챔피언 apiName | None(빈 칸) | "*"(특수 상품·식별 실패). 이번 프레임에 상점을 읽지 않았으면 None."""
    gold: int | None = None
    frame: str | None = None
    arena: str | None = None
    tactician: frozenset[int] = frozenset()
    """전략가(꼬마 전설이) 이름표가 위에 있는 벤치 칸(`bench_memory.tactician_cells`). 그 칸 크롭은 모으지 않는다."""


@dataclass
class _NewSlot:
    at: float
    slot: int
    crop: np.ndarray
    star: int | None
    ctx: FrameContext
    name: Any = None
    """그 칸의 이름 판정(`units.SlotName`) — 이미 알아본 칸이면 모으지 않는다."""


@dataclass
class _Buy:
    """구매 한 건. 상점 칸 비움(`shop`)과 장부(`ledger`)가 같은 구매를 따로 알리므로, 같은 챔피언·창 안의 다른 출처 보고는
    한 건으로 합친다(`sources`). `used` = 짝지어 저장했거나 합성·다른 변화로 무효가 된 구매 — 늦게 온 메아리를 흡수하려고
    창이 끝날 때까지 남겨 둔다."""

    at: float
    champion: str
    sources: set[str]
    used: bool = False


@dataclass
class _UnknownUnit:
    """같은 판의 이름 미상 유닛 하나(저장한 크롭들). 나중에 이름이 붙으면 `name`에 적고 크롭에 후보로 단다."""

    desc: np.ndarray
    metas: list[CropMeta]
    last_at: float
    name: str | None = None


@dataclass
class UnitCollector:
    """프레임마다 증거가 강한 크롭을 `UnitImageDB` 대기에 넣는다(`UnitNamer.name()`이 부른다).

    구매 증거(QA 27 F1 뒤 규칙):
    - 구매 = 상점 칸이 **챔피언 → 빈 칸**(인식기가 이 프레임의 상점을 읽었을 때) 또는 app 장부의 `note_purchase()`.
      같은 챔피언의 두 출처 보고는 `window_s` 안이면 **한 건**이다(짝이 끝난 뒤 늦게 온 메아리도 흡수한다).
    - 새 칸 = 이번 프레임에 벤치 칸이 **정확히 하나** 늘고(사라진 칸·성급이 바뀐 칸 없음, 보드 수·성급 구성 그대로), 그 칸이
      직전 `MIN_EMPTY_FRAMES` 프레임 내내 비어 있었고, 창 안에 다른 변화가 없었다.
    - 변화(칸 사라짐·성급 바뀜·보드 바뀜 = 옮기기·합성·판독 깜빡임)가 보이면 그때까지의 구매·새 칸을 모두 버린다
      (합성된 구매도 여기서 소비된다).
    - 창 안의 남은 구매가 모두 같은 챔피언이고 새 칸 수와 같을 때만 짝을 짓는다. 크롭이 그 챔피언의 승인 크롭보다 다른
      챔피언의 승인 크롭을 확실히 더 닮았으면 저장하지 않는다.
    """

    db: UnitImageDB
    game: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4])
    auto_approve_purchase: bool = False
    window_s: float = PURCHASE_WINDOW_S
    collect_until: int = COLLECT_UNTIL
    """챔피언·성급별 승인 사진이 이만큼 있으면 더 모으지 않는다(0 = 제한 없음). 설정 `[vision] unit_collect_until`."""
    session_stage: str | None = None
    """app 세션의 합친 스테이지(오버레이 표시값). `set_stage()`로 넣는다. 크롭 메타데이터의 스테이지로 쓴다."""
    saved: list[CropMeta] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    """모으지 않은 이유별 횟수(진단용): enough / recognized / quality / tactician."""
    _max_stage: tuple[int, int] | None = None
    unknown_cap: int = UNKNOWN_CAP
    _unk_streak: dict[int, int] = field(default_factory=dict)
    _unk_units: list[_UnknownUnit] = field(default_factory=list)
    _unk_saved: int = 0
    _shop_seen: dict[str, None] = field(default_factory=dict)
    """이번 판 상점에 나온 챔피언(순서 유지) — 이름 미상 크롭의 후보."""
    _buys: list[_Buy] = field(default_factory=list)
    _new: list[_NewSlot] = field(default_factory=list)
    _last_shop: tuple[str | None, ...] | None = None
    _history: list[dict[int, int | None]] = field(default_factory=list)
    """최근 벤치 판독(오래된 → 최근, 최대 `MIN_EMPTY_FRAMES`개): 칸 → 성급."""
    _last_board: tuple[int, tuple[int, ...]] | None = None
    """직전 보드 서명: (유닛 수, 성급 구성). 전투 중 유닛이 움직여도 같다(육각칸은 보지 않는다)."""
    _disturbed_at: float | None = None

    def reset(self) -> None:
        """새 판."""
        self.game = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self._buys.clear()
        self._new.clear()
        self._history.clear()
        self._last_shop = self._last_board = self._disturbed_at = None
        self.session_stage = None
        self._max_stage = None
        self._unk_streak.clear()
        self._unk_units.clear()
        self._unk_saved = 0
        self._shop_seen.clear()

    def set_stage(self, stage: str | None) -> None:
        """app 세션의 합친 스테이지(오버레이에 보이는 값). 프레임 판독 스테이지보다 이것을 믿는다."""
        self.session_stage = stage or None
        key = _stage_key(stage)
        if key is not None and (self._max_stage is None or key > self._max_stage):
            self._max_stage = key

    def _stage_for(self, ctx: FrameContext) -> str | None:
        """크롭에 적을 스테이지: 세션 스테이지 > 프레임 판독(이번 판에서 본 가장 늦은 스테이지보다 앞서면 오독으로 보고 비운다)."""
        if self.session_stage:
            return self.session_stage
        key = _stage_key(ctx.stage)
        if key is None:
            return None
        if self._max_stage is not None and key < self._max_stage:
            return None
        self._max_stage = key
        return ctx.stage

    def note_purchase(self, champion_id: str, at: float | None = None, source: str = "ledger") -> None:
        """app 장부의 구매 이벤트(상점에서 산 챔피언). 같은 창 안에 벤치에 새 칸이 생기면 그 크롭의 이름이 된다."""
        if not champion_id:
            return
        at = time.time() if at is None else at
        self._add_buy(at, champion_id, source)
        self._pair(at)

    def _add_buy(self, at: float, champion: str, source: str) -> None:
        # 같은 구매의 다른 출처 보고(상점 <-> 장부)는 합친다 — 가장 가까운, 아직 그 출처가 없는 같은 챔피언 구매로
        twins = [b for b in self._buys
                 if b.champion == champion and source not in b.sources and abs(b.at - at) <= self.window_s]
        if twins:
            min(twins, key=lambda b: abs(b.at - at)).sources.add(source)
            return
        # 마지막 변화와 같은 프레임(또는 그 전)의 구매 = 그 변화(합성 등)가 이미 소비했다
        used = self._disturbed_at is not None and at <= self._disturbed_at
        self._buys.append(_Buy(at, champion, {source}, used=used))

    def _disturb(self, now: float) -> None:
        """벤치·보드에 새 칸 말고 다른 변화가 보였다 → 창 안의 구매·새 칸을 모두 버린다(합성·옮기기·깜빡임)."""
        self._disturbed_at = now
        for b in self._buys:
            b.used = True
        self._new.clear()

    # ------------------------------------------------------------------ 한 프레임
    def observe(self, ctx: FrameContext, read: Any, names: Any, board_crops: Sequence[np.ndarray],
                bench_crops: Sequence[np.ndarray], panel_conf: float | None) -> list[CropMeta]:
        """`read` = 이름이 붙은 `BoardRead`, `names` = `units.BoardNames`. 이번 프레임에 저장한 크롭들."""
        before = len(self.saved)
        # 벤치 칸만 모은다(30 보고 · 사용자 정책). 보드 크롭(`board_crops`)은 효과·피해 숫자·겹친 유닛·잘린 머리가 많아 쓰지 않는다
        self._duplicates(ctx, read, names, bench_crops)
        self._library(ctx, read, names, bench_crops)
        self._purchases(ctx, read, bench_crops, names)
        return self.saved[before:]

    # ------------------------------------------------------------------ 이름 미상(벤치)
    def observe_unknown(self, ctx: FrameContext, read: Any, bench_crops: Sequence[np.ndarray], *,
                        owned: Iterable[str] = (), unplaced: Iterable[str] = ()) -> list[CropMeta]:
        """준비 단계 프레임의 **최종** 판독(정체 추적 뒤)에서 이름 없는 벤치 칸의 크롭을 `_pending/_unknown/`에 모은다.

        - 이름 없는 칸이 연속 `UNKNOWN_MIN_FRAMES` 프레임이어야 한다(끌기·효과 한 프레임은 거른다). 벤치만. 전략가 칸·품질 거부는 뺀다.
        - 같은 판에서 같은 유닛(닮음 >= `UNKNOWN_SAME`)은 `UNKNOWN_PER_UNIT`장까지(두 번째는 `UNKNOWN_SECOND_S` 뒤), 판마다 `unknown_cap`장.
        - 후보(`suggestions`): 장부 보유인데 칸에 이름이 없는 챔피언 > 특성 풀이 자리 미상 > 이번 판 상점에 나온 챔피언.
        - 나중에 그 유닛(닮은 크롭)에 이름이 붙으면 저장한 크롭의 후보 맨 앞에 그 이름을 단다(승인하지 않는다).
        `bench_crops`는 `read.bench`와 같은 순서여야 한다."""
        from .units import descriptor, similarity

        if ctx.shop is not None:
            for c in ctx.shop:
                if c not in (None, "*"):
                    self._shop_seen[c] = None
        before = len(self.saved)
        present: set[int] = set()
        named = {u.unit_id for u in (*read.board, *read.bench) if getattr(u, "unit_id", None)}
        for u, crop in zip(read.bench, bench_crops):
            slot = u.bench_slot
            if slot is None or crop is None:
                continue
            present.add(slot)
            if u.unit_id:
                self._unk_streak.pop(slot, None)
                self._note_named(descriptor(crop), u.unit_id, similarity)
                continue
            self._unk_streak[slot] = self._unk_streak.get(slot, 0) + 1
            if self._unk_streak[slot] < UNKNOWN_MIN_FRAMES or self._unk_saved >= self.unknown_cap:
                continue
            if slot in ctx.tactician:
                self._skip("tactician")
                continue
            q = crop_quality(crop)
            if q.reject is not None:
                self._skip("quality")
                continue
            d = descriptor(crop)
            unit = next((x for x in self._unk_units if similarity(x.desc, d) >= UNKNOWN_SAME), None)
            if unit is not None and (len(unit.metas) >= UNKNOWN_PER_UNIT or ctx.at - unit.last_at < UNKNOWN_SECOND_S):
                continue
            sugg = [c for c in owned if c not in named] + [c for c in unplaced if c not in named] + \
                [c for c in self._shop_seen if c not in named]
            if unit is not None and unit.name:
                sugg = [unit.name, *sugg]
            note = f"품질: {', '.join(q.flags)}" if q.flags else None
            meta = self.db.add_unknown(crop, star=u.star, game=self.game, stage=self._stage_for(ctx), arena=ctx.arena,
                                       frame=ctx.frame, slot=f"bench:{slot}",
                                       suggestions=list(dict.fromkeys(sugg))[:UNKNOWN_SUGGEST_MAX], note=note)
            if meta is None:
                continue
            self._unk_saved += 1
            self.saved.append(meta)
            if unit is None:
                self._unk_units.append(_UnknownUnit(d, [meta], ctx.at))
            else:
                unit.metas.append(meta)
                unit.last_at = ctx.at
        for slot in [s for s in self._unk_streak if s not in present]:
            del self._unk_streak[slot]
        return self.saved[before:]

    def _note_named(self, d: np.ndarray, champion: str, similarity: Callable) -> None:
        for unit in self._unk_units:
            if unit.name is None and similarity(unit.desc, d) >= UNKNOWN_SAME:
                unit.name = champion
                for m in unit.metas:
                    try:
                        self.db.suggest(m, champion, note=f"같은 판에서 나중에 붙은 이름: {champion}")
                    except OSError:
                        log.warning("이름 미상 크롭 후보 쓰기 실패: %s", m.path)

    def _skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def _save(self, champion: str, crop: np.ndarray, *, evidence: str, star: int | None, score: float,
              ctx: FrameContext, slot: int | None, name: Any = None, approve: bool = False) -> None:
        """벤치 칸 크롭 저장(정책·품질 검사를 거친다)."""
        if self.collect_until > 0 and self.db.approved_count(champion, star) >= self.collect_until:
            self._skip("enough")                  # 이 챔피언·성급은 사진이 충분하다(새 성급은 따로 센다)
            return
        if _recognized(name, champion):
            self._skip("recognized")              # 라이브러리가 이미 알아봤다
            return
        if slot is not None and slot in ctx.tactician:
            self._skip("tactician")
            return
        q = crop_quality(crop)
        if q.reject is not None:
            self._skip("quality")
            log.debug("유닛 사진: %s 벤치 %s 크롭 버림(%s)", champion, slot, q.reject)
            return
        note = f"품질: {', '.join(q.flags)}" if q.flags else None
        if q.flags:
            score *= FLAG_SCORE_MULT
        meta = self.db.add_pending(champion, crop, evidence=evidence, star=star, score=score, game=self.game,
                                   stage=self._stage_for(ctx), arena=ctx.arena, frame=ctx.frame,
                                   slot=f"bench:{slot}" if slot is not None else "bench:?",
                                   approve=approve and not q.flags, note=note)
        if meta is not None:
            self.saved.append(meta)

    def _duplicates(self, ctx: FrameContext, read: Any, names: Any, crops: Sequence[np.ndarray]) -> None:
        # 라이브러리는 모르고(source=duplicate) 같은 프레임의 확정 보드 유닛과 같은 모델인 벤치 칸
        for u, n, crop in zip(read.bench, names.bench, crops):
            if n.unit_id and n.source == "duplicate" and n.confidence >= DUPLICATE_MIN_CONF:
                self._save(n.unit_id, crop, evidence=EVIDENCE_DUPLICATE, star=u.star, score=n.confidence, ctx=ctx,
                           slot=u.bench_slot)

    def _library(self, ctx: FrameContext, read: Any, names: Any, crops: Sequence[np.ndarray]) -> None:
        # 뒷받침된 라이브러리 이름이지만 아직 확실하지 않은(0.6~0.8) 벤치 칸 = 새 자세·새 맵 사진
        for u, n, crop in zip(read.bench, names.bench, crops):
            if (n.unit_id and n.source == "library" and getattr(n, "corroborated", False)
                    and LIBRARY_EVIDENCE_MIN <= n.confidence < RECOGNIZED_CONF):
                self._save(n.unit_id, crop, evidence=EVIDENCE_LIBRARY, star=u.star, score=n.confidence, ctx=ctx,
                           slot=u.bench_slot, name=n)

    def _purchases(self, ctx: FrameContext, read: Any, crops: Sequence[np.ndarray], names: Any = None) -> None:
        now = ctx.at
        # 1) 상점 칸 챔피언 → 빈 칸 = 구매(상점을 이번 프레임에 읽었을 때만 비교한다)
        if ctx.shop is not None:
            if self._last_shop is not None and len(self._last_shop) == len(ctx.shop):
                for was, cur in zip(self._last_shop, ctx.shop):
                    if was not in (None, "*") and cur is None:
                        self._add_buy(now, was, "shop")
            self._last_shop = ctx.shop
        # 2) 벤치·보드 변화
        bench = {u.bench_slot: u.star for u in read.bench if u.bench_slot is not None}
        crop_at = {u.bench_slot: c for u, c in zip(read.bench, crops) if u.bench_slot is not None}
        bench_names = tuple(getattr(names, "bench", ()) or ())
        name_at = {u.bench_slot: n for u, n in zip(read.bench, bench_names) if u.bench_slot is not None}
        board = (len(read.board), tuple(sorted(int(u.star or 0) for u in read.board)))
        last = self._history[-1] if self._history else None
        if last is not None and self._last_board is not None:
            added = [s for s in bench if s not in last]
            gone = [s for s in last if s not in bench]
            restar = [s for s in bench if s in last and bench[s] != last[s]]
            if gone or restar or len(added) > 1 or board != self._last_board:
                self._disturb(now)
            elif added:
                s = added[0]
                quiet = self._disturbed_at is None or now - self._disturbed_at > self.window_s
                empty_before = (len(self._history) >= MIN_EMPTY_FRAMES
                                and all(s not in h for h in self._history[-MIN_EMPTY_FRAMES:]))
                if quiet and empty_before and len(bench) == len(last) + 1:
                    self._new.append(_NewSlot(now, s, crop_at[s], bench[s], ctx, name_at.get(s)))
                else:
                    self._disturb(now)           # 방금 있던 칸이 다시 보인 것(깜빡임) 등 — 새 칸으로 믿지 않는다
        self._history = [*self._history, bench][-MIN_EMPTY_FRAMES:]
        self._last_board = board
        # 3) 짝짓기
        self._pair(now)

    def _pair(self, now: float) -> None:
        self._buys = [b for b in self._buys if now - b.at <= self.window_s]
        self._new = [n for n in self._new if now - n.at <= self.window_s]
        live = [b for b in self._buys if not b.used]
        if not live or not self._new:
            return
        champs = {b.champion for b in live}
        if len(champs) != 1 or len(live) != len(self._new):
            if len(self._new) > len(live) or len(champs) > 1:
                log.debug("구매-벤치 짝이 모호합니다: 구매 %s / 새 칸 %d", live, len(self._new))
            return
        champ = next(iter(champs))
        for ns in self._new:
            if ns.star not in (None, 1):         # 산 유닛은 1성이다(2성이면 합성이 끼었다)
                continue
            if self._contradicts(champ, ns.crop):
                log.info("구매 크롭이 %s의 승인 사진보다 다른 챔피언을 더 닮았습니다 — 저장하지 않음", champ)
                continue
            self._save(champ, ns.crop, evidence=EVIDENCE_PURCHASE, star=1, score=0.95, ctx=ns.ctx,
                       slot=ns.slot, name=ns.name, approve=self.auto_approve_purchase)
        for b in live:
            b.used = True                        # 창이 끝날 때까지 남겨 늦게 온 다른 출처 보고를 흡수한다
        self._new.clear()

    def _contradicts(self, champion: str, crop: np.ndarray) -> bool:
        """승인 크롭 기준으로 이 크롭이 다른 챔피언이 확실한가(그 챔피언 승인 크롭이 있을 때만 판단한다)."""
        from .units import descriptor, similarity

        try:
            idx = self.db._ensure_index()
        except Exception:
            return False
        d: np.ndarray | None = None
        own: float | None = None
        other = 0.0
        for c, _, st, ref in idx.values():
            if st != APPROVED:
                continue
            d = descriptor(crop) if d is None else d
            s = similarity(d, ref)
            if c == champion:
                own = s if own is None else max(own, s)
            else:
                other = max(other, s)
        return own is not None and other >= CONTRADICT_MIN and other - own >= CONTRADICT_MARGIN


def _stage_key(stage: str | None) -> tuple[int, int] | None:
    try:
        a, b = str(stage).split("-")
        return int(a), int(b)
    except (ValueError, AttributeError):
        return None


def _recognized(name: Any, champion: str) -> bool:
    """이 칸을 라이브러리가 이미 그 챔피언으로 **알아봤다**(뒷받침 있음, 신뢰도 >= `RECOGNIZED_CONF`)."""
    return (name is not None and getattr(name, "unit_id", None) == champion
            and getattr(name, "source", None) == "library" and bool(getattr(name, "corroborated", False))
            and float(getattr(name, "confidence", 0.0)) >= RECOGNIZED_CONF)


__all__ = ["APPROVED", "PENDING", "ChampionCoverage", "Coverage", "CropMeta", "CropQuality", "EVIDENCE_KO",
           "FrameContext", "UnitCollector", "UnitImageDB", "arena_signature", "crop_digest", "crop_quality",
           "frame_digest", "open_db", "roster"]
