# 17 app-integrator: 보유 유닛 장부 — 상점 구매 추적으로 "무엇을 가지고 있는지" 알기

작성일: 2026-09-23 / 작성자: app-integrator / 커밋하지 않음 / Jev는 mock만 사용
입력: `06_app-integrator_phase4.md` §2.5·§9-5, `10_app_integrator_fixes.md` §2(늘기만 한다·수동 우선 선례),
`02_jev-strategist_design.md` §4.3b(보유 유닛 결측 규칙, `copies_owned`/`buy_makes_2star`), `15_korean_polite.md`(합쇼체),
vision 16(`vision/board.py`의 `BoardRead`/`UnitSlot`, `Recognizer.last_board_read`) — **같은 시간에 만들어진 계약에 맞췄다**.

---

## 0. 요약

| # | 한 일 | 결과 |
|---|---|---|
| 1 | 상점 칸 + 골드 변화로 구매·판매 추론 (`app/ledger.py`) | 정확히 맞아떨어질 때만 확정. 맞지 않으면 **추측하지 않고** '애매'로 센다 |
| 2 | 보유 유닛 장부(챔피언 → 1성 등가 사본 수, 3=2성/9=3성) | `SessionData.units`, `session.json`에 영속, 새 판에 초기화 |
| 3 | 상점 밖 획득(공동 선택·증강·모루) | `add_unit(..., source="carousel"/"augment"/"anvil")` |
| 4 | vision 보드 판독과 병합 (`app/unit_merge.py`) | 자리·성급·아이템은 vision, 정체는 장부. **개수는 vision이 이긴다**, 남는 칸은 `UNKNOWN_UNIT_ID` |
| 5 | 수동 교정 | `SessionTracker` API 6개 + 문자열 명령(`app/units_cmd.py`) + 터미널 입력(`--live`/`--no-overlay` 공용) |
| 6 | 사용자 문구 | `tft_advisor/unit_status.py`에 4상태(모름·부분 확인·구매 추적·화면 인식) 합쇼체 문구를 모았다 |
| 7 | 테스트 | `tests/app/test_unit_ledger.py` **34개 신규**. 전체 **1031 passed, 0 failed** |

**효과**: `GameState.board`/`bench`가 실제로 채워지므로 §4.3b의 "보유 유닛을 안다" 경로가 처음으로 열린다 —
`TargetComp.owned_units`/`missing_units`, `copies_owned`/`buy_makes_2star`, 2성·3성 보너스, `U(c)` 항, S1-owned 문구,
활성 특성 계산이 모두 살아난다. Jev에 가는 state에도 `board`/`bench`가 들어간다.

---

## 1. 왜 이 방식인가

화면의 3D 모델로는 챔피언을 식별할 수 없다(vision 16). 반면 **상점은 이름표가 붙은 2D 카드**이고 이미 99% 정확도로 읽는다.
TFT에서 유닛이 손에 들어오는 경로는 다섯뿐이다.

| 경로 | 신호 | 추적 |
|---|---|---|
| 상점 구매 | 칸이 사라지고 골드가 코스트만큼 준다 | **자동**(이 문서의 주 내용) |
| 판매 | 골드가 판매가만큼 는다 | 자동(같은 라운드 안에서, 값이 유일할 때) |
| 공동 선택(캐러셀) | 없음 | 수동(`add_unit`) — vision 병합으로 개수 불일치는 드러난다 |
| 증강·모루/구슬 보상 | 없음 | 수동 |
| 합성(3사본 → 2성) | 없음 | 장부가 계산한다(사본 수 기반) |

즉 **정체는 상점에서, 자리·성급·아이템은 화면에서** 온다. 둘을 합치는 것이 `unit_merge`다.

---

## 2. 추론 규칙 (`app/ledger.py`)

### 2.1 정산 단위는 "미결 거래"다

