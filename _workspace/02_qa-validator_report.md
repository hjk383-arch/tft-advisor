# 02 qa-validator: Phase 2 경계면 교차 검증

작성일: 2026-09-21 / 작성자: qa-validator / 대상: CONTRACT_VERSION 0.1.0, 02_jev-strategist_design.md, 01_stats-researcher_sources.md, 01_vision-engineer_layout.md
직접 수정: **소스 코드 수정 없음.** 추가한 파일만 있다: `tests/test_boundaries.py`, `_workspace/qa_scripts/{id_crosscheck,config_proposal_check,jev_request_size}.py`

## 요약: PASS 4 / FAIL 4

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | B가 읽고 쓰는 필드 ↔ A contracts | **FAIL** (제안 누락) | 현재 B가 읽는 GameState/ShopSlot/UnitOnBoard/ItemState/AugmentRef/ActiveTrait/CompStats/AugmentTier 필드는 모두 존재한다. B 10절 제안 7건도 실제로 계약에 없는 것과 정확히 일치한다. **누락된 제안**: (a) `UnitItemStats`에 덱 범위가 없다(contracts.py:365-370). B 6절 3번과 10절 요청 4는 MetaTFT `itemNames[].units[]`, `builds[]`(덱 한정 값)를 쓴다 → `comp_id: str \| None` 필요. (b) B 11절 "`jev-1.13.0` 고정 옵션을 settings에 둔다"에 해당하는 키 제안이 10절에 없다. (c) 제안 4 `item_usage`의 원본 `build_items[].pcnt`는 **최대 1.38이고 1 초과가 35건**이다(덱당 평균 개수라서). 타입을 `Confidence`(0~1)로 두면 실제 데이터가 거부된다. 그리고 B 2.2의 임계값 0.3은 의미를 다시 정해야 한다 | app-integrator, jev-strategist | 아래 담당자별 목록 |
| 2 | B 공식 파라미터 ↔ weights.toml ↔ config.py | **FAIL** | `config_proposal_check.py`: 제안 키를 합치면 **20건이 거부된다(extra_forbidden)**. 풀어 쓰면 기존 섹션 leaf 13개(comp 4, shop 5, augment 4), 새 테이블 `[prefilter]`(8키)와 `[item]`(6키), settings `[advisor]` 5키로 **총 32개 키**다. 기존 키(comp.wi/wa/wb/wt/show_ratio/max_shown/hysteresis_bonus, shop.buy_threshold/stage_weights, shrinkage.*, jev.*, augment.w_jev/w_editorial/editorial_tier_score)는 B와 이름·의미가 모두 일치한다. 추가 사항: `ShrinkageWeights.adjust(x, games)`(config.py:140)에 prior 인자가 없다(B 제안 6). `augment.commit_by_stage`는 2/3/4만 있고 1·5+ 조회 규칙이 없다. B 공식 안에 설정으로 빼지 않은 상수가 있다: b(x,c) 1.0/0.7/0.4/0.2, 사용률 임계 0.3, w_u 1.0/0.6/0.3·×1.5, μ 1.0/0.7/0.5/0.25, `P(undecided)≥0.5`, 특수 상품 폴백 0.3, 쿼터 "S 상위 2" | app-integrator(반영), jev-strategist(상수 목록 확정) | 32키 + `prefilter`/`item` 합=1 validator + `adjust(prior=)` + `jev_model` |
| 3 | A 통계 모델 ↔ MetaTFT 원본 필드 | **PASS** (조건부) | `test_comp_details_convertible_to_compstats`가 424001 원본으로 CompStats를 생성·왕복하는 데 성공했다. AugmentTier 258+2,903행, UnitStats 69행, UnitItemStats 변환도 성공했다. 매핑 표는 아래 3절. **소스에 없는데 계약이 받는 필드**: CompStats/BuildupBoard `top4`(소스 전무), `options`(7~10레벨) `win_rate`, `CompUnit.is_core`/`role`(파생 규칙 미정). **변환 함정**: 특성 `_N`은 인원이 아니라 구간 번호다(→ breakpoints[N-1]). `levels[0]`은 stage·round가 빈 문자열이다. `early_options.level`은 float(4.03)다. `itemNames` 137행 중 11행에 `units` 키가 없다. rank_filter 문자열 순서가 다르다(`CHALLENGER,GRANDMASTER,MASTER,DIAMOND` vs settings `CHALLENGER,DIAMOND,GRANDMASTER,MASTER`). **데이터 결함**: `traits.json`의 `DA_18_Eclipse` breakpoints가 `[None]`이고 덱 1개에 등장한다. comp_details는 **57개 중 1개만 캐시되어 있다** | stats-researcher | 3절 |
| 4 | ID 체계 전수 대조 | **PASS** | `id_crosscheck.py`: 정적 5종 ID는 모두 `_ID_PATTERN`을 통과한다. 상점 풀 name_ko/name_en 중복은 0이다. MetaTFT 유닛 69/65/68/65(units/comps/details/options), 특성 35/36, 아이템 49/137, 증강 258/173 중 미매핑은 전부 unmapped.json 선언분이다. **unmapped.json 선언과 실제 관측이 양방향 모두 차이 0이다.** fixture 7개의 상점 챔피언 23칸 + 특수 1칸 + 빈칸 1 + 증강 3개가 모두 ID로 해석된다. 챔피언 23칸의 정적 cost가 라벨 cost와 모두 일치하고, 모두 MetaTFT에 등장한다. 아칼리는 `DA_18_Akali_AD`(풀에 유일), 바위 게는 `DA_Scuttlecrab18`로 해석된다. 참고로 `DA_Warpath`, `DA_Hustler`는 덱별 등급에 없고 전체 등급에만 있다(B 7절 폴백 경로를 탄다) | - | - |
| 5 | D 인식 필드(MVP) ↔ GameState ↔ B 입력 | **FAIL** | D MVP(layout.md:81)가 다루는 필드는 stage/gold/level/xp/shop/augment_offer/augments_owned(tracked)/items/screen_mode이고, 모두 GameState에 있다. board/bench/active_traits=None은 B가 C3를 빼고 질량을 통계로 넘기는 방식으로 폴백을 정의했다(design 1.1, 5.2). **폴백이 없는 것**: (a) `hp`는 D에서 Phase 3 항목인데, B는 health_status를 hold 규칙(6절 8번), critical 안전장치, `hp_danger_shift`(5.3), I1/A2 문구에 쓴다. hp=None일 때의 동작이 정의되지 않았다(현재 규칙대로면 hold가 절대 True가 되지 않는다). (b) bench/board=None이면 `copies_owned`/`buy_makes_2star`, two/three_star_bonus를 계산할 수 없다. 이때의 규칙이 없다. (c) 장착 아이템은 "추적 요청"만 있고 누락 시 C1/I(c) 동작이 정의되지 않았다. **fixture ↔ D 불일치**: 정답 파일에는 hp/streak/shop_odds가 있지만 D MVP 범위에는 없다. 반대로 D MVP에 있는 items/augments_owned는 정답 파일 7개 중 **어디에도 라벨이 없어** 정확도를 잴 수 없다. streak 부호(+/-)가 미확정이다. `2-4 아이템선택`은 `carousel`로 라벨되어 있다. 2-1과 3-3의 level은 "가려져 추정"한 값이다(note_level) | jev-strategist, vision-engineer | 4절 |
| 6 | B 출력 ↔ A Recommendation ↔ 오버레이 | **FAIL** | 계약 필드는 오버레이 요구(이름 `TargetComp.name`, 적합도 `score`, 보유/부족 `owned_units`/`missing_units`, 아이템 `items_ready`, 다음 보드 `next_buildup_board`)를 모두 갖췄다. B의 점수는 모두 [0,1]로 clip/min 처리된다. **그러나 B 설계에는 `owned_units`, `missing_units`, `items_ready`(owned/craftable/missing 판정, holder), `next_buildup_board`(level_timing으로 어느 레벨 보드를 고르는지), `TargetComp.reasons`, `state_hash`/`latency_ms`/`created_at`을 어떻게 채우는지가 전혀 없다**(design 전체 grep 0건). MVP(보드 None)에서 owned_units가 무엇이 되는지도 정해지지 않았다. `fallback_reason`은 계약상 자유 문자열인데, B는 ASCII 9종을 정의했고 test_contracts.py:179는 `"jev timeout"`을 쓴다. 불일치다 | jev-strategist, app-integrator | 5절 |
| 7 | 요청 크기 추정 재현 | **PASS** | `jev_request_size.py`(Jev 호출 없음, 토큰 = 문자/4~/3): fixture 그대로면 planning 9~11문항, 약 3.8~5.7k tok이다. 설계 가정(재료 4개 + 증강 1개)을 넣으면 planning **26~28문항, 7.1~10.1k tok**(설계 28문항, 7~8k)이다. augment_select는 **46문항, 10.5~14.0k tok**(설계 53~54, ≈12k)이다. 설계 53~54는 C3 8개를 포함한 수치이고, MVP에서는 46이 맞다. state 단독 1.9~3.1k tok(설계 2.5~3.5k), state+최장 질문 ≤3.6k tok으로 한도 32k보다 훨씬 작다. 추정 범위가 일치한다 | - | (선택) 3.2절 표에 "MVP augment_select = 46" 정정 |
| 8 | pytest 전체 | **PASS** | `.venv\Scripts\python -m pytest` → **29 passed, 3 xfailed**(0.36s). 14개는 기존, 15개는 신규 경계면 테스트다. xfail(strict) 3건은 위 FAIL이 해결되면 XPASS가 되어 테스트가 실패하므로, 그때 마커를 지워야 한다: Eclipse null, UnitItemStats.comp_id, config 32키. `typesafe_sdk`는 미설치이고, Phase 2에서는 필요 없다(`pip install -e ".[advisor]"`) | - | - |

