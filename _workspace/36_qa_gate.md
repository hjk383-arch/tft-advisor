# 36 qa-validator: 커밋 전 QA 게이트 (d9ba60e 이후 미커밋 작업 전체)

작성일 2026-09-25 / 작성자 qa-validator / 커밋하지 않음
대상 보고: 35(vision: `UnitTracker` 구매 칸·옮기기·판매·합성·성급 None·대조 규칙·loop/unit_merge 연결),
21 §17(advisor: 목표 덱 레벨별 빌드업 기준 보드·★2 상점 규칙·성급 미상·고정 덱 엄격 모드·계약 추가).
환경: `.venv\Scripts\python.exe`, `PYTHONIOENCODING=utf-8`, Jev는 mock만(라이브 호출 0). 스크래치 스크립트는 `-P`.
스크린샷·HUD 재현은 설정 사본(스크래치)으로: 1920x1080 원본(live4)은 `content_box`를 뺀 사본, test.png는 사용자 설정 그대로
(`jev_backend = "live"` 줄은 두 사본 모두 뺐다). 사용자 `config/`는 건드리지 않았다.

## 최종 판정: **조건부 — vision F1·F2를 고친 뒤 커밋 권고** (advisor·app·계약은 커밋 가능)

- 원본 캡처에서는 틀린 이름 0이다(프레임마다 새 인식기 84칸, 성급 배지 128/128). advisor 규칙(§17)은 모두 PASS다.
- **F1(vision-engineer, 차단 권고)**: 추적기의 구매 짝짓기가 적대적 시나리오 4개에서 **틀린 이름**을 낸다.
  1. 같은 프레임에 끌어 옮기기(가장 왼쪽 빈 칸으로)와 구매가 겹칠 때(닮음 >= 0.35면. 실측 다른 챔피언 쌍의 14%)
  2. 전투 중 구매 두 건: 앞 건은 칸에 떨어지고, 뒤 건은 합성된 경우
  3. 공동 선택·구슬 유닛이 온 직후 3초 안에 합성 구매가 있는 경우
  4. 구슬로 ★ 상승 뒤 착지가 아직 안 보인 구매
- **F2(vision-engineer, 차단 권고)**: 준비 단계가 아닌 화면(모루·아이템 선택)에서는 칸 열쇠만 보고 이름을 붙인다.
  한 인식기로 이어서 돌린 스윕에서 Anvil (0,2) **★2**에 live4 (0,2) **★1 피들스틱**이 붙었다. 닮음은 0.19였다.
- 위 5건은 strict xfail로 고정했다(`tests/test_qa36_unit_track.py`). 고치면 xfail 표시를 지운다.
- WARN 6건은 커밋을 막지 않는다.

## 요약: PASS 4 / WARN 1 / FAIL 1 (항목 6개)

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | 틀린 이름 0(스윕 + 적대적 추적 시나리오) | **FAIL** | 스윕: 새 인식기 84칸 틀림 0(agree 1·2). 이어서 45칸 중 틀림 0, 라벨 없음 10(그중 Anvil (0,2)는 F2). 적대적 검사 28개 중 16 PASS, 틀린 이름 11(F1 9: 끌어 옮기기 4 · 전투 합성 1 · 구슬 1 · 공동 선택 3 / W1 2), 비현실 1 | vision-engineer | F1, F2, W1 |
| 2 | 성급(배지 표 · None 끝까지 · `or 1` 없음) | PASS (WARN) | 배지 ★1 111/111 · ★2 17/17 · 틀림 0. None이 vision → unit_merge → GameState → advisor → "★?"로 흐름(새 e2e 테스트). advisor/app에 성급 `or 1` 없음(ledger 2곳은 장부 성급이 늘 있어 해당 없음) | app-integrator | W2 |
| 3 | advisor 규칙(§17) | PASS (WARN) | test_buildup_reference 16 · test_pinned_strict 12 통과. live4: 부족 쉔·바루스·자야 ↔ 상점 [구매] 자야 0.56(가산 +0.3), 모순 없음. 실제 통계 57덱 x 레벨 2~9에서 고정 덱 상점 허용 유닛 ⊇ 기준 보드 — 모순 0 | jev-strategist | W3 |
| 4 | HUD(고정 크기, 실제 Windows 글꼴) | PASS (WARN) | 실제 기본값은 `lines_board = 7`(settings.toml:139, config.py:267 — 6은 `Budgets` 기본값). 7이면 test.png·live4 모두 빌드업·교체·상점에서 구하세요·레벨 6·판매(참고)가 보인다 | app-integrator | W4 |
| 5 | 개인정보 | PASS | 소환사명 15개: 추적 + 커밋 예정 파일에서 0건. raw/·test/·local.toml·*.bak·_state/·logs/·units_screen/ 모두 ignore. 키 패턴 0건 | - | - |
| 6 | 전체 pytest | PASS | 1530 passed · 4 skipped · 6 xfailed(기존 1 + QA36 5) · 4 failed = 알려진 Windows 4건. `tests/test_vision_qa04.py` 단독 17 passed, 전체 실행에서도 통과 | - | - |

