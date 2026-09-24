# 19 QA: 보드·벤치 챔피언 이름 식별 검증

작성일: 2026-09-23 / 작성자: qa-validator / 대상: 커밋 전 변경(`vision/units.py` 신규 외 13개 파일) / 커밋하지 않음

## 요약: PASS 3 / WARN 2 / FAIL 1

| # | 항목 | 결과 | 근거 | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | 경계면(contracts ↔ vision ↔ unit_merge ↔ features ↔ report) · ID | **PASS** | 아래 §1 | - | - |
| 2 | 정확도 정직성 · 누수 | **WARN** | 라벨은 독립적으로 확인됨(엔지니어가 맞다). 테스트 1건이 순환 채점이었다 → QA가 고침. "남겨 둔" 평가도 같은 판·같은 자리·같은 스킨이라 낙관적임 | vision-engineer | §2 |
| 3 | 오답 0 정책 공격 | **FAIL** | 특성 없는 체력바 유닛(훈련 봇·골렘 등)이 `forced`로 챔피언 이름을 받고 **디스크에 저장된다** | vision-engineer | §3 FAIL-1 |
| 4 | 안전(gitignore · 디스크 자동 학습) | **PASS**(FAIL-1은 예외) | §4 | - | - |
| 5 | 회귀(전체 pytest, HEAD worktree 기준선과 비교) | **PASS** | 새 실패 0. 기준선과 같은 실패 | - | §5 (환경 참고) |
| 6 | 시간 | **WARN** | `recognize()` 350~460ms (이름 끔 335~355ms). 300ms 목표는 이번 변경 전부터 넘었다(4480 캡처의 모니터 탐지 포함) | vision-engineer | §6 |

---

## 1. 경계면 — PASS

- `UnitSlot.unit_id/unit_conf/name_source`, `BoardRead.board_set/unplaced/trait_solutions` (`vision/board.py:124-156`) → `UnitNamer.name` (`vision/units.py:584-604`)이 `dataclasses.replace`로 채운다 → `unit_merge._slot_obs`가 `unit_id`/`unit_conf`를, `board_obs_from`이 `unplaced`를 읽는다(`app/unit_merge.py:117-157`). 이름이 모두 같다.
- `GameState.board/bench`는 `UnitOnBoard(ChampionId, hex|None, bench_slot|None)`이다. 자리 미상 유닛(`hex=None`)은 이전 장부 경로가 이미 만들던 모양이라 advisor·report 모두 처리한다(`report.units_lines`가 "(자리 미상)" 출력).
- `advisor/features.py:169`는 `board`와 `bench` 신뢰도가 **둘 다** 0.6 이상이어야 유닛을 쓴다. `state_with_units`가 보드·벤치 신뢰도를 따로 넣으므로(`unit_merge.py:434-436`) test.png는 보드 0.85 / 벤치 0.38 → 추천에 쓰이지 않는다. 계약과 일치한다(정책 결정은 엔지니어 보고 §8-1, 오케스트레이터/jev-strategist 몫).
- `FieldSource.VISION`(contracts.py:108) ↔ `unit_status._vision_named` ↔ 문구("화면 인식 9기 · 이름 미상 5기") 일치.
- **ID**: 이름 후보는 `champions.json` apiName에서만 나온다(`TraitTable.from_static`). 디스크 라이브러리 폴더 8개 전부 `static.get("champions", ...)`로 확인되는 apiName(`DA_18_Camille`, `DA_KogMaw18_AD` 등). 로더도 잘못된 폴더를 건너뛴다(`units.py:177`). 라벨 `name`은 `fixtures._resolve`로 apiName으로 바뀐다. 회귀 테스트 `test_qa_disk_library_folders_are_canonical_champion_ids` 추가.
- 참고: `champions.json`에 특성 없는 유닛이 17개 있다(훈련 봇, 골렘, 모루 4종, 협곡의 전령 등). 풀이 후보에서 빠지는 것은 맞지만, 이것 때문에 FAIL-1이 생긴다(§3).

## 2. 정확도 · 누수 — WARN

### 2.1 수치(재현)
- `python -m tft_advisor --screenshot tests/fixtures/screens/test/test.png --no-jev` (`PYTHONIOENCODING=utf-8` 필요, §5): 보드 `아칼리 (0,0) · 카밀 (0,1) · 엘리스 (0,2) · 코그모 (2,0) · 카시오페아 (3,0)`, 벤치 `3 자야 · 4 아칼리 · 8 자야 · 9 카밀`, 나머지 5칸 이름 미상. 신뢰도 보드 0.85 / 벤치 0.38. 보고서와 같다.
- `python -m tft_advisor.vision.evaluate tests/fixtures/screens/raw`: board_name 22/22, bench_name 17/17, wrong 0. 확인하지 않은 칸에 이름을 낸 것 1건(Anvil 벤치 4 = 오른).
- 보드 집합 = {아칼리, 엘리스, 카밀, 코그모, 카시오페아}. 사용자 정답과 같다.

