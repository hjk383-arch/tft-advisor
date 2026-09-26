# 25 vision-engineer: 유닛 사진 DB — 모으기(대기) → 사람 검토 → 승인만 사용

작성일: 2026-09-23 / 작성자: vision-engineer / 커밋하지 않음
요청(사용자): "게임하면서 확실히 알아본 유닛은 사진을 저장해 모든 유닛의 이미지 DB를 만들고, 나중에 내가 전부 맞게 기록됐는지 검토한다."
특성 패널 추론에 오래 기대는 것은 약하다(상징·증강·가려진 유닛·보드 전용) → 챔피언별 **검토된** 사진 DB를 쌓는다.
안전 원칙 그대로: 화면 픽셀만, 입력 자동화 없음. 사진은 Riot 아트워크라 커밋하지 않는다(`data/templates/*/units_screen/` gitignore).

## 1. 결정 요약

| 항목 | 결정 | 이유 |
|---|---|---|
| 저장 위치 | 기존 `data/templates/{set}/units_screen/` 유지. 승인 = `{apiName}/`, 대기 = `_pending/{apiName}/`, 삭제 = `_trash/{apiName}/` | 옛 라이브러리(승인)와 호환, `_`로 시작하는 폴더는 라이브러리가 건너뛴다 |
| 메타데이터 | 크롭마다 옆에 `{id}.json`(`CropMeta`) | 옮기기(승인·이름 고치기)가 파일 두 개 이동으로 끝난다. 동시 실행(게임 루프 + 검토 창)에도 한 파일씩이라 안전 |
| 신뢰 | **승인만** 이름 판정에 쓴다. 대기 = 기본 무시(`unit_pending_weight` 0) | 자동 확정은 틀린 표본을 스스로 키운다(QA 19 FAIL-1) |
| 자동 승인 | 없음. 예외: 구매 증거 자동 승인 설정(`unit_purchase_autoapprove`, **기본 끔**) | 구매 증거가 가장 강하지만 그래도 사람 확인이 기본 |
| 옛 `label_*` | 승인으로 친다(사용자 확인 라벨에서 수확) | |
| 옛 `auto_*`(승인 폴더에 바로 쓰던 자동 학습) | 시작할 때 **대기로 옮긴다**(`migrate_legacy`, 증거 `legacy_forced`) | 검토 전에는 믿지 않는다. 현재 디스크에는 0장이었다 |
| 옛 "칸 1개일 때만 디스크 저장" 규칙 | 대기에는 완화(아래 증거), 승인 폴더 직접 쓰기는 **없앰**(`_learn`은 메모리만) | 대기는 믿지 않으므로 넓게 모아도 된다 |

## 2. 증거 종류(무엇을 대기에 넣나) — `vision/unit_db.py UnitCollector`

| 증거 | 조건 | 강도 |
|---|---|---|
| `purchase` | 상점 칸이 **챔피언 X → 빈 칸**(= 샀다, 인식기가 그 프레임 상점을 읽었을 때) 또는 app 장부의 구매 이벤트(`collector.note_purchase(X, at)`) **그리고** 같은 창(3초) 안에 벤치에 **새 칸 하나**가 생겼다: 다른 벤치 칸은 그대로(옮긴 게 아님), 보드 수 그대로(보드에서 내린 게 아님), 새 유닛 1성(합성이 끼지 않음). 창 안의 구매가 모두 같은 챔피언이고 새 칸 수와 같아야 짝짓는다 | 가장 강함(점수 0.95) |
| `traits` | 특성 패널 풀이가 **하나**, 찾은 보드 칸 수와 일치(가려진 유닛 없음, `missed == 0`), 패널 판독 신뢰도 >= 0.75, 그 칸이 구속 배정(`forced`/`traits`)으로 이름 신뢰도 >= 0.85 | 강함 |
| `duplicate` | 같은 프레임에서 이름이 확정된 보드 유닛과 같은 모델인 벤치 유닛(`name_source == "duplicate"`, 신뢰도 >= 0.8) | 보통 |

