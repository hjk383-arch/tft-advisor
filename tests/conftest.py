"""모든 테스트 공용 — **자격 증명 저장소를 격리한다.**

테스트는 진짜 OS 키체인도, 진짜 홈 디렉터리(`~/.config/tft-advisor/`)도 건드리지 않는다.
macOS에서 키체인 접근 허가 창이 뜨는 것도 이 픽스처가 막는다.
키가 필요한 테스트는 지금까지처럼 `monkeypatch.setenv("TYPESAFE_API_KEY", …)`를 쓰면 된다(환경변수가 1순위다).
"""
from __future__ import annotations

import pytest

from tft_advisor import credentials


@pytest.fixture(autouse=True)
def isolate_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv(credentials.KEYRING_ENV, "0")                       # 진짜 키체인 금지
    monkeypatch.setenv(credentials.CONFIG_HOME_ENV, str(tmp_path / "cfg-home"))   # 진짜 홈 금지
    credentials.refresh()
    yield
    credentials.refresh()
