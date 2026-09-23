# 08 QA: vision 07 라운드 검증 (1080p · 듀얼 모니터 · 화면 상태 · 보유 증강)

작성일: 2026-09-22 / 작성자: qa-validator / Windows 11, `.venv` Python 3.14, onnxruntime / 커밋 안 함
대상: `_workspace/07_vision_1080p_modes.md`와 커밋 안 된 vision/app 변경. 참고: `07_vision_draft_labels.md`, `07_user_answers.md`, `06_app-integrator_phase4.md` §2.3~2.4·§8
원본 캡처(`tests/fixtures/screens/raw/`, gitignore)는 이 보고서에 이미지나 플레이어 이름을 싣지 않았다.

## 요약: PASS 13 / FAIL 2(QA가 직접 고침) / WARN 7

- 보고서 수치를 **그대로 재현했다**. 13장 전 필드에서 오답 0, 미인식 0(screen_mode 13/13, 상점 칸 45/45, augments_owned 4/4). 방송 fixture도 05 기준선 그대로다(screen_mode 7/7, 상점 칸 24/25, 틀린 ID 0).
- **순환 채점 점검**: 실화면 아이템 템플릿(`items_screen/`, 같은 캡처에서 잘라낸 8개)을 모두 빼고 CDragon 아이콘만으로 돌려도 전 필드가 그대로다(items 12/12). 이 설정이 leave-one-out보다 엄격하다. 대신 아이템 신뢰도가 0.827~0.831로 내려가 수락 임계 0.80에 가깝다. 보유 증강은 처음부터 CDragon 템플릿만 쓰므로 템플릿 쪽 순환은 없다. 라벨은 같은 에이전트가 썼지만, QA가 캡처 2장을 직접 보고 HUD·상점·증강 칸 수·아이템 칸을 대조해 모두 맞음을 확인했다. 연패 부호와 증강 줄의 의미는 사용자가 확인했다(`07_user_answers.md`).
- **QA가 직접 고친 크래시 2건**: ① 세로 모니터가 낀 듀얼 캡처, ② 한 화면이 순수 검정 조각 때문에 쪼개지는 경우. 둘 다 `recognize()`가 `ValueError`로 죽었다. 여기에 확신 오답 1건(스테이지를 못 읽은 PvE 준비 화면이 `combat` 0.8)과 모니터 자동 선택의 작은 결함 2건도 고쳤다. 회귀 테스트 11개를 추가했다.
- **전체 pytest: 707 passed, 0 failed, 3 skipped**(live Jev). 전달받은 stats 실패 3건은 다른 에이전트가 처리해 지금은 통과한다. vision·app 쪽 새 실패는 없다.
- **가장 큰 WARN 두 가지**: (a) 전투가 실제로 `combat`으로 판별되면서 이제 전투 중(라운드의 절반쯤)에는 추천이 다시 계산되지 않는다. 그래서 전투 중에 산 유닛이 오버레이에 계속 "[구매]"로 남는다. (b) 준비 단계에 상대 보드를 관전하면 상대의 증강 줄을 내 보유 증강으로 읽어 수동 입력값까지 덮을 수 있다.

---

## 1. PASS/FAIL/WARN 표

