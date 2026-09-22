# 04 vision-engineer: Phase 3 구현 보고

작성일: 2026-09-22 / 작성자: vision-engineer / 브랜치 `phase3`(커밋하지 않음)
상태: **구현 완료. 수치는 PROVISIONAL이다.** 원본 1920x1080 캡처가 아직 없다. ROI는 방송 크롭 fixture 7장에서 측정했고, 정확도도 같은 7장으로 쟀다. 그래서 아래 수치는 **실제 정확도가 아니라 회귀 기준선**이다.
사용자에게 보낼 캡처 요청서: `_workspace/04_vision-engineer_capture_request.md`

안전 원칙: 화면 픽셀만 읽는다. 캡처는 `mss`(OS 화면 캡처 API)만 쓴다. 메모리·프로세스·입력에 접근하는 API는 없고, 이를 테스트로 고정했다(`test_vision_code_has_no_memory_or_input_access`). 표지 좌표(XP/새로고침 버튼)는 화면 상태를 판별하는 앵커로만 쓰고 클릭하지 않는다.

---

## 1. 모듈 구성 (`src/tft_advisor/vision/`)

| 파일 | 역할 |
|---|---|
| `capture.py` | `FrameSource` 프로토콜과 `Frame`(BGR ndarray, captured_at, source). `MssSource(monitor, region)`는 실시간 캡처, `FileSource(paths)`는 스크린샷, `ArraySource`는 테스트용이다. `load_image()`는 `np.fromfile + imdecode`로 읽어 Windows 한글 경로에서도 동작한다 |
| `regions.py` | 비율 좌표 `Rect`, `FrameMapper(frame_w, frame_h, content=None)`로 비율 좌표를 픽셀로 바꾼다. `content`는 레터박스·창 테두리·방송 크롭 보정용이다. `Profile` 묶음 `SET18_16X9`의 별칭으로 `"1920x1080"`, `"2560x1440"`, `"3840x2160"`을 둔다. `draw_rois()`는 디버그 PNG를 만든다 |
| `ocr.py` | `OcrEngine` 프로토콜(`read`=검출+인식, `read_line`=한 줄 인식). `RapidOcrEngine`은 rapidocr 3.9 한국어 PP-OCRv5 rec이고 백엔드는 onnxruntime → openvino 순으로 자동 선택한다. 둘 다 없으면 `NullOcr`. `DigitTemplateReader`는 숫자 글리프 템플릿 매칭 대안이다(harvest와 read) |
| `parse.py` | OCR 문자열을 값으로 바꾸는 순수 함수: stage, level("N레벨"/"Lv. N"), xp("a/b", "/"를 "1"로 읽은 경우 복원), int, 상점 확률(5개, 합 100). `XP_TO_NEXT` 표와 `level_from_xp` |
| `matching.py` | `NameMatcher`: rapidfuzz로 **자모 단위**(겹모음·겹받침 분해) 비교를 해서 정적 데이터 name_ko/name_en과 맞춘다. 동명 후보는 `static_data.find_by_name`의 우선순위(DA_·set_native)를 따른다. 수락 조건은 점수 ≥ `shop_fuzzy_min`(85), 또는 점수 ≥ 60이면서 2위와의 차이 ≥ 15 |
| `icons.py` | `IconMatcher`: TM_CCOEFF_NORMED 템플릿 매칭, ±3px 탐색. `group`으로 아이콘이 같은 변형(자석 제거기 사용 횟수)을 묶는다. `slot_is_empty()`는 검은 칸을 판별한다 |
| `screen_mode.py` | `ModeSignals`를 `classify()`에 넣어 (ScreenMode, 신뢰도)를 얻는다. 신호는 하단 버튼(OCR 우선, 없으면 픽셀), "하나 선택" 제목, 증강 이름 매칭 수, 스테이지, 어두운 프레임이다 |
| `recognizer.py` | **`Recognizer.recognize(image) -> GameState`**, `recognize(image)`(싱글턴), `recognize_file(path)`. 필드별 값과 `confidence`, `field_source=vision`을 채운다 |
| `templates.py` | 오프라인 CLI: `fetch-items`(CDragon 아이템 아이콘 212개를 `data/templates/18/items/`에 받는다. 같은 아이콘은 DA_ 우선으로 하나만 둔다), `harvest-items`(원본 캡처로 실화면 템플릿 → `items_screen/`, 있으면 이것이 우선 사용된다), `harvest-digits`, `debug-rois` |
| `evaluate.py` | fixture 필드별 정확도: `python -m tft_advisor.vision.evaluate [DIR] [--json OUT]` |

### 공개 API

```python
from tft_advisor.vision import Recognizer, FileSource, MssSource
rec = Recognizer()                               # static=load_static(), cfg=VisionCfg(), OCR 자동 선택
state = rec.recognize(bgr_ndarray)               # -> contracts.GameState
state = rec.recognize(img, content=(x, y, w, h), source_image="...", captured_at=dt)
from tft_advisor.vision.recognizer import recognize_file
state = recognize_file("shot.png")               # --screenshot 모드용
```

