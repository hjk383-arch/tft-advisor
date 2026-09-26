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
    # 원색 그대로(밝기 255)면 품질 검사의 "강한 효과(밝고 진한 빛)"에 걸린다 → 0.75배로 어둡게(30 보고)
    color = tuple(int(c * 0.75) for c in color)
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
    col.observe(D.FrameContext(at=9.5), r0, _names(0, 2), [], [crops[0], crops[1]], None)   # 새 칸은 직전 2프레임 비어 있어야
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
    for t in (0.7, 1.0):
        col.observe(D.FrameContext(at=t, shop=(ka, ak, None, "*", ak)), BoardRead(bench=_bench(0)), _names(0, 1),
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
    for t in (0.8, 1.0):
        col.observe(D.FrameContext(at=t), BoardRead(board=board0, bench=_bench(0, 1)), _names(1, 2), [a], [a, b], None)
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


def test_board_crops_are_never_collected_even_with_a_unique_trait_solution(static, table, tmp_path):
    """30 보고(사용자 정책): 사진은 벤치에서만. 보드 크롭은 특성 풀이가 확실해도 모으지 않는다(효과·피해 숫자·겹침·잘린 머리)."""
    col = D.UnitCollector(db_at(tmp_path, static))
    yo, orn = cid(static, "요릭"), cid(static, "오른")
    red, blue = img((0, 0, 255)), img((255, 0, 0))
    lib = U.library_from([(yo, red), (orn, blue)])
    counts = {static.trait_by_name(k)["apiName"]: v for k, v in
              {"개화": 1, "전쟁기계": 1, "소환사": 1, "나무정령": 1, "엄호대": 1}.items()}
    p = U.TraitPanel(counts, confidence=0.9)
    board2 = (UnitSlot(star=1, hex=(0, 0)), UnitSlot(star=2, hex=(0, 1)))
    res = U.name_units([U.descriptor(red), U.descriptor(blue)], [], lib, p, table)
    assert res.board_set and all(n.unit_id for n in res.board)
    assert col.observe(D.FrameContext(at=2.0), BoardRead(board=board2), res, [red, blue], [], 0.9) == []
    assert list(tmp_path.rglob("*.png")) == []


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
@pytest.mark.parametrize("case", ["ledger_echo_then_drag", "combine_on_board_then_drag", "bench_flicker"])
def test_qa27_stale_purchase_never_labels_another_unit(static, tmp_path, case):
    """구매 X가 이미 짝지어졌거나(상점+장부 이중 보고) 새 벤치 칸 없이 끝난 뒤(보드 유닛과 합성), 같은 3초 안에 다른 유닛 Y가
    벤치에서 '새 칸'처럼 보이면(두 프레임에 걸친 끌어 옮기기 · 한 프레임 판독 누락) Y의 크롭이 X 이름으로 저장되면 안 된다."""
    col = D.UnitCollector(db_at(tmp_path, static))
    ka = cid(static, "카르마")
    a, y, bought = img((0, 0, 255)), img((0, 255, 255)), img((255, 0, 0))
    if case == "ledger_echo_then_drag":
        for t in (0.7, 1.0):
            col.observe(D.FrameContext(at=t, shop=(ka, None, None, None, None)), BoardRead(bench=_bench(0, 1)),
                        _names(0, 2), [], [a, y], None)
        col.observe(D.FrameContext(at=1.3, shop=(None, None, None, None, None)), BoardRead(bench=_bench(0, 1, 2)),
                    _names(0, 3), [], [a, y, bought], None)               # 상점 증거로 짝지어 저장(맞음)
        col.note_purchase(ka, at=1.3)                                       # 같은 구매를 장부가 뒤늦게 알림(루프 순서)
        col.observe(D.FrameContext(at=1.6), BoardRead(bench=_bench(0, 2)), _names(0, 2), [], [a, bought], None)
        col.observe(D.FrameContext(at=1.9), BoardRead(bench=_bench(0, 2, 5)), _names(0, 3), [], [a, bought, y], None)
    elif case == "combine_on_board_then_drag":
        for t in (0.7, 1.0):
            col.observe(D.FrameContext(at=t), BoardRead(bench=_bench(0, 1)), _names(0, 2), [], [a, y], None)
        col.note_purchase(ka, at=1.1)                                       # 보드의 카르마 2기와 합성 → 벤치 새 칸 없음
        col.observe(D.FrameContext(at=1.3), BoardRead(bench=_bench(0)), _names(0, 1), [], [a], None)          # Y 들어 올림
        col.observe(D.FrameContext(at=1.6), BoardRead(bench=_bench(0, 4)), _names(0, 2), [], [a, y], None)    # Y 내려놓음
    else:
        for t in (0.7, 1.0):
            col.observe(D.FrameContext(at=t), BoardRead(bench=_bench(0, 1)), _names(0, 2), [], [a, y], None)
        col.note_purchase(ka, at=1.1)
        col.observe(D.FrameContext(at=1.3), BoardRead(bench=_bench(0)), _names(0, 1), [], [a], None)          # 1번 칸 판독 누락
        col.observe(D.FrameContext(at=1.6), BoardRead(bench=_bench(0, 1)), _names(0, 2), [], [a, y], None)
    wrong = [m for m in col.saved if m.champion == ka and np.array_equal(db_at(tmp_path, static).load_image(m), y)]
    assert wrong == []
    if case == "ledger_echo_then_drag":                                     # 진짜 구매 짝은 그대로 저장된다
        assert [(m.champion, m.slot) for m in col.saved] == [(ka, "bench:2")]


# ---------------------------------------------------------------- QA 27 F1 수정 규칙
def _prime(col, bench, crops, t0=0.4, shop=None):
    for t in (t0, t0 + 0.3):
        col.observe(D.FrameContext(at=t, shop=shop), BoardRead(bench=_bench(*bench)), _names(0, len(bench)), [],
                    crops, None)


def test_shop_and_ledger_reports_of_two_same_champion_buys_pair_each_once(static, tmp_path):
    """같은 챔피언 두 번 구매, 매번 상점·장부가 둘 다 알린다 → 네 보고가 두 건으로 합쳐지고 두 새 칸이 각각 저장된다."""
    col = D.UnitCollector(db_at(tmp_path, static))
    ka = cid(static, "카르마")
    a, b, c = img((0, 0, 255)), img((255, 0, 0)), img((0, 255, 0))
    _prime(col, (0,), [a], shop=(ka, ka, None, None, None))
    col.observe(D.FrameContext(at=1.2, shop=(None, ka, None, None, None)), BoardRead(bench=_bench(0, 1)),
                _names(0, 2), [], [a, b], None)
    col.note_purchase(ka, at=1.2)
    col.observe(D.FrameContext(at=1.5), BoardRead(bench=_bench(0, 1)), _names(0, 2), [], [a, b], None)
    col.observe(D.FrameContext(at=1.8, shop=(None, None, None, None, None)), BoardRead(bench=_bench(0, 1, 2)),
                _names(0, 3), [], [a, b, c], None)
    col.note_purchase(ka, at=1.8)
    assert [(m.champion, m.slot) for m in col.saved] == [(ka, "bench:1"), (ka, "bench:2")]
    assert all(b.used for b in col._buys) and len(col._buys) == 2


def test_new_slot_after_a_recent_disturbance_is_not_purchase_evidence(static, tmp_path):
    """벤치 유닛을 보드로 올린(보드 변화) 직후 창 안에 산 유닛 → 모호하므로 모으지 않는다(보수적)."""
    col = D.UnitCollector(db_at(tmp_path, static))
    ka = cid(static, "카르마")
    a, b = img((0, 0, 255)), img((255, 0, 0))
    _prime(col, (0, 1), [a, b])
    board = (UnitSlot(star=1, hex=(0, 0)),)
    col.observe(D.FrameContext(at=1.0), BoardRead(board=board, bench=_bench(0)), _names(1, 1), [b], [a], None)
    col.observe(D.FrameContext(at=1.4), BoardRead(board=board, bench=_bench(0)), _names(1, 1), [b], [a], None)
    col.note_purchase(ka, at=1.5)
    col.observe(D.FrameContext(at=1.7), BoardRead(board=board, bench=_bench(0, 3)), _names(1, 2), [b], [a, b], None)
    assert col.saved == []
    # 창이 지나면 다시 모은다
    col.observe(D.FrameContext(at=4.5), BoardRead(board=board, bench=_bench(0, 3)), _names(1, 2), [b], [a, b], None)
    col.observe(D.FrameContext(at=4.8), BoardRead(board=board, bench=_bench(0, 3)), _names(1, 2), [b], [a, b], None)
    col.note_purchase(ka, at=5.0)
    c = img((0, 255, 0))
    col.observe(D.FrameContext(at=5.1), BoardRead(board=board, bench=_bench(0, 3, 4)), _names(1, 3), [b], [a, b, c],
                None)
    assert [(m.champion, m.slot) for m in col.saved] == [(ka, "bench:4")]


def test_purchase_crop_contradicting_approved_crops_is_not_saved(static, tmp_path):
    """산 챔피언의 승인 사진보다 다른 챔피언의 승인 사진을 확실히 더 닮은 크롭 → 저장하지 않는다."""
    db = db_at(tmp_path, static)
    ka, ak = cid(static, "카르마"), cid(static, "아칼리")
    red, blue = img((0, 0, 255)), img((255, 0, 0))
    db.approve(db.add_pending(ka, red, evidence="manual", star=1))
    db.approve(db.add_pending(ak, blue, evidence="manual", star=1))
    col = D.UnitCollector(db)
    a = img((0, 255, 0))
    _prime(col, (0,), [a])
    col.note_purchase(ka, at=1.0)
    col.observe(D.FrameContext(at=1.1), BoardRead(bench=_bench(0, 1)), _names(0, 2), [], [a, img((250, 5, 5), w=30)],
                None)
    assert col.saved == []


# ---------------------------------------------------------------- 30 보고: 수집 정책(벤치만 · 충분하면 그만 · 품질 · 스테이지)
def _dup(champ, conf=0.85):
    return U.SlotName(champ, conf, "duplicate")


def _names_bench(*slot_names):
    return U.BoardNames(board=(), bench=tuple(slot_names))


def _approve_n(db, champ, n, star=1):
    for i in range(n):
        db.approve(db.add_pending(champ, img(((40 * i + 30) % 255, (70 * i) % 255, 200)), evidence="purchase",
                                  star=star))


def test_collection_stops_after_enough_approved_but_a_new_star_is_still_collected(static, tmp_path):
    db = db_at(tmp_path, static)
    ka = cid(static, "카르마")
    _approve_n(db, ka, 3, star=1)
    assert db.approved_count(ka, 1) == 3 and db.approved_count(ka, 2) == 0
    col = D.UnitCollector(db, collect_until=3)
    one = UnitSlot(star=1, bench_slot=0)
    assert col.observe(D.FrameContext(at=1.0), BoardRead(bench=(one,)), _names_bench(_dup(ka)), [],
                       [img((10, 120, 10))], None) == []
    assert col.skipped.get("enough") == 1
    two = UnitSlot(star=2, bench_slot=0)                      # ★2는 처음 → 모은다
    saved = col.observe(D.FrameContext(at=2.0), BoardRead(bench=(two,)), _names_bench(_dup(ka)), [],
                        [img((10, 120, 10))], None)
    assert [(m.champion, m.star, m.evidence) for m in saved] == [(ka, 2, D.EVIDENCE_DUPLICATE)]
    # 0 = 제한 없음
    col0 = D.UnitCollector(db, collect_until=0)
    assert len(col0.observe(D.FrameContext(at=3.0), BoardRead(bench=(one,)), _names_bench(_dup(ka)), [],
                            [img((120, 10, 120))], None)) == 1


def test_label_crops_without_star_count_as_one_star(static, tmp_path):
    db = db_at(tmp_path, static)
    ak = cid(static, "아칼리")
    (db.root / ak).mkdir(parents=True)
    for i in range(3):
        cv2.imencode(".png", img((i * 60, 0, 200)))[1].tofile(str(db.root / ak / f"label_{i}.png"))
    assert db.approved_count(ak, 1) == 3 and db.approved_count(ak, None) == 3


def test_already_recognized_slot_is_not_collected_and_corroborated_middle_library_name_is(static, tmp_path):
    db = db_at(tmp_path, static)
    ka = cid(static, "카르마")
    col = D.UnitCollector(db)
    slot = UnitSlot(star=1, bench_slot=4)
    sure = U.SlotName(ka, 0.86, "library", 0.7, 0.3, corroborated=True)
    middle = U.SlotName(ka, 0.7, "library", 0.58, 0.13, corroborated=True)
    guess = U.SlotName(ka, 0.7, "library", 0.63, 0.21, corroborated=False)
    for n in (sure, guess):
        assert col.observe(D.FrameContext(at=1.0), BoardRead(bench=(slot,)), _names_bench(n), [],
                           [img((10, 120, 10))], None) == []
    saved = col.observe(D.FrameContext(at=2.0), BoardRead(bench=(slot,)), _names_bench(middle), [],
                        [img((10, 120, 10))], None)
    assert [(m.champion, m.evidence, m.slot) for m in saved] == [(ka, D.EVIDENCE_LIBRARY, "bench:4")]
    # 구매 짝도: 새 칸을 라이브러리가 이미 그 챔피언으로 알아봤으면 모으지 않는다
    col2 = D.UnitCollector(db_at(tmp_path / "b", static))
    a, b = img((0, 0, 255)), img((255, 0, 0))
    for t in (0.7, 1.0):
        col2.observe(D.FrameContext(at=t), BoardRead(bench=_bench(0)), _names(0, 1), [], [a], None)
    col2.note_purchase(ka, at=1.1)
    none = U.SlotName(None, 0.0, "none")
    assert col2.observe(D.FrameContext(at=1.3), BoardRead(bench=_bench(0, 1)), _names_bench(none, sure), [],
                        [a, b], None) == []
    assert col2.skipped.get("recognized") == 1


def _glow_crop() -> np.ndarray:
    out = np.full((112, 112, 3), (90, 120, 150), np.uint8)
    cv2.circle(out, (56, 60), 30, (0, 230, 255), -1)          # 밝은 금빛 덩어리(금화·폭발)
    cv2.rectangle(out, (50, 20), (62, 100), (60, 40, 90), -1)
    return out


def _cyan_crop() -> np.ndarray:
    out = img((60, 40, 120))
    cv2.rectangle(out, (36, 16), (76, 104), (255, 230, 0), 4)    # 청록 선택 윤곽(BGR)
    return out


def _neighbour_crop() -> np.ndarray:
    out = img((60, 40, 120))
    cv2.rectangle(out, (8, 30), (24, 90), (160, 30, 160), -1)    # 옆 칸 모델이 왼쪽 끝을 침범(테두리 몇 줄은 배경으로 잡힌다)
    return out


def test_crop_quality_rejects_glow_and_selection_outline_and_flags_neighbour():
    assert D.crop_quality(img((60, 40, 120))).reject is None and D.crop_quality(img((60, 40, 120))).flags == ()
    assert D.crop_quality(_glow_crop()).reject is not None
    assert D.crop_quality(_cyan_crop()).reject is not None
    q = D.crop_quality(_neighbour_crop())
    assert q.reject is None and "옆 칸 침범" in q.flags


def test_collector_applies_quality_and_tactician_rules(static, tmp_path):
    db = db_at(tmp_path, static)
    ka = cid(static, "카르마")
    col = D.UnitCollector(db)
    s0 = UnitSlot(star=1, bench_slot=0)
    for crop in (_glow_crop(), _cyan_crop()):
        assert col.observe(D.FrameContext(at=1.0), BoardRead(bench=(s0,)), _names_bench(_dup(ka)), [], [crop],
                           None) == []
    assert col.skipped.get("quality") == 2
    # 전략가가 그 칸 위에 있다 → 모으지 않는다
    assert col.observe(D.FrameContext(at=2.0, tactician=frozenset({0})), BoardRead(bench=(s0,)),
                       _names_bench(_dup(ka)), [], [img((60, 40, 120))], None) == []
    assert col.skipped.get("tactician") == 1
    # 옆 칸 침범은 저장하되 표시·점수 낮춤
    (m,) = col.observe(D.FrameContext(at=3.0), BoardRead(bench=(s0,)), _names_bench(_dup(ka)), [],
                       [_neighbour_crop()], None)
    assert "옆 칸 침범" in (m.note or "") and m.score < 0.85 and m.status == D.PENDING


def test_crop_stage_uses_the_session_stage_and_drops_a_stale_frame_read(static, tmp_path):
    db = db_at(tmp_path, static)
    ka, ak = cid(static, "카르마"), cid(static, "아칼리")
    col = D.UnitCollector(db, collect_until=0)
    s0 = UnitSlot(star=1, bench_slot=0)
    col.set_stage("2-3")                                      # 오버레이 = 세션 스테이지
    (m,) = col.observe(D.FrameContext(at=1.0, stage="1-3"), BoardRead(bench=(s0,)), _names_bench(_dup(ka)), [],
                       [img((10, 120, 10))], None)
    assert m.stage == "2-3"
    col2 = D.UnitCollector(db, collect_until=0)                # 세션 스테이지가 없을 때: 프레임 값, 뒤로 가면 비운다
    (a,) = col2.observe(D.FrameContext(at=1.0, stage="2-5"), BoardRead(bench=(s0,)), _names_bench(_dup(ak)), [],
                        [img((120, 10, 10))], None)
    (b,) = col2.observe(D.FrameContext(at=2.0, stage="1-4"), BoardRead(bench=(s0,)), _names_bench(_dup(ak)), [],
                        [img((10, 10, 120))], None)
    assert a.stage == "2-5" and b.stage is None
    col2.reset()
    assert col2.session_stage is None and col2._max_stage is None
