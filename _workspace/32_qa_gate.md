# 32 qa-validator: 커밋 전 QA 게이트 (e5ad936 이후 미커밋 작업 전체)

작성일 2026-09-25 / 작성자 qa-validator / 커밋하지 않음
대상 보고: 21 §14(판매·보드 배치 유지·고정 덱 API), 30(벤치 기억·자리 뒤바뀜·구매 뒤 다시 읽기·장부), 31(고정 덱 UI·장부 칸 배치·오버레이).
환경: `.venv\Scripts\python.exe`, `PYTHONIOENCODING=utf-8`, 스크래치 스크립트는 `-P`로 실행. 사용자 `config/settings.local.toml` 그대로
(LiveLoop 재현에서는 1920x1080 원본을 쓰려고 설정 사본의 `content_box`만 None으로 바꿨다 — 파일은 건드리지 않았다).

## 최종 판정: **조건부 커밋 가능** — F1(한 줄)을 고치고 커밋하기를 권한다

- **틀린 이름 0**: 원본 전체 스윕, 벤치 기억 순서, 장부를 넣은 LiveLoop 재현 모두에서 틀린 이름·틀린 칸의 장부 이름이 없다.
  live3의 "벤치 2 카밀(장부)" 문제는 재현되지 않는다.
- **F1(app-integrator, 차단 권고)**: vision 특성 풀이 집합(자리 미상) 유닛이 **정렬 순서로 짝지은 칸의 성급**을 받는다.
  live3 2-2에서 **바루스 ★2**가 되고(실제 ★2는 아칼리), 이 값이 advisor에 들어가 보드 배치가 "바루스★2(2성 · …)"를 추천한다.
  HEAD부터 있던 버그지만, 이번 diff가 그 줄을 고치면서 주석에는 "성급이 모두 같을 때만"이라고 적었고 코드는 그렇게 하지 않는다.
  수정은 한 줄이다(아래 F1). strict xfail 테스트로 고정했다.
- **F2(app-integrator, 비차단)**: 인식 확인 창이 vision 집합 유닛 4기와 같은 칸의 "이름 미상" 4행을 함께 그려
  **"[보드] 8기"**로 보인다(실제 4기). 31 §9의 `board_units`에서 생긴 표시 회귀다. advisor 입력에는 영향이 없다. strict xfail로 고정했다.
- 나머지 항목은 PASS다. WARN 7건은 커밋을 막지 않는다.

## 요약: PASS 5 / WARN 1 / FAIL 1 (항목 7개)

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | 틀린 이름 0(스윕·벤치 기억·확인 창·LiveLoop+장부) | **FAIL**(F1 성급, F2 표시 수) / 이름은 PASS | 이름 98칸 중 틀림 0(agree 1·2). 장부 3가지 x agree 2 x 5프레임에서 틀린 칸의 장부 이름 0 | app-integrator | F1, F2 |
| 2 | BenchMemory | PASS (WARN) | 순서 재현 표(§2). 빈 칸을 유닛으로 본 경우 1칸(문서화된 한계, 이름 없음). 전략가·새 판·맵 변경·이름 유지 규칙 테스트 추가 | vision-engineer | W1, W2, W3, W6 |
| 3 | advisor(판매·보드 배치 유지·고정 덱) | PASS (WARN) | test_sell 17 · test_pinned_comp 9. 자리 미상 장부 유닛 판매 = 칸 번호 None(테스트 추가) | jev-strategist | W4 |
| 4 | 루프·UI(고정·재시작·강제 재판독·포커스·Qt) | PASS | 강제 재판독 1회만(5초 50스텝), 고정 요청과 공존(테스트 2개 추가). 띠 창 `Tool + WindowDoesNotAcceptFocus + WA_ShowWithoutActivating` | - | - |
| 5 | 장부(기준 옮기기·경험치 구매) | PASS (WARN) | 합성 A/B: 애매 168→2, 279→2, 132→0. 보관 세션은 프레임이 없어 재생 불가 | - | W5 |
| 6 | 개인정보·커밋 대상 | PASS | 소환사명 7개 + 사용자 이름: 추적·커밋 예정 파일 0건. raw/·local.toml·_state/ 모두 ignore. 경로·키 0건 | - | - |
| 7 | 전체 pytest | PASS | 1411 passed, 3 skipped, 4 xfailed, 4 failed = 알려진 Windows 4건 | - | - |

---

## 1. 틀린 이름 0

