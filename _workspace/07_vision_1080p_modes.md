# 07 vision-engineer: 1080p 원본 · 듀얼 모니터 캡처 · 화면 상태(준비/전투/특수 선택/종료) · 보유 증강

작성일: 2026-09-22 / 작성자: vision-engineer / 이 PC(Windows 11, Python 3.14 venv, onnxruntime) / 커밋하지 않음
입력: 사용자 실캡처 13장 `tests/fixtures/screens/raw/*.png`(4480x1440 듀얼 모니터 Win+PrtSc, gitignore, 타 플레이어 이름 포함 — 공개 금지)
사용자 확인표: `_workspace/07_vision_draft_labels.md`

---

## 0. 요약

1. **듀얼 모니터 캡처를 자동 처리한다.** 모니터가 덮지 않는 곳이 순수 검정(0,0,0)인 점을 이용해 모니터 영역을 나누고, 스테이지 글자 OCR로 게임 모니터를 고른다. 크롭 사본·설정 없이 `evaluate`/`--screenshot`/`templates`가 그대로 동작한다.
2. **실시간 캡처의 모니터 자동 선택**: `[capture] monitor`가 `"auto"`(새 기본값)면 모든 모니터를 한 장씩 찍어 TFT 화면이 있는 모니터를 고른다(30초마다 재확인). 숫자 지정도 그대로 된다.
3. **16:9 ROI를 1080p 원본으로 보정**: 하단 HUD +0.003(3px), 아이템 벤치 칸 실측, 상점 가격 칸 폭, 특성 패널 폭. 임시 보정 `BOTTOM_HUD_DY`는 정리했고 **16:10 프로파일 수치는 그대로**다(가격 칸 폭만 같은 규칙으로 넓어짐).
4. **준비/전투 구별이 된다.** 새 신호 2개: 보드 가운데 **"N/M" 워터마크**(준비에만 있음) + **빨간 적 체력바 개수**. 쌍 4개 전부 맞았다. 모루·특성 선택(`item_select`), 게임 종료(`game_over`)도 추가.
5. **보유 증강(augments_owned)을 읽는다.** 보드 왼쪽 위 아이콘 줄이 증강이 맞다(1개 → 3개로 늘어남, CDragon 증강 글리프와 0.89~0.96으로 일치). 모르는 칸이 하나라도 있으면 None(5-5의 가운데 증강은 공개 아이콘이 없어 None).
6. **연패 부호를 확인했다**: 파란 물방울 = 연패(HP 95→84→76→71과 일치), 빨강 불꽃 = 연승. `STREAK_SIGN_FACTOR` 0.5 → 0.8(이제 advisor가 연승·연패를 씀).
7. **정확도(초안 라벨 대비, 잠정)**: 13장 전 필드 **틀린 값 0, 미인식 0**. 방송 fixture 회귀 없음(screen_mode 7/7 유지, 1-4 PvE 함정은 규칙으로 막음).
8. **Windows 성능**(onnxruntime, 16코어): 1080p 기본 묶음 평균 **222ms**(목표 300ms 달성), 특성 포함 전 묶음 289ms, 스테이지만 71ms, 듀얼 모니터 스크린샷 330ms.
9. 테스트: **637 passed, 8 failed, 3 skipped** (+`tests/advisor/test_advisor_units.py` 수집 오류 1). 실패 8개와 수집 오류는 **이번 변경과 무관한 이 PC 환경 문제**(§8). 기준선(변경 전 같은 조건)은 588 passed, 10 failed, 3 skipped — 그중 vision 쪽 2개(Windows 한글 경로)는 고쳤다.

---

## 1. 입력 확인

