"""설정 화면의 [TypeSafe API 키] 칸 — 마스킹·표시 토글·연결 테스트·저장·삭제, 그리고 Jev 체크박스 연동.

**진짜 키체인도 진짜 Jev 호출도 없다**: 보관소는 `FakeCreds`(또는 가짜 키체인을 꽂은 진짜 모듈),
연결 테스트는 `caller=`/`verify_result`로 꽂는다. Qt는 헤드리스(`QT_QPA_PLATFORM=offscreen`).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QLineEdit

from tft_advisor import credentials
from tft_advisor.app import setup as core
from tft_advisor.config import load_settings

CONFIG_SRC = Path(__file__).resolve().parents[2] / "config"
KEY = "sk-friend-" + "own-key" + "-7777"
OTHER = "sk-env-" + "other" + "-1111"


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


class FakeCreds:
    """설정 대화상자에 꽂는 가짜 키 보관소. 진짜 키체인·파일·네트워크를 건드리지 않는다."""

    def __init__(self, stored: str | None = None, env: str | None = None, store: str = "keyring") -> None:
        self.stored = stored
        self.env = env
        self.store = store
        self.saved: list[str] = []
        self.deletes = 0
        self.verified: list[str | None] = []
        self.verify_result: credentials.VerifyResult | None = None
        self.save_fails: str | None = None

    # --- 읽기 ---
    def key_info(self) -> credentials.KeyInfo:
        if self.env:
            return credentials.KeyInfo("env", credentials.mask(self.env))
        if self.stored:
            path = Path("/home/friend/.config/tft-advisor/credentials.toml") if self.store == "file" else None
            return credentials.KeyInfo(self.store, credentials.mask(self.stored), path)
        return credentials.KeyInfo()

    def key_present(self) -> bool:
        return bool(self.env or self.stored)

    def stored_key_present(self) -> bool:
        return bool(self.stored)

    # --- 쓰기 ---
    def save_api_key(self, key: str) -> credentials.SaveResult:
        key = (key or "").strip()
        if not key:
            return credentials.SaveResult(False, message="키가 비어 있습니다 — 입력한 뒤 다시 [저장]을 눌러 주세요.")
        if self.save_fails:
            return credentials.SaveResult(False, message=self.save_fails)
        self.saved.append(key)
        self.stored = key
        hint = credentials.mask(key)
        label = "macOS 키체인" if self.store == "keyring" else "폴백 파일"
        return credentials.SaveResult(True, self.store, f"{label}에 저장했습니다 — {hint}", hint,
                                      shadowed_by_env=bool(self.env))

    def delete_api_key(self) -> credentials.DeleteResult:
        self.deletes += 1
        had, self.stored = self.stored, None
        return credentials.DeleteResult(("keyring",) if had else (),
                                        "macOS 키체인에서 키를 지웠습니다." if had else "지울 키가 없었습니다.")

    def verify_key(self, key: str | None = None, *, caller=None) -> credentials.VerifyResult:
        self.verified.append(key)
        return self.verify_result or credentials.VerifyResult(True, "성공 — 키가 유효합니다 (30ms).", "ok", 30.0)


def flat(w: int, h: int, value: int = 90) -> np.ndarray:
    return np.full((h, w, 3), value, np.uint8)


class FakeGrabber(core.MonitorGrabber):
    def __init__(self, monitors) -> None:
        self._monitors = list(monitors)
        self.closed = False

    def monitors(self):
        return list(self._monitors)

    def grab(self, info):
        return flat(info.width, info.height)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    out = tmp_path / "config"
    shutil.copytree(CONFIG_SRC, out)
    return out


@pytest.fixture
def make_dialog(qapp, config_dir, tmp_path, monkeypatch):
    """`make_dialog(creds)` → 헤드리스 설정 대화상자. 키 칸 말고는 전부 가짜 화면이다."""
    monkeypatch.delenv(credentials.ENV_VAR, raising=False)
    made = []

    def build(creds=None, *, backend: str = "mock", verifier=None):
        from tft_advisor.app.setup_dialog import SetupDialog

        mons = core.monitors_from_mss([{"left": 0, "top": 0, "width": 1920, "height": 1080},
                                       {"left": 0, "top": 0, "width": 1920, "height": 1080}])
        settings = load_settings(config_dir)
        settings.advisor.jev_backend = backend
        d = SetupDialog(settings, config_dir=config_dir, state_dir=tmp_path,
                        grabber=FakeGrabber(mons), creds=creds or FakeCreds(), key_verifier=verifier)
        made.append(d)
        return d

    yield build
    for d in made:
        d.deleteLater()


# ---------------------------------------------------------------------------
# 입력칸: 가려져 있다
# ---------------------------------------------------------------------------


def test_the_key_field_is_masked_by_default(make_dialog):
    d = make_dialog()
    assert d.key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert d.key_edit.text() == ""


def test_the_show_button_toggles_the_echo_mode(make_dialog):
    d = make_dialog()
    d.key_show.setChecked(True)
    assert d.key_edit.echoMode() == QLineEdit.EchoMode.Normal and "숨기" in d.key_show.text()
    d.key_show.setChecked(False)
    assert d.key_edit.echoMode() == QLineEdit.EchoMode.Password and d.key_show.text() == "표시"


def test_buttons_follow_what_is_typed_and_stored(make_dialog):
    d = make_dialog(FakeCreds())
    assert d.key_save_btn.isEnabled() is False          # 빈 칸 → 저장할 것이 없다
    assert d.key_delete_btn.isEnabled() is False        # 저장된 것이 없다
    assert d.key_test_btn.isEnabled() is False          # 테스트할 키가 없다
    d.key_edit.setText(KEY)
    assert d.key_save_btn.isEnabled() and d.key_test_btn.isEnabled()


def test_delete_button_is_enabled_when_a_key_is_stored(make_dialog):
    d = make_dialog(FakeCreds(stored=KEY))
    assert d.key_delete_btn.isEnabled() and d.key_test_btn.isEnabled()


# ---------------------------------------------------------------------------
# 상태 줄: 어디에 있는 키인가
# ---------------------------------------------------------------------------


def test_status_says_there_is_no_key(make_dialog):
    d = make_dialog(FakeCreds())
    assert "없습니다" in d.key_status.text()


def test_status_shows_only_a_masked_hint_of_a_stored_key(make_dialog):
    d = make_dialog(FakeCreds(stored=KEY))
    text = d.key_status.text()
    assert credentials.mask(KEY) in text and KEY not in text
    assert "키체인" in text


def test_status_says_the_fallback_file_was_used(make_dialog):
    d = make_dialog(FakeCreds(stored=KEY, store="file"))
    assert "파일" in d.key_status.text() and KEY not in d.key_status.text()


def test_an_environment_variable_is_shown_as_taking_precedence(make_dialog):
    """환경변수를 말없이 가리지 않는다 — 그 값이 먼저 쓰인다고 화면에 적는다."""
    d = make_dialog(FakeCreds(env=OTHER))
    text = d.key_status.text()
    assert credentials.ENV_VAR in text and "먼저" in text
    assert OTHER not in text


# ---------------------------------------------------------------------------
# 저장 / 삭제
# ---------------------------------------------------------------------------


def test_saving_stores_the_key_and_says_which_store_was_used(make_dialog):
    creds = FakeCreds()
    d = make_dialog(creds)
    d.key_edit.setText(KEY)
    result = d.save_key()
    assert result.ok and creds.saved == [KEY]
    assert "키체인" in d.key_result.text()


def test_the_key_is_never_shown_again_after_saving(make_dialog):
    d = make_dialog(FakeCreds())
    d.key_show.setChecked(True)
    d.key_edit.setText(KEY)
    d.save_key()
    assert d.key_edit.text() == ""                       # 입력칸을 비운다
    assert d.key_show.isChecked() is False               # 표시 토글도 되돌린다
    assert d.key_edit.echoMode() == QLineEdit.EchoMode.Password
    for widget in (d.key_status, d.key_result, d.key_help, d.jev_note):
        assert KEY not in widget.text()
    assert credentials.mask(KEY) in d.key_status.text()   # 가린 힌트만 남는다


def test_saving_an_empty_field_is_refused(make_dialog):
    creds = FakeCreds()
    d = make_dialog(creds)
    d.key_edit.setText("   ")
    assert d.save_key().ok is False and creds.saved == []


def test_a_save_failure_is_reported(make_dialog):
    creds = FakeCreds()
    creds.save_fails = "키체인이 거부했다"
    d = make_dialog(creds)
    d.key_edit.setText(KEY)
    assert d.save_key().ok is False
    assert "거부" in d.key_result.text()


def test_saving_while_an_environment_variable_is_set_warns_about_precedence(make_dialog):
    creds = FakeCreds(env=OTHER)
    d = make_dialog(creds)
    d.key_edit.setText(KEY)
    d.save_key()
    assert creds.saved == [KEY]
    assert credentials.ENV_VAR in d.key_result.text()    # "지금은 환경변수가 먼저 쓰인다"


def test_deleting_clears_the_stored_key(make_dialog):
    creds = FakeCreds(stored=KEY)
    d = make_dialog(creds)
    result = d.delete_key()
    assert result.ok and creds.deletes == 1 and creds.stored is None
    assert "없습니다" in d.key_status.text()
    assert d.key_delete_btn.isEnabled() is False


def test_deleting_with_nothing_stored_says_so(make_dialog):
    d = make_dialog(FakeCreds())
    assert d.delete_key().ok is False and "없었습니다" in d.key_result.text()


# ---------------------------------------------------------------------------
# 연결 테스트 — 진짜 호출은 없다
# ---------------------------------------------------------------------------


def test_connection_test_reports_success(make_dialog):
    creds = FakeCreds()
    d = make_dialog(creds)
    d.key_edit.setText(KEY)
    result = d.test_key()
    assert result.ok and "성공" in d.key_result.text()
    assert creds.verified == [KEY]                       # 딱 한 번, 입력한 키로


def test_connection_test_uses_the_stored_key_when_the_field_is_empty(make_dialog):
    creds = FakeCreds(stored=KEY)
    d = make_dialog(creds)
    d.test_key()
    assert creds.verified == [None]                      # None → 보관소가 해석한 키를 쓴다


@pytest.mark.parametrize(("reason", "word"), [
    ("auth", "키가 거부됐다"),
    ("quota", "한도"),
    ("network", "네트워크"),
])
def test_connection_test_reports_a_readable_failure(make_dialog, reason, word):
    creds = FakeCreds()
    creds.verify_result = credentials.VerifyResult(False, f"실패 — {word} 어쩌고", reason)
    d = make_dialog(creds)
    d.key_edit.setText(KEY)
    result = d.test_key()
    assert result.ok is False and word in d.key_result.text() and "실패" in d.key_result.text()


def test_connection_test_goes_through_the_injectable_caller(make_dialog):
    """대화상자는 `key_verifier`를 그대로 보관소에 넘긴다 — 테스트가 진짜 호출을 대신할 수 있다."""
    calls: list[str] = []
    seen: list[object] = []

    def verifier(key: str) -> None:
        calls.append(key)

    class Recording(FakeCreds):
        def verify_key(self, key=None, *, caller=None):
            seen.append(caller)
            caller(key or KEY)
            return credentials.VerifyResult(True, "성공", "ok")

    d = make_dialog(Recording(), verifier=verifier)
    d.key_edit.setText(KEY)
    assert d.test_key().ok
    assert seen == [verifier] and calls == [KEY]   # 진짜 호출 대신 꽂은 함수가 딱 한 번 불린다


def test_the_real_dialog_never_calls_jev_by_itself(make_dialog):
    """대화상자를 열기만 해서는 Jev를 부르지 않는다(연결 테스트 버튼을 눌러야 한 번 나간다)."""
    creds = FakeCreds(stored=KEY)
    d = make_dialog(creds)
    d.refresh_key_row()
    d._sync_jev()
    assert creds.verified == []


# ---------------------------------------------------------------------------
# Jev 체크박스 연동 (요구 3)
# ---------------------------------------------------------------------------


def test_live_checkbox_is_disabled_without_a_key_and_points_at_the_key_row(make_dialog):
    d = make_dialog(FakeCreds())
    assert d.jev_live.isEnabled() is False
    assert "TypeSafe API 키" in d.jev_note.text() and "저장" in d.jev_note.text()


def test_live_checkbox_is_enabled_with_a_key_from_the_keychain(make_dialog):
    d = make_dialog(FakeCreds(stored=KEY))
    assert d.jev_live.isEnabled() is True
    assert core.JEV_NO_KEY_NOTE not in d.jev_note.text()


def test_live_checkbox_is_enabled_with_a_key_from_the_environment(make_dialog):
    d = make_dialog(FakeCreds(env=OTHER))
    assert d.jev_live.isEnabled() is True


def test_saving_a_key_enables_the_live_checkbox_right_away(make_dialog):
    """친구가 키를 넣고 [저장]을 누르면 그 자리에서 체크박스가 살아난다(재실행 필요 없음)."""
    d = make_dialog(FakeCreds())
    assert d.jev_live.isEnabled() is False
    d.key_edit.setText(KEY)
    d.save_key()
    assert d.jev_live.isEnabled() is True
    d.jev_live.setChecked(True)
    assert d.current_choice().jev_backend == "live"


def test_deleting_the_key_disables_the_live_checkbox_again(make_dialog):
    d = make_dialog(FakeCreds(stored=KEY))
    d.delete_key()
    assert d.jev_live.isEnabled() is False
    assert core.JEV_NO_KEY_NOTE in d.jev_note.text()


def test_a_configured_live_backend_stays_checked_without_a_key(make_dialog):
    """설정에 live가 적혀 있는데 키가 사라졌다 — 값을 말없이 바꾸지 않는다(끌 수는 있다)."""
    d = make_dialog(FakeCreds(), backend="live")
    assert d.jev_live.isChecked() is True and d.jev_live.isEnabled() is True
    d.jev_live.setChecked(False)
    assert d.jev_live.isEnabled() is False


# ---------------------------------------------------------------------------
# 유출 금지: 저장한 키는 settings.toml·setup.json 어디에도 없다
# ---------------------------------------------------------------------------


def test_saving_the_dialog_never_writes_the_key_to_settings(make_dialog, config_dir, tmp_path, caplog):
    caplog.set_level("DEBUG")
    creds = FakeCreds()
    d = make_dialog(creds)
    d.key_edit.setText(KEY)
    d.save_key()
    d.jev_live.setChecked(True)
    outcome = d.save_and_close("saved")
    assert outcome.action == "saved"
    written = (config_dir / "settings.toml").read_text("utf-8")
    assert KEY not in written and "api_key" not in written
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".toml", ".log", ".jsonl"):
            assert KEY not in path.read_text("utf-8"), f"키가 {path} 에 있다"
    assert KEY not in caplog.text


# ---------------------------------------------------------------------------
# 진짜 credentials 모듈 + 가짜 키체인으로 한 바퀴 (배선 확인)
# ---------------------------------------------------------------------------


def test_end_to_end_with_the_real_store_and_a_fake_keychain(make_dialog, monkeypatch):
    """대화상자 → 진짜 `credentials` → 가짜 키체인. `LiveJevBackend.key_present()`까지 따라온다."""
    from tests.test_credentials import FakeKeyring

    from tft_advisor.advisor.jev_client import LiveJevBackend

    fake = FakeKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: fake)
    credentials.refresh()

    d = make_dialog(credentials)            # 진짜 모듈을 그대로 꽂는다
    assert d.jev_live.isEnabled() is False and LiveJevBackend.key_present() is False

    d.key_edit.setText(KEY)
    result = d.save_key()
    assert result.ok and result.store == "keyring"
    assert fake.store[(credentials.SERVICE, credentials.ACCOUNT)] == KEY
    assert d.jev_live.isEnabled() is True
    assert LiveJevBackend.key_present() is True          # 체크박스·트레이 토글이 보는 그 함수
    assert credentials.resolve_api_key() == KEY

    d.delete_key()
    assert LiveJevBackend.key_present() is False and d.jev_live.isEnabled() is False
