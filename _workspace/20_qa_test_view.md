# 20 QA: 19 수정 라운드 재검증 + 인식 확인 창(test view)

작성일: 2026-09-23 / 작성자: qa-validator / 대상: 커밋 전 변경(19 수정 라운드 + 20 인식 확인 창) / 커밋하지 않음

## 요약: PASS 9 / WARN 4 / FAIL 1

| # | 항목 | 결과 | 근거 | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| A1 | QA-19 FAIL-1(특성 없는 유닛을 강제로 이름 붙이고 저장) | **PASS** | 공격 변형 14개 중 특성 없는 칸에 이름이 붙은 경우 0. 칸이 2개 이상이면 디스크 저장 0(§A1) | - | - |
| A2 | 패널 캐시 비교(`panel_text_region`/`panel_unchanged`) | **PASS** (WARN 1) | 같은 패널은 0px로 적중, 다른 패널은 5,726px 이상으로 빗나감. 합성 인원·구간 글자 변화도 빗나감(§A2) | vision-engineer | 해상도 참고(§A2) |
| A3 | 패널 신뢰도 0.75 기준 | **PASS** | 0.74 → 신뢰도 0.807, 저장 안 함. 0.75 → `forced` 0.95, 저장함. 실제 패널: 0.80이 12장, 0.73이 1장(5-5 전투 시작) | - | - |
| A4 | 디스크 `auto_*` 0장, git 추적 파일 | **PASS** | QA가 캡처 13장을 두 번 돌린 뒤(autolearn=true)에도 `auto_*` 0장, `label_*` 30장. 추적되지 않은 파일은 코드·테스트·보고서뿐이고 그림은 없다. `units_screen/`와 `_state/`는 gitignore 대상이다 | - | - |
| A5 | 라이브러리만 쓰는 경로의 여유(5-5·Anvil) | **WARN** | 처음 보는 챔피언의 점수가 기준(0.50/0.10) 바로 아래다. 5-5 (2,3) 나무 모델 → 아칼리 s=0.50 · 차 0.14. 기준을 조금만 풀어도 오답이 나온다(§B5) | vision-engineer | §B5 |
| B1 | 경계면(UnitSlot/BoardRead ↔ recog_view, LoopUpdate) | **PASS** | §B1 | - | - |
| B2 | 표시 정직성(창 = GameState = 사람 라벨) | **PASS** | 캡처 13장에서 창과 상태의 차이 0건. 보드·벤치 칸의 자리·성급·아이템과 아이템 벤치가 라벨과 같다(§B2) | - | - |
| B3 | 보드 모드 판정 | **FAIL** | `recog_view.BOARD_MODES`={준비, 전투}이고 vision `READ_MODES["board"]`={준비, 아이템 선택}이다. 모루·전리품 화면에서 **이번 프레임에 새로 읽은** 보드를 "보드가 보이지 않습니다 — 마지막으로 읽은 값"이라고 흐리게 표시한다(Anvil·악의 여단 캡처로 재현) | app-integrator | §B3 FAIL-1 |
| B4 | 이름 출처 라벨(화면/장부/수동/미상) | **PASS** | §B4 | - | - |
| B5 | 5-5에서 18기 모두 "이름 미상" | **PASS**(의도된 동작) | 특성 패널이 넘쳐 `complete=False`라 구속을 쓰지 않는다. 라이브러리에는 2스테이지 챔피언 8명뿐이라 모든 칸이 기준에 못 미친다. 버그가 아니다(§B5) | - | - |
| B6 | CLI > 트레이 > 설정 우선순위와 저장 | **PASS** (WARN 1) | §B6. 누수 경로 1개 | app-integrator | §B6 |
| B7 | Qt(UI 스레드, 포커스, offscreen) | **PASS** (WARN 1) | §B7. 실제 게임에서 포커스를 가져가지 않는지는 사람이 확인해야 한다 | 사용자 | §B7 |
| B8 | cp1252 파이프 | **PASS** | `PYTHONIOENCODING` 없이 `… \| cat`로 13장 모두 rc=0, UnicodeEncodeError 0 | - | - |
| B9 | 회귀(전체 pytest) | **PASS** | 새 실패 0. 기존 Windows 실패 4건만 남는다(§B9) | - | - |

