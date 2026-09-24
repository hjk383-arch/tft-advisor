"""인식 확인 창(사용자 요청 20): 표시 모델(순수) · 루프가 싣는 판독/인식 시간 · 콘솔 출력 · Qt 창 · 토글 · CLI.

Qt는 헤드리스(`QT_QPA_PLATFORM=offscreen`, `tests/app/conftest.py`).
"""
from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest

from tft_advisor.app.loop import LoopUpdate
from tft_advisor.app.names import NameBook
from tft_advisor.app.recog_view import (
    ConsolePrinter, RecogSnapshot, build_view, unit_source, view_lines,
)
from tft_advisor.contracts import (
    UNKNOWN_UNIT_ID, FieldSource, GameState, ItemRef, ItemState, ScreenMode, UnitOnBoard,
)
from tft_advisor.vision.board import BoardRead, UnitSlot

ZYRA, XAYAH, YORICK = "DA_18_Zyra", "DA_18_Xayah", "DA_18_Yorick"
IE, WARMOG = "DA_InfinityEdge", "DA_WarmogsArmor"
SWORD, VEST = "DA_Component_BFSword", "DA_Component_ChainVest"


@pytest.fixture(scope="module")
def names():
    return NameBook()


def board_state(mode: ScreenMode = ScreenMode.PLANNING) -> GameState:
    """보드 3기(화면 이름 · 장부 이름 · 미상) + 벤치 2기 + 아이템 벤치 재료 2 + 소유자 미상 장착 1."""
    return GameState(
        screen_mode=mode, stage="3-2", level=6, gold=30, hp=70,
        board=[
            UnitOnBoard(id=XAYAH, star=2, items=[IE], hex=(0, 3), confidence=0.9),
            UnitOnBoard(id=ZYRA, star=1, hex=(1, 1), confidence=0.85),
            UnitOnBoard(id=UNKNOWN_UNIT_ID, star=1, items=[WARMOG], hex=(3, 6), confidence=0.2),
        ],
        bench=[
            UnitOnBoard(id=YORICK, star=1, bench_slot=0, confidence=0.85),
            UnitOnBoard(id=UNKNOWN_UNIT_ID, star=2, bench_slot=4, confidence=0.2),
        ],
        items=ItemState(
            components=[ItemRef(id=SWORD, category="component"), ItemRef(id=VEST, category="component")],
            equipped=[ItemRef(id=IE, holder=XAYAH), ItemRef(id=WARMOG), ItemRef(id=IE)],
        ),
        confidence={"board": 0.7, "bench": 0.7, "items": 0.95},
        field_source={"board": FieldSource.TRACKED, "bench": FieldSource.TRACKED},
        captured_at=datetime.now(UTC) - timedelta(seconds=3),
    )


def board_read() -> BoardRead:
    return BoardRead(
        board=(UnitSlot(star=2, items=(IE,), hex=(0, 3), unit_id=XAYAH, unit_conf=0.9, name_source="forced",
                        item_count=1),
               UnitSlot(star=1, hex=(1, 1)),
               UnitSlot(star=1, items=(WARMOG,), hex=(3, 6), item_count=2)),
        bench=(UnitSlot(star=1, bench_slot=0), UnitSlot(star=2, bench_slot=4)),
        confidence=0.85, unresolved_items=1,
    )


# ---------------------------------------------------------------------------
# 표시 모델
# ---------------------------------------------------------------------------


def test_units_have_position_name_star_items_and_source(names):
    view = build_view(RecogSnapshot(state=board_state(), board_read=board_read(), recog_ms=412.4,
                                    groups=("board", "hud")), names)
    assert [r.pos for r in view.board] == ["1행 4열", "2행 2열", "4행 7열"]
    xayah, zyra, unknown = view.board
    assert (xayah.name, xayah.star, xayah.items, xayah.source, xayah.detail) == \
        ("자야", 2, ("무한의 대검",), "vision", "특성 구속")
    assert zyra.source == "ledger" and zyra.name == "자이라"
    assert unknown.name == "이름 미상" and unknown.source == "unknown"
    assert unknown.unread_items == 1 and unknown.items_text == "워모그의 갑옷, ?"   # 아이콘 2칸 중 1칸 못 읽음
    assert "신뢰도 0.90 · 화면(특성 구속)" in xayah.text()
    assert any("인식 412ms" in h for h in view.header)
    assert any("판독 0.85" in h for h in view.header)
    assert view.notice is None and view.board_visible


