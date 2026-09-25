# 27 qa-validator: 커밋 전 QA 게이트 (f87a2b6 이후 미커밋 작업 전체)

작성일 2026-09-24 / 작성자 qa-validator / 커밋하지 않음
대상 보고: 21(§10·§11 포함), 22, 23, 24, 25, 26, 28. 이전 게이트 시도의 부분 파일은 없었다(처음부터 검증).
환경: `.venv\Scripts\python.exe`, `PYTHONIOENCODING=utf-8`, 사용자 `config/settings.local.toml` 있는 상태 그대로.

## 최종 판정: **조건부 커밋 가능** — 차단 1건(한 줄 수정)만 고치면 커밋해도 된다

- 차단(B1): `src/tft_advisor/vision/board.py:44` 독스트링에 사용자 소환사명이 들어 있다. 저장소는 공개다. `<내 소환사명>`으로 바꿔야 한다(vision-engineer 또는 오케스트레이터, 한 줄). 같은 이름이 있던 테스트 주석과 23 보고서는 내가 가렸다.
- FAIL 1건(F1, 구매 증거 오표기)은 기본 설정에서 **검토 대기 폴더에만** 쌓이고 승인 없이는 이름 인식에 쓰이지 않는다. 그래서 커밋을 막지 않는다. 다만 `unit_purchase_autoapprove=true`를 켜기 전에는 반드시 고쳐야 한다. strict xfail 테스트로 고정했다.

## 요약: PASS 8 / WARN 2 / FAIL 1 (항목 11개)

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | 경계면(계약 ↔ advisor ↔ UI, StageStats API) | PASS (WARN 1) | 아래 §1 | app-integrator / vision-engineer | W1 |
| 2 | 틀린 이름 0 · 대기 미사용 · 자동 승인 없음 · 구매 증거 견고성 | **FAIL**(구매 증거) / 나머지 PASS | 원본 17장 + test.png, 이름 98칸 중 틀림 0. 구매 증거 모호 3경로 재현 | vision-engineer | F1 |
| 3 | advisor(미상 유닛, 보드 배치, rescore_shop, 스테이지 스케줄, test.png 1위) | PASS (WARN) | test.png 1위 적응가 마스터 이(off·mock 모두), 밤의 끝자락 분류 §3 | jev-strategist | W2·W3 |
| 4 | 스테이지 보드 통계 | PASS (WARN) | 수축·top4 없음·파일명 분리·v2→v3 실제 이관·오프라인 | jev-strategist | W4 |
| 5 | 루프(재평가·재설정·힌트·구매 전달) | PASS | loop.py·session.py 코드 + 테스트 13+12 | - | - |
| 6 | 게임 창 찾기 | PASS | 선택/제외·가림·최소화·content_box·따라가기 코드 확인, 실제 데스크톱 읽기 전용 실행 "not_found"(게임 미실행), 쓰기 없음 | - | - |
| 7 | 설정·커밋 대상 | PASS(B1 제외) | settings.toml = HEAD + 문서화된 새 키 4개, 로컬 층 gitignore, 비밀·크롭·원본 없음 | - | B1 |
| 8 | 종료 | PASS | `app/live.py:271-287` done 플래그로 한 번, `loop.close()`에서 세션 저장 | - | - |
| 9 | `--screenshot` 이중 크롭 | PASS | test.png 4480x1440 · 5-5 4480x1440 · live2 1920x1080 모두 content_box 미적용, 정상 판독 | - | W5(부작용) |
| 10 | 시간 | PASS | 인식 첫 장 411ms / 반복 185~227ms(이전 보고 552~576ms), 추천 4.2~5.0ms, rescore_shop 2.0ms | - | - |
| 11 | 전체 pytest | PASS | 1307 passed, 3 skipped, 1 xfailed, 실패 4 = 알려진 Windows 4건. test_boundaries 디코드 오류는 환경 문제로 확인 후 테스트 수정 | - | - |

---

## 1. 경계면