---

## 1. 틀린 이름 0

### 1.1 원본 캡처 스윕 — PASS
대상: `raw/` 18장 + `test/test.png`. 이미지마다 3번 인식했다. 수집기는 껐다.

| 방식 | 이름 붙은 칸 | 맞음 | 틀림 | 라벨 없음 |
|---|---|---|---|---|
| 프레임마다 새 인식기, agree 1 | 42 | 35 | **0** | 7 |
| 프레임마다 새 인식기, agree 2 | 42 | 35 | **0** | 7 |
| 한 인식기로 이어서(판 순서, 사이 재설정 없음) | 45 | 35 | **0** | 10 |

- 라벨 없는 7칸은 test.png다. 보드 카시오페아·카밀·엘리스·아칼리·코그모는 `traits`, 벤치 4 아칼리·9 카밀은 `library`에서 왔다.
  21 §17.5의 보드 집합과 같고, 이전 게이트에서도 확인됐다.
- 이어서 돌린 스윕의 라벨 없음 +3칸:
  - live3 2-3 준비 끝 세주아니·피들스틱(`tracked`): 직전 보드와 같은 칸이라 맞다(35 §4).
  - **Anvil (0,2) 피들스틱(`tracked`, ★2)**: F2다. Anvil은 다른 판(5-1)이고 그 칸은 ★2다.
    live4 (0,2)는 ★1 피들스틱이고 닮음은 0.19다. 두 캡처 모두 맵 서명이 `0a080a`라 맵이 바뀌었다고 보지 못했다.
- 실제 기술자 닮음(라벨 붙은 칸 1239쌍, 다른 챔피언끼리):

| 중앙값 | 95% | 최대 | 0.35 이상 | 0.50 이상 | 0.55 이상 |
|---|---|---|---|---|---|
| 0.21 | 0.42 | 0.59 | **14.4%** | 1.1% | 0.3% |

  같은 챔피언끼리(다른 프레임)는 5% 0.37, 중앙값 0.70이다. 35 §1.2의 실측과 맞다.