### 1.1 원본 캡처 전체 스윕 — PASS
대상은 `raw/` 17장(live3 2-2 준비·2-3 준비 끝 포함)과 `test/test.png`다. 이미지마다 새 인식기를 쓰고 3번 인식했다.
`agree_frames`는 1과 2(기본)로 돌렸다. 수집기와 저장은 껐다.
- 이름 붙은 칸 98개: 확인 라벨과 일치 96, 확인 라벨 없음 2(Anvil 벤치 3 오른, 27과 같음), **틀림 0**. agree 1·2 결과가 같다.
- live3 2-2: 칸 이름 0(아칼리/바루스는 `_drop_contradicted`로 모름 처리). 집합 {아칼리, 세주아니, 바루스, 피들스틱}은 자리 미상으로 나오고 정답과 같다.
- live3 2-3 준비 끝(한 장만): 벤치 0, 이름 0.

### 1.2 `--screenshot … --test-view --no-overlay --no-jev` — 이름 PASS / F1·F2
live3 2-2, live3 2-3 준비 끝, live2 2-3 준비를 돌렸다. 틀린 칸의 이름은 없다. 다만 live3 2-2에서 두 가지가 보인다.
```
보드 4기: 아칼리 (자리 미상) · 세주아니 (자리 미상) · 바루스 2성 (자리 미상) · 피들스틱 (자리 미상)   ← F1
보드: 바루스★2(2성 · 추천 스테이지 보드) · …                                                  ← F1이 advisor까지 간다
[보드] 8기 — 이름 미상 4기   (칸 4행 + 자리 미상 4행)                                           ← F2
```
21 §14.4의 전/후 기록에 적힌 "바루스★2·피들스틱·아칼리·세주아니"도 같은 증상이다.

### 1.3 LiveLoop + 가짜 장부 — 이름 PASS
실제 `Recognizer`, `SessionTracker`(임시 경로), `advisor off`, `InlineAdviceRunner`로 돌렸다.
프레임은 live3 2-2 x3 → live3 2-3 준비 끝 x2이고, 장부 3가지와 agree 1·2를 조합했다.
- A: live3 실제 장부(쉔·카밀·라칸·아칼리x3·세주아니·바루스x2·피들스틱x2)
- B: A + 오른
- C: 벤치 후보를 줄인 장부

결과:
- 장부 이름이 칸에 붙은 경우는 한 번뿐이다. 준비 끝 전환 프레임에서 ★2 규칙으로 (3,0)에 **아칼리 ★2**가 붙었고, 정답이다.
- 나머지 장부 유닛은 모두 "장부 보유(자리 미상): …" 한 줄로 나온다. 벤치 칸은 전부 "이름 미상"이다.
- 강제 재판독은 판마다 정확히 1회다.
- 참고: 2-2 → 2-3 사이 프레임을 건너뛰었기 때문에 장부가 상점 3칸 비움을 구매로 읽었다(심술두꺼비 등). 실제 연속 프레임에서는 생기지 않는 인위적 현상이라 판정에서 뺐다.

### F1 — `src/tft_advisor/app/unit_merge.py:345` (app-integrator, 한 줄)
```python
star = body.star if body.source != "vision" else slot.star      # 지금
star = body.star                                                  # 제안
```
vision 집합 `Body`는 이미 올바른 성급을 들고 있다(323-331행). 이름 없는 보드 칸 성급이 모두 같으면 그 성급이고,
아니면 장부 사본(`match.star`) 또는 1이다. 제안대로 메모리에서 고쳐 돌리면 아칼리 ★2 · 바루스 ★1이 나온다(장부 없으면 전부 ★1).
고친 뒤 `tests/app/test_ledger_placement.py::test_qa32_vision_unplaced_set_does_not_take_the_star_of_an_arbitrary_slot`의 xfail 표시를 지운다.

### F2 — `src/tft_advisor/app/recog_view.py:364-380` `board_units` (app-integrator)
판독에 `unplaced`(vision 집합)가 있으면, 집합 유닛(hex None)을 그대로 두면서 판독의 보드 칸을 모두 "이름 미상"으로 **더한다**. 그래서 수가 두 배가 된다.
- 제안: vision 집합 유닛 수만큼은 이름 없는 판독 칸을 더하지 않는다.
- 또는: 칸 행(성급·아이템이 보여 유용하다)을 그리고 집합은 `board_common_note`처럼 한 줄로 보인다.
- 고친 뒤 `test_qa32_recognition_window_does_not_double_count_vision_unplaced_board`의 xfail 표시를 지운다.

## 2. BenchMemory

한 인식기로 순서대로 돌렸다(agree 1·2 결과 동일). 정답 = `live3 2-3 준비 끝.expected.json` 벤치 {0,1,2,3,4,7}.

