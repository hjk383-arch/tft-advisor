# 04 qa-validator: Phase 3 점진 QA — stats 모듈

작성일: 2026-09-22 / 작성자: qa-validator
범위: `src/tft_advisor/stats/`(repository, db, diff, refresh, `__main__`, metatft_convert, collectors), `data/stats/`, 관련 테스트
기준 문서: `04_stats-researcher_impl.md`, `03_qa-validator_recheck.md`, `02_jev-strategist_design.md`(Phase 3 개정본)
범위 밖: advisor/vision은 병렬 작업 중이다. 이번 실행에서는 둘 다 테스트 실패가 없었다. advisor 코드는 경계 대조 목적으로만 읽었다.

직접 수정한 것(소스 수정 없음):
- 신규 `tests/test_stats_qa.py`: 테스트 5개. 그중 1개는 strict xfail로 FAIL 항목 stats-1을 고정한다.
- 신규 `_workspace/qa_scripts/stats_repo_check.py`: repository ↔ 원본 ↔ JSON ↔ SQLite ↔ 설계를 전수 대조한다.
- 수정 `_workspace/qa_scripts/id_crosscheck.py`, `converted_stats_check.py`: 기존 스크립트는 R16 분리 전의 합쳐진 형태만 읽었다. 이제 `unit_build_stats`와 `item_stats`도 읽는다.

실행한 명령(네트워크 없음): `python -m tft_advisor.stats refresh --no-fetch`, `info`, `diff`
- 결과: 스냅샷 #1이 #2로 교체됐다. 같은 fetched_at이므로 의도된 동작이다.
- 재생성된 `metatft_18.2b.json`은 실행 전 사본과 **내용이 바이트 단위로 같다**.

