# 28 stats-researcher: 스테이지별 보드 통계 소스 조사 + 수집·저장·조회 API

작성일 2026-09-24 / 작성자 stats-researcher. 요청 간격 1.2초, UA `tft-advisor-research/0.1 (personal use)`(settings `[stats]`).
직접 요청해 확인한 것만 "확인"으로 적었다. 나머지는 "미확인"이다. advisor/와 app/은 건드리지 않았다. 커밋은 하지 않았다.

## 0. 결론

1. **MetaTFT "Early Comps"가 스테이지별 보드 성적을 표본 수와 함께 주는 유일한 소스다(확인).** 공개 JSON이고 인증이 없으며 robots.txt는 전체 허용이다.
   - 데이터는 MetaTFT 데스크톱 앱이 게임 중 기록한 **스테이지 2·3·4·5의 실제 보드**다.
   - 스테이지마다 보드를 클러스터로 묶는다(35/47/49/49개).
   - 제공하는 것: 클러스터별 정확한 보드(variations), 유닛×성급 성적, 다음 스테이지로의 전이, 라운드별 보드 인원 성적.
2. Riot 매치 API는 최종 보드만 준다. 그래서 tactics.tools, lolchess.gg 통계, OP.GG 통계는 모두 **게임 종료 시점** 기준이다. 스테이지 해상도는 MetaTFT(앱 수집)에만 있다.
3. 구현을 마쳤다: 수집기, 변환, SQLite 스냅샷(`source="metatft_early"`), `refresh` 경로, 조회 API(`repo.stage_stats`), 테스트 34개.
   - 현재 로컬 DB는 스냅샷 #2(18.3, 보드 1,471행, 유닛×스테이지 956행)다.
4. 주의할 점이 네 가지 있다.
   - **top4는 소스에 없다.** 쓸 수 있는 지표는 avg_place(최종 등수), win_rate(1등 비율), round_win_rate(그 스테이지 전투 승률)다.
   - 표본은 앱 사용자라서 스테이지 기준선이 4.5가 아니다. 스테이지 2~4는 4.40~4.44이고, 스테이지 5는 생존자만 남아 3.66이다. 그래서 반드시 **`delta`(같은 스테이지 기준선 대비)**로 비교해야 한다.
   - 오늘(2026-09-24 18:22Z) **패치 18.3b**가 시작됐다. Early 데이터의 집계 시각(18:07Z)은 그보다 앞이므로 18.3으로 라벨링했다(`report.patch_note`). 본 통계(`metatft_18.3.json`)도 18.3이라 서로 맞는다. 18.3b 표본이 쌓이면 1~2일 뒤 `refresh`하기를 권한다.
   - Riot 서드파티 정책상 게임 상태에 따른 실시간 추천은 금지 항목이다. 사용자가 이를 알고 개인용으로 쓰기로 선택했다(CLAUDE.md). 이 수집은 그 전제 위에서 한다.

## 1. 소스 비교

