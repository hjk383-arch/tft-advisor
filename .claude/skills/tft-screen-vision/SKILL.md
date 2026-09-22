---
name: tft-screen-vision
description: "TFT 게임 화면 인식 구현 방법. mss 화면 캡처, 해상도 비율 기반 ROI(상점·벤치·보드·아이템 재료·골드·레벨·스테이지·HP·증강 선택), OpenCV 템플릿 매칭으로 챔피언/아이템/증강 아이콘 식별, OCR로 숫자·텍스트 읽기, 화면 상태 판별, GameState 조립. 인식 정확도 개선·새 시즌 아이콘 갱신 시에도 사용."
---

# TFT Screen Vision

## 1. 안전 원칙
화면 픽셀만 사용한다. 게임 메모리 읽기, DLL 주입, 프로세스 후킹, 키/마우스 자동 입력은 Vanguard 탐지 대상이므로 어떤 이유로도 구현하지 않는다.

## 2. 파이프라인

```
capture(frame) → detect_screen_mode(frame) → crop ROIs → recognize each → GameState(+field confidence)
```

모든 단계는 `np.ndarray` 이미지를 입력으로 받는 순수 함수로 만든다. 실시간 캡처와 스크린샷 파일이 같은 코드 경로를 타야 fixture 테스트가 실제 동작을 보장한다.

## 3. 좌표 (ROI)
- `src/tft_advisor/vision/regions.py`에 기준 해상도 1920x1080의 **비율 좌표**(0~1)로 정의한다.
- 16:9가 아닌 해상도(16:10, 21:9)는 레터박스/UI 스케일 차이가 있으므로 fixture로 확인 후 별도 프로파일을 추가한다.
- 인게임 UI 크기 설정이 바뀌면 좌표가 달라진다 → 설정값을 `config/settings.toml`에 둔다.
- 좌표를 잡을 때 디버그 오버레이 이미지(ROI 박스를 그린 PNG)를 `_workspace/`에 저장해 눈으로 확인한다.

## 4. 인식 방법 선택

| 대상 | 1순위 | 대안 |
|---|---|---|
| 상점 챔피언 | 상점 카드의 **이름 텍스트 OCR** → 정적 데이터 이름과 퍼지 매칭 | 초상화 템플릿 매칭 |
| 증강 선택 3개 | 증강 이름 OCR → 퍼지 매칭 | 아이콘 매칭 |
| 아이템 재료/완성템 | 아이콘 템플릿 매칭 (텍스트 없음) | 특징점 매칭(ORB) |
| 보드/벤치 유닛 | 초상화·체력바 위치 탐지 후 템플릿 매칭 | 사용자 수동 보정 |
| 골드/레벨/스테이지/HP | 숫자 OCR | 숫자 글리프 템플릿 매칭(가장 빠르고 안정적) |

- 이름 텍스트가 보이는 곳은 OCR+퍼지 매칭(`rapidfuzz`)이 템플릿 매칭보다 시즌 변경에 강하다(아이콘 재수집 불필요).
- 한국어 클라이언트면 OCR 언어를 `ko`로, 정적 데이터에 한글 이름을 포함한다.
- 보드 유닛 인식은 가장 어렵다. MVP에서는 상점·증강·아이템·골드/레벨을 먼저 완성하고, 보드는 "벤치+보드 초상화"를 단계적으로 추가한다.

## 5. 화면 상태 판별
일반 라운드 / 증강 선택 / 캐러셀 / 전투 중 / 로딩 을 구분한다. 특정 ROI의 색 분포나 고정 UI 요소 템플릿으로 판별한다. 추천은 상태별로 다르게 트리거된다(증강 선택 화면 → 증강 추천).

## 6. 변화 감지
연속 프레임에서 상점 ROI 해시가 바뀌었을 때만 재인식한다. CPU와 Jev 호출을 절약한다. 캡처 주기 기본 2~4 FPS.

## 7. 산출
- `GameState`의 각 필드에 `confidence`를 채운다. 퍼지 매칭 점수가 임계값 미만이면 `None`.
- 템플릿 이미지: `data/templates/{set}/{kind}/{canonical_id}.png`
- fixture 정확도는 `tests/fixtures/screens/*.expected.json` 대비 필드별로 보고한다.

## 8. 의존성
`mss`, `opencv-python`, `numpy`, `rapidfuzz`, OCR은 `rapidocr-onnxruntime`(설치 간단·한국어 지원) 또는 `easyocr`. Tesseract는 별도 설치가 필요하므로 마지막 대안.
- 사용자 PC의 기본 Python은 3.14다. onnxruntime/torch 휠이 3.14를 지원하지 않으면 `py -3.12 -m venv .venv`처럼 지원 버전으로 가상환경을 만든다(설치 전 `pip download --only-binary` 등으로 확인).