- 3성 합성·유닛 끌기·보드↔벤치 이동·두 챔피언 동시 구매는 모두 "모호"로 버린다(테스트 5종).
- 크롭마다 기록: 챔피언 apiName, 성급(성급 배지 판독), 증거·점수, 판 ID(`UnitCollector.game`, 새 판 `reset()`), 스테이지, 맵 서명
  (보드 가운데 바닥 Lab 중앙값 6자리 16진수), 시각(ISO), 원본 프레임 해시(64x36 축소 sha1), 칸("bench:3"/"board:1,2").
- 중복 제거: 같은 그림(sha1) · 거의 같은 그림(같은 챔피언 기존 크롭과 색 분포 닮음 >= 0.95) → 저장 안 함.
- 상한: (챔피언, 성급, 증거)마다 대기 8장. 넘으면 **맵 서명이 새로운** 크롭만 받는다(맵마다 배경이 달라 표본 다양성이 중요, 23 보고 §3).

실측(원본 캡처로 자동 수집 시험, 임시 복사본 라이브러리): 2-2·2-5는 저장 0(라이브러리 `label_*`와 같은 그림 → 중복 제거),
2-6은 보드 5기 `traits` 대기 5장. 라이브 2-3은 풀이가 3개라 0(구매 증거는 프레임 두 장 이상이 필요).

## 3. 검토 창 `app/unit_review.py` (새 파일, app-integrator 파일은 건드리지 않음)
- 실행: `python -m tft_advisor review-units` (그리고 `--coverage`로 콘솔 표). `__main__.main()` 맨 앞에서 첫 인자가
  `review-units`면 이 모듈로 넘긴다(다른 인자 처리는 그대로). 모듈 직접 실행도 된다: `python -m tft_advisor.app.unit_review`.
- 화면: 위 = 적용 범위 요약("승인된 챔피언 N/74 · 검토 대기 M장 · 사진 없는 챔피언 K명" + 없는 챔피언 이름),
  왼쪽 = 챔피언 목록(대기 있는 / 전체 / 사진 없는 챔피언 필터, 검색, 맨 위 "대기 전체"), 오른쪽 = 사진 격자(대기/승인/전체 보기,
  여러 장 선택), 아래 = 고른 사진의 근거(증거·점수·스테이지·칸·맵·시각·판·프레임·파일).
- 동작/단축키: A 승인 · U 승인 취소 · D/Delete 삭제(휴지통으로 옮김) · R 다른 챔피언으로(한국어 이름 검색 대화 상자) ·
  1/2/3 성급 · 0 성급 모름 · F5 새로 고침. 여러 장에 한 번에 적용.
- 창을 닫을 때 바뀐 것이 있으면 `on_changed`를 부른다 → 실행 중 앱은 `recognizer.unit_namer.request_reload`를 넘기면
  다음 인식 프레임(인식 스레드)에서 승인 크롭을 다시 읽는다(`UnitNamer.reload`, UI 스레드에서 라이브러리를 직접 바꾸지 않는다).
- 로직은 Qt 없는 `ReviewModel`에 있고 창은 그리기만 한다(테스트 쉬움).

## 4. 적용 범위 `review-units --coverage`
대상 = 정적 데이터에서 **특성이 있는** 챔피언(훈련 봇·골렘·모루 등 특성 없는 유닛 17종 제외) = 74명(럭스 특성별 변형 포함).
챔피언마다 승인 ★1/★2/★3/성급 모름, 대기 수, 없음 표시. 현재 디스크: **승인 8/74**(옛 확인 라벨 30장), 대기 0.
`label_*` 30장은 성급 정보가 없어 "성급 모름" 칸에 들어간다(검토 창에서 1/2/3으로 지정 가능).

## 5. app-integrator에게(연결 제안 — 내가 app/ 기존 파일을 고치지 않았다)
1. **트레이/인식 확인 창 버튼** "유닛 사진 검토": UI 스레드에서
   `self._review = open_review_window(static, on_changed=recognizer.unit_namer.request_reload)` (창 참조를 들고 있을 것).
