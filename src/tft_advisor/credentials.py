"""TypeSafe API 키 보관소 — 환경변수 → OS 키체인 → 폴백 파일 (CLAUDE.md 고정 제약).

**저장소(이 폴더) 안에는 키를 절대 두지 않는다.** 이 저장소는 공개다. `config/settings.toml`,
`_state/`, `logs/`, `_workspace/` 어디에도 키를 쓰지 않는다. 앱이 키를 읽는 길은 이 모듈 하나뿐이다.

해석 순서 (`resolve_api_key()`)
  1. 환경변수 `TYPESAFE_API_KEY` — 있으면 **무조건 이긴다**(CI·셸에서 명시적으로 준 값).
  2. OS 키체인 — macOS Keychain / Windows Credential Manager / (리눅스) Secret Service. `keyring` 패키지.
  3. 폴백 파일 — `~/.config/tft-advisor/credentials.toml` (파일 0600, 디렉터리 0700).
     키체인 백엔드가 없는 환경(헤드리스 리눅스 등)에서만 쓰인다.
  4. 없음.

쓰기(`save_api_key`)는 키체인을 먼저 시도하고, 안 되면 폴백 파일에 쓴다. 지우기(`delete_api_key`)는
두 곳을 모두 지운다(환경변수는 이 프로세스가 건드리지 않는다 — 셸/OS의 몫이다).

값은 절대 로그·예외 메시지·문자열 표현에 넣지 않는다. 사람에게 보여 줄 때는 `mask()`(`sk-…abcd`)만 쓴다.

테스트용 훅(실제 키체인·홈 디렉터리를 건드리지 않게 한다):
  - `TFT_ADVISOR_CONFIG_HOME` — 폴백 파일이 들어갈 디렉터리를 갈아 끼운다.
  - `TFT_ADVISOR_KEYRING=0` — 키체인 경로를 끈다.
  - `refresh()` — 프로세스 안 캐시를 비운다.
"""
from __future__ import annotations

import logging
import os
import stat
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

ENV_VAR = "TYPESAFE_API_KEY"
"""1순위. 이 값이 있으면 키체인·파일은 읽지도 않는다."""

SERVICE = "tft-advisor"
ACCOUNT = "typesafe-api-key"
"""키체인 항목 이름. macOS에서는 '로그인' 키체인의 일반 암호 `tft-advisor / typesafe-api-key`."""

CONFIG_HOME_ENV = "TFT_ADVISOR_CONFIG_HOME"
KEYRING_ENV = "TFT_ADVISOR_KEYRING"
CREDENTIALS_FILE = "credentials.toml"
DIR_MODE = 0o700
FILE_MODE = 0o600

SOURCE_LABELS = {
    "env": f"환경변수 {ENV_VAR}",
    "keyring": "OS 키체인",
    "file": "폴백 파일",
    "none": "없음",
}


# ---------------------------------------------------------------------------
# 값 가리기
# ---------------------------------------------------------------------------


def mask(key: str | None) -> str:
    """사람에게 보여 줄 힌트. 앞 3글자와 뒤 4글자만 남긴다 — `sk-…abcd`.

    짧은 키(테스트·오타)는 길이만 알려 준다. **이 함수를 거치지 않은 키 값은 화면·로그에 나가지 않는다.**
    """
    k = (key or "").strip()
    if not k:
        return "(없음)"
    if len(k) < 8:
        return "…" + ("*" * len(k))
    return f"{k[:3]}…{k[-4:]}"


# ---------------------------------------------------------------------------
# 폴백 파일
# ---------------------------------------------------------------------------


