# 03 qa-validator: Phase 2 재검증 (Phase 3 진입 판정)

작성일: 2026-09-22 / 작성자: qa-validator
대상:
- CONTRACT_VERSION 0.2.0, `config.py`, `config/*.toml`
- `02_jev-strategist_design.md`(2026-09-22 개정), `02_app-integrator_report.md`(2026-09-22 개정), `03_stats-researcher_fixes.md`
- `stats/metatft_convert.py`, `stats/collectors/metatft.py`, `data/stats/metatft_18.2b.json`, `data/static/18/`

직접 수정 범위: 소스 코드는 수정하지 않았다. 바꾼 것은 테스트와 QA 스크립트뿐이다.
- `tests/test_converted_stats.py`(신규, 9개 테스트, 그중 1개 strict xfail)
- `_workspace/qa_scripts/`:
  - `id_crosscheck.py`: 최신 캐시를 쓰도록 바꾸고, comp_details 57개 전부와 변환 산출물 ID 대조를 추가했다. `comp_options.json`은 선택 입력으로 바꿨다.
  - `jev_request_size.py`: 최신 캐시 경로로 바꿨다.
  - `converted_stats_check.py`(신규), `design_refs_check.py`(신규)

## 판정: **Phase 3 ready: yes**

- 이전 FAIL 4건(1, 2, 5, 6)과 5절 "진입 전 필수" 항목(app-integrator 1~4, jev-strategist 1~4, stats-researcher 1~3)이 모두 해소됐다.
- 새로 찾은 문제는 모두 **진입을 막지 않는다.** Phase 3 초반에 고치면 된다(아래 5절 "진행하며 해결").
- vision 정확도 측정은 사용자 입력이 있어야 하므로 **blocked on user**로 분류했다. FAIL로 세지 않는다.

