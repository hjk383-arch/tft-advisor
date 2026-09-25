# 22 — 게임 창 자동 찾기 ("게임 화면 다시 찾기") · app-integrator

날짜: 2026-09-23 · 커밋 안 함

## 사용자 요청
"이렇게 자동으로 다시 화면인식할수있는 버튼을 넣어줘" — 메인 세션이 손으로 한 일(창 모드 TFT 창의 클라이언트 영역을
user32로 읽어 `content_box`를 계산하고 settings.toml을 고친 것)을 기능으로 만든다.

## 무엇을 만들었나

### `src/tft_advisor/app/game_window.py` (새 파일, Qt 없음)
- `list_windows()` — user32 `EnumWindows` + `GetWindowTextW`/`GetClassNameW`/`GetClientRect`/`ClientToScreen`/
  `IsIconic`/`GetWindowThreadProcessId`, DWM cloaked 검사. 호출하는 동안만 **스레드** DPI 문맥을 per-monitor v2로
  (`SetThreadDpiAwarenessContext(-4)`, 끝나면 되돌림) → 물리 픽셀 = mss 좌표(mss도 프로세스를 per-monitor로 둔다).
- `select_game_window()` — 제외: 우리 프로세스 창, 제목이 "TFT Advisor…"인 창, League 클라이언트(`RCLIENT` 또는
  제목 "League of Legends"/"Riot Client"), 숨김/cloaked. 후보: 제목(공백 정리·대소문자 무시)이
  `tft` / `teamfight tactics` / `league of legends (tm) client` 이거나 클래스 `RiotWindowClass`.
  `UnrealWindow` 클래스만으로는 후보가 안 된다(다른 언리얼 게임). 순위: 최소화 아님 > 게임 클래스 > 넓이.
  **실측: 지금 TFT 창은 제목 `'TFT  '`, 클래스 `UnrealWindow`** (RiotWindowClass 아님).
- `monitor_for_rect` (겹치는 넓이 최대), `clip_to_monitor`, `content_box_for` (소수 6자리, 모니터 전체면 None).
  사용자 배치 → 모니터 1, `[0.22093, 0.108333, 0.77907, 0.858333]`, 1920x1080 — 손으로 한 값과 같다(테스트).
- 검증(`find_game_window`) — 저장하지 않는 경우와 문구:
  - 못 찾음 "게임 창을 찾지 못했습니다 — TFT 게임이 실행 중인지 확인해 주세요…"
  - 최소화 "게임 창이 최소화되어 있습니다 — 게임 창을 화면에 띄운 뒤 다시 눌러 주세요."
  - 가림 "게임 창이 다른 창에 가려져 있습니다(약 N%) — …" : 창 위 6x4 격자 점마다 `WindowFromPoint`→`GetAncestor(GA_ROOT)`.
    게임·우리 프로세스가 아닌 창이 50% 이상이면 거절, 일부면 경고만. 클릭 통과 창(NVIDIA 오버레이, 우리 오버레이)은 OS가 건너뛴다(실측 확인).
  - 너무 작음(< 640x360), 프로파일을 만들 수 없는 비율, 화면 밖, 검은 캡처(권한/전용 전체화면 안내).
  - 픽셀 확인: 창 영역만 잘라 `default_screen_score`(스테이지 막대) + 인식기가 있으면 `screen_score`(스테이지 OCR).
    OCR로 못 읽어도(로비·로딩) 위치는 창 기준이라 **적용하고 경고**("게임 화면(스테이지 글자)은 아직 확인하지 못했습니다…").
  - 어떤 예외도 밖으로 내지 않는다(status="error").
- 결과 `WindowDetection` — `message` 예: **"게임 창을 찾았습니다: 모니터 1, 1920x1080, 위치 (760,156)"**. 설정 화면이 그대로
  쓰는 `SetupDetection`(모든 모니터 목록 + 고른 모니터, `window_note`)을 함께 만든다.
- 저장 `persist()` — **화면 키만** 저장: `[capture] monitor`, `[vision] resolution/aspect/profile(auto)/content_box`
  (기존 `setup.save_settings` 경로: 주석 보존 + `.bak` + 검증) + `_state/setup.json`(`mark_setup_done`).
  `[advisor]`·`[overlay]`·`[ui]`는 건드리지 않는다. 실측 비율(16:9/16:10)이면 aspect="auto".
- `ScreenRedetector` — `LiveLoop.screen_hook`. `request(cb)`는 아무 스레드, 실제 작업은 **캡처 스레드**의 `tick()`
  (인식기·mss·변화 감지기가 그 스레드 것이라 잠금이 필요 없다). 이미 같은 설정이면(±1px) 저장·적용하지 않고
  "설정이 이미 이 위치와 같습니다."
  - 자동 따라가기: `[capture] follow_game_window = true`(기본), `follow_interval_s = 3.0`. 3초마다 창 목록만 보고(싸다),
    위치/크기가 바뀌면 0.5초 뒤 같은 위치인지 한 번 더 보고(끌어 옮기는 중간값 방지) 찾기+검증+적용+저장. 실패한 위치는
    30초 뒤에 다시 시도. 최소화 중에는 아무것도 안 한다. 로그: "게임 창 변화 → 캡처 영역을 다시 맞췄습니다: …".
    시작 직후 첫 확인에서 설정이 창과 다르면(예: 설정 화면이 모니터 전체로 잘못 저장) 자동으로 고친다.
  - `request_apply(settings)` — 설정 화면 [저장] 값을 재시작 없이 루프에 적용(트레이 "설정" 뒤).
- 비 Windows / `TFT_ADVISOR_WINDOW_DETECT=0` → "not_supported", 호출자는 픽셀 감지로.

