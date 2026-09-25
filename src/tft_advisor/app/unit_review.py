"""유닛 사진 검토 창(PySide6) — 게임 중에 모은 유닛 크롭(검토 대기)을 사람이 승인·삭제·이름 고치기·성급 지정한다.

    python -m tft_advisor review-units              # 검토 창
    python -m tft_advisor review-units --coverage   # 챔피언별 적용 범위 표(콘솔)

DB·결정: `vision.unit_db`, `_workspace/25_unit_image_db.md`. **승인한 사진만** 보드·벤치 이름 판정에 쓰인다.
창은 우리 파일만 다룬다(게임 입력·메모리 접근 없음). 오버레이 트레이/인식 확인 창의 버튼은 app-integrator가
`open_review_window(static, on_changed=recognizer.unit_namer.reload)`로 붙인다(25 보고 §5).

단축키: A 승인 · U 승인 취소 · D/Delete 삭제(휴지통) · R 다른 챔피언으로 · 1/2/3 성급 · 0 성급 모름 ·
Ctrl+A 모두 선택 · F5 새로 고침. 여러 장을 골라 한 번에 적용할 수 있다.
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..vision.unit_db import (
    APPROVED, EVIDENCE_KO, PENDING, Coverage, CropMeta, UnitImageDB, open_db, roster,
)

log = logging.getLogger(__name__)

TITLE = "유닛 사진 검토"
ALL_PENDING = "__pending__"          # 챔피언 목록 맨 위 "대기 전체" 항목
ICON = 140
FILTERS = (("pending", "대기 있는 챔피언"), ("all", "전체 챔피언"), ("missing", "사진 없는 챔피언"))
VIEWS = (("pending", "대기"), ("approved", "승인"), ("all", "전체"))


# ---------------------------------------------------------------------------
# Qt 없는 모델(테스트 가능)
# ---------------------------------------------------------------------------


@dataclass
class ChampionRow:
    champion: str
    name: str
    approved: int
    pending: int

    @property
    def label(self) -> str:
        tail = f"승인 {self.approved} · 대기 {self.pending}" if self.approved or self.pending else "사진 없음"
        return f"{self.name}  —  {tail}"


class ReviewModel:
    """검토 창의 상태와 동작(Qt와 무관). 창은 이것을 그리기만 한다."""

    def __init__(self, db: UnitImageDB, names: dict[str, str], champions: Sequence[str]) -> None:
        self.db = db
        self.names = names
        self.champions = list(champions)
        self.changed = 0

    @classmethod
    def from_static(cls, static: Any, directory: str | Path | None = None) -> ReviewModel:
        names = {c["apiName"]: c.get("name_ko") or c["apiName"] for c in static._load("champions")}
        db = open_db(static, directory)
        db.migrate_legacy()
        return cls(db, names, roster(static))

    def name(self, champion: str) -> str:
        return self.names.get(champion, champion)

    def coverage(self) -> Coverage:
        return self.db.coverage(self.champions)

    def rows(self, filt: str = "pending", query: str = "") -> list[ChampionRow]:
        cov = {r.champion: r for r in self.coverage().rows}
        extra = {m.champion for m in self.db.entries()} - set(cov)       # 목록 밖 챔피언 폴더(특수 유닛 등)
        out: list[ChampionRow] = []
        for c in [*self.champions, *sorted(extra)]:
            r = cov.get(c)
            approved = r.approved_total if r else len(self.db.entries(APPROVED, c))
            pending = r.pending if r else len(self.db.entries(PENDING, c))
            row = ChampionRow(c, self.name(c), approved, pending)
            if filt == "pending" and not pending:
                continue
            if filt == "missing" and approved:
                continue
            if query and query.lower() not in row.name.lower() and query.lower() not in c.lower():
                continue
            out.append(row)
        return sorted(out, key=lambda r: (-r.pending, r.name))

    def crops(self, champion: str | None, view: str = "pending") -> list[CropMeta]:
        status = None if view == "all" else (APPROVED if view == "approved" else PENDING)
        if champion == ALL_PENDING:
            return self.db.entries(PENDING)
        return self.db.entries(status, champion)

    def search(self, query: str) -> list[tuple[str, str]]:
        """챔피언 고르기: (apiName, 한국어 이름) — 이름·ID에 검색어가 들어간 것, 한국어 이름 순."""
        q = query.strip().lower()
        cands = sorted(self.champions, key=self.name)
        return [(c, self.name(c)) for c in cands if not q or q in self.name(c).lower() or q in c.lower()]

    # 동작 — 모두 새 메타데이터 목록을 돌려준다
    def approve(self, metas: Sequence[CropMeta]) -> list[CropMeta]:
        out = [self.db.approve(m) for m in metas if m.status != APPROVED]
        self.changed += len(out)
        return out

    def unapprove(self, metas: Sequence[CropMeta]) -> list[CropMeta]:
        out = [self.db.unapprove(m) for m in metas if m.status == APPROVED]
        self.changed += len(out)
        return out

    def delete(self, metas: Sequence[CropMeta]) -> int:
        for m in metas:
            self.db.delete(m)
        self.changed += len(metas)
        return len(metas)

    def relabel(self, metas: Sequence[CropMeta], champion: str) -> list[CropMeta]:
        out = [self.db.relabel(m, champion) for m in metas if m.champion != champion]
        self.changed += len(out)
        return out

    def set_star(self, metas: Sequence[CropMeta], star: int | None) -> list[CropMeta]:
        out = [self.db.set_star(m, star) for m in metas]
        self.changed += len(out)
        return out

    def describe(self, meta: CropMeta) -> str:
        """메타데이터 설명(한국어 여러 줄)."""
        ev = EVIDENCE_KO.get(meta.evidence, meta.evidence)
        score = f" {meta.score:.2f}" if meta.score is not None else ""
        lines = [
            f"{self.name(meta.champion)} ({meta.champion})",
            f"상태: {'승인' if meta.status == APPROVED else '검토 대기'} · 성급: {'★' * meta.star if meta.star else '모름'}",
            f"근거: {ev}{score}",
        ]
        where = " · ".join(x for x in (meta.stage and f"스테이지 {meta.stage}", meta.slot and f"칸 {meta.slot}",
                                       meta.arena and f"맵 {meta.arena}") if x)
        if where:
            lines.append(where)
        when = " · ".join(x for x in (meta.at, meta.game and f"판 {meta.game}", meta.frame and f"프레임 {meta.frame}")
                          if x)
        if when:
            lines.append(when)
        if meta.note:
            lines.append(f"메모: {meta.note}")
        lines.append(f"파일: {meta.path}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 창
# ---------------------------------------------------------------------------


def _pixmap(img: Any, size: int = ICON) -> Any:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPixmap

    import numpy as np

    rgb = np.ascontiguousarray(img[..., :3][..., ::-1])
    h, w = rgb.shape[:2]
    qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)


def build_picker(model: ReviewModel, parent: Any = None, initial: str = "") -> Any:
    """챔피언 고르기 대화 상자(검색 + 목록). `exec()` 뒤 `.chosen`에 apiName(취소면 None)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

    dlg = QDialog(parent)
    dlg.setWindowTitle("어느 챔피언인가요?")
    dlg.chosen = None
    lay = QVBoxLayout(dlg)
    box = QLineEdit(dlg)
    box.setPlaceholderText("이름 검색 (예: 카르마)")
    box.setText(initial)
    lst = QListWidget(dlg)
    lay.addWidget(box)
    lay.addWidget(lst)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
    lay.addWidget(buttons)

    def fill() -> None:
        lst.clear()
        for cid, name in model.search(box.text()):
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, cid)
            lst.addItem(it)
        if lst.count():
            lst.setCurrentRow(0)

    def accept() -> None:
        it = lst.currentItem()
        dlg.chosen = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        dlg.accept()

    box.textChanged.connect(lambda _t: fill())
    box.returnPressed.connect(accept)
    lst.itemDoubleClicked.connect(lambda _it: accept())
    buttons.accepted.connect(accept)
    buttons.rejected.connect(dlg.reject)
    fill()
    dlg.resize(320, 460)
    return dlg


