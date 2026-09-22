"""CLI: 플래그 → 동작 매핑(`--jev`/`--no-jev`, 화면 크기 덮어쓰기, `--no-overlay`, 기본 모드)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tft_advisor import __main__ as cli
from tft_advisor.app.live import choose_ui, overlay_available


def parse(argv: list[str]):
    return cli.build_parser().parse_args(argv)


def test_live_is_the_default_mode():
    args = parse([])
    assert args.screenshot is None and args.live is False   # 둘 다 없으면 main이 live로 간다


def test_jev_flag_mapping():
    assert cli.jev_backend(parse([])) == "auto"             # 설정 [advisor] jev_backend(기본 mock)
    assert cli.jev_backend(parse(["--jev", "live"])) == "live"
    assert cli.jev_backend(parse(["--jev", "mock"])) == "mock"
    assert cli.jev_backend(parse(["--no-jev"])) == "off"
    assert cli.jev_backend(parse(["--jev", "live", "--no-jev"])) == "off"   # --no-jev 가 이긴다


def test_screenshot_and_live_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse(["--screenshot", "a.png", "--live"])


def test_vision_overrides_are_validated(settings):
    args = parse(["--profile", "set18_16x10", "--aspect", "16:10", "--resolution", "1280x800"])
    out = cli.apply_overrides(settings, args)
    assert out.vision.profile == "set18_16x10" and out.vision.aspect == "16:10"
    assert out.vision.resolution == "1280x800"
    assert settings.vision.profile == "auto"   # 원본은 그대로


def test_bad_override_combination_is_rejected(settings):
    args = parse(["--aspect", "16:9", "--resolution", "1280x800"])   # 비율과 해상도가 어긋난다
    with pytest.raises(Exception):
        cli.apply_overrides(settings, args)


def test_unknown_aspect_is_rejected_by_argparse():
    with pytest.raises(SystemExit):
        parse(["--aspect", "5:4"])


def test_missing_screenshot_path_returns_2(tmp_path, capsys):
    assert cli.main(["--screenshot", str(tmp_path / "없음.png")]) == 2


def test_main_routes_to_screenshot(monkeypatch, tmp_path):
    called = {}
    target = tmp_path / "shot.png"
    target.write_bytes(b"x")

    def fake(path, **kw):
        called.update(path=path, **kw)
        return 0

    monkeypatch.setattr("tft_advisor.app.screenshot.run_screenshot", fake)
    assert cli.main(["--screenshot", str(target), "--no-jev"]) == 0
    assert called["path"] == target and called["jev"] == "off" and called["debug_dir"] is None


def test_main_routes_to_live_with_flags(monkeypatch):
    called = {}

    def fake(**kw):
        called.update(kw)
        return 0

    monkeypatch.setattr("tft_advisor.app.live.run_live", fake)
    assert cli.main(["--live", "--no-overlay", "--jev", "mock", "--debug"]) == 0
    assert called["overlay"] is False and called["jev"] == "mock"
    assert Path(called["debug_dir"]).name == "debug"


def test_choose_ui(settings):
    assert choose_ui(settings, want_overlay=False) == "console"
    console_cfg = settings.model_copy(update={"ui": settings.ui.model_copy(update={"backend": "console"})})
    assert choose_ui(console_cfg, want_overlay=True) == "console"
    tk = settings.model_copy(update={"ui": settings.ui.model_copy(update={"backend": "tkinter"})})
    assert choose_ui(tk, want_overlay=True) == "console"      # 조용히 다른 툴킷으로 바꾸지 않는다
    disabled = settings.model_copy(update={"overlay": settings.overlay.model_copy(update={"enabled": False})})
    assert choose_ui(disabled, want_overlay=True) == "console"
    if overlay_available():
        assert choose_ui(settings, want_overlay=True) == "pyside6"