| 항목 | 값 |
|---|---|
| 캡처 크기 | 13장 전부 4480x1440 RGBA(알파 전부 255) |
| 배치 | 왼쪽 x 0..2559 = 2560x1440 모니터(탐색기, 무시), **오른쪽 x 2560..4479, y 0..1079 = 게임 1920x1080(16:9 원본)**, 오른쪽 아래 y 1080..1439 = 순수 검정 |
| 레터박스 | 게임 영역 안에는 없음 |
| 새로 본 화면 | 준비/전투 쌍 4개(2-2, 2-5 **원정 전투**, 2-6, 5-5), 캐러셀 2-4, 모루 5-1, 게임 종료(1위), 특수 상품 "거대화", 악의 여단 2지선다 + "준비" 배너 |

원본은 수정하지 않았다. 작업용 크롭은 스크래치 폴더에만 만들었다.

---

## 2. 듀얼 모니터 처리

### 2.1 스크린샷(`evaluate`, `--screenshot`, `templates`)

`regions.find_screens(image)`:
- 빠른 길: 네 가장자리 줄/열에 순수 검정이 15% 미만이면 모니터 하나로 본다(실시간 프레임 0.2ms).
- 열마다 "순수 검정이 아닌 픽셀"의 위/아래 끝을 재고(중앙값 필터 31열로 화면 안 검정 조각 제거), 끝이 같은 열끼리 묶어 모니터로 본다. 사용자 캡처 → `[(0,0,2560,1440), (2560,0,1920,1080)]`. 4480x1440에서 약 80ms.
- `screen_candidates`: 높이가 같은 모니터가 경계 없이 붙은 경우(3840x1080 등, 가로/세로 ≥ 2.5)는 2·3등분 후보도 만든다.

`Recognizer.content_for` 우선순위: `recognize(content=)` > `[vision] content_box` > (content_box_auto) **후보가 둘 이상이면 `pick_screen`** > 레터박스 탐지.
`pick_screen`: 후보마다 스테이지 ROI 한 줄 OCR → "2-5" 같은 스테이지가 읽히는 후보. 결과는 (프레임 크기, 후보) 단위로 기억해서 **스테이지가 없는 화면(게임 종료)도 같은 모니터**를 쓴다. 기억도 없으면 `stage_bar_score`(위쪽 가운데 스테이지 막대가 어둡고, 밝은 창이 아닌 정도).
`templates.py`(harvest/debug-rois)는 OCR 없이 `game_box_by_pixels`로 고른다.

### 2.2 실시간(`MssSource`)

- `[capture] monitor`: `int`(1 = 주, 2 = 두 번째 …, 0 = 전체) **또는 `"auto"`(새 기본값)**. `config/settings.toml`도 `"auto"`로 바꾸고 한국어 주석을 달았다.
- `"auto"`: 첫 `grab()`에서 모든 물리 모니터를 한 장씩 찍어 채점 → 최고점 모니터. 앱(`app/live.py`)은 `scorer=recognizer.screen_score`(스테이지 OCR이 읽히면 1.0, 아니면 픽셀 점수 x 0.5)를 넘긴다.
- 30초마다 재확인. 이미 고른 모니터는 **다른 모니터가 확인 점수(≥ 0.5)로 더 높을 때만** 바꾼다(게임 종료 화면에서 탐색기로 튀지 않게). 모니터가 하나면 채점 없이 그 모니터.
- mss 인스턴스는 첫 `grab()` 스레드에서 만든다(Windows GDI 핸들이 스레드에 묶임). 잘못된 모니터 번호 오류도 첫 `grab()`에서 난다(예전엔 생성자).
- **실기 미검증**: 이 PC에서 실제 게임을 띄워 돌려 보지는 못했다(캡처 파일로만 검증). 사용자 첫 실행에서 로그 `게임 모니터 자동 선택: 2번` 확인 필요.

---

## 3. ROI 보정 (16:9, 1080p 원본)

측정: OCR 박스 위치(텍스트 중심)와 빈 아이템 칸 테두리 픽셀.