| 소스 | 해상도 | 필드 | 표본 | 최신성 | 접근 | 판정 |
|---|---|---|---|---|---|---|
| **MetaTFT Early Comps** `api.metatft.com/tft-early-comps/comps_overview`, `comps_full?stage&cluster_id&clustering_id` | **스테이지 2/3/4/5**. 클러스터 안 `num_units[n].rounds["3-2"]`는 라운드 단위(보드 인원별) | 보드(유닛 CSV), matchup_count/winrate/avg_hp_delta, final_place_count/avg/winrate, avg_hp. 유닛별 1/2/3성, 유닛별 아이템·위치, 정확한 보드 variations, 전이(to/from, latest), 랭크·서버별 | 스테이지 2: 161,530전투 / 80,759 참가자-게임. 스테이지 3: 369,712. 스테이지 4: 388,130. 스테이지 5: 299,304. 현재 패치 몫은 95.8% | clustering 2694는 2026-09-24T14:52Z에 생성됐고 lastUpdated는 18:07Z다. 페이지는 15분 캐시다(갱신 주기 미확인). 기간(days) 파라미터는 없다 | 공개 JSON, 인증 없음, robots 전체 허용. overview 약 0.9MB, full은 요청당 약 0.3MB × 180 | **채택(1순위)** |
| MetaTFT comp_details `early_options{4..7}` / `options{7..10}` / `levels` (기존 수집) | **레벨** 4~10, 덱별 상위 10개 | unit_list, count, avg, win | 덱별 수십~수백 판 | 본 통계와 같다 | 이미 `CompStats.buildup`에 있음 | 보조(덱별 레벨 보드). 그대로 둔다 |
| MetaTFT explorer `/tft-explorer-api/*` | 최종 레벨(`level=8-any`) | placement_count | 크다 | 1시간 | 공개 | 스테이지 해상도 없음 |
| tactics.tools `team-compositions`, `d3.tft.tools/stats2/unit` | 최종 보드. `levels`는 최종 레벨 분포 | place, top4, win, count | 크다 | 일 단위 | 공개(`__NEXT_DATA__`/JSON) | 스테이지 해상도 없음(확인). 최종 덱 교차검증용 |
| OP.GG MCP `tft_list_meta_decks` | 덱별 `early`(레벨 5), `middle`(레벨 7) 보드 1개씩 | play/win/lose | 덱 10개 | `gameStatDateTime`이 오래됨(01 보고서) | MCP 공개 | 보조. 덱 10개, 레벨 2개뿐 |
| lolchess.gg 메타 덱 가이드 | 초/중/후반 보드(**큐레이션**) | 없음 | 없음 | 패치 단위 | `/meta`는 202 빈 응답(봇 차단 추정). 더 시도하지 않음 | 미확인. 큐레이션 폴백 후보 |
| tftacademy / Mobalytics 가이드 | 초/중반 보드(**큐레이션**) | 없음 | 없음 | 패치 단위 | Mobalytics robots는 `/api/tft`를 Disallow한다. tftacademy는 조사하지 않음 | 쓰지 않음(통계 소스가 있으므로) |

권장: MetaTFT Early Comps를 1순위로 쓴다. 덱별 레벨 보드는 기존 `CompStats.buildup`(MetaTFT comp_details)을 함께 쓴다. 큐레이션 보드는 필요 없다.

## 2. Early Comps 필드 해석(검증)

- 클러스터는 스테이지 표본을 **정확히 분할**한다. 클러스터 matchup_count 합과 final_place_count 합이 스테이지 stats·latest_stats와 네 스테이지 모두에서 같다(확인). 그래서 모든 클러스터의 `comps_full.units`를 합한 값은 "그 스테이지에 유닛 u를 보드에 올린 판" 전체와 같다.
  - overview의 `units`는 클러스터 핵심 유닛만 담는다. 유닛 통계에는 full이 필요하다.
- 지표 매핑:
  - games = `final_place_count`(참가자-게임)
  - rounds = `matchup_count`(전투)
  - avg_place = `final_place_avg`
  - win_rate = `final_place_winrate`(1등 비율. 스테이지 2 기준선 0.128 ≈ 1/8)
  - round_win_rate = `matchup_winrate`(기준선 0.5)
  - avg_hp_delta = `matchup_avg_hp_delta`
- 패치 범위:
  - 클러스터 행: `latest_stats`(현재 패치)를 쓴다.
  - variations, 유닛×성급: 소스가 기간 전체 값만 준다(현재 패치 몫 95.8%).
  - 전이: `transition_to_stats_latest`를 쓴다.
- 전이 값은 소수(가중 카운트)다. 스테이지 사이를 추적할 수 있는 판만 들어간다(예: 클러스터 약 2.3만 전투 중 약 3천).
- ID는 `TFT18_Ahri` 형식이다. 상점 풀 챔피언의 name_en으로 canonical ID에 매핑하며, 65개가 모두 매핑된다(미매핑 0).
  - 예: Pebbles→`DA_18_Sentry`, MamaBeak→`DA_CrimsonRaptor18`, Lux_Base→`DA_Lux18_Base`, Gnar→`DA_18_GnarSmall`.