2. **장부 구매 → 수집기**: `SessionTracker.observe()`가 돌려준/기록한 `LedgerEvent` 중 상점 구매(`kind == "add"`,
   증거가 상점)마다 `recognizer.unit_namer.collector.note_purchase(ev.unit_id, ev.at)` (collector가 None이면 건너뛴다).
   인식기는 상점 칸 "챔피언 → 빈 칸"도 스스로 보지만, 장부 이벤트가 오면 같은 구매를 두 번 세지 않는다(출처별 최대).
3. **새 판**: `recognizer.unit_namer.reset()` — 판 ID·힌트·연속 일치 기록을 새로 한다(23 보고의 힌트와 같이).
4. **설정 키 제안**(`config.py VisionCfg`, 지금은 `getattr` 기본값으로 읽는다):
   `unit_pending_weight: float = 0.0`(0 = 대기 무시, 0~1 = 닮음에 곱해 순위에만), `unit_purchase_autoapprove: bool = False`.
   `unit_autolearn` 설명은 "증거 크롭을 검토 대기에 모은다"로 바꿔 주세요(승인 폴더에 직접 쓰지 않는다).
5. `review-units` 인자는 `__main__.main()` 첫 줄에서 가로챈다(3줄). 파서 구조를 바꾸고 싶으면 서브커맨드로 옮겨도 된다.

## 6. 테스트
- `tests/test_unit_db.py`(15): 대기는 기본 이름에 안 쓰임 / 가중치 0.5 순위만 / 승인하면 이름, `label_*` 승인·휴지통 제외,
  옛 `auto_*` 대기로 이동, 중복·거의 같은 그림·상한(맵 서명), 이름 고치기(파일+json 이동, 메모), 성급, 적용 범위 계산·로스터,
  가짜 장부 구매 이벤트 → 새 벤치 칸 라벨, 상점 칸 빈 칸 → 구매(+자동 승인 설정), 모호한 구매 5종 거절,
  특성 증거(가려진 유닛이면 안 모음), `UnitNamer.from_static(autolearn)` 수집기·이동·`reload`.
- `tests/app/test_unit_review.py`(3, Qt offscreen): 모델 필터·검색·설명, 창에서 성급·승인·다른 챔피언·삭제·닫을 때 `on_changed`,
  `--coverage` 콘솔 출력.
- `tests/test_vision_units.py`: 옛 "칸 1개 강제 이름은 디스크 저장" 테스트를 "대기에만, 승인 폴더에는 안 씀"으로 바꿈.
  디스크 폴더 검사 테스트는 `_pending`/`_trash`를 건너뛴다.

## 7. 남은 것 / 위험
- 구매 증거는 인식 루프가 상점과 벤치를 **같은 3초 창 안에서** 읽어야 한다. 변화 감지가 벤치만 다시 읽고 상점을 몇 초 뒤에 읽으면
  짝을 놓친다(저장 안 함 — 안전한 쪽). 장부 이벤트 연결(§5-2)이 들어오면 보완된다.
- 성급 2·3 사진은 합성으로만 생기므로 `purchase`로는 모이지 않는다(1성만). `traits`/`duplicate`와 검토 창의 성급 지정이 채운다.
- 대기가 쌓이면 디스크가 늘어난다(크롭 112x112 PNG 약 20KB). 상한·중복 제거로 챔피언당 수십 장 수준.
- 검토 창과 게임 루프가 **같은 파일**을 동시에 옮기는 경우(루프가 대기에 쓰는 순간 창이 같은 챔피언 폴더를 새로 고침)는
  파일 단위라 깨지지 않지만, 창 목록이 한 박자 늦을 수 있다(F5).

## QA 27 F1 수정 (2026-09-24, vision-engineer)