---

## A. 19 수정 라운드

### A1. FAIL-1 재검증 — PASS
- 코드: `units._place_board`(units.py:416-448)는 `k==1 and n>1`이면 `_place_single_champion`(:451-469)으로 보낸다. 여기서 `forced`는 나오지 않는다. `forced`는 칸 1개·챔피언 1명일 때만 나온다. 디스크 저장 조건은 `_persist_ok`(:642-650)가 `_learn`(:661)에서 `n.source == "forced"`와 함께 본다.
- 스크래치 공격(합성 크롭, autolearn=True, `_persist_ok` 실제 경로):

| 변형 | 결과 |
|---|---|
| 코그모+더미, 빈 라이브러리 | 둘 다 모름, 저장 0 |
| 코그모+더미, 라이브러리에 코그모 | 코그모 칸만 `traits` 0.85, 더미는 모름, 저장 0 |
| 코그모 2기(같은 모델) | 둘 다 `traits` 0.85(메모리만), 저장 0 |
| 코그모+서로 닮은 더미 2 | 셋 다 모름(모두가 닮지는 않음), 저장 0 |
| 닮은 더미 2 + 패널 = 코그모(오독) | 둘 다 모름, 저장 0 |
| 코그모+아칼리+더미(라이브러리 둘 다 / 코그모만 / 빈) | 더미는 항상 모름. 여유가 0이라 소거되지 않는다. 저장 0 |
| 벤치에 코그모 복사본 | 벤치도 모름(보드 이름이 0.8 미만이라 중복 배정 안 됨) |
| 패널 신뢰도 0.74 / 0.75 (칸 1개) | 0.807 저장 안 함 / 0.95 저장함. 기준이 정확히 동작한다 |
| 칸 1개 더미 + 라이브러리가 "이 모델은 아칼리" | 저장 안 함(모순 검사 동작) |

- **남은 위험(정보)**: 칸 1개 보드에서 특성 패널 OCR이 없는 특성 행을 **만들어 낸** 경우에만 저장이 일어난다(A9 변형). 특성 없는 유닛 1기뿐이면 패널이 비어 풀이가 없으므로 현실성은 낮다. 모순 검사(A10)는 저장을 막지만, 그 칸은 여전히 `forced` 0.95로 표시되고 메모리 표본으로 들어간다(`_learn`의 메모리 경로는 `persist_ok`를 보지 않는다). 이번 실행 동안만 영향이 있다. 권고: 라이브러리와 모순되면 `forced` 신뢰도도 깎는다(vision-engineer, 선택).
- 회귀 테스트 추가: `tests/test_vision_units.py::test_qa20_traitless_unit_variants_never_named_nor_persisted`(7개 변형). 통과.

### A2. 패널 캐시 비교 — PASS
캡처 13장의 패널 글자 영역끼리 비교했다(밝기 차 40 초과 픽셀 수, 허용 6):
- 적중(0px): 2-6 전투 전 ↔ 2-6 전투 시작 ↔ 악의 여단, 5-5 전투 전 ↔ 5-5 전투 시작.
- 내용이 같은데 빗나감(해롭지 않음, OCR만 다시 함): 2-2 전투 전 ↔ 2-2 전투 시작 114px, 수호령 ↔ 2-6 866px/1,213px, 수호령 ↔ 악의 여단 467px.
- 내용이 다르면 항상 빗나감: 5,726px 이상(최소값은 5-5 ↔ Anvil).
- 합성 테스트(인원 "1"→"2", 구간 "1/2"→"1/3")와 원본 테스트 둘 다 통과한다.
- WARN(정보): 허용치는 픽셀 수로 고정돼 있다. 구간 글자 하나 = 18px은 1920x1080 게임 화면에서 잰 값이다. 1280x720이면 약 8px로 줄어 허용치 6에 가까워진다. 720p를 지원 대상으로 삼으면 허용치를 패널 높이에 비례하게 바꾸는 것을 권한다.

