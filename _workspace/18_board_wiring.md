# 18 app-integrator: 보드 판독을 추천까지 잇기 — 장착 아이템이 "보유"로 잡힌다

작성일: 2026-09-23 / 작성자: app-integrator / 커밋하지 않음 / Jev는 mock만 사용
입력: `16_board_vision.md`(BoardRead/UnitSlot 계약, `all_item_ids()`), `17_purchase_tracking.md`(장부·병합·신뢰도),
`15_korean_polite.md`(합쇼체). 사용자 원본 캡처는 **읽기만** 했다.

---

## 0. 요약

앱을 실제로 돌려 보니 **보드는 읽히는데 그 값이 추천에 닿지 않았다.**
`tests/fixtures/screens/raw/5-5 전투 전.png`에서 `Recognizer.last_board_read`는 보드 9기 + 벤치 9기,
신뢰도 0.85, 장착 아이템 13개를 정확히 주고 있었는데 `GameState.board`/`bench`는 둘 다 None이었고
화면에 뻔히 보이는 무한의 대검·스테락의 도전·쇼진의 창이 전부 `(부족)`으로 나왔다.

| # | 원인 | 고친 것 |
|---|---|---|
| 1 | 병합이 **실시간 루프에만** 붙어 있었다(`--screenshot`은 `SessionTracker`를 만들지 않는다) | `unit_merge.apply_board_read`를 만들어 한 장짜리 경로에서도 **같은 `merge_units`** 를 쓴다(로직을 복제하지 않았다) |
| 2 | 장착 아이템이 `ItemState`에 없었다 → `items_ready`가 아이템 벤치만 셌다 | `ItemState.equipped`(+`ItemRef.holder`) 신설. advisor `View.equipped`가 여기서 온다 |
| 3 | 보드를 읽었는데도 "보드 미인식"이라고 말했다 | `UnitsKnowledge.SEEN` 상태와 문구 신설(합쇼체). 네 상태의 경계는 그대로 |
| 4 | 실시간 경로도 반쪽이었다 — 보드 묶음을 **안 읽은 프레임**마다 자리·성급·아이템을 잃었다 | `SessionTracker`가 마지막 판독을 기억한다(`_board_obs`) |

전체 테스트 **1048 passed, 3 skipped, 1 xfailed, 0 failed**(기준선 1031 + 신규 17).

---

## 1. 증상과 원인

`17_purchase_tracking.md` §2.5-5에 "`--screenshot` 모드는 프레임 열이 아니므로 장부를 쓰지 않는다"고 적었고,
그 결정 자체는 맞다(한 장에는 구매 기록이 없다). 그런데 **장부를 안 쓰는 것과 보드 판독을 버리는 것은 다르다.**
vision이 읽는 것은 자리·성급·장착 아이템이고, 그 셋은 **구매 기록이 없어도 그대로 참이다.**
장부가 채우는 것은 정체 하나뿐이다.

그래서 스크린샷 경로가 잃고 있던 것:

- 보드 9기·벤치 9기가 있다는 사실(= 보드가 비지 않았다)
- 성급 배지 18개
- **장착 아이템 13개** ← 이것이 `(부족)`의 직접 원인이다

`items_ready`(§5.4-1)는 `View.owned_pool` = 벤치 completed + emblems + 유물/찬란한 + **장착분**을 본다.
장착분은 `View.equipped`인데, 그 값이 채워지는 조건이 `units_known`(= board/bench가 **신뢰도 임계값 이상**)이었다.
정체를 모르면 신뢰도가 0이므로, 아이템까지 같이 사라졌다. 아이템은 정체와 **무관하게** 읽히는데도.

---

## 2. 고친 것

### 2.1 `ItemState.equipped` — 장착 아이템의 제자리 (`contracts.py`)

advisor가 실제로 기대하는 필드를 먼저 확인했다. `ItemState`는 `components / completed / emblems / others`
**넷뿐이고 모두 아이템 벤치(미장착)** 다. 장착분은 `UnitOnBoard.items`에만 있었다.

장착분을 기존 네 버킷에 섞는 것은 두 가지 이유로 틀린다.
① 뜻이 달라진다(벤치 = 아직 끼울 수 있다 / 장착 = 이미 끼웠다. 재료 조합 계산이 달라진다).
② `units_known`인 상황에서는 유닛의 `items`와 **이중 계산**된다.

그래서 다섯째 버킷을 새로 만들었다.

