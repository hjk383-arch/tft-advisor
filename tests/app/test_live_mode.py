"""`--live` 배선: 가짜 캡처 소스로 콘솔 모드를 끝까지 돌린다(실제 ChangeDetector·SessionTracker 사용).

실제 OCR은 쓰지 않는다(느리고 이 테스트의 대상이 아니다) — `Recognizer`만 가짜로 바꾼다.
"""
from __future__ import annotations

import json

import pytest

from tft_advisor.app import live
from tft_advisor.contracts import ScreenMode

from .conftest import FakeRecognizer, FakeSource, planning_state


@pytest.fixture
def tmp_settings(settings, tmp_path):
    """세션 파일이 저장소의 `_state/`를 건드리지 않게 한다."""
    return settings.model_copy(update={"app": settings.app.model_copy(update={"state_dir": str(tmp_path)})})


@pytest.fixture
def fake_recognizer(monkeypatch):
    states = [planning_state(gold=42)]
    fake = FakeRecognizer(states)
    monkeypatch.setattr("tft_advisor.vision.recognizer.Recognizer", lambda **kw: fake)
    return fake


def test_console_mode_runs_and_prints(tmp_settings, fake_recognizer, capsys):
    """zeros 프레임 3장 → 실제 ChangeDetector가 2번째 프레임에서 '변화 + 안정'을 보고한다."""
    code = live.run_live(settings=tmp_settings, jev="mock", overlay=False,
                         source=FakeSource(count=3), max_frames=3)
    assert code == 0
    out = capsys.readouterr().out
    assert "[목표 덱]" in out and "인식 품질" in out
    assert fake_recognizer.calls, "인식이 한 번은 돌아야 한다"


def test_session_file_is_written(tmp_settings, fake_recognizer, tmp_path):
    live.run_live(settings=tmp_settings, jev="mock", overlay=False, source=FakeSource(count=3), max_frames=3)
    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1 and saved["frames"] >= 1
    assert saved["stage"] == "2-3"


def test_off_backend_gives_fallback_reason(tmp_settings, fake_recognizer, capsys):
    live.run_live(settings=tmp_settings, jev="off", overlay=False, source=FakeSource(count=3), max_frames=3)
    out = capsys.readouterr().out
    assert "Jev 미사용" in out


def test_build_returns_loop_with_thread_runner(tmp_settings, fake_recognizer):
    loop, advisor = live.build(tmp_settings, "mock", None, FakeSource(count=1))
    try:
        assert loop.advisor is advisor
        assert type(loop.runner).__name__ == "ThreadAdviceRunner"
    finally:
        loop.close()


def test_warm_up_does_not_raise_on_broken_advisor():
    class Broken:
        def advise(self, state):
            raise RuntimeError("nope")

        def reset(self):
            pass

    live.warm_up(Broken())   # 예외를 삼킨다


def test_reset_mode_clears_and_reports(tmp_settings, monkeypatch, capsys):
    from tft_advisor.contracts import GameState

    fake = FakeRecognizer([GameState(screen_mode=ScreenMode.GAME_OVER)])
    monkeypatch.setattr("tft_advisor.vision.recognizer.Recognizer", lambda **kw: fake)
    live.run_live(settings=tmp_settings, jev="mock", overlay=False, source=FakeSource(count=3), max_frames=3)
    assert "새 판" in capsys.readouterr().out
