"""이름 미상 벤치 크롭 수집(`UnitCollector.observe_unknown`) · 이름 주기(`UnitImageDB.label_unknown`) · 검토 창(Qt offscreen).

사용자 결정(2026-09-25): "벤치에 이름 미상이면 그때 스샷 찍어서" → AI 호출 없이 크롭을 `_pending/_unknown/`에 모으고, 판이 끝난 뒤
검토 창에서 사람이 이름을 준다(그 이름이 확인 → 승인 폴더, 라이브러리가 된다).
"""
from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tft_advisor.app import unit_review as R  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision import unit_db as D  # noqa: E402
from tft_advisor.vision import units as U  # noqa: E402
from tft_advisor.vision.board import BoardRead, UnitSlot  # noqa: E402

AK, KA, XA, OR = "DA_18_Akali_AD", "DA_Karma18", "DA_18_Xayah", "DA_18_Ornn"


@pytest.fixture(scope="module")
def static():
    return load_static()


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def img(color, shape="rect") -> np.ndarray:
    color = tuple(int(c * 0.75) for c in color)                 # 원색 255는 "강한 빛" 품질 거부에 걸린다
    out = np.full((112, 112, 3), (90, 120, 150), np.uint8)
    if shape == "rect":
        cv2.rectangle(out, (40, 20), (72, 100), color, -1)
    else:
        cv2.circle(out, (56, 60), 30, color, -1)
    return out


def db_at(tmp_path, static):
    return D.UnitImageDB(tmp_path / "units_screen", valid=lambda c: static.get("champions", c) is not None)


def bench(*slots, star=1, names=None):
    names = names or {}
    return BoardRead(bench=tuple(UnitSlot(star=star, bench_slot=s, unit_id=names.get(s)) for s in slots))


def step(col, t, read, crops, **kw):
    return col.observe_unknown(D.FrameContext(at=t, stage="2-5", arena="0a080a", **kw.pop("ctx", {})), read, crops,
                               **kw)


def test_unknown_bench_unit_is_saved_after_two_frames_with_suggestions(static, tmp_path):
    col = D.UnitCollector(db_at(tmp_path, static))
    a = img((200, 40, 40))
    shop = (XA, None, KA, "*", None)
    assert step(col, 1.0, bench(0), [a], ctx={"shop": shop}) == []              # 한 프레임은 아직
    (m,) = step(col, 1.4, bench(0), [a], owned=[OR, AK], unplaced=[AK])
    assert m.champion == D.UNKNOWN_CHAMPION and m.status == D.PENDING and m.evidence == D.EVIDENCE_UNKNOWN
    assert m.slot == "bench:0" and m.star == 1 and m.stage == "2-5" and m.arena == "0a080a"
    assert m.suggestions == [OR, AK, XA, KA]                                     # 장부 보유 > 자리 미상 > 상점
    assert m.path.parent == col.db.root / D.PENDING_DIR / D.UNKNOWN_CHAMPION
    (again,) = col.db.unknown_entries()
    assert again.suggestions == [OR, AK, XA, KA]
    assert col.db.entries() == []                                               # 챔피언 폴더·라이브러리에는 없다
    assert len(U.UnitLibrary.load(col.db.root)) == 0
    assert col.db.coverage([AK]).unknown == 1 and "이름 미상 1장" in col.db.coverage([AK]).summary()


def test_named_or_flickering_slots_are_not_saved(static, tmp_path):
    col = D.UnitCollector(db_at(tmp_path, static))
    a = img((200, 40, 40))
    step(col, 1.0, bench(0, names={0: AK}), [a])
    assert step(col, 1.4, bench(0, names={0: AK}), [a]) == []                   # 이름 있음
    step(col, 2.0, bench(1), [a])
    step(col, 2.4, bench(), [])                                                  # 끊김
    assert step(col, 2.8, bench(1), [a]) == []                                  # 다시 1프레임째


