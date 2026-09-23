"""vision 보드 판독 → `GameState` 배선(18_board_wiring.md).

고치는 문제: 보드는 읽히는데(`Recognizer.last_board_read`) 그 값이 `--screenshot` 경로에서 추천까지
가지 않았다 — `GameState.board`/`bench`가 None이고, 유닛에 끼워진 아이템이 `items_ready`에 안 잡혀
눈에 보이는 아이템이 "(부족)"으로 나왔다.

고정하는 계약
1. 한 장짜리 입력에도 자리·성급·장착 아이템이 들어간다. 구매 기록이 없으니 **정체만 미상**이다.
2. 장착 아이템은 `ItemState.equipped`로 들어가 `items_ready`에서 "보유"로 잡힌다(중복 계산 없이).
3. 보드를 읽었지만 이름만 모르는 상태는 "보드 미인식"이 아니다(`UnitsKnowledge.SEEN`).
4. 실시간 경로도 같다. 보드 묶음을 읽지 않은 프레임에서 직전 판독을 잃지 않는다.

사용자 원본 캡처(`tests/fixtures/screens/raw`, gitignore)는 **읽기만** 한다. 인원 수는 자기 라벨이 아니라
보드 가운데 `N/M` 워터마크(게임이 직접 알려 주는 독립 신호)로 검증한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tft_advisor.app.screenshot import run_screenshot
from tft_advisor.app.session import SessionTracker
from tft_advisor.app.unit_merge import BoardObs, SlotObs, apply_board_read, equipped_refs
from tft_advisor.contracts import UNKNOWN_UNIT_ID, FieldSource, GameState, ItemRef, ItemState, ScreenMode
from tft_advisor.unit_status import UnitsKnowledge, units_knowledge, units_note, units_reason

from .conftest import FakeClock, planning_state

TESTS = Path(__file__).resolve().parents[1]
RAW = TESTS / "fixtures" / "screens" / "raw"
MINI_STATS = TESTS / "fixtures" / "stats" / "mini_18.json"

EQUIPPED = ("DA_GuinsoosRageblade", "DA_KrakensFury")
"""mini 통계의 `executioner-draven` 캐리 BIS 3개 중 둘(상징 하나는 없다)."""


# ---------------------------------------------------------------------------
# 0. 보조
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeSlot:
    """`vision.board.UnitSlot`과 같은 속성만 가진 최소 대역(어댑터가 속성 이름으로 받는다)."""

    star: int | None = 1
    items: tuple[str, ...] = ()
    hex: tuple[int, int] | None = None
    bench_slot: int | None = None
    confidence: float = 0.85


@dataclass(frozen=True)
class FakeRead:
    board: tuple[FakeSlot, ...] = ()
    bench: tuple[FakeSlot, ...] = ()
    confidence: float = 0.85


def board_read_with_items() -> FakeRead:
    """보드 3기(가운데 1기가 아이템 2개 장착) + 벤치 1기."""
    return FakeRead(
        board=(FakeSlot(star=2, hex=(0, 3)),
               FakeSlot(star=1, hex=(1, 3), items=EQUIPPED),
               FakeSlot(star=1, hex=(2, 4))),
        bench=(FakeSlot(star=1, bench_slot=0),))


def mini_advisor():
    from tft_advisor.advisor import Advisor, JsonStatsAdapter
    from tft_advisor.config import load_settings, load_weights

    return Advisor(stats=JsonStatsAdapter.from_file(MINI_STATS), settings=load_settings(),
                   weights=load_weights(), backend="mock")


def statuses(rec, item_id: str) -> set[str]:
    return {r.status for c in rec.target_comps for r in c.items_ready if r.item_id == item_id}


# ---------------------------------------------------------------------------
# 1. 한 장짜리 경로(--screenshot): 자리·성급·아이템은 들어오고 정체만 미상
# ---------------------------------------------------------------------------


def test_apply_board_read_fills_board_bench_and_equipped_items():
    state = apply_board_read(planning_state(level=6), board_read_with_items())
    assert state.board is not None and state.bench is not None
    assert len(state.board) == 3 and len(state.bench) == 1
    assert [u.id for u in state.board] == [UNKNOWN_UNIT_ID] * 3      # 구매 기록이 없다 = 정체 미상
    assert [u.star for u in state.board] == [2, 1, 1]                # 성급은 vision이 읽었다
    assert state.board[1].hex == (1, 3) and state.bench[0].bench_slot == 0
    assert state.board[1].items == list(EQUIPPED)
    assert state.confidence_of("board") == 0.0                       # 이름을 하나도 모른다
    assert state.items is not None
    assert [r.id for r in state.items.equipped] == list(EQUIPPED)
    assert all(r.holder is None for r in state.items.equipped)       # 소유자를 지어내지 않는다
    assert state.items.all_ids() == []                               # 아이템 벤치는 건드리지 않는다
    assert state.items.owned_ids() == list(EQUIPPED)


def test_apply_board_read_keeps_the_item_bench_and_adds_to_it():
    bench = ItemState(components=[ItemRef(id="DA_Component_BFSword")])
    state = apply_board_read(planning_state(items=bench, confidence={"items": 0.9}), board_read_with_items())
    assert [r.id for r in state.items.components] == ["DA_Component_BFSword"]
    assert [r.id for r in state.items.equipped] == list(EQUIPPED)
    assert state.confidence_of("items") == 0.9                       # 아이템 벤치 신뢰도는 그대로


def test_apply_board_read_is_a_no_op_without_a_read():
    state = planning_state()
    assert apply_board_read(state, None) is state
    assert apply_board_read(state, FakeRead()) is state


def test_equipped_refs_name_the_holder_when_the_ledger_knows_it(tmp_path):
    """장부가 정체를 알면 `ItemRef.holder`가 찬다 — 몰라도 아이템 자체는 신뢰도가 그대로다."""
    tracker = SessionTracker(tmp_path / "s.json", clock=FakeClock(100.0))
    tracker.observe(planning_state(level=6), groups=("stage", "hud"))
    tracker.set_units({"DA_Draven18": 1}, source="carousel")
    read = FakeRead(board=(FakeSlot(star=1, hex=(0, 3), items=EQUIPPED),))
    state = tracker.observe(planning_state(level=6), groups=("stage", "hud"), board_read=read)
    holders = {r.holder for r in state.items.equipped}
    assert holders == {"DA_Draven18"}
    assert all(r.confidence == 0.85 for r in state.items.equipped)


def test_equipped_refs_without_a_merge_result_have_no_holder():
    obs = BoardObs(board=(SlotObs(star=1, items=EQUIPPED, confidence=0.7),))
    refs = equipped_refs(obs)
    assert [r.id for r in refs] == list(EQUIPPED)
    assert all(r.holder is None and r.confidence == 0.7 for r in refs)


# ---------------------------------------------------------------------------
# 2. 장착 아이템이 추천을 바꾼다 — (부족) → (보유)
# ---------------------------------------------------------------------------


def test_equipped_items_turn_items_ready_from_missing_to_owned():
    base = dict(screen_mode=ScreenMode.PLANNING, stage="4-2", level=8, gold=30, hp=60,
                items=ItemState(), confidence={"items": 0.9})
    advisor = mini_advisor()
    try:
        before = advisor.advise(GameState(**base))
        advisor.reset()
        after = advisor.advise(apply_board_read(GameState(**base), board_read_with_items()))
    finally:
        advisor.close()
    for item_id in EQUIPPED:
        assert statuses(before, item_id) == {"missing"}, item_id
        assert statuses(after, item_id) == {"owned"}, item_id


def test_equipped_items_are_counted_once():
    """보드 유닛의 items와 `ItemState.equipped`는 같은 판독이다 — 자원 풀에 두 번 들어가면 안 된다."""
    from collections import Counter

    from tft_advisor.advisor.features import build_view
    from tft_advisor.advisor.stats_source import JsonStatsAdapter

    stats = JsonStatsAdapter.from_file(MINI_STATS)
    state = apply_board_read(planning_state(level=6, items=ItemState(), confidence={"items": 0.9}),
                             board_read_with_items())
    view = build_view(state, stats, 0.6, None)
    assert view.equipped_seen is True
    assert Counter(view.equipped) == Counter(EQUIPPED)
    assert Counter(view.owned_pool(stats)) == Counter(EQUIPPED)


def test_tracked_equipped_estimate_does_not_override_a_real_read():
    """세션 추적 추정(`equipped_tracked`)은 vision이 직접 읽은 장착분을 덮지 않는다."""
    from collections import Counter

    from tft_advisor.advisor.features import build_view
    from tft_advisor.advisor.stats_source import JsonStatsAdapter

    stats = JsonStatsAdapter.from_file(MINI_STATS)
    state = apply_board_read(planning_state(items=ItemState(), confidence={"items": 0.9}),
                             board_read_with_items())
    view = build_view(state, stats, 0.6, Counter(["DA_Bloodthirster"]))
    assert Counter(view.equipped) == Counter(EQUIPPED)


# ---------------------------------------------------------------------------
# 3. 문구 — "보드 미인식"이 아니다
# ---------------------------------------------------------------------------


def test_seen_state_wording_is_honest_and_polite():
    state = apply_board_read(planning_state(level=6), board_read_with_items())
    assert units_knowledge(state, 0.6) is UnitsKnowledge.SEEN
    reason, note = units_reason(state, 0.6), units_note(state, 0.6)
    for text in (reason, note):
        assert "보드 미인식" not in text
        assert "4" in text                               # 화면에서 본 4기
        assert not any(bad in text for bad in ("한다", "하라", "해요", "이다"))
    assert "이름" in reason and "이름" in note
    assert "다" == reason.rstrip(".")[-1] or "니다" in reason     # 합쇼체


def test_report_and_warnings_do_not_call_a_read_board_unrecognised():
    from tft_advisor.app.report import format_report, recognition_warnings

    from .conftest import sample_recommendation

    state = apply_board_read(planning_state(level=6), board_read_with_items())
    text = format_report(state, sample_recommendation(), threshold=0.6)
    assert "보드 미인식" not in text and "화면 인식 4기" in text
    warns = recognition_warnings(state, 0.6)
    assert any("챔피언 이름 미상" in w for w in warns)
    assert not any("낮은 신뢰도" in w and "보드" in w for w in warns)   # 0.00을 경고로 늘어놓지 않는다
    assert not any("미인식: " in w and "보드" in w for w in warns)


def test_a_board_that_was_never_read_still_says_unrecognised():
    """네 상태의 경계가 그대로다 — 진짜로 못 읽은 보드는 여전히 "보드 미인식"이다."""
    blind = planning_state()
    assert units_knowledge(blind, 0.6) is UnitsKnowledge.UNKNOWN
    assert "보드 미인식" in units_note(blind, 0.6)


def test_overlay_uses_the_same_note():
    from tft_advisor.app.report import comp_lines

    from .conftest import sample_recommendation

    state = apply_board_read(planning_state(level=6), board_read_with_items())
    note = units_note(state, 0.6)
    comp = sample_recommendation().target_comps[1]          # 보유/부족이 비어 있는 덱
    for compact in (False, True):
        lines = comp_lines(comp, 1, _Names(), compact=compact, units_note=note)
        assert any(note in ln for ln in lines)


class _Names:
    def name(self, x: str) -> str:
        return x

    def joined(self, xs, limit: int = 6) -> str:
        return ", ".join(list(xs)[:limit])


# ---------------------------------------------------------------------------
# 4. 실시간 경로 — loop가 판독을 넘기고, 안 읽은 프레임에서도 잃지 않는다
# ---------------------------------------------------------------------------


def test_live_loop_passes_the_board_read_to_the_session():
    """`LiveLoop.step`이 `recognizer.last_board_read`를 `SessionTracker.observe`로 넘긴다."""
    from tft_advisor.app.loop import LiveLoop

    from .conftest import FakeSource

    read = board_read_with_items()
    seen: dict[str, object] = {}

    class Rec:
        profile = None
        last_board_read = read

        def content_for(self, image, box):
            return None

        def recognize(self, image, **kw):
            return planning_state(level=6)

    class Tracker:
        data = type("D", (), {"recognitions": 0})()
        learner = None

        def observe(self, state, groups, owned_row=None, board_read=None):
            seen["board_read"] = board_read
            return state

        def looks_like_new_game(self, state):
            return False

    loop = LiveLoop(source=FakeSource(1), recognizer=Rec(), tracker=Tracker(),
                    detector=_AlwaysChanged(), clock=FakeClock(0.0))
    loop.step()
    assert seen["board_read"] is read


class _AlwaysChanged:
    def update(self, image, content=None):
        return ("stage", "board")


def test_session_keeps_the_last_board_read_on_frames_that_skip_the_board_group(tmp_path):
    """보드 묶음은 화면이 바뀐 프레임에서만 읽힌다 — 안 읽은 프레임에서 자리·아이템이 사라지면 안 된다."""
    tracker = SessionTracker(tmp_path / "s.json", clock=FakeClock(100.0))
    first = tracker.observe(planning_state(level=6), groups=("stage", "hud"),
                            board_read=board_read_with_items())
    assert len(first.board) == 3 and [r.id for r in first.items.equipped] == list(EQUIPPED)

    later = tracker.observe(planning_state(level=6, gold=12), groups=("stage", "hud"))
    assert later.board is not None and len(later.board) == 3
    assert later.board[1].hex == (1, 3)
    assert [r.id for r in later.items.equipped] == list(EQUIPPED)


def test_new_game_forgets_the_board_read(tmp_path):
    tracker = SessionTracker(tmp_path / "s.json", clock=FakeClock(100.0))
    tracker.observe(planning_state(level=6), groups=("stage", "hud"), board_read=board_read_with_items())
    tracker.reset("test")
    after = tracker.observe(planning_state(level=6), groups=("stage", "hud"))
    assert after.board is None and units_knowledge(after, 0.6) is UnitsKnowledge.UNKNOWN


def test_manual_correction_keeps_the_vision_positions(tmp_path):
    """수동 교정(`_refresh_units`)은 board_read 없이 다시 병합한다 — 자리·아이템을 잃으면 안 된다."""
    tracker = SessionTracker(tmp_path / "s.json", clock=FakeClock(100.0))
    tracker.observe(planning_state(level=6), groups=("stage", "hud"), board_read=board_read_with_items())
    tracker.add_unit("DA_Draven18", 1)
    state = tracker.state
    assert len(state.board) == 3 and state.board[1].hex == (1, 3)
    assert [r.id for r in state.items.equipped] == list(EQUIPPED)
    assert state.field_source["board"] == FieldSource.MANUAL


# ---------------------------------------------------------------------------
# 5. 실캡처 — `--screenshot` 한 번이 실제로 보드를 채운다(인원은 워터마크로 검증)
# ---------------------------------------------------------------------------


def _raw_planning_capture() -> tuple[Path, int] | None:
    """보드 인원 워터마크(`N/M`)가 적힌 준비 화면 캡처 중 인원이 가장 많은 것."""
    best: tuple[int, Path] | None = None
    for label in sorted(RAW.glob("*.expected.json")):
        try:
            data = json.loads(label.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        wm = data.get("_board_count_watermark")
        image = label.with_suffix("").with_suffix(".png")
        if not wm or not image.is_file() or data.get("screen_mode") != "planning":
            continue
        n = int(str(wm).split("/")[0])
        if best is None or n > best[0]:
            best = (n, image)
    return (best[1], best[0]) if best else None


class _Spy:
    """실제 advisor에 넘기면서 들어간 state와 나온 추천을 기록한다."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.states: list[GameState] = []
        self.recs: list[object] = []

    backend_name = "mock"

    @property
    def stats(self):
        return self.inner.stats

    def advise(self, state: GameState):
        self.states.append(state)
        rec = self.inner.advise(state)
        self.recs.append(rec)
        return rec

    def close(self) -> None:
        self.inner.close()


