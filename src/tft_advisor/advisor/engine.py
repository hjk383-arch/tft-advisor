"""추천 엔진 진입점: `Advisor.recommend(state)` / `Advisor.advise(state)` → `Recommendation | None`.

파이프라인(설계 §1): 신뢰도 필터 → 파생값 → 1차 필터 → 폴백 프록시(항상 먼저) → Jev state/질문(힌트 포함)
→ 게이트웨이(캐시·서킷·타임아웃) → 합성 → Recommendation(+debug).

모드(§1.1)
- planning: 덱 + 상점 + 아이템 / augment_select: 덱 + 증강 + 아이템
- carousel: Jev 호출 없음. 직전 추천에 component_priority만 새로 계산해 세션의 직전 추천으로 저장.
  직전 추천이 없으면 통계 전용 경로(상점 제외, Jev 미호출, fallback_reason=jev_disabled)
- combat / item_select / unknown: 직전 추천 그대로(없으면 None)
- loading / game_over: 세션 초기화 후 None

Jev 백엔드: "mock"(결정적, 네트워크 없음) | "live"(TypeSafe) | "off"(jev_disabled 폴백) | JevBackend 인스턴스.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from ..config import Settings, Weights, load_settings, load_weights
from ..contracts import BoardPlan, FallbackReason, GameState, Recommendation, ScreenMode, ShopSlotKind
from .board_plan import plan_board
from .candidates import Candidate, augment_comp_fit, late_cfg, prefilter, unit_stage
from .features import View, build_view, comp_level, craftable_items, global_level, is_late, item_fit
from .jev_client import GatewayResult, JevBackend, JevGateway, LiveJevBackend, MockJevBackend
from .jev_state import NameBook, StateParts, build_state, state_hash
from .questions import (
    A1_LEVELS,
    A2_LEVELS_HP,
    A2_LEVELS_NOHP,
    A3,
    C1_LEVELS,
    C2_LEVELS,
    C3_LEVELS,
    C4,
    C4_UNDECIDED,
    HOLD,
    HOLD_HP,
    HOLD_NOHP,
    QUESTIONS_VERSION,
    S1_NOBOARD_LEVELS,
    S1_OWNED_LEVELS,
    S2_LEVELS,
    S3_LEVELS,
    UNDECIDED,
    QuestionSet,
    a1,
    a2,
    c1,
    c2,
    c3,
    i1,
    s1,
    s2,
    s3,
)
from .scoring import Scorer
from .stage_boards import MetaTftStageBoards, StageBoardSource
from .stats_source import AdvisorStats, load_stats

log = logging.getLogger(__name__)

# settings.advisor.timeout_s(추천 1회 전체 예산)에서 Jev 호출 뒤 합성·Recommendation 생성 몫으로 남겨 두는 시간(초).
# 실측 코드 경로 3~10ms라 넉넉히 잡는다. Jev 한도 = min(jev_retry_budget_s, timeout_s − 경과 − 이 값).
CODE_RESERVE_S = 0.1

BackendSpec = Literal["mock", "live", "off"] | JevBackend


@dataclass
class Session:
    """게임 1판 동안의 advisor 기억(앱 재시작 시 초기화)."""

    prev_shown: list[str] = field(default_factory=list)
    prev_sig: str | None = None
    equipped_tracked: Counter[str] = field(default_factory=Counter)
    prev_bench_items: Counter[str] | None = None
    last: Recommendation | None = None
    last_hash: str | None = None


def resource_signature(view: View, owned: list[str], stats: AdvisorStats) -> str:
    """§8.4: 완성템+상징+유물/찬란한(장착분 포함) + 증강 + 2성 이상 4~5코스트 유닛. 재료는 제외."""
    units = sorted({u.id for u in view.units if (u.star or 1) >= 2 and (stats.champion_cost(u.id) or 0) >= 4})
    payload = "|".join([",".join(sorted(owned)), ",".join(sorted(a.id for a in view.augments)), ",".join(units)])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class Advisor:
    """추천 엔진. 앱 수명 동안 1개(Jev 클라이언트·캐시·세션 유지)."""

    def __init__(self, *, stats: AdvisorStats | None = None, settings: Settings | None = None,
                 weights: Weights | None = None, backend: BackendSpec = "mock") -> None:
        self.settings = settings or load_settings()
        self.w = weights or load_weights()
        self.stats = stats or load_stats()
        self.backend_name, be = self._resolve_backend(backend)
        self.gateway = JevGateway(be, self.settings.advisor)
        self.session = Session()
        self._loop: asyncio.AbstractEventLoop | None = None
        # 스테이지별 실제 보드 통계(MetaTFT Early Comps, 21 §11). stats에 stage_stats가 없거나 비었으면 None(항 0).
        # 테스트는 가짜 StageBoardSource를 넣을 수 있다.
        self.stage_boards: StageBoardSource | None = MetaTftStageBoards.from_stats(self.stats, self.w.board_plan)

    def _resolve_backend(self, spec: BackendSpec) -> tuple[str, JevBackend | None]:
        if spec == "mock":
            return "mock", MockJevBackend()
        if spec == "live":
            return "live", LiveJevBackend(self.settings.advisor)
        if spec == "off":
            return "off", None
        return getattr(spec, "name", "custom"), spec   # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    def advise(self, state: GameState) -> Recommendation | None:
        """동기 래퍼. advisor 전용 이벤트 루프를 재사용한다(Jev 클라이언트 연결 유지). 실행 중인 루프 안에서는 쓰지 말 것."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(self.recommend(state))

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.run_until_complete(self.gateway.aclose())
            self._loop.close()
        self._loop = None

    async def aclose(self) -> None:
        await self.gateway.aclose()

    def reset(self) -> None:
        """새 게임: 세션(히스테리시스·장착 추적·직전 추천)과 캐시를 비운다."""
        self.session = Session()
        self.gateway.clear_cache()

    async def recommend(self, state: GameState) -> Recommendation | None:
        t0 = time.perf_counter()
        mode = state.screen_mode
        if mode in (ScreenMode.LOADING, ScreenMode.GAME_OVER):
            self.reset()
            return None
        if mode in (ScreenMode.COMBAT, ScreenMode.ITEM_SELECT, ScreenMode.UNKNOWN):
            return self.session.last
        if mode == ScreenMode.CAROUSEL:
            if self.session.last is not None:
                return self._carousel_update(state, t0)
            return await self._full(state, mode, t0, use_jev=False)   # §1.1: carousel은 Jev를 부르지 않는다
        return await self._full(state, mode, t0)

    def rescore_shop(self, state: GameState, previous: Recommendation | None = None) -> Recommendation | None:
        """상점 카드만 다시 채점한다(21 §7) — 전투 중 새로고침·라운드 시작 새 상점용. 수 ms, **Jev를 새로 부르지 않는다**.

        - 목표 덱·보드 배치·아이템·증강·재료 우선순위는 `previous`(없으면 직전 추천) 그대로 둔다.
        - 상점 점수 = 코드·통계 채점(`Scorer.shop_advice`). 경로 가중(`rel`)은 직전 요청의 덱 점수를 그대로 쓴다.
        - Jev 상점 판단은 **이 상태와 똑같은 요청이 캐시에 있을 때만** 쓴다(`gateway.cached`, 네트워크 없음).
        - 결과를 세션의 직전 추천으로 저장한다 — 이후 전투 화면의 `recommend()`도 새 상점을 돌려준다.
        - 직전 추천이 없으면 None, 상점을 못 읽었으면(`shop` 신뢰도 미달) `previous`를 그대로 돌려준다.
        """
        t0 = time.perf_counter()
        prev = previous if previous is not None else self.session.last
        if prev is None:
            return None
        view = build_view(state, self.stats, self.settings.vision.state_min_confidence, None)
        if view.shop is None:
            return prev
        if view.items_known and not (view.units_complete or view.equipped_seen):
            view.equipped = list(self.session.equipped_tracked.elements())   # 세션을 바꾸지 않고 추적값만 읽는다
        prev_ids = [t.comp_id for t in prev.target_comps]
        cands, pool = prefilter(view, self.stats, self.w, self.settings.advisor.max_candidate_comps, prev_ids)
        names = NameBook(self.stats)
        parts = build_state(view, cands, self.stats, names, include_shop=True, include_offer=False)
        key = state_hash(parts.state, QUESTIONS_VERSION, self.settings.advisor.jev_model)
        answers = self.gateway.cached(key)
        scorer = self._scorer(view, cands, pool, answers, comp_labels=parts.comp_labels, prev_shown=prev_ids,
                              sig_unchanged=True)
        scorer.score_comps()
        # 목표 덱 고정: 직전 요청의 덱 상대 점수(rel)를 그대로 쓴다(없는 덱만 이번 프록시 값)
        prev_final = {c["comp_id"]: c["final"] for c in prev.debug.get("candidates", []) if "final" in c}
        mx = max(prev_final.values(), default=0.0)
        if mx > 0:
            for cid, f in prev_final.items():
                if cid in scorer.rel:
                    scorer.rel[cid] = f / mx
        glv = global_level(view, [c.comp for c in cands] or pool)
        shop = scorer.shop_advice(glv, parts.shop_desc_lost)
        ms = (time.perf_counter() - t0) * 1000
        rec = prev.model_copy(update={
            "shop": shop, "created_at": datetime.now(timezone.utc), "latency_ms": ms,
            "debug": {**prev.debug, "shop_rescore": {"mode": ScreenMode(state.screen_mode).value, "state_hash": key,
                                                     "jev_cached": answers is not None, "ms": round(ms, 2),
                                                     "shop_jev_state": parts.state.get("shop")}},
        })
        self.session.last = rec
        log.info("rescore_shop mode=%s jev_cached=%s %.1fms", ScreenMode(state.screen_mode).value, answers is not None, ms)
        return rec

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------
    def _view(self, state: GameState) -> View:
        s = self.session
        min_conf = self.settings.vision.state_min_confidence
        view = build_view(state, self.stats, min_conf, None)
        # §4.3(c) equipped_tracked: 연속된 두 요청 모두 items 신뢰 가능할 때만 추적.
        # vision이 장착분을 직접 읽었으면(`equipped_seen`) 추정할 필요가 없다 — 추적값으로 덮지 않는다.
        known = view.units_complete or view.equipped_seen   # 부분 확인이면 유닛 장착분이 모자라다
        if view.items_known:
            cur = view.bench_owned_counter()
            if s.prev_bench_items is not None and not known:
                gone = s.prev_bench_items - cur
                back = cur - s.prev_bench_items
                s.equipped_tracked.update(gone)
                for iid, n in back.items():
                    if s.equipped_tracked[iid]:
                        s.equipped_tracked[iid] -= min(n, s.equipped_tracked[iid])
                s.equipped_tracked = +s.equipped_tracked
            s.prev_bench_items = cur
            if not known:
                view.equipped = list(s.equipped_tracked.elements())
        else:
            s.prev_bench_items = None
        return view

    def _carousel_update(self, state: GameState, t0: float) -> Recommendation:
        last = self.session.last
        assert last is not None
        view = self._view(state)
        scorer = self._scorer(view, *self._candidates(view), answers=None)
        scorer.score_comps()
        prio = scorer.component_priority(last.target_comps)
        rec = last.model_copy(update={
            "component_priority": prio, "latency_ms": (time.perf_counter() - t0) * 1000,
            "created_at": datetime.now(timezone.utc),
            "debug": {**last.debug, "mode": ScreenMode.CAROUSEL.value, "carousel_reuse": True},
        })
        self.session.last = rec   # carousel 직후 combat 등은 갱신된 component_priority를 돌려준다
        return rec

    def _board_plan(self, view: View, scorer: Scorer, glv: int | None) -> BoardPlan | None:
        """보드 배치 추천(21 §6): 1위 목표 덱 기준, 코드 전용(Jev 호출 없음)."""
        comp = scorer.shown[0]["cand"].comp if scorer.shown else None
        level = comp_level(view, comp) if comp is not None else glv
        try:
            return plan_board(view, self.stats, comp, level, scorer.s_now_table(glv), scorer.ko, self.w.board_plan,
                              stage=self.w.unit_stage, stage_board=self.stage_boards)
        except Exception:   # 부가 기능이 추천 전체를 막지 않게 한다
            log.exception("보드 배치 추천 실패")
            return None

    def _candidates(self, view: View) -> tuple[list[Candidate], list[Any]]:
        return prefilter(view, self.stats, self.w, self.settings.advisor.max_candidate_comps, self.session.prev_shown)

    def _scorer(self, view: View, cands: list[Candidate], pool: list[Any], answers=None, **kw) -> Scorer:
        owned = view.owned_pool(self.stats)
        craft = craftable_items(view.components, self.stats) if view.items_known else {}
        return Scorer(view=view, stats=self.stats, w=self.w, settings=self.settings, cands=cands, pool=pool,
                      owned=owned, craftable=craft, answers=answers, **kw)

    async def _full(self, state: GameState, mode: ScreenMode, t0: float, *, use_jev: bool = True) -> Recommendation:
        s = self.session
        view = self._view(state)
        cands, pool = self._candidates(view)
        owned = view.owned_pool(self.stats)
        sig = resource_signature(view, owned, self.stats)
        sig_unchanged = s.prev_sig is not None and sig == s.prev_sig
        common = dict(prev_shown=list(s.prev_shown), sig_unchanged=sig_unchanged)

        include_shop = mode == ScreenMode.PLANNING
        include_offer = mode == ScreenMode.AUGMENT_SELECT
        glv = global_level(view, [c.comp for c in cands] or pool)

        # ④ 폴백(프록시) 먼저: 힌트·폴백 결과 겸용
        proxy = self._scorer(view, cands, pool, None, **common)
        proxy.score_comps()

        names = NameBook(self.stats)
        parts = build_state(view, cands, self.stats, names, include_shop=include_shop, include_offer=include_offer)
        qs, labels = self._questions(view, cands, parts, names, proxy, glv, include_shop, include_offer)
        key = state_hash(parts.state, QUESTIONS_VERSION, self.settings.advisor.jev_model)

        if use_jev:
            budget = self.settings.advisor.timeout_s - (time.perf_counter() - t0) - CODE_RESERVE_S
            res = await self.gateway.ask(key, parts.state, qs.questions, qs.meta, budget_s=budget)
        else:
            res = GatewayResult(None, FallbackReason.JEV_DISABLED, f"mode {mode.value}: Jev not called by design")
        scorer = self._scorer(view, cands, pool, res.answers, comp_labels=parts.comp_labels,
                              aug_labels=labels["aug"], item_labels=labels["item"], **common)
        scorer.score_comps()
        targets = scorer.target_comps()
        shop = scorer.shop_advice(glv, parts.shop_desc_lost) if include_shop else []
        augment = scorer.augment_advice(parts.aug_desc_lost) if include_offer else None
        item = scorer.item_advice()
        prio = scorer.component_priority(targets)
        plan = self._board_plan(view, scorer, glv)

        debug: dict[str, Any] = {
            "mode": mode.value, "questions_version": QUESTIONS_VERSION, "backend": self.backend_name,
            "n_questions": len(qs), "question_ids": list(qs.questions), "jev_state": parts.state,
            "jev": res.answers.to_debug() if res.answers else None,
            "fallback_detail": res.detail or None, "resource_sig": sig, "sig_unchanged": sig_unchanged,
            "global_level": glv, "p_undecided": round(scorer.p_undecided, 4), "blind_late": scorer.blind_late,
            "unit_stage": {**{k: round(v, 4) for k, v in asdict(unit_stage(view, self.w)).items()},
                           "weights": {k: round(v, 4) for k, v in scorer.weight_map().items()}},
            "candidates": [
                {"comp_id": r["cand"].comp_id, "p": round(r["cand"].p, 4), "I": round(r["cand"].I, 4),
                 "A": round(r["cand"].A, 4), "U": round(r["cand"].U, 4), "S": round(r["cand"].S, 4),
                 "adj": round(r["cand"].adj, 4), "L": r["cand"].L, "T": r["cand"].T, "T_exp": r["cand"].T_exp,
                 "terms": {t: {"avail": x.avail, "gate": x.gate, "norm": round(x.norm, 4), "src": x.source}
                           for t, x in r["terms"].items()},
                 "score": round(r["score"], 4), "H": r["H"], "final": round(r["final"], 4)}
                for r in scorer.comp_rows
            ],
            "equipped_tracked": dict(s.equipped_tracked),
            **scorer.debug,
        }
        rec = Recommendation(
            target_comps=targets, shop=shop, augment=augment, item=item, component_priority=prio,
            board_plan=plan, jev_used=res.answers is not None, fallback_reason=res.reason if res.answers is None else None,
            state_hash=key, created_at=datetime.now(timezone.utc), debug=debug,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        s.prev_shown = [t.comp_id for t in targets]
        s.prev_sig = sig
        s.last = rec
        s.last_hash = key
        if res.answers is not None:
            log.info("recommend mode=%s jev=%s cached=%s q=%d tokens=%s model=%s %.0fms", mode.value,
                     res.answers.backend, res.answers.cached, len(qs), res.answers.input_tokens,
                     res.answers.model, rec.latency_ms)
        else:
            log.info("recommend mode=%s fallback=%s q=%d %.0fms", mode.value, res.reason, len(qs), rec.latency_ms)
        return rec

    # ------------------------------------------------------------------
    # 질문 묶음 (§1.1, §3)
    # ------------------------------------------------------------------
    def _questions(self, view: View, cands: list[Candidate], parts: StateParts, names: NameBook, proxy: Scorer,
                   glv: int | None, include_shop: bool, include_offer: bool) -> tuple[QuestionSet, dict[str, Any]]:
        qs = QuestionSet()
        st = parts.state
        labels: dict[str, Any] = {"aug": {}, "item": {}}
        avail = proxy.availability()
        comp_labels = parts.comp_labels

        for k, c in enumerate(cands):
            lab = comp_labels[k]
            if avail["item"]:
                qs.score(f"comp_item_fit_{k}", c1(k, lab), C1_LEVELS, c.I)
            if avail["augment"]:
                qs.score(f"comp_augment_fit_{k}", c2(k, lab), C2_LEVELS, c.A)
            if avail["board"] and "board" in st:
                qs.score(f"comp_board_fit_{k}", c3(k, lab), C3_LEVELS, c.U)

        if cands:
            crit: dict[str, Any] = {}
            hints: dict[str, float] = {}
            rows = {r["cand"].comp_id: r for r in proxy.comp_rows}
            for k, c in enumerate(cands):
                traits = ", ".join(names(t.id) for t in c.comp.key_traits[:4])
                carry = names(c.comp.carry) if c.comp.carry else "-"
                crit[comp_labels[k]] = f"`candidate_comps[{k}]`: main carry {carry}, key traits {traits}"
                hints[comp_labels[k]] = rows[c.comp_id]["final"]
            # 09 J1: 후반(스테이지 ≥ undecided_until_stage)에는 '너무 이르다' 선택지를 주지 않는다(합성도 무시한다)
            if not is_late(view, late_cfg(self.w).undecided_until_stage):
                crit[UNDECIDED] = C4_UNDECIDED
                hints[UNDECIDED] = 1.5 * sum(hints.values()) if not any(avail.values()) else 0.0
            qs.choice("comp_pick", C4, crit, hints)

        if include_shop and "shop" in st and view.shop is not None:
            owned_variant = "board" in st
            snow = proxy.s_now_table(glv)
            for i, slot in enumerate(view.shop):
                e = st["shop"][i]
                if "status" in e or slot.id is None:
                    continue
                if slot.kind == ShopSlotKind.CHAMPION:
                    lv = [x.format(i=i) for x in (S1_OWNED_LEVELS if owned_variant else S1_NOBOARD_LEVELS)]
                    qs.score(f"shop_now_{i}", s1(i, e["unit"], owned_variant), lv, snow.get(slot.id, 0.0))
                    qs.score(f"shop_path_{i}", s2(i, e["unit"]), S2_LEVELS, proxy.c_path(slot.id)[0])
                else:
                    lost = parts.shop_desc_lost.get(i, False)
                    qs.score(f"special_value_{i}", s3(i, e["special_offer"], e.get("description")), S3_LEVELS, 0.5,
                             force_low=lost)

        if include_offer and "augment_offer" in st:
            has_hp = "health_status" in st["game"]
            aug_crit: dict[str, Any] = {}
            aug_hints: dict[str, float] = {}
            for a_i, aug in enumerate(view.augment_offer):
                entry = st["augment_offer"][a_i]
                lost = parts.aug_desc_lost.get(a_i, False)
                best = 0.0
                for k, c in enumerate(cands):
                    h = augment_comp_fit(aug.id, c.comp, self.stats, self.w)
                    best = max(best, h * proxy.rel.get(c.comp_id, 0.0))
                    qs.score(f"aug_comp_fit_{a_i}_{k}", a1(a_i, entry["name"], k, comp_labels[k]), A1_LEVELS, h,
                             force_low=lost)
                qs.score(f"aug_standalone_{a_i}", a2(a_i, entry["name"], has_hp),
                         A2_LEVELS_HP if has_hp else A2_LEVELS_NOHP, 0.5, force_low=lost)
                aug_crit[entry["name"]] = entry["description"]
                aug_hints[entry["name"]] = best
                labels["aug"][a_i] = entry["name"]
            qs.choice("aug_pick", A3, aug_crit, aug_hints)

        craft = proxy.craftable
        if craft and "resources" in st and "item_components" in st["resources"]:
            has_hp = "health_status" in st["game"]
            crit = {}
            hints = {}
            max_bis = 0.0
            for x, (a, b) in craft.items():
                used_by = []
                for k, c in enumerate(cands):
                    if item_fit(x, c.comp, self.stats, self.w.item_fit) < self.w.item_fit.used_by_min:
                        continue
                    if x in c.comp.carry_bis_items and c.comp.carry:
                        used_by.append(f"{names(c.comp.carry)} (main carry of {comp_labels[k]})")
                    else:
                        holders = [u.id for u in c.comp.final_board if x in u.items]
                        if holders:
                            used_by.append(f"{names(holders[0])} (core unit of {comp_labels[k]})")
                    if len(used_by) >= 3:
                        break
                lab = names(x)
                crit[lab] = {"from": [names(a), names(b)], "used_by": used_by}
                bx = proxy.bis(x)
                hints[lab] = bx
                max_bis = max(max_bis, bx)
                labels["item"][lab] = x
            crit[HOLD] = HOLD_HP if has_hp else HOLD_NOHP
            hints[HOLD] = (max_bis + 0.3) if proxy.code_hold(max_bis) else 0.0
            qs.choice("item_pick", i1(has_hp, "board" in st), crit, hints)
        return qs, labels


# ---------------------------------------------------------------------------
# 편의 함수
# ---------------------------------------------------------------------------

_DEFAULT: dict[str, Advisor] = {}


def create_advisor(mode: Literal["mock", "live", "off", "auto"] = "auto", **kw: Any) -> Advisor:
    """Jev 백엔드 선택: 명시 mode("mock" | "live" | "off")가 우선, "auto"(기본)는 `settings.advisor.jev_backend`(기본 "mock").

    "auto"는 더 이상 TYPESAFE_API_KEY 유무로 live를 고르지 않는다(키가 설정된 사용자 환경에서 의도치 않은 과금 방지).
    live는 설정 `jev_backend = "live"` 또는 `create_advisor("live")`로만 켠다. 키 없이 live면 경고만 남기고
    게이트웨이가 인증 실패 폴백으로 처리한다. CLI `--no-jev`(Phase 4)는 `create_advisor("off")`로 연결한다.
    `settings=`를 kw로 주면 그 설정을 쓴다(없으면 load_settings()).
    """
    if mode == "auto":
        settings = kw.get("settings") or load_settings()
        kw["settings"] = settings
        mode = settings.advisor.jev_backend
    if mode == "live" and not LiveJevBackend.key_present():
        log.warning("jev_backend=live 이지만 TypeSafe API 키가 없습니다(환경변수·키체인·폴백 파일 모두)"
                    " → Jev 호출은 인증 실패 폴백이 됩니다")
    return Advisor(backend=mode, **kw)


def advise(state: GameState, mode: Literal["mock", "live", "off", "auto"] = "mock") -> Recommendation | None:
    """프로세스 공용 Advisor(모드별 1개)로 동기 추천. 앱은 `Advisor` 인스턴스를 직접 쓰는 것을 권장."""
    adv = _DEFAULT.get(mode)
    if adv is None:
        adv = _DEFAULT[mode] = create_advisor(mode)
    return adv.advise(state)


__all__ = ["Advisor", "Session", "advise", "create_advisor", "resource_signature", "FallbackReason"]
