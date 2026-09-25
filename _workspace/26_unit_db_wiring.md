# 26 — 유닛 이름·사진 DB 연결 (vision 23·25 보고의 app 쪽) · app-integrator

날짜: 2026-09-23 · 커밋 안 함 · vision/units.py·vision/unit_db.py·advisor/ 무수정

## 1. 장부 → 이름 힌트, 새 판 초기화
- `SessionTracker.owned_champions()`(장부 `data.units.copies` 중 1장 이상인 챔피언 ID) 추가.
- `LiveLoop._feed_unit_namer()`: `tracker.observe()` 직후(캡처 스레드) `recognizer.unit_namer.set_hints(owned)`.
- `LiveLoop._do_reset()`: 세션 초기화 뒤 `recognizer.unit_namer.reset()`.
- 어떤 예외도 로그만 남기고 루프를 멈추지 않는다. 인식기에 `unit_namer`가 없으면(`[vision] unit_names=false`, 가짜 인식기) 건너뛴다.

## 2. 장부 구매 이벤트 → 사진 수집기
- `SessionTracker.last_events`: 이번 `observe()`가 장부에 반영한 `LedgerEvent` 목록(매 observe마다 새로 채운다 → 같은 구매를 두 번 알리지 않는다).
- `kind == "buy"`(상점 칸 + 골드로 추론한 구매)마다 `unit_namer.collector.note_purchase(ev.unit_id, ev.at)`. `collector`가 None이면 건너뛴다.
  `ev.at`은 세션 시계 = `time.time()`(수집기 기본과 같은 시계).

## 3. "유닛 사진 검토" 진입점 (UI 스레드, 모달 아님)
- `app/live.unit_review_opener(recognizer)` → `open_review_window(recognizer.static, on_changed=recognizer.unit_namer.request_reload)`.
  이름 인식이 꺼져 있으면 None(메뉴·버튼이 나오지 않는다).
- 오버레이 트레이/우클릭 메뉴 **"유닛 사진 검토…"**(`OverlayWindow.open_unit_review`): 창 참조를 `review_window`에 들고,
  이미 열려 있으면 새로 열지 않고 앞으로 가져온다. 여는 데 실패하면 상태줄에 알린다. 루프는 계속 돈다.
- 인식 확인 창 머리줄 **[유닛 사진 검토]** 버튼 + 우클릭 메뉴 항목(같은 함수).
- 창을 닫을 때 바뀐 것이 있으면 `request_reload` → 인식 스레드가 다음 프레임에 승인 사진을 다시 읽는다(vision 쪽 계약).

## 4. 설정 키
`config.py VisionCfg`: `unit_pending_weight: float = 0.0 (0~1)`, `unit_purchase_autoapprove: bool = False`.
`config/settings.toml [vision]`에 설명과 함께 추가, `unit_autolearn` 설명을 "검토 대기(_pending)에 모은다, 승인 사진만 이름에 쓰인다"로 바꿨다.
(인식기는 이미 `getattr(cfg, ...)`로 읽고 있어 그대로 연결된다.)

## 5. 인식 확인 창
- `BoardRead.board_common` 중 칸에 안 붙은 챔피언 + `missed_board`를 한 줄로:
  "보드에 확인된 챔피언(자리 미상): 요릭 · 놓친 유닛 1기" (창: 보드 표 아래 노란 줄, 콘솔: `[보드]` 아래).
- 뒷받침 없는 이름은 이름 뒤에 **"(추정)"**(창: 노란색). 판정 `recog_view.is_guess(slot)`: 칸에 `corroborated` 속성이 있으면
  그 값, 지금 `UnitSlot`에는 없으므로 **`name_source == "library"` 이고 `unit_conf <= 0.75`**(vision `LIB_STRICT_CONF_CAP`).
  힌트로 뒷받침된 라이브러리 이름은 상한이 0.90이라 보통 추정으로 표시되지 않는다. 드물게 신뢰도가 0.75 이하인 뒷받침 이름도
  "(추정)"으로 보일 수 있다. **vision 제안: `UnitSlot`에 `corroborated`를 실어 주면 그 값을 그대로 쓴다(코드는 이미 준비됨).**

## 6. 스크린샷 모드
`app/screenshot.single_frame_names()` — 인식기를 만든 뒤(또는 넘겨받은 인식기에) `unit_namer.agree_frames = 1`.
임계·신뢰도 상한은 그대로다. 인식 확인 결과에는 "(추정)"으로 보인다.

## 7. vision 23 보고 §6-1(`--screenshot` content_box)
24 보고 §3의 수정으로 해결된다: content_box는 이미지 크기가 셋업이 기록한 캡처 프레임 크기(`_state/setup.json`)와 같을 때만
(모르면 잘린 크기가 `resolution`일 때만) 적용되고, 아니면 `content_for`의 자동 탐지(여러 모니터 이어 붙인 캡처·레터박스)를 쓴다.
확인: **사용자 로컬 설정 그대로** `python -m tft_advisor --screenshot "tests/fixtures/screens/raw/5-5 전투 전.png"` → 4480x1440 캡처에서
content_box를 적용하지 않고 "준비 · 스테이지 5-5 · 레벨 9"로 읽음. `test_recog_view::test_qa_raw_view_*` 2건도 통과한다.

## 테스트
- 새 `tests/app/test_unit_db_wiring.py` 12개: 힌트·구매 전달(경험치 구매 제외, 중복 없음), `owned_champions`/`last_events`,
  새 판 reset, 이름 인식 예외가 루프를 막지 않음, `is_guess` 규칙, 뷰의 "(추정)"·board_common 줄, 빈 경우, 창 HTML,
  검토 창 여는 함수(static·request_reload), 오버레이 메뉴·인식 확인 창 버튼(다시 누르면 앞으로, 실패 안내), 스크린샷 agree_frames, 설정 키.
- 전체(`PYTHONIOENCODING=utf-8`): 실패는 알려진 4건뿐 — test_api_key 상태 힌트, test_setup 권한 상자, test_credentials 0600 두 개.

## 파일
`src/tft_advisor/app/{loop,session,live,overlay,recog_window,recog_view,screenshot}.py`, `src/tft_advisor/config.py`,
`config/settings.toml`, `tests/app/test_unit_db_wiring.py`