### 실행 중 적용 — `app/loop.py`
- `LiveLoop.screen_hook` (매 step 전에 `tick(loop)`, 예외는 로그만), `LiveLoop.apply_screen(settings)`:
  settings의 capture/vision 교체, `source.set_monitor()`, 인식기 `cfg`·`profile_setting` 교체 + `_screen_cache`·`_panel_cache`
  비움, 변화 감지기 새로 만듦(다음 프레임 전체 인식), 특성 주기 초기화. 세션·장부·직전 추천은 유지. `screen_changes` 카운터.
- `vision/capture.py` `MssSource.set_monitor(monitor)` 추가(작은 메서드, 다음 grab에서 모니터 영역 재계산). vision/units.py·advisor/ 무수정.

### 버튼·진입점
- 오버레이 트레이/우클릭 메뉴 **"게임 화면 다시 찾기"** (실시간 캡처일 때만). 결과는 `redetected` 시그널로 UI 스레드 →
  상태줄 + 트레이 알림 + 인식 확인 창.
- 인식 확인 창(`recog_window.py`) 머리줄 **[게임 화면 다시 찾기]** 버튼 + 우클릭 메뉴 항목, 결과 한 줄(초록/빨강), 찾는 동안 버튼 잠금.
- 설정 화면(`setup_dialog.py`) **[🪟 게임 창 자동 찾기]** — 모니터·해상도·게임 화면 영역 칸을 채운다(저장은 [저장]).
  content_box 칸은 소수 6자리로 늘림(4자리면 3440px 폭에서 0.2px 오차).
- CLI **`python -m tft_advisor --redetect`** — 찾고 저장하고 끝(0 = 저장/이미 같음, 1 = 실패).
- `live.build()`가 실제 mss 캡처일 때만 redetector를 붙인다(`make_redetector`). 콘솔 모드는 따라가기 결과를 출력.
- 트레이 "설정" 저장 뒤 화면 설정도 재시작 없이 적용(`request_apply`) — 안내문 "바로 적용했습니다".

### 설정 감지 버그 수정 (창 모드인데 모니터 전체로 저장)
`setup.detect_live()`가 **게임 창을 먼저** 찾는다(`window_finder` 주입 가능). 찾고 검증되면 그 결과, 아니면 기존 픽셀 감지 +
메모 "게임 창으로 찾지 못해 화면 픽셀로 감지했습니다 — …". 설정 화면의 첫 감지·[자동 감지]·콘솔 설정 모두 이 경로.
요약 첫 줄에 창 결과가 나온다(`SetupDetection.window_note`).

### 설정
`config.py CaptureCfg`: `follow_game_window: bool = True`, `follow_interval_s: float = 3.0 (0.5~60)`.
`config/settings.toml [capture]`에 두 키 + 설명 주석 추가. **사용자의 content_box 등 기존 값은 그대로.**

## 안전
창 제목·클래스·위치·가림(WindowFromPoint)만 읽는다. OpenProcess/ReadProcessMemory/훅/SendInput 없음. 게임 창에 메시지를 보내지 않는다.

## 테스트
- 새 `tests/app/test_game_window.py` 42개: 창 선택/제외(자기 PID, "TFT Advisor", League 클라이언트, cloaked, 최소화 우선순위,
  UnrealWindow 단독 불가), 좌표·다중 모니터(음수 좌표, 걸침 클리핑, 전체화면=None), 가림 비율, 실패 5종 + 검은 캡처 + 예외 무발생,
  배율 캡처 크롭, OCR은 창 영역만 채점, detect_live 창 우선/폴백, persist(화면 키만·.bak·setup.json), 루프 적용(캡처 스레드
  step에서만, 두 번째는 "이미 같음"), apply_screen 캐시 초기화, MssSource.set_monitor, 따라가기(확인 대기·재시도 억제·최소화 무시),
  request_apply, CLI --redetect, Qt offscreen: 설정 화면 버튼·오버레이 메뉴·인식 확인 창 버튼 왕복.
- `tests/conftest.py`: `TFT_ADVISOR_WINDOW_DETECT=0` (테스트가 사용자의 진짜 게임 창을 보지 않게). 모든 저장 테스트는 임시 config 복사본.
- 실제 창 목록 확인: 모듈로 읽은 목록이 수동 결과와 일치(League 클라이언트·우리 창 제외). 확인 시점에는 게임이 끝나 TFT 창이 없어 "not_found" — 정상.

## 갱신 (같은 날, 24 보고와 함께)
- 저장 위치가 바뀌었다: 모든 화면 저장(다시 찾기·따라가기·설정 화면·트레이 토글)은 이제 **`config/settings.local.toml`**
  (이 PC 전용, gitignore)에 쓴다. 공용 `settings.toml`은 HEAD 기본값 + `follow_game_window`/`follow_interval_s` 설명만 추가.
  자세한 내용은 `_workspace/24_shop_refresh.md` §5.
- 사용자 값(모니터 1, 1920x1080, content_box [0.220930, 0.108333, 0.779070, 0.858333], jev live, overlay opacity 0.8)은
  settings.local.toml로 옮겼고, 합친 결과가 옮기기 전과 같음을 확인했다.

## 남은 것 / 주의
- `follow_game_window`가 켜져 있으면 사용자가 창을 옮길 때마다 settings.local.toml이 다시 쓰인다(.bak 1개 유지). 원치 않으면 false.
- 게임을 테두리 없는 전체 화면으로 띄우면 창 = 모니터 전체 → 로컬 층에 `content_box = []`(프레임 전체)로 적힌다.
- 지금 실행 중인 사용자 앱(메인 세션에서 띄운 것)은 옛 코드라 이 기능이 없다 — 다음 실행부터.
