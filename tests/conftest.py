"""모든 테스트 공용 — **자격 증명 저장소를 격리한다.**

테스트는 진짜 OS 키체인도, 진짜 홈 디렉터리(`~/.config/tft-advisor/`)도 건드리지 않는다.
macOS에서 키체인 접근 허가 창이 뜨는 것도 이 픽스처가 막는다.
키가 필요한 테스트는 지금까지처럼 `monkeypatch.setenv("TYPESAFE_API_KEY", …)`를 쓰면 된다(환경변수가 1순위다).
"""
from __future__ import annotations

import os

import pytest

from tft_advisor import credentials

# 세션 범위 픽스처(settings·recognizer)는 아래 autouse(함수 범위)보다 먼저 만들어진다 → import 때 바로 정한다.
os.environ["TFT_ADVISOR_LOCAL_SETTINGS"] = "0"    # 이 PC 전용 config/settings.local.toml 무시(공용 기본값으로 테스트)
os.environ["TFT_ADVISOR_WINDOW_DETECT"] = "0"     # 진짜 게임 창 목록을 읽지 않는다(app.game_window)


@pytest.fixture(autouse=True)
def isolate_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv(credentials.KEYRING_ENV, "0")                       # 진짜 키체인 금지
    monkeypatch.setenv("TFT_ADVISOR_WINDOW_DETECT", "0")                   # 진짜 게임 창 목록 금지(app.game_window)
    monkeypatch.setenv("TFT_ADVISOR_LOCAL_SETTINGS", "0")                  # 이 PC 전용 config/settings.local.toml 무시
    monkeypatch.setenv(credentials.CONFIG_HOME_ENV, str(tmp_path / "cfg-home"))   # 진짜 홈 금지
    credentials.refresh()
    yield
    credentials.refresh()
