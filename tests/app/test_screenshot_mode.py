"""`--screenshot` 모드 end-to-end: 실제 캡처를 인식해 한국어 요약을 출력한다.

값(덱 이름·골드 등)은 **고정하지 않는다** — 인식·통계·가중치가 바뀌면 달라진다. 확인하는 것은
"죽지 않고, 한국어 요약을 내고, 계약을 지키는가"다. 정확도 측정은 `vision.evaluate`와 QA 몫이다.

사용자 원본 캡처(`tests/fixtures/screens/raw/`)는 gitignore라 다른 체크아웃에는 없다 → 있으면 돌리고 없으면 skip.
출력에 소환사 이름은 들어가지 않는다(vision이 플레이어 목록에서 읽는 것은 체력 숫자뿐이다).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tft_advisor.app.screenshot import collect_images, run_screenshot

from .conftest import RAW, SCREENS

TRACKED = sorted(SCREENS.glob("*.png"))
RAW_IMAGES = sorted(RAW.glob("*.png")) if RAW.is_dir() else []


@pytest.fixture(scope="module")
def advisor(settings):
    from tft_advisor.advisor import create_advisor

    adv = create_advisor("mock", settings=settings)
    yield adv
    adv.close()


def run(paths, recognizer, advisor, settings, debug_dir=None) -> list[str]:
    lines: list[str] = []
    code = run_screenshot(Path("(테스트)"), paths=paths, settings=settings, recognizer=recognizer,
                          advisor=advisor, out=lines.append, debug_dir=debug_dir)
    assert code == 0
    return lines


def assert_korean_summary(text: str) -> None:
    assert "인식 품질" in text and "신뢰도" in text
    assert "[목표 덱]" in text or "추천" in text


@pytest.mark.parametrize("path", TRACKED, ids=[p.stem for p in TRACKED])
def test_tracked_fixture_screens(path, recognizer, advisor, settings):
    text = "\n".join(run([path], recognizer, advisor, settings))
    assert path.name in text
    assert_korean_summary(text)


@pytest.mark.skipif(not RAW_IMAGES, reason="사용자 원본 캡처 없음(tests/fixtures/screens/raw/, gitignore)")
def test_raw_user_captures_folder(recognizer, advisor, settings):
    """사용자 실캡처 6장을 폴더로 한 번에 — 추천기 하나를 이어 쓴다(실제 스트림과 같은 경로)."""
    text = "\n".join(run(RAW_IMAGES, recognizer, advisor, settings))
    for p in RAW_IMAGES:
        assert p.name in text
    assert text.count("[목표 덱]") >= len(RAW_IMAGES) - 2   # 전투/unknown 화면은 추천이 없을 수 있다
    assert_korean_summary(text)


def test_missing_path_returns_2(settings, recognizer, advisor, tmp_path):
    lines: list[str] = []
    assert run_screenshot(tmp_path / "없는파일.png", settings=settings, recognizer=recognizer,
                          advisor=advisor, out=lines.append) == 2
    assert "찾지 못했습니다" in lines[0]


def test_collect_images_from_folder_and_file():
    assert collect_images(SCREENS) == TRACKED
    assert collect_images(TRACKED[0]) == [TRACKED[0]]


def test_debug_dump_writes_state_and_rois(recognizer, advisor, settings, tmp_path):
    run([TRACKED[0]], recognizer, advisor, settings, debug_dir=tmp_path)
    assert (tmp_path / f"{TRACKED[0].stem}.state.json").is_file()
    assert (tmp_path / f"{TRACKED[0].stem}.rois.png").is_file()


def test_unreadable_file_does_not_stop_the_batch(recognizer, advisor, settings, tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    text = "\n".join(run([broken, TRACKED[0]], recognizer, advisor, settings))
    assert "읽지 못했습니다" in text and TRACKED[0].name in text