---

## 3. MetaTFT 원본 ↔ 계약 필드 매핑 (캐시 직접 확인)

| 계약 필드 | 원본 | 상태 |
|---|---|---|
| CompStats.avg_place / games | comps_data `overall.avg/count` | OK |
| CompStats.top4 / win_rate | 없음(comps_data, comp_details `placements`에 count/avg만 있음) | 항상 None |
| source_cluster_id / name_en / levelling | `Cluster` / `name[]`(trait/unit ID 조합) / `levelling`(Fast 8, Fast 9, lvl 5~7, Standard) | OK. name은 ID → 한국어 조합 규칙을 stats가 정해야 한다 |
| final_board | `units_string`(7~9기) + `builds`(유닛별 3아이템) | OK. `is_core`, `star`(unit_stats tiers pcnt) 파생 규칙이 미정이다 |
| carry / carry_bis_items | `builds[]` count 또는 score 1위 | 파생 규칙이 미정이다. 424001의 `builds[0]`은 comps_data에서는 Aphelios(count 7,236, score 0.46), comp_details에서는 Nidalee(count 17,807, score 0.51)다. 배열 순서로 carry를 정하면 안 되고, count·score 같은 명시적인 기준이 필요하다 |
| key_traits(TraitReq.count) | `traits_string` `_N` = 구간 번호 | breakpoints[N-1]로 변환해야 한다. Eclipse는 None |
| buildup | `early_options{4,5,6}`(unit_list, avg, count, win, level float), `options{7..10}`(units_list, avg, count, score) | OK. 키 이름이 서로 다르다(`unit_list` vs `units_list`). 소환물 제외 |
| level_timing | `levels[]{stage, round, level}` | 빈 stage 행은 건너뛴다 |
| item_conditional | `itemNames[]{count, avg}` | OK(Hullcrusher 1건 미매핑) |
| (제안) item_usage | `build_items[].pcnt` | 1 초과 값이 있다 → float ≥0 |
| AugmentTier | `augments_tiers.tierList[].label/content[].id`(S27/A87/B123/C21/D0), `comp_augment_tiers{cid}.augments[]{id,tier}`(S/A/B만), `source_title`, `distance` | OK. cid → comp_id 매핑과 distance 기준은 stats가 정한다 |
| UnitStats | `units.json results[]{unit, places[8]}` | OK(파생 계산) |
| UnitItemStats | `itemNames[].units[]{units, count, avg, place_change}`, `builds[]{unit, buildName, place_change}` — **덱 한정** | comp_id 필드가 없다 |
| Provenance.patch/rank_filter/fetched_at | `patch.json`(18.2 + b), `filter_adjustment.rank_filter`, `updated`(epoch ms) | rank_filter는 집합으로 비교한다 |