### A3. 0.75 패널 신뢰도 기준 — PASS
`name_units`(units.py:506)는 유일한 풀이의 `set_conf`를 1.0에서 0.85로 낮춘다. `_persist_ok`는 0.75 미만이면 거짓이다. 실제 패널 신뢰도는 0.80(12장)과 0.73(5-5 전투 시작, 넘친 패널이라 어차피 구속하지 않음)이다. 이 기준은 성능이 떨어진 판독만 걸러 내고, 정상 판독은 막지 않는다.

### A4. 안전 — PASS
- `find data/templates -path "*units_screen*" -name "auto_*"` → 0(전후 모두). `label_*` 30장.
- `git status`: 추적되지 않은 새 파일은 `units.py`, `recog_view.py`, `recog_window.py`, 테스트 2개, `_workspace` 보고서 3개다. `git check-ignore`: `data/templates/*/units_screen/`(.gitignore:42), `_state/`(.gitignore:29).
- 참고: `tests/app/conftest.py`의 세션 `recognizer` 픽스처는 `settings.toml`(unit_autolearn=true)을 쓴다. 지금 캡처에는 보드 1기짜리가 없어 쓰기가 일어나지 않지만, 앞으로 1기 보드 캡처가 들어오면 테스트가 디스크 라이브러리에 쓸 수 있다. QA 테스트는 `monkeypatch`로 끈다. 권고: 픽스처에서 `unit_autolearn=False`(app-integrator, 사소).

---

## B. 인식 확인 창

### B1. 경계면 — PASS
- `recog_view`가 읽는 필드: `UnitSlot.unit_id/name_source/item_count/items/hex/bench_slot`, `BoardRead.board/bench/unplaced/unresolved_items/confidence/count`. 수정 라운드 뒤 `vision/board.py:116-156`과 이름이 모두 같다. `name_source` 값 `forced|traits|library|duplicate|none`(board.py:129, units.py:397) ↔ `VISION_DETAIL`(recog_view.py:29-31) 키 4개가 대응한다. `none`은 이름이 없으니 쓰이지 않는다.
- `LoopUpdate.board_read/recog_ms`(loop.py:59-62): `step()`이 채우고 `_emit`이 advice가 아닌 갱신에만 싣는다(loop.py:370-375). `test_loop_attaches_board_read_and_recognition_time`이 고정한다.
- `field_source`: `unit_merge.merge_units`(unit_merge.py:337-342) VISION/TRACKED/MANUAL ↔ `unit_source` 4번 규칙과 `_source` 라벨이 일치한다.

### B2. 표시 정직성 — PASS
- 캡처 13장 전부(스크래치 스크립트): 창의 보드 행(자리·ID·성급·아이템), 벤치 9칸의 빈 칸·ID·성급·아이템, 장착 아이템 다중집합(`items.equipped`), 미사용 아이템 다중집합을 `GameState`와 비교했다. **차이 0건**.
- 사람 라벨과 대조(`board_slots`/`bench_slots`/`item_bench`가 있는 캡처): 보드 자리 누락·추가 0, 성급 오류 0, 장착 아이템 오류 0, 아이템 벤치 13/13 일치. 이름: 2-2 3/3, 2-5 4/4, 2-6 5/5, 수호령 5/5, 악의 여단 5/5, 벤치 확인분 전부 맞고 오답 0. 단, 디스크 라이브러리가 이 캡처들에서 수확한 표본이라 **자기 채점**이다(QA-19 §2.3과 같음).
- `--screenshot <raw 13장> --test-view --no-overlay --no-jev`: 전부 rc=0. 5-5 출력의 자리·성급·아이템이 `5-5 전투 전.expected.json`과 칸마다 같다(`hex`+1 = "N행 M열").
- 콘솔에는 이름을 모르는 칸도 `★1`로 찍히지만, 이 값은 판독 성급이고 라벨과 같다.
- 회귀 테스트: `tests/app/test_recog_view.py::test_qa_raw_view_equals_state_and_labels[5-5 전투 전 / 2-6 전투 전]`.