실제 값 예(스테이지 3, 표본 ≥ 500 클러스터 중 1위):
- 알리스타·르블랑·오른·렉사이·베이가 보드. delta −0.39, 12,143판, 전투 승률 0.60, `spellweaver-veigar` 연결 0.86.
- 같은 스테이지 유닛 delta 상위(표본 ≥ 1,000): 니달리 −0.39, 아무무 −0.32, 아펠리오스 −0.27.

## 3. 구현

| 파일 | 내용 |
|---|---|
| `src/tft_advisor/stats/collectors/metatft_early.py` (신규) | `collect_early_raw(out, full="all"\|N\|0)`. 순차 요청, 간격 1초 이상, 대체 호스트 `api2.metatft.com`으로 1회 재시도, 캐시 재사용, `manifest.json`. `patch.json`은 api-hc에서 받는다 |
| `src/tft_advisor/stats/collectors/metatft.py` | `Fetcher(hosts=...)` 인자 추가(기본값 기존과 같음) |
| `src/tft_advisor/stats/early_convert.py` (신규) | 원본 → `stage_baseline / stage_boards / unit_stage_stats / stage_transitions / stage_round_sizes`. ID 매핑, 미매핑 기록, `patch_for_source`(집계 시각이 현재 패치 시작보다 앞이면 직전 패치로 라벨링) |
| `src/tft_advisor/stats/db.py` | 스키마 v3: 위 5개 테이블(키 열 + json)을 추가했다. `STAGE_TABLES`. 내용 해시는 스테이지 테이블이 비어 있으면 v2와 같아서 기존 스냅샷의 dedupe가 유지된다. `read_snapshot`은 테이블이 있을 때만 키를 넣는다 |
| `src/tft_advisor/stats/stage_stats.py` (신규) | 조회 API(4절). 최종 덱 연결(comp_links)은 **로드 시점의 comps**로 계산한다. 따라서 comp_id가 바뀌어도 오래된 연결이 남지 않는다 |
| `src/tft_advisor/stats/repository.py` | `repo.stage_stats`, `attach_stage_doc`, 위임 `boards_for` / `unit_stage_stat`, `load_stage_doc`. `open_repository(stage_stats=True)`가 최신 metatft_early 스냅샷을 붙이고, 실패하거나 없으면 빈 객체를 붙인다. Protocol `StatsRepository`는 바꾸지 않았다 |
| `src/tft_advisor/stats/refresh.py`, `__main__.py` | `refresh_stages()`, CLI(5절) |
| `tests/test_stage_stats.py` (신규, 34개) + `tests/fixtures/stats/metatft_early/` (녹화 응답 14파일, 229KB) | 네트워크 없음 |

### 데이터 파일과 커밋 정책
- `data/raw/metatft_early/2026-09-24/`: **58MB**(comps_full 180개). `data/raw/`가 gitignore라 커밋하지 않는다.
- `data/stats/stage_boards_18.3.json`: **1.2MB**. `metatft_{patch}.json`과 같은 정책을 권한다(커밋 대상). 새 PC에서 DB 없이 `open_repository`가 폴백으로 읽는다.
  - 파일명을 `metatft_` 접두사로 짓지 않았다. 그렇게 하면 `latest_snapshot(dir, "metatft_")`와 advisor `stats_source.latest_json`의 `metatft_*.json` glob이 이 파일을 "패치 early_18.3"으로 읽어 최신으로 고르기 때문이다(테스트로 고정).
- `data/stats/stats.sqlite`: 스냅샷 #2(`metatft_early`, 18.3). gitignore다.

## 4. 조회 API (jev-strategist용)

```python
repo = open_repository()          # 기존과 같음. repo.stage_stats가 붙는다(없으면 빈 객체 → 모든 조회가 None/[])
st = repo.stage_stats             # tft_advisor.stats.stage_stats.StageStats
```

