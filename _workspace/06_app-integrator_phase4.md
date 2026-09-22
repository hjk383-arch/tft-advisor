# 06 app-integrator: Phase 4 통합 (실시간 루프 · 오버레이 · --screenshot)

작성일: 2026-09-22 / 작성자: app-integrator / 커밋하지 않음
기준: `02_app-integrator_report.md`(C3 Phase 4 메모), `04_jev-strategist_impl.md`(§2 반환 규칙, §6 전달), `04_vision-engineer_impl.md`·`05_vision_aspect_and_labels.md`(Recognizer·ChangeDetector·화면 비율), `04_qa_phase3_final.md` §6 Phase 4 이월 항목.
**UI 백엔드: PySide6로 확정(사용자 결정).** CONTRACT_VERSION은 0.2.0 그대로다(`contracts.py` 변경 없음).

---

## 0. 요약

1. `--screenshot` 모드가 동작한다. 파일 1장 또는 **폴더**를 받아 인식 → 추천 → **한국어 요약**을 출력한다. 사용자 실캡처 6장(`tests/fixtures/screens/raw/`, gitignore)과 방송 크롭 fixture 7장 모두에서 돈다.
2. 실시간 루프(`--live`, 기본 모드)를 넣었다. 캡처 → ROI 서명 변화 감지 → **바뀐 묶음만 인식** → 세션 병합 → 백그라운드 추천 → 오버레이 갱신. UI 스레드에서는 인식도 Jev 호출도 하지 않는다.
3. PySide6 오버레이: 테두리 없음 · 항상 위 · 반투명 · 기본 클릭 통과. 트레이 메뉴(+ 창이 잠금 해제일 때 단축키)로 표시/숨기기·이동 잠금·위치 저장·불투명도 조절. **목표 덱은 advisor 순서 그대로** 표시한다(점수 재정렬 금지, 테스트로 고정).
4. **PySide6 6.11.2가 이 Intel Mac + Python 3.14에 설치된다**(휠 있음). extras `ui` 추가. 없으면 콘솔 모드로 내려간다(조용히 tkinter로 바꾸지 않는다).
5. CLI: `--screenshot` / `--live` / `--jev {mock,live,off}` / `--no-jev`(→ off, 죽어 있던 플래그를 연결) / `--profile`·`--aspect`·`--resolution` / `--no-overlay` / `--debug`.
6. 테스트: **647 passed, 3 skipped**(이전 559 passed, 3 skipped → +88). live Jev 호출은 CLI 스모크 1회뿐이고 테스트에서는 0회다.
7. 전투 화면 오분류(vision 미해결)는 **보수적 병합·유지 규칙**으로 흡수했다(§7).

---

## 1. 만든 파일

| 파일 | 역할 |
|---|---|
| `src/tft_advisor/app/session.py` | `merge_state()`(부분 인식 병합 규칙), `SessionTracker`(보유 증강·구매 추적, `_state/session.json` 영속), `RESET_MODES`/`KEEP_MODES`/`GROUP_READ_MODES` |
| `src/tft_advisor/app/loop.py` | `LiveLoop`(캡처→변화 감지→부분 인식→병합→추천), `LoopUpdate`, `InlineAdviceRunner`/`ThreadAdviceRunner` |
| `src/tft_advisor/app/report.py` | 한국어 표시 문구(콘솔·오버레이 공용): 상태줄, 목표 덱/상점/증강/아이템 줄, 인식 경고, `format_report()` |
| `src/tft_advisor/app/names.py` | ID → 한국어 표시 이름(`NameBook`), 클라이언트 마크업 제거 |
| `src/tft_advisor/app/overlay.py` | `OverlayWindow`(PySide6), 트레이 메뉴, 위치 저장 |
| `src/tft_advisor/app/platform_window.py` | 항상 위 / 클릭 통과의 플랫폼 분기와 실패 시 대체(`WindowEffect`) |
| `src/tft_advisor/app/screenshot.py` | `--screenshot` 모드 |
| `src/tft_advisor/app/live.py` | `--live` 배선(오버레이 또는 콘솔), `choose_ui`, `warm_up` |
| `src/tft_advisor/__main__.py` | CLI 전면 교체(§3) |
| `src/tft_advisor/config.py`, `config/settings.toml` | `[overlay]` 신설, `[app] state_dir`, `[ui] backend`에 `"console"` 추가(§5) |
| `pyproject.toml` | extras `ui = ["PySide6>=6.10"]` 추가(옛 `ui-qt`는 별칭으로 남김) |
| `.gitignore` | `_state/` 추가 |
| `tests/app/` | `conftest.py` + 6개 파일(§6) |