### 1.2 적대적 추적 시나리오(합성 기술자, `scratchpad/qa36/adv_track.py` → 회귀 테스트 `tests/test_qa36_unit_track.py`)
| 시나리오 | 결과 |
|---|---|
| 벤치 가운데 빈 칸(0·1·3 → 2)에 구매 | PASS(2 = 산 챔피언). 가장 왼쪽 빈 칸이 아닌 4에 생기면 이름 없음 |
| 한 프레임에 구매 둘 + 옮기기 하나(멀리 / 사이에 끼움 / 옮기기 먼저) | PASS(틀림 없음, 애매하면 모름) |
| **끌어 옮기기(가장 왼쪽 빈 칸 1) + 구매(다음 칸 2)가 같은 프레임** | **FAIL — 닮음 0.38·0.45·0.52·0.60 모두 칸 1 = 산 챔피언(`purchase`), 칸 2 = 옮긴 유닛(`tracked`)**. 둘 다 틀림(0.30이면 둘 다 모름) |
| 전투 중 판매 + 같은 칸 구매(닮음 0.45) | PASS(모름) |
| 같은 경우 닮음 0.57·0.62(KEEP_MIN 0.55 이상) | FAIL(옛 이름 유지) — 실측 0.3%라 W1로 분류. 옛 유닛이 ★2, 새 유닛이 ★1이어도 막지 못한다 |
| 차지된 칸에 끌어 놓기(벤치 ↔ 벤치, 보드 ↔ 벤치, 닮음 0.5 두 유닛) | PASS |
| 보드에 사본이 있을 때 합성 구매 → 보드 칸 ★2 | PASS. 같은 프레임에 무관한 유닛도 ★가 오르면 아무것도 정하지 않는다(PASS) |
| **전투 초반 구매 A(칸 2에 떨어짐, 창 만료) + 끝 무렵 C 세 번째(벤치에서 바로 합성)** | **FAIL — 칸 2 = C(`purchase`)** |
| **구슬로 ★ 상승(구매 아님) 뒤 착지가 아직 안 보인 구매** | **FAIL — 그 ★2 칸 = 산 챔피언** |
| 공동 선택 유닛(구매 없음) | PASS(모름). 같은 프레임에 구매가 겹쳐도 PASS |
| **공동 선택 유닛이 온 뒤 3초 안의 구매(착지가 아직 안 보임 / 합성이라 새 칸 없음)** | **FAIL — 공동 선택 유닛 칸 = 산 챔피언** |
| 똑같은 그림(닮음 1.0) 두 유닛 자리 바꾸기 | 판단 불가(합성 기술자의 한계, 제외) |

### F1 — 구매 짝짓기 (vision-engineer, `src/tft_advisor/vision/unit_track.py`)
- **F1a `:213-219`**: 구매가 대기 중이면 가장 왼쪽 빈 칸(`buy_slot`)을 옮기기 후보에서 **빼고** 나머지로 single 매칭(`SINGLE_MIN` 0.35)을 한다.
  그래서 사라진 유닛이 실제로 그 칸에 갔으면(끌어 놓기 → 그다음 구매는 다음 빈 칸) 두 이름이 서로 바뀐다.
  - 제안: `buy_slot`도 닮음 행렬에 넣는다.
  - 사라진 유닛이 `buy_slot`을 가장 닮았거나, 두 칸 닮음 차가 `MATCH_MARGIN`보다 작으면 옮기기도 구매도 짝짓지 않는다(모름).
  - 순번(`rank`)은 옮기기로 찬 칸을 뺀 빈 칸 목록으로 다시 센다.
- **F1b `:313-332` `_pair_buys`**: "창 안의 구매 수 == 순번이 맞는 새 칸 수"만 본다. 두 가지가 빠졌다.
  - 새 칸을 만들지 않는 구매(합성)
  - 구매 **전에** 생긴 새 칸(공동 선택·구슬·짝 못 찾은 옮기기)과 관측 공백(전투·공동 선택) 동안 만료된 구매
  - 제안:
    1. 새 칸은 그 구매가 감지된 프레임(또는 바로 다음 프레임)에 생긴 것만 짝짓는다. `note_purchase`에 프레임 캡처 시각을 넘기면 정확하다.
       지금은 장부 `at` = observe 시점 `time.time()`이다.
    2. 직전 준비 프레임과의 공백이 `BUY_WINDOW_S`보다 길면(전투·공동 선택 뒤 첫 프레임) 구매로 이름을 붙이지 않는다.
    3. 합성 경로(`:333-343`)도 ★ 상승이 구매 시각 이후에 보였을 때만 쓴다(구슬 ★ 상승 차단).
- strict xfail 3개:
  - `test_qa36_drag_to_leftmost_empty_plus_buy_in_same_frame`
  - `test_qa36_combat_early_buy_lands_late_buy_combines`
  - `test_qa36_carousel_unit_then_combining_buy_within_window`