---

## 4. 담당 에이전트별 수정 요청

### app-integrator
1. **[필수]** `config.py`에 B 10절 키 32개를 반영한다. `CompWeights` +4, `ShopWeights` +5, `AugmentWeights` +4(`commit_by_stage: dict[int,float]`), 새 `PrefilterWeights`(8), 새 `ItemWeights`(6), `AdvisorCfg` +5. 추가로 `advisor.jev_model = "jev-latest"`도 넣는다. prefilter.w_* 합=1, item.w_* 합=1 validator를 추가하고, weights.toml·settings.toml에 값을 기록한다. 검증: `_workspace/qa_scripts/config_proposal_check.py` → `rejected keys: 0`. 그 뒤 `tests/test_boundaries.py::test_config_accepts_design_keys`의 xfail을 제거한다.
2. **[필수]** `ShrinkageWeights.adjust(x, games, prior=None)`(config.py:140).
3. **[필수]** `UnitItemStats.comp_id: str | None = None`(contracts.py:365). None이면 전체 통계, 값이 있으면 덱 한정. 반영 후 xfail 제거.
4. **[필수]** B 제안 1~5 채택 여부를 결정한다. `CompStats.item_usage`를 받는다면 `dict[ItemId, Annotated[float, Field(ge=0)]]`로 한다(Confidence 금지, 실측 최대 1.38).
5. [진행 중 가능] `Recommendation.fallback_reason`을 B 8.1의 9종 StrEnum/Literal로 바꾼다. test_contracts.py:179의 `"jev timeout"`을 `"timeout"`으로 정정한다.
6. [진행 중 가능] rank_filter 비교는 집합으로 한다(`frozenset(s.split(","))`). 저장 시 정렬·정규화 헬퍼를 둔다.

