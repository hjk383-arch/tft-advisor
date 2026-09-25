# 24 — 상점 재평가(전투 중 포함) · 보드 배치 표시 · 스크린샷 이중 크롭 · 종료 버튼 · 이 PC 전용 설정 층 · app-integrator

날짜: 2026-09-23 · 커밋 안 함 · 22(게임 창 자동 찾기)에 이어서 한 작업

## 1. 상점이 바뀌면 상점 추천을 다시 계산 (전투 중 새로고침·라운드 시작)

### 동작
- 준비 단계(PLANNING): 상점 ROI가 바뀌면 원래대로 전체 추천을 다시 만든다. **캐시 적중이 아니다** — advisor의
  `state_hash`는 Jev state의 `shop`을 포함한다(실제 mock Advisor로 상점만 다른 두 상태 → 해시 다름·상점 추천이 새 상점 기준, 테스트).
- 전투(KEEP_MODES: combat / item_select / unknown): 목표 덱은 직전 추천 그대로(계약). 단 **이번 프레임에 상점을 읽었고**
  (`"shop"` 묶음, 신뢰도 ≥ `state_min_confidence`) **추천을 만든 상점에 없던 상품이 한 칸이라도 보이면** 상점만 다시 평가한다.
  - 산 칸(빈 칸)·못 읽은 칸은 새 상품이 아니다 → 재평가 없음(기존 kept_view가 산 칸만 뺀다).
  - 같은 상점은 한 번만 요청(`LiveLoop._shop_submitted` = 마지막으로 추천·재평가를 요청한 상점 ID 튜플).
  - 추천을 만든 상점을 모르면(세션 복원 직후) 추천의 상점 칸(`rec.shop`)과 비교(`report.shop_needs_rescore`).
- 실행: 추천 스레드(`ThreadAdviceRunner.submit_shop`) — UI·캡처 스레드를 막지 않는다. 전체 추천이 대기 중이면 재평가는 버린다
  (전체 추천이 상점도 계산한다), 전체 추천을 넣으면 대기 중인 재평가를 지운다.
- 결과(`LiveLoop._on_shop_advice`): 목표 덱을 직전 추천 것으로 **강제 고정**하고 상점 칸만 바꿔 즉시 `advice` 갱신을 보낸다.
  `KeptInfo.shop_fresh=True` → 오버레이/콘솔 [상점] 머리가 "새 상점 기준(전투 중)"으로 바뀌고 [구매] 줄이 다시 밝게 표시된다.
  다음 전체 추천이 오면 `shop_fresh`는 풀린다.

### advisor API — 어댑터
작업 시작 때는 `Advisor.rescore_shop`이 없어서 `app/loop.py rescore_shop(advisor, state, previous)` 어댑터를 두었다.
**작업 끝에 확인: jev-strategist가 같은 이름·시그니처로 `Advisor.rescore_shop(state, previous=None)`을 추가했다(21 §7, Jev 새 호출 없음,
1~2ms)** → 루프는 그것을 그대로 부른다. 대체 경로는 그 메서드가 없는 advisor(테스트 가짜 등)용으로만 남는다:
- advisor에 `rescore_shop(state, previous) -> Recommendation | None`가 있으면 그것을 부른다(**jev-strategist가 이 이름·시그니처로
  추가하면 루프 수정 없이 바로 쓰인다**).
- 없으면: 상태를 PLANNING으로 바꿔 `advise()` 한 번 → 결과의 `shop`만 직전 추천에 옮긴다. 단점(보고): advisor 세션(히스테리시스·
  직전 표시)을 한 번 갱신하고, live 백엔드면 Jev를 한 번 부른다(과금). 목표 덱은 루프가 어쨌든 고정한다.

## 2. [보드 배치] 표시
`Recommendation.board_plan`(jev-strategist가 contracts에 추가, `report.board_plan_lines`)을 오버레이 [목표 덱] 아래에 그린다
(기준 덱 이름·칸 수, 보드 라인업, 교체 — 교체가 있으면 노란색, 벤치). 콘솔(`format_report`)은 jev-strategist가 이미 넣었다.

## 3. `--screenshot` 이중 크롭 수정
`[vision] content_box`는 **실시간 캡처 프레임(모니터 전체)** 기준인데, 게임 영역만 잘라 저장한 이미지에도 적용돼 두 번 잘렸다.
`app/screenshot.content_box_applies(size, settings, frame_size)`:
1. `_state/setup.json`의 `detected.frame_size`(셋업이 기록한 캡처 크기)를 알면 → 이미지 크기가 같을 때만 적용.
2. 모르면 content_box로 자른 크기가 `resolution`(±2px)일 때만 적용.
3. 둘 다 모르면(해상도 auto) 예전처럼 적용.
적용하지 않을 때는 content_box만 뺀 설정으로 `content_for`를 불러(레터박스·여러 모니터 캡처 자동 탐지는 유지) 명시 영역을 넘긴다.