### 2.2 카밀/엘리스 자리 — 엔지니어가 맞다(독립 확인)
- 2-2 특성 패널 크롭을 눈으로 확인: 속사포 1 · 약탈자 1 · 엄호대 1 · 지옥불 1 · 나무정령 1 · 악의 여단 1, **선봉대 없음**. 정적 데이터에서 엘리스 = 악의 여단+선봉대, 카밀 = 악의 여단+약탈자. 풀이는 {카밀, 오른, 바루스} 하나뿐이다.
- 2-2 보드 크롭: (0,1) 뿔 달린 어두운 여자 모델, (0,2) 초록 나무 거인(오른), (3,0) 주황·검정 궁수(바루스). 그러므로 뿔 달린 여자 = 카밀.
- 2-6 크롭: (0,1)이 같은 뿔 달린 여자 모델이고, (0,2)는 거미 다리 모델(엘리스)이다. 색 기술자 닮음도 같은 결론이다. 2-2(0,1)↔2-6(0,1) 0.84, ↔2-6(0,2) 0.53. 2-5(0,2)↔2-6(0,2) 0.77.
- 벤치 9: 2-6(0,1)과 0.75, (0,2)와 0.47이고 눈으로 봐도 같은 모델이다 → **카밀**. 사용자 추정(엘리스)은 틀렸다.
- 벤치 3·8 = 자야: 2-6 상점의 자야 카드에 ★★ 표시가 있고, 카드 그림(초록 머리·잎 뿔)이 3·8번 모델과 같다. 2-2 패널의 나무정령 1은 오른 몫이다 → 자야는 벤치에만 있다. 확인.
- Anvil 벤치 4 = 오른: 크롭이 2-2의 오른 모델(초록 나무 거인)과 같다. 맞을 가능성이 높다(라벨 없음).
- 결론: 라벨은 알고리즘 출력을 정답으로 되쓴 것이 아니다. 특성 패널과 눈으로 한 모델 대조에서 나왔다. 여기에는 순환이 없다.

### 2.3 누수 · 순환
- test.png와 `raw/2-6 전투 전.png`는 md5가 같다(`7b19e4e5…`). 엔지니어가 밝힌 대로다.
- **순환 채점(QA가 고침)**: `tests/test_vision_units.py`의 `heldout` fixture가 모듈 범위였다. `UnitNamer._learn`(`units.py:606-619`)이 `test_raw_heldout_board_and_bench_names`에서 인식한 2-6 크롭을 메모리 표본으로 더한다. 그래서 뒤의 `test_raw_screenshot_state_has_named_board`는 **채점 대상 자신의 크롭이 들어간 라이브러리로** 채점되고 있었다. 그 테스트 안의 두 번째 경로(test.png = 같은 파일)도 마찬가지였다. 수정: `_heldout_pairs`(모듈 범위, 인식기와 2-2·2-5 크롭)와 `heldout`(함수 범위, 테스트마다 `_reset_library`)로 나누고, 경로마다도 초기화한다. 고친 뒤에도 통과한다.
- **"남겨 둔" 평가가 낙관적(WARN)**: 2-5와 2-6은 보드 카밀·엘리스·카시오페아의 자리가 같고, 벤치 9칸이 **완전히 같다**(같은 유닛이 같은 칸에 서 있다). 2-5 표본을 넣은 라이브러리로 2-6을 맞히는 것은 거의 같은 그림을 맞히는 일이다(같은 유닛 닮음 0.86). 엔지니어 표 §4.1에서 의미 있는 행은 "2-2만"(2/5 이름, 3기는 자리 미상, 벤치 4/4)과 "없음"(집합 5/5)이다. 5개 캡처가 모두 **같은 판, 같은 스킨**이라 다른 판·다른 스킨으로 일반화되는지는 측정하지 않았다. 보고서 §4의 "5/5·4/4"를 일반 정확도로 인용하지 말 것.
- `evaluate` 22/22·17/17은 디스크 라이브러리(2-2·2-5·2-7·3-1 수확)로 자기 채점한 것이다. 엔지니어가 밝혔다.

## 3. 오답 0 정책 공격