---

## 2. 아키텍처

### 2.1 데이터 흐름 (실시간)

```
MssSource.grab()                     capture_fps(기본 4) 주기
  → ChangeDetector.update(img, content)      바뀐 ROI 묶음 & change_stable_frames 연속 동일
  → Recognizer.recognize(groups=바뀐 묶음)     "stage"가 바뀌면 전체 재인식, traits_every_s마다 traits 추가
  → SessionTracker.observe()                  직전 GameState에 병합 + 보유 증강·구매 추적
  → ThreadAdviceRunner.submit()               최신 상태만 남기고 이전 요청은 버린다
       → Advisor.advise() → Recommendation
  → on_update(LoopUpdate)                     오버레이(Qt 시그널) 또는 콘솔 print
```

### 2.2 스레드

| 스레드 | 하는 일 | 근거 |
|---|---|---|
| 메인(UI) | Qt 이벤트 루프, 그리기만 | 인식 0.3~0.9s·Jev 최대 2s가 UI를 멈추면 안 된다 |
| `tft-capture` | mss 캡처 + OCR 인식 + 병합 | 프레임당 가장 비싼 구간 |
| `tft-advisor` | `Advisor.advise()` | `Advisor`는 전용 asyncio 루프를 재사용한다(Jev 연결 유지) → **한 스레드에 고정**. 새 상태가 오면 앞의 요청은 버린다 |

`--no-overlay`면 메인 스레드가 캡처 루프를 직접 돌린다(추천은 그래도 별도 스레드).
워밍업(`live.warm_up`)은 추천 스레드가 뜨기 전에 **동기로** 1회 돈다(jev 보고 §7-7의 첫 호출 1.2s 부담을 시작 시점으로 옮긴다).

### 2.3 부분 인식 병합 규칙 (`app/session.merge_state`)

1. 새 값이 있으면 새 값(신뢰도·`field_source`도 함께).
2. 새 값이 없고 **그 묶음을 이번에 실제로 읽었으면**(요청했고 현재 화면이 그 묶음을 읽는 화면이면) 낡은 값을 **버린다**. 이미 산 유닛을 계속 추천하는 쪽이 "모름"보다 나쁘다.
3. 그 밖에는 직전 값 유지 — 전투·unknown 화면에서 HUD를 못 읽었다고 상태를 잃지 않는다.
4. `screen_mode`는 상속하지 않는다. `augment_offer`는 **일회성**이라 증강 화면을 벗어나면 버린다.

`GROUP_READ_MODES`가 "어느 화면에서 어느 묶음이 실제로 읽히는지"의 단일 출처다(`Recognizer.recognize`의 모드 분기와 같아야 한다 — `test_field_group_covers_every_group_rule`이 묶음 누락을 잡는다).

### 2.4 화면 모드 계약 (advisor와 동일)

| 화면 | 루프 동작 |
|---|---|
| `loading` / `game_over` | 세션 초기화 + `advisor.reset()` + 오버레이 비움 (`kind="reset"`) |
| `combat` / `item_select` / `unknown` | **추천을 다시 계산하지 않는다**(직전 추천 유지, `kind="kept"`). 상태 병합·체력 추적은 계속 |
| `carousel` | advisor가 Jev 없이 `component_priority`만 갱신 → UI는 "캐러셀(통계)"로 표시(실패로 보이지 않게) |
| `planning` / `augment_select` | 정상 추천. 증강·아이템 추천은 해당 화면에서만 표시 |

### 2.5 보유 증강(`augments_owned`) 추적 — 현재 상태