def make_window(model: ReviewModel, *, on_changed: Callable[[], Any] | None = None,
                pick: Callable[[ReviewModel, str], str | None] | None = None) -> Any:
    """검토 창(QWidget)을 만든다. `pick(model, 초기 검색어)` = 챔피언 고르기(테스트는 대화 상자 대신 함수를 준다).
    `on_changed`: 창을 닫을 때 바뀐 것이 있으면 부른다(예: `recognizer.unit_namer.reload`)."""
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QIcon, QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
        QPushButton, QSplitter, QVBoxLayout, QWidget,
    )

    class UnitReviewWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.model = model
            self.setWindowTitle(f"TFT Advisor — {TITLE}")
            self.resize(1100, 720)
            root = QVBoxLayout(self)
            self.header = QLabel(self)
            self.header.setWordWrap(True)
            root.addWidget(self.header)
            split = QSplitter(Qt.Orientation.Horizontal, self)
            root.addWidget(split, 1)
            # 왼쪽: 챔피언 목록
            left = QWidget(split)
            ll = QVBoxLayout(left)
            self.filter = QComboBox(left)
            for key, text in FILTERS:
                self.filter.addItem(text, key)
            self.query = QLineEdit(left)
            self.query.setPlaceholderText("챔피언 검색")
            self.champs = QListWidget(left)
            ll.addWidget(self.filter)
            ll.addWidget(self.query)
            ll.addWidget(self.champs, 1)
            # 오른쪽: 사진 격자 + 설명 + 버튼
            right = QWidget(split)
            rl = QVBoxLayout(right)
            top = QHBoxLayout()
            self.view = QComboBox(right)
            for key, text in VIEWS:
                self.view.addItem(text, key)
            top.addWidget(QLabel("보기:", right))
            top.addWidget(self.view)
            top.addStretch(1)
            rl.addLayout(top)
            self.grid = QListWidget(right)
            self.grid.setViewMode(QListView.ViewMode.IconMode)
            self.grid.setIconSize(QSize(ICON, ICON))
            self.grid.setResizeMode(QListView.ResizeMode.Adjust)
            self.grid.setMovement(QListView.Movement.Static)
            self.grid.setSpacing(6)
            self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            rl.addWidget(self.grid, 1)
            self.detail = QLabel(right)
            self.detail.setWordWrap(True)
            self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            rl.addWidget(self.detail)
            bl = QHBoxLayout()
            self.buttons: dict[str, QPushButton] = {}
            for key, text in (("approve", "승인 (A)"), ("unapprove", "승인 취소 (U)"), ("delete", "삭제 (D)"),
                              ("relabel", "다른 챔피언 (R)"), ("star1", "★1 (1)"), ("star2", "★2 (2)"),
                              ("star3", "★3 (3)"), ("star0", "성급 모름 (0)")):
                b = QPushButton(text, right)
                self.buttons[key] = b
                bl.addWidget(b)
            rl.addLayout(bl)
            split.setSizes([300, 800])

            self.filter.currentIndexChanged.connect(lambda _i: self.refresh_champions())
            self.query.textChanged.connect(lambda _t: self.refresh_champions())
            self.view.currentIndexChanged.connect(lambda _i: self.refresh_grid())
            self.champs.currentItemChanged.connect(lambda *_: self.refresh_grid())
            self.grid.itemSelectionChanged.connect(self.show_detail)
            self.buttons["approve"].clicked.connect(self.do_approve)
            self.buttons["unapprove"].clicked.connect(self.do_unapprove)
            self.buttons["delete"].clicked.connect(self.do_delete)
            self.buttons["relabel"].clicked.connect(self.do_relabel)
            for n in (1, 2, 3, 0):
                self.buttons[f"star{n}"].clicked.connect(lambda _c=False, n=n: self.do_star(n or None))
            for seq, fn in (("A", self.do_approve), ("U", self.do_unapprove), ("D", self.do_delete),
                            ("Delete", self.do_delete), ("R", self.do_relabel), ("F5", self.refresh_all),
                            ("1", lambda: self.do_star(1)), ("2", lambda: self.do_star(2)),
                            ("3", lambda: self.do_star(3)), ("0", lambda: self.do_star(None))):
                QShortcut(QKeySequence(seq), self, activated=fn)
            self.refresh_all()

        # ---------------------------------------------------------------- 그리기
        def refresh_all(self) -> None:
            cov = self.model.coverage()
            missing = [self.model.name(c) for c in cov.missing]
            more = f" 외 {len(missing) - 12}명" if len(missing) > 12 else ""
            self.header.setText(f"<b>{TITLE}</b> — {cov.summary()}<br>"
                                f"<span style='color:#9aa4b2'>사진 없는 챔피언: {', '.join(missing[:12])}{more}</span>")
            self.refresh_champions()

        def refresh_champions(self) -> None:
            keep = self.current_champion()
            self.champs.blockSignals(True)
            self.champs.clear()
            pending_total = len(self.model.db.entries(PENDING))
            it = QListWidgetItem(f"◆ 대기 전체  —  {pending_total}장")
            it.setData(Qt.ItemDataRole.UserRole, ALL_PENDING)
            self.champs.addItem(it)
            for row in self.model.rows(self.filter.currentData(), self.query.text()):
                it = QListWidgetItem(row.label)
                it.setData(Qt.ItemDataRole.UserRole, row.champion)
                if not row.approved:
                    it.setForeground(Qt.GlobalColor.darkYellow)
                self.champs.addItem(it)
            self.champs.blockSignals(False)
            idx = next((i for i in range(self.champs.count())
                        if self.champs.item(i).data(Qt.ItemDataRole.UserRole) == keep), 0)
            self.champs.setCurrentRow(idx)
            self.refresh_grid()

        def current_champion(self) -> str | None:
            it = self.champs.currentItem()
            return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

        def refresh_grid(self) -> None:
            self.grid.clear()
            champ = self.current_champion()
            if champ is None:
                return
            for meta in self.model.crops(champ, self.view.currentData()):
                try:
                    icon = QIcon(_pixmap(self.model.db.load_image(meta)))
                except Exception:
                    log.warning("사진을 읽지 못했습니다: %s", meta.path)
                    continue
                star = "★" * meta.star if meta.star else "★?"
                mark = "✔" if meta.status == APPROVED else "…"
                text = f"{mark} {self.model.name(meta.champion)} {star}\n{EVIDENCE_KO.get(meta.evidence, meta.evidence)}"
                it = QListWidgetItem(icon, text)
                it.setData(Qt.ItemDataRole.UserRole, meta)
                it.setToolTip(self.model.describe(meta))
                self.grid.addItem(it)
            self.show_detail()

        def selected(self) -> list[CropMeta]:
            return [it.data(Qt.ItemDataRole.UserRole) for it in self.grid.selectedItems()]

        def show_detail(self) -> None:
            sel = self.selected()
            if not sel:
                self.detail.setText("사진을 고르면 근거가 보입니다. 여러 장을 골라 한 번에 승인·삭제할 수 있습니다.")
            elif len(sel) == 1:
                self.detail.setText(self.model.describe(sel[0]))
            else:
                self.detail.setText(f"{len(sel)}장 선택")

        # ---------------------------------------------------------------- 동작
        def _after(self) -> None:
            row = self.grid.currentRow()
            self.refresh_all()
            if self.grid.count():
                self.grid.setCurrentRow(min(max(0, row), self.grid.count() - 1))

        def do_approve(self) -> None:
            if self.selected():
                self.model.approve(self.selected())
                self._after()

        def do_unapprove(self) -> None:
            if self.selected():
                self.model.unapprove(self.selected())
                self._after()

        def do_delete(self) -> None:
            if self.selected():
                self.model.delete(self.selected())
                self._after()

        def do_relabel(self) -> None:
            sel = self.selected()
            if not sel:
                return
            champ = (pick or _dialog_pick)(self.model, "")
            if champ:
                self.model.relabel(sel, champ)
                self._after()

        def do_star(self, star: int | None) -> None:
            if self.selected():
                self.model.set_star(self.selected(), star)
                self._after()

        def closeEvent(self, event: Any) -> None:     # noqa: N802 (Qt)
            if self.model.changed and on_changed is not None:
                try:
                    on_changed()
                except Exception:
                    log.exception("검토 결과 반영 실패")
            super().closeEvent(event)

    def _dialog_pick(m: ReviewModel, initial: str) -> str | None:
        dlg = build_picker(m, None, initial)
        dlg.exec()
        return dlg.chosen

    return UnitReviewWindow()