| 공격 | 결과 |
|---|---|
| 빈 라이브러리(실제 2-6) | 칸 이름 0, 집합만 `unplaced` → 자리 미상 5기(맞음). PASS |
| 불완전 패널("N+") / 패널 없음 / 빈 패널 + 빈 라이브러리 | 이름·집합 모두 없음. PASS (`test_qa_incomplete_or_missing_panel_names_nothing_without_library`) |
| 실제 패널 3개(2-2·2-5·2-6)에서 행 하나 빠뜨림 / 인원 ±1 | 틀린 **단일** 풀이 0건(풀이 없음 또는 여럿). PASS (`test_qa_one_row_panel_misread_never_gives_a_wrong_unique_set`) |
| 무작위 보드 1,500개(특성표에서 뽑음)에 같은 오류 | 틀린 단일 풀이: 행 누락 135/1491(9%), 인원 -1 98/1500, +1 39/1500, 읽지 못한 상징 43/1500. 실제 보드는 시너지로 묶여 있어 훨씬 튼튼했지만, **패널 OCR 오류 하나가 확신에 찬 틀린 집합(자리 미상 0.8 또는 구속 배정 이름)을 만들 수 있다**. WARN |
| overflow "3+" | `complete=False` → 구속 안 씀. 5-5·Anvil에서 실측 PASS |
| 상징 | 장착 상징을 인식하면 빼고 푼다(테스트 있음). 아이템을 알아보지 못한 상징은 위 "읽지 못한 상징" 행과 같다 → WARN |
| **특성 없는 체력바 유닛**(훈련 봇, 골렘 등) | **FAIL-1**(아래) |
| 16:10 | 이름 라벨이 있는 16:10 캡처가 없다. 커밋된 fixture 7장(약 2000x1120)은 보드 판독이 0기라 이름 경로를 타지 않는다. 검증 불가(엔지니어 한계 §7-7과 같다) |

### FAIL-1: 한 종류 보드 + 특성 없는 유닛 → 틀린 이름이 디스크에 저장된다
- 재현(합성, `tests/test_vision_units.py::test_qa_non_champion_unit_is_not_forced_nor_persisted`, `xfail(strict=True)`): 보드 = 코그모 1기 + 특성 없는 유닛 1기. 패널 = 코그모 특성 → 풀이 {코그모}, 칸 2개 → `slot_values`의 2위가 불가능(-1e9)이다 → **두 칸 모두 `forced` 코그모, 신뢰도 0.95**(`units.py:426-430`). 두 크롭의 닮음은 0.0이다. `autolearn=True`이면 `_learn`이 두 크롭을 모두 `units_screen/DA_KogMaw18_AD/auto_*.png`로 저장한다(`units.py:613-614`). 스크래치 스크립트로 확인: 파일 2개가 저장됐다.
- 현실성: 정적 데이터에 특성 없는 유닛이 17종 있고(훈련 봇 `TFT_TrainingDummy`, 골렘 `TFT_BlueGolem` 등), Anvil 캡처 (0,2)에도 골렘으로 보이는 유닛이 있다. `config/settings.toml`의 `unit_autolearn = true`이므로 기본 설정에서 일어난다. 저장된 틀린 표본은 이후 라이브러리 1-NN을 계속 오염시킨다. 보고서 §1.6이 "강제 칸은 안전하다"고 가정한 부분이 깨진다.
- 수정 제안(vision-engineer):
  1. `forced`는 **칸 수 = 1**일 때만 인정한다. 칸이 여러 개인데 풀이 크기가 1이면, 칸끼리 서로 닮았을 때만(예: 쌍별 닮음 >= `DUP_MIN_SCORE`) 이름을 붙인다. 아니면 닮지 않은 칸은 `none`으로 둔다.
  2. 디스크 저장(`persist`)은 "보드 칸 1개 · 풀이 1개 · 크기 1"로 좁힌다.
  3. 고친 뒤 위 테스트의 `xfail` 표시를 지운다(strict이므로 고치면 XPASS로 실패해서 알려 준다).

## 4. 안전 — PASS
- `.gitignore`: `data/templates/*/units_screen/`(42행), `tests/fixtures/screens/test/`(50행) 추가. `raw/`는 이미 있었다. `git check-ignore`로 세 경로 모두 무시됨을 확인. `git ls-files`와 `git log --all`에 `units_screen`·`screens/test`·`screens/raw` 흔적 없음.
- 추적 대상 새 파일은 `vision/units.py`, `tests/test_vision_units.py`, `_workspace/19_unit_naming.md`뿐이다(그림 없음).
- 라벨 수확(`harvest_units` → `labeled_crops`)은 확인 라벨 `unit_id`만 쓴다. `name_unconfirmed`는 `unit_guess`로 따로 둔다. 확인함.
- 자동 학습: 구속 배정·라이브러리·중복으로 붙인 이름은 신뢰도가 높아도 메모리에만 둔다. 회귀 테스트 `test_qa_autolearn_writes_only_forced_names_to_disk` 추가. 예외는 FAIL-1(`forced`)이다.
- 디스크 라이브러리는 전부 `label_*`(30장)이다. QA 실행(`--screenshot`은 settings.toml의 autolearn=true 상태) 뒤에도 `auto_*`는 0장이다.
- 코드 안전 원칙: 새 모듈에 입력·메모리 접근이 없다(기존 `test_vision_code_has_no_memory_or_input_access` 통과).