QA 27 §2.3 F1: 끝난(또는 짝이 없는) 구매가 창(3초) 안의 벤치 이동·판독 깜빡임과 짝지어져 **다른 유닛 크롭이 산 챔피언 이름으로** 저장됐다.
세 경로(상점+장부 이중 보고 뒤 끌어 옮기기 / 보드 합성 구매 뒤 끌어 옮기기 / 벤치 판독 깜빡임)를 모두 막았다.

### 새 규칙 (`vision/unit_db.py` `UnitCollector`)
| 규칙 | 내용 | 막는 경로 |
|---|---|---|
| 구매 한 건 합치기 | 같은 챔피언의 상점(`shop`)·장부(`ledger`) 보고는 `window_s` 안이면 한 건(`_Buy.sources`). 짝지어 쓴 구매는 `used`로 창 끝까지 남겨 늦게 온 메아리를 흡수한다 | 이중 보고 |
| 변화 = 무효 | 벤치 칸 사라짐 · 기존 칸 성급 바뀜 · 한 프레임에 칸 2개 이상 추가 · 보드 서명(유닛 수 + 성급 구성) 바뀜 → 그때까지의 구매·새 칸을 모두 버린다(`_disturb`). 합성(성급 상승·칸 사라짐)으로 끝난 구매도 여기서 소비된다. 변화 시각 이전 시각의 늦은 장부 보고는 처음부터 소비됨으로 들어온다 | 합성 뒤 옮기기, 깜빡임 |
| 조용한 창 | 새 칸은 직전 `window_s` 안에 변화가 없어야 한다 | 옮기기(들어 올림 → 내려놓음) |
| 빈 칸 이력 | 새 칸은 직전 `MIN_EMPTY_FRAMES`(=2) 프레임 내내 비어 있던 칸이고, 벤치 수가 정확히 +1 | 깜빡임(한 프레임 누락 뒤 재등장) |
| 모순 거부 | 산 챔피언의 승인 크롭이 있고, 크롭이 다른 챔피언 승인 크롭을 `CONTRADICT_MIN`(0.8) 이상·`CONTRADICT_MARGIN`(0.1) 이상 더 닮았으면 저장하지 않는다 | 남은 오류의 안전망 |
| 보드는 육각칸을 보지 않는다 | 서명 = (수, 성급 구성). 전투 중 유닛 이동으로 증거가 끊기지 않게 | - |

- `note_purchase()`도 바로 짝짓기를 시도한다(장부 보고가 인식 뒤에 와도 같은 프레임의 새 칸과 짝).
- 비용: 구매 증거가 더 드물다(게임 시작 첫 두 프레임, 옮기기·합성 직후 3초 안의 구매는 버린다). 버리는 쪽이 안전하다(QA 19 FAIL-1과 같은 원리).
- QA 재현 스크립트(`repro_buy.py`)의 A·D는 이제 아무것도 저장하지 않는다 — 앞 프레임이 하나뿐이라 "빈 칸 이력" 규칙에 걸린다(의도). 앞 프레임을 둔 같은 흐름은 테스트가 덮는다.

### QA 27 W1: `UnitSlot.corroborated`
- `vision/board.py` `UnitSlot.corroborated: bool | None = None`. `UnitNamer.name()`이 `SlotName.corroborated`를 싣는다(이름 없으면 None).
- `app.recog_view.is_guess`는 이미 이 속성을 먼저 읽는다 → 라이브러리 닮음 하나만인 이름(여러 프레임 일치로 나간 것 포함)이 신뢰도와 상관없이 "(추정)"으로 표시된다. app 코드는 고치지 않았다.