- `Recognizer(static=None, cfg=None, ocr=None, profile=None, item_template_dir=None)`: 모두 주입할 수 있다. 테스트는 가짜 OCR을 주입한다.
- 모르는 값은 None이고, 그 필드는 `confidence` 키도 없다. `screen_mode`를 판별하지 못하면 기본값 `unknown`을 두고 confidence 키를 넣지 않는다.
- 스레드 안전성: Recognizer 한 개는 스레드 하나에서만 쓴다(rapidocr 내부 상태 때문).

### 필드별 방법과 신뢰도 규칙

| 필드 | 방법 | confidence |
|---|---|---|
| screen_mode | 4절 규칙 | 증강(제목+이름) 0.95, 증강(한쪽 신호만) 0.75, planning(OCR 버튼) 0.7, planning(픽셀 버튼) 0.55, loading 0.6, carousel(N-4 또는 1-1이고 상점 없음) 0.65 |
| stage | 스테이지 ROI OCR. Stage 1은 막대가 짧아 x 위치가 달라서 ROI를 넓게 잡았다 | OCR 점수 |
| level / xp | "N레벨" 칸과 "a/b" 칸 OCR. XP 필요량과 레벨이 맞으면 둘 다 max 점수, 안 맞으면 ×0.5. 레벨이 가려지면 XP 필요량으로 추정(×0.8, 7레벨 이상 미검증 표면 추가 ×0.75) | 좌동 |
| gold | 동전 아이콘 + 숫자 칸 OCR | OCR 점수 |
| shop_odds | 확률 줄 OCR. 5개이고 합 100일 때만. `meta.json` 관측값과 다르면 ×0.5 | OCR 점수 |
| streak | 숫자 OCR, 부호는 아이콘 색(주황=+, 파랑=−). 0이면 부호 무관 | ×0.6(부호 미검증) |
| hp | 오른쪽 플레이어 목록 OCR. 내 칸은 숫자 글자 높이가 중앙값의 1.4배 이상(관측 약 1.8배)이고 그런 칸이 정확히 1개일 때만 | ×0.7 |
| shop | 카드별 이름 칸 한 줄 OCR + 퍼지 매칭. 챔피언 비용은 정적 데이터, 특수 상품 가격은 비용 칸 OCR. 텍스트가 없고 카드가 어두우면 EMPTY, 매칭 실패는 UNKNOWN | 식별된 칸의 min × 0.9^(UNKNOWN 칸 수) |
| augment_offer | 이름 칸 3개 OCR + 퍼지 매칭(증강만). 3개가 모두 맞을 때만 값을 넣는다. rarity는 augments.json `tier` | min(OCR, 매칭) |
| items | 아이템 벤치 세로 10칸. 빈 칸 판별 후 아이콘 매칭. 점수 ≥ `icon_match_min`(0.8)이고 차이 ≥ 0.05여야 한다. **비어 있지 않은 칸 중 하나라도 실패하면 items=None**(부분 목록은 오해를 낳는다) | min 점수 |
| active_traits | 왼쪽 특성 패널 OCR. 이름을 매칭하고 같은 행 왼쪽 숫자를 인원으로 쓴다. 구간은 traits.json breakpoints | min × 0.55. 인원 1인 비활성 행을 자주 놓쳐서 기본 임계 0.6 **미만**으로 두었다 |
| board, bench, augments_owned | 미구현 → None | - |

---

## 2. 의존성 상태 (Python 3.14.7, macOS 15.7 **x86_64**)

| 패키지 | 결과 |
|---|---|
| mss 10.2.0, numpy 2.3.5, opencv-python 4.14.0.94, rapidfuzz 3.14.6, rapidocr 3.9.2 | 설치 OK |
| **onnxruntime** | **설치 실패**: macOS x86_64용 cp314 휠이 없다(1.23.2는 cp313까지, 1.24+는 macOS arm64만). 대상인 Windows(win_amd64)에는 1.30.0 휠이 있다(`uv pip compile --python-platform windows`로 확인) |
| 대체: **openvino 2025.4.1** | 설치 OK. rapidocr의 openvino 엔진으로 한국어 PP-OCRv5 rec와 PP-OCRv6 det이 동작한다. 모델은 첫 실행 때 내려받는다(modelscope, 약 20MB) |
| torch/easyocr, paddle | cp314 x86_64 mac 휠이 없다(대안으로도 불가) |
| tesseract | 시스템에 없다(마지막 대안이라 쓰지 않음) |

`pyproject.toml`의 `vision` extra만 최소로 수정했다(환경 마커 추가).
```
"onnxruntime>=1.23; sys_platform != 'darwin' or platform_machine != 'x86_64'",
"openvino>=2025.1; sys_platform == 'darwin' and platform_machine == 'x86_64'",
```
→ `uv pip install --python .venv/bin/python -e ".[vision]"` 성공. Windows에서는 여전히 onnxruntime을 쓴다. 오케스트레이터 폴백 규칙상 "OCR 대체"에 해당하지만, 엔진은 같은 rapidocr이고 런타임만 바꿨다. 숫자 글리프 템플릿 매칭(`DigitTemplateReader`)도 구현했지만, 템플릿이 원본 캡처에서만 나오므로 아직 쓰지 않는다.

