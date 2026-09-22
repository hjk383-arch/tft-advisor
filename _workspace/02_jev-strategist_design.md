# 02 jev-strategist: 추천 엔진 설계 (Phase 2, 구현은 Phase 3)

작성일: 2026-09-21 / 작성자: jev-strategist / 대상 계약: `contracts.py` CONTRACT_VERSION 0.1.0

**변경 이력**
| 날짜 | 내용 |
|---|---|
| 2026-09-21 | 초판 |
| 2026-09-22 | Phase 2 QA FAIL 해소(부분 재실행). 추가: §4.3 결측 필드(hp/board/bench/장착 아이템=None) 규칙, §5.4 `TargetComp`·`Recommendation` 메타 필드 채우기 규칙, **§10a 최종 설정 키 표(app-integrator 구현 기준, 이 표가 §10의 키 목록을 대체)**, §10 계약 제안 1~5 채택 결정. 수정: 공식 내부 상수를 설정 키로 분리(§2.2, §5.2, §5.3), `item_usage` 임계값을 pcnt(덱당 평균 개수) 기준으로 재정의, `commit_by_stage` 1·5+ 조회 규칙(§7), `fallback_reason` 닫힌 9종(§8.1), §2.1 수치(548/694/751/884), §3.2 MVP augment_select 46문항, S1/I1/A2 문구 변형(결측 필드 참조 금지), QUESTIONS_VERSION q1→q2 |

표기: **[문서]** = TypeSafe 문서에서 확인 / **[데이터]** = 로컬 캐시·정적 데이터에서 확인 / **[추측]** = 근거 없는 설계 가정(Phase 3에서 측정·튜닝 대상)

---

## 0. 설계 전에 확인한 사실 (TypeSafe 문서, 2026-09-21 조회)

| 항목 | 확인 결과 | 출처 |
|---|---|---|
| 모델 | `jev-latest` → `jev-1.13.0`. 응답 `model` 필드에 버전 ID가 온다 → 로그에 남긴다 | models.md |
| 요청당 한도 | **질문 수 제한 명시 없음.** 토큰 한도: 요청 전체 64k, `state` + 가장 긴 질문 32k | models.md |
| Choice / Score | Choice 옵션 최대 255개. Score 레벨 2~10개 | api.md |
| rate limit | 250k tok/s, 1,200 req/min (동적 조정 중). 429 → SDK가 백오프 재시도, `retry-after` 존중 | models.md |
| state 형식 | 문자열, **JSON 객체(dict)**, 배열 모두 허용. `None`은 불가(내부 값 None은 허용). 권장은 객체 | concepts/state.md, questions.md |
| 응답 필드 | `result.choices[id].choice / .probabilities: dict[str,float] / .confidence`, `result.scores[id].score / .probabilities: dict[int,float] / .legend / .confidence`, `result.nouls[id].noul`, `result.usage.input_tokens/output_tokens`, `result.model`, `result.request_id` | responses.md |
| 언어 | **영어가 주 학습 언어이고 정확도가 가장 높다.** CJK 등은 "처리하지만 동등하지 않다" | models.md "Language support", state.md |
| 수치 약점 | 세기(counting)·산술·숫자 비교 불안정. 숫자는 코드에서 계산해 **이름 붙은 구간(bucket)** 으로 넘겨라 | jev-1.13 jaggedness |
| state 크기 | 무관한 내용이 많을수록 정확도 하락(context rot). 필요한 필드만 보낸다 | jaggedness #5 |
| 간접 참조 | 여러 단계 참조는 정확도 하락. state 경로를 이름으로 직접 가리켜라 | jaggedness #4 |
| Score 레벨 | "정도어가 아니라 상황 묘사". 각 레벨은 따로 평가되며 모델은 레벨 번호·이웃을 보지 못한다. 한 질문 = 한 차원 | score.md |
| Choice | 상대 비교(어느 것). Noul/Score는 절대 평가. 둘 사이 수치 항등식을 기대하지 말 것. `other/none` 옵션 권장. 옵션 설명은 구조화 객체 허용 | choice.md, jaggedness #8, advanced.md |
| 팬아웃 | 같은 state의 모든 질문을 한 요청에. 질문은 병렬 평가되어 추가 질문의 지연 영향이 작다. 13문항: 1회 0.27s vs 13회 2.71s | fan-out.md, parallel_questions cookbook |
| 재시도 | `RetryPolicy(max_retries, backoff_initial, backoff_max, backoff_jitter, http_statuses, respect_retry_after, api_connection_error, api_timeout_error, exceptions, predicate, timeout=총 예산 초)`. 클라이언트 또는 호출 단위로 지정 | retries.md |
| 예외 | `TypeSafeAPIError(.status, .request_id)`, 429 → 대기 ms 필드, 5xx, `TypeSafeAPIConnectionError`, `TypeSafeAPITimeoutError` | exceptions.md |
| 로깅 주의 | `TYPESAFE_LOG_LEVEL=debug`는 요청/응답 **본문을 가리지 않는다**(키 헤더는 가림). 배포 설정은 `info` 이하 | usage.md |
| 가격 | 입력 $0.042/Mtok, 출력 무료 | models.md |

**언어 결정: instructions·criteria·state 모두 영어. UI 표시만 한국어(`name_ko`).**
근거: (1) 문서가 영어를 주 학습 언어로 명시했다. (2) 정적 데이터에 `name_en`, 증강 `desc_en`이 모두 있다 [데이터]. (3) Set 18 완성템은 `desc_ko`가 비어 있어서 Jev는 아이템 이름에서 기능을 떠올려야 한다. 영어 이름("Archangel's Staff")이 모델의 사전 지식과 더 잘 맞을 가능성이 높다 [추측]. 챔피언 `name_en`은 상점 풀 74명 중 중복이 없고, 완성/상징 아이템은 "Flora Fatalis Emblem" 1건만 중복이다 [데이터]. 중복 건은 접미사로 구분한다.
대가: 증강 `desc_en` 592개 중 193개에 치환되지 않은 `?` 값이 있다 [데이터]. 의미 판단에는 큰 지장이 없다고 보지만, `?`는 "X"로 바꿔 보낸다 [추측].

---

## 1. 파이프라인

```
GameState ─┐
           ├─(코드, <30ms) ① 신뢰도 필터 → ② 파생값(조합 가능 완성템, 사본 수, 구간 라벨) → ③ 덱 후보 1차 필터 상위 N
통계 DB  ──┘                                   │
                                               ├─ ④ 통계 전용 추천(폴백 경로)을 항상 먼저 계산 → 즉시 UI에 표시 가능
                                               ├─ ⑤ Jev state(영어 JSON) + 질문 묶음(모드별) → state 해시 캐시 조회
                                               │     └ 미스 시 AsyncTypeSafeClient.system_one 1회 (타임아웃 1.5s)
                                               └─ ⑥ 합성(가중치 = weights.toml) → 히스테리시스 → 컷 → Recommendation
                                                     debug: {state, questions_version, answers 원본, 통계 원값, 항목별 중간값}
```

- 요청 1회에 Jev 호출은 **최대 1번**이다. 모드별 질문을 모두 한 요청에 묶는다.
- ④를 먼저 계산하는 이유: Jev가 실패하거나 느려도 추천이 비지 않는다. 앱이 원하면 ④를 먼저 보여주고 ⑥으로 교체한다(점진 표시, app-integrator 선택).

### 1.1 화면 모드별 질문 구성

| ScreenMode | 호출 조건 | 보내는 질문 | 비고 |
|---|---|---|---|
| `planning` | 상점 5칸 집합, 자원 시그니처(8절), 레벨, 스테이지 중 하나가 바뀌었을 때 | C1 `comp_item_fit_k`, C2 `comp_augment_fit_k`, C3 `comp_board_fit_k`(각 k=0..N-1), C4 `comp_pick`, S1 `shop_now_i`, S2 `shop_path_i`(챔피언 칸), S3 `special_value_i`(특수 상품 칸), I1 `item_pick`(조합 가능 완성템 ≥1) | 기본 모드 |
| `augment_select` | 제시 증강 3개가 바뀔 때(리롤 포함) | C1~C4 + A1 `aug_comp_fit_a_k`(3×N), A2 `aug_standalone_a`(3), A3 `aug_pick` | 상점이 보이지 않으므로 S* 없음. I1은 재료가 있으면 포함(값싼 추측 질문) |
| `carousel` | 진입 시 1회 | **Jev 호출 없음.** 코드가 목표 덱들의 BIS 부족 재료 우선순위를 계산 | 캐러셀 유닛·아이템은 GameState에 없다 → 10절 제안 |
| `item_select` | 제시 아이템이 바뀔 때 | (계약 확장 후) I2 `item_offer_pick` + C1~C4 | 제시 아이템 필드가 계약에 없다 → 10절 제안. 그전에는 호출 안 함 |
| `combat` | 없음 | 직전 추천 유지 | 전투 후 아이템 획득은 다음 planning에서 반영 |
| `loading` / `unknown` | 없음 | 직전 추천 유지(unknown), 초기화(loading) | |
| `game_over` | 없음 | 세션 초기화(히스테리시스 기록 삭제) | |

C2는 보유 증강이 없으면, C3은 보드·벤치가 신뢰 가능하지 않으면, C1은 보유 아이템이 0개면 **보내지 않는다**(가중치 재분배는 5절).

---

## 2. 덱 후보 풀 1차 필터 (코드)

입력: 현재 패치의 `CompStats` 전체(현재 MetaTFT 57 클러스터 [데이터]).

### 2.1 사전 정리
1. `games < prefilter.min_games`(기본 1000) 제외. 2026-09-21 캐시 기준으로 548/694/751/884판짜리 덱 4개가 빠진다 [데이터]. stats 재수집 후 수치는 달라질 수 있다.
2. **중복 제거**: 최종 보드 유닛 집합의 Jaccard ≥ `prefilter.dedupe_jaccard`(0.75)이고 carry가 같으면 표본이 많은 쪽만 남긴다. 예: "Blossom, Sett, Ahri"(Fast 8)와 "Blossom, Ahri, Sett"(Fast 9), comp_augment_tiers의 "APHELIOS > Lvl 8 push" 2건(424001, 424003) [데이터]. 그러지 않으면 2·3위에 사실상 같은 덱이 나온다.

### 2.2 1차 점수 p(c) (모든 항 0~1)

