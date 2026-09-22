# 03 stats-researcher: QA 수정 요청 처리 (Phase 3 진입 전)

작성일: 2026-09-22 / 작성자: stats-researcher / 기준 계약: CONTRACT_VERSION 0.2.0 (app-integrator 반영분)
대상 요청: `02_qa-validator_report.md` 4절 stats-researcher 1~5, `02_jev-strategist_design.md` §10 "stats-researcher에게 요청할 통계 필드" 1~5

## 0. 요약

| # | 요청 | 상태 | 근거 |
|---|---|---|---|
| 1 | comp_details 57개 전부 수집 | **완료 57/57** | `data/raw/metatft/2026-09-22/manifest.json`: clusters 57, comp_details 57, missing 0, failures 0. 요청은 순차로 보냈고 간격은 1.2초 |
| 2 | `DA_18_Eclipse` breakpoints `[None]` | **수정 완료** | CDragon 원본에서 `effects=[{minUnits:null, maxUnits:25000}]`이고 보유 챔피언은 0명이다. `breakpoints=[1]`, `unit_less=true`로 바꿨다. strict xfail 마커를 제거했고 테스트는 PASS다 |
| 3 | 변환 규칙 확정 + 테스트 | **완료** | `src/tft_advisor/stats/metatft_convert.py`에 R1~R15를 순수 함수로 구현했다. `tests/test_stats_convert.py` 18개 테스트 |
| 4 | `shop_specials.json` `desc_en` | 완료(부분 치환) | 345/345에 `desc_en`이 있다. 이 중 189개는 CDragon 변수를 치환하지 못해 `?`가 남아 있다(한국어 desc와 같은 한계) |
| 5 | 미매핑 증강 설명 보완(OP.GG) | 완료 5/10 | OP.GG MCP ko/en에서 `DA_*` 5개를 augments.json에 추가했다(`source:"opgg_mcp"`). 592개가 597개가 됐다. 나머지 5개는 OP.GG에도 없다 |
| §10-5 | 상점 확률 5, 7~10레벨 | 채움(7레벨은 출처가 갈림) | 5절 |

**pytest 최종: `81 passed`** (skip 0, xfail 0). `.venv/bin/python -m pytest -q -rxs`

## 1. 변경 파일

- `data/raw/metatft/2026-09-22/` (gitignore): patch, latest_cluster_info, units, items, augments_tiers, comp_augment_tiers, comps_data, comp_details_424000~424056, manifest
- `data/raw/opgg_mcp/2026-09-22/augments_{ko,en}.json`
- `src/tft_advisor/stats/collectors/metatft.py` (신규): 원본 수집기
  - 의존성은 표준 라이브러리(urllib)뿐이라 pyproject `stats` extra를 바꿀 필요가 없다.
  - 요청은 순차로 보내고 간격은 1초 이상(기본 1.2초)이다. 실패하면 대체 호스트 `api-hc2`로 1회 재시도한다. 캐시된 파일은 다시 받지 않는다.
  - `python -m tft_advisor.stats.collectors.metatft [--refresh] [--only base|comp_details]`
- `src/tft_advisor/stats/metatft_convert.py` (신규): 원본을 계약 모델로 바꾼다. `python -m tft_advisor.stats.metatft_convert [--raw …] [--previous 이전 metatft_*.json]`
- `data/stats/metatft_18.2b.json` (신규, 7.2MB): 담긴 내용은 다음과 같다.
  - CompStats 57
  - AugmentTier 2,699(전체 등급 + 덱별 등급)
  - UnitStats
  - `unit_item_stats` 41,372행(모두 `comp_id`가 있는 덱 한정 행)
  - report
- `src/tft_advisor/stats/static_extract.py`
  - `trait_breakpoints()`: unit_less 특성을 처리한다.
  - OP.GG en 설명(`desc_en_opgg`)을 넣고 OP.GG 전용 `DA_` 증강을 추가한다.
  - shop_specials에 `desc_en`을 넣는다.
  - `SHOP_ODDS_PCT`와 출처, 충돌 기록을 넣는다.
  - raw 경로를 상대경로로 바꿨다.
- `data/static/18/traits.json`, `augments.json`, `shop_specials.json`, `meta.json`: 위 스크립트의 결과를 반영했다. meta.json에 `shop_odds_pct`, `shop_odds_sources`, `shop_odds_conflicts`가 추가됐다.
- `tests/test_boundaries.py`: 최소 수정 2곳.
  1. `test_trait_breakpoints_have_no_null`의 strict xfail을 제거했다.
  2. `RAW`를 `2026-09-21` 고정값에서 **최신 날짜 캐시 디렉터리**로 바꿨다. 09-21 캐시는 Windows 경로에만 있었다. 그래서 MetaTFT 경계 테스트 7개가 계속 skip되고 있었고, 지금은 모두 PASS다.