### 테스트
- `tests/test_unit_db.py::test_qa27_stale_purchase_never_labels_another_unit` — xfail 제거, 3경로 통과. 이중 보고 경로는 진짜 구매 짝(bench:2)이 저장되는지도 본다.
- 새: `test_shop_and_ledger_reports_of_two_same_champion_buys_pair_each_once`, `test_new_slot_after_a_recent_disturbance_is_not_purchase_evidence`(창이 지나면 다시 모음), `test_purchase_crop_contradicting_approved_crops_is_not_saved`(모순 검사를 끄면 실패함을 확인).
- 기존 구매 테스트 3개는 앞 프레임을 하나 더 넣었다(빈 칸 이력 2프레임). 모호 테스트는 이력이 충분한 상태에서 다섯 경우가 여전히 거부된다.
- `tests/test_vision_units.py::test_named_slots_carry_corroborated_flag_for_recog_view`.
- 전체: `PYTHONIOENCODING=utf-8 .venv\Scripts\python.exe -m pytest` → 실패는 기존 Windows 4건뿐(api_key 마스크, setup 권한 상자, credentials 0600 2건).

## 30: 수집 정책 — 벤치만 · 알아보면 그만 · 품질 검사 · 스테이지 (2026-09-25, vision-engineer)

배경: 사용자 라이브 판 대기 크롭 53장 검토 → 승인 47, 삭제 6(세주아니 벤치 0 분홍 튜브 · 쉔 보드 금화/흐림 · 피들스틱 2장 스킬
효과·피해 숫자 "50" · 아칼리 ★2 보드 = 실제 바루스(자리 뒤바뀜 수정 전) · 아칼리 구매 크롭에 피들스틱 낫·연기 침범).
사용자 정책: "저장된 이후에는 인식이 되는 캐릭터는 더이상 안 찍어도 된다. 사진은 대기석에 있을 때 찍어."

### 규칙 (`vision/unit_db.py` `UnitCollector`)
| 규칙 | 내용 |
|---|---|
| 벤치만 | 모으는 증거: `purchase`(가장 강함) · `duplicate`(확정 보드 유닛과 같은 모델, **라이브러리는 아직 모름**) · 새 `library`(벤치 칸의 **뒷받침된** 라이브러리 이름, 신뢰도 0.6~0.8 = 아직 확실하지 않은 새 자세·새 맵). **보드 `traits` 수집은 없앴다**(효과·피해 숫자·겹친 유닛·잘린 머리). 장부 이름이 `unit_merge` 규칙으로 벤치 칸에 붙는 경로는 vision이 알 수 없어 넣지 않았다(그 배치는 지금 순서대로라 믿을 수 없다 — 30 보고 §6) |
| 충분하면 그만 | 그 챔피언·성급의 **승인** 사진이 `collect_until`장(설정 `[vision] unit_collect_until = 3`, 0 = 제한 없음) 이상이면 모으지 않는다. 성급 모름(옛 `label_*`)은 ★1로 센다. ★2·★3은 따로 센다 → 새 성급은 처음 보이면 모은다. `UnitImageDB.approved_count()`(캐시, 옮기기·승인 시 비움) |
| 알아보면 그만 | 이번 칸을 라이브러리가 **같은 챔피언**으로 뒷받침과 함께 신뢰도 >= `RECOGNIZED_CONF`(0.8)로 알아봤으면 모으지 않는다(구매 짝 포함) |
| 전략가 | 전략가 이름표(두께 9~18px 초록 막대)가 위에 있는 벤치 칸(`bench_memory.tactician_cells`, `FrameContext.tactician`)은 모으지 않는다 |
| 품질 거부 | `crop_quality()`(모델 픽셀 기준): 밝고 진한 빛(V>=235·S>=100)이 모델의 30% 이상(금화·폭발) · 청록 선택 윤곽이 크롭의 8% 이상 |
| 품질 표시 | 빛 10% 이상 · 청록 4% 이상 · 옆 칸 모델 침범(가운데와 이어지지 않고 좌우 끝에 닿은 조각 5% 이상) · 머리 잘림(위쪽 줄 가운데 45% 이상) → 저장하되 note "품질: …", 점수 x0.7, 자동 승인 안 함 |
| 스테이지 | app이 매 프레임 `collector.set_stage(세션 합친 스테이지)`(오버레이 표시값, `app/loop._feed_unit_namer`). 없으면 프레임 판독값을 쓰되 이번 판에서 본 가장 늦은 스테이지보다 앞서면 비운다 |
| 기본값 | 대기만(`unit_purchase_autoapprove = false` 그대로). 표시가 붙은 크롭은 설정이 켜져 있어도 자동 승인하지 않는다 |