```
p(c) = pf.w_item*I(c) + pf.w_aug*A(c) + pf.w_unit*U(c) + pf.w_stat*S(c)      # 0.40 / 0.20 / 0.25 / 0.15

b(x,c)  아이템 x의 덱 c 적합도 (위에서부터 첫 번째로 맞는 규칙, 키는 [item_fit])
        = item_fit.carry_bis (1.0)         x ∈ carry_bis_items
        = item_fit.core_unit (0.7)         x ∈ 다른 is_core 유닛의 CompUnit.items
        = item_fit.usage (0.4)             item_usage[x] ≥ item_fit.usage_min_pcnt (0.3)   ※ 아래 pcnt 정의
        = 상징: item_fit.emblem_key_trait (1.0)  상징 특성 ∈ key_traits(c)
                item_fit.emblem_other (0.2)      그 외
        = 0
I(c) = min(1, (Σ_{x∈보유 완성템·상징} b(x,c) + pf.craftable_factor·Σ_{y∈조합 가능 완성템} b(y,c)) / pf.item_saturation)   # 0.5, saturation=2
        "보유 완성템" = 아이템 벤치 completed + 장착 아이템(보드가 신뢰 가능하면 UnitOnBoard.items, 아니면 §4.3 추적 장착분)
A(c) = 보유 증강 a마다 t(a,c)의 평균, 증강 없으면 pf.aug_neutral (0.5)
        t(a,c) = 1.0             a.associated_traits ∩ key_traits(c) ≠ ∅   (augments.json, 19개 [데이터])
               = tier_score(comp_augment_tiers[c][a])   덱별 등급에 있으면
               = tier_score(augments_tiers[a])          전체 등급에 있으면
               = pf.aug_neutral (0.5)                   둘 다 없으면(중립)
U(c) = min(1, Σ_{u∈보유 유닛} w_u / pf.unit_saturation)   # saturation=4
        w_u = pf.unit_w_core (1.0, final_board is_core) / pf.unit_w_final (0.6, final_board 기타)
              / pf.unit_w_buildup (0.3, 현재·다음 레벨 buildup 보드에만 등장) / 0, 2성 이상이면 × pf.unit_star_mult (1.5)
        보유 유닛을 모르면(§4.3) 모든 c에서 U(c)=0 → 상수항이라 순위에 영향 없음
S(c) = stat_norm(c)   (5.1절과 같은 함수)
```

**`item_usage` / pcnt 정의 (2026-09-22 재정의)**: `CompStats.item_usage[x]` = MetaTFT `build_items[x].pcnt` = 그 덱을 한 플레이어 1명(게임 종료 시점)이 가진 아이템 x의 **평균 개수**다. 비율이 아니므로 1을 넘을 수 있다(실측 최대 1.38, 1 초과 35건: 캐리가 같은 아이템 2개를 드는 경우). 임계값 `item_fit.usage_min_pcnt = 0.3`의 뜻은 "이 덱 플레이어 10명 중 약 3명분 이상의 개수가 쓰인다"이다. 1 초과 값도 그대로 비교한다(자르지 않는다). carry_bis/core 아이템은 이미 위 단계에서 걸리므로 이 단계는 "BIS는 아니지만 덱에서 흔히 쓰는 아이템"만 잡는다.

### 2.3 상위 N 결정
- N = `settings.advisor.max_candidate_comps`(8).
- 반드시 포함(쿼터): 직전 추천에 표시된 덱(히스테리시스 대상), S(c) 상위 `prefilter.stat_quota`(2)개(메타 대표 덱을 항상 검토). 쿼터 합이 N을 넘으면 직전 표시 덱 → S 상위 순으로 채우고 자른다(`stat_quota`는 코드에서 `min(stat_quota, N)`으로 클램프).
- 나머지는 p(c) 순.
- **N=8 근거**: (a) 화면에는 1~3개만 나가므로 Jev가 재정렬할 여유가 필요하다. 1차 필터와 최종 점수가 같은 신호(아이템·증강·유닛)를 쓰므로 진짜 1위가 8위 밖에 있을 가능성은 낮다 [추측]. (b) 덱당 state는 약 300토큰, 질문은 덱당 3~6개다. 8개면 state 약 2.5k, 질문 약 24~48개로 한도(64k)의 20% 이하다. (c) 문서상 질문 추가는 지연에 거의 영향이 없다. 비용을 정하는 것은 state 크기와 정확도(context rot)다. 그래서 N을 더 늘리기보다 덱 표현을 줄이는 쪽을 택한다.
- 덱이 넓게 열려 있는 초반(아이템·증강·유닛 없음)에는 I=0, A=0.5, U=0이라 S만 남는다. 결국 메타 상위 8개가 된다(의도한 동작).

---

## 3. Jev 질문 전체 목록

공통 규칙
- 질문 ID는 모델에 전달되지 않는다 [문서]. 그래서 instructions에 대상 이름을 **직접** 넣는다. 예: `` `candidate_comps[2]` ("Lunar Aphelios") ``. 이름과 경로를 함께 넣어 간접 참조를 줄인다.
- 점수 정규화: `x = score / (레벨수 - 1)`.
- `{name}` 등은 코드가 채우는 자리다. 질문 문구 버전 `QUESTIONS_VERSION = "q2"`(2026-09-22: 결측 필드 변형 추가)는 캐시 키와 debug에 남긴다.
- **결측 키 참조 금지**: 질문 문구는 그 요청의 state에 실제로 있는 키만 참조한다. hp·board/bench가 없을 때 쓰는 변형 문구는 §4.3에 있다. 변형 선택은 state 내용으로 결정되므로 캐시 키(state 해시)가 자동으로 구분한다.
- 레벨 문구는 모두 [추측]이다. Phase 3에서 fixture로 검증한 뒤 고친다.

### C1 `comp_item_fit_{k}` — Score(4)
- 조건: 보유 완성템·상징·재료 ≥ 1
- instructions: `How well do the player's items (resources.completed_items, resources.emblems, resources.item_components, resources.craftable_items) fit the team comp candidate_comps[{k}] ("{comp_name}")? Compare them with that comp's main_carry_items and the items listed on its final_board units.`
- levels:
  0. `None of the player's items or components is used by any unit of this comp; they would go to filler units or be wasted.`
  1. `Some of the player's items suit this comp's tanks or support units, but none is an item its main carry uses.`
  2. `One core item of this comp's main carry is already completed or appears in resources.craftable_items.`
  3. `Two or more core items of this comp's main carry are completed or craftable, or the player holds an emblem of this comp's key trait.`
- 참조: `resources.*`, `candidate_comps[k].main_carry_items`, `.final_board[].items`
- Jev의 몫: 기능이 비슷한 대체 아이템(AP 아이템끼리, 탱커 아이템끼리)의 적합성 판단. 정확한 일치는 코드의 b(x,c)가 이미 계산한다.

### C2 `comp_augment_fit_{k}` — Score(4)
- 조건: `augments_owned` ≥ 1
- instructions: `How well do the player's augments (resources.augments, read their descriptions) support playing the team comp candidate_comps[{k}] ("{comp_name}")?`
- levels:
  0. `The augments work against this comp: they reward a trait, unit type or play pattern (for example rerolling low-cost units, or leveling fast) that this comp does not use.`
  1. `The augments are generic (gold, items, or plain stats) and help this comp about as much as any other comp.`
  2. `At least one augment directly boosts this comp's key trait, its main carry's damage type, or its leveling plan.`
  3. `An augment is made for this comp: it grants or boosts this comp's key trait or its main carry specifically.`

### C3 `comp_board_fit_{k}` — Score(4)
- 조건: board 또는 bench가 신뢰 가능(4절)하고 유닛이 1기 이상
- instructions: `How close are the player's current units (board and bench) to the team comp candidate_comps[{k}] ("{comp_name}"), using that comp's final_board and buildup boards?`
- levels:
  0. `The player's units share no units and no traits with this comp; switching means selling almost everything.`
  1. `A few low-cost units or one trait overlap with this comp's buildup boards, but none of its core units is owned.`
  2. `Several units match this comp's buildup boards and at least one of its core units is owned.`
  3. `The player already owns this comp's main carry or several of its core units, some of them at 2 stars.`