- `tests/test_stats_convert.py` (신규): 변환 규칙 단위 테스트 17개와 캐시 전체 변환 테스트 1개.

## 2. 최종 변환 규칙 (`metatft_convert.py` 상단 docstring과 같음)

| 규칙 | 원본 | 변환 | 근거·관측(57덱) |
|---|---|---|---|
| R1 특성 | `traits_string` `DA_X_N` | `TraitReq.count = breakpoints[N-1]`. `N`은 인원이 아니라 구간 번호다. unit_less 특성(Eclipse), 범위 밖 N, 미매핑 특성은 TraitReq를 만들지 않고 미매핑은 기록한다 | `DA_18_Eclipse_1`이 comps_data에 12회 나온다 |
| R2 levels | `levels[]{stage,round,level,count}` | stage 또는 round가 빈 행은 건너뛴다. 같은 레벨이 여러 행이면 count가 가장 큰 행을 쓴다. 결과는 `level_timing {lv:"s-r"}` | 빈 stage 행 24개(전부 lv4 행) |
| R3 item→unit | `itemNames[].units[]` | `units` 키가 없으면 해당 행의 UnitItemStats만 건너뛴다. item_conditional은 그대로 둔다. `builds[]`(1~3아이템)도 UnitItemStats로 만든다. **모두 `comp_id=덱`** | 6,452행 중 263행에 units가 없다 |
| R4 보드 키 | early `unit_list`, options `units_list` | 둘 다 받는다. `&`로 나누고 소환물(`DA_Elderwood18_Lifeblossom/StonebarkTree`)은 뺀다. 소환물을 빼서 보드가 비면 버린다 | — |
| R5 레벨 키 | early `level` float(예 4.028) | 이 float는 그 보드를 가진 참가자들의 평균 레벨이다. 레벨 키로는 **dict 키 문자열의 int**를 쓰고 반올림하지 않는다. 4~7은 early에서, 8~10은 options에서 가져온다. 7은 early에 없을 때만 options에서 가져온다. 4~10 밖의 키(options `11`)와 빈 목록은 버린다. 레벨마다 count 내림차순 상위 10개 | 정수가 아닌 level 758개 |
| R6 early `win` | `win` | **top4 비율**로 해석해 `BuildupBoard.top4`에 넣는다. `win_rate`는 None이다 | 가중 평균이 win 0.595, avg 4.21이다. 1등 비율이라면 0.6은 불가능하다 |
| R7 carry | `unit_stats[].num_items`, `builds` | **count 기준**: 최종 보드 유닛 중 "이 덱에서 3아이템을 든 판 수"(`num_items[3].count`)가 가장 큰 유닛이다. 동률이면 builds score 최대로 정한다. 대표 빌드(R8)가 방어 위주(방어 > 공격)인 유닛은 후보에서 뺀다. 후보가 없으면 제외 없이 다시 고른다. **배열 순서는 쓰지 않는다** | score는 표본이 적은 빌드에서 튀어서 1차 기준으로 부적합하다. 3아이템 보유 판 수는 "아이템을 몰아주는 유닛"을 직접 측정한다. 방어 제외는 탱커가 워모그·가고일 3개를 드는 덱을 막기 위한 것이다. 결과: carry None 0/57 |
| R8 carry_bis_items | `builds[]` (buildName 3개) | **count ≥ 50인 빌드 중 score 최대**로 고른다. 없으면 count 최대 | score는 place 개선을 반영하고, 50판 하한으로 소표본 노이즈를 거른다 |
| R9 is_core | `unit_stats[].pcnt` | 덱 내 출현율 ≥ **0.75** | 455유닛 중 276 |
| R10 role | 대표 빌드 아이템 재료 | carry 유닛이면 `"carry"`. 나머지는 재료를 센다: 공격(검/활/지팡이/장갑) ≥2이면 `"carry"`, 방어(조끼/망토/허리띠) ≥2이면 `"tank"`, 그 외는 `"support"`. hybrid 재료는 양쪽에 0.5씩 센다. 3아이템 빌드가 없으면 1~2아이템 빌드 중 count 최대로 판정한다. 빌드가 아예 없으면 None | carry 164, tank 152, support 134, None 5. CDragon DA_ 아이템 effects가 비어 있어 수치로 분류할 수 없어서 재료 구성으로 판정했다 |
| R11 star | `unit_stats[].tiers` | pcnt가 가장 큰 성급 | — |
| R12 comp_id | `name[]`(특성 + 유닛 헤드라인) | slug로 만든다. 예 `juggernaut-zyra-amumu`. 충돌하면 `~2` 접미사를 붙인다. `--previous`가 주어지면 최종 보드 Jaccard ≥ 0.75인 이전 comp_id를 1:1로 재사용한다(클러스터 재계산 대비). 클러스터 ID는 `source_cluster_id`에 보존한다 | 57개 모두 유일 |
| R13 comp_augment_tiers | `{cid:{distance,source_title,augments}}` | 키는 comps_data의 덱 ID(424xxx)와 같다. **다음 두 조건을 모두 만족할 때만 채택**한다: `distance ≤ 0.5`, 그리고 `source_title` 첫 구간의 챔피언이 덱 최종 보드에 있음. distance 단독으로는 구분이 안 된다(채택 0.17~0.499, 제목 불일치로 기각 0.43~0.50). 그래서 제목 검증이 필수다 | 37개 중 32개 채택. 기각 5개: 424020/424045(Ahri & Morgana), 424027(ElderDragon), 424043/424050(Draven) |
| R14 rank_filter | 소스마다 순서가 다른 문자열 | `contracts.normalize_rank_filter`(정렬)로 저장하고 집합으로 비교한다. comps_data/comp_details는 rank 파라미터 없이 호출하므로 `rank_filter=None` | — |
| R15 item_usage | comps_data `build_items[].pcnt` | 그대로 `CompStats.item_usage`에 넣는다. float ≥ 0이고 자르지 않는다 | 3절 |