상점 ROI와 골드 ROI는 서로 다른 묶음이라 **한 프레임 차이로 따로 도착**한다(칸이 먼저 비고 골드가 다음 프레임).
그래서 프레임 쌍마다 판정하지 않고, 사라진 칸을 `pending`에 쌓아 두었다가 골드가 맞아떨어질 때 한꺼번에 확정한다.

```
anchor_gold           마지막으로 정산한 시점의 골드
pending[]             사라진 상점 칸(챔피언 ID, 코스트)
spent = anchor - now  그 사이에 쓴 골드(음수면 벌었다)
rest  = spent - Σ(pending 코스트) - (리롤이면 2)
```

| `rest` | 판정 |
|---|---|
| `0` | **구매 확정**(`evidence="gold"`, 신뢰도 1.0) |
| `4`의 배수(≤16)이고 경험치/레벨이 올랐다 | 구매 + **경험치 구매**(장부와 무관) |
| `< 0`이고 미결 구매가 없고 스테이지가 그대로 | **판매 후보**(§2.3) |
| 그 밖 | 아직 설명되지 않았다 → `settle_s`(2초)까지 기다린다 → 그래도 안 되면 **애매**(§2.4) |

- 상점 칸이 **3칸 이상 한꺼번에 다른 유닛으로** 바뀌면 새로고침이다: 스테이지가 그대로면 리롤(2골드), 바뀌었으면 라운드 전환
  (이자·연승 수입이 섞여 골드 수식을 못 쓴다). 두 경우 모두 그 자리에서 마지막 정산을 하고 기준을 다시 잡는다.
- 1~2칸만 내용이 바뀌는 일은 게임에서 일어나지 않는다 → **인식 흔들림**으로 보고 구매로 세지 않는다(`noise` 카운터).
- **빈 칸(EMPTY)과 '못 읽음(UNKNOWN)'을 구분한다.** 구매 신호는 `챔피언 → 빈 칸`뿐이다. 못 읽은 칸은 내용 변화로 본다.
- 골드를 아예 못 읽는 프레임 열에서는 사라진 칸이 전부 챔피언일 때만 `evidence="slot"`으로 넣는다(신뢰도 **0.6**).
  "무엇을" 샀는지는 애매하지 않기 때문이다. 골드를 읽는데 값이 안 맞으면 이 경로를 쓰지 않는다(그때는 애매다).
- 특수 상품(`shop_specials`, 예 "3단계와 함께")은 챔피언이 아니므로 장부에 넣지 않는다. 코스트를 알면 골드 수식에는 넣는다.

### 2.2 합성 규칙

장부는 챔피언당 **1성 등가 사본 수**만 센다. 성급은 거기서 계산한다(`bodies_for`): `3사본 = 2성`, `9사본 = 3성`,
`5사본 = 2성 1기 + 1성 2기`. 게임의 자동 합성과 같은 규칙이라 "3번째 사본을 샀더니 2성이 되었다"가 저절로 맞는다.

### 2.3 판매

`sell_value(cost, star)`: 1코스트는 전액(1/3/9), 2코스트 이상은 `2성 = 3c-1`, `3성 = 9c-2`.
판매는 **골드가 늘었을 때만**, **같은 라운드 안에서만**(라운드가 바뀌면 수입이 들어온다), **값이 유일하게 맞을 때만** 적용한다.
1코스트 유닛 둘을 갖고 있으면 어느 쪽을 팔았는지 알 수 없다 → 장부를 건드리지 않고 애매로 센다(설정 `ledger_track_sales`로 끌 수 있다).

### 2.4 애매(ambiguous) — 지어내지 않기

설명되지 않은 거래는 **유닛을 만들지도 지우지도 않고** `units.ambiguous`만 올린다. 그 값이 `board`/`bench` 필드 신뢰도를 낮춘다.

```
conf = ledger_confidence - (ledger_confidence - ledger_confidence_uncertain) * min(1, ambiguous / ledger_uncertain_after)
     = 0.85 → 0.73 → 0.62 → 0.50 (애매 0·1·2·3건)
```