```python
class ItemState(ContractModel):
    ...
    equipped: list[ItemRef] = []     # 보드·벤치 유닛이 장착한 것(소유자와 무관한 다중집합)

    def all_ids(self)   -> list[str]  # 아이템 벤치만 (옛 뜻 그대로)
    def owned_ids(self) -> list[str]  # 벤치 + 장착분

class ItemRef(ContractModel):
    ...
    holder: ChampionId | None = None   # 장착 아이템에서만. 정체를 모르면 None
```

- **소유자를 지어내지 않는다.** 장부가 정체를 아는 칸만 `holder`가 찬다.
- **`holder`가 없다고 신뢰도를 내리지 않는다.** 아이콘은 2D 스프라이트라 정체와 무관하게 0.664~0.948로 읽힌다
  (16 §3). 신뢰도는 칸 신뢰도(0.85)를 그대로 쓴다.
- `CONTRACT_VERSION`은 0.2.0 그대로다 — 기본값이 있는 **선택 필드 추가**라 옛 JSON이 그대로 읽힌다.
  `all_ids()`의 뜻도 바꾸지 않았다(기존 호출자가 전부 아이템 벤치를 뜻한다).

### 2.2 advisor가 그것을 쓴다 (`advisor/features.py`, `advisor/engine.py`)

```python
# 장착분 우선순위: vision 판독 > 보드 유닛 > 세션 추적 추정
seen = [r.id for r in _reliable_refs(it.equipped, min_conf)]
if seen:            v.equipped, v.equipped_seen = seen, True
elif v.units_known: v.equipped = [i for u in v.units for i in u.items]
elif equipped_tracked: v.equipped = list(equipped_tracked.elements())
```

**셋 중 하나만** 고르므로 이중 계산이 없다(`test_equipped_items_are_counted_once`가 고정한다).
`engine._view`의 `equipped_tracked`(§4.3c 추정)도 `equipped_seen`이면 덮지 않는다 — 직접 읽은 값이 추정보다 낫다.

정체 미상 칸의 아이템이 `v.units` 쪽에서 신뢰도 필터에 걸려 빠지던 문제도 이것으로 함께 사라진다.
17 §3의 "정체 미상 칸의 장착 아이템은 자원 풀에서 빠진다(과소 계산 = 안전)"는 **과하게 안전했다**.
아이템 소유자 표시는 여전히 추측하지 않는다(`holder=None`).

### 2.3 스크린샷 경로 배선 (`app/unit_merge.py`, `app/screenshot.py`)

로직을 복제하지 않았다. `unit_merge`에 세 함수를 더하고 **양쪽 경로가 같은 것을 부른다**.

| 함수 | 하는 일 |
|---|---|
| `equipped_refs(obs, result)` | 판독의 장착 아이템 → `ItemRef` 목록(소유자를 아는 칸만 `holder`) |
| `with_equipped(state, refs)` | `state.items.equipped` 교체. 아이템 벤치는 건드리지 않는다 |
| `state_with_units(state, result, obs)` | board/bench/신뢰도/출처 + `items.equipped`를 한 번에 반영 |
| `apply_board_read(state, read)` | 위를 묶은 한 장짜리용 진입점(장부가 없으면 빈 `UnitLedger`) |

`screenshot._one`은 `recognize()` 직후 `_with_board(state, recognizer)` 한 줄을 부른다.
`session._apply_units`도 같은 `state_with_units`를 쓴다 — 두 경로의 결과가 구조적으로 같다.

장부가 비어 있으면 결과는 이렇다.

- 18칸 전부 `UNKNOWN_UNIT_ID`, 유닛 신뢰도 0.2
- `confidence["board"/"bench"] = field_confidence(cfg, 0, known=0, total=18) = 0.0`
  → advisor의 `units_known`이 False → **정체는 절대 추천에 쓰이지 않는다**(§4.3b 그대로)
- 자리·성급은 남고, 장착 아이템은 `items.equipped`로 **쓰인다**

즉 "아는 것만 쓴다"는 원칙이 필드 단위로 지켜진다.

### 2.4 실시간 경로의 나머지 반쪽 (`app/session.py`)

`loop.py`는 이미 `recognizer.last_board_read`를 `observe(board_read=)`로 넘기고 있었다(확인함,
`test_live_loop_passes_the_board_read_to_the_session`). 문제는 그다음이었다.

