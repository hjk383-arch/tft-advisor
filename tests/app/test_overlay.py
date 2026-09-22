"""오버레이: 헤드리스(QT_QPA_PLATFORM=offscreen)에서 만들고 Recommendation을 먹인다.

여기서는 창을 띄우지 않는다(offscreen에서도 show()는 되지만 의미가 없다). 검증 대상은
**표시 내용·순서·조작 API**다. 실제 클릭 통과는 플랫폼 동작이라 `WindowEffect`로만 확인한다.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tft_advisor.app.loop import LoopUpdate
from tft_advisor.app.overlay import OverlayWindow
from tft_advisor.app.platform_window import apply_always_on_top, apply_click_through, click_through_note
from tft_advisor.app.report import StatusInfo
from tft_advisor.contracts import GameState, ScreenMode

from .conftest import planning_state, sample_recommendation


@pytest.fixture
def window(qapp, settings, tmp_path):
    w = OverlayWindow(settings, state_dir=tmp_path)
    yield w
    w.deleteLater()


def test_constructs_headless(window, settings):
    assert window.width() == settings.overlay.width
    assert "TFT Advisor" in window.body.text()


def test_shows_recommendation_in_advisor_order(window):
    window.set_data(planning_state(), sample_recommendation())
    body = window.body.text()
    assert body.index("가짜 덱 A") < body.index("가짜 덱 B")   # 점수(0.41 < 0.77)로 재정렬하지 않는다
    assert "[목표 덱]" in body and "[상점]" in body
    assert "준비" in body and "스테이지 2-3" in body


def test_respects_max_target_comps(qapp, settings, tmp_path):
    cfg = settings.model_copy(update={"ui": settings.ui.model_copy(update={"max_target_comps": 1})})
    w = OverlayWindow(cfg, state_dir=tmp_path)
    w.set_data(planning_state(), sample_recommendation())
    assert "가짜 덱 A" in w.body.text() and "가짜 덱 B" not in w.body.text()
    w.deleteLater()


def test_status_line_shows_patch_and_backend(window):
    window.status = StatusInfo(patch="18.2b", backend="mock")
    window.set_data(planning_state(), sample_recommendation())
    assert "18.2b" in window.foot.text() and "mock" in window.foot.text()


def test_loop_update_is_applied(window):
    rec = sample_recommendation()
    window._on_update_main(LoopUpdate(kind="advice", state=planning_state(), recommendation=rec,
                                      at=datetime.now(UTC)))
    assert "가짜 덱 A" in window.body.text()
    assert window.status.updated_at is not None


def test_reset_update_clears_recommendation(window):
    window.set_data(planning_state(), sample_recommendation())
    window._on_update_main(LoopUpdate(kind="reset", state=GameState(screen_mode=ScreenMode.LOADING)))
    assert "가짜 덱 A" not in window.body.text()


def test_error_update_goes_to_status(window):
    window._on_update_main(LoopUpdate(kind="error", message="인식 실패: 테스트"))
    assert "인식 실패" in window.foot.text()


def test_warnings_reach_status(window):
    window._on_update_main(LoopUpdate(kind="recognized", state=planning_state()))
    assert "미인식" in window.foot.text()


def test_lock_toggle_and_effect(window):
    window.apply_lock(True)
    assert window.click_effect is not None
    window.toggle_locked()
    assert window._locked is False
    assert window.status.extra == "이동 가능"


def test_position_is_saved_and_reloaded(window, tmp_path):
    window.move(111, 222)
    window.save_position()
    saved = json.loads((tmp_path / "overlay.json").read_text(encoding="utf-8"))
    assert saved["x"] == 111 and saved["y"] == 222
    assert window.load_position() == (111, 222)


def test_opacity_is_clamped(window):
    for _ in range(40):
        window.adjust_opacity(-0.05)
    assert window.windowOpacity() >= 0.2
    for _ in range(40):
        window.adjust_opacity(0.05)
    assert window.windowOpacity() <= 1.0


def test_menu_has_expected_actions(window):
    texts = [a.text() for a in window.menu().actions() if a.text()]
    assert any("표시/숨기기" in t for t in texts)
    assert any("잠금" in t for t in texts)
    assert any("위치 저장" in t for t in texts)
    assert any("종료" in t for t in texts)


def test_platform_helpers_report_result(window):
    assert apply_always_on_top(window, True).applied
    effect = apply_click_through(window, True)
    assert effect.method in ("qt", "win32", "none")
    assert click_through_note()


def test_no_recommendation_shows_waiting_text(window):
    window.set_data(None, None)
    assert "추천 대기" in window.body.text()