| 호출 | 반환 | 의미 |
|---|---|---|
| `st.stage_of("3-2" \| 3, *, level=None)` | `int \| None` | 데이터 스테이지 2~5. 1-x는 2로, 6 이상은 5로 올리거나 내린다. `level=L`이면 보드 인원 L이 가장 많이 관측된 스테이지다(실측 {≤4:2, 5·6:3, 7·8:4, 9:5}) |
| `st.stage_baseline(stage \| level=)` | `StageBaseline` | 스테이지 기준선(avg_place, win_rate, round_win_rate, games, rounds) |
| `st.boards_for(stage=None, *, level=None, comp_id=None, kind=None, min_games=100, min_link=0.15, limit=None)` / `repo.boards_for(...)` | `list[StageBoard]` | 스테이지 보드를 avg_place 오름차순(같으면 games 내림차순)으로 준다. `level`을 주면 **유닛 수 == level**인 보드만, `comp_id`를 주면 그 최종 덱으로 이어질 확률이 min_link 이상인 보드만, `kind`는 `"cluster"`(대표, 현재 패치) 또는 `"variation"`(정확한 보드, 표본 ≥ 30) |
| `st.unit_stage_stat(unit, stage \| level=, star=None)` / `repo.unit_stage_stat(...)` | `UnitStageStat \| None` | 그 스테이지에 unit을 **보드에 올린** 참가자-스테이지의 성적. star=None이면 전 성급을 합한 값 |
| `st.unit_stage_stats(stage \| level=, min_games=100)` | `list[UnitStageStat]` | 스테이지 유닛 표, delta 오름차순 |
| `st.rank_units(unit_ids, stage \| level=, stars={uid: 성급}, min_games=50)` | `list[(uid, UnitStageStat \| None)]` | **보유 유닛 배치 우선순위**. delta가 좋은 순이고, 통계가 없는 유닛은 뒤에 None으로 둔다. stars를 주면 그 성급 행을 먼저 쓰고, 표본이 부족하면 전 성급 행을 쓴다. 중복 ID는 제거한다 |
| `st.cluster_for(board_units, stage)` | `(StageBoard, jaccard) \| None` | 지금 보드와 가장 닮은 클러스터 |
| `st.transitions(stage, cluster, min_games=20)` | `list[StageTransition]` | 그 클러스터에서 다음 스테이지 클러스터로 간 비율과 그 경로의 avg_place. 다음 보드는 `st.cluster_board(stage+1, next_cluster)`로 얻는다 |

StageBoard 필드:
- `stage, kind, cluster, units(tuple, canonical, 정렬), size`
- `avg_place, win_rate, round_win_rate, avg_hp_delta, avg_hp, games, rounds, share`
- `delta`(avg_place − 기준선. 음수가 좋다)
- `comp_links((comp_id, p), …)`, `link(comp_id)`

UnitStageStat 필드: `unit_id, stage, star, avg_place, win_rate, round_win_rate, avg_hp_delta, games, rounds, pick_rate, delta`.

comp_links 계산:
- 스테이지 5 클러스터: 최종 보드와 Jaccard가 최대인 덱이다(0.4 이상, 동률이면 균등 분배).
- 스테이지 2~4: 전이 비율 × 다음 클러스터의 연결을 재귀로 계산한다. 연결되지 않은 경로의 몫은 정규화하지 않고 뺀다. 그래서 확률 합이 1 이하다.
- 전이가 없으면: 클러스터 유닛 중 최종 보드에 든 비율이 0.6 이상인 덱을 쓴다.
- 실데이터 180개 클러스터가 모두 연결된다.