주의할 점 두 가지.
- rapidocr는 호출 때 넘긴 `use_det` 등을 **내부에 기억한다**. 그래서 `read()`는 매 호출에 `use_det=True`를 명시한다.
- 기본 Det 설정(`limit_type=min, 736`)은 작은 ROI를 과하게 키워 검출이 실패한다. 그래서 `max, 1280`으로 바꿨다.

성능: 이 Intel Mac(openvino)에서 프레임당 **0.7~1.9초**가 걸린다(준비 단계가 가장 느리다. OCR 호출 약 15회). 스킬 6절의 2~4 FPS 목표에는 미달이다. app-integrator 루프는 "화면 변화가 있을 때만 재인식"(상점 ROI 해시)을 전제로 해야 한다. 최적화 후보(미적용): 하단 HUD 한 줄을 검출 1회로 묶기, 상점 이름 5칸 배치 rec, 특성 패널·HP를 N프레임마다 한 번만 읽기. Windows onnxruntime 수치는 아직 재지 않았다.

---

## 3. ROI

- **Phase 1 layout.md의 TFT-OCR-BOT(2024) 좌표는 Set 18 HUD와 맞지 않는다.** 상점 카드 x 시작점이 0.25에서 0.2865로, 골드는 0.45에서 0.53으로 옮겨졌다. 레벨/XP/확률 줄, 세로 아이템 벤치도 새 배치다. 그래서 전부 다시 측정했다(`regions.py` 머리 주석).
- 측정 방법: 전체 프레임 OCR 박스 위치를 비율로 바꿨다. 7장 사이 편차는 약 ±0.003이다. 방송 크롭이 게임 화면 전체를 비율 그대로 담았다고 **가정**했다. 즉 per-fixture 보정은 항등(`content=None`)이다. 가정이 틀렸다면 `FrameMapper(content=...)`로 fixture별 scale/offset을 줄 수 있다.
- 디버그 오버레이: `_workspace/04_vision_rois_3-3.png`, `_workspace/04_vision_rois_2-1_augment.png`. 눈으로 확인한 결과 모든 박스가 해당 UI 위에 있다.

---

## 4. fixture 결과 (PROVISIONAL, 방송 크롭 7장, openvino 백엔드)

`python -m tft_advisor.vision.evaluate --json _workspace/04_vision_fixture_eval.json`

| 필드 | ok/정답 수 | 틀림 | 미인식 | 비고 |
|---|---|---|---|---|
| screen_mode | 7/7 | 0 | 0 | planning 5, augment 1, carousel 1 |
| stage | 7/7 | 0 | 0 | Stage 1 위치 차이도 처리 |
| level | 3/3 | 0 | 0 | 추정 라벨 2건(2-1, 3-3)은 집계 제외. 둘 다 XP로 추정해 맞았다(6, 3) |
| xp | 5/5 | 0 | 0 | "216"→2/6 같은 "/"→"1" 복원 포함 |
| gold | 5/5 | 0 | 0 | |
| streak | 5/5 | 0 | 0 | 모두 0 또는 연승이다. **연패 표본 0** |
| hp | 7/7 | 0 | 0 | 가장 큰 숫자 규칙 |
| shop_odds | 5/5 | 0 | 0 | |
| shop (5칸 전체 일치) | 4/5 | 1 | 0 | 2-1: 워윅을 OCR이 "위월"(0.45)로 읽어 UNKNOWN. **틀린 ID는 0** |
| shop 칸 단위 | 24/25 | 0 | 1 | 빈 칸 1개(1-4)와 특수 상품 "3단계와 함께"(가격 9)도 맞췄다 |
| augment_offer | 1/1 | 0 | 0 | 고위천사의 지팡이 / 출정 / 수완가 |
| items, augments_owned | 측정 불가 | | | 라벨 0건 |

라벨이 없는 필드(육안 확인만 함):
- **items**: 2-1, 3-3, 3-5에서 BF 대검, 연습용 장갑, 음전자 망토, 쇠사슬 조끼, 자석 제거기를 매칭했다(점수 0.81~0.97, 재료 차이 ≥0.22). 육안으로 보면 그럴듯하다. 1-4와 2-5는 자석 제거기 점수가 0.70~0.72로 기준 미달이어서 items=None, 증강 화면(어둡게 가려짐)과 캐러셀도 None이다. CDragon 원본 아이콘을 템플릿으로 썼으므로 실화면 템플릿으로 바꾸면 좋아질 것으로 예상한다.
- **active_traits**: 3-3(주문술사 3, 나무정령 3, 검은 가시 2, 싸움꾼 2, 엄호대 2)과 3-5가 화면과 일치한다. 인원 1인 비활성 특성은 빠진다. 신뢰도 0.54로 임계값 미만이다.