### B3. FAIL-1: 모루·전리품(아이템 선택) 화면에서 새로 읽은 보드를 "보이지 않음·마지막 값"으로 표시한다
- 위치: `src/tft_advisor/app/recog_view.py:26` `BOARD_MODES = frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT})`
- 비교 대상: `src/tft_advisor/vision/recognizer.py:77` `"board": frozenset({ScreenMode.PLANNING, ScreenMode.ITEM_SELECT})`. vision은 아이템 선택 화면에서 보드를 **읽는다**(보드가 그대로 보이기 때문).
- 재현: `--screenshot "tests/fixtures/screens/raw/Anvil.png" --test-view --no-overlay --no-jev` → `! 보드가 보이지 않습니다 (화면: 아이템 선택) — 아래는 마지막으로 읽은 값입니다`. 그런데 그 아래 보드 8기와 벤치 8칸은 **이 이미지에서 방금 읽은 값**이다(라벨과 일치). 창에서는 표 전체를 흐리게 그린다(recog_window.py:179). `악의 여단.png`도 같다. 실시간에서도 모루가 열려 있는 동안 계속 그렇게 보인다.
- 반대쪽: 전투 화면은 `BOARD_MODES`에 있지만 vision은 보드를 읽지 않는다. 실시간에서는 `groups`에 "board"가 없어 "직전 판독" 알림이 뜨므로 거짓은 아니다.
- 기대: 아이템 선택 화면 = 보드가 보이고 새로 읽음(알림 없음, 흐리게 하지 않음). 전투 = 보드는 보이지만 다시 읽지 않음(지금처럼 "직전 판독").
- 수정 제안(app-integrator): 상수를 복제하지 말고 `READ_MODES["board"] | {ScreenMode.COMBAT}`로 만든다(단일 출처). 고친 뒤 `test_qa_board_is_visible_wherever_vision_reads_the_board`의 `xfail(strict=True)`를 지운다. 고치면 XPASS로 실패해 알려 준다.

### B4. 이름 출처 라벨 — PASS
- `unit_source`(recog_view.py:230-252)를 `merge_units`와 나란히 놓고 확인했다.
  - vision이 이름을 준 칸 → 같은 자리 판독 칸의 `unit_id`가 같다 → "화면(근거)". 스크린샷 13장에서 이름 있는 칸은 모두 "화면(…)"이고, 근거 없는 "화면"은 0건이다.
  - 자리 미상(`hex=None`, `unplaced`) → "화면(자리 미상)". 테스트가 있다.
  - 장부가 채운 칸(판독 칸 `unit_id=None`) → 필드 출처는 TRACKED 또는 MANUAL이다. vision 이름이 하나라도 섞이면 `merge_units`가 VISION이 아니라 TRACKED를 쓰므로(unit_merge.py:339-342), 4번 규칙이 장부 칸을 "화면"이라고 부를 수 없다.
  - 같은 자리 판독 칸의 이름이 상태 이름과 다르면 "화면"이라고 하지 않는다. QA 테스트 `test_qa_slot_with_a_different_vision_name_is_not_labelled_screen`을 추가했다.
  - `UNKNOWN_UNIT_ID` → "미상".
- 참고: 실시간 새 판 리셋 갱신(`_do_reset`, loop.py:320)은 `last_board_read=None`으로 싣는다. 그 한 프레임은 출처가 필드 출처로 돌아간다. 해롭지 않다.