- 증강 화면의 후보(`augment_offer`)만으로는 사용자가 무엇을 골랐는지 알 수 없다. **1위 추천을 골랐다고 가정하지 않는다**(C3.1 그대로).
- 확정 경로는 셋이다: ① vision이 HUD 보유 증강을 읽으면 그대로 사용(`field_source=vision`) — **아직 미구현, 캡처 #6 대기**, ② `SessionTracker.set_augments_owned()` 수동 입력(`manual`), ③ 세션 복원(`tracked`).
- 확정 전에는 `None`이고 advisor가 "모름"으로 처리한다. 마지막으로 본 후보는 `session.json`의 `last_offer`에 남는다(나중에 확인용).
- **수동 입력 UI는 아직 없다**(§9). 현재는 API/세션 파일로만 넣을 수 있다.

### 2.6 세션 영속

- 경로: `{app.state_dir}/session.json` (기본 `_state/session.json`, gitignore). 원자적 쓰기(tmp → replace), 쓰기 실패는 경고만 남기고 앱은 계속 돈다.
- 내용: `stage`, `augments_owned`(+출처), `last_offer`, `purchases`(상점 칸이 챔피언→빈 칸으로 바뀐 횟수 = 구매 추정. 새로고침처럼 3칸 이상 동시에 바뀌면 세지 않는다), `frames`/`recognitions`.
- **2시간이 지난 파일은 복원하지 않는다**(다른 판). `loading`/`game_over`를 보면 즉시 비운다.

---

## 3. CLI (복사해서 쓰는 명령)

```bash
# 베타 테스트: 내 캡처 1장 인식 + 추천 (Jev는 mock = 네트워크·과금 없음)
.venv/bin/python -m tft_advisor --screenshot "tests/fixtures/screens/raw/unnamed-1.png"

# 폴더 통째로(이름순, 추천기를 이어 쓴다 — 실제 스트림처럼)
.venv/bin/python -m tft_advisor --screenshot tests/fixtures/screens/raw

# 진짜 Jev 판단으로(과금됨, TYPESAFE_API_KEY 필요)
.venv/bin/python -m tft_advisor --screenshot "tests/fixtures/screens/raw/unnamed-2.png" --jev live

# 통계 전용(Jev 호출 없음)
.venv/bin/python -m tft_advisor --screenshot tests/fixtures/screens/raw --no-jev

# 실시간 + 오버레이 (인자 없이도 같다)
.venv/bin/python -m tft_advisor --live

# 실시간, 오버레이 없이 콘솔에만
.venv/bin/python -m tft_advisor --live --no-overlay

# 화면 크기가 자동 판별되지 않을 때(16:10 실캡처 기준 예시)
.venv/bin/python -m tft_advisor --live --aspect 16:10
.venv/bin/python -m tft_advisor --live --resolution 1920x1080 --profile set18_16x9

# 인식 결과(JSON) + ROI를 그린 PNG를 logs/debug/ 에 남긴다
.venv/bin/python -m tft_advisor --screenshot tests/fixtures/screens/raw --debug
```

| 플래그 | 뜻 |
|---|---|
| `--screenshot PATH` | 파일 1장 또는 폴더. `--live`와 배타 |
| `--live` | 실시간(**인자를 주지 않으면 기본**) |
| `--jev {mock,live,off}` | 생략 = 설정 `[advisor] jev_backend`(기본 `mock`) |
| `--no-jev` | `--jev off`와 같다(우선순위 최상). Phase 2에서 죽어 있던 플래그를 이번에 연결했다 |
| `--profile`/`--aspect`/`--resolution` | `[vision]`의 같은 키를 덮어쓴다. 검증은 설정과 동일(비율·해상도 불일치는 거부) |
| `--no-overlay` | 콘솔 출력만 |
| `--debug` | 로그 DEBUG + `{log_dir}/debug/`에 GameState JSON·ROI PNG |
| `--config DIR` | 설정 디렉터리 |
| 종료 코드 | 0 정상 / 2 입력 경로 없음 / 3 미구현 / 130 Ctrl+C |

### 출력 예 (실제, `--screenshot`, 닉네임 없음)

