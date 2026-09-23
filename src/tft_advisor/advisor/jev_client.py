"""Jev 호출 계층 (설계 §8): 백엔드(live/mock), 타임아웃·재시도 예산, state 해시 LRU 캐시, 서킷 브레이커,
예외 → `FallbackReason` 매핑.

- `LiveJevBackend`: `typesafe_sdk.AsyncTypeSafeClient` 래퍼. SDK는 이 클래스 안에서만 import한다.
  API 키는 `credentials.resolve_api_key()`가 정한다 — 환경변수 `TYPESAFE_API_KEY` → OS 키체인 → 폴백 파일.
  값은 SDK에 넘기기만 하고 로그·예외·`to_debug()` 어디에도 남기지 않는다.
- `MockJevBackend`: 네트워크 없음, 결정적. 질문마다 코드가 붙인 힌트(QMeta)로 답을 만든다. 실패 주입·답 덮어쓰기 가능.
- `JevGateway`: 엔진이 쓰는 유일한 진입점. `ask()`는 절대 예외를 올리지 않고 (답 | None, FallbackReason | None)을 준다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol

from .. import credentials
from ..config import AdvisorCfg
from ..contracts import FallbackReason
from .questions import QMeta

log = logging.getLogger(__name__)

RETRY_BACKOFF_INITIAL = 0.1
RETRY_BACKOFF_MAX = 0.2
RETRY_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
# 서킷 브레이커가 세는 일시적 실패
TRANSIENT = frozenset({
    FallbackReason.RATE_LIMITED, FallbackReason.OVERLOADED, FallbackReason.SERVER_ERROR,
    FallbackReason.TIMEOUT, FallbackReason.CONNECTION,
})


@dataclass(frozen=True)
class ScoreAns:
    score: float
    confidence: float
    probabilities: dict[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ChoiceAns:
    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class JevAnswers:
    scores: dict[str, ScoreAns] = field(default_factory=dict)
    choices: dict[str, ChoiceAns] = field(default_factory=dict)
    model: str | None = None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    call_ms: float | None = None
    backend: str = "live"      # live | mock
    cached: bool = False

    def to_debug(self) -> dict[str, Any]:
        return {
            "model": self.model, "request_id": self.request_id, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "call_ms": self.call_ms, "backend": self.backend, "cached": self.cached,
            "scores": {k: {"score": round(v.score, 4), "confidence": round(v.confidence, 4)} for k, v in self.scores.items()},
            "choices": {k: {"choice": v.choice, "confidence": round(v.confidence, 4),
                            "probabilities": {lk: round(p, 4) for lk, p in v.probabilities.items()}}
                        for k, v in self.choices.items()},
        }


class JevCallError(Exception):
    """백엔드 실패. reason은 §8.1 닫힌 집합."""

    def __init__(self, reason: FallbackReason, detail: str = "") -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


class JevBackend(Protocol):
    name: str

    async def ask(self, state: dict[str, Any], questions: dict[str, dict[str, Any]], meta: dict[str, QMeta],
                  model: str) -> JevAnswers: ...

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# 예외 매핑
# ---------------------------------------------------------------------------


def classify_exception(exc: BaseException) -> FallbackReason:
    """SDK/asyncio 예외 → FallbackReason (§8.1 판정 순서: auth → 429 → 529 → 5xx → timeout → connection → bad_request)."""
    if isinstance(exc, JevCallError):
        return exc.reason
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return FallbackReason.TIMEOUT
    try:
        import typesafe_sdk as ts
    except ImportError:   # pragma: no cover - SDK 미설치 환경
        return FallbackReason.BAD_REQUEST
    if isinstance(exc, ts.TypeSafeAPITimeoutError):
        return FallbackReason.TIMEOUT
    if isinstance(exc, ts.TypeSafeAPIConnectionError):
        return FallbackReason.CONNECTION
    if isinstance(exc, ts.TypeSafeAPIResponseValidationError):
        return FallbackReason.BAD_REQUEST
    if isinstance(exc, ts.TypeSafeAPIError):
        st = exc.status
        if st in (401, 403):
            return FallbackReason.AUTH
        if st == 429:
            return FallbackReason.RATE_LIMITED
        if st == 529:
            return FallbackReason.OVERLOADED
        if 500 <= st < 600:
            return FallbackReason.SERVER_ERROR
        return FallbackReason.BAD_REQUEST
    if isinstance(exc, ts.TypeSafeError) and "api key" in str(exc).lower():
        return FallbackReason.AUTH
    return FallbackReason.BAD_REQUEST


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


class LiveJevBackend:
    """AsyncTypeSafeClient 1개를 앱 수명 동안 유지(연결 재사용). 클라이언트는 첫 호출 시 현재 이벤트 루프에서 만든다."""

    name = "live"

    def __init__(self, cfg: AdvisorCfg) -> None:
        self.cfg = cfg
        self._client: Any = None

    @staticmethod
    def key_present() -> bool:
        """키가 **어디에든** 있는가 — 환경변수 → OS 키체인 → 폴백 파일(`credentials`가 정한 순서).

        설정 화면 체크박스·트레이 토글·`create_advisor()`가 모두 이 한 곳을 본다.
        """
        return credentials.key_present()

    def _make_client(self) -> Any:
        from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

        policy = RetryPolicy(
            max_retries=self.cfg.jev_max_retries,
            backoff_initial=RETRY_BACKOFF_INITIAL,
            backoff_max=RETRY_BACKOFF_MAX,
            http_statuses=set(RETRY_HTTP_STATUSES),
            respect_retry_after=True,
            timeout=self.cfg.jev_retry_budget_s,
        )
        # 키는 credentials가 정한 순서(환경변수 → 키체인 → 폴백 파일)로 읽어 SDK에 넘긴다.
        # 환경변수만 있을 때 넘기는 값도 같으므로 동작은 예전과 같다.
        return AsyncTypeSafeClient(api_key=credentials.resolve_api_key(), model=self.cfg.jev_model,
                                   retry=policy, timeout=self.cfg.jev_timeout_s)

    async def ask(self, state: dict[str, Any], questions: dict[str, dict[str, Any]], meta: dict[str, QMeta],
                  model: str) -> JevAnswers:
        if not self.key_present():
            raise JevCallError(FallbackReason.AUTH, "TypeSafe API key not set (env/keychain/file)")
        if self._client is None:
            self._client = self._make_client()
        t0 = time.perf_counter()
        res = await self._client.system_one(state, questions, model=model)
        call_ms = (time.perf_counter() - t0) * 1000
        out = JevAnswers(model=res.model, input_tokens=res.usage.input_tokens, output_tokens=res.usage.output_tokens,
                         call_ms=call_ms, backend=self.name)
        try:
            out.request_id = res.request_id
        except Exception:   # noqa: BLE001 - 헤더 없음
            out.request_id = None
        for qid, a in res.scores.items():
            out.scores[qid] = ScoreAns(float(a.score), float(a.confidence), {int(k): float(v) for k, v in a.probabilities.items()})
        for qid, a in res.choices.items():
            out.choices[qid] = ChoiceAns(str(a.choice), float(a.confidence), {str(k): float(v) for k, v in a.probabilities.items()})
        missing = set(questions) - set(out.scores) - set(out.choices)
        if missing:
            raise JevCallError(FallbackReason.BAD_REQUEST, f"answers missing for {sorted(missing)[:5]}")
        return out

    async def aclose(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            try:
                await client.aclose()
            except Exception:   # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


def mock_score(meta: QMeta, override: float | None = None, confidence: float = 0.8) -> ScoreAns:
    """힌트(0~1) → 기대 점수. 확률은 인접 두 레벨에 선형 분배."""
    top = meta.levels - 1
    x = (meta.hint if override is None else override) * top
    lo = int(x)
    frac = x - lo
    probs = {lv: 0.0 for lv in range(meta.levels)}
    probs[lo] = 1 - frac
    if lo + 1 <= top:
        probs[lo + 1] = frac
    return ScoreAns(score=x, confidence=confidence, probabilities=probs)


def mock_choice(meta: QMeta, override: str | None = None, confidence: float | None = None) -> ChoiceAns:
    labels = list(meta.hints)
    raw = {k: max(0.0, v) + 1e-6 for k, v in meta.hints.items()}
    if override is not None:
        raw = {k: (1.0 if k == override else 0.05) for k in labels}
    total = sum(raw.values())
    probs = {k: v / total for k, v in raw.items()}
    pick = max(labels, key=lambda k: (probs[k], -labels.index(k)))
    return ChoiceAns(choice=pick, confidence=probs[pick] if confidence is None else confidence, probabilities=probs)


class MockJevBackend:
    """결정적 가짜 Jev.

    - 기본: 질문 힌트로 답 생성(confidence 0.8).
    - `overrides`: {질문 ID: 0~1 점수 비율 | 선택 라벨} 로 특정 답 고정.
    - `confidence`: 전체 confidence 덮어쓰기(낮은 confidence 경로 테스트).
    - `fail`: FallbackReason 또는 예외 인스턴스 → 매 호출 실패.
    - `delay_s`: 인위 지연(타임아웃 테스트).
    """

    name = "mock"

    def __init__(self, *, overrides: dict[str, float | str] | None = None, confidence: float = 0.8,
                 fail: FallbackReason | BaseException | None = None, delay_s: float = 0.0) -> None:
        self.overrides = dict(overrides or {})
        self.confidence = confidence
        self.fail = fail
        self.delay_s = delay_s
        self.calls = 0
        self.last_state: dict[str, Any] | None = None
        self.last_questions: dict[str, dict[str, Any]] | None = None

    async def ask(self, state: dict[str, Any], questions: dict[str, dict[str, Any]], meta: dict[str, QMeta],
                  model: str) -> JevAnswers:
        self.calls += 1
        self.last_state, self.last_questions = state, questions
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail is not None:
            if isinstance(self.fail, FallbackReason):
                raise JevCallError(self.fail, "mock failure")
            raise self.fail
        out = JevAnswers(model=f"mock:{model}", backend=self.name, input_tokens=None, call_ms=0.0)
        for qid, m in meta.items():
            ov = self.overrides.get(qid)
            if m.kind == "score":
                out.scores[qid] = mock_score(m, ov if isinstance(ov, (int, float)) else None, self.confidence)
            else:
                ans = mock_choice(m, ov if isinstance(ov, str) else None)
                if self.confidence != 0.8:
                    ans = ChoiceAns(ans.choice, self.confidence, ans.probabilities)
                out.choices[qid] = ans
        return out

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


@dataclass
class GatewayResult:
    answers: JevAnswers | None
    reason: FallbackReason | None
    detail: str = ""


class JevGateway:
    """jev_enabled → 서킷/인증 비활성 → 캐시 → 백엔드 호출(총 예산 타임아웃) → 예외 매핑."""

    def __init__(self, backend: JevBackend | None, cfg: AdvisorCfg, *, clock=time.monotonic) -> None:
        self.backend = backend
        self.cfg = cfg
        self.clock = clock
        self._cache: OrderedDict[str, JevAnswers] = OrderedDict()
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._auth_disabled = False
        self.calls = 0

    def clear_cache(self) -> None:
        self._cache.clear()

    def cached(self, key: str) -> JevAnswers | None:
        a = self._cache.get(key)
        if a is not None:
            self._cache.move_to_end(key)
        return a

    async def ask(self, key: str, state: dict[str, Any], questions: dict[str, dict[str, Any]],
                  meta: dict[str, QMeta], *, budget_s: float | None = None) -> GatewayResult:
        """budget_s: 추천 1회 전체 예산(settings.advisor.timeout_s)에서 남은 시간. wait_for 한도는
        min(jev_retry_budget_s, budget_s). 남은 시간이 없으면 호출 없이 timeout 폴백(서킷 실패로 세지 않음)."""
        if not self.cfg.jev_enabled or self.backend is None:
            return GatewayResult(None, FallbackReason.JEV_DISABLED)
        hit = self.cached(key)
        if hit is not None:
            a = JevAnswers(**{**hit.__dict__, "cached": True})
            return GatewayResult(a, None)
        if self._auth_disabled:
            return GatewayResult(None, FallbackReason.AUTH, "disabled for session")
        if self.clock() < self._open_until:
            return GatewayResult(None, FallbackReason.CIRCUIT_OPEN)
        limit = self.cfg.jev_retry_budget_s if budget_s is None else min(self.cfg.jev_retry_budget_s, budget_s)
        if limit <= 0:
            return GatewayResult(None, FallbackReason.TIMEOUT, "overall budget exhausted")
        self.calls += 1
        try:
            ans = await asyncio.wait_for(self.backend.ask(state, questions, meta, self.cfg.jev_model),
                                         timeout=limit)
        except BaseException as exc:   # noqa: BLE001 - 모든 실패를 폴백으로
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise   # 바깥 취소(더 새로운 state가 옴)는 호출자에게 그대로 전달
            reason = classify_exception(exc)
            self._on_failure(reason)
            detail = type(exc).__name__ if not isinstance(exc, JevCallError) else exc.detail
            if reason == FallbackReason.BAD_REQUEST:
                # 코드 결함: 질문 JSON을 ERROR 로그에 남긴다(키 값은 요청에 없다)
                log.error("Jev bad_request (%s): questions=%s", exc, list(questions)[:60])
            else:
                log.warning("Jev 실패 → 폴백 %s (%s)", reason.value, detail)
            return GatewayResult(None, reason, detail)
        self._consecutive_failures = 0
        if self.cfg.cache_size > 0:
            self._cache[key] = ans
            self._cache.move_to_end(key)
            while len(self._cache) > self.cfg.cache_size:
                self._cache.popitem(last=False)
        return GatewayResult(ans, None)

    def _on_failure(self, reason: FallbackReason) -> None:
        if reason == FallbackReason.AUTH:
            self._auth_disabled = True
            return
        if reason in TRANSIENT:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.cfg.circuit_fail_threshold:
                self._open_until = self.clock() + self.cfg.circuit_cooldown_s
                self._consecutive_failures = 0
                log.warning("Jev 서킷 오픈 %.0fs", self.cfg.circuit_cooldown_s)

    async def aclose(self) -> None:
        if self.backend is not None:
            await self.backend.aclose()