## 요약: PASS 13 / FAIL 0 / WARN 2 / BLOCKED(사용자) 1  (행 16개: #4는 조건부 PASS, #5는 설계 PASS·vision BLOCKED로 BLOCKED에 집계)

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | B 필드 ↔ contracts | **PASS** (이전 FAIL) | `UnitItemStats.comp_id`(contracts.py UnitItemStats)가 들어갔다. `CompStats.item_usage`는 `float ≥ 0`이고 상한이 없어 실측 1.38을 받는다. `CompUnit.role`, `TargetComp.levelling`, `component_priority`도 있다. `advisor.jev_model`은 패턴까지 검증된다. `design_refs_check.py` 결과, 설계가 참조하는 계약 필드 22종이 모두 존재한다. 없는 것은 `GameState.item_offer` 하나인데, §10-1에서 보류로 결정된 참조라 정상이다 | - | - |
| 2 | 설계 공식 파라미터 ↔ weights.toml ↔ config.py | **PASS** (이전 FAIL) | `config_proposal_check.py`: §10a 73키 모두 거부 0, toml과 §10a 기본값 차이 0. 합=1, 단조 사다리, 순서 validator가 있다. `ShrinkageWeights.adjust(prior=)`, `commit_for_stage`/`for_stage`가 같은 `_stage_lookup`을 쓴다(config.py `_stage_lookup`). 설계 본문의 `section.key` 참조 64개 중 config에 없는 키는 0이다(`design_refs_check.py`) | - | - |
| 3 | 통계 모델 ↔ MetaTFT 원본 ↔ 변환 규칙 | **PASS** | comp_details 57/57을 받았다. R1~R15가 코드와 테스트로 확정됐다(`test_stats_convert.py` 18개). Eclipse는 `breakpoints=[1]`, `unit_less=true`다. 원본 캐시 기반 경계 테스트 7개는 이전에 skip이었고 지금은 실행되어 통과한다 | - | (N5·N6 참조) |
| 4 | ID 체계 전수 대조 | **PASS (조건부)** | 정적 ID 패턴 위반 0, 상점 풀 이름 중복 0. fixture 7장: 상점 23칸의 코스트가 모두 일치하고 모두 MetaTFT에 등장한다. 변환 산출물의 아이템 142, 특성 34, 증강 258 중 미매핑은 모두 선언된 것이다. **차이 2건(비차단)**: (a) comp_details에서 선언되지 않은 비챔피언 ID 3개가 관측된다(`DA_Elderwood18_Protector`, `DA_TheTower_TrainingDummy`, `DA_TrainingDummy`). 이들이 변환 결과 `buildup` 보드 8곳에 들어가 있다(→ N6). (b) `unmapped.json`의 증강 5개(`DA_CalculatedLoss`, `DA_18_FloraFatalisAugmentPlus`, `DA_ConstructACompanion`, `DA_DoubleTrouble`, `DA_ForgeAFriend`)는 OP.GG로 보완되어 이제 매핑된다. 선언만 남은 상태다 | stats-researcher | 5절 stats 1·2 |
| 5 | D 인식 필드(MVP) ↔ GameState ↔ B 입력 | **PASS (설계)** / **BLOCKED (사용자)** | 설계 측 결측 규칙이 모두 생겼다. §4.3(a): hp=None이면 state 키를 빼고, hold는 moderate로 간주하며, hp_danger_shift는 적용하지 않고, A2/I1은 -nohp 문구를 쓴다. §4.3(b): board/bench=None이면 copies_owned/buy_makes_2star 키를 빼고, bonus=0, S1-noboard 문구를 쓴다. §4.3(c): 장착 아이템을 모르면 `equipped_tracked`로 추적한다. 문구 선택 조건은 "state 키 존재 여부 하나"라서 캐시 키와도 맞다. **vision 쪽 미해결(사용자 필요)**: fixture 7장은 여전히 방송 크롭(1986~2001 × 1117~1126)이다. items/augments_owned 라벨 0건, streak 부호 미확정, 2-4 화면 상태(carousel vs item_select) 미확정 | 사용자 → vision-engineer | 5절 vision |
| 6 | B 출력 ↔ Recommendation ↔ 오버레이 | **PASS** (이전 FAIL) | §5.4가 TargetComp 전 필드(owned/missing, items_ready 3단계 판정·holder, next_buildup_board 선택 규칙, reasons 최대 5개)와 Recommendation 메타(state_hash = §8.3 캐시 키, latency_ms, created_at UTC, jev_used/fallback_reason)를 모두 정의한다. 참조 필드는 모두 계약에 있다. **보완 1건(비차단)**: items_ready 자원 풀 P에 `ItemState.others`가 없다 → N5 | jev-strategist | 5절 jev 1 |
| 7 | 요청 크기 추정 재현 | **PASS** | `jev_request_size.py`(최신 캐시): planning 26~28문항, 7.1~10.1k tok. augment_select 46문항, 10.5~14.0k tok. 설계 §3.2(MVP 28 / 46)와 일치한다 | - | - |
| 8 | pytest 전체 | **PASS** | `.venv/bin/python -m pytest -q -rxs` → **89 passed, 1 xfailed**(2.3s). 기존 81개에 신규 9개를 더했다. xfail(strict) 1건은 N6이다. 해소되면 XPASS로 실패하므로 그때 마커를 지운다. skip 0 | - | - |
| N1 | 변환 산출물 ↔ contracts 0.2.0 | **PASS** | `metatft_18.2b.json`: CompStats 57, AugmentTier 2,699(전체 258 + 덱별 32덱), UnitItemStats 41,372, UnitStats 69가 모두 `model_validate`를 통과하고 JSON 왕복 불일치가 0이다. carry None 0, carry ∈ final_board 57/57, carry role=="carry" 57/57, `carry_bis_items == carry 유닛 items` 57/57, 핵심 유닛이 없는 덱 0, item_usage 빈 덱 0(최대 1.38263, 1 초과 34), buildup·level_timing·item_conditional·key_traits 빈 덱 0. role None은 455 중 5 | - | - |
| N2 | comp_id 파일 간 일관성 | **PASS** | comp_id 57개가 모두 유일하다. `comp_ids` 맵이 source_cluster_id→comp_id로 일치한다. AugmentTier.comp_id(32덱)와 UnitItemStats.comp_id(전 행)가 모두 CompStats comp_id의 부분집합이고, comp_id=None 행은 0이다. 설계 6.3의 holder(carry) 덱 한정 place_change 행이 57/57 덱에 있다 | - | - |
| N3 | 설계 §4.3/§5.4 참조 ↔ 계약·GameState·config | **PASS** | `design_refs_check.py`: config 참조 64개가 모두 존재한다(`pf.` = `prefilter`). 계약 참조는 `item_offer`(보류분)만 없다. §4.3/§5.4가 쓰는 GameState(hp/board/bench/items/level/stage/active_traits), ItemState(completed/emblems/components), UnitOnBoard.items, `is_reliable`, `stage_tuple`, items.json `composition`이 모두 있다 | - | - |
| N4 | §8.1 FallbackReason ↔ StrEnum | **PASS** | 설계 §8.1 표, §10-7 행, `contracts.FallbackReason`이 9종으로 같다. `test_design_fallback_reasons_equal_enum`으로 고정했다 | - | - |
| N5 | 변환 규칙 ↔ 설계 공식 가정 | **PASS + WARN** | 일치하는 것: carry(3아이템 판 수), is_core(≥0.75), item_usage(pcnt, `usage_min_pcnt` 단위), comp_augment_tiers(distance≤0.5 + 제목 챔피언; 설계 t(a,c)/ed는 comp_id로 조회), early `win`→`BuildupBoard.top4`(설계는 top4/win_rate를 어디에도 쓰지 않으므로 영향 없음), level_timing/buildup(§5.4 next_buildup_board의 입력). **WARN (a)**: carry_bis_items에 제작할 수 없는 아이템이 있다. 57덱 중 **13덱**, 고유 BIS 43개 중 11개다(유물 8, 찬란한 2, 증강 상징 `DA_18_EmblemFloraFatalisAugment`). 예: veigar 3덱은 `DA_Artifact_Dawncore`, kayle은 Navori, caitlyn은 Radiant Guinsoo. 영향 두 가지. ① §5.4 items_ready의 P는 completed+emblems+장착분만 본다. 벤치 `ItemState.others`에 있는 유물·찬란한 아이템은 보유해도 `missing`으로 나온다. 조합이 없어 craftable도 될 수 없다. ② R8은 score가 최대인 빌드를 고르는데, 후반 획득 아이템이 생존 편향으로 score를 부풀린다. 그래서 "목표 빌드"로 부적합한 아이템이 BIS가 된다. **WARN (b)**: role="carry"가 덱당 1~5명이다(R10은 "딜러" 의미, 설계 §4.2 문구는 "3아이템 1위 = carry"). state의 `main_carry`는 반드시 `CompStats.carry`를 써야 한다. 2덱(blossom-amumu, inferno-amumu)은 carry(Ashe)가 is_core=False다. 이 경우 U(c)/μ에서 carry가 mu_final 등급을 받는다(동작은 정의돼 있다). **참고**: §5.4-1 "level 모를 때 `max{lv : level_timing[lv] ≤ stage}`"는 해당 집합이 빌 때(최소 timing 이전 스테이지)의 규칙이 없다. level_timing 최소 키는 3(25덱)/4(11)/5(21)이다 | jev-strategist, stats-researcher | 5절 jev 1~3, stats 3 |
| N6 | 변환 buildup ↔ 챔피언 ID | **WARN** (xfail 고정) | 비챔피언 유닛이 buildup 보드 8곳에 있다: `DA_Elderwood18_Protector` 3, `DA_TheTower_TrainingDummy` 3, `DA_TrainingDummy` 2. `SUMMON_IDS`(metatft_convert.py 상수)는 Lifeblossom/StonebarkTree 2개만 거른다. 영향: next_buildup_board에 들어가면 오버레이의 이름·코스트 조회가 None이 된다. S_now 출현 비중에도 섞인다. 별도로 UnitItemStats 146행에 미매핑 아이템 `DA_Artifact_Hullcrusher`가 들어 있다(선언된 것이고 조회만 실패한다) | stats-researcher | 5절 stats 1 |
| N7 | 7레벨 상점 확률 출처 충돌 | **PASS** (영향 없음) | 채택값 16/30/43/10/1과 대안(metabot) 19/30/40/10/1은 설계 §4.1 라벨(none/rare/uncommon/common)로 바꾸면 **완전히 같다**(uncommon/common/common/uncommon/rare). S_now와 C_path는 확률을 쓰지 않는다. vision이 shop_odds를 읽으면 화면값이 우선한다. 모든 레벨의 합은 100이고, fixture 3/4/6레벨 라벨은 meta와 일치한다. `test_level7_odds_conflict_does_not_change_jev_labels`로 고정했다 | - | (선택) 7레벨 화면 캡처가 생기면 확정 |
| N8 | shop_specials desc_en `?` 189/345 | **PASS (영향 제한)** | 189개 모두 set_native이고 `?`는 248개다. 147개는 숫자가 하나도 없다(예 "Gain ? gold."). fixture에 나온 유일한 특수 상품 `DA_ThreeMe18`은 설명이 깨끗하다. 영향 범위는 S3 한 문항(Score(3), 가중치 now만)과 폴백 0.3이다. 덱·아이템 추천에는 영향이 없다. 설계의 "`?` → `X` 치환" 규칙(§0 언어 결정)은 증강에만 적혀 있고, S3(§3)는 "desc_en 없으면 제외"만 정의한다. 그래서 `?`가 포함된 특수 상품 설명의 처리가 정해져 있지 않다 | jev-strategist | 5절 jev 4 |

