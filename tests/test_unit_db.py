"""vision 25 (2026-09-23): 유닛 사진 DB(`vision.unit_db`) — 대기/승인, 구매 증거, 중복 제거, 이름 고치기, 적용 범위.

전부 합성 입력(tmp_path)이다. 실제 `data/templates/.../units_screen`에는 쓰지 않는다.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision import unit_db as D  # noqa: E402
from tft_advisor.vision import units as U  # noqa: E402
from tft_advisor.vision.board import BoardRead, UnitSlot  # noqa: E402


@pytest.fixture(scope="module")
def static():
    return load_static()


@pytest.fixture(scope="module")
def table(static):
    return U.TraitTable.from_static(static)


def cid(static, name: str) -> str:
    return static.champion_by_name(name)["apiName"]


def img(color, bg=(90, 120, 150), w=32) -> np.ndarray:
    out = np.full((112, 112, 3), bg, np.uint8)
    cv2.rectangle(out, (56 - w // 2, 20), (56 + w // 2, 100), color, -1)
    return out


def db_at(tmp_path, static=None, **kw) -> D.UnitImageDB:
    valid = (lambda c: static.get("champions", c) is not None) if static is not None else None
    return D.UnitImageDB(tmp_path / "units_screen", valid=valid, **kw)


# ---------------------------------------------------------------- 대기 vs 승인
def test_pending_crops_never_name_a_unit_by_default(static, tmp_path):
    """대기 크롭은 사람이 승인하기 전에는 라이브러리 표본이 아니다(기본 가중치 0)."""
    red = img((0, 0, 255))
    db = db_at(tmp_path, static)
    ak = cid(static, "아칼리")
    meta = db.add_pending(ak, red, evidence=D.EVIDENCE_PURCHASE, star=1, score=0.95)
    assert meta is not None and meta.status == D.PENDING and meta.path.parent.parent.name == D.PENDING_DIR
    lib = U.UnitLibrary.load(db.root)
    assert len(lib) == 0 and lib.scores(U.descriptor(red)) == {}
    res = U.name_units([], [U.descriptor(red)], lib, None, None)
    assert res.bench[0].unit_id is None
    # 설정으로 낮은 가중치를 주면 순위에는 들지만(0.5배) 엄격 임계는 못 넘는다
    weak = U.UnitLibrary.load(db.root, pending_weight=0.5)
    assert weak.scores(U.descriptor(red))[ak] == pytest.approx(0.5)
    assert U.name_units([], [U.descriptor(red)], weak, None, None).bench[0].unit_id is None
    # 승인하면 표본이 된다
    db.approve(meta)
    lib = U.UnitLibrary.load(db.root)
    assert lib.ids == {ak}
    assert U.name_units([], [U.descriptor(red)], lib, None, None).bench[0].unit_id == ak


def test_label_crops_count_as_approved_and_trash_is_ignored(static, tmp_path):
    db = db_at(tmp_path, static)
    ak = cid(static, "아칼리")
    d = db.root / ak
    d.mkdir(parents=True)
    ok, buf = cv2.imencode(".png", img((0, 0, 255)))
    buf.tofile(str(d / "label_abc.png"))
    (e,) = db.entries()
    assert e.status == D.APPROVED and e.evidence == D.EVIDENCE_LABEL and e.star is None
    db.delete(e)
    assert db.entries() == [] and len(list((db.root / D.TRASH_DIR).rglob("*.png"))) == 1
    assert len(U.UnitLibrary.load(db.root)) == 0


def test_legacy_auto_crops_are_moved_to_pending(static, tmp_path):
    db = db_at(tmp_path, static)
    ko = cid(static, "코그모")
    d = db.root / ko
    d.mkdir(parents=True)
    ok, buf = cv2.imencode(".png", img((0, 0, 255)))
    buf.tofile(str(d / "auto_111.png"))
    buf.tofile(str(d / "label_222.png"))
    assert db.migrate_legacy() == 1
    st = {(m.id, m.status, m.evidence) for m in db.entries()}
    assert st == {("label_222", D.APPROVED, D.EVIDENCE_LABEL), ("auto_111", D.PENDING, D.EVIDENCE_LEGACY)}
    assert db.migrate_legacy() == 0


# ---------------------------------------------------------------- 중복 제거 · 상한
def test_dedupe_exact_and_near_identical_and_cap_by_arena(static, tmp_path):
    db = db_at(tmp_path, static, pending_cap=2)
    ak = cid(static, "아칼리")
    red = img((0, 0, 255))
    assert db.add_pending(ak, red, evidence="purchase", star=1, arena="a") is not None
    assert db.add_pending(ak, red, evidence="purchase", star=1, arena="a") is None          # 같은 그림
    nudged = red.copy()
    nudged[0, 0] = (91, 120, 150)                                                          # 거의 같은 그림
    assert db.add_pending(ak, nudged, evidence="purchase", star=1, arena="a") is None
    assert db.add_pending(ak, img((255, 0, 0)), evidence="purchase", star=1, arena="a") is not None
    # 상한(2) 도달: 같은 맵이면 거절, 새 맵이면 받는다
    assert db.add_pending(ak, img((0, 255, 0)), evidence="purchase", star=1, arena="a") is None
    assert db.add_pending(ak, img((0, 255, 0)), evidence="purchase", star=1, arena="b") is not None
    # 다른 증거·성급은 따로 센다
    assert db.add_pending(ak, img((0, 255, 255)), evidence="traits", star=1, arena="a") is not None
    assert db.add_pending("NOT_A_CHAMPION", img((9, 9, 9)), evidence="purchase") is None


# ---------------------------------------------------------------- 이름 고치기 · 성급
def test_relabel_moves_file_and_metadata(static, tmp_path):
    db = db_at(tmp_path, static)
    ak, ka = cid(static, "아칼리"), cid(static, "카르마")
    meta = db.add_pending(ak, img((0, 0, 255)), evidence=D.EVIDENCE_PURCHASE, star=1, score=0.9, stage="2-3",
                          slot="bench:2")
    new = db.relabel(meta, ka)
    assert not meta.path.exists() and not meta.path.with_suffix(".json").exists()
    assert new.path.parent.name == ka and new.path.exists()
    raw = json.loads(new.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert raw["champion"] == ka and raw["stage"] == "2-3" and raw["slot"] == "bench:2" and "아칼리" not in raw["note"]
    assert ak in raw["note"] and raw["status"] == D.PENDING
    new = db.set_star(new, 2)
    (again,) = db.entries(champion=ka)
    assert again.star == 2 and again.status == D.PENDING
    with pytest.raises(ValueError):
        db.relabel(again, "NOT_A_CHAMPION")
    approved = db.approve(again)
    assert approved.path.parent == db.root / ka and approved.reviewed_at


# ---------------------------------------------------------------- 적용 범위
def test_coverage_counts_approved_per_star_pending_and_missing(static, tmp_path):
    db = db_at(tmp_path, static)
    ak, ka, yo = cid(static, "아칼리"), cid(static, "카르마"), cid(static, "요릭")
    db.approve(db.add_pending(ak, img((0, 0, 255)), evidence="purchase", star=1))
    db.approve(db.add_pending(ak, img((255, 0, 0)), evidence="purchase", star=2))
    db.approve(db.add_pending(ak, img((0, 255, 0)), evidence="purchase", star=None))
    db.add_pending(ka, img((0, 255, 255)), evidence="purchase", star=1)
    cov = db.coverage([ak, ka, yo])
    rows = {r.champion: r for r in cov.rows}
    assert rows[ak].approved == {1: 1, 2: 1, None: 1} and rows[ak].pending == 0
    assert rows[ka].approved_total == 0 and rows[ka].pending == 1
    assert cov.covered == 1 and cov.pending == 1 and cov.missing == [ka, yo]
    assert "1/3" in cov.summary() and "대기 1장" in cov.summary()
    # 실제 로스터: 특성 없는 유닛(훈련 봇·골렘 등)은 빠진다
    names = D.roster(static)
    assert ak in names and all(static.get("champions", c)["traits"] for c in names)
    assert not any("TrainingDummy" in c or "Golem" in c for c in names)


# ---------------------------------------------------------------- 실시간 수집기
def _bench(*slots) -> tuple[UnitSlot, ...]:
    return tuple(UnitSlot(star=1, bench_slot=s) for s in slots)


def _names(n_board=0, n_bench=0):
    none = U.SlotName(None, 0.0, "none")
    return U.BoardNames(board=(none,) * n_board, bench=(none,) * n_bench)


def test_purchase_from_a_ledger_event_labels_the_new_bench_slot(static, tmp_path):
    db = db_at(tmp_path, static)
    col = D.UnitCollector(db)
    ka = cid(static, "카르마")
    crops = {0: img((0, 0, 255)), 1: img((255, 0, 0)), 2: img((0, 200, 200))}
    r0 = BoardRead(bench=_bench(0, 1))
    col.observe(D.FrameContext(at=10.0), r0, _names(0, 2), [], [crops[0], crops[1]], None)
    col.note_purchase(ka, at=10.5)                                  # 가짜 장부 구매 이벤트
    r1 = BoardRead(bench=_bench(0, 1, 2))
    saved = col.observe(D.FrameContext(at=11.0, stage="2-3"), r1, _names(0, 3), [],
                        [crops[0], crops[1], crops[2]], None)
    assert [(m.champion, m.evidence, m.slot, m.star, m.status) for m in saved] == \
        [(ka, D.EVIDENCE_PURCHASE, "bench:2", 1, D.PENDING)]
    assert np.array_equal(db.load_image(saved[0]), crops[2])


def test_purchase_detected_from_the_shop_slot_turning_empty(static, tmp_path):
    col = D.UnitCollector(db_at(tmp_path, static), auto_approve_purchase=True)
    ka, ak = cid(static, "카르마"), cid(static, "아칼리")
    a, b = img((0, 0, 255)), img((255, 0, 0))
    col.observe(D.FrameContext(at=1.0, shop=(ka, ak, None, "*", ak)), BoardRead(bench=_bench(0)), _names(0, 1),
                [], [a], None)
    saved = col.observe(D.FrameContext(at=1.4, shop=(None, ak, None, "*", ak)), BoardRead(bench=_bench(0, 3)),
                        _names(0, 2), [], [a, b], None)
    assert [(m.champion, m.slot, m.status) for m in saved] == [(ka, "bench:3", D.APPROVED)]    # 설정: 구매 자동 승인


@pytest.mark.parametrize("case", ["too_late", "unit_moved", "two_champions", "board_changed", "two_star"])
def test_purchase_is_not_labelled_when_ambiguous(static, tmp_path, case):
    col = D.UnitCollector(db_at(tmp_path, static))
    ka, ak = cid(static, "카르마"), cid(static, "아칼리")
    a, b, c = img((0, 0, 255)), img((255, 0, 0)), img((0, 255, 0))
    board0 = (UnitSlot(star=1, hex=(0, 0)),)
    col.observe(D.FrameContext(at=1.0), BoardRead(board=board0, bench=_bench(0, 1)), _names(1, 2), [a], [a, b], None)
    board1, bench1, at = board0, _bench(0, 1, 2), 1.5
    if case == "too_late":
        col.note_purchase(ka, at=1.0)
        at = 1.0 + D.PURCHASE_WINDOW_S + 1
    elif case == "unit_moved":
        col.note_purchase(ka, at=1.2)
        bench1 = _bench(0, 2)                          # 1번 칸 유닛을 2번으로 옮겼다(새 칸이 아니다)
    elif case == "two_champions":
        col.note_purchase(ka, at=1.2)
        col.note_purchase(ak, at=1.3)
    elif case == "board_changed":
        col.note_purchase(ka, at=1.2)
        board1 = ()                                    # 보드 유닛이 벤치로 내려왔다
    elif case == "two_star":
        col.note_purchase(ka, at=1.2)
        bench1 = (*_bench(0, 1), UnitSlot(star=2, bench_slot=2))
    saved = col.observe(D.FrameContext(at=at), BoardRead(board=board1, bench=bench1),
                        _names(len(board1), len(bench1)), [a] * len(board1), [a, b, c][:len(bench1)], None)
    assert saved == [] and list(tmp_path.rglob("*.png")) == []


def test_traits_evidence_needs_unique_solution_matching_slot_count(static, table, tmp_path):
    col = D.UnitCollector(db_at(tmp_path, static))
    yo, orn = cid(static, "요릭"), cid(static, "오른")
    red, blue = img((0, 0, 255)), img((255, 0, 0))
    lib = U.library_from([(yo, red), (orn, blue)])
    counts = {static.trait_by_name(k)["apiName"]: v for k, v in
              {"개화": 1, "전쟁기계": 1, "소환사": 1, "나무정령": 1, "엄호대": 1}.items()}
    p = U.TraitPanel(counts, confidence=0.9)
    board = (UnitSlot(star=1, hex=(0, 0)),)
    # 칸 1개 < 풀이 2명(가려진 유닛) → 이름은 붙어도 증거로 모으지 않는다
    res = U.name_units([U.descriptor(red)], [], lib, p, table)
    assert res.missed == 1 and res.board[0].unit_id == yo
    assert col.observe(D.FrameContext(at=1.0), BoardRead(board=board), res, [red], [], 0.9) == []
    # 칸 수가 맞으면 모은다
    board2 = (*board, UnitSlot(star=2, hex=(0, 1)))
    res = U.name_units([U.descriptor(red), U.descriptor(blue)], [], lib, p, table)
    saved = col.observe(D.FrameContext(at=2.0), BoardRead(board=board2), res, [red, blue], [], 0.9)
    assert sorted((m.champion, m.star, m.evidence) for m in saved) == sorted(
        [(yo, 1, D.EVIDENCE_TRAITS), (orn, 2, D.EVIDENCE_TRAITS)])


def test_namer_with_collector_passes_frame_context_and_reload_reads_approved(static, table, tmp_path):
    """`UnitNamer.from_static(autolearn=True)`: 수집기가 붙고 옛 auto_ 크롭은 대기로 간다. `reload()`는 승인만 싣는다."""
    root = tmp_path / "units_screen"
    ak = cid(static, "아칼리")
    (root / ak).mkdir(parents=True)
    ok, buf = cv2.imencode(".png", img((0, 0, 255)))
    buf.tofile(str(root / ak / "auto_legacy.png"))
    namer = U.UnitNamer.from_static(static, root, autolearn=True)
    assert namer.collector is not None and len(namer.library) == 0
    (m,) = namer.collector.db.entries()
    assert m.status == D.PENDING
    namer.collector.db.approve(m)
    namer.reload()
    assert namer.library.ids == {ak}
    plain = U.UnitNamer.from_static(static, root, autolearn=False)
    assert plain.collector is None and plain.library.ids == {ak}


# ---------------------------------------------------------------- QA 27: 짝이 끝난(또는 짝이 없는) 구매가 남아 다른 유닛에 붙는 경로
@pytest.mark.xfail(strict=True, reason="QA 27 FAIL: 남은 구매 기록이 창(3초) 안의 벤치 이동·판독 깜빡임과 짝지어진다 "
                                       "(vision-engineer 수정 대상, _workspace/27_qa_gate.md)")
@pytest.mark.parametrize("case", ["ledger_echo_then_drag", "combine_on_board_then_drag", "bench_flicker"])
def test_qa27_stale_purchase_never_labels_another_unit(static, tmp_path, case):
    """구매 X가 이미 짝지어졌거나(상점+장부 이중 보고) 새 벤치 칸 없이 끝난 뒤(보드 유닛과 합성), 같은 3초 안에 다른 유닛 Y가
    벤치에서 '새 칸'처럼 보이면(두 프레임에 걸친 끌어 옮기기 · 한 프레임 판독 누락) Y의 크롭이 X 이름으로 저장되면 안 된다."""
    col = D.UnitCollector(db_at(tmp_path, static))
    ka = cid(static, "카르마")
    a, y, bought = img((0, 0, 255)), img((0, 255, 255)), img((255, 0, 0))
    if case == "ledger_echo_then_drag":
        col.observe(D.FrameContext(at=1.0, shop=(ka, None, None, None, None)), BoardRead(bench=_bench(0, 1)),
                    _names(0, 2), [], [a, y], None)
        col.observe(D.FrameContext(at=1.3, shop=(None, None, None, None, None)), BoardRead(bench=_bench(0, 1, 2)),
                    _names(0, 3), [], [a, y, bought], None)               # 상점 증거로 짝지어 저장(맞음)
        col.note_purchase(ka, at=1.3)                                       # 같은 구매를 장부가 뒤늦게 알림(루프 순서)
        col.observe(D.FrameContext(at=1.6), BoardRead(bench=_bench(0, 2)), _names(0, 2), [], [a, bought], None)
        col.observe(D.FrameContext(at=1.9), BoardRead(bench=_bench(0, 2, 5)), _names(0, 3), [], [a, bought, y], None)
    elif case == "combine_on_board_then_drag":
        col.observe(D.FrameContext(at=1.0), BoardRead(bench=_bench(0, 1)), _names(0, 2), [], [a, y], None)
        col.note_purchase(ka, at=1.1)                                       # 보드의 카르마 2기와 합성 → 벤치 새 칸 없음
        col.observe(D.FrameContext(at=1.3), BoardRead(bench=_bench(0)), _names(0, 1), [], [a], None)          # Y 들어 올림
        col.observe(D.FrameContext(at=1.6), BoardRead(bench=_bench(0, 4)), _names(0, 2), [], [a, y], None)    # Y 내려놓음
    else:
        col.observe(D.FrameContext(at=1.0), BoardRead(bench=_bench(0, 1)), _names(0, 2), [], [a, y], None)
        col.note_purchase(ka, at=1.1)
        col.observe(D.FrameContext(at=1.3), BoardRead(bench=_bench(0)), _names(0, 1), [], [a], None)          # 1번 칸 판독 누락
        col.observe(D.FrameContext(at=1.6), BoardRead(bench=_bench(0, 1)), _names(0, 2), [], [a, y], None)
    wrong = [m for m in col.saved if m.champion == ka and np.array_equal(db_at(tmp_path, static).load_image(m), y)]
    assert wrong == []
