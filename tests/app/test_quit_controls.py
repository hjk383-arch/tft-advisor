"""보이는 종료 버튼: 오버레이 옆 "✕ 종료" 손잡이(클릭 통과와 무관한 별도 창) · 인식 확인 창 [앱 종료] · 시작 안내.

모든 경로는 `OverlayWindow.quit()` → `app.quit()` 한 곳으로 간다(종료 처리는 aboutToQuit에서 한 번).
"""
from __future__ import annotations

import pytest

from tft_advisor.app.overlay import QUIT_HINT, ConfirmButton, OverlayWindow, QuitHandle
from tft_advisor.app.recog_window import RecogController


@pytest.fixture
def quits(monkeypatch, qapp):
    calls = []
    monkeypatch.setattr(qapp, "quit", lambda: calls.append(1))
    return calls


def test_confirm_button_needs_two_clicks(qapp):
    hits = []
    b = ConfirmButton("✕ 종료", "정말 종료? 다시 클릭", lambda: hits.append(1), timeout_ms=50)
    b.click()
    assert hits == [] and b.armed and "정말" in b.text()
    b.click()
    assert hits == [1] and not b.armed and b.text() == "✕ 종료"
    b.click()
    b.disarm()                                   # 시간이 지나면 풀린다(타이머와 같은 처리)
    b.click()
    assert hits == [1]


def test_quit_handle_is_a_separate_clickable_window_next_to_the_overlay(qapp, settings, tmp_path, quits):
    w = OverlayWindow(settings, state_dir=tmp_path)
    w.show_overlay()
    h = w.quit_handle
    assert isinstance(h, QuitHandle) and h.isVisible() and h is not w
    from PySide6.QtCore import Qt

    assert not h.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # 잠금(클릭 통과)과 무관
    assert not (h.windowFlags() & Qt.WindowType.WindowTransparentForInput)
    g = w.frameGeometry()
    assert abs(h.frameGeometry().right() - g.right()) <= 1                          # 오버레이 오른쪽에 붙는다
    w.move(w.x() + 40, w.y() + 30)
    qapp.processEvents()
    assert abs(h.frameGeometry().right() - w.frameGeometry().right()) <= 1          # 따라 움직인다
    w.toggle_visible()
    assert not h.isVisible()
    w.toggle_visible()
    assert h.isVisible()
    h.button.click()
    assert quits == []
    h.button.click()
    assert quits == [1] and not h.isVisible()
    w.hide()
    w.deleteLater()


def test_startup_hint_in_footer_and_log(qapp, settings, tmp_path, caplog):
    import logging

    w = OverlayWindow(settings, state_dir=tmp_path)
    with caplog.at_level(logging.INFO):
        w.show_overlay()
    assert QUIT_HINT in w.foot.text() or QUIT_HINT in (w.status.extra or "")
    assert any(QUIT_HINT in r.getMessage() for r in caplog.records)
    w.quit_handle.hide()
    w.hide()
    w.deleteLater()


def test_recog_window_quit_button_goes_through_overlay_quit(qapp, settings, tmp_path, quits):
    w = OverlayWindow(settings, state_dir=tmp_path)
    ctl = RecogController(settings, state_dir=tmp_path, cli=False, names=w.names, on_quit=w.quit)
    ctl.set_enabled(True, persist=False)
    btn = ctl.window.quit_btn
    assert btn.isVisibleTo(ctl.window) and btn.text() == "앱 종료"
    btn.click()
    assert quits == []
    btn.click()
    assert quits == [1]
    ctl.set_enabled(False, persist=False)
    w.deleteLater()


def test_recog_window_without_quit_callback_has_no_button(qapp, settings, tmp_path):
    ctl = RecogController(settings, state_dir=tmp_path, cli=False)
    ctl.set_enabled(True, persist=False)
    assert not ctl.window.quit_btn.isVisibleTo(ctl.window)
    ctl.set_enabled(False, persist=False)