```
# TFT Advisor — 스크린샷 모드 · 이미지 6장 · Jev mock · 패치 18.2b

=== unnamed-1.png  1279x797  (인식 526ms · 추천 6ms) ===
준비  스테이지 2-3  레벨 4 (0/10)  골드 19  1연승  체력 94
상점 확률: 55/30/15/0/0

[목표 덱] advisor 순서 (점수로 재정렬하지 않음) — Jev 사용
  1. 처형자 카직스  적합도 0.55  캐리 카직스  운영 lvl 7
     보유/부족: 보드 미인식
     아이템: 리치베인(부족) / 밤의 끝자락(부족) / 정의의 손길(부족)
     다음 빌드업(레벨 5): 카직스
  2. 주문술사 베이가  적합도 0.48 …
[상점]
  1. [보류] 자야 0.43 · 빌드업 — 지금 0.19 · 경로 1.00
  2. [구매] 카르마 0.57 · 지금 전력 — 지금 0.39 · 경로 1.00
  …
[재료 우선순위] B.F. 대검, 연습용 장갑, 쓸데없이 큰 지팡이, …
--- 인식 품질 ---
신뢰도: screen_mode 0.70, 경험치 0.97, 골드 0.84, 레벨 0.97, 상점 0.99, …
경고: 미인식: 보드 / 낮은 신뢰도: 연승/연패 0.50
```

---

## 4. 오버레이

### 4.1 표시 내용
- 머리글: 화면·스테이지·레벨(XP)·골드·연승·체력
- **목표 덱 1~3개**: 이름 · 적합도 · 캐리 · 운영(levelling) / 보유·부족 유닛 / 아이템 준비(보유·조합가능·부족). **advisor 순서 그대로**, 표시 개수는 `[ui] max_target_comps`
- 상점: 칸별 `[구매]`(초록) / `[보류]` + 점수 + 이유 태그(지금 전력·최종 덱·빌드업·2성 가능)
- 증강: 증강 선택 화면에서만. 추천 픽에 ★(노랑)
- 아이템: 조합 제안(재료 → 보유자) / "재료 보관 권장"
- 재료 우선순위(캐러셀용)
- 상태줄: `패치 18.2b · mock · Jev 사용 · 3초 전 · 8ms · 잠금(클릭 통과) · ⚠ 미인식: 보드 / 낮은 신뢰도: 연승/연패 0.50`
  - Jev 표시는 `jev_used`/`fallback_reason`을 한국어로 옮긴다. **캐러셀 통계 경로는 "Jev 미사용"이 아니라 "캐러셀(통계)"** 로 보인다(jev 전달 3).

### 4.2 조작

| 동작 | 트레이 메뉴 | 단축키(창이 잠금 해제일 때만) |
|---|---|---|
| 표시/숨기기 | ✓ | `Ctrl+Shift+O` |
| 이동 잠금/해제 | ✓ | `Ctrl+Shift+L` |
| 위치 저장 | ✓ | `Ctrl+S` |
| 불투명도 ±0.05 | ✓ | `Ctrl+Shift+↑` / `↓` |
| 종료 | ✓ | `Ctrl+Q` |

- **전역 단축키(게임에 포커스가 있을 때 먹는 키)는 만들지 않았다.** 키보드 후킹은 CLAUDE.md 고정 제약에 걸린다. 게임 중 조작은 **트레이 아이콘 메뉴**로 한다.
- 잠금 상태(=클릭 통과)에서는 창이 마우스·키보드를 전혀 받지 않는다(그게 목적이다). 옮기려면 트레이에서 "이동 잠금 해제" → 드래그 → "위치 저장".
- 트레이를 쓸 수 없는 환경이면 잠금을 해제하고 **창을 우클릭**하면 같은 메뉴가 나온다.
- 저장 위치는 `_state/overlay.json`(픽셀 좌표). 설정 파일은 앱이 고치지 않는다.

### 4.3 플랫폼 (Mac / Windows 둘 다 대상)

| 항목 | 구현 | 확인 |
|---|---|---|
| 항상 위 | `Qt.WindowStaysOnTopHint` | 공통 |
| 반투명 | `WA_TranslucentBackground` + `setWindowOpacity` | 공통 |
| 클릭 통과 | `Qt.WindowTransparentForInput` + `WA_TransparentForMouseEvents` | macOS에서 실측(`WindowEffect(applied=True, method='qt')`). Qt가 macOS에서는 `NSWindow.ignoresMouseEvents`, Windows에서는 `WS_EX_TRANSPARENT`로 번역한다 |
| Windows 보강 | ctypes `SetWindowLongW(GWL_EXSTYLE, WS_EX_LAYERED\|WS_EX_TRANSPARENT)` | **Windows 실기 미검증**(이 Mac에서는 코드 경로가 돌지 않는다) |
| 대체 동작 | 클릭 통과 실패 시 `applied=False` → 자동으로 **잠금 해제(일반 드래그 창)** 로 내려가고 상태줄에 사유 표시 | `platform_window.click_through_note()` |

