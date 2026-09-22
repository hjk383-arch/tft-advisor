---
name: jev-recommender
description: "TypeSafe Jev(System One)로 TFT 추천 엔진 구현하는 방법. 보유 핵심 아이템·증강 기반 최종 덱 후보(1~3개) 선정, 라운드/레벨별 빌드업 경로, 상점 챔피언 구매 적합도, 증강 선택, 아이템 조합 추천을 위한 Choice/Score/Noul 질문 설계, 후보 생성, 통계+Jev 합성 점수(composite scoring), typesafe_sdk 사용법. 추천 품질 개선·가중치 조정·질문 수정·빌드업 로직 수정 시에도 사용."
---

# Jev Recommender

## 0. 먼저 최신 문서를 확인한다
TypeSafe 문서가 진실 원천이다: https://docs.typesafe.ai/llms.txt (페이지 경로 + `.md`로 마크다운 획득). 구현 전 최소한 `sdk/python/usage.md`, `primitives/choice.md`, `primitives/score.md`, `patterns/composite-scoring.md`를 읽는다. SDK 세부는 `references/typesafe-sdk.md` 참조.

핵심 사실: **Jev는 텍스트(문자열/JSON)만 받는다.** 이미지는 vision 모듈이 GameState로 바꾼 뒤 전달한다.

## 1. 추천의 기본 사고방식 (사용자 도메인 요구사항)

TFT의 의사결정은 두 개의 시간축을 동시에 본다. 이 구조를 어기면 추천이 "통계상 좋지만 지금 못 가는 덱"을 강요하게 된다.

1. **최종 덱은 내 자원이 결정한다.** 보유한 **핵심 아이템(완성템·재료)과 증강**(+ 상징, 보유 고성 유닛)이 어떤 덱으로 갈지를 정한다. 덱의 전체 승률은 자원 적합도가 비슷한 후보들 사이의 **타이브레이커**일 뿐, 1차 기준이 아니다.
2. **빌드업: 라운드마다 그 라운드에 강한 것을 쓰면서 최종 덱으로 간다.** 상점 확률은 레벨에 묶여 있어, 초반에는 저코스트 기물 위주로만 나오고 레벨이 오를수록 고코스트가 나온다. 따라서 "지금 무엇을 살까"는 (a) 현재 스테이지·레벨에서 전력으로서의 가치 + (b) 최종 덱 후보로 가는 경로상의 가치(최종 덱 유닛, 빌드업 보드 유닛, 특성 연결)로 판단한다.
3. **최종 덱 후보는 1~3개를 유지하고 사용자에게 보여준다.** 초반에는 아이템/증강이 적어 후보가 넓고, 자원이 쌓일수록 좁혀진다. 하나로 너무 일찍 고정하지 않는다.

## 2. 역할 분담

| 코드가 한다 (결정적) | Jev가 한다 (의미 판단) |
|---|---|
| 레벨별 상점 확률표로 "지금/다음 레벨에 볼 수 있는 코스트" 계산 | 내 핵심 아이템·증강이 이 최종 덱과 얼마나 맞는가 |
| 덱 후보 1차 필터 (보유 아이템이 덱의 캐리 BIS에 쓰이는지, 증강-덱 통계) | 이 상점 유닛이 지금 보드 전력/빌드업 경로에 기여하는가 |
| 스테이지별 유닛·빌드업 보드 통계 조회, 표본 보정 | 이 증강이 내 아이템·후보 덱과 시너지가 있는가 |
| 조합 가능한 완성템 목록 계산 | 이 완성템을 지금 누구에게, 무엇을 만들까 |
| 최종 가중합, 후보 1~3개 컷, 순위 안정화 | — |

## 3. 추천 파이프라인 (요청 1회당)

```
GameState + 통계 DB
  │
  ├─(코드) 덱 후보 풀: 현재 패치 메타 덱 전체 → 아이템/증강 호환성으로 상위 N(≈6~8)개 추림
  │
  ├─(Jev, 한 요청에 병렬)
  │   comp_item_fit_{c}     Score  — 보유 핵심 아이템이 덱 c의 캐리/탱커 아이템으로 쓰이는 정도
  │   comp_augment_fit_{c}  Score  — 보유 증강이 덱 c의 핵심 특성·플랜과 맞는 정도
  │   comp_board_fit_{c}    Score  — 현재 보드/벤치(고성 유닛 포함)에서 덱 c로 전환 가능한 정도
  │   shop_now_{i}          Score  — shop[i]가 현재 스테이지 보드 전력에 주는 가치
  │   shop_path_{i}         Score  — shop[i]가 후보 덱들의 최종 보드·빌드업 보드에 쓰이는 정도
  │   augment_fit_{a}       Score  — (증강 선택 화면일 때) 증강 a가 내 자원·후보 덱과 맞는 정도
  │   item_pick             Choice — (재료 2개 이상일 때) 만들 완성템 선택
  │
  └─(코드) 합성 → 최종 덱 1~3개 + 상점 추천 + 증강/아이템 추천
```

- 모든 질문이 같은 state를 보므로 **한 요청에 묶는다**. 덱 후보별 질문은 추측적 팬아웃(후보마다 질문 복제)으로 만들고 코드가 결과를 조합한다.
- `shop_path_{i}`는 state의 `candidate_comps`(각 덱의 최종 보드 + 스테이지별 빌드업 보드)를 참조하게 한다. Jev가 "어느 덱"인지 추측하지 않도록 후보를 명시한다.