해석 주의: ROI를 이 7장에서 측정했고 같은 7장으로 평가했으므로 **과적합된 수치**다. 원본 캡처 기준 목표(layout.md 3절: 숫자 ≥98%, 상점·증강 ≥95%, 아이템 ≥90%)의 달성 여부는 아직 모른다.

---

## 5. 테스트

`tests/test_vision.py` 57개(전체 스위트 **242 passed, 3 skipped**, 54초). 실시간 캡처는 쓰지 않는다.
- 안전: vision 소스에 메모리·입력 API가 없음을 확인한다.
- 파싱, 자모 퍼지 매칭(DA_ 우선, 숫자 잡음 거부), ROI 정규화와 스케일·오프셋, 한글 경로 FileSource, 아이콘 매칭과 묶음 margin, 합성 글리프로 DigitTemplateReader harvest→read, 화면 상태 규칙 표.
- 조립: ROI마다 고유 색을 칠한 합성 1920x1080 프레임에 색 키 가짜 OCR을 붙여 planning 전체 필드를 검사한다(EMPTY/SPECIAL 칸, 레벨↔XP 상호 확인, XP로 레벨 추정). NullOcr이면 전부 None/UNKNOWN, 어두운 프레임이면 LOADING.
- fixture 회귀(OCR 백엔드가 있을 때만): 4절 기준선(hp는 6/7, shop 칸은 23/25로 여유를 둠). **틀린 값인데 신뢰도 ≥0.6인 경우가 없어야 한다**는 검사와, 틀린 ID 상점 칸이 0이어야 한다는 검사도 넣었다.

---

## 6. 알려진 한계와 다음 단계

1. 원본 캡처가 필요하다(요청서 참고). 들어오면 ROI를 검증하고, `harvest-items`로 실화면 아이템 템플릿을, `harvest-digits`로 숫자 글리프를 만들고, 기준선을 목표치로 바꾼다.
2. **COMBAT, GAME_OVER, ITEM_SELECT 판별 규칙이 없다.** 상점 HUD는 전투 중에도 보이므로 현재 `planning`은 "준비 또는 전투"라는 뜻이다(신뢰도 0.7). 상단 라운드 아이콘 줄(`round_icons` ROI: 현재 라운드가 노란색으로 강조되고, 톱니=캐러셀, 카드=증강)이 좋은 신호로 보이는데, 전투 캡처가 필요하다.
3. `augments_owned`: 선택 순간을 기록하는 방식(layout.md 2절)은 프레임 간 상태가 필요하므로 app 루프에서 한다. vision은 `augment_offer`를 주고, 증강 화면에서 준비 화면으로 넘어가는 순간을 이벤트로 제공할 수 있다. 어느 모듈이 맡을지 결정이 필요하다(아래 요청 3).
4. board/bench는 Phase 4다.
5. 상점 이름 OCR 저신뢰(워윅→"위월")는 초상화 템플릿 교차 검증으로 보완할 수 있다. 원본 캡처가 오면 한다.

---

## 7. 계약·설정 변경 요청 (직접 수정하지 않음 → app-integrator / QA)

1. **`fixtures.py`**(QA/app-integrator 소유):
   - (a) 정답 파일의 `items`를 `{components|completed|emblems|others: [이름…]}` 형식으로 받아 ItemRef로 바꾸는 처리를 추가한다. 지금은 문자열 리스트를 그대로 GameState에 넘겨 검증 오류가 난다.
   - (b) `item_bench`(10칸 순서 목록, 템플릿 수집용)를 메모 키로 무시하거나 별도로 반환한다. 지금은 GameState extra=forbid 때문에 거부된다.
2. **`config/settings.toml` `[vision]` 추가 키 제안**(`VisionCfg`):
   - `ocr_backend = "auto"`(auto|onnxruntime|openvino|none)
   - `ocr_lang = "korean"`
   - `item_match_margin = 0.05`
   - `name_fuzzy_relaxed_min = 60`, `name_fuzzy_min_margin = 15`
   - 필드 보정 계수 `streak_sign_factor = 0.6`, `hp_factor = 0.7`, `traits_factor = 0.55`
   - `content_box = []`(프레임 안 게임 화면 영역, 창모드·레터박스용)

   지금은 `recognizer.py`의 모듈 상수다. `profile` 기본값 `"1920x1080"`은 16:9 프로파일의 별칭으로 그대로 동작한다.
3. **augments_owned 추적 책임**: vision은 단일 프레임만 처리한다(무상태). app 루프가 `augment_offer`(직전 증강 화면)와 이후 HUD 표시를 받아 `FieldSource.TRACKED`로 채우는 쪽을 제안한다.
4. **정적 데이터(stats-researcher)**:
   - XP 필요량 표(`parse.XP_TO_NEXT`, 7레벨 이상 미검증)를 `meta.json`에 `xp_to_next`로 둘지 결정한다.
   - `static_data._preference`를 공개 함수로 바꾼다(vision `templates.py`가 비공개 함수를 import한다).