`[vision] state_min_confidence`(0.6) 아래로 내려가면 advisor는 §4.3b대로 보유 유닛을 **통째로 "모름"** 으로 다룬다
(= 이 기능을 넣기 전과 같은 동작). 즉 **틀린 유닛 목록으로 추천하느니 모른다고 한다.** 사용자가 `유닛 확인`으로 확인하면 0으로 돌아간다.
vision과 병합할 때는 `이름을 아는 유닛 / vision이 본 유닛` 비율을 한 번 더 곱한다(절반을 모르면 임계값 아래로 떨어진다).

### 2.5 이 추론이 다루지 못하는 것 (한계)

1. **프레임을 놓친 구매**: 상점 묶음을 못 읽는 동안(전투 아닌 화면 전환 등) 사고팔면 골드만 움직인다 → 애매로 잡히거나
   (설명 안 되는 골드 감소), 라운드 전환에 묻힌다. 경험치 구매와 코스트 4 유닛 구매는 골드만으로는 구분되지 않는다
   (경험치/레벨 증가를 함께 볼 때만 경험치로 판정한다).
2. **같은 창 안의 구매+판매**가 상쇄되면(골드 0) 애매가 된다.
3. **증강·기믹이 주는 골드**(스테이지 안에서 갑자기 +N)가 판매가와 우연히 같으면 없는 판매를 만들 수 있다. 상한을 두지 않았다.
4. **상대 보드 관전**: 상점·골드 HUD는 내 것이 그대로 보이므로 증강 줄과 달리 오염되지 않는다.
5. `--screenshot` 모드는 프레임 열이 아니므로 장부를 쓰지 않는다(`SessionTracker`를 만들지 않는다).

---

## 3. vision 보드 판독과의 병합 계약 (`app/unit_merge.py`)

vision 16이 `Recognizer.last_board_read`에 `BoardRead(board, bench, confidence, bars, unresolved_items)`를 남긴다.
칸(`UnitSlot`)은 `star / items / hex / bench_slot / confidence / unit_id(항상 None)`이다.
app은 `board_obs_from()`으로 받는다 — **속성 이름이 같으면 그대로, 딕셔너리여도, `GameState.board`여도 받는다**(어댑터 하나만 고치면 된다).

| 순서 | 규칙 |
|---|---|
| 1 | vision이 `unit_id`를 주면 그 값이 이긴다(직접 본 것이다). 지금은 항상 None이다 |
| 2 | 남은 칸에 장부의 유닛을 **성급이 맞는 것부터** 배정한다. 그 안에서는 (성급↓, 코스트↓, ID)로 결정적이다 |
| 3 | 보드 칸이 벤치보다 먼저다(보통 센 유닛을 올린다) |
| 4 | **개수는 vision이 이긴다.** vision 9기 · 장부 7기 → 2칸은 `UNKNOWN_UNIT_ID`(신뢰도 0.2 → advisor 계산에서 빠진다) |
| 5 | 장부가 더 많으면(vision 3기 · 장부 5기) 남는 유닛은 내보내지 않는다(`dropped`) — 판매를 놓쳤거나 잘못 추적한 것이다 |
| 6 | 성급은 vision이 이긴다. 장부와 다르면 `star_conflicts`로 세고 신뢰도에 반영한다(장부를 몰래 고치지 않는다) |
| 7 | vision 판독이 없으면 장부만으로 만든다: `hex`/`bench_slot`은 None, 레벨만큼 보드에 나머지는 벤치에 둔다 |

- **`unit_id`를 지어내지 않는다.** 정체 미상은 언제나 `contracts.UNKNOWN_UNIT_ID`("UNKNOWN", 정적 데이터에 없다)이고
  신뢰도가 `state_min_confidence` 아래다. advisor의 `build_view`가 신뢰도로 거르므로 Jev state·특성 계산에 절대 들어가지 않는다.