| 경계 | 결과 | 근거 |
|---|---|---|
| `BoardPlan`/`BoardPlanEntry`/`BoardSwap`/`BoardTransition`/`StageBoardHint`, `Recommendation.board_plan` | PASS | `contracts.py:520-600` 전부 선택 필드이고 `extra="forbid"`다. `board_plan.py:314-350`은 생성자로만 만든다(`model_construct` 없음, `src/` 전체 0건). `stage` 1~9, `next_share`는 [0,1]로 자른다 |
| advisor → report/overlay | PASS | `report.board_plan_lines`(report.py:217)를 콘솔(report.py:437)과 오버레이(overlay.py:268-274)가 함께 쓴다. `transition`/`stage_board`는 notes 줄로 나온다(구조 필드를 따로 그리지 않는다) |
| `rescore_shop` ↔ loop | PASS | loop.py `rescore_shop()` 어댑터가 `Advisor.rescore_shop(state, previous)`(engine.py:157)를 부른다. 시그니처가 같다. 결과는 `_on_shop_advice`가 목표 덱을 고정한다 |
| `UnitSlot`/`BoardRead` 새 필드 ↔ recog_view | PASS / **W1** | `board_common`·`missed_board`(board.py:160-162)를 recog_view.py:271-279가 getattr로 읽는다. `corroborated`는 `SlotName`에만 있고 `UnitSlot`에는 없다. 그래서 recog_view.py:41-52의 "(추정)" 판정은 `library`이면서 conf ≤ 0.75인지 보는 대체 규칙으로 동작한다 |
| unit_namer 훅 ↔ loop/session/live | PASS | `set_hints`·`collector.note_purchase`·`reset`·`request_reload`(units.py:779-796)를 loop.py `_feed_unit_namer`/`_do_reset`와 live `unit_review_opener`가 이 이름으로 부른다 |
| StageStats API ↔ `advisor/stage_boards.py` | PASS | 쓰는 멤버를 모두 대조했다: `stage_of`, `unit_stage_stat(star=)`, `boards_for(level=, kind=, min_games=)`, `link`, `cluster_for`, `transitions(min_games=)`, `cluster_board`, `StageTransition.next_cluster/share/games/avg_place`. `stage_of`는 stage를 level보다 먼저 본다(stage_stats.py:195). 2-6 레벨 5가 스테이지 2로 간다 |
| ID | PASS | stage_boards_18.3.json 유닛 65개가 전부 정적 데이터에 있다. comp_links의 comp_id 37개가 전부 현재 통계 덱에 있다 |
| 빈 DB | PASS | `MetaTftStageBoards.from_stats`가 없거나 비었으면 None을 돌려준다(stage_boards.py:88-93). 테스트 `test_stage_boards` 빈 소스 → 결과가 같다 |

## 2. 유닛 이름 · 사진 DB

### 2.1 틀린 이름 0 — PASS
원본 캡처를 모두 돌렸다. `raw/` 17장(live2 2장 포함)과 `test/test.png`다. 디스크 승인 라이브러리 35장으로 3프레임을 봤고, `agree_frames`는 1(스크린샷 모드)과 2(실시간) 둘 다 했다. 스크립트는 스크래치에 있다.
- 이름 붙은 칸 98개: 확인 라벨과 일치 96, 확인 라벨 없음 2(Anvil 벤치 3 오른 — 23 보고의 같은 모델 확인), **틀림 0**.
- live2 2-3: 보드 풀이 3개라 칸 이름이 없다. 벤치 이름도 0(옛 오류였던 아칼리 없음).

### 2.2 대기 사진 기본 미사용 · 자동 승인 없음 — PASS
- `unit_pending_weight` 기본값은 0.0이다(settings.toml, config.py). recognizer.py:175를 거쳐 `UnitLibrary.load(pending_weight=0)`로 가고, 이때 `_pending`/`_trash`는 건너뛴다. `reload`(units.py:796)도 가중치를 유지한다.
- `unit_purchase_autoapprove` 기본값은 false다. 승인 폴더에 직접 쓰는 경로는 `add_pending(approve=...)`의 구매+설정뿐이다.
- 로컬 설정에는 두 키가 없다(기본값 그대로).

### 2.3 구매 증거 — **F1 FAIL** (vision-engineer, `src/tft_advisor/vision/unit_db.py:486-527`)
보고서가 다룬 5종(창 초과, 옮김, 두 챔피언, 보드 변화, 2성)은 막힌다. 아래 3경로는 **다른 유닛 크롭이 산 챔피언 이름으로 저장된다**(재현 스크립트 + strict xfail 테스트 `tests/test_unit_db.py::test_qa27_stale_purchase_never_labels_another_unit`).