def test_bench_shows_all_nine_slots_with_empties(names):
    view = build_view(RecogSnapshot(state=board_state(), board_read=board_read()), names)
    assert len(view.bench) == 9 and view.bench_count == 2
    assert view.bench[0].name == "요릭" and not view.bench[0].empty
    assert view.bench[1].empty and view.bench[1].text() == "벤치 2  (비어 있음)"
    assert view.bench[4].source == "unknown"
    assert view.bench_note == "이름 미상 1기"


def test_equipped_grouped_by_owner_and_unused_by_kind(names):
    view = build_view(RecogSnapshot(state=board_state(), board_read=board_read()), names)
    titles = {g.title: g.items for g in view.equipped}
    assert titles["자야 (1행 4열)"] == ("무한의 대검",)
    assert titles["이름 미상 (4행 7열)"] == ("워모그의 갑옷", "?")
    # items.equipped에 있지만 어느 칸에도 붙지 않은 무한의 대검 1개 → 소유자 미상
    assert titles["소유자 미상"] == ("무한의 대검",)
    assert [g.text() for g in view.unused] == ["재료 2: B.F. 대검, 쇠사슬 조끼"]


def test_non_board_screen_says_board_is_not_visible(names):
    view = build_view(RecogSnapshot(state=board_state(ScreenMode.AUGMENT_SELECT)), names)
    assert not view.board_visible
    assert "보드가 보이지 않습니다" in view.notice and "증강 선택" in view.notice
    assert "마지막으로 읽은 값" in view.notice


def test_missing_values_are_reported_not_invented(names):
    state = GameState(screen_mode=ScreenMode.PLANNING, stage="2-1")
    lines = build_view(RecogSnapshot(state=state), names).lines()
    assert "[보드] 0기 — 읽지 못했습니다" in lines
    assert "[벤치] 읽지 못했습니다" in lines
    assert "[미사용 아이템] 읽지 못했습니다" in lines
    empty = build_view(None, names)
    assert "아직 인식 결과가 없습니다" in empty.notice


def test_source_falls_back_to_field_source(names):
    u = UnitOnBoard(id=ZYRA, star=1, hex=(1, 1))
    vision = GameState(board=[u], field_source={"board": FieldSource.VISION})
    manual = GameState(board=[u], field_source={"board": FieldSource.MANUAL})
    assert unit_source(u, on_bench=False, state=vision) == ("vision", None)
    assert unit_source(u, on_bench=False, state=manual) == ("manual", None)
    unplaced = UnitOnBoard(id=ZYRA, star=1)
    read = BoardRead(unplaced=(ZYRA,))
    assert unit_source(unplaced, on_bench=False, state=GameState(), board_read=read) == ("vision", "자리 미상")


def test_age_text(names):
    view = build_view(RecogSnapshot(state=board_state()), names)
    assert "초 전" in view.age_text()


# ---------------------------------------------------------------------------
# 루프 갱신 → 스냅숏 / 콘솔
# ---------------------------------------------------------------------------


def test_snapshot_from_updates():
    st = board_state()
    snap = RecogSnapshot.from_update(LoopUpdate(kind="recognized", state=st, board_read="R", recog_ms=5.0,
                                                recognized=("board",)))
    assert snap.state is st and snap.board_read == "R" and snap.groups == ("board",)
    assert RecogSnapshot.from_update(LoopUpdate(kind="advice", state=st)) is None
    err = RecogSnapshot.from_update(LoopUpdate(kind="error", message="인식 실패: x"), snap)
    assert err.state is st and err.message == "인식 실패: x"


def test_console_printer_prints_only_changes(names):
    out: list[str] = []
    printer = ConsolePrinter(names, out=out.append)
    st = board_state()
    assert printer.feed(LoopUpdate(kind="recognized", state=st, recog_ms=100.0))
    assert not printer.feed(LoopUpdate(kind="recognized", state=st, recog_ms=250.0))   # 인식 시간만 다름
    assert not printer.feed(LoopUpdate(kind="advice", state=st))
    changed = st.model_copy(update={"gold": 12})
    assert printer.feed(LoopUpdate(kind="kept", state=changed))
    assert len(out) == 2 and "=== 인식 확인 ===" in out[0]