- 같은 성급의 후보가 여럿이면 **어느 칸이 어느 챔피언인지**는 확정할 수 없다(자리 정보가 없다). 배정은 결정적이지만 틀릴 수 있다.
  영향 범위는 `UnitOnBoard.items`의 소유자 표시뿐이고, 장착 아이템의 **집합**은 언제나 맞다(자원 풀·`items_ready`는 정확하다).
- 정체 미상 칸의 장착 아이템은 신뢰도 필터에 걸려 자원 풀에서 빠진다(과소 계산 방향 = 안전).

---

## 4. 수동 교정

### 4.1 API (`SessionTracker`, 오버레이·트레이가 그대로 부른다)

| 메서드 | 뜻 |
|---|---|
| `add_unit(id, copies=1, star=None, source="manual")` | 더한다. 상점 밖 획득은 `source="carousel"/"augment"/"anvil"` |
| `remove_unit(id, copies=1, star=None)` | 뺀다. `copies=0`이면 그 챔피언을 지운다 |
| `set_unit_star(id, star)` | "이 챔피언은 N성입니다" → 사본 수를 맞춘다 |
| `set_units({id: copies})` / `clear_units()` | 장부를 통째로 정하거나 비운다 |
| `confirm_units()` | "지금 장부가 맞습니다" → `ambiguous`를 0으로(신뢰도 회복) |
| `units_rows()` | `(ID, 사본 수, 성급, 출처)` 목록 — UI 표시용 |

수동 변경은 잠금(`RLock`)으로 캡처 스레드와 직렬화되고, 즉시 `session.json`에 저장되며, 출처가 `manual`이면
`GameState.field_source["board"] = manual`이 된다(증강의 "수동 우선" 선례와 같다).

### 4.2 문자열 명령 (`app/units_cmd.apply_command(tracker, line)`)

`유닛` / `유닛 추가 자야 [수]` / `유닛 제거 자야 [수|전부]` / `유닛 성급 자야 2` / `유닛 확인` / `유닛 초기화` / `도움말`.
이름은 한국어 표시 이름 또는 canonical ID이고 **정확 일치만** 받는다(비슷한 이름을 추측해서 엉뚱한 챔피언을 넣지 않는다).

### 4.3 지금 쓸 수 있는 입구

- **터미널**: `--live`(오버레이 모드 포함)와 `--no-overlay` 모두에서 앱을 띄운 터미널에 위 명령을 입력한다
  (`live.start_unit_console`, 대화형 터미널일 때만 켜진다. 데몬 스레드라 게임 루프를 막지 않는다).
- **오버레이 패널은 아직 없다**(§10-1). `apply_command` 하나만 부르면 되므로 입력 상자 하나로 붙일 수 있다.

---

## 5. 사용자 문구 (`tft_advisor/unit_status.py`, 합쇼체)

advisor(`scoring.py`)와 app(`report.py`/`overlay.py`)이 같은 판정을 쓰도록 최상위 모듈에 모았다(`patch_version.py`와 같은 이유).

| 상태 | 조건 | 근거 줄(`TargetComp.reasons`) | 목표 덱 줄 |
|---|---|---|---|
| `UNKNOWN` | board·bench가 없다 | `보드 미인식: 보유/부족 유닛은 구매 추적·수동 입력으로 표시됩니다` | `보유/부족: 보드 미인식(구매 추적 대기 · 수동 입력 가능)` |
| `PARTIAL` | 값은 있으나 신뢰도 < 임계값 | `보유 유닛 부분 확인: 구매 추적이 불확실합니다[, 이름 미상 N기] — 수동 확인을 권합니다` | `보유/부족: 부분 확인 — 구매 추적 N기(추천에는 쓰지 않습니다)` |
| `TRACKED` | 추적·수동 값으로 안다 | `보유 유닛: 구매 추적 기준[(이름 미상 N기)]` | 보유/부족 목록 + `(구매 추적)` |
| `VISION` | vision이 정체까지 읽었다 | (없음) | 보유/부족 목록 |