5. contracts.py 변경은 **필요 없다.** 현재 GameState 0.2.0으로 전 필드를 표현할 수 있다. UNKNOWN 상점 칸, 필드별 confidence, None 모두 가능하다.

---

## Fix round (QA 04 반영, 2026-09-22)

기준: `_workspace/04_qa_vision.md`의 vision-engineer 항목(V1~V8)과 오케스트레이터 추가 요청(XP 표, `preference_key`).
결과: **pytest 444 passed, 3 skipped, xfail 0**(QA strict xfail 2개 해제). fixture 기준선은 그대로다(screen_mode 7/7, stage 7/7, level 3/3, xp 5/5, gold 5/5, streak 5/5, hp 7/7, odds 5/5, shop 칸 24/25, 틀린 ID 0, augment 1/1).

### F1. V2 (FAIL) 이름 매칭: 숫자·등급 토큰 정확 일치 + 모든 구간 margin — `matching.py`
- 이름을 (기본 이름 자모열, **숫자 서명**)으로 나눈다. 서명 = 아라비아 숫자열 + 끝의 로마 숫자 등급(I→1, II→2, III→3).
  OCR 입력의 끝 등급 토큰은 `l | 1 ! i`를 `I`로 바꾼 뒤 해석한다("Il"→II, "1I"→II, "Ill"→III). 해석할 수 없는 토큰("IIII")은 `?`이어서 어떤 후보와도 맞지 않는다.
- 점수 = 기본 이름 fuzz.ratio − (서명 불일치면 30). 서명이 틀린 후보끼리는 같은 벌점이라 "6단계 집결"은 5개 동점(margin 0) → 기권한다.
- 수락 규칙: `score ≥ 85 이고 margin ≥ 10` 또는 `score ≥ 60 이고 margin ≥ 15`. 기존에는 85점 이상이면 margin을 보지 않았다.
- 레거시(set_native=false) 레코드 중 기본 이름이 현 세트 레코드와 같은 것(TFT6 "판도라의 아이템" vs DA "판도라의 아이템 I")은 후보에서 뺀다. 그래서 등급이 누락된 OCR이 레거시 무등급 ID로 가지 않고 기권한다. QA 제안 (c)의 "set_native만"보다 약한 조건이다. 현 세트와 이름이 겹치지 않는 레거시 증강은 남긴다(set_native 플래그 오류에 대한 안전장치).
- `strip_markup()`: `<rules>…</rules>` 마크업을 제거한 이름으로 매칭하고 표시한다.
- **margin 튜닝** (`_workspace/qa_scripts/vision_name_tuning.py`, QA와 같은 자모 1개 치환·삭제 변형을 현재 매처에 넣음):

  | min_margin | 상점 오답 수락 (52,909) | 상점 정답 수락 | 증강 오답 수락 (95,473) | 증강 정답 수락 |
  |---|---|---|---|---|
  | 0 | 17 (0.032%) | 98.3% | 5 | 99.9% |
  | 8 (QA 제안) | 9 (0.017%) | 98.0% | 0 | 99.7% |
  | **10 (채택)** | **5 (0.009%)** | **98.0%** | **0** | **99.0%** |
  | 12 | 2 | 97.8% | 0 | 98.5% |
  | 15 | 0 | 95.1% | 0 | 97.9% |

  수정 전(QA 측정): 상점 109, 증강 1,579. 10을 고른 이유는 두 음절 챔피언 이름의 자모 1개 오류(요릭→아리, 타릭→아리, 변이→바이, 모두 margin 8.9)를 없애면서 상점 정답률은 그대로이고 증강 정답률만 0.7%p 줄기 때문이다. 12 이상은 오답을 2~0으로 더 줄이지만 상점 기권이 늘어난다. 남은 5건은 렝가↔레오나/베이가(margin 10.9)다.
- **남은 위험(텍스트만으로는 해결 불가)**: 등급 토큰 OCR 변형 780건 중 67건(8.6%)은 여전히 다른 등급으로 수락된다. 전부 "II"에서 I 하나가 **빠진** 경우(= 글자가 정확히 다른 등급 이름)이거나, 무등급과 I등급이 둘 다 현 세트에 있는 이름(도둑 무리, 기다림의 미학)이다. "Il"/"1I"/"l" 같은 오독은 모두 맞게 해석한다(74%). 등급이 누락되면 기권한다(17%). 나머지를 막으려면 카드 등급(실버/골드/프리즘) 색과 augments.json `tier`를 교차 확인해야 한다. 같은 기본 이름 안에서는 등급 숫자가 올라갈수록 tier도 올라간다(예: 영원한 브론즈 I=2, II=3). 방송 fixture는 카드 테두리가 세트 테마 색이라 이 색을 잴 수 없다. 캡처 #5(등급 혼합)가 오면 넣는다.
- QA strict xfail 2개(`test_equidistant_shop_special_is_not_accepted`, `test_augment_tier_numeral_ambiguity_is_not_confident`)를 해제했다. 둘 다 통과한다.

