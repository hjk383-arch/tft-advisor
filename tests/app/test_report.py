"""표시 문구: advisor 순서 유지, 한국어 라벨, 인식 경고, 상태줄."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tft_advisor.app.names import NameBook, strip_markup
from tft_advisor.app.report import (
    StatusInfo, age_text, augment_lines, comp_lines, format_report, jev_label, recognition_warnings,
    shop_lines, state_line, status_line,
)
from tft_advisor.contracts import (
    AugmentAdvice, AugmentChoice, FallbackReason, GameState, Recommendation, ScreenMode,
)

from .conftest import planning_state, sample_recommendation, shop_slots


def test_target_comps_are_never_reordered_by_score():
    """advisor 순서 그대로 보여 준다(점수가 단조가 아닐 수 있다)."""
    rec = sample_recommendation()
    assert [c.score for c in rec.target_comps] == [0.41, 0.77]   # 일부러 오름차순
    text = format_report(planning_state(), rec, names=NameBook())
    assert text.index("가짜 덱 A") < text.index("가짜 덱 B")
    assert "1. 가짜 덱 A" in text and "2. 가짜 덱 B" in text


def test_state_line_is_korean():
    line = state_line(planning_state(streak=-3))
    assert "준비" in line and "스테이지 2-3" in line and "3연패" in line


def test_shop_lines_mark_buy_and_reason_tag():
    lines = shop_lines(sample_recommendation(), NameBook())
    assert lines[0].startswith("1. [구매]") and "지금 전력" in lines[0]
    assert lines[1].startswith("2. [보류]")


def test_augment_pick_is_starred():
    rec = Recommendation(jev_used=True, augment=AugmentAdvice(
        choices=[AugmentChoice(augment_id="DA_SpreadingRoots", score=0.9, editorial_tier="S"),
                 AugmentChoice(augment_id="DA_LateGameScaling", score=0.5)],
        pick="DA_LateGameScaling"))
    lines = augment_lines(rec, NameBook())
    assert not lines[0].startswith("★") and lines[1].startswith("★")


def test_jev_label_distinguishes_carousel_from_failure():
    caro = Recommendation(jev_used=False, fallback_reason=FallbackReason.JEV_DISABLED,
                          debug={"mode": ScreenMode.CAROUSEL.value})
    assert jev_label(caro) == "캐러셀(통계)"
    fail = Recommendation(jev_used=False, fallback_reason=FallbackReason.TIMEOUT)
    assert jev_label(fail) == "Jev 시간 초과"
    assert jev_label(None) == "추천 없음"


def test_recognition_warnings_list_missing_and_low_confidence():
    state = planning_state(shop=shop_slots(["A"] * 5), confidence={"gold": 0.3})
    warns = recognition_warnings(state, 0.6)
    assert any("미인식" in w and "아이템" in w for w in warns)
    assert any("낮은 신뢰도" in w and "골드" in w for w in warns)


def test_unknown_screen_is_reported():
    warns = recognition_warnings(GameState(screen_mode=ScreenMode.UNKNOWN), 0.6)
    assert "화면 판별 실패" in warns


def test_status_line_has_patch_backend_age():
    now = datetime.now(UTC)
    info = StatusInfo(patch="18.2b", backend="mock", rec=sample_recommendation(),
                      updated_at=now - timedelta(seconds=90), warnings=["미인식: 보드"])
    line = status_line(info, now)
    assert "패치 18.2b" in line and "mock" in line and "2분 전" in line and "⚠" in line


def test_age_text_without_update():
    assert age_text(None) == "갱신 없음"


def test_report_without_recommendation_explains_mode():
    text = format_report(GameState(screen_mode=ScreenMode.COMBAT), None)
    assert "직전 추천을 유지" in text


def test_names_strip_client_markup():
    assert strip_markup("그웬의 가위 <rules>(2회 사용 가능!)</rules>") == "그웬의 가위 (2회 사용 가능!)"


def test_namebook_falls_back_to_id():
    assert NameBook().name("존재하지_않는_ID") == "존재하지_않는_ID"
    assert NameBook().name(None) == "?"


def test_comp_lines_note_unknown_board():
    rec = sample_recommendation()
    lines = comp_lines(rec.target_comps[1], 2, NameBook())
    assert any("보드 미인식" in ln for ln in lines)