- 전용 전체화면(exclusive fullscreen)에서는 오버레이가 뜨지 않고 캡처도 검게 나온다 → **테두리 없는 창모드** 권장(vision 보고와 같은 제약).
- macOS는 화면 기록 권한(시스템 설정 → 개인정보 보호)이 있어야 mss 캡처가 동작한다.
- pyobjc·전역 후킹 라이브러리는 **넣지 않았다**(의존성·정책 양쪽 이유).

---

## 5. 설정 키 (신규)

| 키 | 기본 | 범위 | 읽는 곳 |
|---|---|---|---|
| `[app] state_dir` | `"_state"` | 경로(상대면 프로젝트 루트 기준) | `session.session_path`, 오버레이 위치 파일 |
| `[ui] backend` | `"auto"` | `auto｜tkinter｜pyside6｜console` (**"console" 추가**) | `live.choose_ui`. `tkinter`는 미구현 → 경고 후 콘솔 |
| `[overlay] enabled` | `true` | bool | `live.choose_ui` |
| `[overlay] anchor` | `"top_right"` | `top_left｜top_right｜bottom_left｜bottom_right` | `OverlayWindow.place` |
| `[overlay] x` / `y` | 24 / 24 | int ≥ 0 (anchor로부터 px) | 〃 |
| `[overlay] width` | 380 | 200~1600 px | 창 너비 |
| `[overlay] scale` | 1.0 | 0.5~3.0 | 글꼴·여백 배율 |
| `[overlay] opacity` | 없음 | (0,1] | 없으면 `[ui] opacity`(0.85) |
| `[overlay] click_through` | 없음 | bool | 없으면 `[ui] click_through`(true) |
| `[overlay] locked` | `true` | bool | true면 클릭 통과·이동 불가 |
| `[overlay] always_on_top` | `true` | bool | |
| `[overlay] screen` | 0 | int ≥ 0 | 여러 모니터일 때 Qt 화면 번호 |
| `[overlay] remember_position` | `true` | bool | `_state/overlay.json` 우선 사용 |

- 헬퍼: `Settings.overlay_opacity()`, `Settings.overlay_click_through()`(= `[overlay]` > `[ui]`).
- 기존 키는 그대로다. Phase 4에서 실제로 소비하기 시작한 키: `capture.monitor`, `vision.capture_fps`, `vision.traits_every_s`, `vision.change_*`, `vision.content_box*`, `ui.max_target_comps`, `ui.opacity`, `ui.click_through`, `app.log_dir`(`--debug`).
- `logging.recommendation_log` / `logging.save_frame_on_error`는 **아직 소비하지 않는다**(§9).

---

## 6. 테스트

```
$ .venv/bin/python -m pytest -rxs
SKIPPED [3] tests/advisor/test_advisor_live.py: live Jev 호출: TFT_LIVE_JEV=1 로 실행
647 passed, 3 skipped in 115.97s
```
(이전 기준선 559 passed, 3 skipped → **+88**. `pyproject.toml addopts`에 이미 `-q`가 있어 따로 주지 않았다.)