def config_home() -> Path:
    """폴백 파일이 들어갈 디렉터리. **저장소 밖**(홈)이다."""
    override = os.environ.get(CONFIG_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "tft-advisor"


def fallback_path() -> Path:
    return config_home() / CREDENTIALS_FILE


def _read_fallback() -> str | None:
    path = fallback_path()
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except Exception:   # noqa: BLE001 — 깨진 파일 때문에 앱이 죽지 않는다
        log.warning("자격 증명 파일을 읽지 못했다: %s", path)
        return None
    value = data.get("typesafe", {}).get("api_key")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_fallback(key: str) -> Path:
    """0700 디렉터리 안에 0600 파일로 원자적으로 쓴다(임시 파일 → rename)."""
    path = fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(DIR_MODE)
    except OSError:   # pragma: no cover — 권한을 못 바꾸는 파일시스템
        log.debug("디렉터리 권한을 %o로 바꾸지 못했다: %s", DIR_MODE, path.parent)
    text = (
        "# TFT Advisor — TypeSafe API 키. 이 파일은 저장소 밖(홈 디렉터리)에 있고 권한은 0600이다.\n"
        "# OS 키체인을 쓸 수 없을 때만 쓰인다. 사람이 직접 고칠 필요는 없다(설정 화면에서 저장·삭제).\n"
        "[typesafe]\n"
        f'api_key = "{_toml_escape(key)}"\n'
    )
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    try:
        path.chmod(FILE_MODE)
    except OSError:   # pragma: no cover
        log.debug("파일 권한을 %o로 바꾸지 못했다: %s", FILE_MODE, path)
    return path


def _delete_fallback() -> bool:
    path = fallback_path()
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        log.warning("자격 증명 파일을 지우지 못했다: %s", path)
        return False
    return True


def fallback_permissions() -> tuple[int | None, int | None]:
    """(파일 모드, 디렉터리 모드) — 없으면 None. 테스트·진단용."""
    path = fallback_path()
    f = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    d = stat.S_IMODE(path.parent.stat().st_mode) if path.parent.exists() else None
    return f, d


# ---------------------------------------------------------------------------
# OS 키체인
# ---------------------------------------------------------------------------


class _Keyring(Protocol):   # pragma: no cover — 타입 힌트용
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


_cached_secret: str | None = None
_cached_source: str | None = None
"""키체인/파일에서 한 번 읽은 값(프로세스 안에서만). macOS가 읽을 때마다 허가를 묻지 않게 한다."""


def keyring_enabled() -> bool:
    return os.environ.get(KEYRING_ENV, "1").strip().lower() not in ("0", "off", "false", "no")


def _keyring() -> Any | None:
    """`keyring` 모듈(쓸 수 있는 백엔드가 있을 때). 테스트는 이 함수를 monkeypatch한다."""
    if not keyring_enabled():
        return None
    try:
        import keyring as kr
    except ImportError:
        return None
    try:
        backend = kr.get_keyring()
    except Exception:   # noqa: BLE001
        log.debug("keyring 백엔드를 얻지 못했다", exc_info=True)
        return None
    name = type(backend).__name__
    if "fail" in name.lower() or "null" in name.lower():
        return None   # keyring.backends.fail.Keyring — 쓸 수 있는 백엔드가 없다
    return kr


def keyring_available() -> bool:
    return _keyring() is not None


def keyring_label() -> str:
    """플랫폼별 키체인 이름(사용자에게 보여 주는 말)."""
    if sys.platform == "darwin":
        return "macOS 키체인"
    if sys.platform.startswith("win"):
        return "Windows 자격 증명 관리자"
    return "OS 키체인"


def _keyring_get() -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        value = kr.get_password(SERVICE, ACCOUNT)
    except Exception:   # noqa: BLE001 — 키체인이 잠겼거나 사용자가 거부했다
        log.warning("키체인에서 키를 읽지 못했다(%s)", keyring_label())
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


# ---------------------------------------------------------------------------
# 해석
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyInfo:
    """키가 **어디에** 있는지. 값은 담지 않는다 — 가린 힌트(`hint`)만 담는다."""

    source: str = "none"          # env | keyring | file | none
    hint: str = "(없음)"
    path: Path | None = None      # source == "file" 일 때만

    @property
    def present(self) -> bool:
        return self.source != "none"

    @property
    def store(self) -> str:
        if self.source == "keyring":
            return keyring_label()
        return SOURCE_LABELS.get(self.source, self.source)

    def describe(self) -> str:
        if not self.present:
            return "저장된 TypeSafe API 키가 없다."
        if self.source == "env":
            return f"{self.store} 의 키를 쓰고 있다 — {self.hint} (환경변수가 가장 먼저 쓰인다)"
        if self.source == "file":
            return f"{self.store}에 저장돼 있다 — {self.hint} ({self.path})"
        return f"{self.store}에 저장돼 있다 — {self.hint}"


def _env_key() -> str | None:
    value = os.environ.get(ENV_VAR, "").strip()
    return value or None


def _stored() -> tuple[str | None, str]:
    """(키, 출처) — 키체인 → 폴백 파일. 환경변수는 보지 않는다."""
    if _cached_secret is not None:
        return _cached_secret, _cached_source or "keyring"
    value = _keyring_get()
    if value:
        _remember(value, "keyring")
        return value, "keyring"
    value = _read_fallback()
    if value:
        _remember(value, "file")
        return value, "file"
    return None, "none"


def _remember(value: str, source: str) -> None:
    global _cached_secret, _cached_source
    _cached_secret, _cached_source = value, source


def refresh() -> None:
    """프로세스 안 캐시를 비운다(저장·삭제 뒤, 그리고 테스트에서)."""
    global _cached_secret, _cached_source
    _cached_secret = _cached_source = None


def resolve_api_key() -> str | None:
    """환경변수 → 키체인 → 폴백 파일 → None. **앱에서 키 값을 얻는 유일한 함수.**"""
    return _env_key() or _stored()[0]


def key_info() -> KeyInfo:
    """키의 출처와 가린 힌트. 값은 돌려주지 않는다(화면·로그에 쓰기 위한 것)."""
    env = _env_key()
    if env:
        return KeyInfo("env", mask(env))
    value, source = _stored()
    if value is None:
        return KeyInfo()
    return KeyInfo(source, mask(value), fallback_path() if source == "file" else None)


def key_present() -> bool:
    """키가 어디에든 있는가. 설정 화면 체크박스·트레이 토글이 보는 값."""
    return resolve_api_key() is not None


def stored_key_present() -> bool:
    """환경변수를 뺀, 이 앱이 **저장해 둔** 키가 있는가(삭제 버튼 활성화용)."""
    return _stored()[0] is not None


# ---------------------------------------------------------------------------
# 저장 / 삭제
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SaveResult:
    ok: bool
    store: str = ""          # keyring | file
    message: str = ""
    hint: str = "(없음)"
    path: Path | None = None
    shadowed_by_env: bool = False


def save_api_key(key: str) -> SaveResult:
    """키체인에 저장한다(안 되면 폴백 파일). 키 값은 로그에 남기지 않는다."""
    value = (key or "").strip()
    if not value:
        return SaveResult(False, message="키가 비어 있다 — 입력한 뒤 다시 [저장]을 누르라.")
    if any(ch.isspace() for ch in value):
        return SaveResult(False, message="키에 공백이 들어 있다 — 복사할 때 줄바꿈·공백이 섞이지 않았는지 보라.")
    hint = mask(value)
    shadowed = _env_key() is not None
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, ACCOUNT, value)
        except Exception as e:   # noqa: BLE001 — 키체인 거부 → 폴백 파일
            log.warning("키체인에 저장하지 못했다(%s): %s", keyring_label(), type(e).__name__)
        else:
            refresh()
            _remember(value, "keyring")
            return SaveResult(True, "keyring", f"{keyring_label()}에 저장했다 — {hint}", hint,
                              shadowed_by_env=shadowed)
    try:
        path = _write_fallback(value)
    except OSError as e:
        return SaveResult(False, message=f"저장하지 못했다: {type(e).__name__}: {e}")
    refresh()
    _remember(value, "file")
    return SaveResult(True, "file", f"{keyring_label()}을 쓸 수 없어 파일에 저장했다(권한 0600) — {hint}\n{path}",
                      hint, path, shadowed_by_env=shadowed)