### board_plan 적용 제안 (advisor 소관이라 코드는 건드리지 않음)
1. **벤치↔보드 교체의 유닛 점수 보정**: `rank_units(보드+벤치 후보, stage=현재 스테이지, stars=성급)`의 `delta`를 목표 덱 적합도에 더하는 작은 항으로 쓴다. 표본 신뢰도는 games 기반 감쇠를 적용한다(예: games/(games+500)).
2. **스테이지 목표 보드**: `boards_for(stage, level=현재 레벨, comp_id=목표 덱, kind="variation")`의 1위와 보유 유닛의 교집합이 크면 그 보드를 "지금 올릴 구성"으로 제시한다. 없으면 `kind="cluster"` 또는 `comp_id=None`으로 넓힌다. 덱별 레벨 보드(`repo.comp(c).buildup[level]`)는 이미 있으니 둘을 병행한다.
3. **피벗 신호**: `cluster_for(현재 보드, stage)` 후 `transitions(...)`의 다음 클러스터들의 avg_place를 비교한다. 목표 덱과 연결이 약하고 다른 경로가 크게 좋으면 알린다.
4. 비교는 반드시 같은 스테이지의 `delta`로 한다. 스테이지 5는 생존자 편향이 크고(기준선 3.66), 연승 보드일수록 좋아 보이는 선택 편향이 있다. 따라서 이 값은 인과 효과가 아니다. 가중치를 작게 시작하라고 권한다.

## 5. 갱신 CLI

```
python -m tft_advisor.stats refresh                      # 본 통계 + 스테이지 보드(기본 --stages include)
python -m tft_advisor.stats refresh --stages skip        # 기존 동작(본 통계만)
python -m tft_advisor.stats refresh --stages only        # 스테이지 보드만(= refresh-stages)
python -m tft_advisor.stats refresh-stages [--no-fetch] [--date D] [--raw DIR] [--refresh-raw] [--full all|N] [--db PATH]
python -m tft_advisor.stats.collectors.metatft_early [--full all|N]   # 원본만 받기
```
- `--full all`(기본)은 약 182요청으로, 1.2초 간격이면 약 4분이 걸린다. `N`은 스테이지별 표본 상위 N개 클러스터만 받는다. 이 경우 유닛 스테이지 성적이 불완전하며 `report.unit_coverage="partial"`로 표시되고 notes에 기록된다.
- 내용이 같으면 새 스냅샷을 쓰지 않는다(dedupe). 보존 개수는 `settings.stats.keep_snapshots`를 따르며 출처별로 센다.

## 6. 테스트

- `tests/test_stage_stats.py` 34 passed. 다루는 내용:
  - ID 매핑 13케이스
  - 변환: latest_stats 사용, variation 필터, 유닛 가중합 수작업 검증, 전이 정규화, 패치 라벨
  - DB: 왕복, dedupe, **본 스냅샷 해시 불변**
  - 조회: 정렬·필터·레벨·comp 연결·rank_units·cluster_for·빈 객체
  - `refresh_stages`/CLI `--no-fetch`, `open_repository` 결합, `metatft_` 최신 파일 선택에 끼지 않음
  - 가짜 Fetcher로 수집기 top-N·호스트·캐시 확인
- 전체: `.venv/Scripts/python.exe -m pytest -o addopts="" -q` → 1298 passed, 3 skipped, 1 xfailed, **5 failed**. 실패 5건은 모두 stats와 무관한 app/credentials/config 테스트다.
  - `test_api_key`, `test_setup`, `test_boundaries::test_config_accepts_design_keys`(subprocess 출력 UTF-8 디코드 오류), `test_credentials` 권한 2건
  - Windows 환경에서 알려진 실패, 그리고 다른 에이전트가 수정 중인 파일이다.

## 7. 전달 사항

- **app-integrator (선택)**: 계약에 `StageBoard`/`UnitStageStat`를 넣을지 결정해 달라. 지금은 stats 내부 frozen dataclass다. Recommendation에 싣는다면 계약화가 필요하다. `StatSource`에 `metatft_early`를 추가할지도 함께 정해 달라(DB의 source 열은 자유 문자열이라 지금은 필요 없다).
- **오케스트레이터**: 18.3b가 시작됐다. 1~2일 뒤 `refresh`(본 통계 + 스테이지)를 다시 돌려 달라. `stage_boards_18.3.json`(1.2MB)을 커밋할지 결정해 달라.
- **qa-validator**: `stage_boards`의 유닛 ID가 정적 데이터와 1:1인지는 테스트로 고정했다. 실데이터 미매핑은 0이다.