### B5. 5-5에서 18기가 모두 "이름 미상"인 이유 — 의도된 동작(여유 WARN 1건)
- 특성 패널: `complete=False`(특성 9행 + 넘침, 5-5 전투 전 신뢰도 0.80). `solve_board_sets`가 None을 돌려주고 `trait_solutions=0`이 된다. 구속이 없으므로 라이브러리만 쓴다(units.py:520-525).
- 디스크 라이브러리: 챔피언 8명(아칼리, 카밀, 카시오페아, 엘리스, 오른, 바루스, 자야, 코그모), 모두 2스테이지 캡처에서 나왔다. 5-5 보드는 대부분 다른 챔피언이다(패널: 부식·대부·기원자·달빛·속사포·적응가·지옥불·선봉대·원시).
- 칸별 1위 점수는 0.30~0.51, 차는 0.00~0.14다. `LIB_MIN_SCORE 0.50`과 `LIB_MIN_MARGIN 0.10`을 동시에 넘는 칸이 없으므로 모두 모름이다. **오답 0 정책이 의도대로 동작한 것이고 버그가 아니다.**
- **WARN(vision-engineer)**: 기준 바로 아래 칸이 있다. 5-5 보드 (2,3)은 아칼리 s≈0.50 · 차 0.14, (3,0)은 아칼리 0.51 · 0.08이다. 크롭을 눈으로 확인하니 (2,3)은 나무 모델이고 (3,0)은 붉은 발톱 모델이라 **둘 다 아칼리가 아니다**. 점수가 0.001만 올라도 확신 있는 오답(신뢰도 약 0.71)이 된다. Anvil도 0.48~0.49 · 0.07~0.08이 있다. 이 기술자는 처음 보는 챔피언에게도 약 0.5를 준다. 권고: 구속이 없는 라이브러리 단독 경로는 기준을 올린다(예: 점수 0.60 이상 또는 차 0.20 이상). 아니면 다른 판 캡처로 기준을 다시 잰다. 알고 있는 챔피언은 1.0/0.58처럼 크게 떨어져 있어서, 기준을 올려도 잃는 것은 적어 보인다.
- 참고: Anvil 벤치 4(slot 3) = 오른, s=1.00이다. 같은 크롭이 라이브러리에 있다(자기 채점).

### B6. 우선순위와 저장 — PASS(WARN 1)
- 실시간: `run_live(test_view=)` → 콘솔 `settings.ui.test_view if cli is None else cli`(live.py:111), 오버레이 `initial_enabled`(recog_window.py)가 같은 규칙을 쓴다. 트레이 토글은 `persists = cli is None`일 때만 `[ui] test_view`에 저장한다. CLI를 준 실행에서는 메뉴 글자가 "(CLI 지정 — 이번 실행에만 적용)"으로 바뀐다. 기존 테스트가 있다.
- 스크린샷: `__main__.py:163-164`가 `bool(args.test_view)`만 본다. 설정값을 보지 않는다. QA 테스트 `test_qa_screenshot_mode_ignores_ui_test_view_setting`을 추가했다. 설정 켬 + 플래그 없음이면 (확인 출력, 창) = (끔, 끔)이다. `--test-view --no-overlay`는 (켬, 끔), `--test-view`는 (켬, 켬).
- **WARN(app-integrator) — CLI 실행의 비저장 토글이 설정 화면을 거쳐 저장된다**: `RecogController.set_enabled`는 CLI 실행에서도 `self.settings.ui.test_view = on`으로 메모리 설정을 바꾼다(recog_window.py, `set_enabled`). 설정 화면은 체크박스를 `s.ui.test_view`로 채우고(setup_dialog.py:326) 저장할 때 항상 `test_view`를 쓴다(setup.py `updates`, None이 아니므로). 재현 순서: `--no-test-view`로 실행 → 트레이에서 켬(저장 안 함) → 트레이 "설정"을 열고 해상도 등만 바꿔 저장 → `[ui] test_view = true`가 저장된다. 수정: `persists`가 거짓이면 메모리 설정을 바꾸지 않는다. 또는 설정 화면이 파일 값을 보게 한다.
- 정보: `_apply_saved_recog`(overlay.py:310-316)는 CLI로 정한 실행에서도 설정 화면 값을 바로 적용한다. Jev(`_apply_saved_jev`)는 CLI로 잠긴 경우 적용하지 않는다. 트레이 토글이 CLI 실행에서도 허용되므로 모순은 아니다. 다만 두 토글의 동작이 다르므로 문서에 한 줄 적어 두는 것을 권한다.

