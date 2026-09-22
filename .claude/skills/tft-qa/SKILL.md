---
name: tft-qa
description: "TFT 어드바이저 품질 검증 방법. 모듈 경계면 교차 검증(contracts ↔ vision ↔ advisor ↔ stats ID ↔ UI), 스크린샷 fixture 인식 정확도, 상황별 추천 품질 평가, pytest 회귀 테스트 작성. 모듈 완성 직후, 패치 업데이트 후, '테스트', '검증', 'QA', '정확도 확인' 요청 시 사용."
---

# TFT QA

## 1. 경계면 체크리스트 (양쪽을 동시에 읽는다)

| 경계면 | 검증 방법 |
|---|---|
| vision 출력 ↔ `GameState` | vision이 조립하는 dict/객체의 키·타입을 contracts.py와 대조. pydantic 검증 우회(`model_construct`, dict 직접 전달) 여부 확인 |
| `GameState` ↔ advisor 입력 | advisor가 접근하는 모든 필드(grep `state.`)가 contracts에 존재하고, Optional 필드의 None 처리가 있는지 |
| ID 체계 | vision/advisor/stats가 쓰는 챔피언·아이템·증강 ID가 전부 `data/static/{set}/`에 존재하는지 스크립트로 전수 대조. `unmapped.json` 확인 |
| stats DB ↔ advisor 조회 | advisor의 SQL/조회 컬럼명이 실제 DB 스키마와 일치하는지, 패치 키 일치 |
| advisor 출력 ↔ UI | `Recommendation` 필드를 UI가 모두 올바르게 읽는지 |
| 설정 ↔ 코드 | `config/*.toml` 키를 코드가 같은 이름으로 읽는지 |

## 2. 인식 정확도
- fixture: `tests/fixtures/screens/{name}.png` + `{name}.expected.json`(사람이 라벨링한 GameState 부분집합)
- 필드별 정확도(상점 5칸, 골드, 레벨, 스테이지, 증강 3개, 아이템 재료)를 표로 보고
- 실패 샘플은 ROI 크롭 이미지를 `_workspace/qa_failures/`에 저장해 원인을 보이게 한다

## 3. 추천 품질
- `tests/fixtures/states/*.json`: 상황 + `expect` 규칙(예: `"top1_in": ["Jinx"]`, `"not_top3": ["X"]`)
- 규칙은 TFT 상식으로 명백한 것만 쓴다(애매한 전략 판단을 정답으로 고정하지 않는다)
- Jev 호출 테스트는 `@pytest.mark.live`로 분리. 기본 실행은 mock 응답(저장된 실제 응답 JSON)으로 돈 들이지 않고 돌린다

## 4. 성능
- 스크린샷 1장 → 추천 end-to-end 시간 측정 (목표 < 2초, 인식 < 300ms)

## 5. 보고서 형식
`_workspace/{phase}_qa-validator_report.md`:
```
## 요약: PASS n / FAIL m
| 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
```
