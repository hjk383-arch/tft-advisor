# 04 QA: vision 모듈 점진 검증

작성일: 2026-09-22 / 작성자: qa-validator / 브랜치 `phase3` (커밋하지 않음)
범위: `src/tft_advisor/vision/`와 경계면(vision → GameState → advisor, 이름 → ID, pyproject vision extra). 소스는 고치지 않았다. 추가한 파일은 `tests/`와 `_workspace/qa_scripts/`에만 있다.
전제: ROI를 측정한 fixture 7장(방송 크롭)으로 평가도 했다. 그래서 fixture 점수는 **정확도가 아니라 회귀 기준선**이다. 정확도는 **BLOCKED(사용자 캡처 필요)**로 분류했고 FAIL로 세지 않았다.

재현 스크립트 (`.venv/bin/python …`, Jev는 mock만 썼고 라이브 호출은 없다)
- `_workspace/qa_scripts/vision_e2e.py [--json OUT]`: fixture → `recognize_file` → 계약 재검증 → `create_advisor("mock").advise`
- `_workspace/qa_scripts/vision_name_margins.py`: 이름 → ID 해석, 후보 쌍 유사도, 자모 1개 변형 적대 검사(결과는 `vision_name_margins.out.json`)
- `_workspace/qa_scripts/vision_roi_scale.py`: ROI 기하 검사와 1080p·1440p·16:10 레터박스·창모드 합성 프레임 인식 비교
- 회귀 테스트: `tests/test_vision_qa04.py` 16개(14 pass, strict xfail 2 = V2 결함)