| ROI | 전(방송 크롭) | 후(1080p) | 근거 |
|---|---|---|---|
| 하단 HUD 11개 세로 | 기준값 | **+0.003** | 글자 중심이 ROI 중심보다 아래: 레벨 +0.002, XP +0.0013, 확률 +0.002, 골드 +0.003, 상점 이름 +0.0027 |
| `item_slots` | x 0.004~0.027, y0 0.2395, 간격 0.0505, 높이 0.040 | **x 0.0052~0.0286, y0 0.2417, 간격 0.0500, 높이 0.0417** | 빈 칸 테두리 x 10~55px, 0번 칸 y 261, 간격 정확히 54px, 높이 45px. 9번 칸에서 예전 값은 약 5px 어긋났다 |
| `shop_costs` 오른쪽 끝 | 카드 끝(x0+0.0995) | **x0+0.1035** | 가격 숫자 오른쪽 끝이 예전 ROI 끝과 같았다(칸 3 "3" 0.6917~0.7016). 카드 사이 틈 안 |
| `traits_panel` x2 | 0.125 | **0.132** | 구간 사다리("3 > 4 > 5 > 7") 끝 x≈0.128 |
| 나머지(스테이지, 골드 가로, 상점 카드·이름, 증강 카드, 플레이어 목록) | | 그대로 | 1080p에서 확인, 여유 안 |

**새 ROI 7개**(1080p 실측, 16:10은 CENTER anchor로 유도 — 미검증):

| 이름 | 비율 좌표 | 용도 |
|---|---|---|
| `board_count` | (0.440, 0.190, 0.610, 0.310) | 준비 단계 보드 워터마크 "3/3" |
| `prep_banner` | (0.450, 0.120, 0.550, 0.190) | "준비" 배너 |
| `select_title` | (0.430, 0.745, 0.570, 0.795) | 하단 "하나 선택"(모루·특성 선택) |
| `game_over_title` | (0.400, 0.100, 0.600, 0.200) | "최종 순위 / 1위" |
| `exit_button` | (0.420, 0.910, 0.580, 0.965) | "나가기" |
| `augments_owned` | (0.200, 0.190, 0.340, 0.265) | 보유 증강 줄 탐색 영역 |
| `combat_area` | (0.200, 0.030, 0.800, 0.620) | 빨간 적 체력바 탐색 |

**`BOTTOM_HUD_DY` 정리**: 16:9 기준값 자체를 1080p로 고쳤으므로 임시 보정 상수는 없앴다. 16:10 프로파일은 `BOTTOM_HUD_DY_16X10 = 0.003`(= 예전 0.006 − 이번 0.003)으로 유도해 **예전과 수치가 같다**(테스트로 고정). 실측이 없는 비율(4:3, 21:9, 32:9)의 유도에는 이제 세로 보정을 넣지 않는다(기준이 원본이므로).

---

## 4. screen_mode 규칙과 근거

### 4.1 측정 (13장 + 방송 7장)

| 신호 | 준비 | 전투 | 그 밖 | 비고 |
|---|---|---|---|---|
| 워터마크 "N/M" 한 줄 OCR | 3/3, 4/4, 9/9, 5/5, 8/8(모루 뒤), "/5"(악의 여단), 2-6만 실패 → 검출로 "0/5"(0.56) | **4장 전부 없음** | 캐러셀·종료 없음 | 2-6 준비는 파란 소용돌이 효과가 가림 → 검출 재시도로 해결 |
| 빨간 체력바(64x4px, H≤6/≥174, S≥180, V≥100) | **0** (5장) | **3, 6, 7, 10** | 0 | 방송 1-4(PvE **준비**)는 크립 4개 → PvE 예외 필요 |
| "준비" 배너 | 악의 여단 화면에만 | 없음 | | 순간 배너라 보조 신호 |
| 하단 "하나 선택" | | | 모루, 악의 여단 | 증강 선택(가운데 y 0.18~0.24)과 다른 위치(y 0.757~0.780) |
| "최종 순위"/"나가기" | | | 종료 | 제목은 두 줄이라 한 줄 인식이 "1"만 읽음 → 검출로 확인 |
| 스테이지 막대 타이머 | 8, 10, 3, 10 | 2, 1, 1, 2 | | 남은 초일 뿐 준비/전투를 가르지 못한다(둘 다 같은 청록 막대) → 쓰지 않음 |
| 상대 이름표(위쪽) | 없음 | 원정·홈 PvP에 있음, 5-5 홈 전투엔 없음 | 캐러셀엔 전원 | 위치·색이 제각각 → 쓰지 않음 |