def test_same_unit_is_saved_at_most_twice_and_the_game_is_capped(static, tmp_path):
    col = D.UnitCollector(db_at(tmp_path, static))
    a, a_later = img((200, 40, 40)), img((198, 42, 40))
    step(col, 1.0, bench(0), [a])
    assert len(step(col, 1.4, bench(0), [a])) == 1
    for t in (2.0, 5.0, 10.0):
        assert step(col, t, bench(0), [a_later]) == []                           # 같은 유닛, 20초 안
    step(col, 30.0, bench(3), [a_later])                                         # 옮긴 뒤(새 칸 1프레임째)
    assert len(step(col, 30.4, bench(3), [a_later])) == 1                       # 한참 뒤 두 번째 크롭
    assert step(col, 60.0, bench(3), [a]) == []                                 # 세 번째는 없다
    # 판 상한
    col2 = D.UnitCollector(db_at(tmp_path / "b", static), unknown_cap=2)
    looks = [img((200, 40, 40)), img((40, 200, 40), "circle"), img((40, 40, 200))]
    for t in (1.0, 1.4):
        step(col2, t, bench(0, 1, 2), looks)
    assert len(col2.db.unknown_entries()) == 2
    col2.reset()                                                                  # 새 판이면 다시 센다
    for t in (5.0, 5.4):
        step(col2, t, bench(2), [looks[2]])
    assert len(col2.db.unknown_entries()) == 3


def test_quality_and_tactician_rules_apply(static, tmp_path):
    col = D.UnitCollector(db_at(tmp_path, static))
    glow = np.full((112, 112, 3), (90, 120, 150), np.uint8)
    cv2.circle(glow, (56, 60), 30, (0, 230, 255), -1)
    for t in (1.0, 1.4):
        assert step(col, t, bench(0), [glow]) == []
    a = img((200, 40, 40))
    for t in (2.0, 2.4):
        assert step(col, t, bench(1), [a], ctx={"tactician": frozenset({1})}) == []


def test_a_later_name_in_the_same_game_becomes_the_first_suggestion(static, tmp_path):
    col = D.UnitCollector(db_at(tmp_path, static))
    a = img((200, 40, 40))
    step(col, 1.0, bench(0), [a])
    step(col, 1.4, bench(0), [a], owned=[OR])
    step(col, 9.0, bench(0, names={0: AK}), [a])                                 # 나중에 구매 추적·사진 비교가 이름을 붙였다
    (m,) = col.db.unknown_entries()
    assert m.suggestions[0] == AK and m.status == D.PENDING and AK in (m.note or "")


def test_review_window_labels_unknown_crops_into_approved_folders(qapp, static, tmp_path):
    model = R.ReviewModel.from_static(static, tmp_path / "units_screen")
    m1 = model.db.add_unknown(img((200, 40, 40)), star=1, slot="bench:0", suggestions=[KA, AK])
    model.db.add_unknown(img((40, 40, 200)), star=2, slot="bench:3", suggestions=[OR])
    win = R.make_window(model, pick=lambda m, q: XA)
    win.show()
    assert "이름 미상 2장" in win.header.text()
    assert win.current_champion() == R.UNKNOWN_GROUP and win.grid.count() == 2
    win.grid.item(0).setSelected(True)
    assert "1 카르마" in win.detail.text() and win.pick_buttons[0].text() == "1 카르마"
    assert model.approve(win.selected()) == []                                  # 이름 없이 승인 불가
    win.do_number(2)                                                             # 숫자 키 2 = 두 번째 후보(아칼리)
    (got,) = model.db.entries(D.APPROVED, AK)
    assert got.evidence == D.EVIDENCE_USER and got.status == D.APPROVED and got.star == 1
    assert not m1.path.exists() and len(model.db.unknown_entries()) == 1
    assert U.UnitLibrary.load(model.db.root).ids == {AK}                        # 라이브러리가 된다
    win.champs.setCurrentRow(0)
    win.grid.item(0).setSelected(True)
    win.do_relabel()                                                             # R(검색) → 자야
    assert [m.champion for m in model.db.entries(D.APPROVED)] == [AK, XA] or \
        sorted(m.champion for m in model.db.entries(D.APPROVED)) == sorted([AK, XA])
    assert model.db.unknown_entries() == []
    win.close()