### C4 `comp_pick` — Choice(N+1)
- 조건: 항상(planning·augment_select)
- instructions: `Which team comp in candidate_comps should the player aim for as their final comp, given their items, augments and units?`
- criteria: `{"{comp_name_k}": "candidate_comps[{k}]: main carry {carry}, key traits {traits}", ..., "undecided": "It is too early to tell: the player's items, augments and units do not point to any one of these comps."}`
- 용도: 가중합에는 넣지 않는다. (a) 1·2위 차이가 `comp.tie_eps` 미만일 때 타이브레이커, (b) `P(undecided)`가 0.5 이상이면 표시 컷을 완화한다(5.2절), (c) debug로 튜닝. 근거: Choice는 상대 평가라 Score와 수치를 섞으면 안 된다 [문서 jaggedness #8].

### S1 `shop_now_{i}` — Score(4)
- 조건: `shop[i].kind == champion`이고 칸 신뢰도 ≥ 임계값
- instructions: `How much would buying the unit in shop[{i}] ("{unit}") strengthen the player's team for the fights of the current stage (game.stage_phase), right now?`
- levels:
  0. `It would not be fielded: it is weaker than the units the player already fields and shares no trait with them.`
  1. `A usable filler: it could replace a weak unit or hold a trait for a few rounds, but adds little strength.`
  2. `A clear upgrade now: it activates or raises an active trait, or shop[{i}].buy_makes_2star is true, or it is a strong unit for this stage.`
  3. `One of the best pickups possible at this stage: it makes the current team much stronger immediately.`
- 사본 수·2성 완성 여부는 코드가 `copies_owned`, `buy_makes_2star`로 계산해 넣는다(세기는 Jev에 맡기지 않는다 [문서]).
- 위 문구는 **보유 유닛을 아는 경우(S1-owned)** 용이다. 보드·벤치를 모르면(MVP) §4.3의 S1-noboard 문구를 쓴다.

### S2 `shop_path_{i}` — Score(4)
- 조건: S1과 같음
- instructions: `Does the unit in shop[{i}] ("{unit}") belong to the player's plan toward the comps in candidate_comps (their final_board or their buildup boards)? candidate_comps is ordered from most to least likely.`
- levels:
  0. `It appears in none of the candidate comps' final boards or buildup boards and shares no key trait with them.`
  1. `It only shares a key trait with a candidate comp, or appears only in an early buildup board.`
  2. `It is a supporting unit in a candidate comp's final_board, or appears in the buildup board for the player's next level.`
  3. `It is the main carry or a core unit of the first or second comp in candidate_comps.`
- 코드 검증값 C_path(5.3절)와 합성한다. 정확한 포함 여부는 코드가 더 정확하다. Jev가 더하는 몫은 특성 연결과 "다음 레벨 보드" 판단이다.

### S3 `special_value_{i}` — Score(3)
- 조건: `shop[i].kind == special`(예: `DA_ThreeMe18` "3단계와 함께")
- instructions: `How valuable is buying the special shop offer shop[{i}] ("{name}": "{description}") for this player right now?`
- levels: 0 `Not useful for this player now.` / 1 `Some value: a modest boost the player can use.` / 2 `High value: it directly strengthens the current team or the top candidate comp.`
- path 항은 0으로 두고 now만 쓴다. 가격(9골드 등)은 CDragon에 없다 [데이터]. 골드 판단은 코드가 하지 않고 표시만 한다. 단, vision이 `ShopSlot.cost`를 읽으면 그 값을 buy 골드 누적(5.3)에 쓴다.
- 설명은 `shop_specials.json`의 `desc_en`을 쓴다. `desc_en`이 없으면 `"description"` 키를 빼고 이름만 보낸 뒤 S3 gate를 `jev.low_confidence_scale`로 강제한다(증강 신규 설명 없음과 같은 처리).

### A1 `aug_comp_fit_{a}_{k}` — Score(4), 추측적 팬아웃 3×N
- 조건: augment_select, a∈{0,1,2}, k∈{0..N-1}
- instructions: `If the player takes the augment augment_offer[{a}] ("{aug_name}"), how well would it support playing the team comp candidate_comps[{k}] ("{comp_name}")?`
- levels: C2와 같은 4개 문구(주어를 "this augment"로 바꾼다).
- 이 결과로 "증강 a를 고르면 덱 순위가 어떻게 바뀌는가"를 반사실적으로 계산한다(7절).

### A2 `aug_standalone_{a}` — Score(4)
- instructions: `Regardless of which comp the player ends up playing, how much does the augment augment_offer[{a}] ("{aug_name}") help this player, given their stage, health, gold and items?`
- levels:
  0. `Little or no value for this player: its condition is unlikely to be met, or its reward arrives too late for the player's health.`
  1. `Modest generic value: a little gold, small stats, or a minor item.`
  2. `Strong generic value: significant gold, a completed item, or a combat effect that works in most comps.`
  3. `Game-changing for this player's situation: a large immediate resource or effect, such as helping a low-health player stabilize or a rich player level fast.`
- 위 문구는 `game.health`가 state에 있을 때(A2-hp)다. hp를 모르면 §4.3의 A2-nohp 문구를 쓴다.

### A3 `aug_pick` — Choice(3)
- criteria: 제시 증강 3개(이름 → 설명)
- 용도: C4와 같이 타이브레이커와 debug에만 쓴다.

### I1 `item_pick` — Choice(M+1)
- 조건: 조합 가능한 완성템 후보 M ≥ 1(6절). M은 최대 45(재료 10개의 서로 다른 쌍)라 한도(255)보다 훨씬 작다
- instructions: `The player can combine two item components now (resources.item_components). Which completed item should they build first, considering the carries of candidate_comps, the current team, and the player's health (game.health_status)?`
- criteria(구조화 객체 [문서 advanced]): `{"Archangel's Staff": {"from": ["Needlessly Large Rod","Tear of the Goddess"], "used_by": ["Zyra (main carry of Juggernaut Zyra)"]}, ..., "hold_components": "Do not combine yet: none of the buildable items is a core item for the candidate comps, and the player's health is high enough to wait for better components."}`
- `used_by`는 코드가 b(x,c) ≥ `item_fit.used_by_min`(0.7)인 (유닛, 덱)을 영어로 적는다. 비어 있으면 `[]`. 적절한 보유자가 누구인지는 코드가 정한다.
- 위 instructions/hold 문구는 `game.health_status`가 state에 있을 때(I1-hp)다. hp를 모르면 §4.3의 I1-nohp 문구를 쓴다.

### I2 `item_offer_pick` — Choice (계약 확장 후)
- 모루·포탈 등에서 제시된 아이템 중 선택 + `"none_fit"`. 구조는 I1과 같다.

### 3.1 추측적 팬아웃 방식
- 덱 후보별 질문(C1~C3, A1)은 **후보마다 복제**한다. 코드는 필요한 답만 읽는다(예: 보드가 없으면 C3를 아예 보내지 않고, A1은 증강 선택 후 버린다).
- 증강 화면에서도 C1~C3를 함께 보낸다. 같은 state라 추가 지연은 거의 없고, 증강 선택 직후 planning에서 C* 결과를 다시 쓸 수 있다. 단, 캐시 키가 다르므로 재사용은 "자원 시그니처가 같을 때"만 한다.

### 3.2 요청당 질문 수·토큰 추정 [추측, Phase 3에서 `usage`로 실측]
| 모드 | 질문 수(N=8) | state 토큰 | 질문 토큰(≈140/문항, Choice는 옵션 수에 비례) | 합계 |
|---|---|---|---|---|
| planning(보드 있음, 재료 4개) | 24(C1~C3) + 1(C4) + 10(S) + 1(I1) = 36 | 3~4k | ≈5.5k | ≈9~10k |
| **planning(MVP: 보드 없음, C3 제외)** | 16 + 1 + 10 + 1 = **28** | 2.5~3.5k | ≈4.3k | ≈7~8k (QA 재현 7.1~10.1k) |
| augment_select(보드 있음) | 24 + 1 + 24(A1) + 3(A2) + 1(A3) + 1(I1) = 54 | 3~4k | ≈8k | ≈12k |
| **augment_select(MVP: C3 제외)** | 16 + 1 + 24 + 3 + 1 + 1 = **46** | 2.5~3.5k | ≈7k | QA 재현 10.5~14.0k |
- 2026-09-22 정정: 초판의 "53~54"는 C3 8개를 포함한 보드 있음 수치다. MVP(보드 None)는 46문항이다(qa `jev_request_size.py` 재현과 일치).
- 한도: 64k/요청, state+최장 질문 32k 대비 여유가 크다.
- 비용: 요청당 약 $0.0005, 한 게임에 40회 호출해도 약 $0.02.
- 지연: 문서 예(13문항)는 0.27s였다. 우리는 state가 더 커서 0.3~0.9s로 예상한다 [추측]. 목표 2s 안에 들어온다.

---

## 4. Jev state 스키마 (GameState → state 변환)

### 4.1 포함/제외 규칙
| GameState 필드 | 처리 |
|---|---|
| 모든 필드 공통 | `state.is_reliable(f, settings.vision.state_min_confidence=0.6)`가 거짓이면 **키 자체를 뺀다.** None도 뺀다. 빠진 필드에 의존하는 질문은 보내지 않는다 |
| `shop[i]` | 칸 `confidence < 0.6`이거나 kind unknown/empty → `{"slot": i, "status": "unknown"/"empty"}`만 남기고 S* 질문을 보내지 않는다 |
| `board/bench` 유닛 | 유닛 `confidence < 0.6`이면 그 유닛만 뺀다. `hex`, `bench_slot`은 뺀다(배치는 질문과 무관) |
| `items` | ItemRef `confidence < 0.6`이면 제외. 장착 아이템(`UnitOnBoard.items`)은 보드 항목 안에 넣는다 |
| `gold`, `hp`, `streak`, `level`, `xp` | 원래 숫자와 함께 **코드가 계산한 구간 라벨**을 넣는다(4.2). 질문은 라벨을 참조한다 |
| `shop_odds` | %를 라벨로 바꾼다: 0→"none", 1~9→"rare", 10~29→"uncommon", ≥30→"common" |
| `active_traits` | 보드에서 계산한 값만 쓴다. 이름, 현재 수, 활성 구간, 다음 구간 |
| 통계 수치(avg_place, games, 티어) | **넣지 않는다.** 통계는 코드가 합성한다. 넣으면 Jev가 "적합도" 판단에 통계를 섞어 이중 계산이 된다(SKILL 4절 예시의 avg_place/games는 뺀다) |
| ID, confidence, field_source, captured_at 등 | 넣지 않는다 |
| 이름 | 영어 `name_en`(0절). 코드가 ID ↔ 영어 이름 사전을 요청마다 만들어 답을 역매핑한다 |

### 4.2 구체 스키마 (planning 예, 값은 예시)
```json
{
  "game": {
    "stage": "3-2", "stage_phase": "stage 3 (mid game: stabilize and level toward 7-8)",
    "level": 6, "xp_to_next": 12,
    "gold": 34, "gold_status": "can afford a few buys and still keep interest",
    "health": 62, "health_status": "moderate",
    "streak": "2-round win streak",
    "shop_odds": {"1-cost": "common", "2-cost": "common", "3-cost": "uncommon", "4-cost": "rare", "5-cost": "none"}
  },
  "resources": {
    "completed_items": ["Archangel's Staff"],
    "emblems": [],
    "item_components": ["Needlessly Large Rod", "Tear of the Goddess", "Chain Vest"],
    "craftable_items": [
      {"item": "Archangel's Staff", "from": ["Needlessly Large Rod", "Tear of the Goddess"]},
      {"item": "Protector's Vow", "from": ["Tear of the Goddess", "Chain Vest"]}
    ],
    "other_items": [],
    "augments": [{"name": "Seraphim's Staff", "description": "Gain an Archangel's Staff. ..."}]
  },
  "board": [{"unit": "Zyra", "cost": 3, "star": 2, "traits": ["Juggernaut"], "items": []}],
  "bench": [{"unit": "Amumu", "cost": 1, "star": 1, "traits": ["Juggernaut", "Primal"]}],
  "active_traits": [{"trait": "Juggernaut", "count": 3, "active_at": 2, "next_at": 4}],
  "candidate_comps": [
    {
      "name": "Juggernaut Zyra", "plan": "Fast 8",
      "main_carry": "Zyra", "main_carry_items": ["Archangel's Staff", "Archangel's Staff", "Hextech Gunblade"],
      "tanks": ["Amumu", "Maokai"],
      "key_traits": ["Juggernaut 4", "Primal 2"],
      "final_board": [{"unit": "Zyra", "cost": 3, "role": "carry", "items": ["..."]},
                      {"unit": "Amumu", "cost": 1, "role": "tank"}],
      "buildup": {"level 6": ["Zyra", "Amumu", "..."], "level 7": ["..."]}
    }
  ],
  "shop": [{"slot": 0, "unit": "Yorick", "cost": 1, "traits": ["..."], "copies_owned": 2, "buy_makes_2star": true}],
  "augment_offer": [{"name": "Hustler", "description": "..."}]
}
```
- `candidate_comps`는 1차 필터 p(c) 순으로 넣는다(S2 문구가 이 순서를 참조한다).
- 덱당 `buildup`은 **현재 레벨과 다음 레벨** 보드만 넣는다. 레벨마다 count가 가장 많은 보드 1안이고, 소환물(`DA_Elderwood18_Lifeblossom` 등)은 뺀다 [데이터]. 최종 보드는 가장 흔한 레벨(8 또는 levelling에 맞는 레벨)의 1안만 넣는다.
- `role`: 코드가 builds에서 정한다. 3아이템 빌드 표본 1위 유닛이 carry, 탱커 아이템(Gargoyle, Warmog 등 탱커 아이템 ID 목록)을 든 유닛이 tank, 나머지는 support. champions.json `role`은 상점 풀 74명 중 72명이 비어 있어 쓸 수 없다 [데이터].
- 덱 이름: MetaTFT `name_string`(특성, 유닛)을 영어 이름으로 바꿔 만든다(예 "Juggernaut Zyra"). UI는 `CompStats.name`(한국어)을 쓴다.
- 구간 라벨 규칙(코드, 값은 [추측]): health ≥70 "healthy", 40~69 "moderate", 20~39 "low", <20 "critical". gold ≥50 "rich (interest capped)", 30~49 "can afford …", 10~29 "tight", <10 "broke". stage_phase는 스테이지별 고정 문장.
- 구간 경계와 라벨 문장은 **설정 키가 아니라 `jev_state.py` 코드 상수**다. 문장이 곧 질문 입력이라 바꾸면 `QUESTIONS_VERSION`을 올려야 하기 때문이다(가중치 튜닝과 분리).

### 4.3 결측 필드 규칙 (MVP: hp / board / bench / active_traits / 장착 아이템 = None) — 2026-09-22 추가

"모른다"는 `GameState` 값이 None이거나 `is_reliable(f, vision.state_min_confidence)`가 거짓인 경우다. 코드 내부에서는 구간 값 `hp_bucket ∈ {healthy, moderate, low, critical, unknown}`을 쓴다(`unknown`은 state에 절대 넣지 않는다).

**(a) hp를 모를 때**
| 대상 | 규칙 |
|---|---|
| Jev state | `game.health`, `game.health_status` 키를 **모두 뺀다**(`"unknown"` 문자열도 넣지 않는다) |
| hold 규칙(§6.8 코드 경로) | `hp_bucket == unknown`은 **moderate로 간주**한다 → 다른 조건(max bis < hold_bis_max, stage < hold_until_stage)이 맞으면 hold=True가 될 수 있다. critical 안전장치는 발동하지 않는다. 늦은 스테이지 hold는 `hold_until_stage`가 막는다 |
| hp_danger_shift(§5.3) | **적용하지 않는다**(ws, wp는 `for_stage` 값 그대로) |
| A2 문구 → **A2-nohp** | instructions: `Regardless of which comp the player ends up playing, how much does the augment augment_offer[{a}] ("{aug_name}") help this player, given their stage, gold and items?` / level 0: `Little or no value for this player: its condition is unlikely to be met, or its reward arrives too late in the game to matter.` / level 3: `Game-changing for this player's situation: a large immediate resource or effect, such as a big combat boost right now or enough gold to level fast.` (level 1·2는 A2-hp와 같다) |
| I1 문구 → **I1-nohp** | instructions: `The player can combine two item components now (resources.item_components). Which completed item should they build first, considering the carries of candidate_comps and the current stage (game.stage_phase)?` / `hold_components`: `Do not combine yet: none of the buildable items is a core item for the candidate comps, and it is early enough to wait for better components.` |
| 문구 선택 조건 | `"health_status" in jev_state["game"]`이면 A2-hp/I1-hp, 아니면 -nohp. **조건은 state 키 존재 여부 하나로만 판단한다**(GameState를 다시 보지 않는다). I1의 `the current team` 구절은 `"board" in jev_state`일 때만 넣는다(I1-hp에서도 보드를 모르면 `considering the carries of candidate_comps and the player's health (game.health_status)`) |

**(b) 보유 유닛(board·bench)을 모를 때**
- "보유 유닛을 안다" = `board`와 `bench`가 **둘 다** 신뢰 가능하다(None이 아니고 필드 신뢰도 ≥ 0.6). 출처(vision/tracked/manual)는 따지지 않는다. 구매 추적·수동 입력으로 채우는 쪽(app)은 두 필드를 모두 채워야 한다(보드가 비었으면 `[]`). 하나만 알면 사본 수가 과소 계산되므로 "모른다"로 처리한다.
- 모를 때:
  - state에서 `board`, `bench`, `active_traits` 키를 뺀다. `shop[i]`에서 `copies_owned`, `buy_makes_2star` 키를 **뺀다**(false/0으로 넣지 않는다).
  - `two_star_bonus`, `three_star_bonus` 항 = 0. reason_tag `two_star`는 나오지 않는다.
  - C3 미전송(기존), U(c)=0(§2.2), `ShopAdvice.reason`에 사본 수를 쓰지 않는다.
  - S1 → **S1-noboard** 문구:
    - instructions: `How much would buying the unit in shop[{i}] ("{unit}") strengthen a typical team for the fights of the current stage (game.stage_phase), given the player's level? The player's current units are not known.`
    - 0: `A weak unit for this stage that appears in none of the candidate comps' buildup boards for the player's level.`
    - 1: `A usable filler for this stage: it could hold a slot or a trait for a few rounds, but adds little strength.`
    - 2: `A clear upgrade for this stage: a strong unit at this level, or it shares a key trait with the candidate comps' buildup boards for the player's level.`
    - 3: `One of the best pickups possible at this stage: strong on its own and a core unit of the top candidate comps' buildup boards.`
  - 선택 조건: `"board" in jev_state`이면 S1-owned, 아니면 S1-noboard.
- 알 때: `copies_owned` = board+bench의 같은 챔피언 수(1성 1, 2성 3, 3성 9로 환산한 1성 등가 개수), `buy_makes_2star` = 1성 등가 개수 % 3 == 2이고 2성 미만 사본이 있음, 3성 판정도 같은 방식(1·2코스트만 bonus).

**(c) 장착 아이템을 모를 때 (C1, I(c), items_ready)**
- 보드가 신뢰 가능하면 `UnitOnBoard.items`를 장착 아이템으로 쓴다.
- 아니면 advisor 세션이 **추적 장착분(`equipped_tracked`)** 을 유지한다: 연속된 두 planning 요청 사이에 아이템 벤치 `completed`(또는 `emblems`)에서 사라진 완성템·상징을 "장착됨"으로 기록한다. TFT에서 완성템은 팔거나 없앨 수 없으므로 벤치에서 사라진 완성템은 유닛에 들어간 것이다. 조건: 두 요청 모두 `items` 신뢰도 ≥ 0.6. 유닛 판매로 아이템이 벤치에 돌아오면(같은 ID가 다시 나타나면) 추적분에서 하나 뺀다. 재료가 사라진 경우는 추적하지 않는다(유닛 위에서 조합됐는지 알 수 없다). `game_over`/`loading`에서 비운다.
- C1 state: `resources.completed_items` / `resources.emblems`에 벤치분 + 장착분(보드 또는 `equipped_tracked`)을 **합쳐** 넣는다(장착 위치는 질문과 무관). 합친 목록이 비고 재료도 없으면 C1은 보내지 않는다(기존 규칙).
- I(c), 자원 시그니처(8.4), items_ready(5.4)도 같은 합친 목록을 쓴다.
- `items` 자체를 모르면(None/신뢰도 미만): C1·I1 미전송, I(c)=0, items_ready는 전부 `missing` + reason "아이템 미인식".

---

## 5. 합성 공식

### 5.1 공통 함수
```
norm(ans)      = ans.score / (levels - 1)                                  # 0~1
gate(ans)      = 1                        if ans.confidence >= jev.min_confidence (0.5)
               = jev.low_confidence_scale if 미만 (0.5)
avail_k        = 1 if 질문 k를 보냈고 답을 받았음, else 0
shrink(x,g,p)  = (g*x + k*p) / (g + k)        # k = shrinkage.k (200)
stat_norm(c)   = clip((comp.stat_avg_worst - adj(c)) / (comp.stat_avg_worst - comp.stat_avg_best), 0, 1)
                 # 제안 기본값 best=4.0, worst=5.2 (현재 57덱 avg 4.17~5.81 [데이터])
adj(c)         = 보유 완성템 중 item_conditional에 있는 것이 있으면
                   mean_i shrink(avg_{c|i}, games_{c|i}, prior = shrink(avg_c, games_c, shrinkage.prior_avg_place))
                 없으면 shrink(avg_c, games_c, shrinkage.prior_avg_place)
```
- 고정 구간(best/worst)으로 정규화하는 이유: 후보 집합 안의 상대 정규화는 후보가 바뀔 때마다 점수가 흔들린다. 고정 구간은 요청 간에 안정적이다.
- item_conditional은 **게임 종료 시점** 보유 기준이다(생존 편향) [stats 보고서]. 그래서 wt 안에서만 쓴다.
- `ShrinkageWeights.adjust`는 prior가 고정이다. 아이템 조건부에는 덱 평균을 prior로 써야 하므로 `adjust(x, games, prior=None)` 확장을 제안한다(10절).

### 5.2 최종 덱
```
J(c)   = Σ_{k∈{i,a,b}} w_k · avail_k · gate_k(c) · norm_k(c)          # w_i, w_a, w_b = comp.wi, wa, wb
m(c)   = Σ_{k} w_k · avail_k · gate_k(c)                                # Jev가 실제로 기여한 질량 0~1
comp_score(c) = (1 - wt)·J(c) + [wt + (1 - wt)·(1 - m(c))] · stat_norm(c)
final(c)      = min(1, comp_score(c) + comp.hysteresis_bonus · H(c))
```
- **자원이 없거나(avail=0) 확신이 낮으면(gate<1) 그 질량이 통계로 넘어간다.** "confidence 낮으면 통계 가중치를 높인다" 원칙을 수식으로 옮긴 것이다. 자원이 모두 확실하면 통계는 wt=0.2로, 타이브레이커 역할만 한다.
- 표시 컷: 1위는 항상 표시한다. 2·3위는 `final ≥ comp.show_ratio · final(1위)`일 때만 표시하고 `min(comp.max_shown, ui.max_target_comps)`(3)까지다. `comp_pick`의 `P(undecided) ≥ comp.undecided_min_p`(0.5)이면 이번 요청에서는 show_ratio 대신 `comp.show_ratio_undecided`(0.6)를 쓴다(초반에 넓게 보여준다).
- 타이브레이커: `|final(1) - final(2)| < comp.tie_eps`(0.02)이면 `comp_pick.probabilities`가 큰 쪽을 1위로 둔다.
- H(c) = 1: c가 직전 추천에 표시됐고 자원 시그니처가 바뀌지 않았을 때(8.2).

### 5.3 상점
```
ws, wp  = shop.for_stage(stage_number);  hp_bucket이 low/critical이면 ws += shop.hp_danger_shift (0.15), wp -= 같은 값 (0~1로 자름). unknown이면 미적용(§4.3)
rel(c)  = final(c) / max final          (표시된 덱 + 후보 전체, 0~1)
S_now(u)= 현재 레벨 L의 buildup 보드 전체(모든 덱)에서 유닛 u의 출현 비중을 count로 가중, 상위 유닛=1이 되게 max 정규화
          # "고수들이 이 레벨에 실제로 올리는 유닛" 프록시. 스테이지별 유닛 성적을 주는 소스는 없다 [stats 보고서]
C_path(u) = min(1, Σ_c rel(c)·μ(u,c)),  μ = shop.mu_core (1.0, final is_core) / shop.mu_final (0.7, final 기타)
            / shop.mu_next_buildup (0.5, 다음 레벨 buildup) / shop.mu_cur_buildup (0.25, 현재 레벨 buildup만) / 0   (위에서 첫 번째로 맞는 값)
now(i)  = λn·gate·norm(S1) + (1 - λn·gate)·S_now       # λn = shop.jev_share_now 0.7
path(i) = λp·gate·norm(S2) + (1 - λp·gate)·C_path      # λp = shop.jev_share_path 0.5
bonus   = shop.two_star_bonus (0.15)  구매하면 2성 완성(코드 계산)
        + shop.three_star_bonus (0.25) 3성 완성, 1~2코스트만
        보유 유닛을 모르면 bonus = 0 (§4.3 b)
shop_score(i) = clip(ws·now + wp·path + bonus, 0, 1)
buy(i)  = shop_score ≥ shop.buy_threshold, 이후 점수 순으로 골드 누적이 gold를 넘는 칸은 buy=False (코드)
reason_tag = argmax{ now_power: ws·now, final_comp: wp·path의 final 기여분, buildup: wp·path의 buildup 기여분, two_star: bonus }
```
- 특수 상품: `shop_score = gate·norm(S3) + (1-gate)·shop.special_fallback_score`, Jev 답이 없으면 `shop.special_fallback_score`(0.3). reason_tag=None, reason에 설명 요약을 넣는다.
- Jev 없이 계산한 S_now와 C_path는 폴백 값으로도 쓴다.

### 5.4 `TargetComp` · `Recommendation` 메타 필드 채우기 — 2026-09-22 추가

표시 컷(5.2)을 통과한 덱 c마다 `TargetComp` 1개를 만든다. 순서는 final(c) 내림차순이다(타이브레이커 반영 후).

| 필드 | 규칙 |
|---|---|
| `comp_id`, `name`, `carry` | `CompStats.comp_id`, `CompStats.name`(한국어), `CompStats.carry` |
| `score` | `final(c)` (0~1, 이미 min(1, ·)로 잘림) |
| `levelling` (제안 3 채택 후) | `CompStats.levelling` 그대로 |
| `owned_units` | 보유 유닛을 알면(§4.3 b): 기준 보드 B(c) 유닛 중 board ∪ bench(유닛 신뢰도 ≥ 0.6)에 있는 것. 모르면 `[]` |
| `missing_units` | 보유 유닛을 알면: B(c) 중 owned_units에 없는 것. **모르면 `[]`** (전부 "부족"으로 표시하면 오해를 준다) + reasons에 `"보드 미인식: 보유/부족 유닛은 수동 입력 시 표시"` |
| `items_ready` | 아래 규칙 |
| `next_buildup_board` | 아래 규칙 |
| `reasons` | 아래 규칙 |

- **기준 보드 B(c)**: `CompStats.final_board`의 유닛 ID를 중복 제거한 목록. 정렬: carry → 나머지 is_core → 그 외, 같은 그룹 안에서는 cost 내림차순, 그다음 ID 사전순(결정적 순서). 소환물은 stats에서 이미 빠져 있다.
- owned_units/missing_units는 **덱 c의 유닛만** 다룬다(덱과 무관한 보유 유닛은 넣지 않는다). 별 수는 따지지 않는다(1성도 owned).

**items_ready** (`CompStats.carry_bis_items`의 항목 하나당 `ItemReadiness` 하나, 목록 순서·중복 유지. 예: 대천사 2개면 2항목)
1. 자원 풀: 완성템 multiset P = 벤치 `completed` + `emblems` + 장착분(보드 `UnitOnBoard.items` 또는 `equipped_tracked`, §4.3 c). 재료 multiset Q = 벤치 `components`. 모두 신뢰도 ≥ 0.6인 ItemRef만.
2. 1차(owned): carry_bis_items를 순서대로 보며 P에 같은 ID가 남아 있으면 `owned`로 두고 P에서 하나 뺀다. 장착 위치가 carry가 아니어도 owned다(옮기기는 사용자 몫).
3. 2차(craftable): 아직 판정되지 않은 항목을 순서대로 보며 `items.json composition`의 재료 2개가 Q에 모두 남아 있으면 `craftable`로 두고 Q에서 두 재료를 뺀다(재료 중복 사용 방지, 목록 앞쪽 BIS 우선).
4. 나머지는 `missing`.
5. `holder_unit_id` = `CompStats.carry`(carry_bis_items는 carry의 아이템이다). carry가 None이면 None.
6. `items`를 모르면 모든 항목 `missing` + reasons에 `"아이템 미인식"`. carry_bis_items가 비었으면 `items_ready = []`.

**next_buildup_board**
1. 현재 레벨 L = `GameState.level`(신뢰 가능할 때). 모르면 stage로 추정한다: `L = max{lv : level_timing[lv] ≤ stage}`(stage_tuple 비교). 그것도 안 되면(stage None 또는 level_timing 비어 있음) `next_buildup_board = None`.
2. **L ≥ 10이면 None.**
3. 목표 레벨 T: `level_timing`에 L보다 큰 레벨 키가 있으면 그중 최솟값(덱의 다음 레벨업 지점, 예 Fast 8 덱이 L=6이면 level_timing이 7을 건너뛰면 8). level_timing이 비었거나 L보다 큰 키가 없으면 T = L + 1.
4. 보드 레벨 T* = `buildup` 키 중 T 이상이면서 보드가 1개 이상 있는 최솟값. 없으면 None(덱의 최종 보드 레벨에 이미 도달 → 오버레이는 owned/missing으로 안내).
5. `buildup[T*]` 중 `games` 최대인 보드(`games` None은 0 취급). 동률이면 `avg_place`가 낮은 쪽, 그래도 동률이면 원래 순서 첫 번째. `BuildupBoard` 객체를 그대로 넣는다.
- Jev state의 "현재·다음 레벨 buildup"(4.2)도 같은 선택 함수(레벨별 games 1위)를 쓴다. 단 state는 T가 아니라 L, L+1 키를 쓴다(빌드업 판단은 인접 레벨이 기준).

**reasons** (한국어 표시 문자열, 최대 5개, 아래 순서로 채우고 5개에서 자름)
1. 자원 근거(최대 2개): 항 k ∈ {item, augment, board} 중 `norm_k(c)·gate_k(c) ≥ 0.5`(Jev) 또는 폴백 프록시 I/A/U ≥ 0.5인 것을 기여도 `w_k·avail_k·gate_k·norm_k` 내림차순으로.
   - item: `"핵심 아이템: {BIS 한국어 이름, owned/craftable만, 최대 3개} → {carry 한국어}"` (해당 없으면 `"보유 아이템 적합"`)
   - augment: associated_traits 겹침이 있으면 `"특성 증강: {증강 이름} ({특성 이름})"`, 없으면 `"증강 시너지: {증강 이름}"`
   - board: `"보유 유닛 {n}/{|B(c)|}기 (핵심 {m})"`
2. 통계: `"메타 평균 {adj:.2f}등 · {games:,}판"` (adj = 5.1 shrink 값, 항상 포함)
3. 상태 플래그(해당 시): `"보드 미인식: …"`(위), `"아이템 미인식"`, `"초반: 방향 미정"`(undecided 완화 적용 시), `"직전 추천 유지"`(H(c)=1이고 bonus 없이는 이 순위가 아니었을 때), `"Jev 미사용(통계 기반)"`(jev_used=False)
- reasons 문구는 코드 상수다(표시용, Jev에 가지 않음).

**Recommendation 메타 필드**
| 필드 | 규칙 |
|---|---|
| `state_hash` | §8.3 캐시 키와 **같은 값**: `sha256(canonical_json(jev_state) + QUESTIONS_VERSION + advisor.jev_model).hexdigest()`(64자 소문자 hex). Jev를 호출하지 않은 경우(폴백·비활성·서킷 오픈)에도 보낼 예정이던 jev_state로 계산한다. jev_state를 만들 수 없는 모드(loading/combat 유지/game_over)는 직전 값 유지 또는 None |
| `latency_ms` | `engine.recommend()` 진입(GameState 수신) 시점 `time.perf_counter()`부터 Recommendation 생성 직전까지, ms float. 캐시 적중도 포함해 실측. 점진 표시(통계 먼저 → Jev 교체)면 두 결과 모두 같은 시작 시점 기준 |
| `created_at` | Recommendation 생성 시 `datetime.now(timezone.utc)`(timezone-aware UTC) |
| `jev_used` | 이 결과의 Jev 항이 실제 Jev 답(신규 호출 또는 캐시)에서 왔으면 True |
| `fallback_reason` | jev_used=False일 때만 §8.1의 9종 중 하나, True면 None |

---

## 6. 아이템 추천

1. **후보 생성(코드)**: 아이템 벤치 재료 multiset에서 서로 다른 무순서 쌍 (i ≤ j, 같은 재료 두 개는 2개 이상 보유 시)을 만든다. `items.json`에서 `composition`의 정렬 튜플 → 결과 아이템 조회표로 바꾼다. 조회표는 `DA_*`, `set_native`만 대상으로 하고, 일반 완성템·상징(뒤집개/프라이팬 조합)·전략가 아이템(망토/왕관/방패)을 포함한다 [데이터 1.6절]. 같은 결과가 여러 쌍에서 나오면 하나로 합친다.
2. **BIS 매칭**: `bis(x) = max_c rel(c)·b(x,c)`. b는 2.2절과 같고 상징은 "상징 특성 ∈ key_traits이고 +1명이면 구간 도달"일 때 1.0이다(구간 계산은 코드).
3. **통계**: 보유자 h(아래 규칙)의 `UnitItemStats.place_change`(음수가 좋음). `st(x) = clip(0.5 - place_change/item.place_change_span, 0, 1)`(span 1.0). 표본이 적으면 shrink로 0에 가깝게 당긴다.
4. **Jev**: I1의 `probabilities[x]`. `pj(x) = P(x) / max_y P(y)`(hold 포함). 상대 비교이므로 같은 질문 안에서만 정규화한다.
5. **합성**: `item_score(x) = item.w_bis·bis + item.w_jev·gate·pj + item.w_stat·st`(0.5/0.35/0.15). gate<1이면 줄어든 질량을 bis로 옮긴다.
6. **보유자(holder)**: 1위 덱 중 b(x,c) ≥ `item_fit.used_by_min`(0.7)인 유닛. 우선순위는 carry > is_core > 기타이고, 보드에 있으면 가산한다. 없으면 None("목표 덱 캐리용"으로 표시).
7. **제안 묶음**: 점수 순으로 **재료가 겹치지 않게** 탐욕적으로 고른다(최대 ⌊재료수/2⌋). `ItemSuggestion.components`에 재료 2개를 넣는다.
8. **"재료 보유"(hold)**: `hold=True` 조건(둘 중 하나)
   - Jev: `choice == "hold_components"`이고 confidence ≥ min_confidence
   - 코드(폴백 포함): `max bis < item.hold_bis_max`(0.4)이고 hp_bucket ∈ {healthy, moderate, **unknown**}이고 stage 번호 < `item.hold_until_stage`(4). hp를 모르면 moderate로 간주한다(§4.3 a)
   - hold여도 suggestions는 채운다(참고용).
   - 반대 방향 안전장치: hp_bucket == critical이면 hold를 무시한다(지금 전력이 필요하다). Jev가 hold를 골라도 무시한다. unknown이면 발동하지 않는다.
9. 완성템이 이미 벤치에 있으면 components 없는 `ItemSuggestion`(holder 추천)으로 넣는다.

---

## 7. 증강 추천

```
fit_{a,c}    = norm(A1_{a,c}) · gate
commit(s)    = augment.commit_for_stage(s)  # 표 {2: 0.3, 3: 0.6, 4: 0.9}. 스테이지가 늦을수록 현재 1위 덱에 묶인다
               조회 규칙(ShopWeights.for_stage와 동일): s 이하 키 중 최댓값의 값, 없으면 최소 키의 값
               → stage 1: 0.3(최소 키 2의 값), stage 5 이상: 0.9(키 4의 값). stage를 모르면 최소 키의 값
fit_comp(a)  = max_c [ (1 - commit) + commit·rel(c) ] · fit_{a,c}
jev_aug(a)   = augment.w_comp · fit_comp(a) + (1 - augment.w_comp) · norm(A2_a)·gate      # w_comp = 0.65
ed(a)        = tier_score(comp_augment_tiers[c*][a]) if 있음 else tier_score(augments_tiers[a]) if 있음 else augment.unlisted_score(0.5)
               c* = fit_comp(a)의 argmax 덱
aug_score(a) = w_jev·jev_aug(a) + w_editorial·ed(a)       # 0.7 / 0.3 (현재 weights.toml)
               A1·A2가 모두 gate<1이면 w_jev를 low_confidence_scale배로 줄이고 나머지를 ed로 옮긴다
pick         = argmax aug_score; 1·2위 차이가 augment.tie_eps(0.03) 미만이면 aug_pick 확률로 결정
```
- **반사실 덱 재평가**: 증강 a를 가정해 C2 값을 `(n·C2(c) + A1_{a,c}) / (n+1)`로 바꾸고(n = 보유 증강 수) 5.2절 공식을 다시 계산한다. `AugmentChoice.reasons`에 "선택 시 목표 덱: X"를 넣는다. 오버레이는 증강 화면에서 선택 후 목표 덱을 미리 보여줄 수 있다.
- 편집자 등급 해석: 덱별 등급 37개 덱은 S/A가 95%다(예 424000: S 50, A 40, B 5) [데이터]. 변별력이 낮으므로 w_editorial은 0.3 이하로 유지한다. **등급에 없는 증강은 나쁜 것이 아니라 "미평가"(0.5)** 로 처리한다. 전체 티어 라벨 S~D는 `tierList[].label`로 확인했다 [데이터].
- `associated_traits`(19개 특성 증강)와 후보 덱 key_traits가 겹치면 reasons에 "특성 증강: X 덱"을 넣는다(코드). 폴백에서는 이 겹침을 fit_{a,c}=1.0 대용으로 쓴다.
- 증강 설명 텍스트: `augments.json desc_en`. 미매핑 신규 증강 10개는 설명이 없다 [stats 4절]. 이 경우 `"description": "unknown (new augment)"`로 넣고 A1/A2 gate를 강제로 low_confidence_scale로 둔다.

---

## 8. 폴백·지연·캐시

### 8.1 폴백 (통계 전용 경로, 항상 먼저 계산)
| 항목 | Jev 항 대신 쓰는 코드 프록시 |
|---|---|
| comp item_fit | I(c) (2.2) |
| comp augment_fit | A(c) (2.2) |
| comp board_fit | U(c) (2.2) |
| shop now / path | S_now / C_path (5.3) |
| augment | fit_{a,c} = 1.0 (특성 증강 겹침) 또는 0.5, A2 = 0.5 → ed 중심 |
| item | bis + stat, hold는 코드 규칙 |
- 폴백 결과는 같은 공식에 gate=1로 넣어 계산한다(가중치 체계 하나로 유지).
- `Recommendation.jev_used=False`, `fallback_reason`은 아래 **닫힌 9종**(ASCII, 이외 값 금지) 중 하나다. 오버레이는 "Jev 미사용"을 표시한다.

| 값 | 발생 조건 (판정 순서대로 첫 번째) |
|---|---|
| `jev_disabled` | `settings.advisor.jev_enabled = false` |
| `circuit_open` | 서킷 브레이커 열림(연속 실패 후 쿨다운 중) — 호출 시도 없음 |
| `auth` | `TYPESAFE_API_KEY` 미설정, 또는 HTTP 401/403 (세션 동안 비활성) |
| `rate_limited` | 재시도 후에도 HTTP 429 |
| `overloaded` | 재시도 후에도 HTTP 529 |
| `server_error` | 그 밖의 HTTP 5xx |
| `timeout` | `TypeSafeAPITimeoutError` 또는 `advisor.jev_retry_budget_s` 초과(asyncio 취소 포함) |
| `connection` | `TypeSafeAPIConnectionError` |
| `bad_request` | HTTP 400/422 및 그 밖의 4xx, 응답 파싱 실패, 그 밖의 예상 못 한 예외(모두 코드 쪽 결함) — ERROR 로그 |

  계약 반영(§10 결정): `class FallbackReason(StrEnum)` 9종, `Recommendation.fallback_reason: FallbackReason | None = None`, validator `jev_used == (fallback_reason is None)`. `tests/test_contracts.py`의 `"jev timeout"`은 `"timeout"`으로 고친다.
- **서킷 브레이커**: 연속 3회 실패하면 60초 동안 Jev를 호출하지 않는다(`circuit_open`). 401/403은 세션 동안 비활성화한다(키 문제이므로 재시도는 무의미하다).
- 400/422(질문 형식 오류)는 코드 버그다. 폴백하고 ERROR 로그에 질문 JSON을 남긴다(키 값은 남기지 않는다).

### 8.2 지연 예산 (목표 < 2s, 호출 트리거부터 Recommendation까지)
| 단계 | 예산 |
|---|---|
| 신뢰도 필터·파생값·1차 필터·폴백 계산 | ≤ 50ms (SQLite는 앱 시작 시 메모리로 적재) |
| state/질문 구성 + 해시 | ≤ 10ms |
| Jev 호출 | 타임아웃 1.2s, `RetryPolicy(max_retries=1, backoff_initial=0.1, backoff_max=0.2, http_statuses={429,500,502,503,504,529}, respect_retry_after=True, timeout=1.5)`. 총 예산 1.5s를 넘길 재시도는 SDK가 하지 않는다 [문서] |
| 합성 | ≤ 10ms |
| 합계 최악 | ≈ 1.6s, 평상시 0.4~1.0s [추측] |
- 클라이언트는 앱 수명 동안 하나(`AsyncTypeSafeClient`)를 유지해 연결을 재사용한다.
- 이전 호출이 진행 중일 때 새 트리거가 오면 이전 결과를 버린다(최신 state만 반영).
- `usage.input_tokens`, `model`, `request_id`, 지연을 recommendation 로그에 남긴다.

### 8.3 캐시
- 키: `sha256(canonical_json(jev_state) + QUESTIONS_VERSION + settings.advisor.jev_model)`(hex). 이 값이 `Recommendation.state_hash`다(5.4). canonical_json은 키 정렬, 구분자 고정이다. **GameState가 아니라 변환된 Jev state를 해시한다.** gold 등은 원래 값이 들어가므로 라운드마다 바뀌지만, 같은 라운드 안에서 흔들리는 인식 노이즈(신뢰도 미만 필드)는 이미 걸러진 상태다.
- LRU `settings.advisor.cache_size`(64). 게임 종료 시 비운다.
- 캐시된 것은 Jev 답이다. 합성은 매번 다시 한다(가중치 변경이 즉시 반영된다).

### 8.4 히스테리시스와 자원 시그니처
- `resource_sig = hash(sorted(완성템 + 상징 + 기타 아이템(유물·찬란한 등), 장착분 포함(§4.3 c)) + sorted(보유 증강) + sorted(2성 이상 4~5코스트 보유 유닛))`.
- 재료는 시그니처에서 뺀다. 매 라운드 바뀌어서 넣으면 히스테리시스가 무력화된다. 대신 재료로 새로 조합 가능해진 **BIS**는 반영된다(I(c)가 바뀐다).
- 시그니처가 바뀐 요청에서는 H(c)=0(즉시 재평가)이고, 그 결과가 새 기준 표시 목록이 된다. 바뀌지 않으면 직전 표시 덱에 bonus를 더한다.
- 세션 상태(직전 표시 덱, 시그니처)는 advisor 인스턴스 메모리에 둔다. 앱 재시작 시 초기화된다.

---

## 9. 테스트 계획 (`tests/fixtures/states/`)

형식(파일 1개 = 상황 1개):
```json
{"description": "...", "state": {GameState JSON}, "prev": {"shown_comp_ids": [...], "resource_sig": "..."} | null,
 "stats": "tests/fixtures/stats/mini_18.json", "jev_replay": "tests/fixtures/jev/<name>.json" | null,
 "expect": {규칙}}
```
- `stats/mini_18.json`: 실제 MetaTFT 캐시에서 뽑은 덱 6~8개(Zyra Juggernaut, Lunar Aphelios, Elderwood Kha'Zix, Spellweaver Veigar, Invoker Ahri, Riftbeast Sentinel 등)의 CompStats 목록과 AugmentTier.
- `jev_replay`: Phase 3에서 한 번 실제로 호출해 기록한 답이다. pytest는 재생만 한다(네트워크 없음, 결정적). 실제 호출 평가는 별도 스크립트로 한다(qa-validator).

| # | 파일 | 상황 | 기대 규칙 |
|---|---|---|---|
| 1 | `s01_board_ap_items.json` | 3-2, Lv6, 동일 보드(Juggernaut 계열 저코스트 + 범용 유닛), 벤치 아이템: 대천사의 지팡이 + 쓸데없이 큰 지팡이 | target_comps[0] = Juggernaut Zyra 계열, items_ready에 대천사 owned |
| 2 | `s02_board_ad_items.json` | #1과 같은 보드, 아이템: 무한의 대검 + 구인수 | target_comps[0] ≠ #1의 1위, AD 캐리 덱(Lunar Aphelios 등). **#1과 #2 1위가 다르면 통과** |
| 3 | `s03_2-1_shop_early.json` | 2-1, Lv4, 아이템 없음, 상점: 강한 1~2코스트 빌드업 유닛 3 + 후보 덱의 4코스트 캐리 1 + 무관 1 | 빌드업 유닛의 shop_score > 4코스트 캐리, 상위 추천 reason_tag = now_power 또는 buildup |
| 4 | `s04_4-1_shop_late.json` | 4-1, Lv7, 목표 덱 확정 자원, 상점: 목표 덱 핵심 4코스트 + 덱과 무관한 강한 2코스트 2성 가능 아님 | 핵심 4코스트 1위, reason_tag = final_comp |
| 5 | `s05_augment_trait.json` | augment_select 3-2, 벤치에 Elderwood 유닛 3, 제시: Elderwood 특성 증강 + 수완가 + 범용 전투 증강 | pick = Elderwood 특성 증강, 반사실 1위 덱 = Elderwood 계열 |
| 6 | `s06_empty_1-4.json` | 1-4, 아이템·증강·보드 없음 | 예외 없이 target_comps 2~3개(undecided 완화), 순서는 stat_norm 순, item=None, augment=None |
| 7 | `s07a_hold_components.json` / `s07b_slam_low_hp.json` | 2-5, 재료: 뒤집개 + 쇠사슬 조끼(후보 덱 BIS 아님). a: HP 90 / b: HP 18 | a: hold=True / b: hold=False, suggestions[0] 존재 |
| 8 | `s08_jev_failure.json` | #1과 같은 state, Jev 모의 529 연속 | jev_used=False, fallback_reason="overloaded", target_comps[0]이 #1과 같은 덱(프록시만으로도 자원 논리 유지), latency < 2s |
| 9 | `s09_hysteresis.json` (연속 2스텝) | 스텝1: A 1위, B 근소 2위. 스텝2: 재료 1개만 추가(시그니처 동일) / 스텝2': 완성템 1개 추가(B 캐리 BIS) | 스텝2: A 유지 / 스텝2': B 1위로 교체 |
| 10 | `s10_low_confidence.json` | 상점 2번 칸 confidence 0.3, items confidence 0.4 | 2번 칸 S1/S2 질문 없음, ShopAdvice(slot=2, buy=False), C1 질문 없음, 아이템 없는 것으로 처리 |
| 11 | `s11_mvp_no_hp_board.json` | 3-2, Lv6, hp/board/bench/active_traits=None, 재료 2개(BIS 아님), 상점 5칸 | state에 health·board·bench·copies_owned·buy_makes_2star 키 없음, S1-noboard/I1-nohp 문구 사용, two_star 태그 없음, owned/missing `[]` + "보드 미인식" reason, hold=True 가능(unknown=moderate), next_buildup_board = 레벨 7(또는 level_timing 다음 레벨) 보드 |
| 12 | `s12_equipped_tracked.json` (연속 2스텝) | 스텝1 벤치에 대천사 / 스텝2 벤치에서 대천사 사라짐, 보드 None | 스텝2 items_ready 대천사 = owned, C1 state completed_items에 대천사 유지 |

공통 불변식(모든 fixture): `Recommendation` 검증 통과, `target_comps` ≤ 3, 모든 score ∈ [0,1], 질문 ID가 state에 없는 인덱스를 참조하지 않음, debug에 state·answers·중간값이 있음, 로그에 API 키 문자열이 없음.

---

## 10. contracts / config 변경 결정 (소유자: app-integrator, 반영 전까지 advisor는 현 계약으로 동작)

2026-09-22 결정. 아래 "채택"은 app-integrator가 그대로 구현한다. 반영 후 CONTRACT_VERSION은 0.2.0으로 올린다(필드 추가 + fallback_reason 타입 축소).

| # | 대상 | 결정 | 정확한 타입/정의 | 이유 |
|---|---|---|---|---|
| 1 | `GameState.item_offer` | **보류(이번에는 거부)** | — | 이 필드를 채울 생산자가 없다. vision MVP·Phase 3 범위에 item_select 화면 판독이 없고 메커닉도 미확인이다. vision이 판독을 설계하면 그때 `item_offer: list[ItemRef] \| None = Field(default=None, max_length=5)` + GAME_STATE_FIELDS 추가로 다시 제안한다. 그전까지 item_select 모드는 Jev를 호출하지 않는다(1.1) |
| 2 | `Recommendation.component_priority` | **채택** | `component_priority: list[ItemId] = Field(default_factory=list, max_length=10)` | 캐러셀 표시용. 계산은 코드만(목표 덱 items_ready의 missing 항목을 만드는 데 부족한 재료, rel(c) 가중 합 내림차순). 캐러셀 외 모드에서도 채워도 된다 |
| 3 | `TargetComp.levelling` | **채택** | `levelling: str \| None = None` | 오버레이 빌드업 안내(예 "Fast 8"). `CompStats.levelling` 복사 |
| 4 | `CompStats.item_usage` | **채택(타입 수정)** | `item_usage: dict[ItemId, Annotated[float, Field(ge=0)]] = Field(default_factory=dict)` — **Confidence(0~1) 금지** | 원본 pcnt는 덱당 평균 개수라 1을 넘는다(실측 최대 1.38, 35건). 상한 없음. 의미는 §2.2 |
| 5 | `CompUnit.role` | **채택** | `role: Literal["carry", "tank", "support"] \| None = None` | state의 main_carry/tanks. 파생 규칙은 stats-researcher가 명시적 기준(builds count/score, 배열 순서 금지)으로 정한다. None이면 advisor가 4.2 규칙으로 추론 |
| 6 | `UnitItemStats.comp_id` | **채택**(QA 1a) | `comp_id: str \| None = None` — None=전체 통계, 값=덱 한정 | MetaTFT `itemNames[].units[]`, `builds[]`는 덱 한정 값이다 |
| 7 | `Recommendation.fallback_reason` | **채택** | `class FallbackReason(StrEnum)`: `JEV_DISABLED="jev_disabled"`, `TIMEOUT="timeout"`, `RATE_LIMITED="rate_limited"`, `OVERLOADED="overloaded"`, `SERVER_ERROR="server_error"`, `CONNECTION="connection"`, `AUTH="auth"`, `BAD_REQUEST="bad_request"`, `CIRCUIT_OPEN="circuit_open"`; `fallback_reason: FallbackReason \| None = None`; model_validator: `jev_used is True` ⇔ `fallback_reason is None` | §8.1 닫힌 집합 |
| 8 | `ShrinkageWeights.adjust` | **채택** | §10a 끝 참조 | 아이템 조건부 통계의 prior는 덱 평균이어야 한다 |
| 9 | config 키 | **채택** | §10a 표 전체 | 5~7절 공식의 상수를 설정으로 분리 |

---

## 10a. 최종 설정 키 표 (app-integrator 구현 기준) — 2026-09-22

**이 표가 설정 키의 단일 기준이다.** 초판 §10의 키 목록과 qa `config_proposal_check.py`의 `PROPOSED_*`는 이 표로 대체한다. "기존"은 현재 config.py에 이미 있는 키(값·범위 유지, 추가 validator만 표시), "신규"는 추가할 키다. 범위는 pydantic `Field(ge/gt/le)`로 구현하고, "제약" 열은 model_validator로 구현한다. 모든 float 키는 TOML에서 정수 표기(예 `1`)도 받는다.

### weights.toml

| 섹션 | 키 | 타입 | 기본값 | 허용 범위 | 상태 | 제약 / 사용처 |
|---|---|---|---|---|---|---|
| `[comp]` | `wi` | float | 0.45 | [0, 1] | 기존(범위 추가) | **wi + wa + wb = 1** (기존 validator, 허용 오차 1e-6) · 5.2 |
| `[comp]` | `wa` | float | 0.25 | [0, 1] | 기존(범위 추가) | 〃 |
| `[comp]` | `wb` | float | 0.30 | [0, 1] | 기존(범위 추가) | 〃 |
| `[comp]` | `wt` | float | 0.2 | [0, 1] | 기존 | 5.2 |
| `[comp]` | `show_ratio` | float | 0.75 | [0, 1] | 기존 | 5.2 표시 컷 |
| `[comp]` | `max_shown` | int | 3 | [1, 3] | 기존 | 실효값 = min(max_shown, ui.max_target_comps) |
| `[comp]` | `hysteresis_bonus` | float | 0.05 | [0, 1] | 기존(상한 추가) | 5.2 |
| `[comp]` | `stat_avg_best` | float | 4.0 | [1, 8] | 신규 | **stat_avg_best < stat_avg_worst** · 5.1 stat_norm |
| `[comp]` | `stat_avg_worst` | float | 5.2 | [1, 8] | 신규 | 〃 |
| `[comp]` | `show_ratio_undecided` | float | 0.6 | [0, 1] | 신규 | **show_ratio_undecided ≤ show_ratio** · 5.2 |
| `[comp]` | `undecided_min_p` | float | 0.5 | [0, 1] | 신규 | 5.2 P(undecided) 임계 |
| `[comp]` | `tie_eps` | float | 0.02 | [0, 0.2] | 신규 | 5.2 타이브레이커 |
| `[prefilter]` | `min_games` | int | 1000 | ≥ 0 | 신규(새 섹션) | 2.1 |
| `[prefilter]` | `dedupe_jaccard` | float | 0.75 | (0, 1] | 신규 | 2.1 |
| `[prefilter]` | `w_item` | float | 0.40 | [0, 1] | 신규 | **w_item + w_aug + w_unit + w_stat = 1** (오차 1e-6) · 2.2 |
| `[prefilter]` | `w_aug` | float | 0.20 | [0, 1] | 신규 | 〃 |
| `[prefilter]` | `w_unit` | float | 0.25 | [0, 1] | 신규 | 〃 |
| `[prefilter]` | `w_stat` | float | 0.15 | [0, 1] | 신규 | 〃 |
| `[prefilter]` | `item_saturation` | float | 2.0 | > 0 | 신규 | 2.2 I(c) |
| `[prefilter]` | `unit_saturation` | float | 4.0 | > 0 | 신규 | 2.2 U(c) |
| `[prefilter]` | `craftable_factor` | float | 0.5 | [0, 1] | 신규 | 2.2 I(c) 조합 가능 완성템 계수 |
| `[prefilter]` | `aug_neutral` | float | 0.5 | [0, 1] | 신규 | 2.2 A(c) 증강 없음/미평가 |
| `[prefilter]` | `unit_w_core` | float | 1.0 | [0, 1] | 신규 | **unit_w_core ≥ unit_w_final ≥ unit_w_buildup** · 2.2 w_u |
| `[prefilter]` | `unit_w_final` | float | 0.6 | [0, 1] | 신규 | 〃 |
| `[prefilter]` | `unit_w_buildup` | float | 0.3 | [0, 1] | 신규 | 〃 |
| `[prefilter]` | `unit_star_mult` | float | 1.5 | [1, 3] | 신규 | 2.2 2성 이상 배수 |
| `[prefilter]` | `stat_quota` | int | 2 | [0, 8] | 신규 | 2.3 S 상위 쿼터(코드에서 min(·, max_candidate_comps)) |
| `[item_fit]` | `carry_bis` | float | 1.0 | [0, 1] | 신규(새 섹션) | **carry_bis ≥ core_unit ≥ usage** · 2.2 b(x,c), 6.2, I1 |
| `[item_fit]` | `core_unit` | float | 0.7 | [0, 1] | 신규 | 〃 |
| `[item_fit]` | `usage` | float | 0.4 | [0, 1] | 신규 | 〃 |
| `[item_fit]` | `emblem_key_trait` | float | 1.0 | [0, 1] | 신규 | 상징 특성 ∈ key_traits |
| `[item_fit]` | `emblem_other` | float | 0.2 | [0, 1] | 신규 | **emblem_other ≤ emblem_key_trait** |
| `[item_fit]` | `usage_min_pcnt` | float | 0.3 | [0, 3] | 신규 | 단위 = 덱당 평균 개수(pcnt), 비율 아님 · 2.2 |
| `[item_fit]` | `used_by_min` | float | 0.7 | [0, 1] | 신규 | I1 used_by, 6.6 holder 임계 |
| `[shop]` | `buy_threshold` | float | 0.5 | [0, 1] | 기존 | 5.3 |
| `[shop]` | `stage_weights` | dict[int, {ws, wp}] | {1:.8/.2, 2:.7/.3, 3:.5/.5, 4:.3/.7, 5:.2/.8} | ws, wp ∈ [0, 1] | 기존 | **ws + wp = 1** (기존) · `for_stage` |
| `[shop]` | `jev_share_now` | float | 0.7 | [0, 1] | 신규 | 5.3 λn |
| `[shop]` | `jev_share_path` | float | 0.5 | [0, 1] | 신규 | 5.3 λp |
| `[shop]` | `two_star_bonus` | float | 0.15 | [0, 1] | 신규 | 5.3 (보유 유닛 모르면 미적용) |
| `[shop]` | `three_star_bonus` | float | 0.25 | [0, 1] | 신규 | 5.3 |
| `[shop]` | `hp_danger_shift` | float | 0.15 | [0, 0.5] | 신규 | 5.3 (hp unknown이면 미적용) |
| `[shop]` | `mu_core` | float | 1.0 | [0, 1] | 신규 | **mu_core ≥ mu_final ≥ mu_next_buildup ≥ mu_cur_buildup** · 5.3 μ |
| `[shop]` | `mu_final` | float | 0.7 | [0, 1] | 신규 | 〃 |
| `[shop]` | `mu_next_buildup` | float | 0.5 | [0, 1] | 신규 | 〃 |
| `[shop]` | `mu_cur_buildup` | float | 0.25 | [0, 1] | 신규 | 〃 |
| `[shop]` | `special_fallback_score` | float | 0.3 | [0, 1] | 신규 | 5.3 특수 상품 폴백 |
| `[shrinkage]` | `k` | float | 200 | ≥ 0 | 기존 | 5.1, 6.3 |
| `[shrinkage]` | `prior_avg_place` | float | 4.5 | [1, 8] | 기존 | adjust 기본 prior |
| `[jev]` | `min_confidence` | float | 0.5 | [0, 1] | 기존 | 5.1 gate |
| `[jev]` | `low_confidence_scale` | float | 0.5 | [0, 1] | 기존 | 5.1 gate |
| `[augment]` | `w_jev` | float | 0.7 | [0, 1] | 기존(범위 추가) | **w_jev + w_editorial = 1** (신규 validator) · 7 |
| `[augment]` | `w_editorial` | float | 0.3 | [0, 1] | 기존(범위 추가) | 〃 |
| `[augment]` | `editorial_tier_score` | dict[S\|A\|B\|C\|D, float] | S 1.0, A 0.75, B 0.5, C 0.25, D 0.0 | 값 [0, 1] | 기존(값 범위 추가) | 7, 2.2 tier_score. 누락 등급 키는 기본값으로 채운다 |
| `[augment]` | `w_comp` | float | 0.65 | [0, 1] | 신규 | 7 jev_aug |
| `[augment]` | `unlisted_score` | float | 0.5 | [0, 1] | 신규 | 7 ed 미평가 |
| `[augment]` | `tie_eps` | float | 0.03 | [0, 0.2] | 신규 | 7 |
| `[augment]` | `commit_by_stage` | dict[int, float] | {2: 0.3, 3: 0.6, 4: 0.9} | 키 int ≥ 1, 값 [0, 1], **비어 있으면 안 됨** | 신규 | 메서드 `commit_for_stage(stage_number: int \| None) -> float`: s 이하 키 중 최댓값의 값, 없거나 s=None이면 최소 키의 값(stage 1→0.3, 5+→0.9). TOML 키는 문자열 `"2"` → int 변환(stage_weights와 같음) |
| `[item]` | `w_bis` | float | 0.5 | [0, 1] | 신규(새 섹션) | **w_bis + w_jev + w_stat = 1** (오차 1e-6) · 6.5 |
| `[item]` | `w_jev` | float | 0.35 | [0, 1] | 신규 | 〃 |
| `[item]` | `w_stat` | float | 0.15 | [0, 1] | 신규 | 〃 |
| `[item]` | `place_change_span` | float | 1.0 | > 0 | 신규 | 6.3 |
| `[item]` | `hold_bis_max` | float | 0.4 | [0, 1] | 신규 | 6.8 |
| `[item]` | `hold_until_stage` | int | 4 | [1, 10] | 신규 | 6.8 (stage 번호 < 이 값일 때만 hold) |

weights 신규 키 합계: comp 5 + prefilter 15 + item_fit 7 + shop 10 + augment 4 + item 6 = **47개**(새 섹션 3개: `prefilter`, `item_fit`, `item`). 새 pydantic 모델: `PrefilterWeights`, `ItemFitWeights`, `ItemWeights`, `Weights`에 `prefilter`, `item_fit`, `item` 필드 추가.

### settings.toml

| 섹션 | 키 | 타입 | 기본값 | 허용 범위 | 상태 | 제약 / 사용처 |
|---|---|---|---|---|---|---|
| `[advisor]` | `jev_enabled` | bool | true | — | 기존 | false → fallback `jev_disabled` |
| `[advisor]` | `timeout_s` | float | 2.0 | > 0 | 기존 | **추천 1회 전체 예산**으로 해석 |
| `[advisor]` | `max_candidate_comps` | int | 8 | [1, 20] | 기존(상한 추가) | 2.3 N |
| `[advisor]` | `cache_size` | int | 64 | ≥ 0 | 기존 | 8.3 LRU (0 = 캐시 끔) |
| `[advisor]` | `jev_model` | str | `"jev-latest"` | 패턴 `^jev-(latest\|\d+\.\d+\.\d+)$` | 신규 | SDK 호출 `model`, 캐시 키·state_hash에 포함. 튜닝 후 `"jev-1.13.0"` 고정 가능 |
| `[advisor]` | `jev_timeout_s` | float | 1.2 | > 0 | 신규 | 시도 1회 타임아웃. **jev_timeout_s ≤ jev_retry_budget_s < timeout_s** |
| `[advisor]` | `jev_retry_budget_s` | float | 1.5 | > 0 | 신규 | RetryPolicy(timeout=) 총 예산 |
| `[advisor]` | `jev_max_retries` | int | 1 | [0, 3] | 신규 | RetryPolicy(max_retries=) |
| `[advisor]` | `circuit_fail_threshold` | int | 3 | ≥ 1 | 신규 | 8.1 서킷 브레이커 |
| `[advisor]` | `circuit_cooldown_s` | float | 60 | ≥ 0 | 신규 | 8.1 |
| `[vision]` | `state_min_confidence` | float | 0.6 | [0, 1] | 기존 | advisor도 4.1·4.3 신뢰 판정에 이 값을 쓴다(별도 키 만들지 않음) |
| `[ui]` | `max_target_comps` | int | 3 | [1, 3] | 기존 | 5.2 실효 표시 수 |

settings 신규 키 합계: **6개**. 총 신규 53개(weights 47 + settings 6).

### 설정 키로 만들지 않는 상수 (코드 상수, 결정)
- 구간 라벨 경계·문장(health/gold/shop_odds/stage_phase), 질문·레벨 문구, reasons 문구 → 질문 입력/표시 문자열이라 `QUESTIONS_VERSION`과 함께 관리.
- RetryPolicy `backoff_initial=0.1`, `backoff_max=0.2`, `http_statuses={429,500,502,503,504,529}`, `respect_retry_after=True` → jev_client 상수.
- st(x)의 중심 0.5, norm의 `levels-1`, 1.0 상한 clip → 수학적 정의.

### `ShrinkageWeights.adjust` 확장 의미
```python
def adjust(self, x: float, games: int | None, prior: float | None = None) -> float:
    """(g*x + k*p)/(g + k).  g = games or 0,  p = self.prior_avg_place if prior is None else prior.
    g + k == 0 이면 x 반환. prior 범위 검증은 하지 않는다(avg_place가 아닌 값에도 쓴다)."""
```
- `prior=None` → 기존 동작과 동일(하위 호환).
- 사용처: (a) 덱 평균 `adjust(avg_c, games_c)`, (b) 아이템 조건부 `adjust(avg_{c|i}, games_{c|i}, prior=adjust(avg_c, games_c))`(5.1), (c) 아이템 통계 `adjust(place_change, games, prior=0.0)`(6.3, 0 쪽으로 수축) — (c) 때문에 prior에 [1,8] 검증을 넣지 않는다.
- games=None 또는 0 → 결과 = p(k>0일 때).

**stats-researcher에게 요청할 통계 필드**
1. 57개 전 클러스터의 `comp_details`(현재 424001 하나만 캐시됨 [데이터]): `builds`(→ carry, carry_bis_items, CompUnit.items/role), `itemNames`(→ item_conditional), `early_options` 4/5/6 + `options` 7~10(→ buildup), `levels`(→ level_timing).
2. `build_items[].pcnt` → 제안 4 `item_usage`.
3. `comp_augment_tiers` 키(클러스터 ID) → `comp_id` 매핑. 37/57만 존재하고 `distance` 필드가 있다(가이드 덱과 클러스터의 거리로 보임). distance가 큰 매칭은 버리는 기준을 정해 달라.
4. `UnitItemStats`(유닛 + 아이템 1개 place_change): MetaTFT `itemNames[].units[].place_change`로 덱 한정 값을 얻을 수 있다.
5. 상점 확률 5, 7~10레벨(현재 3/4/6만 있음). shop_odds 라벨 폴백과 S_now 해석에 필요하다.

---

## 11. 미해결 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| Jev가 Set 18 유닛·특성·아이템 효과를 모를 수 있다(세트 출시가 모델 학습 이후일 가능성) [추측] | 아이템 대체 적합성, 증강-덱 시너지 판단이 약해진다 | state에 특성·역할·BIS 목록을 명시해 "지식" 대신 "비교"로 풀게 했다. Phase 3에서 fixture 1·2·5의 confidence와 정답률을 측정한다. 낮으면 wi/wa를 줄이고 코드 프록시 비중을 높인다 |
| MVP에서 보드·벤치·장착 아이템 인식 불가 [vision 보고서] | C3가 빠지고, 장착 완성템이 자원에서 누락되어 덱 판단이 틀어진다 | C3는 조건부 질문이고 질량은 통계로 넘어간다. 장착 아이템은 advisor 세션의 `equipped_tracked`(벤치에서 사라진 완성템 = 장착)로 보완한다(§4.3 c). hp·보유 유닛 결측 규칙도 §4.3 |
| 편집자 증강 등급의 변별력 부족(S/A 95%) | 증강 추천이 사실상 Jev 단독 | w_editorial ≤ 0.3 유지. 등급 없음 = 중립. 통계가 다시 공개되면 source_kind="stats" 경로로 전환 |
| 통계는 게임 종료 시점 기준(생존 편향) | item_conditional, buildup avg가 "지금 이 보드가 강한가"를 직접 뜻하지 않는다 | wt 안에서만 쓰고, S_now는 avg가 아니라 출현 빈도를 쓴다 |
| 레벨 문구·가중치가 모두 초기 추측값 | 추천 품질 미검증 | debug에 원시 답을 저장해 가중치만 바꿔 재계산한다. qa-validator fixture 평가 후 튜닝 |
| state 크기(N=8) 때문에 정확도가 떨어질 수 있음(context rot) [문서] | 덱별 질문의 판단 흐림 | 덱 표현을 최소화했다(buildup 2개 레벨, 최종 보드 1안). 필요하면 N=6으로 줄이거나 덱별로 요청을 나눠 병렬 호출(asyncio.gather). 후자는 비용이 늘고 지연은 거의 같다 |
| 지연 실측 없음 | 2s 목표 불확실 | Phase 3 첫 작업: 대표 state 1회 호출로 latency·usage 측정(키는 환경변수, 로그에 남기지 않음) |
| rate limit 동적 조정 중 [문서] | 429 증가 | 호출은 상태 변화 시에만(라운드당 1~3회). 재시도 1회 후 폴백, 서킷 브레이커 |
| `jev-latest` 별칭 이동 시 답 분포 변화 [문서] | confidence 임계값 등 튜닝값 무효화 | `settings.advisor.jev_model`(§10a)로 튜닝 후 `jev-1.13.0` 고정. 응답 `model`을 로그에 남긴다 |
| 신규 증강 10개 설명 없음, desc `?` 193개 | 해당 증강 판단 약화 | 7절 gate 강제 축소 + OP.GG 증강 설명으로 보완 요청(stats) |
| 특수 상품 가격 정보 없음 | 특수 상품 구매 추천이 골드를 고려하지 못함 | 표시만 하고 buy는 점수 임계값만 적용. 가격표는 수동 입력 또는 OCR 요청 |
| TYPESAFE_LOG_LEVEL=debug는 본문을 기록 | 게임 상태 로그 유출(키는 가려짐) | 기본 info, 문서화. 키는 코드에서 절대 읽어 쓰지 않고 SDK가 환경변수에서 읽게 둔다 |

---

## 부록: 구현 모듈 배치 제안 (`src/tft_advisor/advisor/`)
- `features.py` — 신뢰도 필터, 조합표, 사본 수, 구간 라벨, b/μ/S_now/C_path(코드 전용, 순수 함수)
- `candidates.py` — 1차 필터, 중복 제거, 상위 N
- `jev_state.py` — GameState → 영어 state, ID ↔ 이름 사전
- `questions.py` — 질문 템플릿(QUESTIONS_VERSION), 모드별 묶음
- `jev_client.py` — AsyncTypeSafeClient 래퍼, RetryPolicy, 서킷 브레이커, 캐시, 예외 → fallback_reason
- `compose.py` — 5~7절 공식, 히스테리시스, 컷 → Recommendation(+debug)
- `engine.py` — `async recommend(state, stats, prev) -> Recommendation`
