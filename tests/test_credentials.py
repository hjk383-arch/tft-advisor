"""`tft_advisor.credentials` — 해석 순서, OS 키체인, 폴백 파일, 연결 테스트, 유출 금지.

**진짜 키체인도 진짜 Jev도 부르지 않는다.** 키체인은 `FakeKeyring`, 연결 테스트는 `caller=`로 꽂는 가짜다.
(홈 디렉터리·키체인 차단은 `tests/conftest.py`의 autouse 픽스처가 한다.)
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tft_advisor import credentials as C

REPO = Path(__file__).resolve().parents[1]
# 조각을 이어 붙인다 — 이 파일 안에도 온전한 문자열이 남지 않게(아래 유출 검사가 저장소 전체를 훑는다).
SENTINEL = "sk-live-" + "SHOULD-NEVER" + "-LEAK-0001"


# ---------------------------------------------------------------------------
# 가짜 키체인
# ---------------------------------------------------------------------------


class FakeKeyring:
    """`keyring` 모듈 흉내 — 호출 횟수를 세고, 지정하면 실패한다."""

    def __init__(self, store: dict | None = None, fail: str | None = None) -> None:
        self.store = dict(store or {})
        self.fail = fail
        self.reads = self.writes = self.deletes = 0

    def get_password(self, service: str, user: str):
        self.reads += 1
        if self.fail == "get":
            raise RuntimeError("keychain locked")
        return self.store.get((service, user))

    def set_password(self, service: str, user: str, password: str) -> None:
        self.writes += 1
        if self.fail == "set":
            raise RuntimeError("user denied")
        self.store[(service, user)] = password

    def delete_password(self, service: str, user: str) -> None:
        self.deletes += 1
        if (service, user) not in self.store:
            raise KeyError("no such password")
        del self.store[(service, user)]


@pytest.fixture
def no_keyring(monkeypatch):
    """키체인 백엔드가 없는 환경(→ 폴백 파일)."""
    monkeypatch.setattr(C, "_keyring", lambda: None)
    C.refresh()
    return None


@pytest.fixture
def fake_keyring(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(C, "_keyring", lambda: kr)
    C.refresh()
    return kr


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv(C.ENV_VAR, raising=False)
    C.refresh()


# ---------------------------------------------------------------------------
# 해석 순서: 환경변수 > 키체인 > 파일 > 없음
# ---------------------------------------------------------------------------


def test_no_key_anywhere(no_keyring):
    assert C.resolve_api_key() is None
    assert C.key_present() is False
    info = C.key_info()
    assert info.source == "none" and info.present is False
    assert "없다" in info.describe()


def test_env_var_wins_over_keyring_and_file(monkeypatch, fake_keyring):
    """1순위는 환경변수다 — 키체인·파일에 다른 값이 있어도 이긴다."""
    fake_keyring.store[(C.SERVICE, C.ACCOUNT)] = "from-keychain-aaaa"
    C.save_api_key("from-keychain-aaaa")
    monkeypatch.setenv(C.ENV_VAR, "from-env-bbbb")
    C.refresh()
    assert C.resolve_api_key() == "from-env-bbbb"
    info = C.key_info()
    assert info.source == "env" and info.hint == C.mask("from-env-bbbb")
    assert "환경변수" in info.describe()


def test_keyring_wins_over_fallback_file(fake_keyring, monkeypatch):
    """2순위는 키체인 — 폴백 파일이 남아 있어도 키체인이 이긴다."""
    C._write_fallback("from-file-cccc")
    fake_keyring.store[(C.SERVICE, C.ACCOUNT)] = "from-keychain-aaaa"
    C.refresh()
    assert C.resolve_api_key() == "from-keychain-aaaa"
    assert C.key_info().source == "keyring"


def test_fallback_file_is_last_before_none(no_keyring):
    C._write_fallback("from-file-cccc")
    C.refresh()
    assert C.resolve_api_key() == "from-file-cccc"
    info = C.key_info()
    assert info.source == "file" and info.path == C.fallback_path()


def test_resolution_order_constant():
    """순서를 한 줄로 못박는다: env → keyring → file → none."""
    assert list(C.SOURCE_LABELS) == ["env", "keyring", "file", "none"]
    assert C.SOURCE_LABELS["env"] == f"환경변수 {C.ENV_VAR}"


def test_empty_and_whitespace_values_do_not_count(monkeypatch, no_keyring):
    monkeypatch.setenv(C.ENV_VAR, "   ")
    C.refresh()
    assert C.key_present() is False


def test_env_key_is_stripped(monkeypatch, no_keyring):
    monkeypatch.setenv(C.ENV_VAR, "  sk-padded-1234  ")
    C.refresh()
    assert C.resolve_api_key() == "sk-padded-1234"


# ---------------------------------------------------------------------------
# 키체인 읽기·쓰기·지우기
# ---------------------------------------------------------------------------


def test_save_goes_to_the_keyring(fake_keyring):
    result = C.save_api_key(SENTINEL)
    assert result.ok and result.store == "keyring"
    assert fake_keyring.store[(C.SERVICE, C.ACCOUNT)] == SENTINEL
    assert C.resolve_api_key() == SENTINEL
    assert C.fallback_path().exists() is False      # 키체인이 되면 파일은 만들지 않는다


def test_save_message_says_which_store_was_used(fake_keyring):
    assert C.keyring_label() in C.save_api_key(SENTINEL).message


def test_delete_removes_the_keyring_entry(fake_keyring):
    C.save_api_key(SENTINEL)
    result = C.delete_api_key()
    assert result.ok and "keyring" in result.removed
    assert C.resolve_api_key() is None and C.key_present() is False


def test_delete_with_nothing_stored_is_harmless(fake_keyring):
    result = C.delete_api_key()
    assert result.ok is False and "없었다" in result.message


def test_delete_does_not_touch_the_environment_variable(monkeypatch, fake_keyring):
    C.save_api_key(SENTINEL)
    monkeypatch.setenv(C.ENV_VAR, "from-env-bbbb")
    C.refresh()
    result = C.delete_api_key()
    assert os.environ[C.ENV_VAR] == "from-env-bbbb"
    assert C.ENV_VAR in result.message              # "환경변수는 그대로다"라고 말해 준다
    assert C.resolve_api_key() == "from-env-bbbb"


def test_keyring_read_failure_falls_through_quietly(monkeypatch):
    """키체인이 잠겼거나 사용자가 거부해도 앱은 죽지 않는다 — 폴백 파일로 내려간다."""
    monkeypatch.setattr(C, "_keyring", lambda: FakeKeyring(fail="get"))
    C._write_fallback("from-file-cccc")
    C.refresh()
    assert C.resolve_api_key() == "from-file-cccc"


def test_keyring_write_failure_falls_back_to_the_file(monkeypatch):
    monkeypatch.setattr(C, "_keyring", lambda: FakeKeyring(fail="set"))
    C.refresh()
    result = C.save_api_key(SENTINEL)
    assert result.ok and result.store == "file"
    assert C.resolve_api_key() == SENTINEL


def test_keyring_is_disabled_by_the_environment_switch(monkeypatch):
    monkeypatch.setenv(C.KEYRING_ENV, "1")
    assert C.keyring_enabled() is True
    monkeypatch.setenv(C.KEYRING_ENV, "0")
    assert C.keyring_enabled() is False and C._keyring() is None


def test_refresh_clears_the_in_process_cache(fake_keyring):
    C.save_api_key(SENTINEL)
    assert C.resolve_api_key() == SENTINEL
    reads = fake_keyring.reads
    C.resolve_api_key()
    assert fake_keyring.reads == reads              # 캐시가 있어 다시 묻지 않는다(키체인 허가 창 방지)
    fake_keyring.store.clear()
    C.refresh()
    assert C.resolve_api_key() is None


def test_save_rejects_empty_and_whitespace_keys(fake_keyring):
    assert C.save_api_key("   ").ok is False
    assert C.save_api_key("sk with space").ok is False
    assert fake_keyring.writes == 0


# ---------------------------------------------------------------------------
# 폴백 파일: 위치와 권한
# ---------------------------------------------------------------------------


def test_fallback_file_lives_outside_the_repository(monkeypatch):
    """저장소는 공개다 — 키 파일이 저장소 안에 생기면 안 된다."""
    monkeypatch.delenv(C.CONFIG_HOME_ENV, raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    path = C.fallback_path()
    assert path == Path.home() / ".config" / "tft-advisor" / "credentials.toml"
    assert not path.is_relative_to(REPO)
    for forbidden in (REPO / "config", REPO / "data", REPO / "_state", REPO / "_workspace", REPO / "logs"):
        assert not path.is_relative_to(forbidden)


def test_fallback_file_permissions_are_0600_in_a_0700_directory(no_keyring):
    result = C.save_api_key(SENTINEL)
    assert result.store == "file"
    path = C.fallback_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert C.fallback_permissions() == (0o600, 0o700)


def test_fallback_file_is_rewritten_with_the_same_permissions(no_keyring):
    C.save_api_key(SENTINEL)
    C.fallback_path().chmod(0o644)
    C.save_api_key("sk-second-key-2222")
    assert stat.S_IMODE(C.fallback_path().stat().st_mode) == 0o600
    C.refresh()
    assert C.resolve_api_key() == "sk-second-key-2222"


def test_fallback_delete_removes_the_file(no_keyring):
    C.save_api_key(SENTINEL)
    assert C.delete_api_key().removed == ("file",)
    assert C.fallback_path().exists() is False


def test_broken_fallback_file_does_not_crash(no_keyring):
    path = C.fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not toml [[[", "utf-8")
    C.refresh()
    assert C.resolve_api_key() is None


def test_fallback_file_round_trips_odd_characters(no_keyring):
    odd = 'sk-"quoted"\\and-backslash-9999'
    C.save_api_key(odd)
    C.refresh()
    assert C.resolve_api_key() == odd


def test_config_home_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv(C.CONFIG_HOME_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert C.config_home() == tmp_path / "xdg" / "tft-advisor"


# ---------------------------------------------------------------------------
# 가리기
# ---------------------------------------------------------------------------


def test_mask_shows_only_the_edges():
    assert C.mask("sk-abcdefgh1234") == "sk-…1234"
    assert C.mask(None) == "(없음)" and C.mask("") == "(없음)"
    assert "short" not in C.mask("short")


def test_key_info_never_carries_the_value(fake_keyring):
    C.save_api_key(SENTINEL)
    info = C.key_info()
    assert SENTINEL not in repr(info) and SENTINEL not in info.describe()
    assert info.hint == C.mask(SENTINEL)


# ---------------------------------------------------------------------------
# 연결 테스트 (진짜 호출은 하지 않는다 — caller를 꽂는다)
# ---------------------------------------------------------------------------


def test_verify_key_calls_the_backend_exactly_once(fake_keyring):
    calls = []
    result = C.verify_key(SENTINEL, caller=calls.append)
    assert result.ok and result.reason == "ok" and "성공" in result.message
    assert calls == [SENTINEL]          # 딱 한 번, 준 키 그대로


def test_verify_key_uses_the_resolved_key_when_none_is_typed(fake_keyring):
    C.save_api_key(SENTINEL)
    seen = []
    assert C.verify_key(None, caller=seen.append).ok
    assert seen == [SENTINEL]


def test_verify_key_without_any_key(no_keyring):
    result = C.verify_key(None, caller=lambda k: None)
    assert result.ok is False and result.reason == "empty"


@pytest.mark.parametrize(("exc", "reason", "word"), [
    ("auth", "auth", "키가 거부"),
    ("rate", "quota", "한도"),
    ("conn", "network", "네트워크"),
    ("timeout", "timeout", "응답"),
    ("server", "server", "서버"),
])
def test_verify_key_reports_a_readable_reason(exc, reason, word):
    import httpx2
    import typesafe_sdk as ts

    headers = httpx2.Headers()
    made = {
        "auth": ts.TypeSafeAuthenticationError(401, {}, headers, "bad key"),
        "rate": ts.TypeSafeRateLimitError(429, {}, headers, "slow down"),
        "conn": ts.TypeSafeAPIConnectionError("no route to host"),
        "timeout": ts.TypeSafeAPITimeoutError(5.0),
        "server": ts.TypeSafeAPIError(503, {}, headers, "unavailable"),
    }[exc]

    def boom(key):
        raise made

    result = C.verify_key(SENTINEL, caller=boom)
    assert result.ok is False and result.reason == reason and word in result.message
    assert "실패" in result.message


def test_verify_key_failure_never_echoes_the_key(caplog):
    caplog.set_level("DEBUG")

    def boom(key):
        raise RuntimeError(f"server said no about {key}")

    result = C.verify_key(SENTINEL, caller=boom)
    assert result.ok is False
    assert SENTINEL not in result.message and SENTINEL not in caplog.text


def test_verify_key_reports_a_missing_sdk():
    def boom(key):
        raise ImportError("no typesafe_sdk")

    assert C.verify_key(SENTINEL, caller=boom).reason == "sdk"


def test_the_ping_payload_is_a_single_cheap_question():
    assert len(C.PING_QUESTIONS) == 1
    assert C.PING_QUESTIONS["ping"]["type"] == "score"
    assert len(C.PING_QUESTIONS["ping"]["criteria"]) == 2


# ---------------------------------------------------------------------------
# 유출 금지 — settings.toml · 로그 · 디버그 출력 · _workspace
# ---------------------------------------------------------------------------


def test_the_key_never_reaches_settings_logs_or_reports(fake_keyring, monkeypatch, tmp_path, caplog):
    """저장 → 설정 저장 → 추천 1회. 그 어디에도 키 문자열이 없어야 한다."""
    import shutil

    from tft_advisor.app import setup as setup_core

    caplog.set_level("DEBUG")
    C.save_api_key(SENTINEL)
    monkeypatch.setenv(C.ENV_VAR, SENTINEL)     # 환경변수 경로도 함께 본다
    C.refresh()

    # 1) 설정 화면의 저장 경로가 쓰는 settings.toml
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / "config" / "settings.toml", config_dir / "settings.toml")
    setup_core.save_settings({"advisor": {"jev_backend": "live"}}, config_dir=config_dir)
    written = (config_dir / "settings.toml").read_text("utf-8")
    assert SENTINEL not in written and "api_key" not in written

    # 2) 추천 1회(mock) — Recommendation JSON과 debug 덤프
    from tft_advisor.advisor.engine import Advisor
    from tft_advisor.contracts import GameState, ScreenMode

    advisor = Advisor(backend="mock")
    try:
        rec = advisor.advise(GameState(screen_mode=ScreenMode.PLANNING, stage="2-3", level=4, gold=10, hp=90))
    finally:
        advisor.close()
    if rec is not None:
        assert SENTINEL not in rec.model_dump_json()

    # 3) 로그
    assert SENTINEL not in caplog.text

    # 4) 저장소 안의 어떤 파일에도 키가 없다(_workspace 보고서 포함)
    for folder in ("config", "_workspace", "src", "tests", "logs", "_state", "data"):
        base = REPO / folder
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix in (".png", ".jpg", ".npy", ".db", ".onnx", ".xml", ".bin"):
                continue
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text("utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            assert SENTINEL not in text, f"키가 {path} 에 들어 있다"


def test_the_repository_holds_no_credentials_file():
    """`credentials.toml`은 저장소 안 어디에도 없어야 한다."""
    assert list(REPO.rglob("credentials.toml")) == []