### jev-strategist
1. **[필수]** `TargetComp` 채우기 규칙을 설계에 추가한다: owned_units/missing_units(보드 None이면 tracked/manual 유닛, 없으면 빈 목록과 "보드 미인식" reason), items_ready(carry_bis_items 각각 owned/craftable/missing 판정, holder), next_buildup_board(현재 level+1 보드의 count 1위, level 10 이상이면 None), reasons, state_hash(8.3 캐시 키 재사용), latency_ms, created_at.
2. **[필수]** hp=None(MVP)일 때 규칙: health_status 키를 생략하고, hold 규칙에서 hp 조건을 어떻게 처리할지(예: "unknown은 moderate로 간주"), hp_danger_shift 미적용, I1/A2 문구 조건을 정한다.
3. **[필수]** bench/board=None일 때 규칙: copies_owned/buy_makes_2star 키를 생략하고, two/three_star_bonus=0으로 둔다. S1 레벨 2 문구가 없는 키를 참조하지 않게 한다. 장착 아이템 미인식 시 C1/I(c) 처리도 정한다.
4. **[필수]** 공식 내부 상수(항목 2 목록) 중 튜닝 대상을 weights 키로 올릴지 확정해 app-integrator에 전달한다. `jev_model` 설정 키를 제안한다. `commit_by_stage`의 1·5+ 조회 규칙을 정한다.
5. [진행 중 가능] `item_usage ≥ 0.3` 임계값을 pcnt(덱당 평균 개수) 기준으로 다시 정의한다.
6. [진행 중 가능] 3.2절 표를 정정한다: MVP augment_select는 46문항(C3 제외). 2.1절의 "694~884판 4개"는 실제로 548/694/751/884다.
7. [진행 중 가능] 특수 상품 S3 문구는 영어 설명이 필요하다. `shop_specials.json`에는 `desc_en`이 없다(아래 stats 3). `ShopSlot.cost`(fixture 3-3 특수 상품 9골드)가 계약에 있으므로, vision이 가격을 읽으면 buy 골드 누적에 쓸 수 있다.