## 3. item_usage 실측 범위 (2026-09-22 스냅샷)

- `build_items[].pcnt`: 578값, **최소 0.0519, 최대 1.38263, 1 초과 34건**. QA 09-21 스냅샷은 35건이었다.
- 뜻은 "그 덱 플레이어 1명이 가진 해당 아이템의 평균 개수"다. 계약 `dict[ItemId, float ≥ 0]`과 맞는다.
- comp_details `itemNames[].pcnt`도 같은 의미다(예 IE 1.09). 변환에는 comps_data 값을 쓴다.

## 4. 수집·검증 결과 (report)

- patch 18.2b, clusters 57, with_details 57, carry_none 0
- `games < 1000`(prefilter 기준) 덱 4개: 604, 713, 772, 919판
- unmapped:
  - 아이템 `DA_Artifact_Hullcrusher` 1개
  - 증강 5개: `DA_18_RivalsAugmentPlus`, `DA_Lineup`, `DA_NestingDolls`, `DA_StarringUp`, `DA_SubscriptionService`. OP.GG에도 없다.
  - 모두 `unmapped.json`에 선언되어 있다(경계 테스트 PASS).

## 5. 상점 확률 (§10 요청 5)

`meta.json.shop_odds_pct`(%, 1~5코스트):

| Lv | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| 1~2 | 100 | 0 | 0 | 0 | 0 |
| 3 | 75 | 25 | 0 | 0 | 0 |
| 4 | 55 | 30 | 15 | 0 | 0 |
| 5 | 45 | 33 | 20 | 2 | 0 |
| 6 | 30 | 40 | 25 | 5 | 0 |
| 7 | 16 | 30 | 43 | 10 | 1 |
| 8 | 15 | 20 | 32 | 30 | 3 |
| 9 | 10 | 17 | 25 | 33 | 15 |
| 10 | 5 | 10 | 20 | 40 | 25 |

- 출처(2026-09-22 조회, 모두 "Set 18" 표기): tftflow.com, esportstales.com, metabot.gg. 화면 관측값(3/4/6레벨)과 모두 일치한다.
- **7레벨은 출처가 갈린다.** metabot.gg는 19/30/40/10/1이다. 채택한 값은 다수 출처(2/3)를 따랐다. 대안은 `shop_odds_conflicts`에 함께 적었다. 7레벨 스크린샷이 생기면 확정한다.
- CDragon에는 이 값이 없다. 그래서 `static_extract.py`의 상수로 관리한다. 패치마다 확인해야 한다.

## 6. 계약 변경 요청

없음. 0.2.0에 반영된 `CompUnit.role`, `CompStats.item_usage`, `UnitItemStats.comp_id`, `CompStats.levelling`을 모두 채운다.

참고 제안(선택, 급하지 않음):
- (a) 상점 확률을 쓰는 곳이 생기면 `static_data`에 `shop_odds(level) -> list[float]` 접근자가 필요하다(app-integrator 영역).
- (b) `BuildupBoard`에는 options(7~10) 보드의 `score`를 담을 자리가 없다. 지금은 쓰지 않으므로 버린다.

## 7. 남은 한계

- 증강 성적 통계는 여전히 모든 소스에서 비어 있다. 편집자 등급만 있다.
- comps_data/comp_details는 rank 필터 없이 전 티어 기준으로 추정된다. units/items는 마스터 이상이다. 두 값을 섞어 비교하지 말 것.
- `shop_specials` desc_en 189개와 증강 desc에 치환되지 않은 `?`가 남아 있다. 증강은 OP.GG에 있는 253개에 한해 `desc_en_opgg`로 보완했다.
- 수집 CLI를 하나로 묶은 `python -m tft_advisor.stats update`와 SQLite 적재는 아직 없다. 지금은 JSON 1파일을 만들고, 로더는 app 쪽에서 만든다.