### F2 — 준비 단계가 아닌 화면의 이름 붙이기 (vision-engineer, `unit_track.py:142-167`)
- `_label_only`는 칸 열쇠가 같으면 이름을 붙인다. 성급·그림이 달라도 그렇다. `bench_held`가 아닌 비준비 화면에는 "held" 조건도 없다.
- 모루·아이템 선택 화면에서 유닛을 옮기거나 새 판 재설정을 놓치면 틀린 이름이 된다.
- 맵 검사(`:144-150`)도 비준비 프레임에서는 건너뛴다. 이번 캡처는 맵 서명이 같아 어차피 못 막았다.
- 제안: 기술자가 있는 비준비 프레임에서는 두 경우에 이름을 붙이지 않는다.
  - 배지 성급 < 추적 성급
  - 닮음 < `KEEP_MIN`
- strict xfail: `test_qa36_label_only_frame_does_not_name_a_different_unit_in_the_slot`

### W1 (vision-engineer, `unit_track.py:194-204`)
- 같은 칸 닮음 >= `KEEP_MIN`(0.55)이면 성급이 **내려가도**(★2 → ★1, 같은 유닛이면 불가능) 정체를 유지한다.
- 성급 하락을 "다른 유닛"으로 보는 한 줄 가드를 권한다.
- 관측 공백(전투) 뒤 첫 프레임에서는 `KEEP_MIN`을 0.65쯤으로 올리는 것도 검토한다. 실측 다른 챔피언 최대는 0.59다.
- strict xfail: `test_qa36_same_slot_star_drop_means_a_different_unit`

## 2. 성급
- 배지 판독(35 §2.1 재현): 라벨 대비 ★1→★1 111, ★2→★2 17, 틀림 0.
  이어서 돌린 스윕에서는 ★1 114 · ★2 17 · (모름, 모름) 3(live3 준비 끝)이다.
- None 흐름: 새 테스트 `tests/advisor/test_qa36_star_e2e.py`(통과)가 아래를 확인한다.
  1. 추적 이름 칸 `star=None`을 `apply_board_read`로 넣으면 `GameState` 유닛 star는 None이다(장부 없음 → 1로 지어내지 않음).
  2. mock advisor 보드 배치 항목의 star도 None이다.
  3. `board_plan_lines`에 "★?"가 나오고 "★1"은 없다.
- Jev `star: null`·판매 최소값 "이상"은 기존 `test_pinned_strict`가 확인한다.
- grep 결과: advisor·app 경로에 성급 `or 1`은 없다.
  - 남은 곳은 `app/ledger.py:77,87`(`copies_for_star`·`sell_value`, 장부 유닛이라 성급이 늘 있다)뿐이다.
  - `board_plan.py:428`의 `else 1`은 스테이지 보드 매칭의 "적어도 ★1" 하한이고 의도된 것이다.
  - `sell.py:47`은 최소 판매가이고 "이상"으로 표시한다.
- **W2(app-integrator)**:
  - `app/report.py:124-125 _unit_label`(상태 줄 "보드 N기: …")은 star None을 ★1처럼 아무것도 표시하지 않는다. 인식 확인 창(`recog_view`)은 "★?"를 쓴다.
  - `hud_model.py:66,254` 라인업 아이콘도 None을 표시하지 않는다(21 §17.11 요청과 같음).

## 3. advisor 규칙 — PASS
- 기준 보드: 상위 3개 중 보유 최다, 아래 레벨 폴백, 1기 잡음 보드 제외, follow 끔·덱 없음 = 예전 동작. 테스트 16개가 통과하고 코드를 읽어 확인했다.
  - `board_plan.py:170-188`: `_usable_boards` 정렬 games↓ → avg_place↑, `ref_min_units_gap`
  - 폴백 = 가장 가까운 아래 레벨, 없으면 가장 낮은 레벨
- 배치 순서: `take(ref)`가 먼저, 그다음 `take(all)`이다(구조적). 올리기 순서도 기준 유닛이 먼저다(`ups.sort`).
  미상 가산 0.4 < keep 0.5라 보드 ★1을 벤치 미상 사본과 바꾸지 않는다.
