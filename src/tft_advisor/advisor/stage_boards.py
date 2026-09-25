"""스테이지별 실제 보드 통계(MetaTFT Early Comps) → 보드 배치 신호(21 §11). 코드 전용, Jev 없음.

원천: `stats.stage_stats.StageStats`(stats-researcher 소유, `_workspace/28_stage_boards_sources.md` §4). advisor는 그 모듈을
import하지 않고 덕 타이핑으로 쓴다(`stats.stage_stats` 속성이 없거나 비었으면 신호 없음 → 예전 동작).

세 가지를 준다.
1. `unit_signal`: 이 스테이지에 이 유닛(성급별)을 보드에 올린 판의 성적. 같은 스테이지 기준선 대비 delta(음수가 좋다)를
   표본 수로 수축(delta x g/(g+k))해 −1~1 신호로 바꾼다. 근거 문구 "통계: 2스테이지 1성 · 평균보다 0.10등 높음(1,234판)"
   (delta는 절대 등수가 아니라 기준선 대비 차이다. 등수는 낮을수록 좋으므로 delta 음수 = "높음").
2. `best_board`: 지금 스테이지·레벨의 실제 보드(정확한 variation 우선, 없으면 cluster) 중 보유 유닛으로 (거의) 만들 수 있는 것.
   고르기 점수 = 품질(수축 delta) + 보유 비율 + 목표 덱 연결(comp_links, 초반엔 작게).
3. `next_hint`: 지금 라인업과 닮은 클러스터에서 다음 스테이지로 가장 많이 간 경로(목표 덱으로 이어지는 경로 우선).
   닮은 정도(Jaccard)가 `trans_match_min` 이상이면 "이 보드는", `trans_similar_min` 이상이면 "비슷한 보드는",
   그보다 낮으면 힌트를 내지 않는다(유닛 1기만 겹친 클러스터로 "이 보드는 보통…"이라 말하지 않게, 27 W4).

주의(28 §0·§4): top4가 없다(avg_place·win_rate·round_win_rate). 연승 보드일수록 좋아 보이는 상관 관계라 인과 효과가 아니다
→ 가중을 작게(`[board_plan] stage_*`/`board_*`), 표본으로 수축한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import BoardPlanWeights


@dataclass(frozen=True)
class UnitSignal:
    value: float            # −1~1. 양수 = 이 스테이지에 이 유닛(성급)을 올린 판이 기준선보다 좋았다(수축 후)
    delta: float            # 원 delta(avg_place − 스테이지 기준선, 음수가 좋다)
    games: int
    stage: int
    star: int | None        # 쓴 행의 성급(None = 전 성급 합)

    def reason(self) -> str:
        star = f" {self.star}성" if self.star else ""
        return f"통계: {self.stage}스테이지{star} · {vs_baseline(self.delta)}({self.games:,}판)"


@dataclass(frozen=True)
class BoardPick:
    stage: int
    kind: str                       # "variation" | "cluster"
    cluster: str
    units: tuple[str, ...]
    owned: tuple[str, ...]          # 그중 보유 유닛
    games: int
    avg_place: float | None
    delta: float | None
    link: float                     # 목표 덱 연결 확률(목표 덱 없으면 0)
    score: float


@dataclass(frozen=True)
class NextHint:
    stage: int                      # 다음 스테이지
    units: tuple[str, ...]          # 다음 스테이지 대표 보드
    share: float                    # 이 클러스터에서 그 경로로 간 비율
    avg_place: float | None
    games: float
    link: float                     # 그 보드의 목표 덱 연결 확률
    match: float = 1.0              # 지금 라인업 ↔ 출발 클러스터 Jaccard
    close: bool = True              # match >= trans_match_min → "이 보드는", 아니면 "비슷한 보드는"


class StageBoardSource(Protocol):
    """board_plan이 쓰는 스테이지 보드 신호(21 §10.4 훅의 구현 계약). 테스트는 가짜 구현을 넣을 수 있다."""

    def unit_signal(self, unit_id: str, star: int | None, stage: str | None, level: int | None) -> UnitSignal | None: ...
    def best_board(self, owned: Mapping[str, int], stage: str | None, level: int | None, comp_id: str | None,
                   comp_scale: float) -> BoardPick | None: ...
    def next_hint(self, lineup: list[str], stage: str | None, level: int | None,
                  comp_id: str | None) -> NextHint | None: ...


def vs_baseline(delta: float) -> str:
    """기준선 대비 delta(음수가 좋다) → "평균보다 0.11등 높음"/"평균보다 0.11등 낮음"/"평균과 비슷함".

    절대 평균 등수("평균 4.40등")와 헷갈리지 않게 '평균보다'를 붙이고, 등수는 낮을수록 좋으므로 delta 음수를 '높음'이라 쓴다.
    """
    if abs(delta) < 0.005:
        return "평균과 비슷함"
    return f"평균보다 {abs(delta):.2f}등 {'높음' if delta < 0 else '낮음'}"


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


class MetaTftStageBoards:
    """`StageStats`(덕 타이핑) → StageBoardSource. 조회 실패는 None으로 흘린다(부가 신호가 추천을 막지 않게)."""

    def __init__(self, stage_stats: Any, w: BoardPlanWeights) -> None:
        self.st = stage_stats
        self.w = w

    @classmethod
    def from_stats(cls, stats: object, w: BoardPlanWeights) -> MetaTftStageBoards | None:
        st = getattr(stats, "stage_stats", None)
        try:
            return cls(st, w) if st is not None and bool(st) else None
        except Exception:   # noqa: BLE001
            return None

    # --- 공통 ---
    def _stage(self, stage: str | None, level: int | None) -> int | None:
        try:
            return self.st.stage_of(stage, level=level) if stage is not None else self.st.stage_of(level=level)
        except Exception:   # noqa: BLE001
            return None

    def _shrunk(self, delta: float | None, games: int | float) -> float | None:
        if delta is None:
            return None
        g = float(games or 0)
        k = self.w.stage_shrink_k
        return delta * g / (g + k) if g + k > 0 else delta

    def _quality(self, delta: float | None, games: int | float) -> float:
        d = self._shrunk(delta, games)
        return 0.0 if d is None else max(-1.0, min(1.0, -d / self.w.stage_delta_span))

    # --- 1. 유닛 ---
    def unit_signal(self, unit_id: str, star: int | None, stage: str | None, level: int | None) -> UnitSignal | None:
        s = self._stage(stage, level)
        if s is None:
            return None
        row = None
        if star:
            row = self.st.unit_stage_stat(unit_id, s, star=star)
            if row is not None and (row.games < self.w.stage_unit_min_games or row.delta is None):
                row = None
        if row is None:
            row = self.st.unit_stage_stat(unit_id, s)
            if row is None or row.games < self.w.stage_unit_min_games or row.delta is None:
                return None
        return UnitSignal(value=self._quality(row.delta, row.games), delta=row.delta, games=row.games, stage=s,
                          star=row.star)

    # --- 2. 추천 스테이지 보드 ---
    def best_board(self, owned: Mapping[str, int], stage: str | None, level: int | None, comp_id: str | None,
                   comp_scale: float) -> BoardPick | None:
        s = self._stage(stage, level)
        if s is None or level is None or not owned:
            return None
        w = self.w
        have = set(owned)
        best: BoardPick | None = None
        for kind in ("variation", "cluster"):
            for b in self.st.boards_for(s, level=level, kind=kind, min_games=w.board_min_games):
                mine = tuple(u for u in b.units if u in have)
                if len(b.units) - len(mine) > w.board_max_missing or not mine:
                    continue
                link = b.link(comp_id) if comp_id else 0.0
                score = (self._quality(b.delta, b.games) + w.board_cover * len(mine) / len(b.units)
                         + w.board_link * comp_scale * link)
                cand = BoardPick(stage=s, kind=kind, cluster=str(b.cluster), units=tuple(b.units), owned=mine,
                                 games=int(b.games), avg_place=b.avg_place, delta=b.delta, link=link, score=score)
                if best is None or (cand.score, cand.games) > (best.score, best.games):
                    best = cand
            if best is not None:        # 정확한 보드(variation)가 있으면 대표 보드(cluster)로 넓히지 않는다
                break
        return best

    # --- 3. 다음 스테이지 ---
    def next_hint(self, lineup: list[str], stage: str | None, level: int | None,
                  comp_id: str | None) -> NextHint | None:
        s = self._stage(stage, level)
        if s is None or not lineup or s >= 5:
            return None
        found = self.st.cluster_for(lineup, s)
        if not found:
            return None
        match = float(found[1])
        if match < self.w.trans_similar_min:
            return None
        cluster = found[0].cluster
        opts: list[tuple[Any, Any, float]] = []
        for t in self.st.transitions(s, cluster, min_games=self.w.trans_min_games):
            nb = self.st.cluster_board(s + 1, t.next_cluster)
            if nb is not None:
                opts.append((t, nb, nb.link(comp_id) if comp_id else 0.0))
        if not opts:
            return None
        linked = [o for o in opts if o[2] >= self.w.trans_link_min]
        t, nb, link = max(linked or opts, key=lambda o: (o[0].share, o[0].games))
        return NextHint(stage=s + 1, units=tuple(nb.units), share=t.share, avg_place=t.avg_place, games=t.games,
                        link=link, match=match, close=match >= self.w.trans_match_min)


__all__ = ["BoardPick", "MetaTftStageBoards", "NextHint", "StageBoardSource", "UnitSignal", "vs_baseline"]
