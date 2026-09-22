---
name: tft-advisor-orchestrator
description: "롤토체스(TFT) 실시간 추천 프로그램(tft_advisor) 개발·유지보수 전체를 조율하는 오케스트레이터. 챔피언/증강/아이템 추천 기능 구현, 통계 사이트 수집, 화면 인식, Jev 추천 엔진, 오버레이 UI 작업 요청 시 반드시 사용. 후속 작업: 새 패치/시즌 업데이트, 통계 갱신, 인식 정확도 개선, 추천 품질 개선, 특정 모듈만 다시, 이전 결과 기반 수정·보완·재실행 요청 시에도 반드시 이 스킬을 사용. 단순 TFT 메타 질문은 직접 답변."
---

# TFT Advisor Orchestrator

TFT 게임 화면을 실시간으로 읽고, 통계 사이트 데이터 + TypeSafe Jev 판단으로 챔피언·증강·아이템을 추천하는 Python 앱(`tft_advisor`)의 개발 에이전트들을 조율한다.

## 실행 모드: 서브 에이전트 (파일 기반 협업)

이 환경에는 TeamCreate/SendMessage가 없으므로 `Agent` 도구로 에이전트를 호출하고, 에이전트 간 정보는 `_workspace/` 파일로 전달한다. 에이전트끼리 직접 대화할 수 없으므로 **각 에이전트 프롬프트에 읽어야 할 다른 에이전트의 산출물 경로를 명시**한다. 모든 호출에 `model: "opus"`를 지정한다.

## 에이전트 구성

| 에이전트 | subagent_type | 역할 | 스킬 | 주요 출력 |
|---|---|---|---|---|
| stats-researcher | stats-researcher | 통계 소스·수집기·DB | tft-stats-collect | `src/tft_advisor/stats/`, `data/` |
| vision-engineer | vision-engineer | 캡처·인식 → GameState | tft-screen-vision | `src/tft_advisor/vision/` |
| jev-strategist | jev-strategist | 후보·Jev 질문·점수 | jev-recommender | `src/tft_advisor/advisor/` |
| app-integrator | app-integrator | 계약·실시간 루프·UI | (에이전트 정의 내장) | `contracts.py`, `app/` |
| qa-validator | qa-validator | 경계면·정확도·품질 검증 | tft-qa | `tests/`, QA 보고서 |

## 프로젝트 레이아웃 (목표)

```
LOL Chess/
├── pyproject.toml
├── config/weights.toml, config/settings.toml
├── src/tft_advisor/
│   ├── contracts.py        # GameState, Recommendation 등 — 단일 진실 원천
│   ├── stats/              # 수집기, DB 접근
│   ├── vision/             # capture, regions, recognize, ocr
│   ├── advisor/            # candidates, jev_questions, scoring
│   └── app/                # live loop, overlay
├── data/static/{set}/      # 챔피언·특성·아이템·증강 정적 데이터
├── data/templates/{set}/   # 아이콘 템플릿
├── data/stats/             # 패치별 통계 DB
├── tests/fixtures/screens/ # 스크린샷 + expected.json
├── tests/fixtures/states/  # 가짜 GameState 상황
└── _workspace/             # 에이전트 중간 산출물 (보존)
```

## 워크플로우

### Phase 0: 컨텍스트 확인
1. `_workspace/`와 `src/tft_advisor/` 존재 여부 확인
2. 모드 결정:
   - 둘 다 없음 → **초기 구축**: Phase 1부터
   - 존재 + 특정 모듈 수정 요청 → **부분 재실행**: 해당 에이전트 + qa-validator만 호출
   - 존재 + "새 패치/시즌" → **패치 업데이트 흐름**(아래)
3. 부분 재실행 시 이전 보고서 경로를 프롬프트에 포함한다.

### Phase 1: 조사 (병렬)
단일 메시지에서 동시 호출 (`run_in_background: true`):
- stats-researcher → `_workspace/01_stats-researcher_sources.md` + `data/static/{set}/` 초안
- vision-engineer → `_workspace/01_vision-engineer_layout.md` (스크린샷 `tests/fixtures/screens/` 있으면 활용, 없으면 사용자에게 요청 사항 정리)