### F2. V1 (FAIL) harvest-items와 템플릿 합치기 — `item_ids.py`(신규), `templates.py`, `icons.py`, `recognizer.py`
- `ItemCatalog`: 아이템 묶음(마크업 뗀 이름이 같거나 아이콘이 같은 것 중 **category가 같은** 것, union-find), 대표 ID(`preference_key` 1위 → 마크업 없는 것), `resolve(표시 이름|apiName) → 대표 ID|None`, `same_item(a, b)`, `display_name`.
- `harvest_items()`와 CLI `harvest-items`: `item_bench`(또는 임시 키 `note_item_bench`)의 **한국어 표시 이름**을 대표 ID로 바꿔 `items_screen/{ID}.png`로 저장한다. 해석하지 못한 이름과 빈 칸 크롭은 저장하지 않고 오류로 보고한다(종료 코드 1). 라벨 글자는 ID로 쓰지 않는다.
- 템플릿 로드: `items/`(CDragon)와 `items_screen/`(실화면)을 **ID별로 합친다**. 한 ID에 템플릿이 여러 장이면 점수는 최댓값이다. 실화면 템플릿은 원본 아이콘을 **대체하지 않고 추가**된다. stem이 정적 데이터 아이템 ID가 아니면 로드하지 않고 경고한다(한글 파일명 → ValidationError 경로 차단). `Recognizer(item_template_dir=…)`는 디렉터리 목록도 받는다.
- 테스트(QA가 설명한 경로): `test_harvest_items_maps_korean_labels_to_ids_and_supplements_cdragon`. 한국어 라벨 "B.F. 대검"은 `DA_Component_BFSword.png`로 저장되고, "없는 아이템"은 오류로 보고되며, 옛 방식 한글 파일명은 경고 후 무시된다. 실화면 템플릿 1개 + CDragon 3개 상태에서 3개 아이템을 모두 인식하고 ValidationError가 나지 않는다.
- 참고: 실화면 템플릿과 CDragon 아이콘을 섞으면, 실화면 템플릿이 렌더링 특성(테두리·축소)을 공유하는 탓에 다른 아이템 크롭에서 점수가 부풀 수 있다. ITEM_MIN_MARGIN(0.05)과 `icon_match_min`(0.8)이 막는다. 실측은 원본 캡처 harvest 후에 한다.

### F3. V3 shop 필드 신뢰도 — `recognizer._read_shop`
- 필드 신뢰도 = **식별된 칸 중 최댓값**("상점 줄을 읽었는가")이다. 기존 `min(칸) × 0.9^UNKNOWN`은 버렸다. 칸별 `ShopSlot.confidence`는 그대로 두고, 약한 칸은 advisor가 인덱스를 유지한 채 "인식 불확실"로 거른다.
- 실측 3-5: 필드 신뢰도가 0.557에서 0.988로 바뀌어 advisor 입력에 들어간다(5칸 모두 정답).
- 테스트: `test_shop_field_confidence_is_not_dragged_down_by_one_weak_slot`(vision)와 `test_advisor_filters_weak_shop_slot_individually`(mock advisor에서 약한 칸만 "인식 불확실", 나머지 4칸은 평가됨, qa04 파일). QA의 `test_reliable_fields_have_reliable_sub_items`에서 shop 조항을 뺐다(설계가 바뀌었고 이유는 docstring에 적음). 증강·아이템 조항은 그대로 둔다.

### F4. V4 streak 보정 — `STREAK_SIGN_FACTOR` 0.6 → **0.5**
- 부호(연승/연패)가 검증되기 전까지는 **advisor 입력에서 뺀다**(0.5 × OCR 점수 ≤ 0.5, 임계 0.6과 0.1 이상 떨어짐). streak=0은 부호와 무관하므로 OCR 점수를 그대로 쓴다(포함).
- 이유: 연패 표본이 0장이고, 부호가 틀리면 경제 판단이 반대로 간다. "모름"이 "확신에 찬 오답"보다 낫다. 캡처 #8로 부호 규칙을 확인하면 0.8로 올린다(상수 한 줄). 요청서 질문 3에 쉬운 말로 적었다.
- 테스트: `test_streak_sign_factor_is_off_the_threshold`.

### F5. V6 자석 제거기 ID와 마크업 — `recognizer._read_items`
- 아이콘 매칭 결과를 묶음 대표 ID로 바꿔 출력한다. 자석 제거기는 `…_UsesLeft7` 대신 `DA_Consumable_ItemRemover`(DA_, set_native)다. 같은 아이콘을 쓰는 TFT_Consumable_ItemRemover와 UsesLeft2~10이 한 묶음이다.
- `ItemRef.name_ko`는 마크업을 뗀 이름이다. 남은 횟수 배지 OCR은 넣지 않았다(계약에 필드 없음. 요청서 질문 4 답변 후 결정).
- fixture 비교(app-integrator R1a): 라벨 "자석 제거기"는 `ItemCatalog.resolve`로 같은 대표 ID가 된다. `find_by_name`을 쓰면 `TFT_Consumable_ItemRemover`가 되므로 `same_item()`으로 비교할 것.
- 정적 데이터 원본의 마크업 제거(S1)는 stats-researcher 소관이다. vision은 읽을 때 제거하므로 S1과 무관하게 동작한다.