| 순서 | 준비 끝 프레임 벤치 | 빈 칸을 유닛으로 봄 | 놓침 | 이름 |
|---|---|---|---|---|
| live2 → live3 2-2 → 2-3 끝 | {0,1,2,3,4,7} | 0 | 0 | 0(이름 붙은 칸 없음) |
| live3 2-2 → 2-3 끝 | {0,…,5,7} | **{5}**(W2, 30 §2 한계) | 0 | 0 |
| live3 2-2 x3 → 2-3 끝 x3 | 위와 같음, 프레임마다 안정 | {5} | 0 | 0 |
| live2 → 2-3 끝(2-2 건너뜀) | {0,1,2,3,4,7} | 0 | 0 | 0 — 칸 4가 live2의 **★2**를 이어 씀(W1) |
| 2-3 끝 → 2-2(역순) | 2-3 끝: 벤치 0(기억 없음) | 0 | - | 0 |
| 옛 돌 맵 2-6 → 2-3 끝(맵 변경) | 벤치 0, `bench_held` False | 0 | 6(의도: 기준을 쓰지 않음) | 0 |

추가 테스트(`tests/test_bench_memory.py`):
- 전략가 막대 칸은 빈 칸 기준으로 배우지 않고, 체력바 없는 프레임에서도 유닛으로 세지 않는다 — PASS
- 그림(모양)이 바뀐 칸은 직전 이름을 쓰지 않는다 · `reset()` 뒤 기억 없음 — PASS
- 그림이 바뀐 "기준 없음" 칸이 직전 성급·아이템을 이어 쓴다 — strict xfail(W1)

루프 새 판: `loop.py` `_do_reset`이 `bench_memory.reset()`, 예약·비교값 초기화를 부른다 — PASS.

## 3. advisor

| 검사 | 결과 | 근거 |
|---|---|---|
| 이름 미상·저신뢰 유닛 판매 안 함 | PASS | `View`에 없어서 후보가 되지 않는다(sell.py:108-109). 저신뢰/직전 계획이면 판매를 붙이지 않는다(sell.py:93) |
| 자리 미상 장부 유닛(bench_slot None) 판매 | PASS | `SellAdvice.bench_slot/hex` = 유닛 값 그대로(None). 칸 번호를 지어내지 않는다. 표시(`report.sell_lines`)는 칸 번호를 쓰지 않는다. 테스트 `test_qa32_unplaced_ledger_unit_sale_never_points_at_a_slot` 추가 |
| 초반 쌍 유지 · 이자 계산 · 판매가 = 장부 규칙 | PASS | `interest = min(5, gold//10)`, 문구 경계(28+2 → "30골드 → 이자 +1"), `unit_sell_value` = `ledger.sell_value` 전 코스트·성급(기존 테스트) |
| 보드 배치: 신뢰도 하락 시 유지(문구) · 캐러셀/증강 직전 계획 · reset에서만 지움 | PASS | `relaxed_view` + `LOW_TRUST_NOTE`, `_previous_plan`/`stale_copy`, `reset()`이 새 `Session` |
| 고정 덱: 스테이지 넘어 1위 · 해제 복원 · reset 해제 · 캐시 키 · Jev 추가 호출 없음 · rescore_shop | PASS | test_pinned_comp 9개(가짜 백엔드 호출 수: 고정 +1(새 state), 해제 = 캐시, 전투 중 고정 = 0) |

## 4. 루프·UI
- 고정 클릭: `request_pin` → 세션 기록, `runner.set_pin`, `runner.submit(readvise_state(last_state))`. 캡처 없음. advisor 반영은 추천 스레드의 `_PinSync.sync`에서 한다(loop.py:236, 테스트가 스레드 이름으로 확인) — PASS
- 재시작: `_restore_pin`과 `make_pin(pinned=…)`, 새 판 `_send_pin(None)`과 `tracker.reset` — PASS(테스트)
- 강제 재판독: `last_events`는 `observe`마다 비워진다(session.py:408). 신호가 둘(상점 칸 비움 + 벤치 수 변화)이어도 예약은 하나이고, 같은 판독이면 다시 예약하지 않는다.
  5초 동안 1회만 실행된다(테스트 추가). 고정 요청은 예약을 소비하지도 겹치지도 않는다(테스트 추가) — PASS
- 띠 창: `FramelessWindowHint | Tool | WindowDoesNotAcceptFocus`, `WA_ShowWithoutActivating`, `NoFocus`, 항상 위는 `apply_always_on_top`(overlay.py:469). 종료 손잡이와 같다 — PASS(실제 게임 위 확인은 사용자 몫)
- Qt offscreen 테스트(띠 버튼, 오버레이 머리·메뉴·인식 확인 창 공유) — PASS