진단: `UnitCollector.skipped` = 이유별 건너뛴 수(enough / recognized / tactician / quality).

### 품질 기준 실측(승인 78장 · 삭제 11장, `data/templates/18/units_screen`)
- 승인: 거부 0, 표시 4(아칼리 옆 칸, 오른 효과+머리, 세주아니 청록+옆 칸, 코그모 옆 칸).
- 이번 삭제 6장: 쉔 금화 **거부**, 피들스틱 청록 윤곽 **거부**, 피들스틱 "50" 표시(효과), 아칼리 구매 연기+낫 표시(효과·옆 칸),
  세주아니 튜브 표시(머리 잘림 — 튜브 자체는 못 가린다), 아칼리 ★2(실제 바루스)는 품질 문제가 아니라 이름 문제(30 보고의 자리
  뒤바뀜 수정으로 막힘; 보드 수집도 없앴다).
- 벤치 크롭 상자(체력바 위 끝 +8 ~ +120px, 1080p)는 벤치 유닛 대부분의 머리~발을 덮는다. 키 큰 모델(세주아니 탄 사람, 카르마
  날개)은 위가 닿는다 → "머리 잘림" 표시. 상자를 바꾸면 기존 승인 사진과 기하가 달라져 이름 비교가 흔들리므로 바꾸지 않았다.

### 스테이지 "1-3/1-4" 조사
메타데이터의 스테이지는 그 프레임 OCR 값(`FrameContext.stage`)이었다. 크롭 시각(17:51:43~17:52:39, 판 시작 17:51:01)과
라이브 3 캡처(17:54:51 = 2-2)를 보면 1-3/1-4 표기가 틀렸다고 단정할 근거는 화면에 없었다(1-4에 3코스트 피들스틱은 어색하다).
어느 쪽이든 이제 세션 스테이지(오버레이 값)를 쓰고, 세션 값이 없을 때 뒤로 가는 판독은 버린다.

### 다른 담당에게
- 검토 창(`app/unit_review.py`)이 note의 "품질: …"을 보여 주고 점수 순으로 뒤에 두면 정책이 완성된다(나는 고치지 않았다).
- 설정 키 `unit_collect_until`은 `config.py VisionCfg`에 한 줄, `config/settings.toml [vision]`에 설명과 함께 추가했다.

### 테스트(`tests/test_unit_db.py`)
- 보드 수집 없음: `test_board_crops_are_never_collected_even_with_a_unique_trait_solution`(옛 traits 증거 테스트 대체)
- `test_collection_stops_after_enough_approved_but_a_new_star_is_still_collected`, `test_label_crops_without_star_count_as_one_star`
- `test_already_recognized_slot_is_not_collected_and_corroborated_middle_library_name_is`(구매 짝 포함)
- `test_crop_quality_rejects_glow_and_selection_outline_and_flags_neighbour`, `test_collector_applies_quality_and_tactician_rules`
- `test_crop_stage_uses_the_session_stage_and_drops_a_stale_frame_read`
- 테스트용 단색 크롭 `img()`는 0.75배로 어둡게 했다(원색 255는 "강한 빛"에 걸린다).

## 35+: 이름 미상 벤치 크롭 → 판 뒤 사람이 이름 주기 (2026-09-25, vision-engineer)

사용자 결정: "벤치에 이름 미상이면 그때 스샷 찍어서 인식하도록 해" → AI/API 호출 없이, 이름 미상 벤치 유닛 크롭을 검토 대기에
**이름 없이** 모으고, 판이 끝난 뒤 검토 창에서 이름을 주면 그 챔피언 승인 사진(라이브러리)이 된다.