- 부족 목록 ↔ 상점: 두 경로가 같은 보유 집합(`View.units`, 추정 이름 제외)과 같은 `buildup_reference`를 쓴다.
  - live4: "상점에서 구하세요: 쉔 · 바루스 · 자야" ↔ `[구매] 자야 0.56 · 레벨 5 빌드업 부족`.
  - test.png: 부족 유닛이 상점에 없다(자야·레오나·요릭은 카직스 빌드업 밖).
- ★2 규칙: 확인된 ★2만 적용한다. ★3 목표는 "3성 목표 · 보유 N/9"로 예외다. ★3 보유면 "이미 3성 보유"다.
  ★3 완성 경우(★1 2 + ★2 2)도 ★3 목표가 아니면 막는다(문서화된 사용자 규칙 그대로).
- 고정 엄격 모드:
  - 상점은 고정 덱 유닛만 [구매]로 권한다.
  - 아이템은 고정 덱 BIS·핵심만 권하고, 없으면 보관 문구를 낸다.
  - 판매는 대안 덱을 보호하지 않고, 2스테이지 조용함 규칙은 유지한다.
  - 실제 통계 57덱 x 레벨 2~9에서 기준 보드(아래 레벨 폴백 포함) 유닛이 `pinned_units`(L ~ L+2) 밖인 경우는 0이다
    (`scratchpad/qa36/pin_ref.py`). "상점에서 구하세요 X" ↔ "[보류] 고정 덱에 없음 X" 모순은 지금 데이터에서 없다.
- 고정하지 않으면 예전과 같다(`pin_other_rel` 0은 고정 때만 쓴다). fixture 상태 추천도 바뀌지 않았다(전체 테스트 통과).
- **W3(jev-strategist)**: `scoring.py:480`의 상점 부족 가산은 `v.units_known`일 때만 붙는다.
  - 보드 배치는 저신뢰(`relaxed_view`)여도 "상점에서 구하세요"를 낸다.
  - 그래서 저신뢰 프레임에서는 부족 유닛이 가산 없이 [보류]로 남을 수 있다.
  - 가능성이 낮은 경우지만, 같은 relaxed 보유 집합으로 가산하거나 저신뢰일 때는 그 줄을 빼기를 권한다.
  - 기준 보드 선택이 데이터 의존이라는 점(레벨 공백이 생기면 모순 가능)은 통계 갱신 뒤 `pin_ref.py`로 다시 확인할 것.

## 4. HUD(실제 Windows Qt, 460x960, `scratchpad/qa36/hud.py` → `hud_test_lb7.png`, `hud_live4_lb7.png`)
- 기본 `lines_board`는 **7**이다. 아이콘 모드에서는 아이콘 줄 1 + 글자 6줄이고, 기준 줄은 제목 옆에 붙는다. 예산은 줄지 않았다(comps 3 · board 7 · shop 5 · item 4 · augment 4).
- 보이는 줄:

| | 보이는 줄 | 접힘 |
|---|---|---|
| test.png | 레벨 5 빌드업 · 교체 · 상점에서 구하세요 · 레벨 6 · 판매 참고 | "… 외 2줄"(벤치 · 참고) |
| live4 | 레벨 5 빌드업 · 교체 · 상점에서 구하세요 · 레벨 6 · 참고 | 없음 |
| 최악(교체 ↑ + 부족 + 레벨 6 + 판매 3줄 + 벤치 + 참고, 합성 계획) | lines_board 7 → "판매: 요릭★? (+1골드 이상)"까지 | 7 → "… 외 4줄". 6이면 판매 줄이 접힌다. 8이면 "판매 이유"까지 보이고 증강 4→3줄 |

