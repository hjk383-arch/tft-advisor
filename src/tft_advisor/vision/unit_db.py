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
| `purchase` | 상점 칸 X가 빈 칸이 되고(= 구매), 같은 정산 창 안에 **벤치에 새 칸 하나**가 생겼다(1성) | 가장 강함 |
| `traits` | 특성 패널 풀이가 **하나**이고 찾은 보드 칸 수와 맞으며 그 칸이 구속 배정으로 이름을 받았다 | 강함 |
| `duplicate` | 같은 프레임에서 이름이 확정된 보드 유닛과 같은 모델인 벤치 유닛 | 보통 |
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
EVIDENCE_KO = {
    EVIDENCE_PURCHASE: "상점 구매", EVIDENCE_TRAITS: "특성 풀이", EVIDENCE_DUPLICATE: "같은 모델(보드)",
    EVIDENCE_LEGACY: "옛 자동 학습", EVIDENCE_LABEL: "확인 라벨", EVIDENCE_MANUAL: "직접 지정",
}

PENDING_CAP = 8           # (챔피언, 성급, 증거)마다 대기 크롭 상한 — 넘으면 **맵 서명이 새로운** 크롭만 받는다
NEAR_DUP = 0.95           # 같은 챔피언의 기존 크롭(대기·승인)과 이보다 닮으면 거의 같은 그림 → 저장하지 않는다
PURCHASE_WINDOW_S = 3.0   # 상점 구매와 벤치 새 칸이 이 시간 안에 함께 보여야 짝을 짓는다(장부 정산 창 2초 + 여유)
TRAITS_MIN_CONF = 0.85    # 특성 풀이 증거로 모을 칸의 최소 이름 신뢰도
DUPLICATE_MIN_CONF = 0.8
PANEL_SURE = 0.75         # (units.PANEL_SURE와 같은 값) 패널 판독이 이보다 애매하면 특성 증거로 모으지 않는다


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

    @property
    def pending(self) -> int:
        return sum(r.pending for r in self.rows)

    def summary(self, names: Mapping[str, str] | None = None) -> str:
        """한국어 요약 한 줄."""
        return (f"승인된 챔피언 {self.covered}/{len(self.rows)} · 검토 대기 {self.pending}장 · "
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
        return Coverage(tuple(ChampionCoverage(c, dict(by.get(c, {})), pend.get(c, 0)) for c in champions))


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


@dataclass
class _NewSlot:
    at: float
    slot: int
    crop: np.ndarray
    star: int | None
    ctx: FrameContext


@dataclass
class UnitCollector:
    """프레임마다 증거가 강한 크롭을 `UnitImageDB` 대기에 넣는다(`UnitNamer.name()`이 부른다).

    구매 증거: 상점 칸이 **챔피언 → 빈 칸**으로 바뀐 것(= 샀다, 인식기가 이 프레임의 상점을 읽었을 때) 또는 app 장부가
    `note_purchase()`로 알려 준 구매 + 같은 창(`PURCHASE_WINDOW_S`) 안에 **벤치에 새로 생긴 칸**(다른 벤치 칸은 그대로, 보드 수
    그대로, 1성). 창 안의 구매가 모두 같은 챔피언이고 새 칸 수와 같을 때만 짝을 짓는다(아니면 모호 → 버린다).
    """

    db: UnitImageDB
    game: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4])
    auto_approve_purchase: bool = False
    window_s: float = PURCHASE_WINDOW_S
    saved: list[CropMeta] = field(default_factory=list)
    _buys: list[tuple[float, str, str]] = field(default_factory=list)
    _new: list[_NewSlot] = field(default_factory=list)
    _last_shop: tuple[str | None, ...] | None = None
    _last_bench: dict[int, int | None] | None = None
    _last_board: int | None = None

    def reset(self) -> None:
        """새 판."""
        self.game = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self._buys.clear()
        self._new.clear()
        self._last_shop = self._last_bench = self._last_board = None

    def note_purchase(self, champion_id: str, at: float | None = None, source: str = "ledger") -> None:
        """app 장부의 구매 이벤트(상점에서 산 챔피언). 같은 창 안에 벤치에 새 칸이 생기면 그 크롭의 이름이 된다."""
        if champion_id:
            self._buys.append((time.time() if at is None else at, champion_id, source))

    # ------------------------------------------------------------------ 한 프레임
    def observe(self, ctx: FrameContext, read: Any, names: Any, board_crops: Sequence[np.ndarray],
                bench_crops: Sequence[np.ndarray], panel_conf: float | None) -> list[CropMeta]:
        """`read` = 이름이 붙은 `BoardRead`, `names` = `units.BoardNames`. 이번 프레임에 저장한 크롭들."""
        before = len(self.saved)
        self._traits(ctx, read, names, board_crops, panel_conf)
        self._duplicates(ctx, read, names, bench_crops)
        self._purchases(ctx, read, bench_crops)
        return self.saved[before:]

    def _save(self, champion: str, crop: np.ndarray, *, evidence: str, star: int | None, score: float,
              ctx: FrameContext, slot: str, approve: bool = False) -> None:
        meta = self.db.add_pending(champion, crop, evidence=evidence, star=star, score=score, game=self.game,
                                   stage=ctx.stage, arena=ctx.arena, frame=ctx.frame, slot=slot, approve=approve)
        if meta is not None:
            self.saved.append(meta)

    def _traits(self, ctx: FrameContext, read: Any, names: Any, crops: Sequence[np.ndarray],
                panel_conf: float | None) -> None:
        # 특성 풀이가 하나 · 칸 수 일치(가려진 유닛 없음) · 패널 판독 확실 · 구속 배정으로 이름 받은 칸
        if names.solutions != 1 or names.missed or names.board_set is None or (panel_conf or 0.0) < PANEL_SURE:
            return
        for u, n, crop in zip(read.board, names.board, crops):
            if n.unit_id and n.source in ("forced", "traits") and n.confidence >= TRAITS_MIN_CONF:
                self._save(n.unit_id, crop, evidence=EVIDENCE_TRAITS, star=u.star, score=n.confidence, ctx=ctx,
                           slot=f"board:{u.hex[0]},{u.hex[1]}" if u.hex else "board:?")

    def _duplicates(self, ctx: FrameContext, read: Any, names: Any, crops: Sequence[np.ndarray]) -> None:
        for u, n, crop in zip(read.bench, names.bench, crops):
            if n.unit_id and n.source == "duplicate" and n.confidence >= DUPLICATE_MIN_CONF:
                self._save(n.unit_id, crop, evidence=EVIDENCE_DUPLICATE, star=u.star, score=n.confidence, ctx=ctx,
                           slot=f"bench:{u.bench_slot}")

    def _purchases(self, ctx: FrameContext, read: Any, crops: Sequence[np.ndarray]) -> None:
        now = ctx.at
        # 1) 상점 칸 챔피언 → 빈 칸 = 구매(상점을 이번 프레임에 읽었을 때만 비교한다)
        if ctx.shop is not None:
            if self._last_shop is not None and len(self._last_shop) == len(ctx.shop):
                for was, cur in zip(self._last_shop, ctx.shop):
                    if was not in (None, "*") and cur is None:
                        self._buys.append((now, was, "shop"))
            self._last_shop = ctx.shop
        # 2) 벤치 새 칸(다른 칸은 그대로, 보드 수 그대로)
        bench = {u.bench_slot: u.star for u in read.bench if u.bench_slot is not None}
        crop_at = {u.bench_slot: c for u, c in zip(read.bench, crops) if u.bench_slot is not None}
        if self._last_bench is not None:
            added = [s for s in bench if s not in self._last_bench]
            gone = [s for s in self._last_bench if s not in bench]
            if added and not gone and self._last_board == len(read.board):
                for s in added:
                    self._new.append(_NewSlot(now, s, crop_at[s], bench[s], ctx))
        self._last_bench, self._last_board = bench, len(read.board)
        # 3) 짝짓기
        self._buys = [b for b in self._buys if now - b[0] <= self.window_s]
        self._new = [n for n in self._new if now - n.at <= self.window_s]
        if not self._buys or not self._new:
            return
        champs = {c for _, c, _ in self._buys}
        # 같은 구매가 상점과 장부 양쪽에서 들어오면 두 번 센다 → 출처별로 센 것 중 큰 쪽을 구매 수로 본다
        per_source: dict[str, int] = {}
        for _, _, src in self._buys:
            per_source[src] = per_source.get(src, 0) + 1
        n_buys = max(per_source.values())
        if len(champs) != 1 or n_buys != len(self._new):
            if len(self._new) > n_buys or len(champs) > 1:
                log.debug("구매-벤치 짝이 모호합니다: 구매 %s / 새 칸 %d", self._buys, len(self._new))
            return
        champ = next(iter(champs))
        for ns in self._new:
            if ns.star not in (None, 1):         # 산 유닛은 1성이다(2성이면 합성이 끼었다)
                continue
            self._save(champ, ns.crop, evidence=EVIDENCE_PURCHASE, star=1, score=0.95, ctx=ns.ctx,
                       slot=f"bench:{ns.slot}", approve=self.auto_approve_purchase)
        self._buys.clear()
        self._new.clear()


__all__ = ["APPROVED", "PENDING", "ChampionCoverage", "Coverage", "CropMeta", "EVIDENCE_KO", "FrameContext",
           "UnitCollector", "UnitImageDB", "arena_signature", "crop_digest", "frame_digest", "open_db", "roster"]