### 수집 (`vision/unit_db.py` `UnitCollector.observe_unknown`, `Recognizer._collect_unknown`)
- 언제
  - 준비 단계 · 체력바가 보이는 프레임(`bench_held` 아님) · 내 맵(`unit_tracker.frozen` 아님).
  - 정체 추적 **뒤의 최종 판독**에서 이름 없는 벤치 칸이 연속 `UNKNOWN_MIN_FRAMES`(2) 프레임이면.
  - 벤치만. 전략가 칸 · 품질 거부(강한 빛·청록 윤곽)는 뺀다. 품질 표시는 note로.
- 어디
  - `_pending/_unknown/unknown_{hash}.png + .json`: 챔피언 자리 = `UNKNOWN_CHAMPION`("_unknown"), 근거 `unknown`.
  - 메타데이터: 세션 스테이지 · 배지 성급 · 맵 서명 · 칸 · 판 ID.
  - 이름 판정·라이브러리에는 쓰지 않는다(`_`로 시작하는 폴더는 표본이 아니다).
- 중복·상한
  - 같은 판에서 같은 유닛(닮음 >= 0.70, 실측 다른 챔피언 최대 0.59)은 2장까지(처음 + 20초 뒤 한 장). 옮겨 다녀도 같은 유닛이다.
  - 판마다 `unknown_cap`(20)장. `reset()`(새 판)에서 다시 센다.
- 이름 후보(`CropMeta.suggestions`, 최대 8)
  - 순서: 장부 보유인데 이름 붙은 칸이 없는 챔피언(`UnitNamer.hints`) > 특성 풀이 자리 미상(`BoardRead.unplaced`) > 이번 판 상점에 나온 챔피언.
  - 같은 판에서 그 유닛(닮은 크롭)에 나중에 이름이 붙으면 그 이름을 후보 **맨 앞**에 달고 note를 남긴다.
    승인하지는 않는다(`UnitImageDB.suggest`).
- 적용 범위: `Coverage.unknown`, 요약 "… · 이름 미상 N장"(`review-units --coverage`·검토 창 머리줄).

### 검토 창 (`app/unit_review.py`)
- 챔피언 목록 맨 위에 "? 이름 미상 — N장". 있으면 창을 열 때 그것부터 보인다.
- 사진을 고르면 후보 버튼 최대 5개("1 카르마" …)가 보인다. **이 목록에서는 숫자 키 1~5가 후보 고르기**다(다른 목록에서는 성급).
  R(검색)도 된다.
- 이름을 주면 그 챔피언 **승인** 폴더로 옮긴다(`UnitImageDB.label_unknown`, 근거 `user` "사용자 이름" — 사람의 이름이 확인이다).
  이름 없이 승인(A)은 되지 않는다. 삭제(D)는 휴지통.

### 실제 프레임 확인(임시 DB 사본, 사용자 폴더는 건드리지 않음)
- live4를 3번 인식 → 벤치 5칸 모두 이름 미상 크롭 5장(★2 둘, ★1 셋, 스테이지 2-5).
- 후보는 보드 자리 미상 알리스타·렉사이 + 상점 자야·어미 부리·코그모·심술두꺼비(보드에 이름이 붙은 오른은 빠짐).
- 벤치 0은 "품질: 청록 윤곽"(마우스가 올라가 있던 칸), 벤치 1은 "머리 잘림" 표시.

### 테스트
- 새 `tests/test_unit_unknown.py` 6개:
  - 2프레임 뒤 저장 + 후보 순서 + 라이브러리 밖 + 적용 범위
  - 이름 있음/끊김은 저장 안 함
  - 유닛당 2장 · 20초 · 판 상한 · reset
  - 품질·전략가
  - 나중 이름이 첫 후보
  - 검토 창(offscreen): 이름 미상 목록 먼저, 후보 버튼, 이름 없이 승인 불가, 숫자 키 2 → 승인 폴더 · 근거 user · 라이브러리에 들어감, R → 다른 챔피언
- 전체: 알려진 Windows 4건뿐(다른 에이전트가 장부를 고치는 중인 실행 제외, §35-8 참고).