## 5. 회귀 — PASS
- 기준선: `git worktree`로 HEAD(424f225)를 스크래치에 만들고 gitignore 자산(템플릿, stats.sqlite, raw 캡처)을 복사했다. `PYTHONPATH=<worktree>/src`로 실행했다. stash는 쓰지 않았고 worktree는 지웠다.
- 같은 환경(`PYTHONIOENCODING` 없음)에서 **기준선과 변경 후의 실패 5건이 같다**:
  `test_api_key::test_status_shows_only_a_masked_hint_of_a_stored_key`, `test_setup::test_dialog_shows_the_permission_box_on_black_captures`, `test_boundaries::test_config_accepts_design_keys`, `test_credentials` 0600 두 건.
- `PYTHONIOENCODING=utf-8`이면 `test_config_accepts_design_keys`가 통과해 실패는 4건이다(엔지니어가 말한 4건). 5번째 실패는 환경 탓이다. 하위 프로세스가 cp1252로 출력하다 한글에서 죽는다. 기존부터 있던 문제다.
- `tests/test_vision_units.py` + `tests/app/test_board_wiring.py`(QA 테스트 포함): 전부 통과, xfail 1(FAIL-1).
- 환경 참고(app-integrator): 파이프로 출력하면 `python -m tft_advisor --screenshot`이 `UnicodeEncodeError`(cp1252)로 죽는다(`app/screenshot.py:64`). 해결 명령은 `set PYTHONIOENCODING=utf-8`이다. 근본 해결은 진입점에서 `sys.stdout.reconfigure(encoding="utf-8")`. 이번 변경과는 무관하다.

## 6. 시간 — WARN
test.png(4480x1440), 워밍업 뒤 5회:
| 조건 | ms |
|---|---|
| 이름 켬, 패널 캐시 없음 | 444 / 458 / 444 |
| 이름 켬, 패널 캐시 적중 | 390 / 353 |
| 이름 끔(`unit_names=false`) | 333~354 |
| `groups={"board"}`만(모니터 탐지 포함) | 켬 206 / 끔 170 |

- 이름 붙이기 비용은 캐시가 적중하면 +20~40ms, 패널 OCR을 다시 하면 +90~120ms다. 보고서 §6과 같다. 300ms 목표는 이번 변경 전부터 넘었다(끔 상태 335ms).
- 패널 캐시 키 WARN(`recognizer.py:646`): 24x64로 줄이고 `//24`로 양자화한다. 큰 인원 글자를 "1"로 바꾸면 1536칸 중 **3~4칸**만 달라지고, 작은 사다리 글자는 바꿔도 키가 **같았다**. 활성·비활성 전환은 행 색이 바뀌어 잡히겠지만, 인원만 바뀌는 경우(이미 있는 특성만 가진 챔피언 추가)에 옛 패널을 쓸 위험이 있다 → 틀린 풀이. 제안: 인원 칸 열(x 0.25~0.40)만 원래 해상도로 해시에 더하거나, 양자화를 줄인다.

---

## 담당자별 요청

### vision-engineer
1. **FAIL-1**(필수): `units.py:426-430` `forced` 조건과 `:613` 저장 조건을 §3대로 좁히고, `test_qa_non_champion_unit_is_not_forced_nor_persisted`의 xfail을 지운다.
2. WARN: 패널 캐시 키 민감도(`recognizer.py:646`).
3. WARN: 보고서 §4의 정확도를 "같은 판·같은 자리 표본" 조건으로 적는다. 다른 판 캡처(다른 스킨·자리)로 보이지 않았던 챔피언을 평가한다.
4. 참고: 풀이가 여럿이고 `set_conf=0`이면(`units.py:486`) 라이브러리만으로 이름을 붙이는데, 그 이름이 **어느 풀이에도 없는** 챔피언일 수 있다. 풀이 합집합 안에 있을 때만 받도록 거르는 것을 권한다(오답 0 강화).

### 오케스트레이터 / jev-strategist
- 보고서 §8-1(보드만 믿고 쓸지)은 정책 결정이다. 경계면은 어느 쪽이든 맞는다. FAIL-1을 고치기 전에는 보드 단독 신뢰를 켜지 않는 것을 권한다.

### app-integrator
- `--screenshot` 파이프 출력의 cp1252 문제(§5).

## QA가 바꾼 파일
- `tests/test_vision_units.py`: `heldout` fixture를 모듈 범위(`_heldout_pairs`)와 함수 범위(`heldout` + `_reset_library`)로 나눴다(순환 채점 제거). QA 테스트 6개를 추가했다(`test_qa_*`, 그중 xfail 1).