@dataclass(frozen=True)
class DeleteResult:
    removed: tuple[str, ...] = ()
    message: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.removed)


def delete_api_key() -> DeleteResult:
    """키체인과 폴백 파일 양쪽에서 지운다. 환경변수는 건드리지 않는다(셸/OS의 몫)."""
    removed: list[str] = []
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, ACCOUNT)
        except Exception:   # noqa: BLE001 — 항목이 없으면 keyring이 예외를 던진다
            log.debug("키체인에 지울 항목이 없다", exc_info=True)
        else:
            removed.append("keyring")
    if _delete_fallback():
        removed.append("file")
    refresh()
    if not removed:
        msg = "지울 키가 없었다."
    else:
        names = {"keyring": keyring_label(), "file": "폴백 파일"}
        msg = ", ".join(names[r] for r in removed) + "에서 키를 지웠다."
    if _env_key() is not None:
        msg += f" 환경변수 {ENV_VAR} 는 그대로다 — 앱은 그 값을 계속 쓴다(셸에서 지우라)."
    return DeleteResult(tuple(removed), msg)


# ---------------------------------------------------------------------------
# 연결 테스트 (실제 Jev 호출 1회)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    message: str
    reason: str = ""              # ok | auth | quota | network | timeout | server | sdk | empty | bad
    latency_ms: float | None = None

    def __str__(self) -> str:
        return self.message


