"""게임 창 자동 찾기(`app/game_window.py`) — 창 고르기·좌표 계산·검증·저장·실행 중 적용·버튼.

실제 창 목록은 쓰지 않는다(tests/conftest.py가 TFT_ADVISOR_WINDOW_DETECT=0). 가짜 창 목록 + 가짜 캡처기로
사용자 실제 배치(3440x1440 주 모니터의 (760,156)에 1920x1080 창)를 재현한다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tft_advisor.app import game_window as gw
from tft_advisor.app import setup as core
from tft_advisor.config import load_settings

from .conftest import FakeRecognizer, FakeSource, planning_state

CONFIG_SRC = Path(__file__).resolve().parents[2] / "config"
OWN = 999
GAME = gw.WinInfo(hwnd=11, title="TFT  ", cls="UnrealWindow", pid=30604, client=(760, 156, 1920, 1080))
CLIENT = gw.WinInfo(hwnd=12, title="League of Legends", cls="RCLIENT", pid=10368, client=(1103, 340, 1280, 720))
ADVISOR = gw.WinInfo(hwnd=13, title="TFT Advisor", cls="Qt6112QWindowToolSaveBits", pid=OWN,
                     client=(3035, 24, 380, 550))
RECOG = gw.WinInfo(hwnd=14, title="TFT Advisor — 인식 확인", cls="Qt6112QWindowToolSaveBits", pid=OWN,
                   client=(28, 68, 460, 564))
DISCORD = gw.WinInfo(hwnd=15, title="#general - Discord", cls="Chrome_WidgetWin_1", pid=24896,
                     client=(1015, 175, 1280, 720))
USER_WINDOWS = [CLIENT, ADVISOR, RECOG, DISCORD, GAME]
# mss 번호: 1 = 3440x1440 주 모니터, 2 = 세로 1080x1920 (-1080, 9), 3 = 3840x2160 (3440, 161)
MONITORS = [core.MonitorInfo(1, 0, 0, 3440, 1440, primary=True), core.MonitorInfo(2, -1080, 9, 1080, 1920),
            core.MonitorInfo(3, 3440, 161, 3840, 2160)]
EXPECTED_BOX = (0.22093, 0.108333, 0.77907, 0.858333)


class FakeGrabber(core.MonitorGrabber):
    def __init__(self, monitors=MONITORS, frames: dict[int, np.ndarray] | None = None, value: int = 90) -> None:
        self._monitors = list(monitors)
        self._frames = frames or {}
        self.value = value
        self.grabs: list[int] = []

    def monitors(self):
        return list(self._monitors)

    def grab(self, info):
        self.grabs.append(info.number)
        return self._frames.get(info.number, np.full((info.height, info.width, 3), self.value, np.uint8))

    def close(self):
        pass


def owner_is(win: gw.WinInfo):
    return lambda x, y: (win.hwnd, win.pid)


def find(windows=USER_WINDOWS, *, grabber=None, owner=None, scorer=None):
    return gw.find_game_window(grabber=grabber or FakeGrabber(), windows=lambda: list(windows), own_pid=OWN,
                               owner=owner or owner_is(GAME), scorer=scorer)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    out = tmp_path / "config"
    shutil.copytree(CONFIG_SRC, out, ignore=shutil.ignore_patterns("*.local.toml", "*.bak", "*.tmp"))
    return out


# ---------------------------------------------------------------------------
# 1. 창 고르기
# ---------------------------------------------------------------------------


def test_selects_the_tft_window_and_skips_client_and_our_windows():
    assert gw.select_game_window(USER_WINDOWS, own_pid=OWN) == GAME


def test_title_with_trailing_spaces_and_case_matches():
    assert gw.is_game_window(gw.WinInfo(1, "  tft  ", "X", 1, (0, 0, 1920, 1080)))
    assert gw.is_game_window(gw.WinInfo(1, "League of Legends (TM) Client", "RiotWindowClass", 1, (0, 0, 10, 10)))


def test_riot_class_alone_is_enough_but_unreal_alone_is_not():
    assert gw.is_game_window(gw.WinInfo(1, "", "RiotWindowClass", 1, (0, 0, 10, 10)))
    assert not gw.is_game_window(gw.WinInfo(1, "Some Other Game", "UnrealWindow", 1, (0, 0, 10, 10)))


def test_our_process_and_advisor_titles_are_excluded_even_from_another_process():
    fake = gw.WinInfo(20, "TFT", "UnrealWindow", OWN, (0, 0, 1920, 1080))           # 우리 프로세스
    other = gw.WinInfo(21, "TFT Advisor", "UnrealWindow", 5, (0, 0, 1920, 1080))    # 제목이 우리 앱
    assert gw.select_game_window([fake, other], own_pid=OWN) is None


def test_league_client_is_never_chosen():
    renamed = gw.WinInfo(22, "League of Legends", "RiotWindowClass", 7, (0, 0, 1920, 1080))
    assert gw.select_game_window([CLIENT, renamed], own_pid=OWN) is None


def test_prefers_visible_non_minimized_then_game_class_then_largest():
    small = gw.WinInfo(30, "TFT", "UnrealWindow", 1, (0, 0, 1280, 720))
    big = gw.WinInfo(31, "TFT", "UnrealWindow", 1, (0, 0, 1920, 1080))
    mini = gw.WinInfo(32, "TFT", "UnrealWindow", 1, (0, 0, 2560, 1440), iconic=True)
    other_cls = gw.WinInfo(33, "TFT", "Foo", 1, (0, 0, 3000, 2000))
    hidden = gw.WinInfo(34, "TFT", "UnrealWindow", 1, (0, 0, 3840, 2160), cloaked=True)
    assert gw.select_game_window([small, big, mini, other_cls, hidden]) == big
    assert gw.select_game_window([mini]) == mini   # 최소화된 것만 있으면 그것(→ "최소화" 안내)


# ---------------------------------------------------------------------------
# 2. 좌표 → 모니터 → content_box
# ---------------------------------------------------------------------------


def test_user_layout_maps_to_monitor_1_and_the_hand_computed_box():
    mon = gw.monitor_for_rect(GAME.client, MONITORS)
    assert mon.number == 1
    local, clipped = gw.clip_to_monitor(GAME.client, mon)
    assert local == (760, 156, 1920, 1080) and not clipped
    assert gw.content_box_for(local, mon) == EXPECTED_BOX


def test_window_on_a_negative_coordinate_monitor():
    win = (-1080 + 100, 9 + 200, 800, 450)
    mon = gw.monitor_for_rect(win, MONITORS)
    assert mon.number == 2
    local, _ = gw.clip_to_monitor(win, mon)
    assert local == (100, 200, 800, 450)
    assert gw.content_box_for(local, mon) == (round(100 / 1080, 6), round(200 / 1920, 6),
                                              round(900 / 1080, 6), round(650 / 1920, 6))


def test_window_spanning_two_monitors_goes_to_the_larger_overlap_and_is_clipped():
    win = (3000, 300, 1920, 1080)          # 440px는 1번, 1480px는 3번
    mon = gw.monitor_for_rect(win, MONITORS)
    assert mon.number == 3
    local, clipped = gw.clip_to_monitor(win, mon)
    assert clipped and local == (0, 139, 1480, 1080)


def test_fullscreen_window_means_no_content_box():
    mon = MONITORS[0]
    assert gw.content_box_for((0, 0, 3440, 1440), mon) is None


def test_off_screen_window_has_no_monitor():
    assert gw.monitor_for_rect((20000, 20000, 1920, 1080), MONITORS) is None


def test_occlusion_counts_only_foreign_windows():
    assert gw.occlusion(GAME.client, GAME, OWN, owner_is(GAME)) == 0.0
    assert gw.occlusion(GAME.client, GAME, OWN, owner_is(ADVISOR)) == 0.0     # 우리 창은 가림으로 치지 않는다
    assert gw.occlusion(GAME.client, GAME, OWN, owner_is(DISCORD)) == 1.0

    def left_half_covered(x, y):
        return (DISCORD.hwnd, DISCORD.pid) if x < 760 + 960 else (GAME.hwnd, GAME.pid)

    assert gw.occlusion(GAME.client, GAME, OWN, left_half_covered) == 0.5


# ---------------------------------------------------------------------------
# 3. 찾기 + 검증
# ---------------------------------------------------------------------------


def test_find_reproduces_the_manual_fix():
    res = find()
    assert res.ok, res.message
    assert res.monitor.number == 1 and res.resolution == "1920x1080"
    assert res.content_box == EXPECTED_BOX
    assert res.message == "게임 창을 찾았습니다: 모니터 1, 1920x1080, 위치 (760,156)"
    det = res.detection
    assert det.monitor.number == 1 and det.game_size == (1920, 1080) and det.aspect == "16:9"
    assert det.content_px == (760, 156, 1920, 1080) and det.measured
    assert det.window_note == res.message and det.summary_lines()[0] == res.message
    choice = gw.screen_choice(res, load_settings())
    assert (choice.monitor, choice.resolution, choice.aspect, choice.profile) == (1, "1920x1080", "auto", "auto")


def test_find_uses_ocr_scorer_on_the_cropped_game_area():
    seen = []

    def scorer(img):
        seen.append(img.shape[:2])
        return 1.0

    res = find(scorer=scorer)
    assert res.confirmed and res.detection.game_found
    assert seen == [(1080, 1920)]                       # 모니터 전체가 아니라 창 영역만 채점한다


def test_unconfirmed_hud_still_applies_but_says_so():
    res = find(scorer=lambda img: 0.1)
    assert res.ok and not res.confirmed
    assert any("확인하지 못했습니다" in w for w in res.warnings)


@pytest.mark.parametrize("windows, owner, status, words", [
    ([CLIENT, ADVISOR], None, "not_found", "찾지 못했습니다"),
    ([gw.WinInfo(11, "TFT  ", "UnrealWindow", 1, (0, 0, 0, 0), iconic=True)], None, "minimized", "최소화"),
    ([GAME], owner_is(DISCORD), "occluded", "다른 창에 가려져 있습니다"),
    ([gw.WinInfo(11, "TFT", "UnrealWindow", 1, (0, 0, 400, 300))], None, "too_small", "너무 작습니다"),
    ([gw.WinInfo(11, "TFT", "UnrealWindow", 1, (20000, 0, 1920, 1080))], None, "off_screen", "모니터에도"),
])
def test_failures_are_reported_in_korean_and_not_ok(windows, owner, status, words):
    res = find(windows, owner=owner)
    assert res.status == status and not res.ok
    assert words in res.message
    assert res.detection is None


def test_black_capture_is_rejected():
    res = find(grabber=FakeGrabber(value=0))
    assert res.status == "black" and "검게" in res.message


def test_partial_occlusion_warns_but_applies():
    def corner(x, y):
        return (DISCORD.hwnd, DISCORD.pid) if (x < 1100 and y < 500) else (GAME.hwnd, GAME.pid)

    res = find(owner=corner)
    assert res.ok and 0 < res.occluded < 0.5
    assert any("일부" in w for w in res.warnings)


def test_find_never_raises():
    def boom():
        raise OSError("user32 down")

    res = gw.find_game_window(grabber=FakeGrabber(), windows=boom, own_pid=OWN, owner=owner_is(GAME))
    assert res.status == "error" and "OSError" in res.message


def test_real_enumeration_is_disabled_in_tests_and_reports_not_supported():
    res = gw.find_game_window(grabber=FakeGrabber())
    assert res.status == "not_supported"
    assert gw.current_game_rect() is None


def test_capture_scaled_frame_is_cropped_proportionally():
    """캡처 크기가 모니터 목록 크기와 다르면(배율) 같은 비율로 잘라 검사한다."""
    frames = {1: np.full((720, 1720, 3), 90, np.uint8)}   # 절반 크기 캡처
    seen = []
    res = find(grabber=FakeGrabber(frames=frames), scorer=lambda img: seen.append(img.shape[:2]) or 1.0)
    assert res.ok and seen == [(540, 960)]


# ---------------------------------------------------------------------------
# 4. 설정 화면 감지가 창을 먼저 본다(버그 수정: 창 모드인데 모니터 전체로 저장하던 문제)
# ---------------------------------------------------------------------------


def test_setup_detect_live_prefers_the_game_window():
    settings = load_settings()
    finder = lambda grabber, scorer: find(grabber=grabber, scorer=scorer)   # noqa: E731
    det = core.detect_live(settings, grabber=FakeGrabber(), window_finder=finder)
    assert det.content_box == EXPECTED_BOX and det.game_size == (1920, 1080)
    choice = core.choice_from_detection(det, settings)
    assert choice.resolution == "1920x1080" and choice.aspect == "auto" and choice.monitor == 1


def test_setup_detect_live_falls_back_to_pixels_with_a_note():
    settings = load_settings()
    finder = lambda grabber, scorer: find([CLIENT], grabber=grabber, scorer=scorer)   # noqa: E731
    det = core.detect_live(settings, grabber=FakeGrabber(), window_finder=finder)
    assert det.window_note is None
    assert any("픽셀로 감지" in n for n in det.notes)


def test_setup_detect_live_not_supported_is_silent():
    det = core.detect_live(load_settings(), grabber=FakeGrabber())   # 테스트 환경 = 지원 안 함
    assert not any("게임 창" in n for n in det.notes)


# ---------------------------------------------------------------------------
# 5. 저장 + 실행 중 적용
# ---------------------------------------------------------------------------


def test_persist_writes_only_screen_keys_and_setup_state(config_dir, tmp_path):
    settings = load_settings(config_dir)
    before = (config_dir / "settings.toml").read_text(encoding="utf-8")
    res = find()
    fresh = gw.persist(res, settings, config_dir=config_dir, state_dir=tmp_path / "state")
    after = load_settings(config_dir)
    assert after.capture.monitor == 1 and after.vision.resolution == "1920x1080"
    assert after.vision.content_box == pytest.approx(EXPECTED_BOX)
    assert after.vision.aspect == "auto" and after.vision.profile == "auto"
    assert after.advisor.jev_backend == settings.advisor.jev_backend           # 다른 섹션은 그대로
    assert after.overlay == settings.overlay and after.ui == settings.ui
    assert fresh.vision.content_box == pytest.approx(EXPECTED_BOX)
    assert core.setup_completed(tmp_path / "state")
    assert (config_dir / "settings.toml").read_text(encoding="utf-8") == before      # 공용 파일은 그대로
    local = (config_dir / "settings.local.toml").read_text(encoding="utf-8")
    assert "content_box" in local and "jev_backend" not in local                      # 화면 키만 로컬 층에
    assert "follow_game_window" in before


def fake_loop(settings):
    from tft_advisor.app.loop import LiveLoop

    class Src(FakeSource):
        monitors: list = []

        def set_monitor(self, m):
            self.monitors.append(m)

    return LiveLoop(source=Src(), recognizer=FakeRecognizer([planning_state()]), settings=settings)


def test_redetector_applies_to_the_running_loop_on_the_capture_thread(config_dir, tmp_path):
    settings = load_settings(config_dir).model_copy()
    settings = core.apply_updates(settings, {"capture": {"monitor": 1},
                                             "vision": {"content_box": [0.0, 0.0056, 1.0, 1.0],
                                                        "resolution": "3440x1432", "aspect": "21:9"}})
    loop = fake_loop(settings)
    red = gw.ScreenRedetector(settings, config_dir=config_dir, state_dir=tmp_path, follow=False,
                              finder=lambda scorer=None: find(scorer=scorer))
    loop.screen_hook = red
    got = []
    red.request(got.append)
    assert got == [] and loop.screen_changes == 0              # 요청만 — 실제 일은 캡처 스레드의 step()에서
    loop.step()
    assert len(got) == 1 and got[0].ok and got[0].applied and got[0].saved is not None
    assert loop.screen_changes == 1 and loop.source.monitors == [1]
    assert loop.settings.vision.content_box == pytest.approx(EXPECTED_BOX)
    assert loop.settings.vision.aspect == "auto" and loop.settings.vision.resolution == "1920x1080"
    assert load_settings(config_dir).vision.content_box == pytest.approx(EXPECTED_BOX)
    # 한 번 더 누르면 "이미 같다" — 저장·적용하지 않는다
    red.request(got.append)
    loop.step()
    assert got[1].ok and got[1].unchanged and not got[1].applied and loop.screen_changes == 1
    assert "이미" in got[1].text()


def test_redetector_failure_changes_nothing(config_dir, tmp_path):
    settings = load_settings(config_dir)
    loop = fake_loop(settings)
    saved = []
    red = gw.ScreenRedetector(settings, config_dir=config_dir, state_dir=tmp_path, follow=False,
                              finder=lambda scorer=None: find(owner=owner_is(DISCORD)), saver=saved.append)
    res = red.redetect(loop)
    assert res.status == "occluded" and saved == [] and loop.screen_changes == 0


def test_apply_screen_resets_recognizer_caches_and_detector():
    from tft_advisor.app.loop import LiveLoop

    class Rec(FakeRecognizer):
        pinned_profile = None

        def __init__(self):
            super().__init__([planning_state()])
            self.cfg = load_settings().vision
            self.profile_setting = "old"
            self._screen_cache = {"k": 1}
            self._panel_cache = ("x",)

    rec = Rec()
    loop = LiveLoop(source=FakeSource(), recognizer=rec, settings=load_settings())
    old_detector = loop.detector
    new = core.apply_updates(load_settings(), {"capture": {"monitor": 2},
                                               "vision": {"content_box": list(EXPECTED_BOX), "resolution": "1920x1080",
                                                          "aspect": "auto", "profile": "auto"}})
    loop.apply_screen(new)
    assert rec.cfg.content_box == pytest.approx(EXPECTED_BOX) and rec.profile_setting == "1920x1080"
    assert rec._screen_cache == {} and rec._panel_cache is None
    assert loop.detector is not old_detector and loop.settings.capture.monitor == 2
    assert loop.settings.advisor == load_settings().advisor


def test_mss_source_set_monitor_switches_on_next_grab():
    from tft_advisor.vision import capture

    class FakeSct:
        monitors = [{"left": 0, "top": 0, "width": 30, "height": 10},
                    {"left": 0, "top": 0, "width": 10, "height": 10}, {"left": 10, "top": 0, "width": 20, "height": 10}]

        def grab(self, box):
            return np.zeros((box["height"], box["width"], 4), np.uint8)

        def close(self):
            pass

    src = capture.MssSource(monitor=1)
    src._sct = FakeSct()
    assert src.grab().size == (10, 10)
    src.set_monitor(2)
    assert src.grab().size == (20, 10) and src.monitor == 2


# ---------------------------------------------------------------------------
# 6. 자동 따라가기
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_follow_applies_once_the_moved_window_stays_put(config_dir, tmp_path):
    # 지금 설정 = 사용자 창 위치(메인 세션이 손으로 맞춘 값)
    settings = core.apply_updates(load_settings(config_dir), {
        "capture": {"monitor": 1},
        "vision": {"resolution": "1920x1080", "aspect": "auto", "profile": "auto", "content_box": list(EXPECTED_BOX)}})
    loop = fake_loop(settings)
    clock = Clock()
    where = {"win": GAME}
    finds = []

    def finder(scorer=None):
        finds.append(where["win"].client)
        return find([where["win"]], owner=owner_is(where["win"]), scorer=scorer)

    followed = []
    red = gw.ScreenRedetector(settings, config_dir=config_dir, state_dir=tmp_path, follow=True, interval_s=3,
                              finder=finder, peek=lambda: where["win"], on_follow=followed.append, clock=clock)
    red.tick(loop)                      # 처음 본 위치 — 한 번 더 확인
    assert finds == []
    clock.t += gw.FOLLOW_CONFIRM_S
    red.tick(loop)                      # 지금 설정과 같다 → 저장 없이 기억만
    assert len(finds) == 1 and red.last.unchanged and followed == [] and loop.screen_changes == 0
    clock.t += 3
    red.tick(loop)
    assert len(finds) == 1              # 그대로면 찾지 않는다(창 목록만 본다)

    moved = gw.WinInfo(11, "TFT  ", "UnrealWindow", 30604, (100, 100, 1600, 900))
    where["win"] = moved
    clock.t += 3
    red.tick(loop)                      # 옮기는 중일 수 있다 → 아직 적용하지 않는다
    assert len(finds) == 1
    clock.t += gw.FOLLOW_CONFIRM_S
    red.tick(loop)
    assert len(finds) == 2 and len(followed) == 1 and followed[0].ok and loop.screen_changes == 1
    assert loop.settings.vision.resolution == "1600x900"
    assert load_settings(config_dir).vision.resolution == "1600x900"


def test_follow_does_not_hammer_a_failing_position(config_dir, tmp_path):
    settings = load_settings(config_dir)
    loop = fake_loop(settings)
    clock = Clock()
    moved = gw.WinInfo(11, "TFT  ", "UnrealWindow", 30604, (100, 100, 1600, 900))
    calls = []

    def finder(scorer=None):
        calls.append(1)
        return find([moved], owner=owner_is(DISCORD))      # 가려져 있다

    red = gw.ScreenRedetector(settings, config_dir=config_dir, state_dir=tmp_path, follow=True, interval_s=3,
                              finder=finder, peek=lambda: moved, clock=clock)
    for _ in range(6):
        red.tick(loop)
        clock.t += 3
    assert len(calls) == 1 and loop.screen_changes == 0
    clock.t += gw.FOLLOW_RETRY_S
    red.tick(loop)
    clock.t += gw.FOLLOW_CONFIRM_S
    red.tick(loop)
    assert len(calls) == 2


def test_follow_ignores_minimized_window(config_dir, tmp_path):
    settings = load_settings(config_dir)
    clock = Clock()
    mini = gw.WinInfo(11, "TFT", "UnrealWindow", 1, (0, 0, 0, 0), iconic=True)
    red = gw.ScreenRedetector(settings, config_dir=config_dir, state_dir=tmp_path, follow=True, interval_s=3,
                              finder=lambda scorer=None: pytest.fail("최소화면 찾지 않는다"), peek=lambda: mini,
                              clock=clock)
    for _ in range(3):
        red.tick(None)
        clock.t += 3


def test_follow_is_off_when_not_supported(config_dir, tmp_path):
    red = gw.ScreenRedetector(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path)
    assert red.follow is False             # 테스트 환경(= 창 목록 없음)


def test_request_apply_pushes_saved_settings_into_the_loop(config_dir, tmp_path):
    settings = load_settings(config_dir)
    loop = fake_loop(settings)
    red = gw.ScreenRedetector(settings, config_dir=config_dir, state_dir=tmp_path, follow=False)
    new = core.apply_updates(settings, {"capture": {"monitor": 3}})
    red.request_apply(new)
    red.tick(loop)
    assert loop.settings.capture.monitor == 3 and loop.screen_changes == 1


def test_config_follow_keys():
    s = load_settings()
    assert s.capture.follow_game_window is True and s.capture.follow_interval_s == 3.0


# ---------------------------------------------------------------------------
# 7. CLI --redetect
# ---------------------------------------------------------------------------


def test_cli_redetect(config_dir, tmp_path, capsys, monkeypatch):
    from tft_advisor import __main__ as cli

    settings = core.apply_updates(load_settings(config_dir), {"app": {"state_dir": str(tmp_path / "state")}})
    code = cli.run_redetect(settings, config_dir, finder=lambda scorer=None: find(scorer=scorer))
    out = capsys.readouterr().out
    assert code == 0 and "게임 창을 찾았습니다: 모니터 1, 1920x1080, 위치 (760,156)" in out
    code = cli.run_redetect(settings, config_dir, finder=lambda scorer=None: find([CLIENT]))
    assert code == 1 and "찾지 못했습니다" in capsys.readouterr().out
    assert cli.build_parser().parse_args(["--redetect"]).redetect is True


# ---------------------------------------------------------------------------
# 8. 버튼 (Qt offscreen)
# ---------------------------------------------------------------------------


def test_setup_dialog_window_button_fills_monitor_resolution_and_box(qapp, config_dir, tmp_path):
    from tft_advisor.app.setup_dialog import SetupDialog

    d = SetupDialog(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path, grabber=FakeGrabber(),
                    window_finder=lambda grabber, scorer: find([CLIENT], grabber=grabber))
    assert "게임 창" in d.window_btn.text()
    d.window_finder = lambda grabber, scorer: find(grabber=grabber)
    d._recognizer = type("R", (), {"screen_score": staticmethod(lambda img: 1.0), "cfg": None,
                                   "profile_setting": None})()
    res = d.find_window()
    assert res.ok and "게임 창을 찾았습니다" in d.status.text()
    choice = d.current_choice()
    assert choice.monitor == 1 and choice.resolution == "1920x1080"
    assert choice.content_box == pytest.approx(EXPECTED_BOX)
    d.window_finder = lambda grabber, scorer: find(owner=owner_is(DISCORD), grabber=grabber)
    res = d.find_window()
    assert res.status == "occluded" and "가려져" in d.status.text()
    assert d.current_choice().content_box == pytest.approx(EXPECTED_BOX)     # 실패는 위젯을 바꾸지 않는다
    d.deleteLater()


def test_overlay_menu_and_recog_window_button(qapp, config_dir, tmp_path):
    from tft_advisor.app.overlay import OverlayWindow
    from tft_advisor.app.recog_window import REDETECT_TEXT, RecogController

    settings = load_settings(config_dir)
    red = gw.ScreenRedetector(settings, config_dir=config_dir, state_dir=tmp_path, follow=False,
                              finder=lambda scorer=None: find(scorer=scorer))
    w = OverlayWindow(settings, state_dir=tmp_path, config_dir=config_dir)
    texts = [a.text() for a in w.menu().actions()]
    assert REDETECT_TEXT not in texts                          # 실시간 캡처가 아니면 항목 없음
    w.attach_redetector(red)
    ctl = RecogController(settings, state_dir=tmp_path, config_dir=config_dir, cli=False, names=w.names,
                          on_redetect=w.request_redetect)
    w.attach_recog(ctl)
    menu = w.menu()
    action = next(a for a in menu.actions() if a.text() == REDETECT_TEXT)
    ctl.set_enabled(True, persist=False)
    win = ctl.window
    assert win.redetect_btn.isVisibleTo(win) and win.redetect_btn.text() == REDETECT_TEXT
    win.redetect_btn.click()                                    # 버튼 → 요청(캡처 스레드 몫)
    assert not win.redetect_btn.isEnabled() and "찾는 중" in w.status.extra
    loop = fake_loop(settings)
    loop.screen_hook = red
    loop.step()                                                 # 캡처 스레드 역할
    qapp.processEvents()
    assert w.last_redetect is not None and w.last_redetect.ok
    assert "게임 창을 찾았습니다" in w.status.extra
    assert win.redetect_btn.isEnabled() and "게임 창을 찾았습니다" in win.redetect_label.text()
    action.trigger()                                            # 트레이 메뉴 항목도 같은 요청
    loop.step()
    qapp.processEvents()
    assert "이미" in win.redetect_label.text()
    ctl.set_enabled(False, persist=False)
    w.deleteLater()