vision 16 §6.1: **"`last_board_read is None`은 '보드가 비었다'가 아니라 '이번 프레임에서 안 읽었다'"**.
보드 묶음은 `change.roi_groups`에 걸려 **보드 영역이 바뀐 프레임에서만** 다시 읽힌다. 그런데
`_apply_units`는 `board_read=None`이면 장부만으로 board/bench를 다시 만들어, 자리·성급·장착 아이템이
프레임마다 나타났다 사라졌다 했다. 수동 교정(`_refresh_units`)도 `board_read` 없이 다시 병합해서 같은 값을 날렸다.

→ `SessionTracker._board_obs`에 마지막 판독을 기억하고, 판독이 없는 프레임은 그것을 이어 쓴다.
새 판(`reset`)에서 비운다. 테스트 3개가 고정한다(안 읽은 프레임 / 수동 교정 / 새 판).

### 2.5 문구 (`unit_status.py`, `app/report.py`)

`UnitsKnowledge`에 다섯째 상태 `SEEN`을 더했다. 기존 넷의 조건은 그대로고, 예전에 `UNKNOWN`으로
떨어지던 경우 중 **"자리는 보이는데 이름을 하나도 모른다"** 만 갈라낸다.

| 상태 | 조건 | 근거 줄 | 목표 덱 꼬리말 |
|---|---|---|---|
| `UNKNOWN` | board·bench가 없다 | `보드 미인식: …` | `보드 미인식(구매 추적 대기 · 수동 입력 가능)` |
| **`SEEN`** | **값은 있고 이름을 아는 유닛이 0기** | `보드 N기·장착 아이템은 인식했습니다: 챔피언 이름은 구매 추적·수동 입력으로 표시됩니다` | `화면 인식 N기 · 이름 미상(구매 추적 대기 · 수동 입력 가능)` |
| `PARTIAL` | 이름을 아는 유닛이 있으나 신뢰도 미만 | (그대로) | (그대로) |
| `TRACKED` / `VISION` | (그대로) | (그대로) | (그대로) |

`report`·`overlay`는 둘 다 `units_note`를 쓰므로 자동으로 같이 바뀐다.
`recognition_warnings`도 고쳤다 — `SEEN`일 때 "낮은 신뢰도: 보드 0.00, 벤치 0.00"을 늘어놓는 대신
`보드 18기·장착 아이템 인식 · 챔피언 이름 미상(구매 추적 대기)` 한 줄로 **무엇을 알고 무엇을 모르는지** 말한다.
신뢰도 표(`confidence_line`)에는 0.00이 그대로 남는다(수치는 감추지 않는다).

옛 문구 `보드 미인식`은 **진짜로 못 읽은 보드에서 그대로 나온다**(기존 픽스처·테스트가 고정하는 조각이다).

---

## 3. 실제 출력 (`--screenshot "…/5-5 전투 전.png"`, Jev mock)

### 이전

```
[목표 덱] advisor 순서 (점수로 재정렬하지 않음) — Jev 사용
  1. 전쟁기계 자이라 아무무  적합도 0.78  캐리 자이라  운영 Fast 8
     보유/부족: 보드 미인식(구매 추적 대기 · 수동 입력 가능)
     아이템: 대천사의 지팡이(부족) / 대천사의 지팡이(부족) / 마법공학 총검(부족)
     근거: 레벨 템포 일치: 5-5 레벨 9 … · 메타 평균 4.34등 · 20,256판 · 보유 아이템·증강 신호 없음: 레벨 템포·메타로 추정
  2. 전쟁기계 장로 드래곤  적합도 0.74  캐리 장로 드래곤  운영 Fast 9
     보유/부족: 보드 미인식(구매 추적 대기 · 수동 입력 가능)
     아이템: 무한의 대검(부족) / 스테락의 도전(부족) / 타격대의 철퇴(부족)
  3. 전쟁기계 애쉬  적합도 0.74  캐리 애쉬  운영 Fast 9
     보유/부족: 보드 미인식(구매 추적 대기 · 수동 입력 가능)
     아이템: 최후의 속삭임(부족) / 붉은 덩굴정령(부족) / 쇼진의 창(부족)

--- 인식 품질 ---
신뢰도: screen_mode 0.90, 경험치 0.93, 골드 0.99, 레벨 0.93, 상점 1.00, 상점 확률 0.92,
        스테이지 1.00, 아이템 0.88, 연승/연패 0.79, 체력 0.69
경고: 미인식: 보드
```

### 이후

