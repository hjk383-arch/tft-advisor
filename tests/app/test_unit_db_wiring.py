"""유닛 이름·사진 DB 연결(vision 23·25 보고의 app 쪽 연결).

1. observe 뒤 `unit_namer.set_hints(장부 보유 챔피언)`, 새 판 `unit_namer.reset()`
2. 장부 구매 이벤트 → `unit_namer.collector.note_purchase(id, at)`
3. 트레이/인식 확인 창 "유닛 사진 검토" → `open_review_window(static, on_changed=request_reload)`
5. 인식 확인 창: board_common/missed_board 줄, 뒷받침 없는 이름 "(추정)"
6. 스크린샷 모드: agree_frames = 1
"""
from __future__ import annotations

from dataclasses import dataclass

from tft_advisor.app.ledger import LedgerEvent
from tft_advisor.app.loop import LiveLoop
from tft_advisor.app.names import NameBook
from tft_advisor.app.recog_view import GUESS_MARK, RecogSnapshot, board_common_note, build_view, is_guess
from tft_advisor.app.session import SessionTracker
from tft_advisor.config import load_settings
from tft_advisor.contracts import GameState, ScreenMode, UnitOnBoard

from .conftest import FakeClock, FakeDetector, FakeRecognizer, FakeSource, planning_state


class FakeCollector:
    def __init__(self):
        self.purchases = []

    def note_purchase(self, cid, at=None):
        self.purchases.append((cid, at))


class FakeNamer:
    def __init__(self):
        self.hints = []
        self.resets = 0
        self.reloads = 0
        self.collector = FakeCollector()
        self.agree_frames = 2

    def set_hints(self, ids):
        self.hints.append(sorted(ids))

    def reset(self):
        self.resets += 1

    def request_reload(self):
        self.reloads += 1


class NamerRecognizer(FakeRecognizer):
    def __init__(self, states):
        super().__init__(states)
        self.unit_namer = FakeNamer()


class EventTracker(SessionTracker):
    """observe가 정해 둔 장부 이벤트를 남긴다(구매 추론을 재현하지 않고 연결만 본다)."""

    def __init__(self, events):
        super().__init__()
        self.script = list(events)

    def observe(self, *a, **kw):
        merged = super().observe(*a, **kw)
        self.last_events = self.script.pop(0) if self.script else []
        for ev in self.last_events:
            if ev.kind == "buy":
                self.data.units.add(ev.unit_id, 1, source="shop")
        return merged


def make(states, script, events):
    clock = FakeClock()
    rec = NamerRecognizer(states)
    loop = LiveLoop(source=FakeSource(), recognizer=rec, settings=load_settings(), tracker=EventTracker(events),
                    detector=FakeDetector(script), clock=clock, sleep=clock.sleep)
    return loop, rec.unit_namer


def test_hints_and_purchases_reach_the_unit_namer():
    buy = LedgerEvent(kind="buy", unit_id="DA_18_Yorick", copies=1, evidence="gold", at=123.0)
    xp = LedgerEvent(kind="xp", gold=-4, evidence="gold", at=124.0)
    loop, namer = make([planning_state(), planning_state(stage="2-4")], [{"shop"}, {"shop"}], [[buy, xp], []])
    loop.step()
    assert namer.collector.purchases == [("DA_18_Yorick", 123.0)]     # 구매만, 경험치 구매는 아니다
    assert namer.hints[-1] == ["DA_18_Yorick"]
    loop.step()
    assert namer.collector.purchases == [("DA_18_Yorick", 123.0)]     # 같은 구매를 두 번 알리지 않는다
    assert namer.hints[-1] == ["DA_18_Yorick"]


def test_session_tracker_exposes_owned_champions_and_last_events():
    t = SessionTracker()
    t.observe(planning_state(), {"shop"})
    assert t.last_events == [] and t.owned_champions() == []
    t.data.units.add("DA_18_Zyra", 2, source="manual")
    assert t.owned_champions() == ["DA_18_Zyra"]


def test_new_game_resets_the_unit_namer():
    loop, namer = make([planning_state(), GameState(screen_mode=ScreenMode.GAME_OVER)], [{"shop"}, {"stage"}], [])
    loop.settings = loop.settings.model_copy(update={"app": loop.settings.app.model_copy(
        update={"reset_confirm_frames": 1})})
    loop.step()
    loop.step()
    assert namer.resets == 1


def test_namer_errors_do_not_break_the_loop():
    loop, namer = make([planning_state()], [{"shop"}], [])

    def boom(ids):
        raise RuntimeError("x")

    namer.set_hints = boom
    assert loop.step() is not None


# ---------------------------------------------------------------------------
# 인식 확인 창 표시
# ---------------------------------------------------------------------------


@dataclass
class Slot:
    unit_id: str | None
    unit_conf: float = 0.9
    name_source: str = "traits"
    hex: tuple | None = None
    bench_slot: int | None = None
    star: int | None = 1
    items: tuple = ()
    item_count: int = 0


@dataclass
class Read:
    board: list
    bench: list
    board_common: tuple = ()
    missed_board: int = 0
    unplaced: tuple = ()
    count: int = 1
    unresolved_items: int = 0


def test_is_guess_rule():
    assert is_guess(Slot("A", 0.75, "library"))
    assert not is_guess(Slot("A", 0.85, "library"))                 # 힌트로 뒷받침(완화 임계, 상한 0.90)
    assert not is_guess(Slot("A", 0.6, "traits"))
    assert not is_guess(Slot(None, 0.5, "library"))
    s = Slot("A", 0.95, "library")
    s.corroborated = False
    assert is_guess(s)