## 5. 장부
- 보관 세션 `_state/sessions/session_20260925_182720_762740.json`(읽기만 함)은 `ambiguous` 352건과 **마지막 80개 이벤트**만 저장한다(애매 73, 같은 값 연속 반복 29). 프레임 관측이 없어 재생할 수 없다(W5).
- 대신 HEAD `ledger.py`와 현재 `ledger.py`에 같은 합성 흐름을 넣어 비교했다. 흐름은 10판 x 18라운드이고 구매·리롤·경험치 구매·수입이 들어 있다.

| 흐름 | HEAD 애매 | 현재 애매 | HEAD 경험치 구매 | 현재 경험치 구매 |
|---|---|---|---|---|
| 막대 먼저 50% | 168 | 2 | 60 | 100 |
| 막대 먼저 100% | 279 | 2 | 32 | 100 |
| 골드 먼저 100% | 9 | 2 | 99 | 100 |
| + 1프레임 골드 오독 | 132 | 0 | 62 | 94 |

- 스테이지 전환 뒤 늦게 오른 경험치(+2)와 4코스트 구매가 겹쳐도 경험치 구매로 오인하지 않았다(구매 1, 애매 0).
- 테스트 `test_xp_bar_read_before_gold_is_still_an_xp_buy`, `test_unexplained_change_is_reported_once_and_rebased` 통과.

## 6. 개인정보 · 커밋 대상 — PASS
- 추적 파일과 커밋 예정 새 파일 전체에서 소환사명 7개와 사용자 이름을 대소문자 무시로 찾았다: 0건. 30·31 보고와 테스트에도 없다.
- `tests/fixtures/screens/raw/`(원본·expected.json, 다른 플레이어 이름 포함), `config/*.local.toml`, `_state/`는 모두 gitignore다.
- 절대 경로·사용자 이름 경로·API 키 모양: 0건.
- `config/weights.toml` 변경은 `[comp] pin_other_rel`, `[sell]` 표이고, `config.py` `SellWeights`/`CompWeights`와 키 이름이 같다.
- raw/에 깨진 이름 복사본(30 §6-4)이 있으나 ignore라 커밋에 영향이 없다.

## 7. 전체 pytest — PASS
`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -o addopts="" -q` → **1411 passed, 3 skipped, 4 xfailed, 4 failed**.
실패는 알려진 Windows 4건(api_key 힌트, setup 권한 상자, credentials 0600 두 건)뿐이다.
xfail 4건:
- 27의 구매 증거 1건
- 이번에 추가한 strict 3건: F1, F2, W1

---

## WARN

| # | 내용 | 위치 | 담당 | 제안 |
|---|---|---|---|---|
| W1 | "기준 없음/애매"(규칙 4) 칸은 그림이 직전 유닛과 달라도 **성급·아이템**을 이어 쓴다(이름만 뺀다). live2 → 2-3 끝에서 칸 4가 옛 ★2를 받았다. 장착 아이템이 다른 유닛 칸에 붙을 수 있다 | `vision/bench_memory.py:221-223` | vision-engineer | 그림이 `SAME_MAX`를 넘으면 규칙 3처럼 `star=None, items=(), item_count=0`. strict xfail 테스트가 있다 |
| W2 | 앱을 판 중간에 켜 빈 칸 기준이 없으면, 떠난 칸(live3 칸 5)이 이름 없는 유닛으로 남는다(30 §2 한계와 같다) | bench_memory.py 규칙 4 | vision-engineer | 다음 바 보이는 프레임에서 고쳐진다. 그대로 둬도 된다 |
| W3 | "같은 그림" 판정이 경계만 본다(색 무시). 모양이 같고 색만 다른 합성 유닛은 0.024/0.027로 **같은 유닛**(이름 유지)이 됐다. 실측 다른 유닛은 0.086 이상이라 여유는 있다 | bench_memory.py:206-207 | vision-engineer | 색 히스토그램 거리를 보조 조건으로 더하는 것을 검토 |
| W4 | 자리 미상 장부 유닛의 보드/벤치 쪽은 추정이다(31 §9). 판매 근거의 "보드에서 빼도 되는 유닛 ·" 앞말이 틀릴 수 있다(칸 번호는 없음) | `advisor/sell.py:188-189` | jev-strategist | `hex`와 `bench_slot`이 모두 None이면 앞말을 빼기 |
| W5 | 보관 세션에 프레임 기록이 없어 장부 재생 비교가 불가능하다. 합성 A/B로 대신했다 | app/session.py 보관 형식 | app-integrator(선택) | 디버그 설정일 때만 FrameObs 요약을 링 버퍼로 보관하면 다음 QA에서 재생할 수 있다 |
| W6 | 벤치 기억이 발동한 전환 프레임은 `unplaced=()`로 vision 집합을 버린다. 그 프레임에서는 보드 이름이 장부 규칙(★2 확정)만 남는다. 틀린 이름은 아니다 | bench_memory.py:230 | vision-engineer | 직전 판독의 `unplaced`도 이어 쓰기 |
| W7 | 장부 ★s 규칙은 vision 성급 판독을 믿는다. ★1을 ★2로 잘못 읽으면 "사본 3개 이상인 유일한 챔피언"이 그 칸에 붙는다 | `app/unit_merge.py:258-270` | app-integrator | `star_conf`가 낮은 칸은 ★s 규칙에서 빼기 |