### 4.2 규칙 (`screen_mode.classify`, 위에서부터 첫 일치)

1. 가운데 "하나 선택" + 증강 이름 2개 이상 → `augment_select` 0.95 (기존)
2. 증강 이름 2개 이상, 또는 가운데 제목만(HUD·하단 제목 없음) → `augment_select` 0.75 (기존 + 하단 제목 제외)
3. HUD 없음 + 스테이지 없음 + ("최종 순위" 또는 "나가기") → `game_over` 0.95(둘 다) / 0.8
4. HUD 없음 + 하단 "하나 선택" → `item_select` 0.8 (모루, 악의 여단 등 특수 선택)
5. HUD 있음:
   - 워터마크 또는 "준비" 배너 → `planning` **0.9**(확정)
   - 빨간 체력바 ≥ 2 **그리고 PvP 라운드**(1-x, x-7 아님) → `combat` 0.8
   - 그 밖 → `planning` 0.7(HUD OCR) / 0.55(픽셀) = "준비 또는 전투"(예전 의미 그대로)
6. 어두운 프레임 → `loading` 0.6, 스테이지 N-4·1-1 → `carousel` 0.65, 그 밖 `unknown` (기존)

**PvE 예외의 이유**: 크립 라운드는 준비 단계에도 크립이 빨간 체력바를 달고 서서 워터마크를 가린다(방송 1-4: "1/3"이 가려져 읽히지 않음 → 예외 없이는 `combat`으로 틀림). PvE에서는 안전한 쪽(`planning` 0.7)으로 둔다. 전투를 준비로 보는 것은 해가 없고(06 §7), 준비를 전투로 보면 추천이 멈춘다.

**체력바 2개 기준**: 상대 이름표 막대가 빨간 플레이어 색일 수 있어 1개로는 전투로 보지 않는다.

**알려진 한계**: 전투가 끝나 적이 다 죽은 순간은 `planning` 0.7. PvE 전투는 `planning` 0.7.

### 4.3 계약 관련

- 새 ScreenMode 값은 만들지 않았다. **악의 여단 특성 2지선다 → `item_select`**(가장 가까운 값, "모루/포탈 등 세트 특수 선택"). 추천을 새로 만들지 않고 직전 추천을 유지하는 KEEP_MODES에 이미 들어 있어 동작은 맞다.
- **제안(app-integrator 경유)**: 특수 선택에서 **무엇을 고를지 추천**까지 하려면(모루 4개 중 하나, 특성 선택지) `GameState`에 `select_offer: list[str]`(선택지 이름) 같은 필드가 필요하다. 지금은 화면 판별만 한다. 게임 종료 화면의 **최종 순위(1위)** 도 읽을 수 있으니 기록용 `placement` 필드를 원하면 알려 달라.

### 4.4 어떤 화면에서 무엇을 읽나 (`recognizer.READ_MODES` = `app/session.GROUP_READ_MODES`)

| 묶음 | 읽는 화면 | 변경 |
|---|---|---|
| stage, players(HP) | 전부 | 그대로 |
| hud, shop, traits | planning, **combat** | 전투 중에도 HUD·상점이 그대로 보이고 살 수 있다(예전엔 전투가 planning으로 분류돼 읽혔다 → 동작 유지) |
| items | planning, **combat**, augment_select, **item_select, carousel** | 아이템 벤치는 이 화면들에서 모두 보인다(캡처로 확인) |
| augment | augment_select | 그대로 |
| **owned**(새 묶음, augments_owned) | planning | 증강 줄은 보드에 붙어 있다: 원정 전투에선 위치가 13px 달라지고 상대 줄도 보인다 → 준비만 |