### F6. V7 성능과 변화 감지 — `ocr.py`, `recognizer.py`, `change.py`(신규)
- 한 줄 ROI(stage, level, xp, gold, odds, streak, 특수 상품 가격, 증강 이름 3개)는 **한 줄 인식(rec만)을 먼저** 쓴다. 점수 ≥ 0.85이고 파서가 받아들이면 그대로 쓰고, 아니면 그 칸만 검출+인식으로 재시도한다(`_read_parsed_many`). 여러 칸은 배치(`read_lines`)로 한 번에 인식한다: 모드 신호 4칸(stage, 버튼 2, 증강 제목), HUD 숫자 5칸, 상점 이름 5칸, 증강 이름 3칸.
- HUD 버튼과 증강 제목도 한 줄 인식으로 키워드를 찾는다. HUD 글자가 읽히면 제목의 검출 재시도는 생략한다(증강 화면에는 상점 HUD가 없다).
- HP: `read_boxes`로 검출만 한 뒤, 숫자 모양 박스(가로/세로 ≤ 1.8)만 배치 인식한다. 이름 박스 8개의 인식 비용이 빠진다.
- **부분 인식** `recognize(image, groups=…)`: 묶음 `GROUPS = hud, shop, items, augment, players(hp), traits`. stage와 screen_mode는 항상 읽는다. 필드 → 묶음 대응은 `FIELD_GROUP`이다. **기본값 `DEFAULT_GROUPS`에서 traits를 뺐다.** 특성 패널 검출+인식이 프레임 시간의 약 35%인데, active_traits는 TRAITS_FACTOR로 임계 미만이라 advisor가 쓰지 않는다. `evaluate.py`는 진단용으로 전부 읽는다.
- **변화 감지** `change.py`:
  - `roi_signatures(image, profile, content)`: 묶음별 32x16 회색조 서명. 7묶음 합쳐 약 2ms/프레임.
  - `changed_groups(prev, cur, threshold=24)`: 서명 픽셀 절대차 최댓값이 threshold를 넘은 묶음. ±5 잡음은 무시하고 숫자 한 글자 변화는 잡는다.
  - `ChangeDetector(profile, stable_frames=2).update(frame) → set[묶음]`: 바뀌었고 **2프레임 연속 같은** 묶음만 돌려준다. 4 FPS 기준 약 250~500ms 안정 뒤에 돌려주므로, 상점 새로고침·구매 애니메이션과 캐러셀 회전 중에는 읽지 않는다. 묶음 이름은 GROUPS에 "stage"를 더한 것이다. "stage"가 바뀌면 app은 전체 인식을 돌린다.
- **시간**(이 Intel Mac, openvino. 다른 에이전트의 pytest가 동시에 돌아 부하 평균 4~12. 같은 프로세스에서 fixture 7장 × 3회를 교차 측정):

  | 경로 | mean | median | max |
  |---|---|---|---|
  | 수정 전 실측(작업 시작 시, 부하 약 4) | 0.782s | 0.850s | 1.114s |
  | 수정 전 경로 에뮬레이션(검출 우선, 전체 묶음) | 0.851s | 0.908s | 1.256s |
  | 수정 후, 전체 묶음(traits 포함) | 0.745s | 0.745s | 1.201s |
  | **수정 후, 기본(traits 제외)** | **0.541s** | **0.526s** | 0.920s |
  | 수정 후, 루프 전형(stage+hud+shop만) | 0.224s | 0.249s | 0.393s |

  검출+인식 호출은 프레임당 10.3회에서 약 1~2회(플레이어 목록, 파서가 거부한 칸)로 줄었다. 남은 비용의 대부분은 큰 ROI 두 곳(플레이어 목록 약 0.2~0.37s, 특성 패널 약 0.3~0.46s)이다. 부하가 없는 상태에서 다시 재면 더 빠를 것으로 본다. Windows onnxruntime 수치는 여전히 없다.
- 테스트: `test_single_line_fields_use_fast_line_recognition_first`(planning 합성 프레임에서 검출 호출 ≤ 2), `test_recognize_groups_limits_fields`, `test_default_groups_skip_traits_panel`, `test_changed_groups_and_stability`.