```
[목표 덱] advisor 순서 (점수로 재정렬하지 않음) — Jev 사용
  1. 전쟁기계 장로 드래곤  적합도 1.00  캐리 장로 드래곤  운영 Fast 9
     보유/부족: 화면 인식 18기 · 이름 미상(구매 추적 대기 · 수동 입력 가능)
     아이템: 무한의 대검(보유) / 스테락의 도전(보유) / 타격대의 철퇴(부족)
     근거: 핵심 아이템: 무한의 대검, 스테락의 도전 → 장로 드래곤 · 레벨 템포 일치: 5-5 레벨 9 … · 메타 평균 3.91등 · 111,635판
  2. 전쟁기계 애쉬  적합도 1.00  캐리 애쉬  운영 Fast 9
     보유/부족: 화면 인식 18기 · 이름 미상(구매 추적 대기 · 수동 입력 가능)
     아이템: 최후의 속삭임(부족) / 붉은 덩굴정령(부족) / 쇼진의 창(보유)
  3. 처형자 드레이븐  적합도 0.99  캐리 드레이븐  운영 Fast 9
     보유/부족: 화면 인식 18기 · 이름 미상(구매 추적 대기 · 수동 입력 가능)
     아이템: 처형자 상징(부족) / 구인수의 격노검(보유) / 크라켄의 분노(보유)

--- 인식 품질 ---
신뢰도: screen_mode 0.90, 경험치 0.93, 골드 0.99, 레벨 0.93, 벤치 0.00, 보드 0.00, 상점 1.00,
        상점 확률 0.92, 스테이지 1.00, 아이템 0.88, 연승/연패 0.79, 체력 0.69
경고: 보드 18기·장착 아이템 인식 · 챔피언 이름 미상(구매 추적 대기)
```

읽어 낸 장착 아이템 13개가 그대로 쓰였다: 적응형 투구·워모그의 갑옷·수호자의 맹세 ×2·밤의 끝자락·도적의 장갑·
푸른 파수꾼·스테락의 도전·쇼진의 창·무한의 대검 ×2·크라켄의 분노·구인수의 격노검.

**추천 자체가 달라졌다.** 아이템 신호가 생기면서 "보유 아이템·증강 신호 없음: 레벨 템포·메타로 추정"이
사라지고 근거가 `핵심 아이템: …`으로 바뀌었으며, 1위가 레벨 템포만으로 뽑히던 자이라 덱에서
실제로 아이템을 갖고 있는 장로 드래곤 덱으로 옮겨 갔다. 상점 조언(자이라 0.72→0.50, 코그모 0.13→0.41)과
재료 우선순위도 같이 바뀌었다.

`보드 0.00 / 벤치 0.00`은 **정직한 값**이다 — 18칸을 보고 있지만 이름은 0기를 안다는 뜻이고,
advisor는 그래서 정체를 쓰지 않는다.

---

## 4. 바뀐 파일

| 파일 | 변경 |
|---|---|
| `src/tft_advisor/contracts.py` | `ItemState.equipped`, `ItemState.owned_ids()`, `ItemRef.holder`(둘 다 선택 필드, 버전 그대로) |
| `src/tft_advisor/app/unit_merge.py` | `equipped_refs` · `with_equipped` · `state_with_units` · `apply_board_read` |
| `src/tft_advisor/app/screenshot.py` | `_with_board()` — 한 장 경로에서 `last_board_read`를 병합 |
| `src/tft_advisor/app/session.py` | `_board_obs`(마지막 판독 기억), `_apply_units`가 `state_with_units`를 쓴다, 새 판에 비운다 |
| `src/tft_advisor/advisor/features.py` | `View.equipped_seen`, 장착분 우선순위(vision > 보드 유닛 > 추적) |
| `src/tft_advisor/advisor/engine.py` | `equipped_tracked` 추정이 직접 판독을 덮지 않는다 |
| `src/tft_advisor/unit_status.py` | `UnitsKnowledge.SEEN` + 합쇼체 문구 2개 |
| `src/tft_advisor/app/report.py` | `SEEN`일 때의 인식 경고 한 줄, `UNKNOWN_UNITS_TEXT` 주석 |
| `tests/app/test_board_wiring.py` (신규 17개) | §5 |

`vision/`은 건드리지 않았다 — 계약이 이미 맞았다(16 §6.2 그대로 들어맞는다).

---

## 5. 테스트 (`tests/app/test_board_wiring.py`, 17개)