| 경로 | 재현 | 원인 |
|---|---|---|
| 상점+장부 이중 보고 뒤 끌어 옮기기 | 상점 칸 비움과 새 벤치 칸이 같은 프레임 → 짝지어 저장·기록 비움 → 루프가 인식 **뒤**에 `note_purchase`(같은 구매) → 3초 안에 벤치 유닛 Y를 두 프레임에 걸쳐 옮김(들어 올림 = gone만, 내려놓음 = added만) → Y가 카르마로 저장 | 짝이 끝난 구매의 장부 메아리가 `_buys`에 남는다. 출처별 최대값 규칙(512-515행)은 두 보고가 **동시에** 창 안에 있을 때만 막는다 |
| 보드 합성 구매 뒤 끌어 옮기기 | 보드의 2기와 합성되어 벤치 새 칸 없음 → 구매가 남음 → Y 옮기기 → Y가 저장 | 짝이 안 된 구매가 창 안에서 다음 "새 칸"을 기다린다 |
| 벤치 판독 깜빡임 | 구매(새 칸 없음) → 한 프레임 벤치 칸 누락 → 다음 프레임 재등장 = "새 칸" | 같음 |

- 영향: 기본 설정에서는 **대기 폴더 오염**만 생긴다. 검토 창이 근거(증거·칸·시각)를 보여 주므로 사람이 거를 수 있다. 자동 승인을 켜면 틀린 표본이 승인 라이브러리로 들어가 틀린 이름이 스스로 강화된다(QA 19 FAIL-1과 같은 종류).
- 수정 제안:
  - (a) 짝을 지으면 같은 챔피언의 늦게 온 다른 출처 보고를 `window_s` 동안 소거한다(예: 짝 기록 `(champ, at)`를 두고, 그 뒤 들어오는 같은 챔피언 보고를 무시).
  - (b) "새 칸"을 인정하려면 창 안에 `gone`(사라진 칸)이 한 번도 없어야 한다. 또는 새 칸 크롭이 최근 사라진 칸 크롭과 닮았으면(색 분포 ≥ 0.9) 옮김으로 본다.
  - (c) 구매와 새 칸의 순서·간격을 확인한다(새 칸이 구매 뒤 1초 안).
  - 고친 뒤 xfail 표시를 지운다.

### 2.4 부작용 — W5
`--screenshot`도 `Recognizer(cfg=settings.vision)`를 쓰므로 `unit_autolearn=true`이면 수집기가 켜진다(screenshot.py:131). 그 결과 `units_screen/_pending/`에 `traits` 5장이 들어갔다(2026-09-23 22:16, test.png 2-6). 이번 게이트의 3회 실행은 중복 제거로 새로 쓴 것이 없다. 크롭은 대기 상태라 해는 없지만, QA 실행이 사용자 DB에 쌓인다. 수정 제안(app-integrator): 스크린샷 모드에서는 `unit_namer.collector = None`으로 끄거나, 증거에 `screenshot` 출처 표시를 붙인다.

## 3. advisor

| 검사 | 결과 | 근거 |
|---|---|---|
| 이름 미상 유닛은 세지 않음 | PASS | unit_status.py `_side`에서 `UNKNOWN_UNIT_ID`와 conf < 임계값은 used에서 뺀다. features.py:179-185는 View.board/bench를 owned에서만 채운다 |
| 보드 배치가 미상 유닛을 벤치로 내리지 않음 | PASS | board_plan.py:239-254에서 후보는 View 유닛뿐이다. `open_slots = slots − board_hidden`이다. 미상 유닛은 lineup·bench·swaps 어디에도 나오지 않는다 |
| rescore_shop: Jev 새 호출 0, 상점만 바뀜 | PASS | `gateway.cached()`는 사전 조회만 한다(jev_client.py:304). `model_copy(update={shop, created_at, latency_ms, debug})`다. 테스트 `test_shop_rescore`가 호출 수를 세는 가짜 백엔드로 `be.calls` 불변을 확인한다. 실측 2.0ms |
| 스테이지 스케줄 | PASS | test.png 2-6 debug `unit_stage`: board_scale 0.22, 실효 가중 item 0.60 / aug 0.33 / board 0.066. `test_unit_stage`: 2-3은 아이템 덱, 5-1은 유닛 덱 |
| CLAUDE.md "최종 덱 = 보유 아이템·증강, 승률 타이브레이커" | PASS | test.png 1위 적응가 마스터 이 렝가(근거: 핵심 아이템 밤의 끝자락 조합 가능, 증강 시너지). off·mock 모두 같다 |
| 1위 여유 | **W2** | 1위 0.62, 2위 검은 가시 워윅 0.61(차 0.01). 입력이 조금만 흔들려도 1·2위가 바뀔 수 있다. 히스테리시스(prev_shown)가 실시간에서는 완충한다 |