### stats-researcher
1. **[필수]** comp_details를 **57개 클러스터 전부** 수집한다(현재 424001 하나). buildup, level_timing, item_conditional, carry/BIS가 전부 여기에 의존한다. B 10절 요청 1~5도 함께 처리한다.
2. **[필수]** `traits.json`의 `DA_18_Eclipse` breakpoints `[None]`를 고친다(CDragon effects 확인). 반영 후 `test_trait_breakpoints_have_no_null` xfail을 제거한다.
3. **[필수]** 수집기 변환 규칙을 확정한다: `_N` → breakpoints[N-1], `levels`의 빈 stage 행 건너뛰기, `itemNames[].units` 누락 처리, `unit_list`/`units_list` 키 차이, early `level` float → 레벨 키 int, `is_core`·`role`·`carry` 파생 기준(builds 배열 순서가 아니라 count 또는 score 같은 명시적 기준), comp_augment_tiers `distance` 컷오프, cluster → comp_id 매핑.
4. [진행 중 가능] `shop_specials.json`에 `desc_en`을 추가한다(B state가 영어라서).
5. [진행 중 가능] 미매핑 증강 10개의 설명을 OP.GG로 보완한다(최소 5개는 OP.GG에 있음).

### vision-engineer
1. **[필수, vision 정확도 측정 전]** 현재 fixture 7장은 **방송 수동 크롭(1986~2001 × 1117~1126)** 이다. 1920x1080 ROI 좌표를 검증하거나 픽셀 정확도를 잴 수 없다. 사용자에게 원본 1920x1080 전체 캡처(layout.md 6절 목록)를 요청한다. 01_layout.md는 아직 "스크린샷 없이 작성한 초안" 상태다.
2. **[필수]** MVP 범위와 정답 필드를 맞춘다. hp/streak/shop_odds를 MVP에 넣을지 결정한다(fixture에는 라벨이 있다). items와 augments_owned 라벨을 정답 파일에 추가한다(현재 0건이라 아이템 ≥90% 목표를 잴 수 없다).
3. [진행 중 가능] streak 부호(+연승/-연패)를 사용자에게 확인한다. `2-4 아이템선택`이 carousel인지 item_select인지 확인한다. 추정 라벨(2-1과 3-3의 level)은 정확도 비교에서 뺄 수 있게 `_uncertain` 같은 메모 키로 표시한다.

---

## 5. Phase 3 진입 전 반드시 해결 vs 진행하며 해결

**진입 전 필수(advisor 구현 차단 요인)**
- app-integrator 1~4: config 32키 + adjust(prior), UnitItemStats.comp_id, 제안 1~5 결정(item_usage 타입)
- jev-strategist 1~4: TargetComp 채우기 규칙, hp/board None 폴백, 상수·jev_model 키 확정
- stats-researcher 1~3: comp_details 57개, Eclipse, 변환 규칙

**vision 정확도 측정 전 필수(구현 착수는 가능)**
- vision-engineer 1~2: 원본 1920x1080 캡처, 정답 필드 정렬(items/augments_owned 라벨)

**진행하며 해결**
- fallback_reason enum, rank_filter 집합 비교, item_usage 임계값 재정의, 문서 수치 정정, shop_specials desc_en, 미매핑 증강 설명, streak 부호, 2-4 화면 상태 라벨

---

## 6. 재실행 방법
```
.venv\Scripts\python -m pytest -rxX                         # 29 passed, 3 xfailed (기대)
.venv\Scripts\python _workspace\qa_scripts\id_crosscheck.py   # ID 전수 대조 JSON
.venv\Scripts\python _workspace\qa_scripts\config_proposal_check.py
.venv\Scripts\python _workspace\qa_scripts\jev_request_size.py
```
(콘솔에서 한글이 깨지면 `set PYTHONIOENCODING=utf-8`)