## 요약: PASS 7 / FAIL 0 (WARN 4, 비차단)

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | Repository API ↔ 설계 조회 | **PASS** | 설계가 쓰는 조회를 모두 제공한다. 제공 경로는 아래 "1 상세" 표에 있다. Protocol 메서드 32개가 모두 구현돼 있고 시그니처 불일치는 0이다. `isinstance(repo, AdvisorStats)`는 True이고, advisor Protocol 메서드 중 누락은 0이다. 참고: advisor가 부르는 조회는 두 구현(repository, advisor의 `JsonStatsAdapter`)이 같은 값을 준다(`test_repository_matches_advisor_json_adapter`). 두 구현의 의미가 갈리는 경우(아래 W3)는 advisor가 현재 쓰지 않는다 | - | jev 1(선택), jev 2(선택) |
| 2 | `unit_item_stats` / `unit_build_stats` 분리(R16) | **PASS** | **원본 전수 대조(57/57 덱)**: holds는 원본 itemNames[].units[] 24,274행과 1:1이고, games·avg·place_change 불일치 0, 키 중복 0이다. builds는 원본 builds[] 17,098행과 1:1이고 불일치 0이다(1아이템 6,077, 2아이템 1,224, 3아이템 9,797). holds에 다중 아이템 행 0, comp_id=None 행 0. **파생 전체값(comp_id=None)**: (유닛, 아이템) 쌍 4,639개를 다시 계산했다. games 합계와 games 가중 avg_place·place_change의 불일치 0, [1,8] 밖으로 나가는 값 0이다. `fallback_overall=True`도 동작한다. **downstream**: `tests/fixtures/stats/mini_18.json`(4,883행)은 새 holds의 정확한 부분집합이다(중복 0, comps 동일). advisor `JsonStatsAdapter`는 `unit_item_stats`만 읽으므로 올바른 의미를 받는다. 옛 형태를 전제한 QA 스크립트 2개는 내가 고쳤다. contracts는 바뀌지 않았다(`UnitItemStats` 그대로) | - | - |
| 3 | SQLite ↔ JSON, 보존, diff | **PASS + WARN** | **일치**: `open_repository()`(SQLite)와 `from_json`을 모델 수준에서 비교했다. comps, 증강 등급 전 범위(전체 + 57덱), unit_stats, item_stats, holds(유닛 × 전체·57덱), builds가 모두 **equal**이고 meta(patch/fetched_at/rank)도 같다. 행 수는 57/2,699/69/24,274/17,098/142로 DB·JSON 모두 같다. **보존**: 임시 DB에 7번 적재하면 최신 5개(7..3)가 남고 고아 행은 0이다. 같은 fetched_at으로 다시 적재하면 교체된다. **diff**: 합성 변경(avg +0.25, carry 변경, 덱 제거, 전체 등급 S→D, 유닛 +0.3, 신규 미매핑)을 모두 잡는다. 같은 문서끼리 비교하면 빈 diff다. **WARN W1**: `db.connect()`는 읽기 경로(list/find/read_snapshot)에서도 `executescript(SCHEMA)`와 `INSERT OR REPLACE schema_info`를 실행한다(`db.py` `connect`). 그래서 읽기 전용 DB를 열면 `OperationalError: attempt to write a readonly database`가 난다(재현했다). 앱이 refresh와 동시에 열면 쓰기 잠금을 두고 경합한다. pytest 도중 `stats.sqlite-journal`이 잠깐 생긴 것도 관측했다. **WARN W2**(diff 사소): 덱별 증강 등급 변경은 보고하지 않는다(`diff.py` `tiers()`는 전체 등급만 본다). 이전 스냅샷이 없으면 "패치 변경: 예, 덱 57개 추가"로 출력된다. 같은 캐시로 `refresh --no-fetch`를 다시 실행하면 이전 스냅샷이 교체되므로 `diff` CLI에 비교할 대상이 남지 않는다(현재 DB 상태가 이렇다) | stats | stats 1, 2 |
| 4 | WARN 1/2, `is_craftable`/`item_category` | **PASS** | **WARN 2 해소**: final_board와 buildup의 비챔피언 유닛 0개. unmapped 선언과 관측의 차이 0(`id_crosscheck.py`). **WARN 1 해소**: stats는 판별 함수를 제공한다. 설계 §2.2·§4.3c·§5.4는 개정됐다. advisor는 `features.py` `OWNED_OTHER_CATEGORIES`로 유물·찬란한 아이템을 보유 풀에 넣고, `scoring.py` items_ready와 component_priority에서 `recipe is None`인 항목을 건너뛴다. **카테고리 × 조합 가능 전수 결과**: completed 72/72 True, tactician 6/6 True, emblem 16 True / 5 False(Coven, Defender, Juggernaut, FloraFatalis, FloraFatalisAugment. 모두 items.json `composition=[]`이므로 옳다), artifact 83과 radiant 82는 모두 False, component 20은 모두 False(`is_component`는 True), consumable, assist_reward, other도 False. BIS 43개 중 조합 불가 11개(유물 8, 찬란한 2, 증강 상징 1)이고 영향 덱은 13/57이다. category=None인 BIS 0. craft 55쌍은 결과 중복 0이고 recipe와의 왕복 불일치 0이다. 상징 21개 → 특성 20개로 모두 매핑되고 static에 존재하며 모두 어떤 덱의 key_traits에 들어 있다. **WARN W4(사소)**: `recipe()`/`is_craftable()`이 비세트 레거시 `TFT_Item_*` 39개에도 True를 준다. 반면 `craft()`와 advisor 어댑터는 `DA_*`·set_native만 대상으로 한다 | stats | stats 3(선택) |
| 5 | `settings.stats` → 수집기 | **PASS** | `refresh.collect_kwargs`는 days, rank_filter, request_interval_s, user_agent를 settings에서 읽는다. 수집기 `main`의 argparse 기본값도 `load_settings().stats`에서 온다. 두 경로 모두 테스트로 고정돼 있다(`test_stats_refresh.py` 3개). db_path도 settings를 쓴다. 최소 간격은 config(ge=1.0)와 `Fetcher`의 `max(1.0, ·)` 두 곳에서 강제한다. 남은 상수는 `collect_raw`/`Fetcher`의 폴백 기본값(DEFAULT_RANKS, DEFAULT_UA, 1.2, 3)이다. 설정 없이 직접 호출할 때만 쓰이고 현재 값은 settings 기본값과 같다. `queue=1100`은 설정 키가 아니다. `stats.primary`/`fallbacks`는 아직 소비처가 없다(수집기가 metatft 하나뿐이라 정상) | - | (선택) app 1 |
| 6 | 로드·조회 속도 | **PASS** | import 0.18s, `open_repository()`(SQLite, preload) **1.01s**, preload=False면 0.55s, from_json 1.0s. 모두 앱 시작 때 1회뿐이다. 조회: `unit_item_stat` **0.38µs**. 실시간 1회 분량을 모사하면(53덱 × 보드 유닛 × 아이템 stat/recipe/craftable + BIS 카테고리 + 증강 등급 + shop_odds 10) **2.7ms**다. 실시간 루프에 문제없다. preload=False일 때 유닛별 첫 조회 비용은 약 10ms다 | - | - |
| 7 | pytest 전체 | **PASS** | `.venv/bin/python -m pytest -rxs` → **242 passed, 3 skipped**(live Jev, `TFT_LIVE_JEV=1` 필요), 63s. 여기에는 신규 `tests/test_stats_qa.py`가 포함되지 않는다. 신규 파일을 따로 돌린 결과는 4 passed, 1 xfailed(strict, W1)다. stats 계열 테스트 57개 PASS. advisor/vision 테스트도 이번 실행에서는 모두 통과했다(범위 밖, 진행 중) | - | - |

