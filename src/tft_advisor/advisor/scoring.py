"""합성 점수 (설계 §5~§7) → TargetComp / ShopAdvice / AugmentAdvice / ItemAdvice / component_priority.

Jev 답이 없으면(폴백) 같은 공식에 코드 프록시를 gate=1로 넣는다(§8.1). 원시 항은 debug로 남긴다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, Weights
from ..contracts import (
    AugmentAdvice,
    AugmentChoice,
    CompStats,
    ItemAdvice,
    ItemReadiness,
    ItemSuggestion,
    ReasonTag,
    ShopAdvice,
    ShopSlotKind,
    TargetComp,
)
from ..unit_status import units_reason
from .candidates import (
    Candidate,
    augment_comp_fit,
    augment_proxy,
    late_blind,
    late_cfg,
    scaled_board_weights,
    stat_norm,
    tempo_active,
    tier_score,
    unit_stage,
)
from .features import (
    View,
    board_at,
    buy_makes_2star,
    buy_makes_3star,
    comp_board_units,
    copies_owned,
    is_late,
    item_fit,
    key_trait_ids,
    next_buildup_board,
    resource_availability,
)
from .jev_client import JevAnswers
from .jev_state import NameBook, augment_entry
from .questions import HOLD, UNDECIDED
from .stats_source import AdvisorStats

TERMS = ("item", "augment", "board")
# 직전 표시 2·3위 덱의 히스테리시스 비율(§5.2) = weights `[comp] hysteresis_other_share`.
# §6.3 st(x) 전체(파생) 행 표본 수 할인(다른 덱 표본이 섞인 값이므로 더 강하게 수축) = weights `[item] overall_stat_games_factor`.
QPREFIX = {"item": "comp_item_fit", "augment": "comp_augment_fit", "board": "comp_board_fit"}


def clip01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _holder_reason(holder: str, deck: str | None) -> str:
    """"니달리 핵심 아이템" / 1위가 아닌 표시 덱 기준이면 "워윅 핵심 아이템(검은 가시 워윅)"."""
    return f"{holder} 핵심 아이템" + (f"({deck})" if deck else "")


@dataclass
class Term:
    avail: int
    gate: float
    norm: float
    source: str   # "jev" | "proxy" | "none"


@dataclass
class Scorer:
    """요청 1회분 합성기. engine이 만들고 필요한 순서로 메서드를 부른다."""

    view: View
    stats: AdvisorStats
    w: Weights
    settings: Settings
    cands: list[Candidate]
    pool: list[Any]                       # 사전 정리 후 전체 CompStats(S_now)
    owned: list[str]                      # 보유 완성템 풀 P
    craftable: dict[str, tuple[str, str]]
    answers: JevAnswers | None = None
    prev_shown: list[str] = field(default_factory=list)
    sig_unchanged: bool = False
    comp_labels: list[str] = field(default_factory=list)       # candidate_comps[k].name (comp_pick 라벨)
    aug_labels: dict[int, str] = field(default_factory=dict)   # augment_offer 인덱스 → aug_pick 라벨
    item_labels: dict[str, str] = field(default_factory=dict)  # item_pick 라벨 → 아이템 ID
    debug: dict[str, Any] = field(default_factory=dict)

    # --- Jev 답 접근 ---
    def gate(self, conf: float, force_low: bool = False) -> float:
        j = self.w.jev
        if force_low:
            return j.low_confidence_scale
        return 1.0 if conf >= j.min_confidence else j.low_confidence_scale

    def jscore(self, qid: str, levels: int, force_low: bool = False) -> tuple[float, float] | None:
        """(norm, gate) 또는 None(미전송/폴백)."""
        if self.answers is None:
            return None
        a = self.answers.scores.get(qid)
        if a is None:
            return None
        return clip01(a.score / (levels - 1)), self.gate(a.confidence, force_low)

    def jchoice(self, qid: str):
        if self.answers is None:
            return None
        return self.answers.choices.get(qid)

    # ------------------------------------------------------------------
    # 최종 덱 (§5.2)
    # ------------------------------------------------------------------
    def availability(self) -> dict[str, bool]:
        return resource_availability(self.view, self.owned)

    @property
    def owned_aug_desc_lost(self) -> bool:
        """보유 증강이 있고 그 설명이 모두 없음/의미 손실(jev_state.augment_entry 기준)."""
        if not self.view.augments:
            return False
        names = NameBook(self.stats)
        return all(augment_entry(a.id, self.stats, names)[1] for a in self.view.augments)

    def weight_map(self) -> dict[str, float]:
        """항별 가중. tempo는 후반 + 보드 미인식(tempo_active)일 때만 항으로 들어온다(09 J1).

        21 §10: 보드 항(wb)은 스테이지별 `unit_stage.deck_board_scale`배 — 2~3스테이지 유닛은 지나가는 빌드업이라
        목표 덱 선정에 작게 반영하고, 줄어든 몫은 아이템·증강에 wi:wa 비율로 옮긴다(redistribute). 합은 그대로 1.
        """
        cw = self.w.comp
        wi, wa, wb = scaled_board_weights(cw.wi, cw.wa, cw.wb, unit_stage(self.view, self.w).board_scale,
                                          self.w.unit_stage.redistribute)
        return {"item": wi, "augment": wa, "board": wb, "tempo": late_cfg(self.w).w_tempo}

    def comp_terms(self, k: int, c: Candidate, aug_override: tuple[float, float, str] | None = None) -> dict[str, Term]:
        avail = self.availability()
        proxy = {"item": c.I, "augment": c.A, "board": c.U}
        terms: dict[str, Term] = {}
        for t in TERMS:
            if t == "augment" and aug_override is not None:
                n, g, src = aug_override
                terms[t] = Term(1, g, n, src)
                continue
            if not avail[t]:
                terms[t] = Term(0, 1.0, 0.0, "none")
                continue
            # 보유 증강 설명이 전부 없거나 의미를 잃었으면(static 미수록 증강 등) Jev 증강 판단의 gate를 낮춘다(09 §3)
            js = self.jscore(f"{QPREFIX[t]}_{k}", 4, force_low=(t == "augment" and self.owned_aug_desc_lost))
            if js is not None:
                terms[t] = Term(1, js[1], js[0], "jev")
            else:
                terms[t] = Term(1, 1.0, proxy[t], "proxy")
        # 09 J1: 후반인데 보드를 모르면 레벨 템포(코드 계산)가 보드 항을 대신해 플랜(리롤/빠른 레벨업)을 반영한다
        if c.T is not None and tempo_active(self.view, self.owned, self.w):
            terms["tempo"] = Term(1, 1.0, c.T, "code")
        return terms

    def comp_score(self, c: Candidate, terms: dict[str, Term]) -> tuple[float, float, float]:
        """(comp_score, J, m)."""
        cw = self.w.comp
        wk = self.weight_map()
        J = sum(wk[t] * x.avail * x.gate * x.norm for t, x in terms.items())
        m = sum(wk[t] * x.avail * x.gate for t, x in terms.items())
        s = (1 - cw.wt) * J + (cw.wt + (1 - cw.wt) * (1 - m)) * c.S
        return s, J, m

    def score_comps(self) -> None:
        cw = self.w.comp
        self.comp_rows: list[dict[str, Any]] = []
        for k, c in enumerate(self.cands):
            terms = self.comp_terms(k, c)
            s, J, m = self.comp_score(c, terms)
            H = self.hysteresis_weight(c.comp_id)
            final = min(1.0, s + cw.hysteresis_bonus * H)
            self.comp_rows.append({"k": k, "cand": c, "terms": terms, "score": s, "J": J, "m": m, "H": H, "final": final})
        mx = max((r["final"] for r in self.comp_rows), default=0.0)
        self.rel = {r["cand"].comp_id: (r["final"] / mx if mx > 0 else 0.0) for r in self.comp_rows}

        # 방향 미정 판단(§5.2): Jev comp_pick P(undecided), 폴백은 "자원 신호 전무"
        # 09 J1: '초반' 판단은 스테이지 < undecided_until_stage 에서만. 후반 무자원은 blind_late(정보 부족)로 따로 표시
        pick = self.jchoice("comp_pick")
        late = is_late(self.view, late_cfg(self.w).undecided_until_stage)
        if late:
            self.p_undecided = 0.0
        elif pick is not None:
            self.p_undecided = pick.probabilities.get(UNDECIDED, 0.0)
        else:
            self.p_undecided = 1.0 if not any(self.availability().values()) else 0.0
        self.undecided = self.p_undecided >= cw.undecided_min_p
        self.blind_late = late_blind(self.view, self.owned, self.w)

        order = sorted(self.comp_rows, key=lambda r: (-r["final"], r["k"]))
        protected_top = bool(order) and order[0]["H"] >= 1.0   # 직전 1위가 그대로 1위면 타이브레이커로 뒤집지 않는다
        if (pick is not None and len(order) >= 2 and not protected_top
                and abs(order[0]["final"] - order[1]["final"]) < cw.tie_eps):
            p0 = pick.probabilities.get(self.comp_label(order[0]["k"]), 0.0)
            p1 = pick.probabilities.get(self.comp_label(order[1]["k"]), 0.0)
            if p1 > p0:
                order[0], order[1] = order[1], order[0]
                self.debug["comp_tiebreak"] = "comp_pick"
        ratio = cw.show_ratio_undecided if (self.undecided or self.blind_late) else cw.show_ratio
        limit = min(cw.max_shown, self.settings.ui.max_target_comps)
        shown = order[:1]
        for r in order[1:]:
            if len(shown) >= limit:
                break
            if order[0]["final"] > 0 and r["final"] >= ratio * order[0]["final"]:
                shown.append(r)
        self.order = order
        self.shown = shown
        no_bonus_rank = [r["cand"].comp_id for r in sorted(self.comp_rows, key=lambda r: (-r["score"], r["k"]))]
        self.kept_by_hysteresis = {
            r["cand"].comp_id for i, r in enumerate(order)
            if r["H"] and no_bonus_rank.index(r["cand"].comp_id) > i
        }

    def hysteresis_weight(self, comp_id: str) -> float:
        """H(c)(§5.2, Phase 3 수정): 시그니처 불변일 때 직전 1위 = 1, 직전 2·3위 = other_share, 그 외 0.

        직전 1위가 표시된 다른 덱보다 (1 - other_share)·bonus만큼 보호받는다(1위 널뛰기 방지).
        """
        if not self.sig_unchanged or comp_id not in self.prev_shown:
            return 0.0
        if comp_id == self.prev_shown[0]:
            return 1.0
        return self.w.comp.hysteresis_other_share

    def comp_label(self, k: int) -> str:
        return self.comp_labels[k] if k < len(self.comp_labels) else self.cands[k].comp_id

    # ------------------------------------------------------------------
    # TargetComp (§5.4)
    # ------------------------------------------------------------------
    def items_ready(self, c: Candidate) -> list[ItemReadiness]:
        comp = c.comp
        holder = comp.carry
        if not comp.carry_bis_items:
            return []
        if not self.view.items_known:
            return [ItemReadiness(item_id=x, status="missing", holder_unit_id=holder) for x in comp.carry_bis_items]
        P = Counter(self.owned)
        Q = Counter(self.view.components)
        status: list[str | None] = [None] * len(comp.carry_bis_items)
        for i, x in enumerate(comp.carry_bis_items):
            if P[x] > 0:
                P[x] -= 1
                status[i] = "owned"
        for i, x in enumerate(comp.carry_bis_items):
            if status[i] is not None:
                continue
            rec = self.stats.recipe(x)   # 제작 불가(유물·찬란한·증강 상징)는 craftable이 될 수 없다(QA WARN N5a)
            if rec is not None:
                a, b = rec
                need = Counter([a, b])
                if all(Q[k] >= n for k, n in need.items()):
                    Q.subtract(need)
                    status[i] = "craftable"
        return [ItemReadiness(item_id=x, status=s or "missing", holder_unit_id=holder)
                for x, s in zip(comp.carry_bis_items, status)]

    def target_comp(self, row: dict[str, Any]) -> TargetComp:
        c: Candidate = row["cand"]
        comp = c.comp
        B = comp_board_units(comp, self.stats)
        v = self.view
        reasons: list[str] = []
        if v.units_known:
            # 부분 확인이면 missing_units는 "확인된 유닛 중에 없다"는 뜻이다 — 근거 문구(units_reason)가 그 한계를 알린다
            have = {u.id for u in v.units}
            owned_units = [u for u in B if u in have]
            missing_units = [u for u in B if u not in have]
        else:
            owned_units, missing_units = [], []
        ready = self.items_ready(c)

        # 1. 자원 근거(최대 2개)
        wk = self.weight_map()
        contrib = []
        for t, term in row["terms"].items():
            if term.avail and term.norm * term.gate >= 0.5:
                contrib.append((wk[t] * term.avail * term.gate * term.norm, t))
        for _, t in sorted(contrib, key=lambda x: -x[0])[:2]:
            if t == "item":
                ok = [self.ko(r.item_id) for r in ready if r.status in ("owned", "craftable")][:3]
                reasons.append(f"핵심 아이템: {', '.join(ok)} → {self.ko(comp.carry)}" if ok else "보유 아이템 적합")
            elif t == "augment":
                reasons.append(self.augment_reason(comp))
            elif t == "tempo":
                reasons.append(f"레벨 템포 일치: {v.stage} 레벨 {v.level} (이 덱 평균 레벨 {c.T_exp})")
            else:
                core = {u.id for u in comp.final_board if u.is_core}
                m = sum(1 for u in owned_units if u in core)
                lead = "확인된 보유 유닛" if v.units_partial else "보유 유닛"
                reasons.append(f"{lead} {len(owned_units)}/{len(B)}기 (핵심 {m})")
        # 2. 통계
        reasons.append(f"메타 평균 {c.adj:.2f}등 · {comp.games or 0:,}판")
        # 3. 상태 플래그 (후반 무자원 안내는 오버레이가 근거 앞 3개만 보여 주므로 보드 플래그보다 앞에 둔다)
        if self.blind_late and not self.undecided:
            reasons.append("보유 아이템·증강 신호 없음: 레벨 템포·메타로 추정")
        units_note = units_reason(v.state, v.min_conf)   # 보드 미인식 / 부분 확인 / 구매 추적 기준
        if units_note is not None:
            reasons.append(units_note)
        if not v.items_known:
            reasons.append("아이템 미인식")
        if self.undecided:
            reasons.append("초반: 방향 미정")
        if comp.comp_id in self.kept_by_hysteresis:
            reasons.append("직전 추천 유지")
        if self.answers is None:
            reasons.append("Jev 미사용(통계 기반)")
        return TargetComp(
            comp_id=comp.comp_id, name=comp.name, score=clip01(row["final"]), carry=comp.carry,
            levelling=comp.levelling, reasons=reasons[:5], owned_units=owned_units, missing_units=missing_units,
            items_ready=ready, next_buildup_board=next_buildup_board(comp, c.L),
        )

    def augment_reason(self, comp) -> str:
        kt = key_trait_ids(comp)
        for a in self.view.augments:
            hit = set(self.stats.augment_traits(a.id)) & kt
            if hit:
                return f"특성 증강: {self.ko(a.id)} ({self.ko(sorted(hit)[0])})"
        name = self.ko(self.view.augments[0].id) if self.view.augments else "-"
        return f"증강 시너지: {name}"

    def ko(self, api_id: str | None) -> str:
        if api_id is None:
            return "-"
        return self.stats.name(api_id, "ko") or api_id

    def target_comps(self) -> list[TargetComp]:
        return [self.target_comp(r) for r in self.shown]

    # ------------------------------------------------------------------
    # 상점 (§5.3)
    # ------------------------------------------------------------------
    def s_now_table(self, level: int | None) -> dict[str, float]:
        if level is None:
            return {}
        lv = min(max(level, 4), 10)
        acc: Counter[str] = Counter()
        for comp in self.pool:
            for b in comp.buildup.get(lv, []):
                for u in b.units:
                    acc[u] += b.games or 0
        mx = max(acc.values(), default=0)
        return {u: g / mx for u, g in acc.items()} if mx > 0 else {}

    def c_path(self, unit_id: str) -> tuple[float, float, float]:
        """(C_path, final 기여, buildup 기여)."""
        sw = self.w.shop
        fin = bld = 0.0
        for c in self.cands:
            r = self.rel.get(c.comp_id, 0.0)
            fb = {u.id: u for u in c.comp.final_board}
            mu, kind = 0.0, None
            if unit_id in fb:
                mu, kind = (sw.mu_core if fb[unit_id].is_core else sw.mu_final), "final"
            elif c.L is not None:
                nb = board_at(c.comp, c.L + 1) if c.L < 10 else None
                cb = board_at(c.comp, c.L)
                if nb is not None and unit_id in nb.units:
                    mu, kind = sw.mu_next_buildup, "buildup"
                elif cb is not None and unit_id in cb.units:
                    mu, kind = sw.mu_cur_buildup, "buildup"
            if kind == "final":
                fin += r * mu
            elif kind == "buildup":
                bld += r * mu
        return min(1.0, fin + bld), fin, bld

    def shop_weights(self) -> tuple[float, float]:
        sw = self.w.shop
        st = sw.for_stage(self.view.stage_number)
        ws, wp = st.ws, st.wp
        if self.view.hp_bucket in ("low", "critical"):
            ws, wp = clip01(ws + sw.hp_danger_shift), clip01(wp - sw.hp_danger_shift)
        return ws, wp

    def shop_advice(self, global_level: int | None, special_lost: dict[int, bool]) -> list[ShopAdvice]:
        v = self.view
        if v.shop is None:
            return []
        sw = self.w.shop
        ws, wp = self.shop_weights()
        snow = self.s_now_table(global_level)
        rows: list[dict[str, Any]] = []
        out: dict[int, ShopAdvice] = {}
        for i, slot in enumerate(v.shop):
            if slot.kind in (ShopSlotKind.EMPTY, ShopSlotKind.UNKNOWN) or slot.id is None:
                out[i] = ShopAdvice(slot=i, kind=slot.kind, buy=False, score=0.0)
                continue
            if slot.confidence < v.min_conf:
                out[i] = ShopAdvice(slot=i, kind=slot.kind, offer_id=slot.id, buy=False, score=0.0,
                                    reason="인식 불확실")
                continue
            if slot.kind == ShopSlotKind.SPECIAL:
                lost = special_lost.get(i, False)
                js = self.jscore(f"special_value_{i}", 3, force_low=lost)
                if js is not None:
                    n, g = js
                    score = g * n + (1 - g) * sw.special_fallback_score
                else:
                    score = sw.special_fallback_score
                rec = self.stats.shop_special(slot.id) or {}
                desc = (rec.get("desc_ko") or "").strip()
                rows.append({"slot": i, "kind": slot.kind, "id": slot.id, "score": clip01(score), "tag": None,
                             "reason": (desc[:60] or self.ko(slot.id)), "cost": slot.cost})
                continue
            uid = slot.id
            sn = snow.get(uid, 0.0)
            cp, fin, bld = self.c_path(uid)
            lam_n, lam_p = sw.jev_share_now, sw.jev_share_path
            j1 = self.jscore(f"shop_now_{i}", 4)
            j2 = self.jscore(f"shop_path_{i}", 4)
            now = lam_n * j1[1] * j1[0] + (1 - lam_n * j1[1]) * sn if j1 else sn
            path = lam_p * j2[1] * j2[0] + (1 - lam_p * j2[1]) * cp if j2 else cp
            bonus = 0.0
            two = three = False
            if v.units_known:
                cost = self.stats.champion_cost(uid) or 0
                three = cost <= 2 and buy_makes_3star(uid, v.units)
                two = buy_makes_2star(uid, v.units)
                bonus = (sw.three_star_bonus if three else 0.0) + (sw.two_star_bonus if two else 0.0)
            score = clip01(ws * now + wp * path + bonus)
            fshare = fin / (fin + bld) if (fin + bld) > 0 else 0.0
            parts = {
                ReasonTag.NOW_POWER: ws * now,
                ReasonTag.FINAL_COMP: wp * path * fshare,
                ReasonTag.BUILDUP: wp * path * (1 - fshare),
                ReasonTag.TWO_STAR: bonus,
            }
            tag = max(parts, key=lambda t: (parts[t], -list(parts).index(t)))
            reason = f"지금 {now:.2f} · 경로 {path:.2f}"
            if v.units_known:
                n_own = copies_owned(uid, v.units)
                reason += f" · 확인 보유 {n_own}" if v.units_partial else f" · 보유 {n_own}"   # 부분 확인: 하한
            rows.append({"slot": i, "kind": slot.kind, "id": uid, "score": score, "tag": tag, "reason": reason,
                         "cost": slot.cost if slot.cost is not None else self.stats.champion_cost(uid),
                         "now": round(now, 4), "path": round(path, 4), "s_now": round(sn, 4), "c_path": round(cp, 4),
                         "bonus": bonus, "jev_now": j1, "jev_path": j2})
        # 구매: 임계값 이상을 점수 순으로, 골드 누적이 gold를 넘으면 False
        spent = 0
        for r in sorted(rows, key=lambda r: (-r["score"], r["slot"])):
            buy = r["score"] >= sw.buy_threshold
            if buy and v.gold is not None and r["cost"] is not None:
                if spent + r["cost"] > v.gold:
                    buy = False
                else:
                    spent += r["cost"]
            out[r["slot"]] = ShopAdvice(slot=r["slot"], kind=r["kind"], offer_id=r["id"], buy=buy,
                                        score=round(r["score"], 4), reason_tag=r["tag"], reason=r["reason"])
        self.debug["shop"] = {"ws": ws, "wp": wp, "level": global_level,
                              "rows": [{k: (v2.value if isinstance(v2, ReasonTag) else v2) for k, v2 in r.items()
                                        if k not in ("kind",)} for r in rows]}
        return [out[i] for i in sorted(out)]

    # ------------------------------------------------------------------
    # 증강 (§7)
    # ------------------------------------------------------------------
    def augment_advice(self, desc_lost: dict[int, bool]) -> AugmentAdvice | None:
        v = self.view
        if not v.augment_offer:
            return None
        aw = self.w.augment
        commit = aw.commit_for_stage(v.stage_number)
        rows = []
        for a_i, aug in enumerate(v.augment_offer):
            lost = desc_lost.get(a_i, False)
            fits: dict[str, float] = {}
            gates: list[float] = []
            for k, c in enumerate(self.cands):
                js = self.jscore(f"aug_comp_fit_{a_i}_{k}", 4, force_low=lost)
                if js is not None:
                    fits[c.comp_id] = js[0] * js[1]
                    gates.append(js[1])
                else:
                    fits[c.comp_id] = 1.0 if set(self.stats.augment_traits(aug.id)) & key_trait_ids(c.comp) else 0.5
            best_c, fit_comp = None, 0.0
            for c in self.cands:
                val = ((1 - commit) + commit * self.rel.get(c.comp_id, 0.0)) * fits[c.comp_id]
                if best_c is None or val > fit_comp:
                    best_c, fit_comp = c, val
            j2 = self.jscore(f"aug_standalone_{a_i}", 4, force_low=lost)
            a2 = j2[0] * j2[1] if j2 else 0.5
            jev_aug = aw.w_comp * fit_comp + (1 - aw.w_comp) * a2
            tier = None
            ed = None
            if best_c is not None:
                t = self.stats.augment_tier(aug.id, best_c.comp_id)
                if t is not None:
                    tier, ed = t.tier, tier_score(t.tier, self.w)
            if ed is None:
                t = self.stats.augment_tier(aug.id, None)
                if t is not None:
                    tier, ed = t.tier, tier_score(t.tier, self.w)
            if ed is None:
                ed = aw.unlisted_score
            w_jev = aw.w_jev
            all_low = bool(gates) and j2 is not None and all(g < 1 for g in gates + [j2[1]])
            if all_low:
                w_jev *= self.w.jev.low_confidence_scale
            score = clip01(w_jev * jev_aug + (1 - w_jev) * ed)
            rows.append({"i": a_i, "aug": aug, "score": score, "tier": tier, "best": best_c, "fit_comp": fit_comp,
                         "a2": a2, "ed": ed})
        order = sorted(rows, key=lambda r: (-r["score"], r["i"]))
        pick_row = order[0]
        pick_ans = self.jchoice("aug_pick")
        if pick_ans is not None and len(order) >= 2 and order[0]["score"] - order[1]["score"] < aw.tie_eps:
            p = {r["i"]: pick_ans.probabilities.get(self.aug_labels.get(r["i"], ""), 0.0) for r in order[:2]}
            if p[order[1]["i"]] > p[order[0]["i"]]:
                pick_row = order[1]
        choices = []
        for r in rows:
            reasons = []
            best = r["best"]
            if best is not None:
                hit = set(self.stats.augment_traits(r["aug"].id)) & key_trait_ids(best.comp)
                if hit:
                    reasons.append(f"특성 증강: {best.comp.name} ({self.ko(sorted(hit)[0])})")
                cf = self.counterfactual_top(r["i"], r["aug"].id, force_low=desc_lost.get(r["i"], False))
                r["cf"] = cf.comp_id if cf is not None else None
                if cf is not None:
                    reasons.append(f"선택 시 목표 덱: {cf.name}")
            if r["tier"]:
                reasons.append(f"편집자 등급 {r['tier']}")
            choices.append(AugmentChoice(augment_id=r["aug"].id, score=round(r["score"], 4),
                                         editorial_tier=r["tier"], reasons=reasons))
        self.debug["augment"] = [{"id": r["aug"].id, "score": round(r["score"], 4), "fit_comp": round(r["fit_comp"], 4),
                                  "a2": round(r["a2"], 4), "ed": r["ed"],
                                  "best": r["best"].comp_id if r["best"] else None,
                                  "counterfactual_top": r.get("cf")} for r in rows]
        return AugmentAdvice(choices=choices, pick=pick_row["aug"].id)

    def counterfactual_top(self, a_i: int, aug_id: str, force_low: bool = False):
        """증강 a를 가정한 C2' = (n·C2 + A1)/(n+1)로 덱 점수를 다시 계산한 1위 덱(CompStats)(§7)."""
        n = len(self.view.augments)
        best_comp, best = None, -1.0
        for k, c in enumerate(self.cands):
            js_a1 = self.jscore(f"aug_comp_fit_{a_i}_{k}", 4, force_low=force_low)   # 본 점수 경로와 같은 gate
            if js_a1 is not None:
                a1n, a1g = js_a1
                c2 = self.jscore(f"comp_augment_fit_{k}", 4, force_low=self.owned_aug_desc_lost) if n else None
                if c2 is not None:
                    newn = (n * c2[0] + a1n) / (n + 1)
                    g = min(c2[1], a1g)
                else:
                    newn, g = a1n, a1g
                src = "jev"
            else:
                ids = [a.id for a in self.view.augments] + [aug_id]
                newn, g, src = augment_proxy(c.comp, ids, self.stats, self.w), 1.0, "proxy"
            terms = self.comp_terms(k, c, aug_override=(newn, g, src))
            s, _, _ = self.comp_score(c, terms)
            if s > best:
                best, best_comp = s, c.comp
        return best_comp

    # ------------------------------------------------------------------
    # 아이템 (§6)
    # ------------------------------------------------------------------
    def holder_for(self, item_id: str, comp: CompStats | None = None) -> str | None:
        """보유자(기본: 1위 덱). comp를 주면 그 덱 기준 — bis를 준 덱과 보유자 덱을 맞출 때(27 W3)."""
        if not self.shown:
            return None
        comp = comp if comp is not None else self.shown[0]["cand"].comp
        fw = self.w.item_fit
        if item_fit(item_id, comp, self.stats, fw) < fw.used_by_min:
            return None
        if item_id in comp.carry_bis_items:
            return comp.carry
        on_board = {u.id for u in self.view.board} if self.view.units_known else set()
        cands = [u for u in comp.final_board if item_id in u.items]
        if not cands:
            return None
        cands.sort(key=lambda u: (0 if u.id == comp.carry else 1 if u.is_core else 2, 0 if u.id in on_board else 1, u.id))
        return cands[0].id

    def item_stat(self, item_id: str, holder: str | None) -> float:
        if holder is None or not self.shown:
            return 0.5
        comp_id = self.shown[0]["cand"].comp_id
        row = self.stats.unit_item_stat(holder, item_id, comp_id, fallback_overall=True)
        if row is None or row.place_change is None:
            return 0.5
        games = row.games
        if row.comp_id is None and games:   # 덱 한정 행 없음 → 전체 파생값, 표본 할인 후 수축(0 쪽으로 더 당김)
            games = int(games * self.w.item.overall_stat_games_factor)
            self.debug.setdefault("item_stat_overall", []).append(f"{holder}:{item_id}")
        pc = self.w.shrinkage.adjust(row.place_change, games, prior=0.0)
        return clip01(0.5 - pc / self.w.item.place_change_span)

    def bis(self, item_id: str) -> float:
        return self.bis_source(item_id)[0]

    def bis_source(self, item_id: str) -> tuple[float, Candidate | None]:
        """(bis, 그 값을 준 후보 덱). 같은 값이면 1위 덱을 먼저 고른다(보유자 표시와 덱을 맞추려고)."""
        top = self.shown[0]["cand"].comp_id if self.shown else None
        best: tuple[float, Candidate | None] = (0.0, None)
        for c in sorted(self.cands, key=lambda c: c.comp_id != top):     # 안정 정렬: 1위 덱 먼저, 나머지는 원래 순서
            v = self.rel.get(c.comp_id, 0.0) * item_fit(item_id, c.comp, self.stats, self.w.item_fit)
            if v > best[0]:
                best = (v, c)
        return best

    def item_holder(self, item_id: str) -> tuple[str | None, str | None]:
        """(표시 보유자, 덱 이름 — 1위 덱 기준이면 None). 점수(bis·st)는 바꾸지 않는다.

        bis가 1위 덱에서 왔으면 예전과 같다(1위 덱 보유자). 다른 표시 덱(오버레이의 2·3위)에서 왔으면 그 덱의 보유자를
        쓰고 덱 이름을 붙인다 — 적합도 근거와 화면의 보유자가 서로 다른 덱에서 오지 않게(27 W3).
        표시되지 않은 덱에서 왔거나 그 덱에 보유자가 없으면 예전 동작(1위 덱 보유자).
        """
        top = self.shown[0]["cand"] if self.shown else None
        _, src = self.bis_source(item_id)
        shown_ids = {r["cand"].comp_id for r in self.shown}
        if top is None or src is None or src.comp_id == top.comp_id or src.comp_id not in shown_ids:
            return self.holder_for(item_id), None
        h = self.holder_for(item_id, src.comp)
        return (h, src.comp.name) if h is not None else (self.holder_for(item_id), None)

    def code_hold(self, max_bis: float) -> bool:
        """코드 hold 규칙(§6.8): BIS 적합 낮음 + hp healthy/moderate/unknown + 이른 스테이지."""
        v, iw = self.view, self.w.item
        return (max_bis < iw.hold_bis_max and v.hp_bucket in ("healthy", "moderate", "unknown")
                and v.stage_number is not None and v.stage_number < iw.hold_until_stage)

    def item_advice(self) -> ItemAdvice | None:
        v = self.view
        if not v.items_known:
            return None
        bench_completed = v.completed + v.emblems + v.others_owned
        if not self.craftable and not bench_completed:
            return None
        iw = self.w.item
        ans = self.jchoice("item_pick")
        labels = self.item_labels
        pj: dict[str, float] = {}
        gate = 1.0
        if ans is not None:
            mx = max(ans.probabilities.values(), default=0.0)
            for lab, p in ans.probabilities.items():
                if lab in labels:
                    pj[labels[lab]] = p / mx if mx > 0 else 0.0
            gate = self.gate(ans.confidence)
        rows = []
        for x, (a, b) in self.craftable.items():
            bx = self.bis(x)
            h, h_deck = self.item_holder(x)          # 표시 보유자: bis를 준 표시 덱 기준(27 W3)
            st = self.item_stat(x, self.holder_for(x))   # 점수는 예전 그대로(1위 덱 보유자 통계)
            if ans is not None:
                score = iw.w_bis * bx + iw.w_jev * gate * pj.get(x, 0.0) + iw.w_jev * (1 - gate) * bx + iw.w_stat * st
            else:
                score = (iw.w_bis + iw.w_jev) * bx + iw.w_stat * st
            rows.append({"item": x, "components": [a, b], "bis": bx, "st": st, "holder": h, "deck": h_deck,
                         "score": clip01(score)})
        # 재료가 겹치지 않게 탐욕 선택(최대 ⌊재료수/2⌋)
        remaining = Counter(v.components)
        picked = []
        for r in sorted(rows, key=lambda r: (-r["score"], r["item"])):
            if len(picked) >= len(v.components) // 2:
                break
            need = Counter(r["components"])
            if all(remaining[k] >= n for k, n in need.items()):
                remaining.subtract(need)
                picked.append(r)
        suggestions = [
            ItemSuggestion(item_id=r["item"], components=r["components"], holder_unit_id=r["holder"],
                           score=round(r["score"], 4),
                           reason=(_holder_reason(self.ko(r["holder"]), r["deck"]) if r["holder"] else
                                   ("목표 덱 캐리용" if r["bis"] >= self.w.item_fit.used_by_min else "범용")))
            for r in picked
        ]
        for x in dict.fromkeys(bench_completed):   # 벤치 완성템 → 보유자 추천(§6-9)
            h, h_deck = self.item_holder(x)
            suggestions.append(ItemSuggestion(item_id=x, components=[], holder_unit_id=h,
                                              score=round(clip01(self.bis(x)), 4),
                                              reason=(f"{self.ko(h)}에게" + (f"({h_deck})" if h_deck else ""))
                                              if h else "목표 덱 캐리용"))
        # hold
        max_bis = max((r["bis"] for r in rows), default=0.0)
        hold = False
        if rows:
            if ans is not None and ans.choice == HOLD and ans.confidence >= self.w.jev.min_confidence:
                hold = True
            elif self.code_hold(max_bis):
                hold = True
            if v.hp_bucket == "critical":
                hold = False
        keys = ("item", "bis", "st", "holder", "deck", "score")
        self.debug["item"] = {"rows": [{k: r[k] for k in keys} for r in rows],
                              "max_bis": max_bis, "hold": hold, "jev_choice": ans.choice if ans else None}
        return ItemAdvice(suggestions=suggestions, hold=hold)

    # ------------------------------------------------------------------
    # 재료 우선순위 (§10-2)
    # ------------------------------------------------------------------
    def component_priority(self, targets: list[TargetComp]) -> list[str]:
        Q = Counter(self.view.components) if self.view.items_known else Counter()
        weight: dict[str, float] = {}
        for t in targets:
            r = self.rel.get(t.comp_id, 0.0)
            for ir in t.items_ready:
                if ir.status != "missing":
                    continue
                rec = self.stats.recipe(ir.item_id)
                if rec is None:   # 제작 불가 BIS는 부족 재료를 정의할 수 없다 → 건너뜀
                    continue
                need = Counter(rec)
                for comp_id, n in need.items():
                    short = max(0, n - Q[comp_id])
                    if short:
                        weight[comp_id] = weight.get(comp_id, 0.0) + r * short
        return [c for c, _ in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))][:10]


def fallback_stat_only(c: Candidate, w: Weights) -> float:
    """디버그용: 자원 신호 없이 통계만 쓴 점수."""
    return stat_norm(c.adj, w)


__all__ = ["Scorer", "Term", "augment_comp_fit", "fallback_stat_only"]