기타: `recog_view.py:349` 한 줄 안에 긴 공백이 들어간 조건식이 있다(동작은 맞다, 모양만).

## 이번 게이트에서 고친 것 / 더한 것(테스트만, 제품 코드 수정 없음)
- `tests/advisor/test_sell.py`: `test_qa32_unplaced_ledger_unit_sale_never_points_at_a_slot`
- `tests/app/test_forced_reread.py`: `test_qa32_forced_reread_happens_once_and_does_not_loop`, `test_qa32_pin_request_does_not_consume_or_duplicate_forced_reread`
- `tests/test_bench_memory.py`: `test_qa32_tactician_is_never_a_bench_unit_nor_an_empty_reference`, `test_qa32_changed_picture_never_keeps_the_name_and_reset_forgets`, strict xfail `test_qa32_changed_picture_without_empty_reference_drops_star_and_items`(W1)
- `tests/app/test_ledger_placement.py`: strict xfail `test_qa32_vision_unplaced_set_does_not_take_the_star_of_an_arbitrary_slot`(F1), `test_qa32_recognition_window_does_not_double_count_vision_unplaced_board`(F2)
- 문서의 이름 가리기: 필요 없었다(0건).

## 커밋 목록(제안, 커밋하지 않음)
`loop.py`·`contracts.py`는 두 기능(강제 재판독/고정, 판매/고정)이 한 파일에 섞여 있다. 비대화형 환경이라 hunk 단위로 나누기 어려우므로 3개로 제안한다.
F1을 먼저 고치면(한 줄, xfail 제거) 2번에 함께 넣는다.

1. **vision: 벤치 체력바 없는 프레임 기억, 자리 뒤바뀜 모름 처리, 상점 확률로 특성 풀이 확정 + 장부 기준 옮기기·경험치 구매**
   - `src/tft_advisor/vision/{bench_memory.py(새), board.py, units.py, recognizer.py, evaluate.py}`
   - `src/tft_advisor/app/ledger.py`
   - `tests/test_bench_memory.py(새), tests/test_vision_units.py, tests/test_vision_board.py, tests/app/test_unit_ledger.py`
2. **판매 추천·보드 배치 유지·목표 덱 고정(advisor API + 오버레이/인식 확인 창/트레이 UI), 구매 뒤 다시 읽기, 장부 이름은 유일할 때만 칸에**
   - `src/tft_advisor/advisor/{sell.py(새), board_plan.py, engine.py, scoring.py}`, `src/tft_advisor/{contracts.py, config.py}`, `config/weights.toml`
   - `src/tft_advisor/app/{pinning.py(새), deck_chooser.py(새), loop.py, live.py, overlay.py, recog_view.py, recog_window.py, report.py, session.py, unit_merge.py}`
   - `tests/advisor/{test_sell.py, test_pinned_comp.py}(새), tests/app/{test_pin_target_deck.py, test_forced_reread.py, test_ledger_placement.py}(새)`
3. **문서**: `_workspace/21_board_trust.md`(§14), `_workspace/30_live3_fixes.md`, `_workspace/31_pin_target_deck.md`, `_workspace/32_qa_gate.md`

## 재현 스크립트(세션 스크래치, 저장소 밖)
`qa32_sweep.py`(스윕), `qa32_seq.py`(벤치 기억 순서), `qa32_loop.py`(LiveLoop + 가짜 장부 + 인식 확인 줄),
`qa32_ledger.py`·`qa32_ledger2.py`(HEAD vs 현재 장부 A/B). `-P`로 실행한다.