### 3.1 "2-6 아이템 추천에 밤의 끝자락이 없다" — 분류: **결함 아님, 점수 설계의 결과(W3, 조정 후보)**
test.png 재료 {B.F. 대검, 쇠사슬 조끼, 곡궁, 거인의 허리띠}에 대한 `debug["item"]`:

| 아이템 | 재료 | bis | st(아이템 통계) | 보유자 | 점수 |
|---|---|---|---|---|---|
| 스테락의 도전 | 대검+허리띠 | 0.986 | 0.859 | 니달리 | 0.967 |
| 거인의 결의 | 조끼+곡궁 | 0.986 | 0.735 | 렝가 | 0.948 |
| 밤의 끝자락 | 대검+조끼 | **1.000** | 0.520 | 마스터 이 | 0.928 |
| 내셔의 이빨 | 허리띠+곡궁 | 0 | 0.5 | - | 0.075 |

- 점수 = 0.85 x bis + 0.15 x st다(Jev off). 밤의 끝자락은 bis 최고지만 통계 항(마스터 이 + 밤의 끝자락 덱 한정 행이 중립 0.52)에서 밀린다.
- 탐욕 선택(scoring.py:634-642)이 스테락(대검)과 결의(조끼)를 먼저 고르면, 밤의 끝자락 재료(대검+조끼)가 남지 않는다.
- 결과 조합(스테락→니달리 + 결의→렝가)은 **둘 다 1위 덱 핵심 아이템**이고 재료 4개를 모두 쓴다(합 1.915 대 밤의 끝자락+내셔 1.003). TFT 상식으로도 명백한 오답은 아니다.
- 표시상 어색한 점 두 가지:
  - 스테락의 bis 0.986은 **2위 워윅 덱**(rel 0.986 x 적합 1.0)에서 나왔다. 보유자·근거 문구는 1위 덱의 니달리다(scoring.py:565-579, 595-597). 1위 덱 기준 적합은 그보다 낮다.
  - 1위 덱 캐리 BIS에 우선 가산이 없다.
- 조정 후보(jev-strategist, 사용자 판단):
  - (a) bis를 보유자 덱(1위) 기준으로 계산
  - (b) 1위 덱 캐리 BIS에 작은 가산
  - (c) 탐욕 대신 재료 조합 전체 합 최대화(이미 합은 현재 선택이 크다)
- 원칙("캐리 BIS 먼저")을 원하면 (b)가 가장 작은 변경이다. Jev live의 `item_pick`이 판단을 보탤 수 있다.

## 4. 스테이지 보드 통계 — PASS (W4)
- 수축: 유닛 신호와 보드 품질 모두 `delta x g/(g+500)`이다(stage_boards.py:102-111). 표본이 50 미만인 성급 행은 전 성급 행으로 대체한다.
- top4: 어디에서도 쓰지 않는다. 쓰는 지표는 avg_place/delta, win_rate, round_win_rate이고, early_convert.py:254에 "not provided"로 적혀 있다.
- 파일명: `stage_boards_18.3.json`은 `metatft_*.json` glob(stats_source.py:60)에 걸리지 않는다. repository.py:550은 `latest_json(..., "stage_boards")`로 따로 찾는다(테스트 고정).
- 스키마 v3 이관(실제 확인):
  1. HEAD `db.py`(v2)로 임시 DB에 18.3 스냅샷을 썼다.
  2. 새 코드로 그 스냅샷을 읽었다(stage 키 없음).
  3. 같은 문서를 다시 쓰면 **같은 id를 돌려준다**(해시 불변 = dedupe 유지).
  4. metatft_early 스냅샷을 쓴 뒤 `schema_version`은 3, stage_boards는 1,471행이다.
  5. 읽기 경로는 `_has_table`로 v2 DB를 막는다.