`recognize()`의 분기는 이 표 하나만 본다. 두 표가 같은지 `tests/app/test_session.py::test_group_read_modes_equal_vision_read_modes`와 `tests/test_vision_1080p.py::test_read_modes_cover_groups_and_match_app_table`이 고정하고, 모드마다 실제 호출되는 헬퍼가 표와 같은지 `test_recognize_reads_groups_exactly_in_read_modes`(ScreenMode 8개 파라미터)가 고정한다.
"owned"는 `FIELD_GROUP`에 넣지 않았다: augments_owned는 app에서 `_CARRY_FIELDS` 규칙(새 값이 있을 때만 덮어쓰기)이라 vision의 "못 읽음"이 수동 입력값을 지우지 않는다. `ChangeDetector.roi_groups`에 "owned"(증강 줄 ROI)를 추가했다.

---

## 5. 보유 증강 (augments_owned)

**결론: 보드 왼쪽 위 아이콘 줄은 보유 증강이다. 판독을 구현했다.**

근거
- 짙은 회색 상자(BGR 28,29,29) 안에 38x37px(1080p) 금색 글리프가 한 줄. 2-2~3-1 1칸, 5-1·5-5 3칸 = 증강 선택 시점(2-1, 3-2, 4-2)과 일치. 가운데 정렬(x≈0.266)로 칸 수만큼 좌우로 늘어난다.
- CommunityDragon hexcore 증강 아이콘(`fetch-augments`, 317개)과 그대로 일치: 1번 칸 **어수선한 마음 0.893~0.905(2위 0.66, 차 0.25)**, 3번 칸 **초월 0.951(2위 불완전한 초월 0.878, 차 0.073)**.
- 2번 칸(육각 방패 + 아래 화살표)은 최고 0.53 → 공개 아이콘에 없는 증강(정적 데이터의 `missing-*` 자리표시 47개 또는 404 4개 중 하나로 추정). **None 처리**(안전).

구현 (`icons.find_icon_row`, `icons.AugmentIconMatcher`, `Recognizer._read_augments_owned`)
- 탐색 영역에서 배경색 마스크 → 닫기 → 가장 큰 덩어리. 높이 = 화면 높이의 3.4%(±20%), 가로 = 칸의 정수배(1~5), 꽉 찬 사각형(85%)일 때만 줄로 본다.
- 칸을 38px로 맞추고 ±4px 탐색, 템플릿 36px(실측 최적). 한 칸 약 20ms(317개 템플릿).
- 수락: 점수 ≥ 0.80, 1·2위 차 ≥ 0.05, **같은 아이콘을 쓰는 다른 이름의 세트 증강이 없을 것**(예: "가지 뻗기"/"가지 뻗기+"는 아이콘 공유 → None. 이런 아이콘이 19개).
- 한 칸이라도 실패하면 필드 전체 None(부분 목록은 advisor가 "이것뿐"으로 오해한다). 줄이 없으면: 스테이지 1이면 `[]`(0.9), 그 밖은 None(유닛·효과가 가렸을 수 있음).
- 신뢰도 = 칸 점수의 최솟값(0.89~0.95).
- 보충 경로: `python -m tft_advisor.vision.templates harvest-augments SCREENSHOT LABEL.json` — 사용자가 확인한 이름(`augments_owned`)으로 실화면 칸을 `data/templates/18/augments_screen/`에 저장(칸 수와 라벨 수가 다르면 저장 안 함). **5-5 가운데 증강 이름을 사용자에게 물었다**(확인표 질문 2).
- `--screenshot` 실행 확인: advisor 근거에 "증강 시너지: 어수선한 마음"이 나온다.

**등급 색**: 이번 증강 둘 다 금색 글리프, 정적 데이터 tier 2(골드)와 일치. 실버/프리즘 색은 아직 표본 없음.