def test_view_marks_guesses_and_shows_board_common():
    names = NameBook()
    board = [UnitOnBoard(id="DA_18_Zyra", star=1, hex=(0, 1), confidence=0.7),
             UnitOnBoard(id="DA_18_Xayah", star=1, hex=(1, 2), confidence=0.9)]
    read = Read(board=[Slot("DA_18_Zyra", 0.7, "library", hex=(0, 1)), Slot("DA_18_Xayah", 0.9, "forced", hex=(1, 2))],
                bench=[], board_common=("DA_18_Xayah", "DA_18_Yorick"), missed_board=1)
    state = planning_state(board=board, bench=[])
    view = build_view(RecogSnapshot(state=state, board_read=read, kind="screenshot"), names)
    rows = {r.unit_id: r for r in view.board}
    assert rows["DA_18_Zyra"].guess and GUESS_MARK in rows["DA_18_Zyra"].text()
    assert not rows["DA_18_Xayah"].guess
    yorick = names.name("DA_18_Yorick")
    assert view.board_common_note == f"보드에 확인된 챔피언(자리 미상): {yorick} · 놓친 유닛 1기"
    assert any(view.board_common_note in ln for ln in view.lines(with_age=False))


def test_board_common_note_empty_cases():
    names = NameBook()
    assert board_common_note(None, [], names) is None
    assert board_common_note(Read([], []), [], names) is None
    assert board_common_note(Read([], [], missed_board=2), [], names) == "놓친 유닛 2기"


def test_recog_window_renders_guess_and_note(qapp, settings, tmp_path):
    from tft_advisor.app.recog_window import RecogWindow

    names = NameBook()
    board = [UnitOnBoard(id="DA_18_Zyra", star=1, hex=(0, 1), confidence=0.7)]
    read = Read(board=[Slot("DA_18_Zyra", 0.7, "library", hex=(0, 1))], bench=[],
                board_common=("DA_18_Yorick",), missed_board=1)
    w = RecogWindow(settings, names=names, state_dir=tmp_path, passive=False)
    w.show_snapshot(RecogSnapshot(state=planning_state(board=board, bench=[]), board_read=read, kind="screenshot"))
    html = w.body.text()
    assert GUESS_MARK in html and "놓친 유닛 1기" in html and "자리 미상" in html
    w.deleteLater()


# ---------------------------------------------------------------------------
# 유닛 사진 검토 진입점
# ---------------------------------------------------------------------------


def test_review_opener_uses_static_and_reload(monkeypatch):
    from tft_advisor.app import live, unit_review

    seen = {}

    def fake_open(static, directory=None, *, on_changed=None):
        seen["static"], seen["cb"] = static, on_changed
        return "window"

    monkeypatch.setattr(unit_review, "open_review_window", fake_open)
    rec = NamerRecognizer([])
    rec.static = object()
    opener = live.unit_review_opener(rec)
    assert opener() == "window" and seen["static"] is rec.static
    seen["cb"]()
    assert rec.unit_namer.reloads == 1
    assert live.unit_review_opener(FakeRecognizer([])) is None      # 유닛 이름 인식이 꺼져 있으면 없음


def test_overlay_menu_and_recog_button_open_review_non_modally(qapp, settings, tmp_path):
    from tft_advisor.app.overlay import OverlayWindow
    from tft_advisor.app.recog_window import REVIEW_TEXT, RecogController

    opened = []

    class Win:
        def __init__(self):
            self.visible = True
            self.raised = 0

        def isVisible(self):   # noqa: N802
            return self.visible

        def raise_(self):
            self.raised += 1

        def activateWindow(self):   # noqa: N802
            pass

    def opener():
        opened.append(Win())
        return opened[-1]

    w = OverlayWindow(settings, state_dir=tmp_path)
    assert "유닛 사진 검토…" not in [a.text() for a in w.menu().actions()]
    w.attach_unit_review(opener)
    action = next(a for a in w.menu().actions() if a.text() == "유닛 사진 검토…")
    action.trigger()
    assert len(opened) == 1 and w.review_window is opened[0]
    action.trigger()                                  # 이미 열려 있으면 새로 열지 않고 앞으로
    assert len(opened) == 1 and opened[0].raised == 1
    opened[0].visible = False
    ctl = RecogController(settings, state_dir=tmp_path, cli=False, names=w.names, on_review=w.open_unit_review)
    ctl.set_enabled(True, persist=False)
    btn = ctl.window.review_btn
    assert btn.isVisibleTo(ctl.window) and btn.text() == REVIEW_TEXT
    btn.click()
    assert len(opened) == 2
    ctl.set_enabled(False, persist=False)

    def broken():
        raise OSError("no dir")

    w.attach_unit_review(broken)
    w.review_window = None
    assert w.open_unit_review() is None and "열지 못했습니다" in w.status.extra
    w.deleteLater()


def test_screenshot_mode_sets_single_frame_names():
    from tft_advisor.app.screenshot import single_frame_names

    rec = NamerRecognizer([])
    single_frame_names(rec)
    assert rec.unit_namer.agree_frames == 1
    single_frame_names(FakeRecognizer([]))            # 이름 인식이 없어도 괜찮다


def test_config_unit_db_keys():
    s = load_settings()
    assert s.vision.unit_pending_weight == 0.0 and s.vision.unit_purchase_autoapprove is False