- 오프라인: 새 테스트 80개를 no-network 가드 플러그인으로 돌렸다. 차단된 연결 0이다.
- **W4 문구 정직성**(jev-strategist):
  - "통계: 2스테이지 1성 평균 등수 −0.11"과 "평균 등수 −0.27"은 **기준선 대비 차이**인데 절대 평균 등수처럼 읽힌다. 바로 다음 줄 "평균 4.40등"은 절대값이다. "기준 대비 −0.11등"으로 바꾸기를 권한다.
  - `next_hint`는 Jaccard > 0이면 "이 보드는 보통 …으로 이어집니다"라고 말한다(stage_stats.py:233-235). test.png는 0.5라 괜찮지만, 유닛 1기만 겹쳐도(0.25 이하) 같은 문장이 나온다. 최소 Jaccard(예 0.4)를 두거나 "비슷한 보드는"으로 문장을 약하게 하기를 권한다.

## 5. 루프 — PASS
- 전투 중 새로고침 → 재평가 1회: `_shop_submitted` 키 비교(loop.py:448-468), `test_same_new_shop_is_rescored_once`.
- 구매로 빈 칸 → 재평가 없음: `cid is not None` 조건, `test_buying_in_combat_does_not_rescore`.
- 새 판 → `namer.reset()`: loop.py:378-383. 수집기 판 ID도 reset된다(units.py:779-784).
- 힌트: `tracker.observe` 직후 `set_hints(owned_champions())`.
- 구매 전달 1회: `last_events`를 observe마다 비운다(session.py:395). 단, 이 전달이 인식 **뒤**에 도착해서 F1의 "장부 메아리" 경로가 생긴다. 고칠 곳은 수집기다.

## 6. 게임 창 찾기 — PASS
- 선택: 제목 정확 일치 {tft, teamfight tactics, league of legends (tm) client} 또는 `RiotWindowClass`. `UnrealWindow` 단독은 제외한다. 브라우저 탭 "TFT - Chrome" 같은 창은 걸리지 않는다.
- 제외: 우리 PID, "tft advisor" 접두사, RCLIENT/"league of legends"/"riot client", 숨김·cloaked 창.
- 순위: 최소화 아님 > 게임 클래스 > 넓이.
- 거절: 최소화, 가림 ≥ 50%(6x4 격자 `WindowFromPoint`, 우리 PID는 가림으로 치지 않음), 너무 작음, 비율 프로파일 없음, 검은 캡처. 예외는 밖으로 내지 않는다(game_window.py:406-515).
- content_box: 모니터 기준 비율을 소수 6자리로 계산하고, 모니터 전체면 None이다. 배율 캡처는 비율로 변환한다(479-482행).
- 따라가기: 3초마다 창 목록을 보고, 바뀌면 0.5초 뒤 같은 위치일 때만 적용한다. 실패한 위치는 30초 억제하고, 최소화 중에는 무시한다(647-677행).
- 실제 데스크톱 읽기 전용 실행(`find_game_window()`만 호출, persist 없음): 창 15개, 게임 제목 후보 0 → `not_found`(게임 미실행). 설정·상태 파일 변화는 없다.

## 7. 설정 · 커밋 대상 — PASS(B1 제외)
- `config/settings.toml`의 HEAD 대비 변경:
  - 머리 주석
  - `[capture] follow_game_window`/`follow_interval_s`
  - `[vision] unit_pending_weight`/`unit_purchase_autoapprove`
  - `unit_autolearn` 설명
  - **값 변경 없음**(모니터·해상도·content_box·jev_backend는 HEAD 기본값 그대로)