| 파일 | 건수 | 내용 |
|---|---|---|
| `tests/app/test_session.py` | 17 | 병합 4규칙(유지·버림·화면 밖 묶음·일회성 필드), 신뢰도·출처 승계, 구매 추적(새로고침 제외), **증강을 추정하지 않음**, 수동/vision 우선순위, 영속(왕복·오래된 파일·깨진 파일·reset) |
| `tests/app/test_loop.py` | 16 | 변화 없음 → 인식 0회, 부분 묶음 전달, `stage` 변화 → 전체 인식, `traits_every_s` 주기, 화면 모드 3종×유지 / 2종×초기화, 프레임 간 병합, 인식 예외 → `kind="error"`(죽지 않음), `run()` 종료·정지 이벤트, ThreadAdviceRunner 최신값만, **mock에서 소켓 0회** |
| `tests/app/test_report.py` | 13 | **점수 재정렬 금지**, 한국어 라벨, 구매/보류 표시, ★ 픽, 캐러셀 vs 실패 구분, 인식 경고, 상태줄, 마크업 제거 |
| `tests/app/test_overlay.py` | 14 | offscreen 생성, Recommendation 표시·순서, `max_target_comps`, 상태줄, LoopUpdate 4종, 잠금 토글, 위치 저장/복원, 불투명도 클램프, 메뉴 항목, 플랫폼 헬퍼 |
| `tests/app/test_screenshot_mode.py` | 12 | **실캡처 7장(추적) 각각 + 사용자 raw 6장 폴더** end-to-end, 없는 경로, 폴더 수집, `--debug` 덤프, 깨진 파일이 배치를 멈추지 않음 |
| `tests/app/test_live_mode.py` | 6 | 콘솔 모드 전체 실행(실제 `ChangeDetector`), 세션 파일 기록, `off` 백엔드 표시, `build()` 스레드 실행기, 워밍업 예외 흡수, game_over 초기화 |
| `tests/app/test_cli.py` | 10 | 기본 모드, `--jev`/`--no-jev` 매핑, 배타, 화면 크기 덮어쓰기(+거부), 라우팅, `choose_ui` |

- raw 캡처 테스트는 디렉터리가 없으면 skip한다(gitignore라 다른 체크아웃에는 없다). 기대값은 **고정하지 않았다**(인식·통계가 바뀌면 값이 달라진다) — "죽지 않고 한국어 요약을 낸다"만 본다.
- 테스트 중 live Jev 호출 0회. `tests/app`은 `_state/`를 건드리지 않는다(tmp_path).

### 수동 스모크
- `--screenshot`(raw 6장, mock): 6장 모두 요약 출력. 인식 322~589ms, 추천 4~6ms.
- `--screenshot ... --jev live`: **1회 호출 성공**. mock과 다른 픽이 나왔다(증강 1위가 "작은 털뭉치 친구", 덱 순서도 다름) — live 경로가 실제로 붙어 있다는 확인.
- 콘솔 실시간(raw 리플레이 소스): 6장×3프레임 → 화면 전환·병합·추천 정상.
- 오버레이 실시간(offscreen, 리플레이): 캡처 스레드 + 추천 스레드 + Qt 시그널 경로 정상, 상태줄에 "3초 전 · 잠금(클릭 통과)" 표시.
- 오버레이 렌더 실물 확인(macOS cocoa, `QWidget.grab()`): 380x353px, 한국어 정상 표시.