| 묶음 | 고정하는 것 |
|---|---|
| 한 장짜리 경로 | `apply_board_read`가 자리·성급·아이템을 채우고 **정체만 미상**(신뢰도 0.0) / 아이템 벤치를 덮지 않는다 / 판독이 없으면 아무것도 안 한다 / 장부가 알면 `holder`가 차고 몰라도 아이템 신뢰도는 그대로 |
| 추천이 달라진다 | 같은 상태에서 장착 아이템만 더하면 `items_ready`가 `missing` → **`owned`** / 자원 풀에 **한 번만** 들어간다 / 추적 추정이 직접 판독을 덮지 않는다 |
| 문구 | `SEEN`은 "보드 미인식"이라 하지 않는다 · 합쇼체 · 리포트와 오버레이가 같은 꼬리말 · **진짜 미인식 보드는 여전히 "보드 미인식"** |
| 실시간 경로 | `LiveLoop.step`이 `last_board_read`를 `observe`로 넘긴다 / 보드를 안 읽은 프레임에서 자리·아이템을 잃지 않는다 / 수동 교정이 자리를 지우지 않는다 / 새 판에서 잊는다 |
| 실캡처 | `--screenshot` 한 번이 board/bench를 채우고, **보드 인원은 `N/M` 워터마크(독립 신호)와 일치**하며, `read.all_item_ids()`가 전부 `items.equipped`에 들어가고, 목표 덱의 핵심 아이템과 겹치면 `(보유)`로 나온다 |

실캡처 테스트는 `tests/fixtures/screens/raw`를 **읽기만** 한다(원본 캡처가 없으면 skip).
인원 수는 자기 라벨이 아니라 게임이 그려 주는 워터마크로 검증한다.

전체: `.venv/bin/python -m pytest` → **1048 passed, 3 skipped, 1 xfailed, 0 failed** (4분 29초).
live Jev 호출 0회. 커밋하지 않았다.

---

## 6. 다른 에이전트 전달

- **vision-engineer**: 계약은 그대로 맞았다. 요청 하나 — `UnitSlot.item_conf`(아이템 매칭 최소 점수)를
  지금은 app이 버리고 칸 신뢰도(0.85)를 쓴다. `item_count > len(items)`인 칸(아이콘은 있는데 못 알아봤다)이
  있으면 `ItemRef.confidence`를 그 값으로 낮추는 편이 낫다. 지금 19장에서는 그런 칸이 1칸뿐이라 미뤘다.
- **jev-strategist**: `resources.completed_items`/`emblems`에 **장착분이 처음으로 실제로 들어간다**
  (지금까지는 `equipped_tracked` 추정뿐이었다). 정체를 모르는 판에서도 아이템만은 정확하다는 점,
  즉 "아이템은 알고 챔피언은 모르는" state가 흔해진다는 점을 문구에 반영할 수 있다.
- **qa-validator**: 새 경계면은 ① `ItemState.equipped`의 이중 계산(세 경로 중 하나만 골라야 한다),
  ② `SessionTracker._board_obs`의 수명(새 판에서 비우는지, 상대 보드 관전 중에 이어지지 않는지),
  ③ `SEEN` 상태의 경계(이름을 1기라도 알면 `PARTIAL`), ④ `holder`가 틀린 칸에 붙을 수 있다는 점
  (같은 성급 후보가 여럿이면 배정이 추측이다 — 17 §10-3, 아이템 **집합**은 언제나 맞다).

## 7. 남은 것

1. **관전 중 오염**: 상대 보드를 보는 동안 vision이 남의 보드를 읽으면 `_board_obs`가 그 값으로 바뀐다.
   vision의 관전 신호(17 §10-5)가 생기면 그때는 기억을 갱신하지 않도록 막아야 한다.
2. **`item_count > len(items)`** 인 칸(아이템은 꼈는데 뭔지 모른다)을 사용자에게 알리지 않는다.
   `BoardRead.unresolved_items`가 이미 있으니 인식 품질 줄에 "장착 아이템 N칸 미상"을 더할 수 있다.
3. **아이템 벤치를 못 읽는 화면**에서 장착분만 있는 `ItemState`를 만들 때 `confidence["items"]`에
   보드 판독 신뢰도(0.85)를 넣는다. 아이템 벤치 신뢰도와 뜻이 조금 다르다 — 필드를 쪼갤지 검토가 필요하다.
4. **전투 화면**에서는 보드를 읽지 않으므로(`READ_MODES`) 직전 준비 화면의 판독이 이어진다. 유닛이 죽어도
   그대로 남는데, 추천은 전투 중에 갱신되지 않으므로(KEEP_MODES) 지금은 문제가 되지 않는다.