## 요약: PASS 9 / FAIL 2 / WARN 6 / BLOCKED 1

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | 제약 준수(화면 픽셀만 사용) | **PASS** | `src/` 전체에서 pymem·ReadProcessMemory·OpenProcess·ctypes·pyautogui·pynput·win32api/gui/process·psutil·SendInput·keyboard/mouse·frida·subprocess·Quartz/CGEvent를 grep했고 0건이다(advisor의 `KeyboardInterrupt`는 예외 처리라 무관). venv에도 해당 패키지가 없다. 캡처는 `mss`만 쓴다(`capture.py:95-127`). 버튼 ROI는 앵커로만 쓰고 클릭하지 않는다. 네트워크는 오프라인 CLI의 CDragon 아이콘 다운로드(`templates.py`)와 rapidocr 첫 실행 모델 다운로드뿐이며, 둘 다 게임 프로세스와 무관하다. 기존 가드 테스트 `test_vision.py:34`도 있다 | - | - |
| 2 | `recognize()` 출력 = 계약에 맞는 GameState | **PASS** | 7/7 fixture가 `GameState.model_validate(dump)` 왕복을 통과했다. `model_construct` 같은 검증 우회는 없다(`recognizer.py:145`에서 생성자를 쓴다). "값이 None ⇔ confidence 키 없음" 규칙이 7장 전부에서 성립한다(`test_fixture_states_are_contract_valid_and_confidence_keyed`) | - | - |
| 3 | 저신뢰 필드가 advisor 입력에서 빠지는지 | **PASS** | `features.py:143-186 build_view`가 모든 스칼라 필드를 `is_reliable(f, state_min_confidence=0.6)`로 거르고, 칸·증강·아이템은 항목별로 한 번 더 거른다(`scoring.py:337`, `jev_state.py:235`, `features.py:139`). vision은 필드 신뢰도를 min(항목)으로 만든다. 그래서 필드가 신뢰되면 모든 항목도 신뢰되고, advisor의 항목 필터 때문에 증강 인덱스가 밀리는 일이 없다(`test_reliable_fields_have_reliable_sub_items`). 실측으로 빠진 필드: active_traits(0.54, 설계대로), 3-5 shop(0.557, 아래 #11) | - | - |
| 4 | vision이 None으로 두는 필드 ↔ advisor §4.3 폴백 | **PASS** | board/bench는 항상 None이고 `units_known=False`가 된다(§4.3b: copies_owned 키를 빼고 S1-noboard). hp가 None이면 `hp_bucket=unknown`이다(§4.3a). items가 None이면 `items_known=False`이고 장착 추적을 초기화한다(`engine.py:167-170`). augments_owned가 None이면 빈 목록이 된다. augment_select 화면에서 offer가 None(3개 중 일부만 매칭)이면 A 질문이 생략된다(`scoring.py:406`). active_traits는 vision이 채우지만 advisor는 읽지 않는다(무해) | - | - |
| 5 | fixture → recognize → mock advise, 전 구간 | **PASS** | **7/7**에서 Recommendation이 유효하다(`Recommendation.model_validate` 통과). target_comps는 3개, jev_used=True(mock)다. 2-1 증강 화면은 pick=`DA_SeraphimsStaff`이고 제시된 3개 안에 있다. 상점 구매 추천: 2-1 [0,2,3,4], 2-5 [0,1], 3-3 [1,3,4]. 3-5는 shop 필드가 빠져서 추천이 없다(#11). advisor 시간 4~38ms. `test_fixture_vision_to_mock_advisor_end_to_end` | - | - |
| 6 | fixture 이름 → `data/static/18` ID | **PASS** | 상점 챔피언과 특수 상품 25칸, 증강 3개가 모두 해석된다(`vision_name_margins.py` 1절). 상점 풀 챔피언 이름은 전부 자기 자신으로 정확히 매칭된다(`test_champion_names_exact_match_themselves`) | - | - |
| 7 | **이름 매칭: 확신하는 오답(confident wrong match)** | **FAIL** | `matching.py:77` 수락 규칙 `score ≥ 85 OR (≥60 AND margin ≥15)`은 85점 이상이면 **margin을 보지 않는다**. 서로 다른 ID인데 이름 유사도가 85 이상인 쌍: **상점 14쌍**("1~5단계 집결"이 숫자 한 글자만 다름, 91.7), **증강 98쌍**(I/II/III 등급, 레거시 중복), 특성 0쌍. 자모 1개를 치환·삭제한 적대 변형에서 다른 ID를 수락한 비율: 상점 109/53,070(0.21%, 챔피언만은 10/12,585, 예: "엘리스" 변형이 알리스타로 87.5점·margin 1.8), 증강 **1,579/99,994(1.58%)**. 실례: `"6단계 집결"`은 5개 후보가 같은 거리인데 임의 ID를 수락한다. `"영원한 브론즈 Il"`(II 오독)은 `DA_BronzeForLifeI`로 97.3점·margin 2.6에 수락되고, 신뢰도가 약 0.97이어서 advisor 임계를 통과한다. 규칙에 "85 이상이어도 margin ≥8"을 넣어 모의 실험하면 상점 109→9, 증강 1,579→**0**이 된다(증강 변형 중 19.7%는 기권하는데, 대부분 등급 숫자가 애매한 경우다). strict xfail 2개로 고정했다 | vision-engineer | **V2** |
| 8 | ROI 정규화와 content-box(scale/offset) | **PASS** | 기하: 모든 ROI가 1920x1080, 2560x1440, 3840x2160, 1920x1200+cb(0,60), 2560x1440 창(200,150,1600x900) 사상에서 ±1px 이내이고, `to_rel` 역사상도 맞다. 인식: fixture 4장을 **1080p·1440p·16:10 레터박스·창모드(cb 지정)**로 바꿔도 native와 모든 필드가 같다(예외 1건: 1440p 증강 화면의 hp가 None). cb 없이 창모드이면 모든 값이 None/UNKNOWN이다. 즉 **틀리지 않고 안전하게 실패한다**(`test_window_without_content_box_fails_safe`). 단 cb 자동 탐지가 없고 설정 키도 없다 → #13 R2 | - | (R2 content_box) |
| 9 | pyproject vision extra 플랫폼 마커 | **PASS** | `uv pip compile --extra vision --python-version 3.14`: win_amd64에서 onnxruntime 1.30.0, linux x86_64에서 onnxruntime 1.30.0, **mac x86_64에서 openvino 2025.4.1**(onnxruntime 제외), win arm64에서 onnxruntime을 설치한다. 마커 두 줄은 상호 배타이고 빈틈이 없다. 참고: mac arm64는 기본 타깃(macOS 13)에서 해석에 실패하고 `MACOSX_DEPLOYMENT_TARGET=14.0`이면 성공한다(onnxruntime 1.30 휠은 `macosx_14_0_arm64`뿐). 실제 Apple Silicon + macOS 14 이상이면 문제없다. 현재 venv(3.14.7 x86_64)의 설치 상태도 해석 결과와 일치한다 | - | (선택) 주석에 "arm64 mac은 macOS 14+" 추가 |
| 10 | 라이선스 | **WARN** | 파이썬 의존성은 모두 허용적 라이선스다(mss MIT, opencv Apache-2.0, rapidfuzz MIT, rapidocr/PaddleOCR 모델 Apache-2.0, onnxruntime MIT, openvino Apache-2.0). 다만 `data/templates/18/items/` **CDragon 아이콘 212개**는 Riot 저작물이다. CommunityDragon은 비공식 미러이고 라이선스가 없으며, Riot의 "Legal Jibber Jabber" 팬 정책(비상업·고지)의 적용을 받는다. 저장소 어디에도 출처·고지 표기가 없고 `.gitignore`에서 제외되지 않아 커밋될 수 있다. `fetch-items`로 다시 만들 수 있으니 커밋할 필요는 없다. openvino는 `openvino-telemetry`를 함께 설치한다(동의 기반이지만 확인 권장) | app-integrator | **L1** |
| 11 | shop 필드 신뢰도 이중 차감 | **WARN** | `recognizer.py:252` 필드 신뢰도 = min(칸) × 0.9^UNKNOWN. 그런데 advisor는 이미 칸 단위로 거른다(`scoring.py:337`). 3-5에서는 5/5칸이 **모두 맞았는데** 요릭 칸이 0.557이어서 shop 필드 전체가 빠졌고, 구매 추천이 사라졌다. 신뢰도가 높은 4칸의 정보가 버려진 것이다 | vision-engineer | **V3** |
| 12 | streak 신뢰도가 임계 경계에 걸림 | **WARN** | `STREAK_SIGN_FACTOR=0.6` × OCR 점수(≤1)이고 advisor 임계가 0.6이다. 연승이 0이 아닌 fixture 3장(2-5, 3-3, 3-5)이 모두 **정확히 0.600**이어서, 포함 여부가 반올림(`round(…,3)`)으로 결정된다. OCR 점수가 0.9991 미만이면 빠진다. 포함할지 뺄지 의도적으로 정해야 한다 | vision-engineer + 사용자(질문 3) | **V4** |
| 13 | vision 요청 7건 판정 | **판정 완료** | 아래 "요청 판정" 표 참고 | 각 담당 | R1~R5 |
| 14 | 성능 | **WARN** (Windows 수치 없음) | 이 Intel Mac(openvino)에서 워밍업 1.32s, 이후 0.53~1.40s/프레임(단독). 다른 QA 작업과 동시에 돌리면 3-3이 2.24s였다. **프로파일: 검출+인식 `read()` 12회/프레임 × 161ms = 1.93s(86%)**, 한 줄 인식 `read_line()` 5회 × 26ms = 0.13s. 스킬 목표(인식 <300ms, 전 구간 <2s)와 2~4 FPS 모두 미달이다. advisor는 mock에서 4~38ms이고 live는 예산이 최대 2s라, 변화 감지 없이 매 프레임 돌리면 전 구간 >2s가 된다. 변화 감지 전략은 아래에 있다 | vision-engineer, app-integrator | **V7** |
| 15 | 아이템 템플릿 수집(harvest)과 라벨 형식 불일치 | **FAIL** (잠복) | 캡처 요청서 3절은 `item_bench`에 **한국어 이름**을 쓰라고 안내한다. 그런데 `templates.py:113-116 harvest-items`는 라벨 값을 그대로 파일명(`{api}.png`)으로 쓰고, `IconMatcher`는 파일명(stem)을 `ItemRef.id`로 쓴다. 한국어 파일명(`B.F. 대검.png`)은 CanonicalId 패턴을 어겨 **`recognize()`가 ValidationError로 죽는다**(`ItemRef(id='B.F. 대검')`로 확인). 또 `_default_item_template_dir`(`recognizer.py:340`)는 `items_screen/`에 PNG가 **하나라도 있으면** CDragon 212개 전체를 버린다. 첫 harvest가 아이템 몇 개만 담으면 그 밖의 아이템은 인식하지 못하거나 가장 가까운 템플릿으로 오인할 수 있다. 가드 테스트 `test_item_template_stems_are_static_item_ids`를 추가했다(현재 PASS) | vision-engineer | **V1** |
| 16 | 자석 제거기 ID와 표시 이름 | **WARN** | 벤치의 자석 제거기가 항상 `TFT_Consumable_ItemRemover_UsesLeft7`로 나온다. 같은 묶음 안의 1위 변형이 임의로 뽑힌 결과인데, fixture 배지는 4·5·7로 다르다. 이 ID는 "7회"라는 틀린 사실을 담는다. 또 정적 데이터 items의 name_ko 22개에 `<rules>(7회 사용 가능!)</rules>` 마크업이 섞여 있어서, UI에 ItemRef.name_ko를 표시하면 태그가 그대로 보인다 | vision-engineer / stats-researcher | **V6**, **S1** |
| 17 | 캡처 요청서(비개발자용) | **WARN** | 화면 목록과 우선순위(★/☆), 설정 체크, 파일명 규칙은 충분하다. 명확성 문제가 8건 있다(아래) | vision-engineer | **V8** |
| 18 | pytest 전체 | **PASS** | **376 passed, 3 skipped, 4 xfailed** (101.9s). xfail 중 2개는 이번에 추가한 V2 strict xfail이고 2개는 기존 것이다. `tests/test_vision_qa04.py` 16개(14 pass + 2 xfail) | - | - |
| 19 | 실제 인식 정확도(원본 1920x1080) | **BLOCKED (사용자)** | fixture는 여전히 방송 크롭 7장(1986~2001 × 1117~1126)이다. items, augments_owned, 연패, 전투, 7레벨 이상 라벨은 0건이다. 기준선: screen_mode 7/7, stage 7/7, level 3/3, xp 5/5, gold 5/5, streak 5/5, hp 7/7, odds 5/5, shop 4/5(칸 24/25, 틀린 ID 0), augment 1/1 | 사용자 → vision-engineer | 캡처 요청서 |

WARN 7번째 항목(#8의 content_box 부재)은 #13 R2에 포함해 따로 세지 않았다.

---

## 요청 판정 (vision 보고서 7절)

| 요청 | 판정 | 담당 | 조건·메모 |
|---|---|---|---|
| R1a `fixtures.py` items 이름 → ItemRef | **수용** | app-integrator (fixtures.py는 src, QA는 src 수정 금지). QA가 테스트 작성 | `find_by_name("items", …)`(DA_ 우선)을 쓴다. 분류는 라벨 버킷이 아니라 정적 데이터 `category`를 따르고, 버킷과 다르면 오류로 처리한다. **자석 제거기는 묶음 단위로 비교해야 한다.** 라벨 "자석 제거기"는 `TFT_Consumable_ItemRemover`로 해석되고 vision은 `…_UsesLeftN`을 내므로, ID로만 비교하면 항상 "wrong"이 된다(V6와 함께 정할 것). 참고: "고속 연사포"는 `DA_Artifact_RapidFireCannon`(유물)으로 해석된다. 요청서 예시는 completed 칸에 두었으니 분류를 확인해야 한다 |
| R1b `item_bench` 키 | **수용(무시가 아니라 별도 반환)** | app-integrator(fixtures), vision(harvest) | `ExpectedScreen.extras["item_bench"]`에 **apiName으로 바꾼** 10칸을 담는다. harvest-items는 원본 JSON 대신 이 값을 읽는다(V1과 짝). 캡처 요청서의 `note_item_bench` 임시 안내는 반영 후 지운다 |
| R2 `[vision]` 설정 키 | **부분 수용** | app-integrator(config.py, settings.toml) → vision이 연결 | **필수**: `content_box`(또는 `capture_region`, MssSource `region`과 같은 의미. 없으면 창모드에서 전부 None, #8). `ocr_backend`(진단용). V2 이후의 `name_fuzzy_min_margin`, `item_match_margin`. **보류**: streak/hp/traits 보정 계수는 원본 캡처로 보정하기 전까지 코드 상수로 둔다(사용자가 조정할 값이 아니다). `ocr_lang`은 낮은 우선순위 |
| R3 augments_owned 추적 | **수용, 단 조건 추가** | app-integrator(세션 상태). HUD 판독은 vision | vision이 단일 프레임만 처리(무상태)하는 원칙에는 동의한다. 다만 **직전 `augment_offer`만으로는 사용자가 무엇을 골랐는지 알 수 없다.** HUD의 보유 증강 판독(캡처 #6)이나 선택 카드 강조 감지가 필요하다. 추천 1위를 골랐다고 가정하면 안 된다. 증강 새로고침도 고려해야 한다(마지막으로 본 offer 기준). 판독이 생기기 전까지는 None으로 두면 advisor §4.3이 처리한다 |
| R4 레벨 → XP 표를 meta.json으로 | **수용** | stats-researcher | `meta.json`에 `xp_to_next`와 관측 레벨 목록을 출처와 함께 둔다(meta 167행 tftflow 표). vision은 이것을 읽고, 없으면 `parse.XP_TO_NEXT`로 대체한다. 7레벨 이상은 캡처 #2로 확정한다. 표가 틀려도 `parse_xp`가 None을 돌려주므로 안전하게 실패한다 |
| R5 `static_data._preference` 공개 | **수용(사소)** | stats-researcher | 이름을 `preference_key`로 공개하고 `_preference` 별칭을 유지한다. `templates.py:22` import를 교체한다 |
| R6 contracts 변경 불필요 | **동의** | - | 0.2.0으로 모든 출력을 표현할 수 있음을 확인했다 |

---

## 수정 요청 (담당별)

### vision-engineer
- **V1 (FAIL #15)**: `harvest-items`는 라벨 이름을 `static.find_by_name("items", name)`으로 apiName으로 바꾼 뒤 저장하고, 해석하지 못한 이름은 저장하지 말고 오류로 보고한다. `_default_item_template_dir`은 디렉터리를 통째로 바꾸지 말고 **ID별로 합친다**(`items_screen/{id}.png`가 있으면 그것, 없으면 `items/{id}.png`). IconMatcher는 stem이 정적 데이터 ID가 아니면 로드할 때 건너뛰고 경고한다.
- **V2 (FAIL #7)**: `matching.py:77`을 고친다. (a) 모든 점수 구간에 margin 하한을 둔다(예: ≥85이면 margin ≥8, 60~85이면 ≥15). 모의 실험상 증강 오답 1,579→0, 상점 109→9. (b) 끝에 붙은 등급 토큰(I/II/III, 숫자)은 따로 떼어 **정확히 일치**할 때만 인정한다. 비교 전에 `l`, `|`, `1`을 `I`로 정규화하고, 기본 이름은 퍼지로, 등급은 정확 일치로 본다. 등급이 애매하면 카드 테두리 색(실버/골드/프리즘)과 `tier`로 교차 확인하고, 그래도 모르면 None으로 둔다. (c) 증강 후보를 `set_native=True`(255/597)로 제한해 레거시 이름 충돌을 없앤다. 수정되면 `tests/test_vision_qa04.py`의 strict xfail 2개가 XPASS로 실패하므로 xfail 표시를 지운다.
- **V3 (#11)**: shop 필드 신뢰도는 "상점 줄을 읽었는가"를 나타내게 한다. 예: HUD 신호와 식별된 칸 신뢰도의 중앙값, 또는 max. 칸별 차단은 advisor의 칸 필터가 맡는다. 필드 신뢰도가 임계 이상인데 일부 칸만 미달이면 advisor가 그 칸을 "인식 불확실"로 표시하는지 테스트로 확인한다(`scoring.py:337`에 이미 있음).
- **V4 (#12)**: streak 보정 계수를 임계에서 떨어뜨린다. 부호를 확정하기 전에 포함하려면 0.7, 제외하려면 0.5로 둔다. streak=0은 부호와 무관하니 지금처럼 그대로 둔다. 사용자 답변(요청서 질문 3)에 맞춘다.
- **V5 (참고, #4)**: COMBAT을 판별하지 못해 전투 프레임이 planning이 되고, advisor가 `_full`을 다시 계산한다. 캡처 #7이 오면 `round_icons` 규칙을 넣는다. 그 전까지는 변화 감지(V7)로 불필요한 재계산을 억제한다.
- **V6 (#16)**: 묶음(`_item_group`)이 같은 변형은 대표 ID로 출력한다(`DA_Consumable_ItemRemover` 또는 static 우선순위 1위). 남은 횟수가 필요하면 배지 숫자를 OCR로 따로 읽는다. 표시 이름에서는 `<…>` 마크업을 제거한다(`_item_group`이 쓰는 정규식을 ItemRef.name_ko에도 적용).
- **V7 (#14, 성능 및 변화 감지 전략)**:
  1. **빠른 개선**: 한 줄짜리 고정 ROI(stage, level, xp, gold, odds, streak value)는 `_read_text`에서 `read_line`(약 26ms)을 **먼저** 쓰고, 파서가 거부할 때만 검출(`read`, 약 161ms)로 넘어간다. 검출 호출이 12회에서 약 3~4회로 줄어 이 Mac 기준 약 0.4~0.6s/프레임으로 예상된다.
  2. **변화 감지(app 루프)**: 4 FPS로 캡처하고, 프레임마다 ROI 묶음별 작은 서명(회색조 64x8 축소, 약 0.3ms/묶음)을 계산한다. 묶음: G1 스테이지와 라운드 아이콘, G2 상점 이름 줄, G3 HUD 숫자 줄, G4 증강 제목과 이름, G5 아이템 세로줄, G6 플레이어 목록과 특성 패널(느리게 변함, 약 3s마다).
  3. 화면 상태는 먼저 픽셀 신호로 판별한다(`hud_panel_pixels`, `frame_is_dark`, G4 서명). OCR은 **바뀐 묶음만**, 그리고 서명이 **2프레임 연속 같을 때**(약 250~500ms 안정) 돌린다. 상점 새로고침·구매 애니메이션과 캐러셀 움직임을 읽지 않기 위해서다.
  4. Recognizer에 묶음 단위 부분 인식 API를 둔다(예: `recognize(image, groups=…)`). app은 직전 GameState에 합치고 필드별 captured_at을 유지한다.
  5. advisor 호출은 합친 상태의 `resource_signature`가 바뀔 때만 한다(advisor 캐시와 히스테리시스는 이미 있다).
  6. Recognizer는 전용 스레드 1개에서 돌리고, 입력 큐는 "최신 프레임만" 유지한다(오래된 프레임은 버린다). Windows onnxruntime 수치를 측정해 보고한다.
- **V8 (#17, 캡처 요청서)**:
  1. Windows 11은 `PrtSc`를 누르면 기본으로 캡처 도구(부분 캡처)가 열린다. 대신 **`Win+PrtSc`**(전체 화면 PNG가 `사진\스크린샷`에 자동 저장됨)를 1순위로 안내한다.
  2. 게임 스크린샷 키(F12)의 저장 위치와 형식을 적는다. JPG라면 "JPG 금지" 규칙과 충돌하므로 확인이 필요하다.
  3. JSON 직접 작성은 비개발자에게 부담이 크다. "스크린샷만 넣으면 Claude가 DRAFT 라벨을 만들고, 사용자는 틀린 값만 고친다"를 기본 흐름으로 두고, JSON 템플릿은 부록으로 옮긴다.
  4. 개발 내부 사정(`fixtures.py`, app-integrator, `note_item_bench` 임시 규칙)을 사용자 체크리스트에서 빼서 개발자 메모로 옮긴다. item_bench가 한국어 이름인지 apiName인지는 V1과 R1b에 맞춰 하나로 통일한다.
  5. 찍기 전 설정에 다음을 추가한다: **채팅창 최소화 또는 숨김**(기존 fixture 2장에서 레벨이 채팅에 가려짐), HDR 끄기, 그래픽 필터·색약 모드 끄기.
  6. 질문 3(streak)에 결과를 쉬운 말로 적는다. 예: "지금 설정이면 연승/연패 숫자가 추천에 쓰일 때도 있고 안 쓰일 때도 있습니다."
  7. `tests/fixtures/screens/raw/` 폴더는 아직 없다. "폴더가 없으면 만들어 주세요"라고 적거나, 파일을 전달만 받는 흐름으로 바꾼다. 참고로 `evaluate.py`와 fixture 테스트는 `screens/*.expected.json`만 보고 **`raw/` 하위 폴더는 보지 않는다**. 평가 경로를 맞춰야 한다.
  8. 플레이어 목록에 다른 소환사 이름이 찍히니 공개 저장소에 올리지 말라는 한 줄을 넣는다.

### app-integrator
- **R1a, R1b, R2**: 위 판정표대로 한다.
- **L1 (#10)**: `data/templates/*/items/`를 `.gitignore`에 넣는다(`fetch-items`로 재생성 가능). README를 만들 때 Riot 팬 정책 고지 문구와 CommunityDragon 출처를 넣는다. `openvino-telemetry`의 기본 동작(동의 전 전송 없음)을 확인하고, 필요하면 `opt_in_out --opt_out`을 안내한다.
- **V7-2~6**: 변화 감지와 앱 루프 설계. app 통합은 아직 없다(`__main__.py:32`는 "미구현" 메시지).
- **R3**: augments_owned 세션 추적. HUD 판독 없이 추정하지 않는다.

### stats-researcher
- **R4**: `meta.json` `xp_to_next`
- **R5**: `preference_key`를 공개한다.
- **S1 (#16)**: `static_extract`가 items(자석 제거기, 그웬의 가위 등 22개)의 `name_ko`/`name_en`에서 `<rules>…</rules>` 같은 마크업을 제거하도록 한다(원문은 `desc_*`에 둔다).

### 사용자 (BLOCKED #19)
- 캡처 요청서의 ★ 항목(약 16~20장)과 질문 1~5에 답한다. 특히 연패 아이콘(#8), 전투 화면(#7), 증강 선택 직후 HUD(#6), 7레벨 이상(#2)이 필요하다.