PING_STATE: dict[str, Any] = {"check": "connection test", "value": 1}
PING_QUESTIONS: dict[str, dict[str, Any]] = {
    "ping": {
        "type": "score",
        "instructions": "Ignore the content. Answer with the lowest level. This is a connection test.",
        "criteria": ["lowest level", "highest level"],
    },
}
"""가장 싼 호출: 질문 1개·레벨 2개·state 두 줄. 연결 테스트는 이것 **한 번**만 한다."""


def _live_ping(key: str, *, model: str = "jev-latest", timeout_s: float = 15.0) -> None:
    """진짜 TypeSafe 호출 1회. 성공하면 조용히 돌아오고, 실패하면 SDK 예외를 그대로 올린다."""
    from typesafe_sdk import RetryPolicy, TypeSafeClient

    client = TypeSafeClient(api_key=key, model=model, timeout=timeout_s,
                            retry=RetryPolicy(max_retries=0))
    try:
        client.system_one(PING_STATE, PING_QUESTIONS, model=model)
    finally:
        try:
            client.close()
        except Exception:   # noqa: BLE001
            pass


_REASON_MESSAGES = {
    "auth": "키가 거부됐다 — 키가 잘못됐거나 만료됐다. TypeSafe 대시보드에서 다시 복사해 보라.",
    "quota": "요청 한도(쿼터·rate limit)에 걸렸다 — 키는 유효하다. 잠시 뒤 다시 시도하라.",
    "network": "네트워크에 연결하지 못했다 — 인터넷·방화벽·프록시를 확인하라.",
    "timeout": "응답이 제때 오지 않았다 — 네트워크가 느리거나 서버가 붐빈다. 다시 시도하라.",
    "server": "TypeSafe 서버 쪽 오류다 — 키 문제가 아니다. 잠시 뒤 다시 시도하라.",
}


def _classify(exc: BaseException) -> str:
    """예외 → 사람이 읽을 이유 코드. `advisor.jev_client`의 판정 규칙을 그대로 쓴다."""
    from .advisor.jev_client import classify_exception
    from .contracts import FallbackReason

    reason = classify_exception(exc)
    return {
        FallbackReason.AUTH: "auth",
        FallbackReason.RATE_LIMITED: "quota",
        FallbackReason.CONNECTION: "network",
        FallbackReason.TIMEOUT: "timeout",
        FallbackReason.SERVER_ERROR: "server",
        FallbackReason.OVERLOADED: "server",
    }.get(reason, "bad")


def verify_key(key: str | None = None, *, caller: Any = None, model: str = "jev-latest",
               timeout_s: float = 15.0) -> VerifyResult:
    """연결 테스트 — **Jev를 딱 한 번** 부른다(가장 싼 질문 1개).

    `key`를 주지 않으면 지금 해석되는 키를 쓴다. `caller`를 주면 그것을 대신 부른다
    (테스트는 여기에 가짜를 꽂는다 — 테스트에서 진짜 호출은 절대 일어나지 않는다).
    """
    value = (key or "").strip() or resolve_api_key()
    if not value:
        return VerifyResult(False, "테스트할 키가 없다 — 키를 입력하거나 저장한 뒤 누르라.", "empty")
    fn = caller or (lambda k: _live_ping(k, model=model, timeout_s=timeout_s))
    t0 = time.perf_counter()
    try:
        fn(value)
    except ImportError:
        return VerifyResult(False, "typesafe_sdk 가 설치돼 있지 않다 — `pip install -e \".[advisor]\"`.", "sdk")
    except BaseException as exc:   # noqa: BLE001 — 실패 이유를 화면에 옮기는 것이 이 함수의 일이다
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        reason = _classify(exc)
        detail = _REASON_MESSAGES.get(reason)
        if detail is None:
            detail = f"호출이 실패했다: {type(exc).__name__}"
        log.warning("연결 테스트 실패: %s (%s)", reason, type(exc).__name__)   # 키 값은 남기지 않는다
        return VerifyResult(False, f"실패 — {detail}", reason)
    ms = (time.perf_counter() - t0) * 1000
    return VerifyResult(True, f"성공 — TypeSafe에 연결했고 키가 유효하다 ({ms:.0f}ms).", "ok", ms)


__all__ = [
    "ENV_VAR", "SERVICE", "ACCOUNT", "CONFIG_HOME_ENV", "KEYRING_ENV",
    "KeyInfo", "SaveResult", "DeleteResult", "VerifyResult",
    "config_home", "delete_api_key", "fallback_path", "fallback_permissions", "key_info", "key_present",
    "keyring_available", "keyring_enabled", "keyring_label", "mask", "refresh", "resolve_api_key",
    "save_api_key", "stored_key_present", "verify_key",
]