옛 문구 `"보드 미인식: 보유/부족 유닛은 수동 입력 시 표시"`는 사라졌지만 **부분 문자열 `보드 미인식`은 유지**했다
(기존 픽스처 `s11_mvp_no_hp_board.json`과 테스트가 이 조각을 고정한다).

---

## 6. 설정 키 (신규, `[app]`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `ledger_enabled` | `true` | false면 board/bench를 채우지 않는다(옛 동작) |
| `ledger_settle_s` | `2.0` | 상점 변화와 골드 변화가 어긋나도 기다려 주는 시간(초) |
| `ledger_confidence` | `0.85` | 애매한 거래가 없을 때의 필드 신뢰도 |
| `ledger_confidence_uncertain` | `0.5` | 애매가 `uncertain_after`건 쌓였을 때(0.6 미만 = advisor는 '모름') |
| `ledger_uncertain_after` | `3` | 위 값까지 내려가는 건수 |
| `ledger_track_sales` | `true` | 골드 증가로 판매를 추론할지 |

---

## 7. 바뀐/새 파일

| 파일 | 변경 |
|---|---|
| `src/tft_advisor/app/ledger.py` (신규 470줄) | `UnitLedger`, `PurchaseTracker`, `FrameObs`/`SlotKey`, `CostBook`, `sell_value`, `bodies_for`, `field_confidence`, `LedgerCfg` |
| `src/tft_advisor/app/unit_merge.py` (신규 300줄) | vision 계약 Protocol·어댑터(`board_obs_from`), `merge_units`, `MergeResult` |
| `src/tft_advisor/app/units_cmd.py` (신규 150줄) | 문자열 명령 → 수동 교정 API |
| `src/tft_advisor/unit_status.py` (신규) | 4상태 판정 + 합쇼체 문구(advisor·app 공용) |
| `src/tft_advisor/app/session.py` | `SessionData.units`, `_track_units`, `_apply_units`, 수동 교정 API 6개, `RLock`, `observe(board_read=)`, 새 판에 장부·추론기 초기화, `summary()` |
| `src/tft_advisor/app/loop.py` | `recognizer.last_board_read`를 `observe`로 전달 |
| `src/tft_advisor/app/live.py` | `LedgerCfg.from_settings`, `start_unit_console`(터미널 수동 교정) |
| `src/tft_advisor/app/report.py` / `overlay.py` | `comp_lines(units_note=)`, 상태별 문구 |
| `src/tft_advisor/advisor/scoring.py` | 보드 근거 줄을 `unit_status.units_reason`로 교체(문구 일원화) |
| `src/tft_advisor/contracts.py` | `UNKNOWN_UNIT_ID` 상수 추가(스키마 변경 아님, `CONTRACT_VERSION` 그대로 0.2.0) |
| `src/tft_advisor/config.py`, `config/settings.toml` | `[app] ledger_*` 6키 |
| `tests/app/test_unit_ledger.py` (신규 34개) | §8 |

---

## 8. 테스트

`.venv/bin/python -m pytest` → **1031 passed, 3 skipped, 1 xfailed, 0 failed**(7분 44초).
기준선은 967 passed / 2 failed였고, 그 2건(`test_raw_captures_*`)은 같은 시간에 돌던 vision-engineer가 해결했다.
늘어난 수에는 vision 16(보드 판독)의 신규 테스트도 섞여 있다.

`tests/app/test_unit_ledger.py`(34개)는 가짜 시계 + 프레임 열로 다음을 고정한다.