def test_loop_attaches_board_read_and_recognition_time(settings):
    from .test_loop import make_loop

    updates: list[LoopUpdate] = []
    loop, rec, _ = make_loop(settings, [board_state()], [{"board"}], updates=updates)
    rec.last_board_read = board_read()
    loop.step()
    first = [u for u in updates if u.kind != "advice"][0]
    assert first.board_read is rec.last_board_read
    assert first.recog_ms is not None and first.recog_ms >= 0
    assert all(u.board_read is None and u.recog_ms is None for u in updates if u.kind == "advice")


def test_view_lines_in_text(names):
    text = "\n".join(view_lines(RecogSnapshot(state=board_state(), board_read=board_read()), names))
    assert "[보드] 3기" in text and "[벤치] 2/9" in text and "[장착 아이템]" in text and "[미사용 아이템]" in text


# ---------------------------------------------------------------------------
# Qt 창 · 토글 · 오버레이 메뉴
# ---------------------------------------------------------------------------


def test_window_renders_and_skips_identical_content(qapp, settings, tmp_path, names):
    from tft_advisor.app.recog_window import RecogWindow

    w = RecogWindow(settings, names=names, state_dir=tmp_path)
    snap = RecogSnapshot(state=board_state(), board_read=board_read())
    w.show_snapshot(snap)
    body = w.body.text()
    assert "자야" in body and "이름 미상" in body and "벤치 2/9" in body and "미사용 아이템" in body
    n = w.renders
    w.show_snapshot(RecogSnapshot(state=board_state(), board_read=board_read()))
    assert w.renders == n
    w.show_snapshot(RecogSnapshot(state=board_state(ScreenMode.CAROUSEL)))
    assert "보드가 보이지 않습니다" in w.body.text()
    w.move(123, 45)
    w.save_position()
    assert w.load_position() == (123, 45)
    w.close()


def test_passive_window_does_not_take_focus(qapp, settings, names):
    from PySide6.QtCore import Qt

    from tft_advisor.app.recog_window import RecogWindow

    w = RecogWindow(settings, names=names, passive=True)
    assert w.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert w.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert not (w.windowFlags() & Qt.WindowType.WindowTransparentForInput)   # 클릭 통과 아님(옮길 수 있다)
    w.close()


def test_controller_toggle_persists_only_without_cli(qapp, settings, tmp_path, names):
    from tft_advisor.app.recog_window import RecogController

    saved: list[dict] = []
    s = settings.model_copy(deep=True)
    ctl = RecogController(s, state_dir=tmp_path, names=names, saver=lambda u, config_dir=None: saved.append(u))
    assert ctl.enabled is False and ctl.window is None
    ctl.feed(LoopUpdate(kind="recognized", state=board_state()))   # 꺼져 있어도 마지막 값은 기억한다
    ctl.set_enabled(True)
    assert ctl.window is not None and ctl.window.isVisible()
    assert "자야" in ctl.window.body.text()
    assert saved == [{"ui": {"test_view": True}}] and s.ui.test_view is True
    ctl.window.close_window()                 # × 버튼 = 토글 끄기
    assert ctl.enabled is False and not ctl.window.isVisible()
    assert saved[-1] == {"ui": {"test_view": False}}

    saved.clear()
    locked = RecogController(settings.model_copy(deep=True), state_dir=tmp_path, names=names, cli=True,
                             saver=lambda u, config_dir=None: saved.append(u))
    assert locked.enabled is True            # CLI > 설정
    locked.set_enabled(False)
    assert saved == []                        # CLI로 정한 실행은 저장하지 않는다
    if locked.window is not None:
        locked.window.close()


def test_overlay_menu_has_toggle_and_feeds_window(qapp, settings, tmp_path, names):
    from tft_advisor.app.overlay import OverlayWindow
    from tft_advisor.app.recog_view import MENU_TEXT
    from tft_advisor.app.recog_window import RecogController

    overlay = OverlayWindow(settings.model_copy(deep=True), names=names, state_dir=tmp_path)
    ctl = RecogController(overlay.settings, state_dir=tmp_path, names=names, saver=lambda *a, **k: None)
    overlay.attach_recog(ctl)
    action = next(a for a in overlay.menu().actions() if a.text().startswith(MENU_TEXT))
    assert action.isCheckable() and not action.isChecked()
    action.setChecked(True)                   # 트레이 체크 → 창이 뜬다
    assert ctl.enabled and ctl.window.isVisible()
    overlay._on_update_main(LoopUpdate(kind="recognized", state=board_state(), board_read=board_read()))
    assert "화면(특성 구속)" in ctl.window.body.text()
    ctl.set_enabled(False)
    assert not action.isChecked()             # 메뉴 체크도 같이 풀린다
    ctl.window.close()
    overlay.close()


