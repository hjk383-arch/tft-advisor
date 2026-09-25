"""스테이지별 보드·유닛 통계 조회 API (MetaTFT Early Comps). 소유: stats-researcher.

advisor는 `open_repository()`가 붙여 주는 `repo.stage_stats`(이 모듈의 `StageStats`)를 쓴다. 스냅샷이 없으면 빈
`StageStats`가 붙고 모든 조회가 None / 빈 목록을 돌려준다(예외 없음).

    st = repo.stage_stats
    st.stage_of("3-2")                       # 3   (스테이지 문자열/정수/레벨 → 데이터 스테이지 2~5)
    st.boards_for(3)                         # 스테이지 3 보드(클러스터+정확한 보드) avg_place 오름차순
    st.boards_for(level=6, comp_id="juggernaut-zyra-amumu")   # 6인 보드 중 그 최종 덱으로 이어지는 것
    st.unit_stage_stat("DA_18_Veigar", 3)    # 스테이지 3에 베이가를 올린 판의 성적(전 성급)
    st.unit_stage_stat("DA_18_Veigar", 3, star=2)
    st.rank_units(["DA_18_Veigar", "DA_18_Ornn"], 3)   # 보유 유닛을 스테이지 성적 순으로(배치 추천용)
    st.cluster_for(board_units, 3)           # 지금 보드와 가장 닮은 스테이지 클러스터(Jaccard)
    st.transitions(3, cluster)               # 그 클러스터 → 다음 스테이지 클러스터(비율·최종 성적)

지표: avg_place = 최종 등수 평균, win_rate = 1등 비율, round_win_rate = 그 스테이지 전투 승률, games = 참가자-게임
수(final_place_count), rounds = 전투 수. **top4는 소스에 없다.** `delta` = avg_place − 같은 스테이지 기준선(음수 =
평균보다 좋음). 표본이 MetaTFT 앱 사용자라 기준선이 4.5가 아니므로 delta로 비교할 것.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tft_advisor.contracts import CompStats

STAGES = (2, 3, 4, 5)
DEFAULT_LEVEL_STAGE: dict[int, int] = {1: 2, 2: 2, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4, 9: 5, 10: 5}
"""레벨 → 데이터 스테이지 기본값(stage_round_sizes가 있으면 인원수별 최빈 스테이지로 덮어쓴다)."""
LINK_MIN_JACCARD = 0.4        # 스테이지 5 클러스터 ↔ 최종 덱 보드 매칭 최소 Jaccard
LINK_MIN_CONTAIN = 0.6        # 전이 정보가 없을 때: 클러스터 유닛 중 최종 보드에 든 비율
LINK_MIN_P = 0.05             # 저장할 최소 연결 확률
_STAGE_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


@dataclass(frozen=True)
class StageBaseline:
    stage: int
    avg_place: float | None
    win_rate: float | None
    round_win_rate: float | None
    games: int
    rounds: int


@dataclass(frozen=True)
class StageBoard:
    stage: int
    kind: str                      # "cluster"(대표 보드, 현재 패치) | "variation"(정확한 보드, 소스 기간 전체)
    cluster: str
    units: tuple[str, ...]
    avg_place: float | None
    win_rate: float | None
    round_win_rate: float | None
    avg_hp_delta: float | None
    avg_hp: float | None
    games: int
    rounds: int
    share: float | None            # cluster만: 스테이지 전투 중 이 클러스터 비율
    delta: float | None            # avg_place − 스테이지 기준선
    comp_links: tuple[tuple[str, float], ...] = ()   # (comp_id, 확률) 내림차순 — 이 보드가 이어지는 최종 덱

    @property
    def size(self) -> int:
        return len(self.units)

    def link(self, comp_id: str) -> float:
        return next((p for c, p in self.comp_links if c == comp_id), 0.0)


@dataclass(frozen=True)
class UnitStageStat:
    unit_id: str
    stage: int
    star: int | None               # None = 전 성급 합
    avg_place: float | None
    win_rate: float | None
    round_win_rate: float | None
    avg_hp_delta: float | None
    games: int
    rounds: int
    pick_rate: float | None        # 그 스테이지 전투 중 이 유닛이 보드에 있던 비율
    delta: float | None


@dataclass(frozen=True)
class StageTransition:
    stage: int
    cluster: str
    next_cluster: str
    share: float                   # 이 클러스터에서 다음 스테이지로 간 경로 중 next_cluster 비율
    avg_place: float | None
    win_rate: float | None
    round_win_rate: float | None
    games: float


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa or sb else 0.0


@dataclass
class StageStats:
    """스테이지 보드 스냅샷 1개(메모리). `from_doc`으로 만든다. 모든 조회는 dict/리스트 조회(I/O 없음)."""

    report: dict[str, Any] = field(default_factory=dict)
    baseline: dict[int, StageBaseline] = field(default_factory=dict)
    boards: dict[int, list[StageBoard]] = field(default_factory=dict)
    units: dict[tuple[str, int, int | None], UnitStageStat] = field(default_factory=dict)
    trans: dict[tuple[int, str], list[StageTransition]] = field(default_factory=dict)
    level_stage: dict[int, int] = field(default_factory=lambda: dict(DEFAULT_LEVEL_STAGE))
    round_sizes: list[dict[str, Any]] = field(default_factory=list)

    # ---- 생성 -------------------------------------------------------------------------------------
    @classmethod
    def empty(cls) -> StageStats:
        return cls()

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any] | None, comps: Iterable[CompStats] = ()) -> StageStats:
        """`early_convert.build_early` 산출물(또는 DB read_snapshot) → 조회 객체. comps로 최종 덱 연결을 계산한다."""
        if not doc:
            return cls.empty()
        base = {int(r["stage"]): StageBaseline(int(r["stage"]), _f(r.get("avg_place")), _f(r.get("win_rate")),
                                               _f(r.get("round_win_rate")), int(r.get("games") or 0),
                                               int(r.get("rounds") or 0))
                for r in doc.get("stage_baseline", [])}

        def delta(stage: int, ap: Any) -> float | None:
            b = base.get(stage)
            return round(float(ap) - b.avg_place, 4) if ap is not None and b and b.avg_place is not None else None

        trans: dict[tuple[int, str], list[StageTransition]] = defaultdict(list)
        for r in doc.get("stage_transitions", []):
            t = StageTransition(int(r["stage"]), str(r["cluster"]), str(r["next_cluster"]), float(r["share"]),
                                _f(r.get("avg_place")), _f(r.get("win_rate")), _f(r.get("round_win_rate")),
                                float(r.get("games") or 0))
            trans[(t.stage, t.cluster)].append(t)
        for v in trans.values():
            v.sort(key=lambda t: -t.share)

        raw_boards = doc.get("stage_boards", [])
        cluster_units = {(int(r["stage"]), str(r["cluster"])): tuple(r["units"])
                         for r in raw_boards if r.get("kind") == "cluster"}
        links = _comp_links(cluster_units, trans, list(comps))
        boards: dict[int, list[StageBoard]] = defaultdict(list)
        for r in raw_boards:
            st, cl = int(r["stage"]), str(r["cluster"])
            boards[st].append(StageBoard(
                stage=st, kind=r["kind"], cluster=cl, units=tuple(r["units"]), avg_place=_f(r.get("avg_place")),
                win_rate=_f(r.get("win_rate")), round_win_rate=_f(r.get("round_win_rate")),
                avg_hp_delta=_f(r.get("avg_hp_delta")), avg_hp=_f(r.get("avg_hp")), games=int(r.get("games") or 0),
                rounds=int(r.get("rounds") or 0), share=_f(r.get("share")), delta=delta(st, r.get("avg_place")),
                comp_links=links.get((st, cl), ())))
        for v in boards.values():
            v.sort(key=_board_key)

        units: dict[tuple[str, int, int | None], UnitStageStat] = {}
        for r in doc.get("unit_stage_stats", []):
            u = UnitStageStat(r["unit_id"], int(r["stage"]), r.get("star"), _f(r.get("avg_place")),
                              _f(r.get("win_rate")), _f(r.get("round_win_rate")), _f(r.get("avg_hp_delta")),
                              int(r.get("games") or 0), int(r.get("rounds") or 0), _f(r.get("pick_rate")),
                              delta(int(r["stage"]), r.get("avg_place")))
            units[(u.unit_id, u.stage, u.star)] = u

        sizes = list(doc.get("stage_round_sizes", []))
        level_stage = dict(DEFAULT_LEVEL_STAGE)
        by_n: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for r in sizes:
            by_n[int(r["num_units"])][int(r["stage"])] += int(r.get("rounds") or 0)
        for n, per_stage in by_n.items():
            if n in level_stage and sum(per_stage.values()) >= 1000:
                level_stage[n] = max(per_stage, key=lambda s: per_stage[s])
        return cls(report=dict(doc.get("report") or {}), baseline=base, boards=dict(boards), units=units,
                   trans=dict(trans), level_stage=level_stage, round_sizes=sizes)

    # ---- 기본 -------------------------------------------------------------------------------------
    def __bool__(self) -> bool:
        return bool(self.boards or self.units)

    @property
    def patch(self) -> str | None:
        return self.report.get("patch")

    def stage_of(self, stage: int | str | None = None, *, level: int | None = None) -> int | None:
        """"3-2" / 3 / level 6 → 데이터 스테이지(2~5). 스테이지 1은 2로, 6 이상은 5로 올린다/내린다."""
        if stage is not None:
            m = _STAGE_RE.match(str(stage))
            if not m:
                return None
            return min(max(int(m.group(1)), STAGES[0]), STAGES[-1])
        if level is not None:
            return self.level_stage.get(int(level), STAGES[-1] if level > 10 else STAGES[0])
        return None

    def stage_baseline(self, stage: int | str | None = None, *, level: int | None = None) -> StageBaseline | None:
        s = self.stage_of(stage, level=level)
        return self.baseline.get(s) if s is not None else None

    # ---- 보드 -------------------------------------------------------------------------------------
    def boards_for(self, stage: int | str | None = None, *, level: int | None = None, comp_id: str | None = None,
                   kind: str | None = None, min_games: int = 100, min_link: float = 0.15,
                   limit: int | None = None) -> list[StageBoard]:
        """스테이지(또는 레벨)의 보드. avg_place 오름차순(같으면 games 내림차순).

        level을 주면 **유닛 수 == level**인 보드만(스테이지를 안 주면 레벨의 대표 스테이지). comp_id를 주면 그
        최종 덱으로 이어질 확률(comp_links) >= min_link인 보드만. kind: "cluster" | "variation" | None(둘 다).
        """
        s = self.stage_of(stage, level=level)
        if s is None:
            return []
        out = [b for b in self.boards.get(s, [])
               if b.games >= min_games and (kind is None or b.kind == kind)
               and (level is None or b.size == level)
               and (comp_id is None or b.link(comp_id) >= min_link)]
        return out[:limit] if limit else out

    def cluster_for(self, units: Iterable[str], stage: int | str | None = None, *,
                    level: int | None = None) -> tuple[StageBoard, float] | None:
        """지금 보드와 Jaccard가 가장 큰 스테이지 클러스터와 그 값. 보드가 비었거나 데이터가 없으면 None."""
        s = self.stage_of(stage, level=level)
        u = set(units)
        if s is None or not u:
            return None
        best = max(((b, jaccard(u, b.units)) for b in self.boards.get(s, []) if b.kind == "cluster"),
                   key=lambda x: (x[1], x[0].games), default=None)
        return best if best and best[1] > 0 else None

    def transitions(self, stage: int | str, cluster: str, *, min_games: float = 20) -> list[StageTransition]:
        """클러스터 → 다음 스테이지 클러스터(비율 내림차순). next 보드는 `cluster_board(stage+1, next_cluster)`."""
        s = self.stage_of(stage)
        return [t for t in self.trans.get((s, str(cluster)), []) if t.games >= min_games] if s else []

    def cluster_board(self, stage: int | str, cluster: str) -> StageBoard | None:
        s = self.stage_of(stage)
        return next((b for b in self.boards.get(s, []) if b.kind == "cluster" and b.cluster == str(cluster)), None) \
            if s else None

    # ---- 유닛 -------------------------------------------------------------------------------------
    def unit_stage_stat(self, unit_id: str, stage: int | str | None = None, *, level: int | None = None,
                        star: int | None = None) -> UnitStageStat | None:
        """그 스테이지에 unit을 보드에 올린 판의 성적. star=None이면 전 성급 합."""
        s = self.stage_of(stage, level=level)
        return self.units.get((unit_id, s, star)) if s is not None else None

    def unit_stage_stats(self, stage: int | str | None = None, *, level: int | None = None,
                         min_games: int = 100) -> list[UnitStageStat]:
        """스테이지의 유닛별(전 성급) 성적, delta 오름차순."""
        s = self.stage_of(stage, level=level)
        rows = [u for (uid, st, star), u in self.units.items() if st == s and star is None and u.games >= min_games]
        return sorted(rows, key=lambda u: (u.delta is None, u.delta or 0.0, -u.games))

    def rank_units(self, unit_ids: Iterable[str], stage: int | str | None = None, *, level: int | None = None,
                   stars: Mapping[str, int] | None = None, min_games: int = 50) -> list[tuple[str, UnitStageStat | None]]:
        """보유 유닛을 스테이지 성적(delta) 순으로. stars를 주면 그 성급 행을 먼저 쓰고 표본이 모자라면 전 성급 행.
        통계가 없는(또는 표본 < min_games) 유닛은 None과 함께 뒤에 둔다. 중복 ID는 한 번만."""
        seen: list[str] = []
        for u in unit_ids:
            if u not in seen:
                seen.append(u)
        out: list[tuple[str, UnitStageStat | None]] = []
        for u in seen:
            row = None
            if stars and stars.get(u):
                row = self.unit_stage_stat(u, stage, level=level, star=stars[u])
                if row is not None and row.games < min_games:
                    row = None
            if row is None:
                row = self.unit_stage_stat(u, stage, level=level)
                if row is not None and row.games < min_games:
                    row = None
            out.append((u, row))
        return sorted(out, key=lambda x: (x[1] is None or x[1].delta is None,
                                          (x[1].delta if x[1] and x[1].delta is not None else 0.0), x[0]))


def _board_key(b: StageBoard) -> tuple:
    return (b.avg_place is None, b.avg_place or 0.0, -b.games, b.kind, b.units)


def _comp_links(cluster_units: Mapping[tuple[int, str], tuple[str, ...]],
                trans: Mapping[tuple[int, str], list[StageTransition]],
                comps: list[CompStats]) -> dict[tuple[int, str], tuple[tuple[str, float], ...]]:
    """스테이지 클러스터 → 최종 덱(comp_id) 확률.

    스테이지 5: 최종 보드와 Jaccard 최대(>= LINK_MIN_JACCARD, 동률은 균등 분배).
    스테이지 2~4: 다음 스테이지로의 전이 비율 × 다음 클러스터의 연결 확률(재귀). 전이가 없으면 포함률
    |클러스터 ∩ 최종 보드| / |클러스터| >= LINK_MIN_CONTAIN인 덱(최대값, 동률 균등).
    """
    finals = [(c.comp_id, {u.id for u in c.final_board}) for c in comps if c.final_board]
    if not finals:
        return {}
    memo: dict[tuple[int, str], dict[str, float]] = {}

    def by_overlap(units: tuple[str, ...], score, thr: float) -> dict[str, float]:
        if not units:
            return {}
        scored = [(cid, score(set(units), fb)) for cid, fb in finals]
        best = max(v for _, v in scored)
        if best < thr:
            return {}
        top = [cid for cid, v in scored if v >= best - 1e-9]
        return {cid: 1.0 / len(top) for cid in top}

    def contain(a: set[str], b: set[str]) -> float:
        return len(a & b) / len(a) if a else 0.0

    def links(key: tuple[int, str]) -> dict[str, float]:
        if key in memo:
            return memo[key]
        memo[key] = {}
        st, cl = key
        units = cluster_units.get(key, ())
        if st >= STAGES[-1]:
            out = by_overlap(units, jaccard, LINK_MIN_JACCARD)
        else:
            acc: dict[str, float] = defaultdict(float)
            for t in trans.get(key, []):
                for cid, p in links((st + 1, t.next_cluster)).items():
                    acc[cid] += t.share * p
            out = dict(acc) if acc else by_overlap(units, contain, LINK_MIN_CONTAIN)
        memo[key] = out
        return out

    result = {}
    for key in cluster_units:
        got = links(key)
        tot = sum(got.values())
        # 전이 비율 합으로 정규화하지 않는다: 연결 안 된 경로(다른 덱으로 가지 않음) 몫은 확률에서 빠진 채로 둔다.
        items = sorted(((c, round(p, 4)) for c, p in got.items() if p >= LINK_MIN_P), key=lambda x: (-x[1], x[0]))
        if items and tot > 0:
            result[key] = tuple(items[:5])
    return result
