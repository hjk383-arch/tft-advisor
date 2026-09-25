"""vision 25: 유닛 사진 검토 창(`app.unit_review`) — Qt offscreen. 합성 DB(tmp_path)만 쓴다."""
from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tft_advisor.app import unit_review as R  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision import unit_db as D  # noqa: E402


@pytest.fixture(scope="module")
def static():
    return load_static()


def img(color) -> np.ndarray:
    out = np.full((112, 112, 3), (90, 120, 150), np.uint8)
    cv2.rectangle(out, (40, 20), (72, 100), color, -1)
    return out


@pytest.fixture
def model(static, tmp_path):
    m = R.ReviewModel.from_static(static, tmp_path / "units_screen")
    ak, ka = (static.champion_by_name(n)["apiName"] for n in ("아칼리", "카르마"))
    m.db.add_pending(ak, img((0, 0, 255)), evidence=D.EVIDENCE_PURCHASE, star=1, score=0.95, stage="2-3")
    m.db.add_pending(ak, img((255, 0, 0)), evidence=D.EVIDENCE_TRAITS, star=2, score=0.9)
    m.db.approve(m.db.add_pending(ka, img((0, 255, 0)), evidence=D.EVIDENCE_PURCHASE, star=1))
    m.ids = (ak, ka)
    return m


def test_model_rows_filters_and_search(model, static):
    ak, ka = model.ids
    rows = model.rows("pending")
    assert [(r.champion, r.approved, r.pending) for r in rows] == [(ak, 0, 2)]
    assert any(r.champion == ka and r.approved == 1 for r in model.rows("all"))
    missing = {r.champion for r in model.rows("missing")}
    assert ak in missing and ka not in missing and len(missing) == len(D.roster(static)) - 1
    assert model.rows("all", "카르")[0].champion == ka
    assert model.search("카르마")[0] == (ka, "카르마")
    text = model.describe(model.crops(ak)[0])
    assert "상점 구매" in text and "검토 대기" in text and "스테이지 2-3" in text


def test_window_approve_delete_relabel_star_offscreen(qapp, model, static):
    ak, ka = model.ids
    changed = []
    win = R.make_window(model, on_changed=lambda: changed.append(1), pick=lambda m, q: ka)
    win.show()
    assert "승인된 챔피언 1/" in win.header.text() and "대기 2장" in win.header.text()
    # "대기 전체" 항목 → 2장
    win.champs.setCurrentRow(0)
    assert win.grid.count() == 2
    # 첫 장 성급 3 → 승인
    win.grid.setCurrentRow(0)
    win.grid.item(0).setSelected(True)
    first = win.selected()[0]
    win.do_star(3)
    assert {m.id: m.star for m in model.db.entries(D.PENDING)}[first.id] == 3
    win.grid.clearSelection()
    win.grid.item(0).setSelected(True)
    target = win.selected()[0]
    win.do_approve()
    approved = [m for m in model.db.entries(D.APPROVED) if m.champion == target.champion]
    assert any(m.id == target.id for m in approved)
    # 남은 대기 1장 → 다른 챔피언(카르마)으로
    win.champs.setCurrentRow(0)
    assert win.grid.count() == 1
    win.grid.item(0).setSelected(True)
    win.do_relabel()
    assert [m.champion for m in model.db.entries(D.PENDING)] == [ka]
    # 삭제 → 휴지통
    win.champs.setCurrentRow(0)
    win.grid.item(0).setSelected(True)
    win.do_delete()
    assert model.db.entries(D.PENDING) == []
    assert "대기 0장" in win.header.text()
    win.close()
    assert changed == [1]                      # 닫을 때 한 번 반영(예: unit_namer.reload)


def test_cli_coverage_prints_the_table(static, tmp_path, capsys, monkeypatch):
    root = tmp_path / "units_screen"
    db = D.open_db(static, root)
    ak = static.champion_by_name("아칼리")["apiName"]
    db.approve(db.add_pending(ak, img((0, 0, 255)), evidence=D.EVIDENCE_PURCHASE, star=1))
    assert R.main(["--coverage", "--dir", str(root)]) == 0
    out = capsys.readouterr().out
    assert "승인된 챔피언 1/" in out and "아칼리" in out and "← 없음" in out