# ---------------------------------------------------------------------------
# CLI · 설정 · 콘솔 인코딩
# ---------------------------------------------------------------------------


def test_cli_flags_and_routing(monkeypatch):
    from tft_advisor import __main__ as cli

    p = cli.build_parser()
    assert p.parse_args([]).test_view is None
    assert p.parse_args(["--test-view"]).test_view is True
    assert p.parse_args(["--no-test-view"]).test_view is False
    with pytest.raises(SystemExit):
        p.parse_args(["--test-view", "--no-test-view"])
    called = {}
    monkeypatch.setattr("tft_advisor.app.live.run_live", lambda **kw: called.update(kw) or 0)
    assert cli.main(["--live", "--no-overlay", "--no-setup", "--test-view"]) == 0
    assert called["test_view"] is True


def test_setup_choice_writes_ui_test_view(settings):
    from tft_advisor.app.setup import SetupChoice

    assert "ui" not in SetupChoice().updates()
    assert SetupChoice(test_view=True).updates()["ui"] == {"test_view": True}
    assert SetupChoice(test_view=True).apply(settings).ui.test_view is True


def test_ensure_utf8_stdio_fixes_cp1252_pipe(monkeypatch):
    from tft_advisor.app.report import ensure_utf8_stdio

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr("sys.stdout", stream)
    ensure_utf8_stdio()
    print("인식 확인 ★")
    stream.flush()
    assert raw.getvalue().decode("utf-8").strip() == "인식 확인 ★"


def test_live_console_prints_recognition_check(monkeypatch, settings, capsys):
    """`--live --no-overlay --test-view`: 콘솔에 인식 확인 블록이 찍힌다."""
    from tft_advisor.app import live

    from .test_loop import make_loop

    loop, rec, _ = make_loop(settings, [board_state()], [{"board"}])
    rec.last_board_read = board_read()
    live._run_console(loop, settings, "mock", None, max_frames=1, test_view=True)
    out = capsys.readouterr().out
    assert "=== 인식 확인 ===" in out and "화면(특성 구속)" in out


def test_screenshot_mode_prints_recognition_check(settings, recognizer):
    """`--screenshot PATH --test-view`(창 없이): 요약 뒤에 인식 확인 블록이 붙는다. 창은 `test_window`로만 뜬다."""
    from tft_advisor.app.screenshot import run_screenshot
    from tft_advisor.vision.ocr import RapidOcrEngine

    from .conftest import RAW

    image = RAW / "5-5 전투 전.png"
    if not image.is_file() or RapidOcrEngine.available_backend() is None:
        pytest.skip("캡처 또는 OCR 백엔드 없음")
    lines: list[str] = []
    assert run_screenshot(image, settings=settings, out=lines.append, recognizer=recognizer, jev="mock",
                          test_view=True, test_window=False) == 0
    text = "\n".join(lines)
    assert "=== 인식 확인 ===" in text
    assert "[보드]" in text and "[벤치]" in text and "[장착 아이템]" in text and "[미사용 아이템]" in text


# ---------------------------------------------------------------------------
# QA 20 (qa-validator): 표시의 정직성 · 우선순위 · 실제 캡처 대조
# ---------------------------------------------------------------------------


def test_qa_board_is_visible_wherever_vision_reads_the_board(names):
    from tft_advisor.vision.recognizer import READ_MODES

    for mode in READ_MODES["board"]:
        snap = RecogSnapshot(state=board_state(mode), board_read=board_read(), groups=("board", "items"))
        view = build_view(snap, names)
        assert view.board_visible, mode
        assert not view.notice or "보드가 보이지 않습니다" not in view.notice, mode


def test_qa_stale_board_notice_only_when_board_group_not_read(names):
    stale = build_view(RecogSnapshot(state=board_state(), board_read=board_read(), groups=("hud", "shop")), names)
    assert stale.notice and "보드를 다시 읽지 않았습니다" in stale.notice
    fresh = build_view(RecogSnapshot(state=board_state(), board_read=board_read(), groups=("board", "hud")), names)
    assert fresh.notice is None


def test_qa_slot_with_a_different_vision_name_is_not_labelled_screen(names):
    """같은 자리 판독 칸의 이름이 상태의 이름과 다르면 '화면'이라 부르지 않는다(필드 출처로 돌아간다)."""
    state = board_state()
    read = BoardRead(board=(UnitSlot(star=1, hex=(1, 1), unit_id=XAYAH, unit_conf=0.9, name_source="library"),),
                     confidence=0.85)
    zyra = next(u for u in state.board if u.id == ZYRA)
    assert unit_source(zyra, on_bench=False, state=state, board_read=read) == ("ledger", None)