---

## 3. 이전 "진입 전 필수" 체크

| 담당 | 요청 | 상태 | 확인 방법 |
|---|---|---|---|
| app-integrator 1 | config §10a 키 + 합=1 validator + toml | ✅ | config_proposal_check 73/0/0, `test_config_validators_reject` |
| app-integrator 2 | `adjust(prior=)` | ✅ | `test_shrinkage_adjust_prior` |
| app-integrator 3 | `UnitItemStats.comp_id` | ✅ | `test_unit_item_stats_has_comp_scope`(xfail 제거됨), N2 |
| app-integrator 4 | 제안 1~5 결정, item_usage 타입 | ✅ | §10 표, contracts `item_usage: float ≥ 0` |
| app-integrator 5·6 | FallbackReason, rank_filter 집합 | ✅ | N4, `test_rank_filter_normalization` |
| jev-strategist 1 | TargetComp·메타 채우기 규칙 | ✅ | §5.4 (보완: N5a) |
| jev-strategist 2 | hp=None 규칙 | ✅ | §4.3(a), §6.8 |
| jev-strategist 3 | board/bench/장착 None 규칙 | ✅ | §4.3(b)(c) |
| jev-strategist 4 | 상수 → 키, jev_model, commit_by_stage 1·5+ | ✅ | §10a, `test_stage_lookups` |
| jev-strategist 5·6 | item_usage 재정의, 문서 수치 | ✅ | §2.2 pcnt 정의, §3.2 46문항 |
| stats-researcher 1 | comp_details 57개 | ✅ | manifest 57/0, 변환 with_details 57 |
| stats-researcher 2 | Eclipse | ✅ | `test_trait_breakpoints_have_no_null` PASS |
| stats-researcher 3 | 변환 규칙 확정 | ✅ | R1~R15 + 18 테스트 + N1/N2 |
| stats-researcher 4·5 | desc_en, 증강 설명 | ✅(부분) | N8, 증강 5/10 |
| vision-engineer 1·2 | 원본 1920x1080 캡처, items/augments_owned 라벨 | ⛔ **blocked on user** | fixture 크기가 여전히 1986~2001 × 1117~1126, 라벨 키 변화 없음 |