---

## 6. 정확도

### 6.1 1080p 원본 13장 (`python -m tft_advisor.vision.evaluate tests/fixtures/screens/raw`, 초안 라벨 대비 — 잠정)

| 필드 | ok / 라벨 수 | 틀림 | 미인식 | 비고 |
|---|---|---|---|---|
| screen_mode | **13/13** | 0 | 0 | 준비 5, 전투 4(원정 1 포함), 캐러셀, 모루, 악의 여단, 종료 |
| stage | 12/12 | 0 | 0 | |
| level / xp | 9/9 / 9/9 | 0 | 0 | 전투 화면 포함 |
| gold | 9/9 | 0 | 0 | |
| streak | 9/9 | 0 | 0 | 연패 4종(−1, −3, −4, −5) + 연승 9, 부호 전부 맞음 |
| hp | 12/12 | 0 | 0 | |
| shop_odds | 9/9 | 0 | 0 | |
| shop (5칸 전체) | 9/9 | 0 | 0 | 칸 단위 **45/45**, 빈칸·특수 상품 "거대화"(1골드) 포함 |
| items | 12/12 | 0 | 0 | **CDragon 아이콘만으로** 12/12. 단 필드 신뢰도(칸 최솟값)가 0.827~0.831로 **수락 임계 0.8에 가깝다**(가장 약한 칸은 배지 숫자가 그려진 아이템 제거기로 추정). 이 캡처들로 실화면 템플릿 8개를 harvest한 뒤 0.96~1.0(자기 캡처라 참고용). 이 PC의 `items_screen/`에 그대로 두었다 |
| augments_owned | 4/4 | 0 | 0 | 5-5 준비는 모르는 증강 때문에 라벨에서 뺌(인식도 None — 올바른 안전 실패) |
| active_traits | 라벨 없음 | | | 5-5에서 인원 1인 행 2개를 놓침(기존 한계, advisor 미사용) |

**확신에 찬 오답 0건.**

### 6.2 방송 fixture 회귀 (`python -m tft_advisor.vision.evaluate`)

screen_mode 7/7, stage 7/7, level 3/3, xp 5/5, gold 5/5, streak 5/5, hp 7/7, odds 5/5, shop 4/5(칸 24/25, 틀린 ID 0), augment 1/1 — **05 보고서 기준선과 같다**. 준비 화면 3장은 이제 워터마크로 신뢰도 0.9.

---

## 7. Windows 성능 (이 PC, rapidocr + onnxruntime, 16 논리 코어)

`Recognizer` 생성 약 0.8s(OCR 모델 + 아이템 212 + 증강 317 템플릿).

| 경로 | 평균 | 중앙값 | 최대 |
|---|---|---|---|
| 1080p 기본 묶음(traits 제외, owned 포함) | **222ms** | 232 | 291 |
| 1080p 전 묶음(traits 포함) | 289ms | 321 | 408 |
| 1080p 스테이지·화면 상태만 | 71ms | 70 | 91 |
| + hud / shop / items / players / traits / owned | 89 / 85 / 104 / 125 / 153 / 92 | | |
| 4480x1440 듀얼 모니터 스크린샷(기본 묶음) | 330ms | 335 | 339 |
| ChangeDetector.update(1080p) | 0.7ms | | |

- 기본 경로가 처음으로 목표(300ms) 안에 들어왔다(Intel Mac openvino 509ms 대비). 앱 루프는 바뀐 묶음만 읽으므로 평상시는 71~130ms 수준.
- 항상 읽는 한 줄 인식이 4칸 → 9칸으로 늘었다(화면 상태 신호). 배치라 추가 비용은 작다. 워터마크를 한 줄로 못 읽은 HUD 화면(전투 등)만 검출을 한 번 더 한다(약 10ms).

---

## 8. 테스트