### B7. Qt — PASS(WARN 1)
- UI 스레드: 루프 스레드는 `window.on_loop_update` → 시그널 → `_on_update_main` → `_feed_recog`(overlay.py:210-223) 순서로 넘긴다. `RecogController`와 `RecogWindow`는 UI 스레드에서만 호출된다. 콘솔 모드의 `ConsolePrinter`는 Qt를 쓰지 않는다. 스크린샷 창은 메인 스레드에서 `QApplication`을 만든다.
- 포커스: passive 창은 `FramelessWindowHint | Tool | WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating` + `NoFocus`(닫기 버튼과 스크롤 포함)다. `apply_always_on_top`은 Qt 플래그만 더하고(platform_window.py:50-59) `show()` 전에 불린다. Win32 `SetWindowPos`나 `SetForegroundWindow` 호출은 없다. 클릭 통과는 아니다(드래그용, 의도). 입력 주입이나 메모리 접근은 없다.
- offscreen 테스트: `tests/app/test_recog_view.py` 26개(QA 5개 포함, xfail 1) 통과.
- WARN(사용자 확인): 전체 화면 창 모드 게임 위에서 창을 누르거나 끌 때 게임이 포커스를 잃지 않는지는 헤드리스로 확인할 수 없다. 사람이 한 번 확인해야 한다(20 보고서 §5와 같음).

### B8. cp1252 — PASS
- `env -u PYTHONIOENCODING python -m tft_advisor --screenshot <각 캡처> --test-view --no-overlay --no-jev | cat` → 13장 모두 rc=0, stderr에 Error 0. `ensure_utf8_stdio`는 `main()` 맨 앞과 `run_screenshot`(out is print)에서 불린다. 테스트 `test_ensure_utf8_stdio_fixes_cp1252_pipe`가 있다. QA-19 §5의 환경 요청이 해결됐다.

### B9. 회귀 — PASS
- `PYTHONIOENCODING=utf-8 .venv\Scripts\python.exe -m pytest`: 실패는 기존 Windows 4건뿐이다. `test_api_key::test_status_shows_only_a_masked_hint_of_a_stored_key`, `test_setup::test_dialog_shows_the_permission_box_on_black_captures`, `test_credentials` 0600 권한 2건. 이번 변경과 관계없다. 전체 개수는 §부록.
- 관찰: 이 환경에서는 `pytest -q`가 마지막 "N passed" 줄을 찍지 않았다(요약 목록만 나옴). 개수는 junit xml로 셌다.

---

## 담당자별 요청

### app-integrator
1. **FAIL-1(필수)**: `recog_view.py:26` `BOARD_MODES`를 `vision.recognizer.READ_MODES["board"] | {ScreenMode.COMBAT}`에서 가져온다. 그다음 `test_qa_board_is_visible_wherever_vision_reads_the_board`의 xfail을 지운다.
2. WARN: CLI 실행의 비저장 토글이 설정 화면을 거쳐 `[ui] test_view`에 저장되는 누수(§B6).
3. 사소: `tests/app/conftest.py` `recognizer` 픽스처에 `unit_autolearn=False`(§A4).

### vision-engineer
1. WARN: 구속 없는 라이브러리 단독 경로의 기준 여유(§B5). 처음 보는 챔피언이 0.50/0.14까지 올라온다.
2. 정보: 라이브러리와 모순되는 `forced` 칸은 저장은 막히지만 0.95로 표시되고 메모리 표본이 된다(§A1).
3. 정보: 패널 캐시 허용치는 픽셀 수로 고정돼 있다. 720p에서는 여유가 작다(§A2).

### 사용자
- 실제 게임(전체 화면 창 모드)에서 인식 확인 창을 눌러도 게임이 포커스를 잃지 않는지 확인해 주세요.

## QA가 바꾼 파일(테스트만, 제품 코드는 수정하지 않음)
- `tests/app/test_recog_view.py`: QA 테스트 5개를 추가했다(보드 모드 xfail strict 1, 직전 판독 알림, 출처 불일치, 스크린샷 모드의 설정 무시, 실제 캡처 창 = 상태 = 라벨 ×2).
- `tests/test_vision_units.py`: `test_qa20_traitless_unit_variants_never_named_nor_persisted`(7개 변형)를 추가했다.

## 부록: 전체 스위트 개수
- junit 기준: tests 1126 · failures 4 · errors 0 · skipped 5. junit은 xfail을 skipped로 센다. 그래서 skipped 5 = 건너뜀 3 + xfail 2(기존 1, QA 20 FAIL-1 1)이고, 통과는 1117이다. app-integrator 보고의 1112 통과에 QA가 추가한 테스트가 더해진 값과 맞다.
- 모든 실행이 끝난 뒤에도 `auto_*`는 0장이다.