- `config/settings.local.toml`(사용자 값: 모니터 1, 1920x1080, content_box, jev live, opacity 0.8)은 `config/*.local.toml`로 무시된다.
- 테스트 독립성: conftest가 `TFT_ADVISOR_LOCAL_SETTINGS=0`과 `TFT_ADVISOR_WINDOW_DETECT=0`을 설정한다. 로컬 파일이 있는 상태에서 전체 스위트가 통과했다.
- `git check-ignore`로 확인한 무시 대상: `data/stats/stats.sqlite`(`*.sqlite`, 기존 정책), `data/raw/`, `_state/`, `config/*.bak`, `data/templates/*/units_screen/`(`_pending` 포함), `tests/fixtures/screens/raw/`.
- 비밀·경로 검사: diff와 새 파일 전체에서 키 패턴, 사용자 경로, 이메일 모두 0건. 소환사명 1건 → **B1**(board.py:44). tests·23 보고서의 같은 이름은 내가 가렸다.
- `tests/fixtures/stats/metatft_early/`(229KB)와 `data/stats/stage_boards_18.3.json`(1.2MB)은 공개 집계 데이터다. 개인 식별자(puuid·summoner·riotId)는 없다.
- `weights.toml`의 `[unit_stage]`·`[board_plan]` 키는 `config.UnitStageWeights`/`BoardPlanWeights`가 읽는다(`extra` 거부 로더 통과, 테스트 `test_toml_schedule_matches_documented_values`).

## 8. 종료 — PASS
트레이, Ctrl+Q, ✕ 손잡이, [앱 종료]는 모두 `OverlayWindow.quit()`(overlay.py:593)를 거쳐 `app.quit()`에 이르고, `aboutToQuit`가 `shutdown()`(live.py:271)을 부른다. `done` 플래그로 한 번만 실행되고, KeyboardInterrupt 경로도 같은 함수를 탄다. 실행 순서는 `stop` → 캡처 스레드 join(3초) → `loop.close()`(추천 스레드 정지·캡처 닫기·세션 저장) → 창 숨김이다.

## 9. `--screenshot`(로컬 설정 있음) — PASS
| 이미지 | 크기 | content_box | 판독 |
|---|---|---|---|
| test.png | 4480x1440 | 미적용(setup.json frame 3440x1440과 다름) | 준비 2-6 레벨 5, 보드 5 이름, 벤치 4 이름 + 미상 5 |
| 5-5 전투 전 | 4480x1440 | 미적용 | 준비 5-5 레벨 9, 유닛 18 이름 미상(특성 풀이 없음 — 정상) |
| live2 2-3 준비 | 1920x1080 | 미적용 | 준비 2-3 레벨 4 골드 17 연패 2, 이름 0(틀린 이름 없음) |

## 10. 시간 — PASS
- 인식: 첫 장 316~462ms, 같은 인식기 반복 185~227ms(목표 < 300ms). 21 보고의 이전 수치는 552~576ms(첫 장)였다.
- 추천: 4.2~5.0ms(이전 3~6ms)
- rescore_shop: 2.0ms
- 보드 배치: 추천 시간 안에 포함된다(변화 없음)

## 11. 전체 pytest — PASS
`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -o addopts="" -q` 결과: **1307 passed, 3 skipped, 1 xfailed, 4 failed**. 실패는 알려진 Windows 4건이다.
- `test_api_key` 힌트
- `test_setup` 권한 상자
- `test_credentials` 0600 두 건

게이트 중 테스트 변경 뒤 해당 파일을 다시 돌렸다: test_unit_db + test_boundaries 33 passed, 3 xfailed / test_vision_units 64 passed.

`test_boundaries::test_config_accepts_design_keys`는 **환경 문제였다**. `PYTHONIOENCODING`이 없으면 자식 프로세스가 cp949로 출력하고, `encoding="utf-8"`로 읽다가 디코드 오류가 나며 returncode 1이 된다. 테스트만 고쳤다(자식에 `PYTHONIOENCODING=utf-8` 전달). 환경변수를 빼고 돌려도 18 passed다.

## 게이트 중 내가 바꾼 것(테스트·문서만)
- `tests/test_boundaries.py`: 자식 프로세스 인코딩 고정
- `tests/test_unit_db.py`: F1 회귀 테스트 3케이스(strict xfail)
- `tests/test_vision_units.py:672`: 소환사명 → `<내 소환사명>`
- `_workspace/23_unit_naming_live.md:11`: 같은 가림

## 담당자별 수정 요청
- **vision-engineer**
  - B1: `src/tft_advisor/vision/board.py:44` 소환사명 가리기(커밋 전, 한 줄)
  - F1: 구매 증거의 남은 구매/장부 메아리/옮김·깜빡임 오표기(§2.3). 고친 뒤 xfail 제거
  - W1: `UnitSlot.corroborated` 전달(recog_view.py:45가 이미 읽을 준비가 되어 있다)