### 1 상세: 설계 조회 → repository

| 설계 위치 | 필요한 조회 | 제공 | 확인 |
|---|---|---|---|
| §2.1 사전 정리 | 전체 덱, games, final_board/carry(Jaccard) | `comps(min_games=)` | min_games 1000 → 53덱(제외 604/713/772/919, 설계 §2.1 개정 수치와 같음) |
| §2.2 b(x,c), I(c) | carry_bis_items, is_core 유닛 items, item_usage, 상징→특성, key_traits | CompStats 필드 + `emblem_trait` | 상징 특성 20/20이 key_traits ID 체계와 같다. key_traits ID는 모두 static에 있다 |
| §2.2 A(c)/t(a,c), §7 ed(a) | associated_traits, 덱별 → 전체 → 중립 등급 | `augment_traits`, `augment_tier(a, c)` / `augment_tier_for` | 등급은 S/A/B/C만 나온다(weights `editorial_tier_score` 키 범위 안). 덱별 등급은 32덱. 등급 없음 → None → advisor가 중립값 |
| §2.2 U(c), §5.3 S_now/C_path | final_board is_core, buildup(레벨별) | `comps()` 전체 | buildup·final_board 모두 상점 챔피언만 있다 |
| §5.1 adj(c) | item_conditional, games, avg_place | CompStats | - |
| §5.4 items_ready / component_priority | composition(정렬 무관), 제작 불가 판별 | `recipe`, `is_craftable`, `item_category` | 4번 행 |
| §5.4 next_buildup_board | level_timing, buildup, levelling | CompStats | level_timing 최소 키 3(25덱)/4(11)/5(21). 설계에 공집합 규칙이 추가됐다 |
| §5.4 B(c) 정렬 | 챔피언 코스트 | `champion_cost` | - |
| §6.1 조합 후보 | (재료 a, b) → 결과, DA_·set_native | `craft`, `components` | 55쌍, 중복 0 |
| §6.3 st(x) | 보유자 × 아이템, 덱 한정 place_change와 games | `unit_item_stat(u, x, comp_id)` | carry × BIS 덱 한정 행 누락 0. final_board 유닛 × 아이템 984쌍 중 **12쌍(1.2%)은 덱 한정 행이 없다**(원본 itemNames 상위 N 컷). 이 경우 advisor는 0.5(중립)를 쓴다 → jev 2 |
| §4.1 shop_odds 라벨 폴백 | 레벨 1~10 확률 | `shop_odds` | 10개 레벨 모두 합이 100이다 |
| §4.2 Jev state 이름 | 영어 이름, 증강 desc_en | `name(id, "en")`, `augment` | - |

**설계 대비 부족한 조회: 없다.** `unit_stats`와 `item_stats`는 설계가 쓰지 않는 추가 제공분이다(표본 티어가 다르다는 점이 meta에 명시돼 있다).

## 담당자별 수정 요청 (모두 비차단)