## 4. state 구성 (JSON 객체)

```json
{
  "game": {"stage": "3-2", "level": 6, "gold": 34, "hp": 62,
           "shop_odds_now": {"1": 0.25, "2": 0.40, "3": 0.30, "4": 0.05, "5": 0.0}},
  "resources": {
    "completed_items": ["Guinsoo's Rageblade"],
    "item_components": ["B.F. Sword", "Tear of the Goddess"],
    "augments": [{"name": "...", "description": "..."}],
    "emblems": []
  },
  "board": [{"unit": "Jinx", "cost": 4, "star": 1, "items": ["Guinsoo's Rageblade"]}],
  "bench": [...],
  "active_traits": [{"trait": "Sniper", "count": 2, "next_breakpoint": 4}],
  "candidate_comps": [
    {"id": "c1", "name": "...", "final_board": [...], "carry": "...",
     "carry_bis_items": [...], "key_traits": [...],
     "buildup": {"stage2": [...], "stage3": [...], "stage4": [...]},
     "avg_place": 4.1, "games": 12000}
  ],
  "shop": [{"unit": "Ahri", "cost": 2, "traits": [...]}],
  "augment_offer": [{"name": "...", "description": "..."}]
}
```
- 이름은 사람이 읽는 이름을 쓴다(내부 ID는 모델에게 의미가 약하다). 증강은 **설명 텍스트를 반드시 포함**한다.
- 신뢰도 낮은 인식 필드는 제외한다.

## 5. 질문 작성 규칙
- Score 레벨은 정도어가 아니라 **상황 묘사**로 쓴다. 예 `comp_item_fit`:
  0 "보유 아이템이 이 덱의 어떤 핵심 유닛에게도 쓰이지 않는다" /
  1 "보조 유닛이나 탱커에게는 쓸 수 있지만 캐리 아이템은 아니다" /
  2 "메인 캐리의 핵심 아이템 중 일부를 이미 갖췄거나 바로 만들 수 있다"
- 질문 ID는 모델에 전달되지 않으므로 instructions에 필요한 의미를 전부 담는다. state 경로는 `` `candidate_comps[1]` `` 처럼 백틱으로 참조한다.
- Choice에는 no-match 선택지(`"hold_components"` 등)를 둔다.

## 6. 합성 점수

### 최종 덱 후보
```
resource_fit(c) = wi*item_fit + wa*augment_fit + wb*board_fit          # Jev, 0~1 정규화
comp_score(c)   = resource_fit(c) * (1 - wt) + wt * stat_norm(avg_place, games)   # wt 작게(≈0.2)
```
- 표시: 상위 1~3개. **2·3위는 1위 점수의 일정 비율(예 ≥ 0.75) 이상일 때만** 보여준다(자원이 확실하면 1개만).
- 순위 안정화: 이전 요청의 후보를 약하게 가산(히스테리시스)해 라운드마다 덱이 널뛰지 않게 한다. 단, 새 핵심 아이템·증강을 얻으면 즉시 재평가한다.

### 상점 유닛
```
shop_score(i) = ws(stage) * now_value(i) + wp(stage) * path_value(i)
now_value     = Jev shop_now 와 스테이지별 유닛 통계(해당 스테이지 보드에서의 성적) 합성
path_value    = Jev shop_path, 코드 검증(유닛이 후보 덱 final_board/buildup에 있으면 가산, 상위 덱 가중)
```
- `ws/wp`는 스테이지 함수: 2스테이지는 now 비중 큼(연승/체력 관리), 4스테이지 이후는 path 비중 큼. 값은 `config/weights.toml`.

### 공통
- 표본 수축: `adj = (games*x + k*prior)/(games + k)`.
- Jev `confidence`가 낮으면 해당 Jev 항의 가중치를 줄인다.
- 원시 판단(Jev 답, 통계)은 저장해 두고 가중치만 바꿔 재계산 가능하게 한다.

## 7. 출력 (`Recommendation`, contracts.py)
- `target_comps`: 1~3개, 각 `{name, score, reasons[], owned_units, missing_units, items_ready, next_buildup_board}` — 사용자가 "내가 궁극적으로 어떤 덱으로 가는지"와 "지금 무엇이 모자란지"를 알 수 있게
- `shop`: 5칸 각각 `{unit, buy: bool, score, reason_tag}` (reason_tag: "지금 전력" / "최종 덱 유닛" / "빌드업" / "2성 가능")
- `augment` / `item`: 해당 화면에서만

## 8. 실시간 제약
- 추천 1회 목표 < 2초. `AsyncTypeSafeClient`, 상태 변화가 있을 때만 호출, state 해시 캐시.
- 429/529는 SDK 재시도 → 최종 실패 시 통계 전용 추천 폴백(UI에 "Jev 미사용" 표시).
- 요청 수·토큰 usage를 로그로 남긴다.

## 9. 검증
- `tests/fixtures/states/`에 대표 상황을 둔다: 같은 보드 + 다른 핵심 아이템 → **다른 최종 덱**이 나와야 한다 / 2-1 상점에서 저코스트 강한 유닛 우선 / 4-1 이후 최종 덱 유닛 우선 / 증강이 특정 특성을 요구할 때 해당 덱이 1위.
- 실패 사례는 state·질문·후보·답·합성 과정을 모두 로그로 남겨 원인을 분리한다(인식 오류 / 후보 누락 / 질문 표현 / 가중치).