- **app-integrator**
  - W5: 스크린샷 모드에서 사진 수집기 끄기(screenshot.py:131 근처)
  - (선택) 오버레이 [보드 배치]의 "참고:" 4줄이 길다. 좁은 오버레이에서는 "지금 이 스테이지 추천 보드" 한 줄을 우선한다
- **jev-strategist**
  - W3: 아이템 추천의 bis 출처 덱과 보유자 덱 불일치, 캐리 BIS 가산 여부(§3.1, 사용자 판단)
  - W4: "평균 등수 −0.11" → "기준 대비"로 문구 수정, `next_hint` 최소 Jaccard
  - W2: 1·2위 차 0.01(참고)

## 커밋할 파일(정확한 목록)
수정(추적 중):
```
.gitignore
_workspace/02_jev-strategist_design.md
config/settings.toml
config/weights.toml
src/tft_advisor/__main__.py
src/tft_advisor/advisor/candidates.py
src/tft_advisor/advisor/engine.py
src/tft_advisor/advisor/features.py
src/tft_advisor/advisor/jev_state.py
src/tft_advisor/advisor/questions.py
src/tft_advisor/advisor/scoring.py
src/tft_advisor/app/live.py
src/tft_advisor/app/loop.py
src/tft_advisor/app/overlay.py
src/tft_advisor/app/recog_view.py
src/tft_advisor/app/recog_window.py
src/tft_advisor/app/report.py
src/tft_advisor/app/screenshot.py
src/tft_advisor/app/session.py
src/tft_advisor/app/setup.py
src/tft_advisor/app/setup_dialog.py
src/tft_advisor/config.py
src/tft_advisor/contracts.py
src/tft_advisor/stats/__main__.py
src/tft_advisor/stats/collectors/metatft.py
src/tft_advisor/stats/db.py
src/tft_advisor/stats/refresh.py
src/tft_advisor/stats/repository.py
src/tft_advisor/unit_status.py
src/tft_advisor/vision/board.py            (B1 수정 후)
src/tft_advisor/vision/capture.py
src/tft_advisor/vision/recognizer.py
src/tft_advisor/vision/units.py
tests/advisor/test_advisor_units.py
tests/app/test_api_key.py
tests/app/test_jev_toggle.py
tests/app/test_setup.py
tests/conftest.py
tests/fixtures/states/s02_board_ad_items.json
tests/test_boundaries.py
tests/test_credentials.py
tests/test_phase3_final_qa.py
tests/test_vision_units.py
```
새 파일:
```
_workspace/21_board_trust.md
_workspace/22_game_window_detect.md
_workspace/23_unit_naming_live.md
_workspace/24_shop_refresh.md
_workspace/25_unit_image_db.md
_workspace/26_unit_db_wiring.md
_workspace/27_qa_gate.md
_workspace/28_stage_boards_sources.md
data/stats/stage_boards_18.3.json
src/tft_advisor/advisor/board_plan.py
src/tft_advisor/advisor/stage_boards.py
src/tft_advisor/app/game_window.py
src/tft_advisor/app/unit_review.py
src/tft_advisor/stats/collectors/metatft_early.py
src/tft_advisor/stats/early_convert.py
src/tft_advisor/stats/stage_stats.py
src/tft_advisor/vision/unit_db.py
tests/advisor/test_board_plan.py
tests/advisor/test_board_trust.py
tests/advisor/test_shop_rescore.py
tests/advisor/test_stage_boards.py
tests/advisor/test_unit_stage.py
tests/app/test_game_window.py
tests/app/test_quit_controls.py
tests/app/test_screenshot_crop.py
tests/app/test_shop_refresh.py
tests/app/test_unit_db_wiring.py
tests/app/test_unit_review.py
tests/fixtures/stats/metatft_early/   (14파일)
tests/test_stage_stats.py
tests/test_unit_db.py
```
커밋하지 않음(모두 이미 gitignore): `config/settings.local.toml`, `config/settings.toml.bak`, `data/stats/stats.sqlite*`, `data/raw/`, `_state/`, `data/templates/18/units_screen/`(`_pending` 포함), `tests/fixtures/screens/raw/`, `tests/fixtures/screens/test/`.