---

## 5. 담당자별 수정 요청 (모두 Phase 3 진행 중 해결 가능, 진입 비차단)

### jev-strategist (설계 문서)
1. **[우선]** §5.4 items_ready의 자원 풀 P, §2.2 I(c)의 "보유 완성템", §4.3(c) `equipped_tracked`의 추적 대상에 **`ItemState.others` 중 category ∈ {artifact, radiant}** 를 포함한다. 근거: carry_bis_items 13/57덱이 유물·찬란한 아이템이다(N5a). 제작할 수 없는 BIS는 craftable 판정과 `component_priority` 계산에서 **건너뛴다**고 명시한다(조합표에 없으므로 부족 재료를 정의할 수 없다).
   - 재현: `.venv/bin/python _workspace/qa_scripts/converted_stats_check.py` → `bis_items.no_composition`
2. §4.2의 `role` 문단(“3아이템 빌드 표본 1위 유닛이 carry …”)을 stats R10 의미로 고친다. `role="carry"`는 딜러이고 덱당 1~5명이다. **`main_carry`는 `CompStats.carry`만 쓴다.** `tanks`는 role=="tank"다. role None(5/455)일 때만 advisor가 추론한다.
3. §5.4 next_buildup_board 1단계에 공집합 규칙을 추가한다. 예: `level_timing`의 모든 키보다 이른 스테이지면 L = min(level_timing 키) − 1(하한 1). §2.2 U(c)의 "현재·다음 레벨 buildup"도 같은 L을 쓴다.
4. S3(§3)의 특수 상품 `desc_en`에 `?`가 있으면 증강과 같은 규칙으로 처리한다(`?` → `X`). 숫자가 전혀 없는 설명(147개)은 S3 gate를 `jev.low_confidence_scale`로 강제할지도 정한다(N8).
5. (문서 수치) §2.1: 548/694/751/884 → 09-22 스냅샷은 604/713/772/919. §7: "덱별 등급 37개 덱" → 채택 32개(R13 기각 5). §7·§11: "미매핑 신규 증강 10개" → 5개. "desc `?` 193" → augments 597개 기준으로 다시 센다.