- 합성/판매가 규칙(`bodies_for`, `sell_value`)
- 깔끔한 구매 / **골드가 한 프레임 늦게 오는 구매** / 한 프레임에 두 번 구매
- 리롤(-2, 5칸 교체) · 경험치 구매(-4, 경험치 상승) · 구매+경험치 동시 · 레벨업
- 판매(사본 제거) / **판매가가 겹치면 추측하지 않는다**
- 골드 불일치·프레임 건너뜀·못 읽은 칸 → **유닛을 지어내지 않고** 애매로 센다
- 골드를 못 읽을 때의 칸 증거 구매(신뢰도 0.6)
- 3사본 → 2성 표시, 공동 선택·모루 추가, 수동 교정 6종, 문자열 명령
- vision 병합: 개수 불일치 양방향, 성급 우선, 아이템 부착, 어댑터(데이터클래스·딕셔너리)
- 영속 왕복(`units` 블록), 새 판 초기화, `units` 없는 옛 세션 파일, `ledger_enabled=false`
- 상태 문구 4종(합쇼체 검사 포함)과 리포트 출력
- **advisor가 실제로 달라진다**: board 미인식일 때 vs 장부가 찬 상태에서 `owned_units`가 비었다 → 채워지고 근거 줄이 바뀐다(mock Jev)

수동 스모크: `--screenshot`(픽스처 1장, mock) — 새 문구 `보드 미인식(구매 추적 대기 · 수동 입력 가능)` 확인.
live Jev 호출 0회.

---

## 9. 다른 에이전트 전달

- **vision-engineer**: `last_board_read`를 그대로 받는다(`app.unit_merge.board_obs_from`). 요청 두 가지.
  ① 칸별 `confidence`는 지금 상수(`SLOT_CONF`)인데, **성급 신뢰도(`star_conf`)가 낮은 칸은 `star=None`으로** 주면 병합이 더 안전하다
  (성급이 틀리면 사본 수 충돌로 잡힌다). ② `bars`/`unresolved_items`처럼 **보드를 다 못 읽은 신호**가 있으면 `BoardRead.confidence`에
  반영해 달라 — app은 그 값을 필드 신뢰도의 상한으로 쓴다. `unit_id`를 읽게 되면 그대로 채우면 장부보다 우선한다.
- **jev-strategist**: `board`/`bench`가 채워지기 시작하므로 §4.3b의 **"안다" 경로가 실제로 돌기 시작한다**(S1-owned 문구,
  `copies_owned`/`buy_makes_2star`, 2·3성 보너스, C3, 활성 특성). 출처는 `field_source=tracked|manual`이고 신뢰도는 0.5~0.85다.
  "vision이 본 것"이 아니라 "구매 기록"이라는 점을 감안한 문구가 필요하면 알려 달라(state에는 출처를 싣지 않았다).
- **qa-validator**: 새 경계면은 ① `PurchaseTracker`의 정산 상태기계(미결 거래·정산 창), ② `merge_units`의 개수/성급 충돌 규칙,
  ③ `UNKNOWN_UNIT_ID`가 advisor 신뢰도 필터에 걸려 빠지는지, ④ 수동 교정의 스레드 안전성(캡처 스레드 ↔ 콘솔 스레드),
  ⑤ 장부를 비웠을 때 직전 board가 이어지지 않는지다. 실캡처가 필요한 것은 **같은 라운드의 연속 프레임**(구매 직전/직후 2장)이다.

## 10. 남은 것

1. **오버레이 수동 교정 패널**(클릭으로 유닛 추가/삭제/성급). 지금은 터미널 입력 + API뿐이다.
2. **경험치 구매 vs 코스트 4 구매**를 경험치 판독 없이 가르지 못한다(§2.5-1).
3. 병합 시 같은 성급 후보가 여럿이면 **어느 칸이 누구인지**는 추측이다(아이템 소유자 표시에만 영향).
4. 장부 이벤트(`units.events`, 최근 80건)를 저장만 하고 **되돌리기 UI**는 없다.
5. 상대 보드를 관전하는 동안 vision이 상대 보드를 읽으면 개수가 어긋난다 — vision의 관전 신호(10 §7 요청)가 있으면
   그때는 병합을 건너뛰도록 막을 수 있다. 지금은 신뢰도 하락으로만 나타난다.