### stats-researcher
1. **[W1, 우선]** 읽기 경로에서 DB에 쓰지 않도록 한다.
   - 방법: `db.py`에 읽기 전용 연결을 둔다. 예: `sqlite3.connect(f"file:{p}?mode=ro", uri=True)`. `list_snapshots`, `find_snapshot`, `read_snapshot`은 이 연결을 쓴다. `SCHEMA`와 `schema_info` 쓰기는 `write_snapshot`에서만 한다.
   - 재현: `.venv/bin/python -m pytest tests/test_stats_qa.py::test_open_repository_on_readonly_db`. 고치면 XPASS로 실패하므로 그때 `@pytest.mark.xfail` 마커를 지운다.
   - 이유: 앱이 DB를 여는 순간 refresh가 쓰는 중이면 잠금 경합이 생긴다. 읽기 전용 배포와 권한 환경에서도 열리지 않는다.
2. **[W2, 선택]** diff 개선.
   - (a) 덱별 증강 등급 변경(comp_id가 있는 AugmentTier)도 `augment_tiers.changed_by_comp`로 보고한다.
   - (b) old가 None이면 `patch_changed`를 False나 None으로 두고, 렌더링에 "(비교 대상 없음)"을 표시한다.
   - (c) 선택 사항: 같은 fetched_at으로 다시 적재할 때 diff의 비교 기준이 사라지는 점을 `info`/`diff` 도움말에 명시한다.
3. **[W4, 선택]** `recipe`/`is_craftable`의 대상을 `craft`와 같게 `DA_*`·set_native로 제한하거나, docstring에 레거시 `TFT_Item_*` 39개가 포함된다고 적는다. advisor 어댑터와의 의미 차이를 없애기 위해서다.
4. (참고) 보존 순서의 기준은 `built_at`이다. 그래서 `load old.json`을 하면 오래된 데이터가 "현재" 스냅샷이 되고, 더 새로 수집한 스냅샷이 밀려날 수 있다. 의도된 롤백 동작이라면 `load` 도움말에 적어 둔다.

### jev-strategist (advisor; 병렬 작업 중이므로 참고용)
1. (선택) advisor 테스트는 `load_stats(path)`, 즉 `JsonStatsAdapter`로 돌고 실제 실행은 `StatsRepository`로 돈다. 현재 advisor가 부르는 조회는 두 구현의 값이 같다(`tests/test_stats_qa.py`로 고정). 다만 의미가 갈리는 지점이 있다(W3).
   - `unit_item_stat(comp_id=None)`: repository는 파생 전체값을 주고, 어댑터는 None을 준다.
   - `shop_odds`의 범위 밖 레벨: repository는 ValueError, 어댑터는 `[]`를 준다.
   - `comps()` 순서: repository는 avg_place로 정렬하고, 어댑터는 파일 순서를 따른다. 현재 advisor는 자체 정렬을 하므로 영향이 없다.

   권장: advisor fixture 테스트를 `InMemoryStatsRepository.from_doc(mini)`로도 한 번 돌리도록 매개변수화한다. mini는 그대로 로드되는 것을 확인했다(11덱).
2. (선택) §6.3 st(x): final_board 유닛 × 아이템 중 1.2%(12/984)는 덱 한정 행이 없다. 지금은 이 경우 중립 0.5다. 파생 전체값을 폴백으로 쓰려면 `AdvisorStats.unit_item_stat`에 `fallback_overall` 키워드를 추가해야 한다(어댑터에도). 표본 편향 주의: 전체값은 여러 덱을 가중 평균한 값이다.

### app-integrator
1. (선택, stats 제안 재확인) `StatsCfg.keep_snapshots: int = 5`. 현재는 `write_snapshot(keep=5)`의 기본값에 들어 있다.

## 재실행
```
.venv/bin/python -m pytest -q -rxs                                   # 기대: 246 passed, 3 skipped(live), 1 xfailed(W1)
.venv/bin/python -m pytest -q -rxs tests/test_stats_qa.py             # 4 passed, 1 xfailed(W1)
.venv/bin/python _workspace/qa_scripts/stats_repo_check.py           # repository ↔ 원본 ↔ JSON ↔ SQLite ↔ 설계 + 속도 + 보존
.venv/bin/python _workspace/qa_scripts/converted_stats_check.py      # holds/builds/item_stats 로드 포함
.venv/bin/python _workspace/qa_scripts/id_crosscheck.py              # 선언-관측 차이 0
.venv/bin/python -m tft_advisor.stats refresh --no-fetch && .venv/bin/python -m tft_advisor.stats info
```
