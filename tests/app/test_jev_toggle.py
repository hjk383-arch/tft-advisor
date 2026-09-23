"""Jev 실시간 판단 토글 — 설정 체크박스 ↔ `jev_backend`, 트레이 토글(재시작 없이 교체·저장), CLI 우선순위.

**live Jev는 한 번도 부르지 않는다**: advisor는 전부 가짜이고, 전환기의 `builder`/`saver`를 갈아 끼운다.
Qt는 헤드리스(`QT_QPA_PLATFORM=offscreen`, `tests/app/conftest.py`)로 돈다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tft_advisor import __main__ as cli
from tft_advisor.app import setup as core
from tft_advisor.app.jev_toggle import JevSwitcher
from tft_advisor.config import load_settings

from .conftest import planning_state

CONFIG_SRC = Path(__file__).resolve().parents[2] / "config"


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


class FakeAdvisor:
    """`advise()` 호출을 세는 가짜 advisor(네트워크 없음)."""

    def __init__(self, name: str = "mock") -> None:
        self.backend_name = name
        self.calls: list = []
        self.closed = False

    def advise(self, state):
        self.calls.append(state)
        return None

    def reset(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeLoop:
    """`set_advisor`만 있는 최소 루프."""

    def __init__(self) -> None:
        self.advisor = FakeAdvisor()
        self.swaps: list = []

    def set_advisor(self, advisor) -> None:
        self.advisor = advisor
        self.swaps.append(advisor)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    out = tmp_path / "config"
    shutil.copytree(CONFIG_SRC, out)
    return out


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-used")


def made(built: list):
    """가짜 builder — 만든 백엔드 이름을 기록한다."""

    def build(name, settings):
        built.append(name)
        return FakeAdvisor(name)

    return build


def switcher(loop=None, *, backend="mock", built=None, saved=None, **kw) -> JevSwitcher:
    return JevSwitcher(loop=loop, settings=load_settings(), backend=backend,
                       builder=made(built if built is not None else []),
                       saver=(lambda updates, config_dir=None: saved.append(updates))
                       if saved is not None else (lambda updates, config_dir=None: None), **kw)


def set_jev_backend(config_dir: Path, value: str) -> None:
    core.save_settings({"advisor": {"jev_backend": value}}, config_dir=config_dir)


def make_dialog(config_dir: Path, state_dir: Path):
    import numpy as np

    from tft_advisor.app.setup_dialog import SetupDialog

    class Grabber(core.MonitorGrabber):
        def __init__(self) -> None:
            self.closed = False

        def monitors(self):
            return core.monitors_from_mss([{"left": 0, "top": 0, "width": 1920, "height": 1080}])

        def grab(self, info):
            return np.full((1080, 1920, 3), 90, np.uint8)

        def close(self) -> None:
            self.closed = True

    return SetupDialog(load_settings(config_dir), config_dir=config_dir, state_dir=state_dir,
                       grabber=Grabber())


# ---------------------------------------------------------------------------
# 1. 체크박스 ↔ jev_backend (Qt 없는 순수 규칙)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend, checks", [("mock", (False, False)), ("live", (True, False)),
                                             ("off", (False, True))])
def test_checkbox_mapping_round_trips_all_three_backends(backend, checks):
    assert core.jev_checks(backend) == checks
    assert core.jev_backend_from_checks(*checks) == backend


def test_off_check_wins_over_live_check():
    """두 체크가 같이 켜지는 일은 UI에서 막지만(live 비활성), 규칙 자체도 off를 우선한다."""
    assert core.jev_backend_from_checks(True, True) == "off"


# ---------------------------------------------------------------------------
# 2. 설정 대화상자 체크박스
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("live, off, want", [(False, False, "mock"), (True, False, "live"),
                                             (False, True, "off")])
def test_dialog_saves_the_backend_the_checkboxes_mean(qapp, config_dir, tmp_path, with_key, live, off, want):
    d = make_dialog(config_dir, tmp_path)
    d.jev_live.setChecked(live)
    d.jev_off.setChecked(off)
    assert d.current_choice().jev_backend == want
    assert d.save_and_close("saved").action == "saved"
    assert load_settings(config_dir).advisor.jev_backend == want
    d.deleteLater()


@pytest.mark.parametrize("backend, live, off", [("mock", False, False), ("live", True, False),
                                                ("off", False, True)])
def test_dialog_loads_the_saved_backend_into_the_checkboxes(qapp, config_dir, tmp_path, with_key,
                                                            backend, live, off):
    set_jev_backend(config_dir, backend)
    d = make_dialog(config_dir, tmp_path)
    assert (d.jev_live.isChecked(), d.jev_off.isChecked()) == (live, off)
    assert d.current_choice().jev_backend == backend
    d.deleteLater()


def test_dialog_disables_live_without_an_api_key(qapp, config_dir, tmp_path, no_key):
    d = make_dialog(config_dir, tmp_path)
    assert d.jev_live.isEnabled() is False
    assert "TYPESAFE_API_KEY" in d.jev_note.text()
    assert d.current_choice().jev_backend == "mock"
    d.deleteLater()


def test_dialog_enables_live_with_an_api_key(qapp, config_dir, tmp_path, with_key):
    d = make_dialog(config_dir, tmp_path)
    assert d.jev_live.isEnabled() is True
    assert "TYPESAFE_API_KEY" not in d.jev_note.text()
    d.deleteLater()


def test_dialog_keeps_a_saved_live_setting_when_the_key_is_missing(qapp, config_dir, tmp_path, no_key):
    """설정에 live가 적혀 있는데 키만 빠진 경우: 말없이 mock으로 바꾸지 않고, 끄는 것은 되게 둔다."""
    set_jev_backend(config_dir, "live")
    d = make_dialog(config_dir, tmp_path)
    assert d.jev_live.isChecked() and d.jev_live.isEnabled()   # 끌 수는 있다
    assert d.current_choice().jev_backend == "live"
    d.jev_live.setChecked(False)
    assert d.jev_live.isEnabled() is False                     # 다시 켤 수는 없다
    assert d.current_choice().jev_backend == "mock"
    d.deleteLater()


def test_dialog_off_checkbox_disables_the_live_checkbox(qapp, config_dir, tmp_path, with_key):
    d = make_dialog(config_dir, tmp_path)
    d.jev_off.setChecked(True)
    assert d.jev_live.isEnabled() is False
    assert "off" in d.jev_note.text()
    d.deleteLater()


def test_dialog_mock_note_explains_the_trade_off(qapp, config_dir, tmp_path, with_key):
    d = make_dialog(config_dir, tmp_path)
    note = d.jev_note.text()
    assert "과금 없음" in note and "가짜 고정값" in note
    d.deleteLater()


# ---------------------------------------------------------------------------
# 3. JevSwitcher — 교체·저장·차단
# ---------------------------------------------------------------------------


def test_switch_builds_swaps_and_persists(with_key, config_dir):
    built, saved, loop = [], [], FakeLoop()
    sw = switcher(loop, built=built, saved=saved)
    result = sw.switch("live")
    assert result.ok and result.backend == "live" and result.saved
    assert built == ["live"] and loop.advisor.backend_name == "live"
    assert saved == [{"advisor": {"jev_backend": "live"}}]
    assert sw.backend == "live" and sw.settings.advisor.jev_backend == "live"


def test_switch_persists_to_settings_toml_for_the_next_run(with_key, config_dir):
    """기본 saver(설정 화면과 같은 저장 경로) — 주석을 유지하고 .bak을 남긴다."""
    sw = JevSwitcher(loop=FakeLoop(), settings=load_settings(config_dir), backend="mock",
                     config_dir=config_dir, builder=made([]))
    assert sw.switch("live").saved
    assert load_settings(config_dir).advisor.jev_backend == "live"
    text = (config_dir / "settings.toml").read_text(encoding="utf-8")
    assert "# 우선순위: CLI" in text                       # 주석 보존
    assert (config_dir / "settings.toml.bak").is_file()


def test_switching_to_live_makes_no_jev_call_until_the_next_recommendation(with_key):
    loop = FakeLoop()
    sw = switcher(loop)
    sw.switch("live")
    new = loop.advisor
    assert new.calls == []            # 교체만으로는 아무것도 묻지 않는다(워밍업 없음)
    new.advise(planning_state())      # 다음 추천에서 비로소 새 백엔드를 쓴다
    assert len(new.calls) == 1


def test_switch_keeps_the_shown_recommendation(with_key):
    from tft_advisor.app.loop import LiveLoop

    from .conftest import FakeRecognizer, sample_recommendation

    loop = LiveLoop(source=None, recognizer=FakeRecognizer([planning_state()]), advisor=FakeAdvisor())
    loop.last_recommendation = sample_recommendation()
    sw = switcher(loop)
    assert sw.switch("live").ok
    assert loop.last_recommendation is not None and loop.last_recommendation.target_comps
    loop.runner.close()


def test_switch_refuses_live_without_an_api_key(no_key):
    built, loop = [], FakeLoop()
    sw = switcher(loop, built=built)
    result = sw.switch("live")
    assert result.ok is False and "TYPESAFE_API_KEY" in result.message
    assert built == [] and sw.backend == "mock" and loop.swaps == []


def test_switch_off_needs_no_api_key(no_key):
    sw = switcher(FakeLoop())
    assert sw.switch("off").ok and sw.backend == "off"


def test_cli_locked_run_refuses_to_toggle(with_key):
    sw = switcher(FakeLoop(), locked_by_cli="off")
    assert sw.can_toggle is False
    result = sw.switch("live")
    assert result.ok is False and "--jev off" in result.message
    assert sw.backend == "mock"


def test_switch_survives_a_save_failure(with_key):
    def boom(updates, config_dir=None):
        raise OSError("읽기 전용")

    sw = JevSwitcher(loop=FakeLoop(), settings=load_settings(), builder=made([]), saver=boom)
    result = sw.switch("live")
    assert result.ok and result.saved is False and "저장하지 못했습니다" in result.message
    assert sw.backend == "live"       # 이번 실행에는 적용된다


def test_build_failure_keeps_the_old_backend(with_key):
    def boom(name, settings):
        raise RuntimeError("stats 없음")

    sw = JevSwitcher(loop=FakeLoop(), settings=load_settings(), builder=boom)
    result = sw.switch("live")
    assert result.ok is False and "만들지 못했습니다" in result.message and sw.backend == "mock"


def test_set_backend_runs_in_a_worker_thread(with_key):
    import threading

    seen = {}

    def on_done(result):
        seen["result"] = result
        seen["thread"] = threading.current_thread().name

    sw = switcher(FakeLoop())
    sw.set_backend("live", on_done=on_done).join(timeout=5)
    assert seen["result"].ok and seen["thread"] != threading.current_thread().name


def test_same_backend_is_a_no_op(with_key):
    built = []
    sw = switcher(FakeLoop(), backend="mock", built=built)
    assert sw.switch("mock").ok and built == []


# ---------------------------------------------------------------------------
# 4. 루프 — 교체는 추천 스레드 안에서
# ---------------------------------------------------------------------------


def test_thread_runner_swaps_inside_its_own_thread_and_closes_the_old_advisor():
    from tft_advisor.app.loop import ThreadAdviceRunner

    old, new = FakeAdvisor("mock"), FakeAdvisor("live")
    runner = ThreadAdviceRunner(old, lambda state, rec: None)
    try:
        runner.set_advisor(new)
        assert runner.advisor_swapped.wait(5)
        assert runner.advisor is new and old.closed is True
        runner.submit(planning_state())
        for _ in range(50):
            if new.calls:
                break
            import time

            time.sleep(0.02)
        assert new.calls and old.calls == []
    finally:
        runner.close()


def test_live_loop_set_advisor_updates_loop_and_runner():
    from tft_advisor.app.loop import InlineAdviceRunner, LiveLoop

    from .conftest import FakeRecognizer

    old, new = FakeAdvisor("mock"), FakeAdvisor("live")
    runner = InlineAdviceRunner(old, lambda state, rec: None)
    loop = LiveLoop(source=None, recognizer=FakeRecognizer([planning_state()]), runner=runner)
    loop.set_advisor(new)
    assert loop.advisor is new and runner.advisor is new and old.closed is True


# ---------------------------------------------------------------------------
# 5. 오버레이 트레이 토글
# ---------------------------------------------------------------------------


@pytest.fixture
def window(qapp, settings, tmp_path, with_key):
    from tft_advisor.app.overlay import OverlayWindow

    w = OverlayWindow(settings, state_dir=tmp_path, jev=switcher(FakeLoop()))
    yield w
    w.deleteLater()


def jev_action(window):
    from tft_advisor.app.jev_toggle import MENU_TEXT

    return next(a for a in window.menu().actions() if a.text().startswith(MENU_TEXT))


def test_menu_has_a_checkable_jev_item(window):
    action = jev_action(window)
    assert action.isCheckable() and action.isChecked() is False and action.isEnabled()


def test_toggling_the_menu_item_switches_the_backend_and_the_status_line(window, qapp):
    action = jev_action(window)
    action.setChecked(True)                       # 사용자가 체크한 것과 같다
    window.jev_thread.join(timeout=5)
    qapp.processEvents()                          # 작업 스레드 → UI 시그널
    assert window.jev.backend == "live"
    assert window.status.backend == "live" and "live" in window.foot.text()
    assert jev_action(window).isChecked() is True

    action = jev_action(window)
    action.setChecked(False)
    window.jev_thread.join(timeout=5)
    qapp.processEvents()
    assert window.jev.backend == "mock" and window.status.backend == "mock"


def test_menu_item_is_locked_when_the_cli_fixed_the_backend(qapp, settings, tmp_path, with_key):
    from tft_advisor.app.overlay import OverlayWindow

    w = OverlayWindow(settings, state_dir=tmp_path, jev=switcher(FakeLoop(), locked_by_cli="live"))
    action = jev_action(w)
    assert action.isEnabled() is False and "--jev live" in action.text()
    w.deleteLater()


def test_menu_item_is_disabled_without_an_api_key(qapp, settings, tmp_path, no_key):
    from tft_advisor.app.overlay import OverlayWindow

    w = OverlayWindow(settings, state_dir=tmp_path, jev=switcher(FakeLoop()))
    action = jev_action(w)
    assert action.isEnabled() is False and "API 키 없음" in action.text()
    w.deleteLater()


def test_setup_dialog_change_is_applied_without_a_restart(window, qapp):
    """트레이 "설정"에서 Jev를 바꾸면 재시작 없이 적용한다(파일은 대화상자가 이미 썼다 → 다시 쓰지 않는다)."""
    from tft_advisor.app.setup import SetupChoice, SetupOutcome

    saved = []
    window.jev._saver = lambda updates, config_dir=None: saved.append(updates)
    window._apply_saved_jev(SetupOutcome(action="saved", choice=SetupChoice(jev_backend="live")))
    window.jev_thread.join(timeout=5)
    qapp.processEvents()
    assert window.jev.backend == "live" and window.status.backend == "live"
    assert saved == []


def test_overlay_without_a_switcher_has_no_jev_item(qapp, settings, tmp_path):
    from tft_advisor.app.jev_toggle import MENU_TEXT
    from tft_advisor.app.overlay import OverlayWindow

    w = OverlayWindow(settings, state_dir=tmp_path)
    assert not any(a.text().startswith(MENU_TEXT) for a in w.menu().actions())
    w.deleteLater()


# ---------------------------------------------------------------------------
# 6. CLI 우선순위 (CLI > 트레이 토글 > settings.toml)
# ---------------------------------------------------------------------------


def parse(argv):
    return cli.build_parser().parse_args(argv)


@pytest.mark.parametrize("argv, want", [([], "auto"), (["--jev", "live"], "live"),
                                        (["--jev", "mock"], "mock"), (["--no-jev"], "off")])
def test_cli_flag_decides_the_run(argv, want):
    assert cli.jev_backend(parse(argv)) == want


@pytest.mark.parametrize("jev, locked", [("auto", None), ("live", "live"), ("off", "off"),
                                         ("mock", "mock")])
def test_run_live_locks_the_toggle_only_for_an_explicit_cli_flag(monkeypatch, settings, jev, locked):
    """`--jev`를 준 실행만 트레이 토글이 잠긴다("auto" = 설정 파일을 따르는 보통 실행)."""
    from tft_advisor.app import live

    seen = {}
    monkeypatch.setattr(live, "build", lambda *a, **kw: (FakeLoop(), FakeAdvisor()))
    monkeypatch.setattr(live, "warm_up", lambda advisor: None)
    monkeypatch.setattr(live, "_run_overlay",
                        lambda *a, **kw: seen.update(kw) or 0)
    assert live.run_live(settings=settings, jev=jev, overlay=True) == 0
    assert seen["jev_cli"] == locked


def test_run_overlay_passes_the_lock_to_the_switcher(monkeypatch, settings, tmp_path):
    """오버레이 배선: 전환기가 창에 붙고, CLI 값이 잠금으로 들어간다."""
    from tft_advisor.app import live

    seen = {}

    def fake_make_overlay(s, *, state_dir=None, config_dir=None, jev=None):
        seen["jev"] = jev
        raise RuntimeError("stop")   # 창을 실제로 띄우지 않는다

    monkeypatch.setattr("tft_advisor.app.overlay.make_overlay", fake_make_overlay)
    loop = FakeLoop()
    with pytest.raises(RuntimeError):
        live._run_overlay(loop, settings, "mock", None, None, None, jev_cli="off")
    assert seen["jev"].locked_by_cli == "off" and seen["jev"].loop is loop
