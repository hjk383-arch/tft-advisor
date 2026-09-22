"""advisor 테스트 공용 픽스처.

- 기본은 mock Jev(네트워크 없음, 결정적) + mini 통계(tests/fixtures/stats/mini_18.json).
- `@pytest.mark.live` 테스트는 실제 TypeSafe API를 부른다. 기본 skip. 실행: `TFT_LIVE_JEV=1 .venv/bin/python -m pytest -m live -rxs`
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tft_advisor.advisor import Advisor, JsonStatsAdapter, MockJevBackend
from tft_advisor.config import load_settings, load_weights
from tft_advisor.contracts import GameState

TESTS = Path(__file__).resolve().parents[1]
STATES = TESTS / "fixtures" / "states"
MINI = TESTS / "fixtures" / "stats" / "mini_18.json"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: 실제 TypeSafe Jev API 호출(TFT_LIVE_JEV=1일 때만 실행)")
    config.addinivalue_line("markers", "real_stats: 실제 통계 저장소(open_repository) 또는 StatsRepository 구현으로 실행")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("TFT_LIVE_JEV") == "1":
        return
    skip = pytest.mark.skip(reason="live Jev 호출: TFT_LIVE_JEV=1 로 실행")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def stats() -> JsonStatsAdapter:
    return JsonStatsAdapter.from_file(MINI)


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def weights():
    return load_weights()


def load_fixture(name: str) -> dict:
    return json.loads((STATES / f"{name}.json").read_text(encoding="utf-8"))


def fixture_names() -> list[str]:
    return sorted(p.stem for p in STATES.glob("s*.json"))


def to_state(raw: dict) -> GameState:
    return GameState.model_validate(raw)


@pytest.fixture
def make_advisor(stats, settings, weights):
    def _make(backend=None, settings_=None, **mock_kw) -> Advisor:
        be = backend if backend is not None else MockJevBackend(**mock_kw)
        return Advisor(stats=stats, settings=settings_ or settings, weights=weights, backend=be)
    return _make