## 4. 보이는 종료 버튼
- **"✕ 종료" 손잡이**(`overlay.QuitHandle`): 오버레이 오른쪽 위 바로 위(화면 밖이면 아래)에 붙는 **별도의 작은 창** — 항상 위,
  테두리 없음, 포커스를 가져가지 않음, 클릭 통과 아님. 오버레이가 잠금(클릭 통과)이어도 누를 수 있다. 창 일부만 입력을 받게 하는
  방식(WM_NCHITTEST)보다 플랫폼 차이 없이 튼튼해서 이쪽을 골랐다. 오버레이를 따라 움직이고 표시/숨기기를 함께 한다.
- **인식 확인 창 [앱 종료]** 버튼(머리줄).
- 둘 다 `ConfirmButton` — 첫 클릭 "정말 종료? 다시 클릭", 3초 안에 다시 눌러야 끝난다(실수 방지).
- 모든 종료 경로(트레이 "종료"·Ctrl+Q·✕ 손잡이·[앱 종료])는 `OverlayWindow.quit()` → `app.quit()` → `aboutToQuit`의 `shutdown()`
  한 곳(루프 정지 → `loop.close()` = 추천 스레드 정지·캡처 닫기·세션 저장 → 창 숨김). `shutdown`은 두 번 불려도 한 번만 돈다.
- 시작 안내 1회: 로그 + 오버레이 상태줄 30초 "종료: 트레이 아이콘 오른쪽 클릭 → 종료, 또는 ✕ 버튼".

## 5. 이 PC 전용 설정 층 `config/settings.local.toml`
- `config.load_settings()` = `settings.toml`(공용 기본값, 공개 저장소) 위에 `settings.local.toml`을 섹션·키 단위로 덮어 읽는다
  (`merge_layers`). `.gitignore`: `config/*.local.toml`, `config/*.tmp`.
- `setup.save_settings()`는 기본으로 **로컬 층에** 쓴다(주석 보존·.bak·합친 결과 검증). 설정 화면·"게임 화면 다시 찾기"·따라가기·
  Jev 트레이 토글·인식 확인 창 토글이 모두 이 함수를 거친다. `path=`를 주면 그 파일에 직접 쓴다(옛 동작).
  로컬 층에서 `content_box` None은 `content_box = []`(프레임 전체라는 값)으로 적는다. 첫 저장 때 설명 머리 주석을 넣는다.
- 마이그레이션: `config/settings.toml`을 HEAD로 되돌리고(+ `follow_game_window`/`follow_interval_s` 설명, 머리 주석 갱신),
  사용자 값(monitor 1, resolution 1920x1080, aspect/profile auto, content_box [0.220930, 0.108333, 0.779070, 0.858333],
  follow_game_window, jev_backend live, overlay opacity 0.8)을 `config/settings.local.toml`로 옮겼다. **합친 결과가 옮기기 전과
  같음을 확인**했다 → 다시 시작해도 지금 설정 그대로 동작한다.
- 테스트 격리: `tests/conftest.py`가 import 때 `TFT_ADVISOR_LOCAL_SETTINGS=0`(기본 config/의 로컬 층 무시)과
  `TFT_ADVISOR_WINDOW_DETECT=0`을 정한다(세션 범위 픽스처가 autouse보다 먼저 만들어지므로 import 때). 임시 config 복사
  (`copytree`)는 `*.local.toml`·`*.bak`·`*.tmp`를 빼고 복사한다. 저장 검증 테스트는 로컬 파일을 본다.
- 주의: 지금 떠 있는 앱(옛 코드)에서 트레이 토글을 누르면 옛 경로대로 `settings.toml`에 쓴다 — 다시 시작하면 해결.

## 테스트
- 새: `tests/app/test_shop_refresh.py`(13), `tests/app/test_screenshot_crop.py`(4), `tests/app/test_quit_controls.py`(5),
  `tests/app/test_game_window.py`(42, 22 보고).
- 고침: test_setup(저장 위치·백업·명시 path·로컬 층 격리), test_jev_toggle·test_api_key·test_credentials(로컬 파일 확인),
  test_phase3_final_qa(CaptureCfg 새 키 허용), test_game_window(persist·follow 기준값).
- 전체(`PYTHONIOENCODING=utf-8`): 실패는 예상된 4개만 — test_api_key 상태 힌트, test_setup 권한 상자, test_credentials 0600 두 개.
  (이전에 로컬 값 때문에 깨지던 config 기본값·qa08 monitor·recog_view raw 캡처·jev 기본 mock 테스트가 모두 통과)

## 파일
- 코드: `src/tft_advisor/app/{loop,report,overlay,recog_window,screenshot,setup,live}.py`, `src/tft_advisor/config.py`
- 설정: `config/settings.toml`(공용), `config/settings.local.toml`(새, gitignore), `.gitignore`
- advisor/·vision/units.py 무수정