- 기준선(변경 전, 이 PC): `pytest --ignore=tests/advisor/test_advisor_units.py` → 601개 중 **588 passed, 10 failed, 3 skipped**.
- 변경 후: 648개 중 **637 passed, 8 failed, 3 skipped**. (+47개: `tests/test_vision_1080p.py` 45, session 1, config 1)
- 고친 기존 실패 2개(**Windows 한글 경로**, vision 책임): `cv2.imwrite`가 Windows에서 한글 파일명을 깨뜨림("B.F. 대검" → "B.F. ëŒ€ê²€", `--debug`의 `라운드 1-4.rois.png` 저장 실패). `capture.save_image`(imencode + tofile)를 만들고 templates/ocr/app screenshot·loop 디버그 저장에 썼다.
- **남은 실패 8개 + 수집 오류 1개는 이번 변경과 무관한 이 PC 환경 문제**(오케스트레이터 전달):
  - `tests/test_stats_repository.py` 7개 + `tests/test_stats_convert.py::test_build_all_on_cached_snapshot`: 이 PC의 MetaTFT 원본 캐시 `data/raw/`가 불완전(클러스터 57개 중 상세 1개만). stats-researcher 영역. 캐시 재수집 또는 Mac에서 복사 필요.
  - `tests/advisor/test_advisor_units.py`: `import httpx2` 실패(`ModuleNotFoundError`). venv에 typesafe_sdk/httpx2가 없음(advisor extra 미설치로 보임). jev-strategist/app-integrator 영역.
- 새 테스트(`tests/test_vision_1080p.py`): 듀얼 모니터 분할·게임 모니터 선택(스테이지 OCR, 기억), 같은 높이 모니터 2등분 후보, `pick_monitor`, 한글 경로 저장, 화면 상태 규칙 14개, 워터마크 파서, 빨간 체력바 개수(초록·주황·정사각 제외), 증강 줄 칸 탐지, 증강 매칭·자기 일치, 모호한 아이콘 → None, 줄 없음(스테이지 1 → [], 그 밖 None), READ_MODES = app 표, **모드 8개 x 호출 헬퍼 = 표**, 1080p 보정·16:10 수치 고정, 원본 캡처 회귀(오답 0, screen_mode 전부 ok — 파일 없으면 skip).

---

## 9. 변경 파일

| 파일 | 변경 |
|---|---|
| `src/tft_advisor/vision/regions.py` | 16:9 1080p 보정(`HUD_DY_1080P`, 아이템 칸, 가격 칸, 특성 폭), 새 ROI 7개 + anchor, `BOTTOM_HUD_DY` 정리(`BOTTOM_HUD_DY_16X10`), `find_screens`/`screen_candidates`/`stage_bar_score`/`game_box_by_pixels` |
| `src/tft_advisor/vision/screen_mode.py` | 신호 6개 추가, `parse_board_count`, `count_enemy_bars`, 규칙 재작성(§4.2) |
| `src/tft_advisor/vision/recognizer.py` | 묶음 "owned", `READ_MODES`(모드 분기 단일 출처), 화면 상태 신호 배치 읽기, `pick_screen`/`screen_score`, `_read_augments_owned`, `STREAK_SIGN_FACTOR` 0.8, `augment_template_dir` 인자 |
| `src/tft_advisor/vision/icons.py` | `find_icon_row`, `AugmentIconMatcher`, `augment_cell_template` |
| `src/tft_advisor/vision/capture.py` | `save_image`(한글 경로), `MssSource(monitor="auto", scorer=)`, `pick_monitor`, `default_screen_score` |
| `src/tft_advisor/vision/templates.py` | `fetch-augments`, `harvest-augments`, 듀얼 모니터 캡처 자동 처리, `save_image` 사용 |
| `src/tft_advisor/vision/change.py` | 묶음 "owned" ROI |
| `src/tft_advisor/vision/ocr.py`, `vision/evaluate.py` | 글리프 저장 `save_image` / 문서 |
| `src/tft_advisor/app/session.py` | `GROUP_READ_MODES` = `READ_MODES`(combat·item_select·carousel·owned) |
| `src/tft_advisor/app/live.py` | `MssSource(..., scorer=recognizer.screen_score)` |
| `src/tft_advisor/app/screenshot.py`, `app/loop.py` | 디버그 PNG 저장을 `save_image`로(Windows 한글 경로) |
| `src/tft_advisor/config.py`, `config/settings.toml` | `[capture] monitor: int \| "auto"`, 기본 `"auto"` |
| `.gitignore`, `data/templates/README.md` | `data/templates/*/augments/`, `augments_screen/` |
| `tests/test_vision_1080p.py` (신규), `tests/test_vision.py`, `tests/app/test_session.py`, `tests/test_config_round.py` | §8 |
| `tests/fixtures/screens/raw/*.expected.json` | 초안 라벨 13개 (**gitignore, 커밋 금지**) |
| `data/templates/18/items/`(212), `augments/`(317), `items_screen/`(8) | 이 PC에서 재생성(gitignore) |

