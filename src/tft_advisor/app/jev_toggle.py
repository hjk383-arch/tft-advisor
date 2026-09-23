"""실행 중 Jev 백엔드 전환 — 오버레이 트레이 메뉴 "Jev 실시간 판단 (과금)" 체크.

우선순위: **CLI > 트레이 토글 > settings.toml**
- `--jev {mock,live,off}` / `--no-jev` 로 띄운 실행은 그 값으로 **고정**된다(토글이 잠기고, 이유를 메뉴에 적는다).
- 플래그 없이 띄우면 `[advisor] jev_backend`(기본 mock)로 시작하고, 토글이 바꾼 값을 같은 파일에 저장한다
  (설정 화면과 같은 저장 경로 — 주석·순서 보존 + `settings.toml.bak` 백업).

스레드: 교체는 **UI 스레드 밖**에서 한다 — `create_advisor()`가 통계 DB·정적 데이터를 읽느라 수백 ms 걸린다.
교체 자체는 Jev를 부르지 않는다(워밍업하지 않는다). 새 백엔드는 **다음 추천**부터 쓰이고, 그때까지 화면에는
직전 추천이 그대로 남는다. 실제 갈아 끼우기는 추천 스레드 안에서 일어난다(`loop.set_advisor`).

안전: live는 키가 있을 때만 켤 수 있다. 키 판정은 `credentials.key_present()` 한 곳이다 —
환경변수 `TYPESAFE_API_KEY` → OS 키체인 → 폴백 파일(CLAUDE.md 고정 제약). 키는 설정 화면에서 넣는다.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from .setup import JEV_CHOICES, JEV_LABELS_SHORT, JEV_NO_KEY_NOTE, jev_key_present

log = logging.getLogger(__name__)

MENU_TEXT = "Jev 실시간 판단 (과금)"


def label(backend: str) -> str:
    return JEV_LABELS_SHORT.get(backend, backend)


@dataclass
class SwitchResult:
    """전환 결과(작업 스레드 → UI). `saved`: `settings.toml`에 썼는가."""

    ok: bool
    backend: str
    message: str
    saved: bool = False


class JevSwitcher:
    """실행 중인 advisor를 다른 Jev 백엔드로 갈아 끼운다.

    `loop`는 `LiveLoop`(없으면 교체만 기록한다), `builder`/`saver`는 테스트에서 갈아 끼우는 자리다.
    """

    def __init__(self, *, loop: Any = None, settings: Settings | None = None, backend: str = "mock",
                 config_dir: Path | None = None, locked_by_cli: str | None = None,
                 builder: Callable[[str, Settings | None], Any] | None = None,
                 saver: Callable[..., Any] | None = None) -> None:
        self.loop = loop
        self.settings = settings
        self.backend = backend if backend in JEV_CHOICES else "mock"
        self.config_dir = config_dir
        self.locked_by_cli = locked_by_cli if locked_by_cli in JEV_CHOICES else None
        self._builder = builder
        self._saver = saver
        self._lock = threading.Lock()
        self.busy = False
        self.switches = 0

    # ------------------------------------------------------------------ 상태
    @property
    def can_toggle(self) -> bool:
        """CLI가 이번 실행의 백엔드를 못박았으면 토글하지 않는다(CLI > 토글)."""
        return self.locked_by_cli is None

    def live_available(self) -> bool:
        return jev_key_present()

    def lock_note(self) -> str:
        return (f"CLI --jev {self.locked_by_cli} 로 고정된 실행이다 — 이번 실행에는 바꿀 수 없다"
                " (플래그 없이 실행하거나 설정 화면에서 바꾸라).")

    def blocked_reason(self, target: str = "live") -> str | None:
        """토글을 막아야 하면 이유, 아니면 None."""
        if not self.can_toggle:
            return self.lock_note()
        if target == "live" and self.backend != "live" and not self.live_available():
            return JEV_NO_KEY_NOTE
        return None

    # ------------------------------------------------------------------ 전환
    def build(self, name: str) -> Any:
        if self._builder is not None:
            return self._builder(name, self.settings)
        from ..advisor import create_advisor

        return create_advisor(name, settings=self.settings)

    def save(self, name: str) -> None:
        updates = {"advisor": {"jev_backend": name}}
        if self._saver is not None:
            self._saver(updates, config_dir=self.config_dir)
            return
        from .setup import save_settings

        save_settings(updates, config_dir=self.config_dir)

    def switch(self, name: str, *, persist: bool = True) -> SwitchResult:
        """백엔드를 바꾼다(부른 스레드에서 끝까지 한다). **UI 스레드에서 직접 부르지 말 것** — `set_backend()`."""
        if name not in JEV_CHOICES:
            return SwitchResult(False, self.backend, f"알 수 없는 Jev 백엔드: {name}")
        blocked = self.blocked_reason(name)
        if blocked is not None:
            return SwitchResult(False, self.backend, blocked)
        if name == self.backend:
            return SwitchResult(True, self.backend, f"이미 {label(name)}이다")
        with self._lock:
            self.busy = True
            try:
                try:
                    advisor = self.build(name)
                except Exception as e:   # noqa: BLE001 — 전환 실패로 앱이 죽지 않는다
                    log.warning("Jev 백엔드 %s 생성 실패", name, exc_info=True)
                    return SwitchResult(False, self.backend,
                                        f"Jev 백엔드를 만들지 못했다: {type(e).__name__}: {e}")
                if self.loop is not None:
                    try:
                        self.loop.set_advisor(advisor)
                    except Exception as e:   # noqa: BLE001
                        log.warning("advisor 교체 실패", exc_info=True)
                        return SwitchResult(False, self.backend,
                                            f"advisor를 바꾸지 못했다: {type(e).__name__}: {e}")
                self.backend = name
                self.switches += 1
                self._remember(name)
                note, saved = "", False
                if persist:
                    try:
                        self.save(name)
                        saved = True
                    except Exception as e:   # noqa: BLE001 — 저장이 실패해도 이번 실행에는 적용된다
                        log.warning("jev_backend 저장 실패", exc_info=True)
                        note = f" (설정 파일에 저장하지 못했다: {e} — 이번 실행에만 적용된다)"
            finally:
                self.busy = False
        return SwitchResult(True, name, f"Jev 백엔드를 {label(name)}(으)로 바꿨다 — 다음 추천부터 적용된다{note}",
                            saved)

    def set_backend(self, name: str, *, persist: bool = True,
                    on_done: Callable[[SwitchResult], None] | None = None) -> threading.Thread:
        """전환을 작업 스레드에서 시작한다(UI가 멈추지 않는다). 결과는 `on_done(SwitchResult)`."""

        def work() -> None:
            result = self.switch(name, persist=persist)
            if on_done is not None:
                try:
                    on_done(result)
                except Exception:   # noqa: BLE001
                    log.exception("Jev 전환 결과 콜백 실패")

        thread = threading.Thread(target=work, name="tft-jev-switch", daemon=True)
        thread.start()
        return thread

    def toggle_live(self, checked: bool, **kw: Any) -> threading.Thread:
        """체크 → live, 해제 → mock(off에서 해제해도 mock으로 간다)."""
        return self.set_backend("live" if checked else "mock", **kw)

    # ------------------------------------------------------------------
    def _remember(self, name: str) -> None:
        """메모리의 Settings에도 반영한다(트레이에서 연 설정 화면이 지금 값을 보여 주도록)."""
        try:
            self.settings.advisor.jev_backend = name   # type: ignore[union-attr]
        except Exception:   # noqa: BLE001
            log.debug("메모리 설정 갱신 실패", exc_info=True)


__all__ = ["JevSwitcher", "SwitchResult", "MENU_TEXT", "label"]