### stats-researcher
1. **[우선]** buildup 보드에서 비챔피언 유닛을 거른다. 방법은 둘 중 하나다.
   - `SUMMON_IDS`에 `DA_Elderwood18_Protector`, `DA_TheTower_TrainingDummy`, `DA_TrainingDummy`를 추가한다.
   - 더 견고하게, `static.get("champions", id)`가 None이거나 `shop_pool`이 아닌 ID를 제거한다.

   반영한 뒤 `tests/test_converted_stats.py::test_converted_buildup_units_are_champions`의 xfail 마커를 제거한다. `unmapped.json`의 `summons_in_boards`에도 3개를 선언한다.
2. `unmapped.json`의 `augments.metatft_augments_tiers`에서 OP.GG로 보완된 5개를 빼거나 `resolved_by_opgg`로 옮긴다(선언과 관측의 차이를 0으로 복구). `checked_at`을 갱신한다.
3. (선택, jev 1과 함께 결정) R8 BIS 후보를 제작 가능한 아이템(completed, 조합 가능한 emblem)으로 한정하는 안을 검토한다. 유물·찬란한 아이템은 CompUnit.items에만 남기거나 별도 필드로 둔다. 13덱은 현재 BIS에 제작 불가 아이템이 들어가 있어서, 제작 가능한 두 번째 대안이 b(x,c)=carry_bis를 받지 못한다. 설계를 먼저 바꾸는 쪽(jev 1)을 택하면 이 변경은 필요 없다. 둘 중 하나로 합의해 달라.
4. (선택) `stats/collectors/metatft.py`의 `main`이 `settings.stats`(`request_interval_s`, `user_agent`, `days`, `rank_filter`)를 읽지 않고 같은 값을 하드코딩한다. 설정 ↔ 코드 경계를 맞추려면 기본값을 `load_settings().stats`에서 가져온다.

### app-integrator
- 진입 전 요청 없음. (선택) 상점 확률 접근자 `static_data.shop_odds(level)`는 stats 제안 (a)와 같다. 설계 §4.1 shop_odds 라벨 폴백을 구현할 때 추가한다.

### vision-engineer / 사용자 (blocked on user, FAIL 아님)
1. 원본 1920x1080 전체 캡처(layout.md 6절 목록). 현재 fixture는 방송 크롭이다.
2. 정답 파일에 items/augments_owned 라벨을 추가한다(현재 0건이라 아이템 ≥90% 목표를 잴 수 없다).
3. streak 부호, `2-4 아이템선택`의 screen_mode(carousel vs item_select), 7레벨 상점 확률 캡처(N7 확정용)를 확인한다.

---

## 6. 재실행 방법
```
.venv/bin/python -m pytest -q -rxs                                  # 89 passed, 1 xfailed (기대)
.venv/bin/python _workspace/qa_scripts/config_proposal_check.py     # 73 / rejected 0 / differ 0
.venv/bin/python _workspace/qa_scripts/converted_stats_check.py     # 변환 산출물 ↔ 계약·정적·설계 가정
.venv/bin/python _workspace/qa_scripts/design_refs_check.py         # 설계 참조 ↔ config/contracts, FallbackReason
.venv/bin/python _workspace/qa_scripts/id_crosscheck.py             # ID 전수 대조(원본 57 comp_details + 변환 산출물)
.venv/bin/python _workspace/qa_scripts/jev_request_size.py
```
모든 스크립트는 `data/raw/metatft/`의 최신 날짜 디렉터리와 `data/stats/metatft_*.json`의 최신 파일을 자동으로 고른다. 네트워크 호출은 없다.