def test_qa_screenshot_mode_ignores_ui_test_view_setting(monkeypatch, settings, tmp_path):
    """`[ui] test_view = true`여도 `--screenshot`은 CLI 플래그 없이 창·확인 출력을 켜지 않는다."""
    from tft_advisor import __main__ as cli

    on = settings.model_copy(deep=True)
    on.ui.test_view = True
    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: on)
    monkeypatch.setattr(cli, "wants_setup", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr("tft_advisor.app.screenshot.run_screenshot", lambda *a, **kw: calls.append(kw) or 0)
    img = tmp_path / "x.png"
    img.write_bytes(b"")
    assert cli.main(["--screenshot", str(img), "--no-jev"]) == 0
    assert (calls[-1]["test_view"], calls[-1]["test_window"]) == (False, False)
    assert cli.main(["--screenshot", str(img), "--no-jev", "--test-view", "--no-overlay"]) == 0
    assert (calls[-1]["test_view"], calls[-1]["test_window"]) == (True, False)
    assert cli.main(["--screenshot", str(img), "--no-jev", "--test-view"]) == 0
    assert (calls[-1]["test_view"], calls[-1]["test_window"]) == (True, True)


@pytest.mark.parametrize("capture", ["5-5 전투 전", "2-6 전투 전"])
def test_qa_raw_view_equals_state_and_labels(capture, settings, recognizer, names, monkeypatch):
    """실제 캡처: 창에 보이는 보드·벤치·장착·미사용 = GameState 그대로, 자리·성급·아이템 = 사람 라벨."""
    from collections import Counter

    from tft_advisor.app.screenshot import _with_board
    from tft_advisor.fixtures import load_expected
    from tft_advisor.vision.capture import load_image
    from tft_advisor.vision.ocr import RapidOcrEngine

    from .conftest import RAW

    image = RAW / f"{capture}.png"
    if not image.is_file() or RapidOcrEngine.available_backend() is None:
        pytest.skip("캡처 또는 OCR 백엔드 없음")
    if recognizer.unit_namer is not None:
        monkeypatch.setattr(recognizer.unit_namer, "autolearn", False)   # QA 실행이 디스크 라이브러리를 건드리지 않게
    state = _with_board(recognizer.recognize(load_image(image)), recognizer)
    read = recognizer.last_board_read
    view = build_view(RecogSnapshot(state=state, board_read=read, kind="screenshot"), names)
    assert view.board_visible and view.notice is None

    def hexpos(r):
        a, b = r.pos.split()
        return int(a[:-1]) - 1, int(b[:-1]) - 1

    # 창 = 상태
    by_hex = {tuple(u.hex): u for u in state.board if u.hex is not None}
    for r in view.board:
        u = by_hex[hexpos(r)]
        assert (r.unit_id or UNKNOWN_UNIT_ID, r.star, r.items) == (u.id, u.star, tuple(names.names(list(u.items))))
    occupied = {u.bench_slot: u for u in state.bench}
    assert [not r.empty for r in view.bench[:9]] == [i in occupied for i in range(9)]
    shown_eq = Counter(x for g in view.equipped for x in g.items if x != "?")
    assert shown_eq == Counter(names.name(i.id) for i in state.items.equipped)
    shown_unused = Counter(x for g in view.unused for x in g.items)
    assert shown_unused == Counter(names.name(r.id) for a in ("components", "completed", "emblems", "others")
                                   for r in getattr(state.items, a))
    # 출처: 이름이 있으면 같은 자리 판독 칸이 같은 이름을 가졌다(스크린샷에는 장부가 없다)
    for r in view.board:
        assert r.source == ("vision" if r.unit_id else "unknown")
    # 상태 = 사람 라벨(자리·성급·아이템)
    ex = load_expected(RAW / f"{capture}.expected.json").extras
    labelled = {tuple(s["hex"]): s for s in ex["board_slots"]}
    assert {hexpos(r) for r in view.board} == set(labelled)
    for r in view.board:
        s = labelled[hexpos(r)]
        assert r.star == s.get("star", r.star)
        assert Counter(r.items) == Counter(names.names(s.get("items", [])))
        if s.get("unit_id") and r.unit_id:
            assert r.unit_id == s["unit_id"]
