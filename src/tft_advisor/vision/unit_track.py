"""판 안에서 유닛 정체 이어 가기 — 구매 칸 · 옮기기 · 판매 · 합성(35 보고).

## 왜
라이브(live4 2-5): 특성 풀이로 보드 집합은 맞혔지만(알리스타·오른·렉사이·피들스틱) 칸 이름은 피들스틱 하나, 벤치 5칸은 전부
"이름 미상"이었다. 라이브러리는 작고(승인 11/74) 뒷받침 없는 이름은 엄격하며(맵 규칙), 장부 이름은 유일할 때만 칸에 붙는다.
그런데 **이번 판 안에서는** 정체를 한 번 알면 잃을 이유가 없다: 산 유닛은 정해진 칸에 떨어지고, 옮기면 같은 판·같은 맵·같은
조명의 같은 모델이다.

## 규칙(화면 픽셀 + 상점 칸 변화 + app 장부 이벤트만)
1. **구매 → 가장 왼쪽 빈 벤치 칸**(TFT 규칙). 구매 = 상점 칸이 챔피언 → 빈 칸(이 프레임에 상점을 읽었을 때) 또는 app 장부의
   `buy` 이벤트(`note_purchase`, 같은 챔피언 두 출처 보고는 창 안에서 한 건). 새 벤치 칸이 **직전 프레임의 가장 왼쪽 빈 칸**에
   생겼고, 창(`BUY_WINDOW_S`) 안의 구매 수와 그런 새 칸 수가 같으면 시간 순 구매 ↔ 왼쪽부터 칸으로 짝짓는다(`source="purchase"`).
   새 칸 없이 기존 칸의 성급이 오르면 = 합성 구매 → 그 칸의 정체가 없으면 그 챔피언(있으면 같아야 한다).
2. **옮기기**: 이번 프레임에 사라진 칸(+ 잠시 전에 사라진 칸 `LIMBO_S`)과 새로 생긴 칸을 모델 닮음으로 짝짓는다.
   - 하나 사라지고 하나 생김(끌어 옮기기 한 번): 닮음 >= `SINGLE_MIN`이고 성급이 맞으면.
   - 여럿: 닮음 >= `MATCH_MIN`이고 행·열 모두 2위보다 `MATCH_MARGIN` 이상 높을 때만. 애매하면 정체를 **버린다**(추측 금지).
   - 같은 칸에 그대로 있는 유닛끼리 자리를 바꾼 경우(벤치 칸 위로 끌어 놓으면 두 유닛이 바뀐다): 엇갈린 닮음 합이 곧은 합보다
     `SWAP_MARGIN` 이상 크면 바꾼다. 자기 칸 닮음이 `KEEP_MIN`보다 낮은데 설명이 안 되면 정체를 버린다.
   실측(옛 판 준비 캡처 4장, 확인 라벨): 같은 칸 같은 유닛 중앙값 0.74 · 옮긴 같은 챔피언 5% 0.43 · 다른 챔피언 95% 0.40, 최대 0.59.
3. **판매·합성**: 사라진 칸이 `LIMBO_S` 안에 다시 나타나지 않으면 정체를 지운다(판매 · 합성으로 사라진 사본). 장부 판매 이벤트는
   그 챔피언의 대기 정체를 바로 지운다.
4. **vision 이름과 합치기**: 칸에 확실한 vision 이름(특성 구속·같은 모델·뒷받침된 사진 비교, 신뢰도 >= `VISION_ADOPT`)이 있으면
   정체로 받는다. 정체와 확실한 vision 이름이 **다르면 둘 다 버린다**. 뒷받침 없는 추정 이름은 정체를 이기지 못한다.
5. **성급**: 배지를 읽으면 배지. 못 읽으면 같은 칸에 그대로 있는(닮음 >= `STAR_KEEP_MIN`) 정체의 성급, 아니면 None(모름 — 1성으로 두지 않는다).
6. 체력바가 사라진 프레임(`BoardRead.bench_held`)·준비 단계가 아닌 화면에서는 정체를 **갱신하지 않고** 칸 열쇠로 이름만 붙인다
   (`bench_memory`가 "그대로"라고 한 칸만).

출력: 이름 없는 칸에 `unit_id`, `name_source` = `purchase`(산 칸에 그대로) | `tracked`(옮긴 뒤·vision에서 받은 정체), `unit_conf`.
보드 `unplaced`(특성 집합 중 칸을 모르는 챔피언)는 이름이 붙은 만큼 줄인다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from .units import similarity

log = logging.getLogger(__name__)

BUY_WINDOW_S = 3.0
LIMBO_S = 6.0
SINGLE_MIN = 0.35
MATCH_MIN = 0.50
MATCH_MARGIN = 0.12
KEEP_MIN = 0.55
"""같은 칸에 그대로 선 유닛의 최소 닮음. 이보다 낮으면 다른 유닛으로 본다(정체를 버린다). 전투 중(보드를 읽지 않는 동안) 팔고
같은 칸에 다른 유닛을 사면 다음 준비 프레임에서 같은 칸 = 다른 유닛이다 — 같은 프레임의 다른 유닛끼리 닮음이 0.56까지
나왔다(live4 오른/알리스타). 연속 프레임의 같은 유닛은 대개 0.9 이상(표본을 매 프레임 새로 한다)."""
SWAP_MARGIN = 0.30
SWAP_MIN = 0.45
KEEP_MIN_AFTER_GAP = 0.65
"""관측 공백(전투·공동 선택, `BUY_WINDOW_S`보다 긴) 뒤 첫 프레임의 같은 칸 유지 최소 닮음(QA 36 W1: 다른 챔피언 최대 0.59)."""
PAIR_DT = 1.0
"""구매 ↔ 새 칸 짝: 구매가 감지된 프레임과 새 칸이 처음 보인 프레임의 시각 차가 이 안(같은 프레임 또는 바로 다음 프레임)이어야 한다
(QA 36 F1b: 전투 초반 구매가 떨어진 칸이 끝 무렵 합성 구매 이름을 받았다). app은 장부 이벤트에 프레임 캡처 시각을 넘긴다."""
STAR_KEEP_MIN = 0.55
DESC_REFRESH_MIN = 0.60       # 같은 칸 닮음이 이 이상이면 표본을 지금 그림으로 바꾼다(자세 변화 따라가기)
VISION_ADOPT = 0.75
PURCHASE_CONF = 0.9
TRACKED_CONF = 0.85
STRONG_SOURCES = ("forced", "traits", "duplicate")
BENCH = 9

Key = tuple


@dataclass
class Track:
    unit_id: str | None
    star: int | None = None
    desc: np.ndarray | None = None
    source: str = "none"
    conf: float = 0.0
    moved: bool = False


@dataclass
class _Buy:
    at: float
    champion: str
    sources: set[str]
    used: bool = False


@dataclass
class _Appear:
    key: Key
    at: float
    rank: int | None
    """직전 프레임 빈 벤치 칸들(왼쪽부터) 중 이 칸의 순번. 0 = 가장 왼쪽 빈 칸. 직전 벤치를 모르면 None."""
    group: int = 0
    """같은 프레임에 함께 생긴 새 칸 수(연달아 산 경우 0..group-1 순번이어야 한다)."""


@dataclass
class UnitTracker:
    """`Recognizer`가 하나 들고 보드 판독마다 `update()`를 부른다. 새 판이면 `reset()`(app 루프)."""

    slots: dict[Key, Track] = field(default_factory=dict)
    limbo: list[tuple[float, Track]] = field(default_factory=list)
    buys: list[_Buy] = field(default_factory=list)
    appeared: list[_Appear] = field(default_factory=list)
    star_ups: list[tuple[float, Key]] = field(default_factory=list)
    _last_shop: tuple | None = None
    _bench_occ: set[int] | None = None
    _arena: str | None = None
    _last_active: float | None = None
    """마지막으로 갱신한(준비 단계) 프레임 시각. 공백이 길면 그 뒤 첫 프레임에서는 구매 이름을 붙이지 않는다."""
    dropped: int = 0
    """애매해서 버린 정체 수(진단용)."""

    def reset(self) -> None:
        self.slots.clear()
        self.limbo.clear()
        self.buys.clear()
        self.appeared.clear()
        self.star_ups.clear()
        self._last_shop = self._bench_occ = self._arena = self._last_active = None

    # ------------------------------------------------------------------ 이벤트
    def note_purchase(self, champion_id: str, at: float | None = None, source: str = "ledger") -> None:
        if not champion_id:
            return
        if at is None:
            import time

            at = time.time()
        twins = [b for b in self.buys if b.champion == champion_id and source not in b.sources
                 and abs(b.at - at) <= BUY_WINDOW_S]
        if twins:
            min(twins, key=lambda b: abs(b.at - at)).sources.add(source)
            return
        self.buys.append(_Buy(at, champion_id, {source}))
        self._pair_buys(at)

    def note_sale(self, champion_id: str) -> None:
        """장부 판매: 그 챔피언의 대기(사라진) 정체를 지운다."""
        self.limbo = [(t, tr) for t, tr in self.limbo if tr.unit_id != champion_id]

    # ------------------------------------------------------------------ 한 프레임
    def update(self, read: Any, board_desc: list, bench_desc: list, now: float, *,
               shop: tuple | None = None, active: bool = True, arena: str | None = None) -> Any:
        """판독 → 정체를 붙인 판독. `board_desc`/`bench_desc`는 `read.board`/`read.bench`와 같은 순서의 모델 기술자
        (체력바가 사라진 프레임이면 비어 있어도 된다). `active=False`(준비 단계가 아님)면 갱신하지 않고 이름만 붙인다."""
        try:
            if getattr(read, "bench_held", False) or not active:
                return self._label_only(read, board_desc, bench_desc)
            if arena is not None:
                from .units import same_arena

                if self._arena is not None and not same_arena(arena, self._arena):
                    log.info("맵이 바뀌었습니다(%s → %s) — 유닛 정체를 비웁니다(새 판)", self._arena, arena)
                    self.reset()
                self._arena = arena
            return self._update(read, board_desc, bench_desc, now, shop)
        except Exception:                               # 보조 기능 — 판독을 막지 않는다
            log.exception("유닛 정체 추적 실패")
            return read

    def _label_only(self, read: Any, board_desc: list = (), bench_desc: list = ()) -> Any:
        """갱신하지 않고 칸 열쇠로 이름만 붙인다. 붙이는 조건(QA 36 F2):
        - 배지 성급이 추적 성급보다 낮지 않다(★2 → ★1은 같은 유닛일 수 없다)
        - 기술자가 있으면 닮음 >= `KEEP_MIN`, 없으면 `bench_memory`가 "그대로"(held)라고 한 칸만."""
        held = getattr(read, "bench_held", False)

        def put(u: Any, key: Key | None, d: np.ndarray | None) -> Any:
            tr = self.slots.get(key) if key is not None else None
            if tr is None or not tr.unit_id or u.unit_id not in (None, tr.unit_id):
                return u
            if u.star is not None and tr.star is not None and u.star < tr.star:
                return u
            if d is not None and tr.desc is not None:
                if similarity(tr.desc, d) < KEEP_MIN:
                    return u
            elif not (held and u.name_source == "held"):
                return u                                  # 그림을 비교할 수 없으면 bench_memory "그대로" 칸만
            return replace(u, unit_id=tr.unit_id, unit_conf=round(min(tr.conf, TRACKED_CONF), 3),
                           name_source="tracked", star=u.star if u.star is not None else tr.star)
        board = tuple(put(u, _key(u, False), d) for u, d in zip(read.board, _pad(list(board_desc), len(read.board))))
        bench = tuple(put(u, _key(u, True), d) for u, d in zip(read.bench, _pad(list(bench_desc), len(read.bench))))
        return _with_unplaced(replace(read, board=board, bench=bench))

    def _update(self, read: Any, board_desc: list, bench_desc: list, now: float, shop: tuple | None) -> Any:
        self._shop_buys(shop, now)
        cur: dict[Key, tuple[Any, np.ndarray | None]] = {}
        for u, d in zip(read.board, _pad(board_desc, len(read.board))):
            k = _key(u, False)
            if k is not None:
                cur[k] = (u, d)
        for u, d in zip(read.bench, _pad(bench_desc, len(read.bench))):
            k = _key(u, True)
            if k is not None:
                cur[k] = (u, d)
        gap = self._last_active is None or now - self._last_active > BUY_WINDOW_S
        self._last_active = now
        prev_occ = self._bench_occ
        # 관측 공백(전투·공동 선택) 뒤 첫 프레임의 새 칸은 구매 칸 후보가 아니다(무엇이 언제 왔는지 모른다, QA 36 F1b)
        empties = [i for i in range(BENCH) if i not in prev_occ] if prev_occ is not None and not gap else None
        keep_min = KEEP_MIN_AFTER_GAP if gap else KEEP_MIN
        # 1) 그대로 있는 칸: 자리 바꾸기 · 다른 유닛으로 바뀐 칸 가려내기
        stay = [k for k in cur if k in self.slots]
        self._swaps(stay, cur)
        vanished: list[tuple[Key, Track]] = [(k, t) for k, t in self.slots.items() if k not in cur]
        new_keys = [k for k in cur if k not in self.slots]
        # 합성은 사본이 줄어든다: 성급이 오른 프레임에 다른 칸이 하나 이상 사라져야 합성 구매 후보다(구슬·증강 성급 상승 차단)
        copy_vanished = bool(vanished)
        for k in stay:
            tr = self.slots.get(k)
            if tr is None:
                new_keys.append(k)
                continue
            u, d = cur[k]
            s = similarity(tr.desc, d) if tr.desc is not None and d is not None else None
            star_drop = u.star is not None and tr.star is not None and u.star < tr.star   # QA 36 W1: 같은 유닛은 성급이 안 내려간다
            if (s is not None and s < keep_min) or star_drop:
                vanished.append((k, tr))                  # 다른 유닛이 이 칸에 섰다(판매 후 구매 등)
                del self.slots[k]
                new_keys.append(k)
                continue
            if u.star is not None and tr.star is not None and u.star > tr.star and not gap and copy_vanished:
                self.star_ups.append((now, k))           # 합성(공백 뒤 첫 프레임의 상승은 언제 무엇으로 올랐는지 모른다)
            if u.star is not None:
                tr.star = u.star
            elif s is not None and s < STAR_KEEP_MIN:
                tr.star = None
            if d is not None and (s is None or s >= DESC_REFRESH_MIN):
                tr.desc = d
        for k, _ in vanished:
            self.slots.pop(k, None)
        # 2) 옮기기: 사라진 칸(+잠시 전에 사라진 대기 정체) ↔ 새 칸
        old = [(t0, tr) for t0, tr in self.limbo if now - t0 <= LIMBO_S]
        pool: list[tuple[Key | None, Track]] = [(k, t) for k, t in vanished] + [(None, t) for _, t in old]
        # 구매가 기다리는 중이면 "가장 왼쪽 빈 칸"에 생긴 새 벤치 칸은 옮기기 후보에서 뺀다(산 유닛이다)
        if any(not bb.used and now - bb.at <= BUY_WINDOW_S for bb in self.buys) and empties:
            new_keys = [k for k in new_keys if not (k[0] == "bench" and k[1] == empties[0])] +                 [k for k in new_keys if k[0] == "bench" and k[1] == empties[0]]
            buy_slot = ("bench", empties[0])
        else:
            buy_slot = None
        cands = [k for k in new_keys if k != buy_slot]
        buy_slot_suspect = False
        if buy_slot is not None and buy_slot in cur and buy_slot in new_keys:
            # QA 36 F1a: 사라진 유닛이 그 가장 왼쪽 빈 칸으로 **옮겨 갔을** 수도 있다. 그 칸 그림을 닮았고 다른 새 칸을 확실히
            # 더 닮지 않았으면, 옮기기도 구매도 짝짓지 않는다(둘 다 모름)
            bd = cur[buy_slot][1]
            for i, (src, tr) in enumerate(list(pool)):
                if tr.desc is None or bd is None:
                    continue
                s_buy = similarity(tr.desc, bd)
                s_other = max((similarity(tr.desc, cur[k][1]) for k in cands if cur[k][1] is not None), default=0.0)
                if s_buy >= SINGLE_MIN and s_other < s_buy + MATCH_MARGIN:
                    buy_slot_suspect = True
                    if src is not None and tr.unit_id:
                        self.dropped += 1
                    pool[i] = (src, Track(None, tr.star, None))     # 이 정체는 버린다(어디로 갔는지 모른다)
        moved = self._match(pool, cands, cur, single=len(vanished) == 1 and len(cands) == 1 and not old)
        used = set()
        for pi, key in moved:
            src_key, tr = pool[pi]
            used.add(pi)
            u, d = cur[key]
            tr.moved = True
            if u.star is not None:
                tr.star = u.star
            if d is not None:
                tr.desc = d
            self.slots[key] = tr
        nv = len(vanished)
        self.limbo = [(now, tr) for i, (_, tr) in enumerate(pool[:nv]) if i not in used and tr.unit_id]
        self.limbo += [(t0, tr) for j, (t0, tr) in enumerate(old) if nv + j not in used]
        # 3) 새 칸(짝 없는): 새 정체(이름은 vision·구매가 채운다)
        fresh = [k for k in new_keys if k not in self.slots]
        fresh_bench = sorted(k[1] for k in fresh if k[0] == "bench")
        for key in fresh:
            u, d = cur[key]
            self.slots[key] = Track(None, u.star, d)
            if key[0] == "bench" and empties is not None:      # 직전 벤치를 모르면(첫 프레임·공백 뒤) 구매 칸 후보가 아니다
                rank = empties.index(key[1]) if key[1] in empties else None
                if buy_slot_suspect:
                    rank = None                           # 옮긴 유닛일 수 있는 칸이 끼면 이 프레임 새 칸 모두 후보 아님
                self.appeared.append(_Appear(key, now, rank, len(fresh_bench)))
        # 4) 구매 짝짓기 · 합성 구매
        self._pair_buys(now)
        # 5) vision 이름과 합치기
        for k, (u, _) in cur.items():
            self._merge_vision(k, u)
        self._bench_occ = {k[1] for k in cur if k[0] == "bench"}
        return self._label(read, cur)

    # ------------------------------------------------------------------ 세부
    def _shop_buys(self, shop: tuple | None, now: float) -> None:
        if shop is None:
            return
        last, self._last_shop = self._last_shop, shop
        if last is None or len(last) != len(shop):
            return
        changed = [i for i, (a, b) in enumerate(zip(last, shop)) if a != b]
        if len(changed) > 2:                             # 새로고침·라운드 전환
            return
        for i in changed:
            if last[i] not in (None, "*") and shop[i] is None:
                self.note_purchase(last[i], now, "shop")

    def _swaps(self, stay: list[Key], cur: dict) -> None:
        for i, a in enumerate(stay):
            for b in stay[i + 1:]:
                ta, tb = self.slots.get(a), self.slots.get(b)
                da, db = cur[a][1], cur[b][1]
                if ta is None or tb is None or ta.desc is None or tb.desc is None or da is None or db is None:
                    continue
                straight = similarity(ta.desc, da) + similarity(tb.desc, db)
                x1, x2 = similarity(ta.desc, db), similarity(tb.desc, da)
                if x1 >= SWAP_MIN and x2 >= SWAP_MIN and x1 + x2 - straight >= SWAP_MARGIN:
                    self.slots[a], self.slots[b] = tb, ta
                    ta.moved = tb.moved = True

    def _match(self, pool: list, new_keys: list[Key], cur: dict, *, single: bool) -> list[tuple[int, Key]]:
        if not pool or not new_keys:
            return []
        S = np.full((len(pool), len(new_keys)), -1.0)
        for i, (src, tr) in enumerate(pool):
            for j, k in enumerate(new_keys):
                u, d = cur[k]
                if tr.desc is None or d is None or src == k:
                    continue                              # 같은 칸에서 닮지 않아 떨어진 정체를 그 칸에 되돌리지 않는다
                if tr.star is not None and u.star is not None and tr.star != u.star:
                    continue                              # 성급이 다르면 같은 유닛이 아니다(합성은 따로)
                S[i, j] = similarity(tr.desc, d)
        out: list[tuple[int, Key]] = []
        if single:
            if S[0, 0] >= SINGLE_MIN:
                out.append((0, new_keys[0]))
            return out
        taken_i, taken_j = set(), set()
        for flat in np.argsort(-S, axis=None):
            i, j = divmod(int(flat), S.shape[1])
            s = S[i, j]
            if s < MATCH_MIN:
                break
            if i in taken_i or j in taken_j:
                continue
            row = sorted((S[i, jj] for jj in range(S.shape[1]) if jj != j), reverse=True)
            col = sorted((S[ii, j] for ii in range(S.shape[0]) if ii != i), reverse=True)
            if (row and s - row[0] < MATCH_MARGIN) or (col and s - col[0] < MATCH_MARGIN):
                self.dropped += 1                         # 애매 → 짝짓지 않는다(정체를 잃는 쪽이 안전)
                continue
            taken_i.add(i)
            taken_j.add(j)
            out.append((i, new_keys[j]))
        return out

    def _pair_buys(self, now: float) -> None:
        self.buys = [b for b in self.buys if now - b.at <= BUY_WINDOW_S]
        self.appeared = [a for a in self.appeared if now - a.at <= BUY_WINDOW_S]
        self.star_ups = [s for s in self.star_ups if now - s[0] <= BUY_WINDOW_S]
        live = sorted((b for b in self.buys if not b.used), key=lambda b: b.at)
        if not live:
            return
        # 구매 칸 후보 = "가장 왼쪽 빈 칸부터" 생긴 새 칸(같은 프레임에 n개면 순번 0..n-1). 다른 새 칸(끌어 놓기 등)은 보지 않는다
        fresh = sorted((a for a in self.appeared if a.key in self.slots and self.slots[a.key].unit_id is None
                        and a.rank is not None and a.rank < a.group
                        and self.slots[a.key].star in (None, 1)),     # 산 유닛은 ★1로 떨어진다(★2 새 칸은 구매가 아니다)
                       key=lambda a: (a.at, a.key[1]))
        if fresh:
            if (len(fresh) == len(live) and _leftmost_fill(fresh)
                    and all(abs(a.at - b.at) <= PAIR_DT for b, a in zip(live, fresh))):
                for b, a in zip(live, fresh):
                    tr = self.slots[a.key]
                    tr.unit_id, tr.source, tr.conf = b.champion, "purchase", PURCHASE_CONF
                    b.used = True
                self.appeared = [a for a in self.appeared if a not in fresh]
            return
        # 새 칸 없이 성급이 오른 칸 = 합성 구매(같은 챔피언 구매 하나일 때만)
        champs = {b.champion for b in live}
        # 구매 이후(같은 프레임·다음 프레임)에 보인 성급 상승만 — 구슬·전투 중 상승은 구매가 아니다(QA 36 F1b)
        first = min(b.at for b in live)
        ups = [k for t, k in self.star_ups if k in self.slots and first - PAIR_DT <= t <= first + PAIR_DT + BUY_WINDOW_S]
        if len(champs) == 1 and len(ups) == 1:
            tr = self.slots[ups[0]]
            champ = champs.pop()
            if tr.unit_id in (None, champ):
                tr.unit_id, tr.source, tr.conf = champ, tr.source if tr.unit_id else "purchase", PURCHASE_CONF
                for b in live:
                    b.used = True
                self.star_ups.clear()

    def _merge_vision(self, key: Key, u: Any) -> None:
        tr = self.slots.get(key)
        if tr is None or not u.unit_id:
            return
        strong = (u.unit_conf >= VISION_ADOPT and (u.name_source in STRONG_SOURCES or (
            u.name_source == "library" and getattr(u, "corroborated", None) is not False)))
        if tr.unit_id is None:
            if strong:
                tr.unit_id, tr.source, tr.conf = u.unit_id, "vision", min(TRACKED_CONF, u.unit_conf)
            return
        if tr.unit_id != u.unit_id and strong:
            log.info("유닛 정체 충돌(%s: 추적 %s / 화면 %s) — 둘 다 버립니다", key, tr.unit_id, u.unit_id)
            tr.unit_id, tr.source, tr.conf = None, "conflict", 0.0
            self.dropped += 1

    def _label(self, read: Any, cur: dict) -> Any:
        def put(u: Any, key: Key | None) -> Any:
            tr = self.slots.get(key) if key is not None else None
            if tr is None:
                return u
            star = u.star if u.star is not None else tr.star
            if tr.source == "conflict":
                return replace(u, unit_id=None, unit_conf=0.0, name_source="none", corroborated=None, star=star)
            if not tr.unit_id:
                return replace(u, star=star)
            if u.unit_id == tr.unit_id:
                return replace(u, star=star)
            strong_vision = u.unit_id and u.name_source in STRONG_SOURCES
            if strong_vision:
                return u
            src = "purchase" if tr.source == "purchase" and not tr.moved else "tracked"
            conf = PURCHASE_CONF if src == "purchase" else min(tr.conf, TRACKED_CONF)
            return replace(u, unit_id=tr.unit_id, unit_conf=round(conf, 3), name_source=src, corroborated=True,
                           star=star)
        board = tuple(put(u, _key(u, False)) for u in read.board)
        bench = tuple(put(u, _key(u, True)) for u in read.bench)
        return _with_unplaced(replace(read, board=board, bench=bench))


def _key(u: Any, on_bench: bool) -> Key | None:
    if on_bench:
        s = getattr(u, "bench_slot", None)
        return ("bench", s) if s is not None else None
    h = getattr(u, "hex", None)
    return ("board", tuple(h)) if h is not None else None


def _pad(descs: list, n: int) -> list:
    return list(descs) + [None] * (n - len(descs)) if len(descs) < n else list(descs)[:n]


def _leftmost_fill(fresh: list[_Appear]) -> bool:
    """새 칸들이 "가장 왼쪽 빈 칸부터" 찬 모양인가: 같은 프레임에 생긴 칸들의 순번이 정확히 0..n-1(한 프레임에 하나면 0)."""
    by: dict[float, list[int | None]] = {}
    for a in fresh:
        by.setdefault(a.at, []).append(a.rank)
    return all(sorted(r if r is not None else -1 for r in ranks) == list(range(len(ranks))) for ranks in by.values())


def _with_unplaced(read: Any) -> Any:
    """보드 특성 집합(`board_set`) 중 칸 이름이 붙은 챔피언을 `unplaced`에서 뺀다(이름 없는 보드 칸 수와 같을 때만)."""
    bs = tuple(getattr(read, "board_set", ()) or ())
    if not bs:
        return read
    named = {u.unit_id for u in read.board if u.unit_id}
    left = tuple(sorted(c for c in bs if c not in named))
    free = sum(1 for u in read.board if not u.unit_id)
    return replace(read, unplaced=left if len(left) == free else ())


__all__ = ["Track", "UnitTracker"]