| # | 항목 | 결과 | 근거 (파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | `READ_MODES` ↔ `GROUP_READ_MODES` | PASS | `vision/recognizer.py:54-69` = `app/session.py:55-68` (8개 묶음, 모드 집합이 같다). `test_group_read_modes_equal_vision_read_modes`와 `test_recognize_reads_groups_exactly_in_read_modes`(모드 8개)가 고정한다 | - | - |
| 2 | `RESET_MODES`/`KEEP_MODES` ↔ advisor 반환 규칙 | PASS | `session.py:69-71` ↔ `advisor/engine.py:141-146`(LOADING/GAME_OVER → reset·None, COMBAT/ITEM_SELECT/UNKNOWN → `session.last`). contracts의 ScreenMode 값만 쓰고 새 값은 만들지 않았다(악의 여단은 `item_select`, 사용자 OK) | - | - |
| 3 | 화면 순서별 병합 동작 | PASS | 새 테스트 `test_combat_frame_updates_shop_but_owned_augments_survive`: 전투 중 상점 1칸 구매가 병합·구매 추적에 반영되고, 보유 증강(수동)은 유지된다. `test_item_select_and_carousel_keep_hud_but_update_items`: 모루 화면에서는 HUD·상점이 유지된다. game_over는 `loop.py:226` reset(Victory 1장 → "세션 초기화" 출력으로 확인) | - | - |
| 4 | 전투 화면에서 상점 추천이 흔들리지 않는가 | PASS / **WARN** | 흔들리지 않는다(KEEP). 다만 **아예 갱신되지 않는다**. 06 §7은 "전투를 planning으로 봐도 해롭지 않다(구매 가능)"는 전제로 짜였는데, 이번 라운드부터 전투가 실제로 `combat` → KEEP이 된다. `--screenshot` 출력에서도 `2-5 전투 시작`에 직전 이미지의 상점 추천(다른 상점 목록)이 **"직전 추천" 표시 없이** 그대로 나온다(`app/report.py:253-262`, `overlay.py:153-168`) | app-integrator, jev-strategist | §3-A1 |
| 5 | augments_owned ID 체계 | PASS | 템플릿 317개 모두 static에 있다. `_resolve_augment_icon`은 set_native를 우선한다: 어수선한 마음 → `DA_ClutteredMind`(TFT7_ 버전 아님), 초월 → `DA_Ascension`. 둘 다 stats `augment_tiers` ID와 같다(18.3 스냅샷). advisor는 `a.id`와 `confidence ≥ 0.6`만 본다(`advisor/features.py:182-183`) → 그대로 쓸 수 있다. 새 테스트 `test_augment_icon_ids_are_static_ids_and_prefer_set_native` | - | - |
| 6 | 공유 아이콘·미등록 → None | PASS | 칸 하나라도 실패하면 필드 전체가 None이다(`recognizer.py` `_read_augments_owned`). 5-5 가운데 증강 → None 확인. JPEG q60 열화에서도 오답 없이 None | - | - |
| 7 | 증강 템플릿 경로 ↔ gitignore/README | PASS | `git check-ignore`: `data/templates/*/augments/`, `augments_screen/`(`.gitignore:37-38`), `tests/fixtures/screens/raw/`(`.gitignore:44`). README 표에 "커밋하지 않음"이 적혀 있다 | - | - |
| 8 | 증강 커버리지 | WARN | stats 18.3 증강 258개 중 **81개는 아이콘 템플릿이 없다**(아이콘 없음·공유) → 보유해도 None이 되어 수동 입력이 필요하다. 공유 아이콘 키는 QA 집계로 28개(보고서는 19개. 셈 방식 차이로 보이며 영향 없음). stats ID 5개는 static에 없다(`DA_StarringUp`, `DA_18_RivalsAugmentPlus`, `DA_NestingDolls`, `DA_SubscriptionService`, `DA_Lineup`) | stats-researcher(정보), vision | §3-S1 |
| 9 | streak 부호 0.5 → 0.8 | PASS / WARN(경미) | 사용자가 부호를 확인했다. advisor는 streak를 Jev 상태 텍스트로만 쓴다(`advisor/features.py:155`, `jev_state.py:188`). 경제 규칙에는 쓰지 않는다. 다만 신뢰도 = OCR 점수 × 0.8이라 **OCR 점수가 0.75면 정확히 임계 0.6**이다. QA04-V4의 "임계에서 떨어뜨리기"는 OCR 점수 0.9 이상에서만 성립한다(`tests/test_vision.py:511-517`은 1.0과 0.9만 검사). 실측 OCR 점수는 약 1.0(신뢰도 0.79~0.80)이라 지금은 영향이 없다 | vision | §3-V4 |
| 10 | `[capture] monitor="auto"` 설정 | PASS | `config.py` `int(ge=0) \| Literal["auto"]`, 기본 "auto" = `settings.toml` 한국어 주석. 새 테스트 `test_capture_monitor_setting_accepts_auto_and_int`(-1 거부, "primary" 거부) | - | - |
| 11 | 모니터 자동 선택 로직 | **FAIL → 고침** | ① 다른 모니터에도 TFT가 떠 있으면(방송·리플레이) 둘 다 1.0이라 동점 → `pick_monitor`가 앞 번호를 골라 **게임 모니터에서 방송 모니터로 옮겼다**. ② `MssSource(monitor="auto", region=...)` → `int("auto")` ValueError. 둘 다 고쳤다(§2). 30초 재확인은 `tft-capture` 스레드에서 돈다(`app/live.py:153`) → UI 스레드를 막지 않는다. 주기 사이에는 채점하지 않는다(테스트로 고정) | vision(확인) | - |
| 12 | 듀얼 캡처의 세로 모니터 / 검정 조각 | **FAIL → 고침** | 1080x1920 세로 모니터 + 1920x1080 게임 캡처 → `pick_screen`의 `profile_for(1080,1920)`에서 `ValueError: 잘못된 비율 좌표`. 방송 fixture 3장은 스테이지 칸을 순수 검정으로 칠하면 `find_screens`가 한 화면을 766px과 1080px 조각으로 나눈다 → 둘 다 프로파일 유도가 안 돼 `recognize()`가 죽는다. 고친 뒤 3장 모두 정상 판별(planning 0.7/0.9/0.9) | vision | §3-V1 |
| 13 | 단일 모니터 · 16:10 레터박스 · 필러박스 · 스케일 회귀 | PASS | 13장에서 게임 영역을 잘라 5가지로 변형해 돌렸다: 단일 1080p / 1920x1200 레터박스 / 2560x1080 필러박스 / 1440p 확대 / 900p 축소 / JPEG q60. **screen_mode 65/65가 원본과 같다.** 필드 차이는 JPEG q60에서 보유 증강 4장이 None으로 바뀐 것뿐이다(오답 아님) | - | - |
| 14 | PvE 오분류 | **FAIL → 고침**(규칙 1줄) | 방송 1-4(PvE 준비, 크립 체력바 4개)에서 스테이지만 못 읽으면 `_is_pve(None)=False` → **combat 0.8(확신 오답, 추천 멈춤)**. 전투 판정에 "스테이지를 읽었을 것"을 추가했다. 고친 뒤 planning 0.7 | vision(확인) | - |
| 15 | 상대 보드 관전 중 보유 증강 오독 | **WARN** | `owned`는 planning에서 읽는다. 준비 단계에 상대 보드를 관전하면 그 보드의 증강 줄이 같은 자리에 보인다(07 §4.4: 원정 전투에서 상대 줄 확인). 이것이 `field_source=vision`, 신뢰도 0.9로 **수동 입력까지 덮는다**(`session.py` `_track_augments`: vision이면 무조건 믿음). 관전 캡처가 없어 재현은 못 했다 | app-integrator, vision | §3-A2 |
| 16 | game_over 오판 → 세션 초기화 | WARN | HUD 없음 + 스테이지 없음 + "나가기" 한 글자만 있어도 game_over 0.8 → `RESET_MODES` → 수동 증강을 포함한 세션 전체가 지워진다. 설정·ESC 메뉴처럼 HUD를 가리는 오버레이는 캡처가 없어 검증하지 못했다. 인식 1회 만에 초기화된다(변화 감지의 안정 프레임만 거친다) | app-integrator, vision | §3-A3 |
| 17 | 증강 선택 "하나 선택" 충돌 | PASS(방송 기준) | 하단 `select_title`과 가운데 제목의 위치가 다르다. `select_title`이면 증강 판독을 건너뛴다. 방송 증강 fixture 1/1 유지. 1080p 증강 화면 캡처는 아직 없다(07 §11-2) | vision | 캡처 대기 |
| 18 | `--screenshot` end-to-end | PASS / WARN | 13장 6.4초(인식 218~401ms/장, 추천 0~4ms). 한국어 요약이 자연스럽다(연패·연승, "증강 시너지: 어수선한 마음", 거대화 설명, 게임 종료 → 초기화). WARN: ① KEEP 화면에 직전 이미지의 추천이 "직전" 표시 없이 나온다(#4). ② `5-5 전투 전`(레벨 9)의 근거에 **"초반: 방향 미정"**이 붙는다. 보유 증강이 None이고 보드를 인식하지 못하면 5스테이지에서도 undecided가 된다 | app-integrator, jev-strategist | §3-A1, §3-J1 |
| 19 | 전체 pytest | PASS | `pytest -q`: **707 passed, 3 skipped**(TFT_LIVE_JEV), 0 failed. 새 파일 `tests/test_qa08_vision_boundaries.py` 11개 | - | - |
| 20 | 실시간 프레임당 비용(레터박스 사용자) | WARN | `content_for`가 매 프레임 `screen_candidates`를 부른다. 단일 모니터 프레임이라도 가장자리가 검정(레터박스)이면 느린 경로로 간다: 1920x1200에서 +27ms, 2560x1600에서 +50ms(여기에 `detect_content_box` 16~31ms가 더해진다). 4fps면 한 코어의 약 20~30% | vision | §3-V2 |

---

## 2. QA가 직접 고친 것 (작은 수정, vision-engineer 확인 요청)

| 파일 | 변경 | 이유 |
|---|---|---|
| `src/tft_advisor/vision/recognizer.py` `content_for` | 후보가 여럿이면 프로파일 유도가 가능한 것만 남긴다(`_profile_ok`). 하나도 없으면 한 화면으로 보고(`detect_content_box(image)`), 하나면 그 후보를 쓴다 | 세로 모니터·검정 조각 크래시(#12) |
| `.../recognizer.py` `pick_screen` | 같은 필터를 적용하고, 픽셀 점수에는 이미 계산한 프로파일을 재사용한다 | 직접 호출될 때도 안전하게 |
| `src/tft_advisor/vision/screen_mode.py` `classify` | 전투 판정 조건에 `sig.stage is not None`을 추가했다 | PvE 준비 + 스테이지 미인식 → combat 0.8 확신 오답(#14). 07 보고서의 "안전한 쪽" 원칙과 같다 |
| `src/tft_advisor/vision/capture.py` `pick_monitor` | 인자 `prefer=`를 추가했다. 동점이면 지금 쓰는 모니터를 유지한다. `MssSource._pick`이 현재 모니터를 넘긴다 | 방송 모니터로 옮겨 가는 문제(#11-①) |
| `.../capture.py` `MssSource.__init__` | `"auto"`이면 `int()`를 부르지 않는다(region이 우선) | `int("auto")` ValueError(#11-②) |
| `tests/test_qa08_vision_boundaries.py`(신규) | 테스트 11개: 가짜 mss로 auto 선택·유지·이동·주기, 모니터 1대, region+auto, 설정 검증, 세로 모니터, 검정 조각 분할, 전투에 스테이지 필요, 전투/모루 병합, vision 증강 우선(현재 계약 기록), 증강 ID 체계(템플릿 없으면 skip) | 회귀 고정 |

고친 뒤 원본 13장과 방송 7장 평가 수치는 그대로다(아래 §4).

---

## 3. 담당 에이전트별 할 일

### app-integrator
- **A1 (WARN, 우선)**: 전투 중에는 추천이 멈춘다. 이제 `combat`이 실제로 나온다(쌍 4개 모두). 06 §7의 "전투도 planning으로 처리돼 구매가 반영된다"는 동작이 이번 라운드에서 사라졌다. 선택지:
  (a) COMBAT을 KEEP에서 빼 planning처럼 추천한다. 전투 중에도 HUD·상점을 읽으므로 가능하다. jev-strategist와 advisor 계약(`engine.py:144`)을 함께 바꿔야 한다.
  (b) KEEP은 유지하되, 병합 상태의 상점에서 빈 칸이 된 유닛의 "[구매]" 줄을 흐리게 하거나 지운다.
  (c) 최소한 KEEP 화면에는 "전투 중 — 준비 단계 추천 유지" 표시를 단다. `report.format_report`는 rec가 있으면 kept 표시를 하지 않는다(`report.py:253`). 오버레이도 `kind=="kept"`를 표시하지 않는다(`overlay.py:187-199`).
- **A2 (WARN)**: 보유 증강에는 **한 판 안에서는 추가만 된다**는 불변식을 적용하자. vision 값은 직전 목록의 확장(앞부분 일치)일 때만 받는다. 다르면 같은 값이 N회 연속으로 들어오거나 증강 선택 화면을 지난 뒤에만 받는다. 그래야 상대 보드 관전과 수동 입력 덮어쓰기를 막을 수 있다(`session.py` `_track_augments`). 지금 동작은 `test_owned_augments_from_vision_are_trusted_over_manual`에 "현재 계약"으로 기록해 두었다. 규칙을 바꾸면 이 테스트도 같이 고치면 된다.
- **A3 (WARN)**: `RESET_MODES` 진입 조건을 강화하자. game_over는 신뢰도 0.95(제목과 나가기 둘 다)일 때나 두 번 연속 관측될 때만 받는다. 또는 초기화 전에 `session.json`을 `session.prev.json`으로 백업한다. 수동으로 넣은 증강을 오판 한 번에 잃지 않게 하려는 것이다.

### vision-engineer
- **V1**: QA 수정(§2) 3건을 검토해 달라. 근본 원인은 두 가지다. ① `find_screens`는 폭 146px짜리 순수 검정 조각만으로 한 화면을 쪼갠다(중앙값 창 31열로 부족하다). 모니터 경계는 "검정 공백이 넓고, 위/아래 끝이 다른 상태가 수백 px 이어질 때"로 판단하자. ② `profile_for_aspect`/`derive_profile`은 가로/세로 1.0 미만에서 알기 어려운 `ValueError`를 낸다. 명확한 예외나 None을 돌려주자.
- **V2 (성능)**: 실시간 단일 모니터 프레임에서는 `screen_candidates`를 건너뛰자(예: `MssSource`가 모니터 1대를 찍을 때는 `content_for`에 "단일 화면" 힌트를 준다). 또는 프레임 크기별로 콘텐츠 박스를 캐시해 몇 초마다만 다시 잰다. 레터박스 사용자가 매 프레임 43~81ms를 쓰고 있다.
- **V3**: 아이템 신뢰도 여유가 작다. CDragon 템플릿만 쓰면 0.827~0.831로 임계 0.80에 가깝다. 사용자마다 `harvest-items`를 해야 한다는 점을 README·첫 실행 안내에 적자. 또는 배지 숫자(제거기) 영역을 가리고 매칭하자.
- **V4 (경미)**: streak 신뢰도는 OCR 0.75일 때 정확히 0.6이 된다. 부호가 확정됐으니 `STREAK_SIGN_FACTOR`를 0.85~0.9로 올리거나 OCR 점수에 하한을 두자. `test_streak_sign_factor_is_off_the_threshold`에 OCR 0.75 경우도 넣자.
- **캡처 요청(사용자 경유)**: 준비 단계에 상대 보드를 관전하는 화면(A2 재현), ESC/설정 메뉴 화면(A3), 1080p 증강 선택 화면, PvE 준비/전투 쌍.

### jev-strategist
- **J1**: 5-5·레벨 9(보드 미인식, 보유 증강 None)에서 "초반: 방향 미정"이 뜬다. undecided 폴백(`advisor/scoring.py:149-154`, 자원 신호 전무 → 1.0)이 스테이지를 보지 않는다. 4스테이지 이후에는 "초반" 문구를 쓰지 않거나 "정보 부족"으로 바꾸자.
- A1(a)를 택하면 `engine.py:144`에서 COMBAT의 반환 규칙을 조정해야 한다.

### stats-researcher (정보)
- **S1**: 18.3 stats 증강 ID 5개가 static에 없다(위 #8). 다른 에이전트가 18.3 작업 중이라 범위 밖이지만 기록해 둔다.

---

## 4. 재현 수치

| 실행 | 결과 |
|---|---|
| `python -m tft_advisor.vision.evaluate tests/fixtures/screens/raw` (기본 템플릿) | screen_mode 13/13, stage 12/12, level 9/9, xp 9/9, gold 9/9, streak 9/9, hp 12/12, shop_odds 9/9, shop 9/9(칸 45/45), items 12/12, augments_owned 4/4. **오답 0, 미인식 0**. QA 수정 후 다시 돌려도 같다 |
| 같은 평가, **items_screen 제외(CDragon만)** | 전 필드 동일(items 12/12). 아이템 신뢰도 0.827~0.831(기본 0.959~1.0) |
| `python -m tft_advisor.vision.evaluate` (방송 7장) | screen_mode 7/7, stage 7/7, level 3/3, xp 5/5, gold 5/5, streak 5/5, hp 7/7, odds 5/5, shop 4/5(칸 24/25, none 1, 틀린 ID 0), augment_offer 1/1. 05 기준선과 같다 |
| 변형 5종 × 13장 | screen_mode 65/65가 원본과 같다. JPEG q60에서만 보유 증강 4장 → None |
| 신뢰도(모드) | planning(워터마크) 0.9, combat 0.8, carousel 0.65, item_select 0.8, game_over 0.95 |
| `--screenshot raw --jev mock` | 13장 6.4초. 인식 218~401ms(4480x1440, 듀얼 캡처 경로), 추천 ≤ 4ms |

**수치 신뢰도**: 13장은 **두 판**, 한 사용자, 1080p 하나에서 나왔다. 라벨 초안은 vision 에이전트가 썼다. 템플릿 순환은 위 CDragon 전용 실행으로 배제했다. 라벨 순환은 QA가 `5-5 전투 전`과 `수호령`의 HUD·상점·증강 줄·아이템 칸을 직접 봐서 전부 일치함을 확인했다. 사용자는 의미(부호·증강 줄·제거기)를 확인했다. 따라서 "이 두 판에서 오답 0"은 믿을 수 있다. 하지만 표본 수(모드당 1~5장)로는 **일반화된 정확도라고 할 수 없다**. 특히 PvE, 관전, 증강 선택, 16:10 화면은 1080p 실측이 없다.
엄밀한 leave-one-out은 할 수 없었다. `items_screen/` 8개에 어느 캡처에서 잘라냈는지 기록이 없어서다. 대신 "하나만 빼기"보다 엄격한 "전부 빼기"를 했다.