def test_screenshot_mode_on_a_real_capture_fills_board_bench_and_equipped(settings, recognizer):
    from tft_advisor.vision.ocr import RapidOcrEngine

    found = _raw_planning_capture()
    if found is None:
        pytest.skip("원본 캡처 없음(tests/fixtures/screens/raw, gitignore)")
    if RapidOcrEngine.available_backend() is None:
        pytest.skip("OCR 백엔드 없음")
    image, want = found
    if len(recognizer.item_matcher) == 0:
        pytest.skip("아이템 템플릿 없음")

    from tft_advisor.advisor import create_advisor

    spy = _Spy(create_advisor("mock", settings=settings))
    lines: list[str] = []
    try:
        assert run_screenshot(image, settings=settings, out=lines.append,
                              recognizer=recognizer, advisor=spy) == 0
    finally:
        spy.close()

    assert spy.states, "추천이 한 번도 불리지 않았다"
    state = spy.states[-1]
    read = recognizer.last_board_read
    assert read is not None, "이 캡처에서 보드를 읽지 못했다"

    # 1. 인원은 게임이 직접 알려 주는 워터마크(독립 신호)와 같다
    assert state.board is not None and state.bench is not None
    assert len(state.board) == want == len(read.board)
    assert len(state.bench) == len(read.bench)

    # 2. 장착 아이템이 전부 ItemState.equipped로 들어간다
    from collections import Counter

    assert Counter(r.id for r in state.items.equipped) == Counter(read.all_item_ids())

    # 3. 문구: "보드 미인식"이 아니다
    text = "\n".join(lines)
    assert "보드 미인식" not in text
    assert units_knowledge(state, settings.vision.state_min_confidence) is UnitsKnowledge.SEEN

    # 4. 장착분이 목표 덱의 핵심 아이템과 겹치면 "(보유)"로 나온다(벤치에 없는 아이템으로만 검사)
    equipped = set(read.all_item_ids()) - set(state.items.all_ids())
    rec = spy.recs[-1]
    wanted = {r.item_id for c in rec.target_comps for r in c.items_ready}
    hit = wanted & equipped
    if hit:
        owned = {r.item_id for c in rec.target_comps for r in c.items_ready if r.status == "owned"}
        assert hit <= owned, (sorted(hit), sorted(owned))
        assert "(보유)" in text
