"""실행 전 설정(셋업): 해상도·모니터 자동 감지, settings.toml 저장, 첫 실행 게이팅, 콘솔·Qt UI.

실제 화면은 쓰지 않는다 — 모니터 목록과 캡처 프레임을 가짜로 넣는다(`FakeGrabber`).
Qt 대화상자는 헤드리스(`QT_QPA_PLATFORM=offscreen`, conftest에서 지정)로 만들고 버튼 슬롯을 직접 부른다.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from tft_advisor import __main__ as cli
from tft_advisor.app import setup as core
from tft_advisor.config import load_settings

from .conftest import FakeRecognizer, planning_state

CONFIG_SRC = Path(__file__).resolve().parents[2] / "config"


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


def mss_list(*sizes: tuple[int, int], virtual: bool = True) -> list[dict]:
    """(w, h) 목록 → mss 스타일 monitors 목록(0번 = 가상 전체 화면). 모니터를 가로로 이어 붙인다."""
    mons, left = [], 0
    for w, h in sizes:
        mons.append({"left": left, "top": 0, "width": w, "height": h})
        left += w
    total_h = max(h for _, h in sizes)
    return ([{"left": 0, "top": 0, "width": left, "height": total_h}] + mons) if virtual else mons


def flat(w: int, h: int, value: int = 90) -> np.ndarray:
    return np.full((h, w, 3), value, np.uint8)


def letterboxed(w: int, h: int, bar: int) -> np.ndarray:
    """위아래에 검은 띠가 있는 프레임(레터박스)."""
    img = np.zeros((h, w, 3), np.uint8)
    img[bar:h - bar] = 90
    return img


class FakeGrabber(core.MonitorGrabber):
    """모니터 목록과 프레임을 미리 정해 두는 캡처기(mss를 열지 않는다)."""

    def __init__(self, monitors: list[core.MonitorInfo], frames: dict[int, np.ndarray] | None = None) -> None:
        self._monitors = monitors
        self._frames = frames or {}
        self.closed = False

    def monitors(self) -> list[core.MonitorInfo]:
        return list(self._monitors)

    def grab(self, info: core.MonitorInfo) -> np.ndarray:
        return self._frames.get(info.number, flat(info.width, info.height))

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """진짜 config/를 복사한 작업용 설정 디렉터리(주석 보존 검증에 원문이 필요하다)."""
    out = tmp_path / "config"
    shutil.copytree(CONFIG_SRC, out)
    return out


# ---------------------------------------------------------------------------
# 1. 모니터 목록
# ---------------------------------------------------------------------------


def test_single_monitor_list_uses_index_zero():
    """모니터가 하나뿐인 mss 목록(가상 화면 없음)은 0번을 그대로 쓴다 — MssSource와 같은 번호 규칙."""
    mons = core.monitors_from_mss(mss_list((1920, 1080), virtual=False))
    assert [m.number for m in mons] == [0]
    assert mons[0].primary and mons[0].size == (1920, 1080)


def test_dual_monitor_numbering_and_primary():
    mons = core.monitors_from_mss(mss_list((1920, 1080), (2560, 1440)))
    assert [m.number for m in mons] == [1, 2]
    assert mons[0].primary and not mons[1].primary
    assert mons[1].left == 1920


def test_empty_monitor_list():
    assert core.monitors_from_mss([]) == []


def test_primary_falls_back_to_first_when_no_monitor_is_at_origin():
    mons = core.monitors_from_mss([{"left": -1920, "top": 0, "width": 3840, "height": 1080},
                                   {"left": -1920, "top": 0, "width": 1920, "height": 1080},
                                   {"left": 0, "top": 100, "width": 1920, "height": 1080}])
    assert mons[0].primary and not mons[1].primary


def test_hidpi_qt_scale_is_merged_by_physical_size():
    """고DPI: Qt는 논리 크기(1512x982 @2x), mss는 실제 픽셀(3024x1964)을 준다 → 배율로 맞춘다."""
    mons = core.monitors_from_mss(mss_list((3024, 1964), (1920, 1080)))
    screens = [core.QtScreenInfo(left=0, top=0, width=1512, height=982, dpr=2.0, name="Built-in"),
               core.QtScreenInfo(left=1512, top=0, width=1920, height=1080, dpr=1.0, name="DELL")]
    merged = core.merge_qt_screens(mons, screens)
    assert merged[0].scale == 2.0 and merged[0].name == "Built-in"
    assert merged[1].scale == 1.0 and merged[1].name == "DELL"
    assert "배율 2x" in merged[0].label()


def test_qt_screens_that_match_nothing_leave_scale_alone():
    mons = core.monitors_from_mss(mss_list((1920, 1080), virtual=False))
    merged = core.merge_qt_screens(mons, [core.QtScreenInfo(left=99, top=99, width=800, height=600)])
    assert merged[0].scale == 1.0 and merged[0].name == ""


# ---------------------------------------------------------------------------
# 2. 감지 — 비율 / 프로파일
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("size", "aspect", "profile", "measured"), [
    ((1920, 1080), "16:9", "set18_16x9", True),
    ((2560, 1440), "16:9", "set18_16x9", True),
    ((1920, 1200), "16:10", "set18_16x10", True),
    ((1280, 800), "16:10", "set18_16x10", True),
    ((2560, 1080), "21:9", "set18_21x9", False),
    ((1600, 1200), "4:3", "set18_4x3", False),
])
def test_detect_picks_aspect_and_profile(size, aspect, profile, measured):
    mons = core.monitors_from_mss(mss_list(size, virtual=False))
    det = core.detect(mons, lambda i: flat(*size))
    assert det.game_size == size
    assert det.aspect == aspect
    assert det.profile_name == profile
    assert det.measured is measured
    assert any("유도" in w for w in det.warnings) is (not measured)   # 미검증 비율만 경고한다


def test_unknown_aspect_warns():
    det = core.detect(core.monitors_from_mss(mss_list((1000, 1000), virtual=False)), lambda i: flat(1000, 1000))
    assert det.aspect is None
    assert any("지원 목록" in w for w in det.warnings)


def test_an_extreme_ratio_falls_back_to_16x9_instead_of_crashing():
    """1:1 처럼 16:9에서 ROI를 유도할 수 없는 비율 — 감지가 예외로 죽지 않고 경고하고 넘어간다."""
    det = core.detect(core.monitors_from_mss(mss_list((1000, 1000), virtual=False)), lambda i: flat(1000, 1000))
    assert det.profile_name == "set18_16x9" and det.measured is False
    assert any("쓸 수 있는 ROI 배치가 없습니다" in w for w in det.warnings)


def test_detect_picks_highest_scoring_monitor():
    mons = core.monitors_from_mss(mss_list((1920, 1080), (1920, 1200)))
    frames = {1: flat(1920, 1080), 2: flat(1920, 1200)}
    det = core.detect(mons, lambda i: frames[i.number], scorer=lambda f: 0.9 if f.shape[0] == 1200 else 0.1)
    assert det.monitor.number == 2
    assert det.game_found is True and det.scorer_kind == "ocr"
    assert det.aspect == "16:10"


def test_pixel_scoring_never_claims_the_game_was_found():
    """기본(픽셀) 채점은 모니터 비교용이다 — "찾았다"고 말하지 않는다(OCR 채점만 확인한다)."""
    mons = core.monitors_from_mss(mss_list((1920, 1080), virtual=False))
    det = core.detect(mons, lambda i: flat(1920, 1080), scorer=None)
    assert det.scorer_kind == "pixel" and det.game_found is False
    assert not any("찾지 못했습니다" in w for w in det.warnings)


def test_ocr_scoring_warns_when_the_game_is_not_on_screen():
    mons = core.monitors_from_mss(mss_list((1920, 1080), virtual=False))
    det = core.detect(mons, lambda i: flat(1920, 1080), scorer=lambda f: 0.05)
    assert det.game_found is False
    assert any("테두리 없는 창 모드" in w for w in det.warnings)


def test_tie_keeps_the_currently_configured_monitor():
    mons = core.monitors_from_mss(mss_list((1920, 1080), (1920, 1080)))
    det = core.detect(mons, lambda i: flat(1920, 1080), scorer=lambda f: 0.4, prefer=2)
    assert det.monitor.number == 2


def test_no_signal_falls_back_to_the_primary_monitor():
    mons = core.monitors_from_mss(mss_list((1920, 1080), (1920, 1080)))
    det = core.detect(mons, lambda i: flat(1920, 1080), scorer=lambda f: 0.0, prefer="auto")
    assert det.monitor.primary


def test_capture_error_on_one_monitor_does_not_stop_detection():
    mons = core.monitors_from_mss(mss_list((1920, 1080), (1920, 1080)))

    def grab(info):
        if info.number == 1:
            raise OSError("no access")
        return flat(1920, 1080)

    det = core.detect(mons, grab, scorer=lambda f: 0.8)
    assert det.probes[0].error is not None
    assert det.monitor.number == 2 and det.game_found


def test_no_monitors_warns():
    det = core.detect([], lambda i: flat(8, 8))
    assert det.monitor is None and det.warnings
    assert det.summary_lines() == ["모니터를 찾지 못했습니다."]


# ---------------------------------------------------------------------------
# 3. 게임 화면 영역(content box)
# ---------------------------------------------------------------------------


def test_letterbox_is_trimmed_and_changes_the_aspect():
    """1920x1200 모니터에 16:9 게임(위아래 검은 띠) → 잘라낸 뒤 16:9로 잡는다."""
    frame = letterboxed(1920, 1200, bar=60)
    mons = core.monitors_from_mss(mss_list((1920, 1200), virtual=False))
    det = core.detect(mons, lambda i: frame)
    assert det.content_px == (0, 60, 1920, 1080)
    assert det.game_size == (1920, 1080) and det.aspect == "16:9"
    x1, y1, x2, y2 = det.content_box
    assert (x1, x2) == (0.0, 1.0) and y1 == pytest.approx(0.05) and y2 == pytest.approx(0.95)
    assert any("잘라냈습니다" in n for n in det.notes)


def test_content_box_auto_off_keeps_the_whole_frame():
    frame = letterboxed(1920, 1200, bar=60)
    mons = core.monitors_from_mss(mss_list((1920, 1200), virtual=False))
    det = core.detect(mons, lambda i: frame, content_box_auto=False)
    assert det.content_box is None and det.game_size == (1920, 1200)


def test_content_box_survives_the_settings_round_trip(config_dir):
    """비율로 저장한 영역이 그대로 읽히고 픽셀로 환산된다."""
    frame = letterboxed(1920, 1200, bar=60)
    det = core.detect(core.monitors_from_mss(mss_list((1920, 1200), virtual=False)), lambda i: frame)
    choice = core.choice_from_detection(det, load_settings(config_dir))
    choice.content_box = det.content_box
    core.save_settings(choice.updates(), config_dir=config_dir)
    fresh = load_settings(config_dir)
    assert fresh.vision.content_px(1920, 1200) == (0, 60, 1920, 1080)


# ---------------------------------------------------------------------------
# 4. 검은 화면 = 화면 기록 권한
# ---------------------------------------------------------------------------


def test_black_frame_detection():
    assert core.is_black_frame(np.zeros((100, 100, 3), np.uint8))
    assert core.is_black_frame(None)
    assert not core.is_black_frame(flat(100, 100, 90))


def test_all_black_captures_report_a_permission_problem():
    mons = core.monitors_from_mss(mss_list((1920, 1080), (1920, 1080)))
    det = core.detect(mons, lambda i: np.zeros((i.height, i.width, 3), np.uint8), scorer=lambda f: 0.9)
    assert det.permission_issue is True
    assert det.game_found is False          # 검은 화면은 채점하지 않는다
    assert any("권한" in w for w in det.warnings)


def test_one_black_monitor_is_not_a_permission_problem():
    mons = core.monitors_from_mss(mss_list((1920, 1080), (1920, 1080)))
    frames = {1: np.zeros((1080, 1920, 3), np.uint8), 2: flat(1920, 1080)}
    det = core.detect(mons, lambda i: frames[i.number], scorer=lambda f: 0.9)
    assert det.permission_issue is False and det.monitor.number == 2


def test_permission_help_points_at_the_macos_setting(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    assert "개인정보 보호 및 보안" in core.permission_help() and "화면 기록" in core.permission_help()
    monkeypatch.setattr("sys.platform", "win32")
    assert "전체화면" in core.permission_help()


def test_test_capture_on_a_black_frame_shows_the_permission_path():
    out = core.run_test_capture(np.zeros((1080, 1920, 3), np.uint8), recognizer=None)
    assert out.ok is False and "검게" in out.message


# ---------------------------------------------------------------------------
# 5. 테스트 캡처
# ---------------------------------------------------------------------------


def test_test_capture_reads_fields_and_draws_rois():
    rec = FakeRecognizer([planning_state()])
    out = core.run_test_capture(flat(1920, 1080), rec)
    assert out.ok and out.image is not None and out.image.shape == (1080, 1920, 3)
    rows = dict((label, value) for label, value, _ in out.rows)
    assert rows["화면"] == "준비" and rows["스테이지"] == "2-3" and rows["골드"] == "10"
    assert [label for label, _, _ in out.rows] == [label for _, label in core.FIELD_LABELS]
    assert "읽었습니다" in out.message


def test_test_capture_says_so_when_the_hud_is_unreadable():
    from tft_advisor.contracts import GameState

    out = core.run_test_capture(flat(1920, 1080), FakeRecognizer([GameState()]))
    assert out.ok and "읽지 못했습니다" in out.message


def test_test_capture_survives_a_recognizer_crash():
    class Boom:
        def recognize(self, image):
            raise RuntimeError("터짐")

    out = core.run_test_capture(flat(1920, 1080), Boom())
    assert out.ok is False and "터짐" in out.message


# ---------------------------------------------------------------------------
# 6. settings.toml 저장 (주석·형식 보존)
# ---------------------------------------------------------------------------


def test_save_keeps_comments_and_key_order(config_dir):
    before = (config_dir / "settings.toml").read_text(encoding="utf-8")
    core.save_settings(core.SetupChoice(monitor=2, aspect="21:9").updates(), config_dir=config_dir)
    after = (config_dir / "settings.toml").read_text(encoding="utf-8")
    assert "# TFT Advisor 실행 설정" in after
    assert "# 캡처 주기·안정 프레임은 [vision] capture_fps" in after
    assert 'monitor = 2' in after and 'aspect = "21:9"' in after
    assert before.count("\n[vision]") == after.count("\n[vision]") == 1   # 섹션이 늘지 않았다
    assert after.index("[capture]") < after.index("[vision]") < after.index("[advisor]")


def test_save_backs_up_the_previous_file(config_dir):
    before = (config_dir / "settings.toml").read_text(encoding="utf-8")
    core.save_settings(core.SetupChoice(monitor=1).updates(), config_dir=config_dir)
    assert (config_dir / "settings.toml.bak").read_text(encoding="utf-8") == before


def test_save_round_trips_every_key_the_dialog_writes(config_dir):
    choice = core.SetupChoice(monitor=3, aspect="16:10", resolution="auto", profile="set18_16x10",
                              content_box=(0.05, 0.1, 0.95, 0.9), content_box_auto=False,
                              jev_backend="off", overlay_opacity=0.6, overlay_scale=1.4)
    core.save_settings(choice.updates(), config_dir=config_dir)
    s = load_settings(config_dir)
    assert s.capture.monitor == 3
    assert (s.vision.aspect, s.vision.profile, s.vision.resolution) == ("16:10", "set18_16x10", "auto")
    assert s.vision.content_box == (0.05, 0.1, 0.95, 0.9) and s.vision.content_box_auto is False
    assert s.advisor.jev_backend == "off"
    assert s.overlay.opacity == 0.6 and s.overlay.scale == 1.4


def test_clearing_the_content_box_comments_the_key_out(config_dir):
    core.save_settings(core.SetupChoice(content_box=(0.1, 0.1, 0.9, 0.9)).updates(), config_dir=config_dir)
    assert load_settings(config_dir).vision.content_box is not None
    core.save_settings(core.SetupChoice(content_box=None).updates(), config_dir=config_dir)
    text = (config_dir / "settings.toml").read_text(encoding="utf-8")
    assert load_settings(config_dir).vision.content_box is None
    assert "# content_box = " in text          # 설명 겸 예시로 남는다


def test_invalid_combination_is_never_written(config_dir):
    before = (config_dir / "settings.toml").read_text(encoding="utf-8")
    bad = {"vision": {"resolution": "1280x800", "aspect": "16:9"}}   # 비율과 해상도가 어긋난다
    with pytest.raises(Exception):
        core.save_settings(bad, config_dir=config_dir)
    assert (config_dir / "settings.toml").read_text(encoding="utf-8") == before


def test_save_creates_the_file_when_there_is_none(tmp_path):
    path = core.save_settings(core.SetupChoice(monitor=1).updates(), config_dir=tmp_path)
    assert path.is_file() and not (tmp_path / "settings.toml.bak").exists()
    assert load_settings(tmp_path).capture.monitor == 1


def test_render_value_and_missing_section():
    assert core.render_value(True) == "true"
    assert core.render_value("a\"b") == '"a\\"b"'
    assert core.render_value([0.5, 1]) == "[0.5, 1]"
    out = core.update_toml_text("[app]\nset_number = 18\n", {"capture": {"monitor": 2}})
    assert "[capture]" in out and "monitor = 2" in out


def test_inline_comment_with_a_hash_inside_a_string_is_kept():
    out = core.update_toml_text('[stats]\nuser_agent = "a # b"   # 설명\n', {"stats": {"user_agent": "c"}})
    assert out.strip().endswith('user_agent = "c"   # 설명')


# ---------------------------------------------------------------------------
# 7. 첫 실행 게이팅
# ---------------------------------------------------------------------------


def test_first_run_needs_setup_and_completion_is_recorded(tmp_path, settings):
    assert core.setup_completed(tmp_path) is False
    assert core.needs_setup(settings, state_dir=tmp_path) is True
    path = core.mark_setup_done(tmp_path, core.SetupChoice(monitor=2, resolution="1920x1200"))
    assert core.setup_completed(tmp_path) is True
    assert core.needs_setup(settings, state_dir=tmp_path) is False
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["monitor"] == 2 and data["resolution"] == "1920x1200" and data["completed_at"]


def test_broken_state_file_counts_as_first_run(tmp_path):
    core.setup_state_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert core.setup_completed(tmp_path) is False


def test_commit_writes_both_the_settings_and_the_state(config_dir, tmp_path):
    settings = load_settings(config_dir)
    out = core.commit(core.SetupChoice(monitor=2), settings=settings, config_dir=config_dir,
                      action="start", state_dir=tmp_path)
    assert out.action == "start" and out.path == config_dir / "settings.toml"
    assert out.settings.capture.monitor == 2
    assert core.setup_completed(tmp_path)


def test_resolve_state_dir_handles_relative_and_absolute(settings, tmp_path):
    assert core.resolve_state_dir(settings).is_absolute()
    absolute = settings.model_copy(update={"app": settings.app.model_copy(update={"state_dir": str(tmp_path)})})
    assert core.resolve_state_dir(absolute) == tmp_path


# ---------------------------------------------------------------------------
# 8. CLI 연결
# ---------------------------------------------------------------------------


def parse(argv: list[str]):
    return cli.build_parser().parse_args(argv)


@pytest.mark.parametrize(("argv", "first_run", "expected"), [
    ([], True, True),                       # 첫 실행 → 실시간 모드가 설정을 먼저 연다
    ([], False, False),                     # 두 번째부터는 바로 시작
    (["--setup"], False, True),             # --setup 은 언제나 연다
    (["--no-setup"], True, False),          # 첫 실행이어도 건너뛴다
    (["--screenshot", "a.png"], True, False),   # 스크린샷 모드는 화면을 안 본다
])
def test_wants_setup(monkeypatch, settings, argv, first_run, expected):
    monkeypatch.setattr(core, "needs_setup", lambda s, **kw: first_run)
    assert cli.wants_setup(settings, parse(argv)) is expected


def test_setup_and_no_setup_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse(["--setup", "--no-setup"])


def test_setup_step_stops_after_save_only(monkeypatch, settings, capsys):
    saved = core.SetupOutcome(action="saved", settings=settings, path=Path("x.toml"))
    monkeypatch.setattr(core, "run_setup", lambda *a, **kw: saved)
    out, code = cli.run_setup_step(settings, parse(["--setup", "--no-overlay"]))
    assert code == 0 and out is not None
    assert "저장" in capsys.readouterr().out


def test_setup_step_continues_to_live_after_start(monkeypatch, settings):
    monkeypatch.setattr(core, "run_setup",
                        lambda *a, **kw: core.SetupOutcome(action="start", settings=settings, path=Path("x")))
    _, code = cli.run_setup_step(settings, parse(["--setup"]))
    assert code is None          # None = 계속 진행(실시간 앱으로)


def test_cancelling_setup_exits_without_running_live(monkeypatch, settings, capsys):
    monkeypatch.setattr(core, "run_setup", lambda *a, **kw: core.SetupOutcome(action="cancelled"))
    _, code = cli.run_setup_step(settings, parse(["--setup"]))
    assert code == 0
    assert "취소" in capsys.readouterr().err


def test_main_opens_setup_on_first_run(monkeypatch, tmp_path):
    """`--live`: 첫 실행이면 설정 → (저장 후 시작) → 실시간 루프."""
    calls = {}
    monkeypatch.setattr(cli, "wants_setup", lambda s, a: True)
    monkeypatch.setattr(cli, "run_setup_step", lambda s, a: (calls.setdefault("setup", True), (s, None))[1])
    monkeypatch.setattr("tft_advisor.app.live.run_live", lambda **kw: calls.setdefault("live", 0) or 0)
    assert cli.main(["--live", "--no-overlay"]) == 0
    assert calls == {"setup": True, "live": 0}


# ---------------------------------------------------------------------------
# 9. 콘솔 대체 UI (PySide6 없음 / --no-overlay)
# ---------------------------------------------------------------------------


def console_detection(size=(1920, 1200), count=2):
    mons = core.monitors_from_mss(mss_list(*([size] * count), virtual=count > 1))
    return core.detect(mons, lambda i: flat(*size), scorer=lambda f: 0.9)


def run_console(config_dir, answers, state_dir, det=None):
    lines: list[str] = []
    it = iter(answers)
    outcome = core.run_console_setup(
        load_settings(config_dir), config_dir=config_dir, state_dir=state_dir,
        input_fn=lambda prompt: next(it), out=lines.append,
        detect_fn=lambda: det or console_detection())
    return outcome, "\n".join(lines)


def test_console_setup_saves_and_starts(config_dir, tmp_path):
    outcome, text = run_console(config_dir, ["1"], tmp_path)
    assert outcome.action == "start"
    assert "16:10" in text and "set18_16x10" in text
    assert load_settings(config_dir).capture.monitor == 1
    assert core.setup_completed(tmp_path)


def test_console_setup_save_only_and_cancel(config_dir, tmp_path):
    assert run_console(config_dir, ["5"], tmp_path)[0].action == "saved"
    outcome, text = run_console(config_dir, ["0"], tmp_path)
    assert outcome.action == "cancelled" and "취소" in text


def test_console_setup_can_pick_another_monitor(config_dir, tmp_path):
    outcome, text = run_console(config_dir, ["2", "2", "1"], tmp_path)
    assert outcome.action == "start" and outcome.choice.monitor == 2
    assert "[1] 1번 모니터" in text


def test_console_setup_can_pick_auto_monitor(config_dir, tmp_path):
    outcome, _ = run_console(config_dir, ["2", "0", "1"], tmp_path)
    assert outcome.choice.monitor == "auto"
    assert load_settings(config_dir).capture.monitor == "auto"


def test_console_setup_can_override_the_aspect(config_dir, tmp_path):
    outcome, _ = run_console(config_dir, ["3", "21:9", "1"], tmp_path)
    assert outcome.choice.aspect == "21:9" and outcome.choice.resolution == "auto"
    assert load_settings(config_dir).vision.aspect == "21:9"


def test_console_setup_rejects_a_bad_answer_and_asks_again(config_dir, tmp_path):
    outcome, text = run_console(config_dir, ["9", "1"], tmp_path)
    assert outcome.action == "start" and "중에서 골라 주세요" in text


def test_console_setup_can_redetect(config_dir, tmp_path):
    outcome, text = run_console(config_dir, ["4", "1"], tmp_path)
    assert outcome.action == "start" and text.count("게임 해상도") >= 2


def test_console_setup_shows_the_permission_path_on_black_captures(config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    mons = core.monitors_from_mss(mss_list((1920, 1080), virtual=False))
    det = core.detect(mons, lambda i: np.zeros((1080, 1920, 3), np.uint8), scorer=lambda f: 0.9)
    _, text = run_console(config_dir, ["0"], tmp_path, det=det)
    assert "화면 기록" in text and "권한" in text


def test_console_setup_survives_a_capture_failure(config_dir, tmp_path, monkeypatch):
    """mss 자체가 실패해도(권한 거부·헤드리스) 설정 화면이 역추적을 뱉으며 죽지 않는다."""
    monkeypatch.setattr("sys.platform", "darwin")

    def boom():
        raise OSError("gdi: 화면을 열 수 없다")

    lines: list[str] = []
    outcome = core.run_console_setup(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path,
                                     input_fn=lambda p: "0", out=lines.append, detect_fn=boom)
    text = "\n".join(lines)
    assert outcome.action == "cancelled"
    assert "화면 캡처에 실패했습니다" in text and "화면 기록" in text


def test_safe_detect_never_raises():
    det = core.safe_detect(lambda: (_ for _ in ()).throw(RuntimeError("mss 없음")))
    assert det.permission_issue and any("mss 없음" in w for w in det.warnings)
    assert det.monitor is None


def test_console_setup_treats_eof_as_cancel(config_dir, tmp_path):
    def boom(prompt):
        raise EOFError

    outcome = core.run_console_setup(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path,
                                     input_fn=boom, out=lambda s: None,
                                     detect_fn=console_detection)
    assert outcome.action == "cancelled"


def test_run_setup_falls_back_to_console_without_pyside6(config_dir, tmp_path, monkeypatch):
    import builtins

    real = builtins.__import__

    def no_pyside(name, *a, **kw):
        if "setup_dialog" in name or name.startswith("PySide6"):
            raise ImportError("no PySide6")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_pyside)
    outcome = core.run_setup(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path, gui=True,
                             input_fn=lambda p: "0", out=lambda s: None,
                             detect_fn=console_detection)
    assert outcome.action == "cancelled"   # 콘솔 UI가 답을 받았다 = 대체 경로로 내려갔다


# ---------------------------------------------------------------------------
# 10. Qt 대화상자 (헤드리스)
# ---------------------------------------------------------------------------


@pytest.fixture
def dialog(qapp, config_dir, tmp_path):
    from tft_advisor.app.setup_dialog import SetupDialog

    mons = core.monitors_from_mss(mss_list((1920, 1080), (1920, 1200)))
    frames = {1: flat(1920, 1080), 2: flat(1920, 1200)}
    d = SetupDialog(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path,
                    grabber=FakeGrabber(mons, frames))
    yield d
    d.deleteLater()


def test_dialog_auto_detects_on_open(dialog):
    assert dialog.detection is not None and dialog.detection.probes
    assert dialog.monitor_combo.count() == 3            # 자동 + 모니터 2대
    assert "1번 모니터" in dialog.monitor_combo.itemText(1)
    assert "감지" in dialog.detect_btn.text() or "자동" in dialog.detect_btn.text()


def test_dialog_fills_widgets_from_the_detection(qapp, config_dir, tmp_path):
    """16:10 모니터가 골라지면 해상도·프로파일·요약이 전부 그 값으로 채워진다."""
    from tft_advisor.app.setup_dialog import SetupDialog

    mons = core.monitors_from_mss(mss_list((1920, 1200), virtual=False))
    d = SetupDialog(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path,
                    grabber=FakeGrabber(mons, {0: flat(1920, 1200)}))
    choice = d.current_choice()
    assert choice.monitor == 0 and choice.resolution == "1920x1200"
    assert choice.aspect == "auto"                       # 실측 비율은 auto로 둔다(해상도가 바뀌어도 따라간다)
    assert "1920x1200" in d.summary.text() and "16:10" in d.summary.text()
    assert "set18_16x10" in d.summary.text() and "실측" in d.summary.text()
    d.deleteLater()


def test_dialog_warns_about_a_derived_profile(qapp, config_dir, tmp_path):
    from tft_advisor.app.setup_dialog import SetupDialog

    mons = core.monitors_from_mss(mss_list((2560, 1080), virtual=False))
    d = SetupDialog(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path,
                    grabber=FakeGrabber(mons, {0: flat(2560, 1080)}))
    assert d.warn_label.isVisible() or d.warn_label.text()
    assert "유도" in d.warn_label.text()
    assert "21:9" in d.summary.text()
    d.deleteLater()


def test_dialog_shows_the_permission_box_on_black_captures(qapp, config_dir, tmp_path):
    from tft_advisor.app.setup_dialog import SetupDialog

    mons = core.monitors_from_mss(mss_list((1920, 1080), virtual=False))
    black = {0: np.zeros((1080, 1920, 3), np.uint8)}
    d = SetupDialog(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path,
                    grabber=FakeGrabber(mons, black))
    assert d.detection.permission_issue is True
    assert "권한" in d.perm_label.text()
    d.grabber._frames = {0: flat(1920, 1080)}            # 권한을 켠 뒤 [다시 시도]
    d.auto_detect(ocr=False)
    assert d.detection.permission_issue is False
    d.deleteLater()


def test_dialog_save_writes_settings_and_marks_setup_done(dialog, config_dir, tmp_path):
    outcome = dialog.save_and_close("start")
    assert outcome.action == "start"
    assert load_settings(config_dir).capture.monitor == 1
    assert core.setup_completed(tmp_path)


def test_dialog_cancel_saves_nothing(dialog, config_dir, tmp_path):
    before = (config_dir / "settings.toml").read_text(encoding="utf-8")
    dialog.reject()
    assert dialog.outcome.action == "cancelled"
    assert (config_dir / "settings.toml").read_text(encoding="utf-8") == before
    assert core.setup_completed(tmp_path) is False


def test_dialog_manual_content_box(dialog, config_dir):
    dialog.box_manual.setChecked(True)
    for spin, v in zip(dialog.box_spins, (0.1, 0.2, 0.8, 0.9)):
        spin.setValue(v)
    assert dialog.current_choice().content_box == (0.1, 0.2, 0.8, 0.9)
    dialog.save_and_close("saved")
    assert load_settings(config_dir).vision.content_box == (0.1, 0.2, 0.8, 0.9)


def test_dialog_ignores_an_empty_manual_box(dialog):
    dialog.box_manual.setChecked(True)
    for spin in dialog.box_spins:
        spin.setValue(0.5)                               # x1 == x2 → 무시
    assert dialog.current_choice().content_box is None


def test_dialog_refuses_to_save_a_contradictory_combination(dialog, config_dir):
    before = (config_dir / "settings.toml").read_text(encoding="utf-8")
    dialog.aspect_combo.setCurrentIndex([dialog.aspect_combo.itemData(i)
                                         for i in range(dialog.aspect_combo.count())].index("4:3"))
    dialog.res_combo.setEditText("1920x1080")            # 4:3 과 어긋난다
    outcome = dialog.save_and_close("start")
    assert outcome.action == "cancelled"
    assert "저장하지 못했습니다" in dialog.warn_label.text()
    assert (config_dir / "settings.toml").read_text(encoding="utf-8") == before


def test_dialog_overlay_and_jev_overrides(dialog, config_dir):
    dialog.jev_off.setChecked(True)          # Jev 선택은 체크박스다(자세한 검증은 test_jev_toggle.py)
    dialog.opacity_spin.setValue(0.5)
    dialog.scale_spin.setValue(1.5)
    dialog.save_and_close("saved")
    s = load_settings(config_dir)
    assert s.advisor.jev_backend == "off" and s.overlay.opacity == 0.5 and s.overlay.scale == 1.5


def test_dialog_jev_defaults_to_mock(dialog):
    assert dialog.current_choice().jev_backend == "mock"


def test_dialog_test_capture_shows_a_table_and_a_preview(dialog, monkeypatch):
    monkeypatch.setattr(dialog, "_get_recognizer", lambda s: FakeRecognizer([planning_state()]))
    out = dialog.test_capture()
    assert out.ok and dialog.table.rowCount() == len(core.FIELD_LABELS)
    assert dialog.table.item(0, 1).text() == "준비"
    assert dialog.preview.pixmap() is not None and not dialog.preview.pixmap().isNull()


def test_dialog_test_capture_reports_a_black_screen(qapp, config_dir, tmp_path, monkeypatch):
    from tft_advisor.app.setup_dialog import SetupDialog

    mons = core.monitors_from_mss(mss_list((1920, 1080), virtual=False))
    d = SetupDialog(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path,
                    grabber=FakeGrabber(mons, {0: np.zeros((1080, 1920, 3), np.uint8)}))
    monkeypatch.setattr(d, "_get_recognizer", lambda s: FakeRecognizer([planning_state()]))
    out = d.test_capture()
    assert out.ok is False and "검게" in out.message
    assert "화면" in d.test_status.text()
    d.deleteLater()


def test_dialog_detection_failure_does_not_crash(qapp, config_dir, tmp_path):
    from tft_advisor.app.setup_dialog import SetupDialog

    class Broken(FakeGrabber):
        def monitors(self):
            raise OSError("mss 없음")

    d = SetupDialog(load_settings(config_dir), config_dir=config_dir, state_dir=tmp_path, grabber=Broken([]))
    assert d.detection.permission_issue and "mss 없음" in d.warn_label.text()
    d.deleteLater()


def test_to_pixmap_keeps_the_size():
    from tft_advisor.app.setup_dialog import to_pixmap

    pix = to_pixmap(flat(64, 32))
    assert (pix.width(), pix.height()) == (64, 32)


# ---------------------------------------------------------------------------
# 11. 오버레이 트레이 메뉴 → 설정
# ---------------------------------------------------------------------------


def test_overlay_has_a_setup_entry_and_passes_the_config_dir(qapp, config_dir, tmp_path):
    from tft_advisor.app.overlay import OverlayWindow

    w = OverlayWindow(load_settings(config_dir), state_dir=tmp_path, config_dir=config_dir)
    assert w.config_dir == config_dir
    menu = w.menu()
    assert any("설정" in a.text() for a in menu.actions())
    menu.deleteLater()
    w.deleteLater()


def test_overlay_setup_entry_opens_the_dialog(qapp, config_dir, tmp_path, monkeypatch):
    from tft_advisor.app import overlay as overlay_mod
    from tft_advisor.app.overlay import OverlayWindow
    from tft_advisor.app.setup_dialog import SetupDialog

    mons = core.monitors_from_mss(mss_list((1920, 1080), virtual=False))
    seen = {}

    class Headless(SetupDialog):
        def __init__(self, *a, **kw):
            kw["grabber"] = FakeGrabber(mons, {0: flat(1920, 1080)})
            super().__init__(*a, **kw)
            seen["config_dir"] = kw.get("config_dir")

        def exec(self):
            return self.save_and_close("saved") and 1

    monkeypatch.setattr("tft_advisor.app.setup_dialog.SetupDialog", Headless)
    w = OverlayWindow(load_settings(config_dir), state_dir=tmp_path, config_dir=config_dir)
    outcome = w.open_setup()
    assert outcome.action == "saved" and seen["config_dir"] == config_dir
    assert "다시 시작" in w.status.extra
    assert core.setup_completed(tmp_path)
    w.deleteLater()
    assert overlay_mod is not None