def open_review_window(static: Any, directory: str | Path | None = None, *,
                       on_changed: Callable[[], Any] | None = None) -> Any:
    """실행 중인 Qt 앱에서 검토 창을 띄운다(오버레이 트레이·인식 확인 창 버튼용). 창 객체를 돌려준다(참조를 들고 있을 것)."""
    win = make_window(ReviewModel.from_static(static, directory), on_changed=on_changed)
    win.show()
    return win


def main(argv: Sequence[str] | None = None) -> int:
    from ..app.report import ensure_utf8_stdio
    from ..static_data import load_static

    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(prog="tft_advisor review-units", description="유닛 사진(보드·벤치 크롭) 검토")
    ap.add_argument("--coverage", action="store_true", help="챔피언별 승인·대기 사진 수를 콘솔에 출력하고 끝냅니다")
    ap.add_argument("--dir", type=Path, default=None, help="유닛 사진 폴더(기본 data/templates/{set}/units_screen)")
    ap.add_argument("--set", type=int, default=None, help="세트 번호(기본: 설정)")
    args = ap.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    static = load_static(args.set) if args.set is not None else load_static()
    model = ReviewModel.from_static(static, args.dir)
    if args.coverage:
        print(model.coverage().table(model.names))
        return 0
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    win = make_window(model)
    win.show()
    return int(app.exec())


if __name__ == "__main__":                    # pragma: no cover
    raise SystemExit(main())