### F7. XP 표와 preference_key (오케스트레이터 추가 요청)
- XP 필요량 표를 정적 데이터에서 읽는다: `Recognizer.xp_table = static.xp_to_next()`(meta.json `xp_to_next`, tftflow Set 18). 없으면 `parse.XP_TO_NEXT`를 쓴다. 이 대체값도 같은 출처 값 `{1:2, 2:2, 3:6, 4:10, 5:20, 6:36, 7:56, 8:68, 9:68}`으로 바꿨다. 기존 7~9레벨 값 48/76/84는 틀렸다.
- `parse_xp(text, table)`와 `level_from_xp(xp, table)`는 표를 인자로 받는다. **필요량이 같은 레벨이 여럿이면**(1·2레벨=2, 8·9레벨=68) XP로 레벨을 추정하지 않고 None을 돌려준다. 레벨↔XP 교차 확인은 그대로다.
- `templates.py`와 `item_ids.py`는 `static_data.preference_key`(공개 이름)를 쓴다.
- 테스트: `test_xp_table_comes_from_static_meta`.

### F8. 라이선스(L1)와 템플릿 README
- `.gitignore`에 `data/templates/*/items/`와 `data/templates/*/items_screen/`을 추가했다.
- **결정**: `items_screen/`(사용자 캡처에서 잘라낸 아이콘)도 Riot 아트워크이므로 추적하지 않는다. `digits/`(흑백 숫자 글리프)는 아트워크가 아닌 숫자 모양이라 추적할 수 있다.
- `data/templates/README.md`에 출처(CommunityDragon), Riot 팬 정책, 고지 문구, 재생성 명령, "stem = apiName, ID별 합치기" 규칙을 적었다. openvino-telemetry 확인은 app-integrator L1에 남긴다.

### F9. V8 캡처 요청서 개정 — `_workspace/04_vision-engineer_capture_request.md`
QA가 지적한 8건을 모두 반영했다.
1. `Win+PrtSc`(사진\스크린샷에 PNG 자동 저장)를 1순위로 안내하고, Windows 11에서 `PrtSc`만 누르지 말라고 적었다.
2. F12 저장 위치를 적고, `.jpg`이면 쓰지 말라고 적었다.
3. 기본 흐름을 "스크린샷 → Claude 초안 → 틀린 칸만 수정"으로 바꾸고, JSON은 부록 A로 옮겼다.
4. 개발 내부 사정은 부록 B(개발자 메모)로 옮겼다. item_bench는 한국어 표시 이름으로 통일했다.
5. 채팅창 숨김, HDR 끄기, 필터·색약 모드 끄기를 추가했다.
6. 질문 3에 streak 결정을 쉬운 말로 적었다.
7. `raw/` 폴더가 없으면 만들라고 적고, 평가 경로 메모를 넣었다.
8. 다른 소환사 이름이 보이니 공개 저장소에 올리지 말라는 주의를 넣었다.

### F10. app-integrator에게 필요한 `[vision]` 설정 키 (config.py, settings.toml)
| 키 | 기본값 | 연결 위치 | 비고 |
|---|---|---|---|
| `content_box` | `[]`(=프레임 전체) 또는 `[x, y, w, h]` | `Recognizer.recognize(content=…)`, `ChangeDetector.update(content=…)` | **필수**. 없으면 창모드에서 전부 None이 된다(QA #8) |
| `ocr_backend` | `"auto"` (auto\|onnxruntime\|openvino\|none) | `create_ocr` | 진단용. 연결하려면 vision에 인자 하나를 추가해야 한다(요청 시 추가) |
| `name_fuzzy_min_margin` | `10` | `NameMatcher(min_margin=)` | 튜닝 근거는 F1 표 |
| `name_fuzzy_relaxed_margin` | `15` | `NameMatcher(relaxed_margin=)` | |
| `item_match_margin` | `0.05` | `ITEM_MIN_MARGIN` | |
| `change_threshold` | `24` | `ChangeDetector(threshold=)` | |
| `change_stable_frames` | `2` | `ChangeDetector(stable_frames=)` | |
| `capture_fps` | `4` | app 루프 | |
| `traits_every_s` | `3` | app 루프(`groups`에 "traits" 추가 주기) | |

- 기존 키 `shop_fuzzy_min`(85), `icon_match_min`(0.8), `state_min_confidence`(0.6)는 그대로 쓴다.
- 보정 계수(streak/hp/traits)는 코드 상수로 둔다(QA R2 판정과 같음).
- 키가 생기면 `Recognizer.__init__`에서 `self.cfg`를 읽도록 vision 쪽에서 연결한다. 지금은 `NameMatcher` 생성자 인자만 준비해 두었다.

### 변경 파일
- 수정: `src/tft_advisor/vision/{matching,icons,ocr,parse,recognizer,templates,evaluate}.py`, `tests/test_vision.py`(테스트 함수 14개 추가), `tests/test_vision_qa04.py`(xfail 2개 해제, shop 조항 조정, advisor 칸 필터 테스트 추가), `.gitignore`, `_workspace/04_vision-engineer_capture_request.md`
- 신규: `src/tft_advisor/vision/item_ids.py`, `src/tft_advisor/vision/change.py`, `data/templates/README.md`, `_workspace/qa_scripts/vision_name_tuning.py`
- 참고: QA의 `vision_name_margins.py`는 옛 수락 규칙을 자체 구현(`match_jamo`)해서 측정한다. 새 매처로 재검사하려면 `vision_name_tuning.py`를 쓴다.