---

## 10. 앱 / QA 전달

**app-integrator**
1. `GROUP_READ_MODES`를 바꿨다(§4.4). 전투 중에도 hud/shop/items/traits를 읽으므로, **전투 화면에서 상점을 읽었는데 값이 없으면 낡은 상점을 버린다**(병합 규칙 2). 예전에도 전투가 planning으로 분류돼 같은 동작이었다.
2. `combat`이 실제로 나오기 시작한다 → KEEP_MODES 동작(직전 추천 유지)이 처음으로 실사용된다. 준비로 돌아오면(워터마크) 정상 추천.
3. `item_select`: 모루·악의 여단 선택 화면. 아이템 벤치는 읽는다.
4. `augments_owned`가 vision에서 온다(`field_source=vision`, 준비 화면만). None이면 기존 값 유지(`_CARRY_FIELDS`). 수동 입력 UI는 여전히 필요(모르는 증강·모호한 아이콘 19종).
5. `[capture] monitor` 기본 `"auto"`, `live.py`에서 `scorer=recognizer.screen_score`를 넘긴다. 첫 실행 로그 `게임 모니터 자동 선택: N번` 확인 요망.
6. 계약 제안: 특수 선택지(`select_offer`), 최종 순위(`placement`) — 필요하면(§4.3).

**jev-strategist**: `streak`가 이제 신뢰도 0.8 x OCR 점수(≥ 0.6)로 advisor 입력에 들어간다(연패는 음수). 경제 판단 로직이 연패를 쓰는지 확인 요망.

**qa-validator**: 원본 캡처 회귀는 `tests/test_vision_1080p.py::test_raw_*`(파일 없으면 skip). 수치는 초안 라벨 기준(잠정). PvE 전투(1-x, x-7)는 의도적으로 `planning` 0.7.

**stats-researcher**: 이 PC의 `data/raw/` MetaTFT 캐시가 불완전해 stats 테스트 8개가 실패한다(§8).

---

## 11. 남은 질문 / 다음 라운드

1. 사용자 확인(확인표 §3): 연패 부호, 증강 이름 2개 + **5-5 가운데 증강 이름**, 제거기 숫자, 악의 여단 분류, "6" 상자, 불타는 묘목.
2. 증강 선택 화면(3-2, 4-2) 1080p 캡처 — 1080p에서 증강 이름 ROI 확인(방송 기준값 그대로), 실버/프리즘 글리프 색.
3. PvE 준비/전투 쌍 — 크립 라운드 전투 구별(지금은 안전하게 준비로 봄).
4. 아이콘 공유 증강 19종(X / X+) — 글리프 색·테두리로 등급을 가를 수 있는지(표본 필요).
5. 16:10에서 새 ROI 7개는 유도값(미검증). 16:10 캡처는 이 PC에 없다.
6. 보드/벤치 유닛 인식은 여전히 없음(Phase 5). 5-5 준비(보드 9기)·2-5(벤치 9칸)가 좋은 시작 표본이다.