완료 후 두 보고서를 읽고 사용자에게 **통계 소스 선정안**을 요약·확인받는다(소스 선택은 사용자 결정 사항).

### Phase 2: 계약 확정 (순차)
- app-integrator → `pyproject.toml`, 패키지 골격, `contracts.py` (Phase 1 보고서 둘을 입력으로)
- jev-strategist → `_workspace/02_jev-strategist_design.md` (contracts.py + 통계 소스 보고서 입력)
- qa-validator → contracts ↔ 설계 문서 ↔ 소스 보고서 교차 검증 `_workspace/02_qa-validator_report.md`

FAIL 항목이 있으면 해당 에이전트 1회 재호출 후 진행.

### Phase 3: 구현 (병렬) + 점진 QA
동시 호출:
- stats-researcher → 수집기 + DB
- vision-engineer → 인식 모듈 (fixture 기준)
- jev-strategist → advisor 모듈 (`tests/fixtures/states/` 가짜 상태로 개발)

각 에이전트 완료 **즉시** qa-validator를 그 모듈 대상으로 호출한다(전체 완료를 기다리지 않음).

### Phase 4: 통합
- app-integrator → 실시간 루프 + 오버레이 + `--screenshot` 모드
- qa-validator → 전체 경계면 + end-to-end(스크린샷 → 추천) 검증

### Phase 5: 실사용 피드백
1. 사용자에게 실행 방법과 결과 요약 보고
2. 피드백 수집 → 해당 에이전트 부분 재실행
3. 반복되는 피드백은 스킬/에이전트 정의에 반영하고 CLAUDE.md 변경 이력에 기록

### 패치 업데이트 흐름
1. stats-researcher: 정적 데이터·통계 갱신, 변경 목록(`_workspace/patch_{ver}_diff.md`) 작성
2. vision-engineer: 새/변경 아이콘 템플릿 갱신 (diff 파일 입력)
3. jev-strategist: 새 증강/덱 반영 확인, 가중치 점검
4. qa-validator: 회귀 테스트

## 사용자 결정이 필요한 지점
- 통계 소스 선정 (Phase 1 후)
- 스크린샷 제공 및 정답 라벨 확인 (Phase 1/3)
- UI 형태: 오버레이 vs 보조 창 (Phase 4 전)

## 에러 핸들링

| 상황 | 전략 |
|---|---|
| 에이전트 1개 실패 | 1회 재시도, 재실패 시 누락을 명시하고 나머지 진행 |
| 스크린샷 미제공 | vision은 스크린샷 불필요 부분만 구현, Phase 3 인식 구현은 대기 |
| 통계 소스 차단 | 대체 소스로 전환, 사용자에게 보고 |
| Jev API 키 없음/실패 | mock 모드로 개발 진행, live 테스트는 보류 표시 |
| 에이전트 간 데이터 충돌 | 출처 병기, 삭제 금지, contracts 변경은 app-integrator 경유 |

## 테스트 시나리오

### 정상 흐름
1. 사용자: "tft advisor 구축 시작해줘"
2. Phase 0: `_workspace/` 없음 → 초기 구축
3. Phase 1: 두 에이전트 병렬 → 소스 보고서·레이아웃 보고서 → 사용자 소스 확인
4. Phase 2: contracts.py 생성, 설계 문서, QA PASS
5. Phase 3: 세 모듈 병렬 구현, 각각 점진 QA
6. Phase 4: `python -m tft_advisor --screenshot tests/fixtures/screens/shop_01.png` 가 추천을 출력

### 에러 흐름
1. Phase 3에서 OCR 설치 실패로 vision-engineer 부분 실패
2. 1회 재호출 → 대안 엔진(숫자 템플릿 매칭)으로 전환
3. qa-validator가 골드/레벨 정확도를 측정해 보고
4. 최종 보고에 "OCR 대안 사용, 정확도 X%" 명시