- 권고: 기본 7을 유지한다(설정은 바꾸지 않았다). 판매 이유까지 늘 보이려면 8로 올린다. 그러면 증강이 1줄 줄어든다(960 높이).
- **W4(app-integrator)**: 460px에서는 "레벨 5 빌드업(달빛 아펠리오스 니달리): 알리스타 · 오른 · 쉔 · 바루스 · 자야 — 보유 2/5"가 잘려 **"보유 n/5"가 보이지 않는다**.
  - 덱 이름은 이미 제목 옆 "(기준 …)"에 있다. HUD에서는 `(덱 이름)`을 빼면 된다(`report.py:256-258`의 `who`를 HUD 경로에서 생략).
  - "상점에서 구하세요 … (미확인 유닛 중에 있을 수 있습니다)"도 꼬리가 잘린다. 핵심 유닛 이름은 보인다.

## 5. 개인정보 — PASS
- 캡처에 보이는 소환사명 15개(사용자 1 + 상대 14 — 이 문서에도 적지 않는다)는 추적 파일과 커밋 예정 파일(수정 21 + 새 파일 8)에서 0건이다.
- `raw/live4 준비.*`, `test/test.png`, `config/settings.local.toml`, `config/*.bak`, `_state/`, `logs/`, `data/templates/18/units_screen/`은 모두 `.gitignore`다.
- 예전부터 커밋돼 있던 로컬 경로(`_workspace/01·02·07·11`의 `C:\Users\…`)는 이번 변경과 무관하고 11 게이트에서 확인됐다. 새 파일에는 없다.

## 6. 전체 pytest — PASS
- `python -m pytest -o addopts="" -q`: **1530 passed, 4 skipped, 6 xfailed, 4 failed**.
- 실패 4건은 알려진 Windows 4건이다: `test_api_key` 마스크 힌트, `test_setup` 권한 상자, `test_credentials` 0600 두 건.
- xfail 6 = 기존 1 + QA36 strict 5.
- `tests/test_vision_qa04.py` 단독: 17 passed.

## QA가 더한 파일
- `tests/test_qa36_unit_track.py`: 적대적 추적 시나리오. 통과 6(회귀 고정) + strict xfail 5(F1 x3, F2, W1).
- `tests/advisor/test_qa36_star_e2e.py`: 성급 미상 끝까지 흐름(통과).
- 스크래치(커밋 안 함): `scratchpad/qa36/{sweep,adv_track,anvil,pin_ref,hud,hud_worst}.py`, HUD PNG.

## 커밋 목록(권고)
F1·F2를 고친 뒤 한 번에 커밋하거나, 아래 두 커밋으로 나눈다. 1은 지금 커밋 가능하다.

1. **advisor·계약·app 표시**(jev-strategist 21 §17 + 성급 None 소비 쪽)
   - `src/tft_advisor/advisor/{board_plan,candidates,engine,features,jev_state,scoring,sell}.py`
   - `src/tft_advisor/contracts.py`, `src/tft_advisor/config.py`, `config/weights.toml`
   - `src/tft_advisor/app/report.py`
   - `tests/advisor/{test_buildup_reference,test_pinned_strict,test_advisor_units,test_stage_boards,test_unit_stage,test_qa36_star_e2e}.py`
   - `_workspace/21_board_trust.md`
2. **vision 유닛 정체 추적**(35) — F1·F2 수정 후 xfail을 지우고
   - `src/tft_advisor/vision/{unit_track,recognizer,units,bench_memory}.py`
   - `src/tft_advisor/app/{loop,unit_merge}.py`
   - `tests/{test_unit_track,test_qa36_unit_track,test_bench_memory}.py`
   - `_workspace/35_unit_tracking.md`, `_workspace/36_qa_gate.md`
- 1만 먼저 커밋할 경우의 주의:
  - `unit_merge.py`(성급 None)를 2에 두면 1의 advisor는 None을 받지 않을 뿐이다(호환).
  - `bench_memory.py`의 None은 `unit_merge`와 무관하게 GameState로 간다. 그래도 1의 advisor가 None을 처리하므로 안전하다.
- `_workspace/34_lolchess_source.md`(추적 안 됨)는 이번 범위 밖이다. 개인정보 검사는 통과했다.