### 성능 (이 Intel Mac, 기본 묶음)
| 구간 | 실측 |
|---|---|
| 전체 인식(스크린샷 1장) | 322~589 ms |
| 추천(mock, 실제 통계 57덱) | 3~6 ms |
| 오버레이 갱신 | 1 ms 미만 |
- 목표(인식 300ms 미만)는 여전히 미달이다. 다만 루프는 **바뀐 묶음만** 읽으므로 평상시에는 이보다 싸다. **Windows onnxruntime 수치는 아직 없다**(사용자 기기에서 측정 필요, QA #14 이월).

---

## 7. 전투/준비 오분류(vision 미해결)를 다루는 방법

vision은 전투 화면을 `planning`으로, 상대 보드 관전 중 전투를 `unknown`으로 낸다(05 보고 §7, 같은 라운드의 준비/전투 쌍 캡처가 있어야 고친다). 루프는 이렇게 버틴다.

1. **애매한 화면에서 상태를 버리지 않는다.** `unknown`에서는 어떤 묶음도 "읽었지만 비었다"로 치지 않으므로 직전 HUD·상점·아이템이 그대로 유지된다(§2.3 규칙 3).
2. **`unknown`은 새 추천을 만들지 않는다.** 직전 추천을 그대로 보여 준다(`kind="kept"`) → 관전 화면에서 추천이 흔들리지 않는다.
3. **전투를 planning으로 봐도 해롭지 않다.** 전투 중에도 상점 구매는 가능하고, 자원 시그니처가 같으면 advisor가 히스테리시스로 같은 덱을 유지한다.
4. **세션 초기화는 `loading`/`game_over`에서만** 한다. 오분류로 세션이 날아가지 않는다.
5. 변화 감지가 한 겹 더 막아 준다: 전투 애니메이션 중에는 ROI 서명이 계속 흔들려 `change_stable_frames` 조건을 넘지 못하므로 인식 자체가 덜 돈다.

---

## 8. 다른 에이전트 / QA 전달

- **vision-engineer**: `Recognizer.recognize(groups=…)`·`content_for()`·`profile_for()`·`ChangeDetector.from_cfg()`를 앱이 그대로 쓴다(수정 없음). 앱이 의존하는 계약: ① 요청하지 않은 묶음의 필드는 `None`, ② 어떤 화면에서 어떤 묶음을 읽는지가 `app/session.GROUP_READ_MODES`와 일치. **모드 분기를 바꾸면 그 표도 같이 바꿔야 한다**(테스트가 묶음 누락만 잡고, 모드 변경은 못 잡는다). HUD 보유 증강 판독이 들어오면 `augments_owned`만 채우면 앱이 바로 쓴다.
- **jev-strategist**: 반환 규칙(§2절)을 그대로 구현했다. `Advisor.advise()`는 **추천 전용 스레드 하나에 고정**해 호출한다. `debug.mode == "carousel"`을 UI 표시에 쓴다(요청 3 반영).
- **stats-researcher**: 오버레이 상태줄의 패치는 `advisor.stats.meta.patch`에서 읽는다. `items.json` name_ko 마크업 22건은 앱이 표시 단계에서 떼어 내고 있다(`names.strip_markup`) — `static_extract` 쪽에서 제거되면 그대로 무해하게 동작한다.
- **qa-validator**: 새 경계면은 ① `merge_state` 규칙 ↔ `Recognizer` 모드 분기, ② `RESET_MODES`/`KEEP_MODES` ↔ advisor 반환 규칙, ③ UI 표시 순서 ↔ `target_comps` 순서(3w-c DEFERRED 항목을 **테스트로 고정 완료**: `test_target_comps_are_never_reordered_by_score`, `test_shows_recommendation_in_advisor_order`)다.

---

## 9. 남은 것 / 막힌 것

### 사용자 입력이 필요해 막힌 것
1. **준비/전투 구별**(vision): 같은 라운드의 준비 + 전투 캡처 한 쌍이 필요하다. 지금은 §7의 보수적 동작으로 버틴다.
2. **HUD 보유 증강 판독**(vision, 캡처 #6): 이게 없으면 `augments_owned`는 수동 입력 전까지 계속 `None`이다.
3. **Windows 실기 검증**: 클릭 통과 ctypes 경로, onnxruntime 인식 속도, 전체화면/창모드 동작. 코드 경로는 있으나 실행해 보지 못했다.

### 이번에 하지 않은 것(Phase 5 후보)
4. **보유 증강 수동 입력 UI**: 지금은 `SessionTracker.set_augments_owned()` API뿐이다. 오버레이에 증강 3개를 클릭으로 넣는 작은 패널이 필요하다(클릭을 받으려면 잠금 해제 상태여야 한다).
5. **보드/벤치 인식이 없다** → 목표 덱의 "보유/부족 유닛"이 대부분 "보드 미인식"으로 나온다. 추천 품질에 가장 크게 남은 구멍이다(vision 다음 과제).
6. `logging.recommendation_log`(추천 JSONL 적재)와 `save_frame_on_error`(실패 프레임 저장)를 아직 연결하지 않았다. 튜닝 데이터 수집용으로 Phase 5에 넣는 것을 제안한다.
7. 리플레이 소스(파일 스트림을 실시간처럼 재생)는 테스트 안의 헬퍼로만 있다. QA가 자주 쓰면 `--replay DIR` 플래그로 승격할 만하다.
8. openvino-telemetry 안내(QA #10 이월), `static_data.py:41` 낡은 주석(QA R5) — 코드 동작과 무관해 이번엔 손대지 않았다.
9. 오버레이 글꼴은 시스템 기본(Apple SD Gothic Neo / 맑은 고딕)을 쓴다. 게임 위 가독성 피드백을 받아 `scale`·`opacity` 기본값을 조정하면 된다.
