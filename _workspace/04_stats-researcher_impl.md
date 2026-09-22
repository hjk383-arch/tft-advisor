# 04 stats-researcher: Phase 3 구현 (stats 조회 API + 갱신 CLI + SQLite)

작성일: 2026-09-22 / 작성자: stats-researcher / 기준 계약: CONTRACT_VERSION 0.2.0 (변경 없음)

## 0. 요약

| 항목 | 상태 |
|---|---|
| 조회 API `StatsRepository` (Protocol + 구현 + `open_repository()`) | 완료. `src/tft_advisor/stats/repository.py` |
| 저장 형식 | **SQLite** `data/stats/stats.sqlite`(= `settings.stats.db_path`, gitignore) + 사람이 읽는 JSON `data/stats/metatft_{patch}.json` 유지 |
| 갱신 CLI | `python -m tft_advisor.stats refresh`(별칭 `update`) / `load` / `info` / `diff` |
| 패치 diff | 이전 스냅샷과 비교. 패치가 바뀌면 `_workspace/patch_{ver}_diff.md` 자동 작성 |
| QA WARN 2 (buildup의 비챔피언 유닛) | 해결. strict xfail 마커 제거, 테스트 PASS |
| unmapped.json 오래된 항목 5개 | 해결. 선언 목록과 관측 목록의 차이 0 |
| 수집기가 `settings.stats` 읽기 | 해결(`refresh`, 수집기 `main` 둘 다) |
| QA WARN 1 (BIS에 유물·찬란한 아이템) | 데이터는 그대로 두고, 판별용 `is_craftable` / `item_category`를 추가 |
| pytest | **120 passed**, xfail 0, skip 0. 이 중 신규 30개: repository 19, refresh 11 |

## 1. 조회 인터페이스 (jev-strategist가 쓰는 부분)

```python
from tft_advisor.stats.repository import open_repository, StatsRepository, InMemoryStatsRepository, StatsNotFound
repo = open_repository()          # 프로세스당 1회. SQLite 최신 스냅샷을 메모리에 올린다(~1.5s, import 포함)
```

- 로드가 끝나면 모든 조회는 dict 조회다(I/O 없음). 덱 57개 × 보드 × 아이템 전체 조회에 약 1ms가 걸린다.
- 조회에 실패하면 예외 대신 `None`이나 빈 컬렉션을 돌려준다. 예외는 `shop_odds`의 범위 밖 레벨(ValueError) 하나뿐이다.
- 반환 모델은 모두 contracts 타입이다. 여러 곳에서 같은 객체를 공유하므로 수정하면 안 된다.
- `advisor/stats_source.py`의 `AdvisorStats` Protocol은 이 인터페이스의 부분집합이다. `isinstance(open_repository(), AdvisorStats)`가 True임을 확인했다.

| 메서드 | 반환 | 의미 |
|---|---|---|
| `meta` | `StatsMeta` | source, patch, set_number, fetched_at, built_at, rank_filter_units(마스터 이상), rank_filter_comps(None = 전 티어), snapshot_id, counts |
| `comps(*, min_games=None)` | `list[CompStats]` | avg_place 오름차순, 같으면 games 내림차순. min_games 미만은 뺀다 |
| `comp(comp_id)` / `comp_by_cluster("424000")` | `CompStats \| None` | |
| `augment_tier(aid, comp_id=None)` | `AugmentTier \| None` | **정확한 범위만** 본다. None이면 전체 등급, comp_id를 주면 그 덱 등급만 |
| `augment_tier_for(aid, comp_id)` | `AugmentTier \| None` | 설계 ed(a)·t(a,c)용. 덱별 등급 → 전체 등급 → None(미평가, 중립값) 순으로 찾는다 |
| `augment_tiers(comp_id=None)` | `dict[aid, AugmentTier]` | 전체 258개, 덱별 등급은 32개 덱 |
| `comps_with_augment_tiers()` | `frozenset[str]` | |
| `unit_stats(uid)` / `all_unit_stats()` | `UnitStats` | 69개(마스터 이상) |
| `unit_item_stat(uid, item, comp_id=None, *, fallback_overall=False)` | `UnitItemStats \| None` | "그 덱에서 uid가 item을 (다른 아이템과 함께) 든 판"의 place_change. 음수가 좋다. **comp_id=None이면 파생값**(2절) |
| `unit_item_stats(uid, comp_id=None)` | `list[UnitItemStats]` | 아이템 1개 행 전부. games 내림차순 |
| `unit_builds(uid, comp_id)` | `list[UnitItemStats]` | 덱 한정. **아이템 구성이 정확히** 1~3개인 빌드 행 |
| `item_stats(item)` | `PlacementStats \| None` | 아이템 전체 성적(마스터 이상, 보유 유닛 무관). 142개 |
| `champion / is_champion / champion_cost / champion_traits` | | `is_champion`은 상점 풀만 True다. 소환물·허수아비·크립은 False |
| `trait / trait_breakpoints` | | breakpoints의 null은 제거한다 |
| `item / item_category / is_component / is_craftable / recipe / craft / components` | | 3절 |
| `emblem_trait(item)` | `TraitId \| None` | 상징 21개를 모두 매핑했다. 예: Ravager Emblem → `DA_18_Slayer` |
| `augment / augment_traits / shop_special / name(id, "ko"\|"en")` | | |
| `shop_odds(level)` | `list[int]` (5개, %) | 1~10레벨. 7레벨은 다수 출처의 값을 쓴다(meta의 conflicts 참고) |

테스트나 가짜 데이터에는 `InMemoryStatsRepository.from_doc({"comps": [...], ...})`를 쓴다. comps만 넣어도 동작한다. JSON 파일에서 만들 때는 `InMemoryStatsRepository.from_json(path)`를 쓴다.

## 2. 통계 의미 (advisor 주의)

- **유닛+아이템을 두 종류로 나눴다(R16, 신규).** 기존 `unit_item_stats`(41,372행)에는 뜻이 다른 두 원본이 섞여 있었다. 그래서 같은 (유닛, 덱, 아이템) 키가 4,980개 중복됐다.
  - `unit_item_stats` 24,274행: MetaTFT `itemNames[].units[]`에서 온다. "아이템 x를 든 판"이며, 설계 §7 holder의 place_change는 이쪽을 쓴다.
  - `unit_build_stats` 17,098행: `builds[]`에서 온다. "구성이 정확히 이 아이템들인 판"이다.
  - JSON 키와 SQLite `unit_items.kind`('holds' / 'build')로 구분한다. 계약 모델은 그대로 `UnitItemStats`다.
  - `advisor/stats_source.py`의 JSON 어댑터도 `unit_item_stats`를 읽으므로, 이제 중복 없이 올바른 뜻의 값을 받는다.
- **전체(comp_id=None) 유닛+아이템 값은 소스에 없다.** 그래서 덱 한정 'holds' 행을 games로 가중 평균해 만든 **파생값**이다. games는 합계이고, avg_place와 place_change는 가중 평균이다.
  - 덱 한정 행이 우선이다. 폴백이 필요할 때만 `fallback_overall=True`를 쓴다.
  - 모든 덱에서 carry × carry_bis_items 조합의 덱 한정 행이 있다(누락 0).
- 표본의 티어가 다르다. CompStats, 덱별 AugmentTier, UnitItemStats는 전 티어이고, UnitStats와 item_stats는 마스터 이상이다(`meta` 참고). 두 표본을 섞어 비교하면 안 된다.
- AugmentTier는 여전히 편집자 등급뿐이다(games=None).

## 3. QA WARN 1: 제작할 수 없는 BIS (데이터는 그대로 두고 판별 함수 추가)

- carry_bis_items에 조합할 수 없는 아이템이 들어 있는 덱은 **57개 중 13개**다. 고유 아이템은 11개다.
  - 유물 8개: Dawncore, BlightingJewel, HellfireHatchet, LichBane, NavoriFlickerblade, RapidFireCannon, SilvermereDawn, WitsEnd
  - 찬란한 아이템 2개: GuinsoosRagebladeRadiant, SpearOfShojinRadiant
  - 증강 상징 1개: `DA_18_EmblemFloraFatalisAugment`
- 이 아이템들은 데이터에서 빼지 않는다. 보유하고 있으면 가장 좋은 선택이기 때문이다.
- `repo.is_craftable(x)`의 판정: category가 completed, emblem, tactician 중 하나이고 composition이 재료 2개이면 True다. 유물, 찬란한 아이템, 조합표가 없는 상징(Coven, Defender, Juggernaut, FloraFatalis 등), 미매핑 아이템(`DA_Artifact_Hullcrusher`)은 False다.
- `repo.item_category(x)`는 artifact, radiant, emblem 등을 알려준다. 미매핑 아이템은 None이다.
- **jev-strategist 권장 처리** (QA recheck 03 5절 jev-1)
  1. items_ready의 보유 풀 P에 `ItemState.others` 중 category ∈ {artifact, radiant}인 아이템을 넣는다. 보유하고 있으면 owned로 판정한다.
  2. `not is_craftable(x)`인 BIS는 craftable 판정과 `component_priority` 계산에서 건너뛴다. 필요한 재료를 정의할 수 없기 때문이다. 보유하지 않았으면 missing으로 표시한다.
- stats R8(BIS 선택 규칙)은 바꾸지 않았다. QA가 제안한 3번 선택지 대신 "설계에서 처리"하는 쪽을 택했다.
- 조합표 관련 함수:
  - `recipe(x)`는 정렬된 재료 2개를 돌려준다.
  - `craft(a, b)`는 순서와 무관하다. `DA_*`이면서 set_native인 아이템만 대상이다(55개 조합, 결과 중복 0).
  - `components()`는 `DA_Component_*` 10개를 돌려준다.

## 4. 저장 형식 결정: SQLite (런타임) + JSON (중간 산출물)

- **SQLite `data/stats/stats.sqlite`** (gitignore, `settings.stats.db_path`와 같음)
  1. 설정·스킬·오케스트레이터가 이미 이 경로를 기준으로 한다. 계약이나 설정을 바꿀 필요가 없다.
  2. 파일 하나에 여러 스냅샷(출처 × 패치 × 수집 시각)을 둘 수 있다. 덕분에 패치 diff, 이전 스냅샷의 comp_id 재사용(R12), 롤백이 쉽다. 기본으로 최근 5개를 보존한다.
  3. 적재가 트랜잭션 1개라 원자적이다. 갱신이 실패해도 앱이 읽는 이전 스냅샷은 그대로 남는다.
  4. 표준 라이브러리라서 의존성이 늘지 않는다. pyproject의 `stats` extra는 바꾸지 않았다.
- **행 구조:** 계약 모델의 JSON을 `json` 열에 그대로 넣고, 조회와 diff에 쓰는 키 열만 따로 뺐다. CompStats는 중첩 구조(buildup, level_timing 등)라서 정규화해도 advisor가 결국 객체 전체를 쓴다. 그래서 정규화의 이득이 없다고 판단했다.
- **런타임 동작:** 스냅샷 1개를 메모리에 전부 올린다. 행 약 44k개, 로드 약 1.5s, 이후 조회는 μs 단위다. SQLite 질의를 실시간 경로에 두지 않는다.
- **JSON `data/stats/metatft_{patch}.json`은 계속 만든다.** 사람이 읽고 diff하는 산출물이고, QA 테스트와 스크립트의 입력이며, DB가 없을 때 `open_repository`가 폴백으로 읽는다.
- 스키마: `src/tft_advisor/stats/db.py` 상단 docstring. 테이블은 snapshots, comps, augment_tiers, unit_stats, unit_items, item_stats이며 `schema_info.schema_version=1`이다.

## 5. 갱신 CLI

```
python -m tft_advisor.stats refresh              # 수집(settings.stats) → 변환 → JSON + SQLite → diff
python -m tft_advisor.stats refresh --no-fetch   # 네트워크 없이 data/raw/metatft 최신 캐시만 변환
    [--date YYYY-MM-DD] [--raw DIR] [--refresh-raw] [--db PATH] [--diff-out auto|none|PATH]
python -m tft_advisor.stats load [metatft_X.json] [--db PATH]
python -m tft_advisor.stats info [--db PATH]
python -m tft_advisor.stats diff [--old ID] [--new ID] [--out PATH]
```

- **수집:** 순차 요청이다. 간격, User-Agent, days, rank는 모두 `settings.stats`에서 읽는다(request_interval_s ≥ 1.0은 config 검증기와 Fetcher 양쪽에서 강제). 실패하면 대체 호스트로 1회 재시도한다. 이미 받은 파일은 다시 요청하지 않는다(`--refresh-raw`로 강제할 수 있다). 수집에 실패하면 종료 코드 1이다.
  - 수집기 단독 실행 `python -m tft_advisor.stats.collectors.metatft`도 기본값을 settings에서 읽는다.
- **comp_id 유지:** DB의 직전 스냅샷(없으면 최신 JSON)의 최종 보드를 `--previous`로 넘긴다. 클러스터가 재계산돼도 Jaccard ≥ 0.75인 덱은 comp_id를 그대로 유지한다.
- **diff 내용:** 덱 추가·제거, 덱 변경(avg_place ±0.10 이상, carry, BIS, levelling, 최종 보드), 전체 증강 등급 변경, 유닛·아이템 평균 등수 ±0.10 이상 변화, 신규 미매핑 ID. 패치가 바뀌면 `_workspace/patch_{ver}_diff.md`를 자동으로 쓴다.
  - 정적 데이터(CDragon) 갱신은 여전히 `static_extract.py`를 따로 실행한다. diff에 신규 미매핑이 나오면 정적 데이터를 갱신하라는 신호다.
- 현재 로컬 DB: `data/stats/stats.sqlite` 스냅샷 #1 (18.2b, 2026-09-22 캐시).

## 6. QA 미결 항목 처리

| 항목 | 처리 |
|---|---|
| WARN 2 / N6 비챔피언 유닛 | `SUMMON_IDS`에 `DA_Elderwood18_Protector`, `DA_TheTower_TrainingDummy`, `DA_TrainingDummy`를 추가했다. 그 밖에 `champion_filter`(상점 풀 챔피언만 통과)를 buildup에 적용해, 새 비챔피언 ID가 생겨도 걸러지고 report `non_champion_units_dropped`에 기록된다. `test_converted_buildup_units_are_champions`의 strict xfail을 제거했고 PASS다. unmapped.json `summons_in_boards`에 3개를 선언했다 |
| unmapped 오래된 항목 5개 | `augments.metatft_augments_tiers`에서 OP.GG로 보완된 5개를 `resolved_by_opgg`로 옮겼다. `opgg_only_DA` 키는 삭제했다(참조처 없음). checked_at은 2026-09-22다. `id_crosscheck.py` 결과, 선언과 관측이 units·augments 모두 차이 0이다 |
| 수집기 하드코딩 | `refresh.collect_kwargs(settings)`와 수집기 `main`이 모두 `settings.stats`를 쓴다. 테스트 2개로 고정했다 |
| WARN 1 | 3절 |

## 7. 변경 파일

- 신규:
  - `src/tft_advisor/stats/repository.py`: Protocol, 구현, open_repository
  - `db.py`, `diff.py`, `refresh.py`, `__main__.py`
  - `tests/test_stats_repository.py`, `tests/test_stats_refresh.py`
- 수정:
  - `src/tft_advisor/stats/metatft_convert.py`:
    - SUMMON_IDS와 champion_filter 추가
    - R16 holds/build 분리
    - R17 `item_stats` 추가(raw items.json places)
    - report에 `non_champion_units_dropped`, `raw_dir`, `fetched_at` 추가
  - `stats/collectors/metatft.py`: `main`의 기본값을 settings에서 읽는다
  - `stats/__init__.py`: docstring
  - `data/static/18/unmapped.json`
  - `tests/test_converted_stats.py`: xfail 마커 제거만
- 데이터:
  - `data/stats/metatft_18.2b.json` 재생성. `unit_build_stats`와 `item_stats` 키를 추가했고, `unit_item_stats`는 holds 행만 남겼다
  - `data/stats/stats.sqlite` 신규
- 건드리지 않은 것: contracts.py, config.py, TOML, pyproject.toml, advisor/, vision/

## 8. 테스트

`.venv/bin/python -m pytest -q -rxs` → **120 passed** (34s), xfail 0, skip 0. 네트워크를 쓰지 않는다. 수집 함수는 monkeypatch로 가짜로 바꾸고, 변환은 `data/raw/metatft/` 최신 캐시로 한다. 원본 캐시가 없으면 해당 테스트는 skip된다.

- `test_stats_repository.py`(19):
  - SQLite 로드와 메타, SQLite ↔ JSON 왕복, JSON 폴백과 StatsNotFound, 최소 dict로 만든 가짜 저장소
  - 덱 정렬과 min_games, cluster 조회, buildup이 모두 챔피언인지
  - 증강 등급 범위와 폴백
  - UnitStats, carry × BIS의 덱 한정 행 전수 확인, 전체 파생값의 가중 평균 검증과 fallback, 정확한 빌드, item_stats
  - 카테고리와 조합 가능 여부(WARN 1), 조합표 왕복, 상징 → 특성, shop_odds, 정적 조회
- `test_stats_refresh.py`(11):
  - settings → 수집 인자(refresh와 수집기 main), Fetcher 최소 간격
  - `--no-fetch` 적재, 같은 스냅샷 재적재 시 교체와 빈 diff, 보존 개수
  - 패치가 바뀔 때 diff 파일 자동 생성, diff 변경 검출과 markdown
  - CLI info, diff, load

## 9. 계약·설정 변경 요청 (app-integrator) — 필수 없음, 모두 선택 사항

1. (선택) `StatsCfg.keep_snapshots: int = 5`. 지금은 `db.write_snapshot(keep=5)` 기본값에 들어 있다.
2. (선택) `UnitItemStats.kind: Literal["holds","build"] | None`. 지금은 컬렉션을 분리하고 DB에 `kind` 열을 두는 것으로 충분하다. 한 리스트에 섞어 넘겨야 할 때만 필요하다.
3. (참고) `static_data.StaticData.observed_shop_odds()`는 관측된 3개 레벨만 돌려준다. 1~10 전체 확률은 `repo.shop_odds(level)`(meta `shop_odds_pct`)로 제공한다. QA가 app-integrator에 요청했던 `shop_odds(level)` 접근자는 이것으로 충족된다.

## 10. 남은 한계

- 증강 성적 통계는 여전히 없다(편집자 등급만 있음).
- 유닛+아이템 전체(comp_id=None) 값은 파생값이다. 소스의 전체 unit-item 엔드포인트는 아직 조사하지 않았다.
- 로드에 약 1.5s가 걸린다(import 포함, unit-item 44k행을 사전 검증). 앱 시작 때 1회만 발생한다. 줄이려면 `preload=False`(첫 조회 때 유닛별로 검증)를 쓸 수 있다.

## Fix round (Phase 3 QA 후속, 2026-09-22)

기준: `_workspace/04_qa_stats.md`(W1/W2/W4), `04_qa_vision.md` R4/R5. 수정 범위: `src/tft_advisor/stats/`, `static_data.py`, `data/static/18/meta.json`, stats 테스트, `.gitignore`. advisor, vision, contracts, config, TOML은 건드리지 않았다.

| 항목 | 조치 |
|---|---|
| **W1 (우선)** | `db.py`의 읽기 경로(`list_snapshots`, `find_snapshot`, `read_snapshot`)는 `connect_ro()`로 연다. 이 함수는 `file:...?mode=ro` URI를 쓰며, DDL도 INSERT도 실행하지 않는다. `read_snapshot`은 조회 전체를 읽기 트랜잭션 1개(`BEGIN`…`rollback`)로 묶어 한 시점만 본다. 스키마 생성·마이그레이션·`schema_info`와 `journal_mode=WAL`, busy timeout 10s는 `write_snapshot`이 쓰는 `_connect_rw()`에서만 처리한다. WAL이므로 읽기가 열려 있어도 refresh는 바로 커밋하고, 읽기 쪽은 커밋된 직전 상태를 본다. 파일·디렉터리가 읽기 전용이라 `-shm`을 만들 수 없으면 `immutable=1`로 다시 시도한다. `open_repository`는 find→read 사이에 동시 refresh의 보존 정리로 스냅샷이 사라지면 최대 3번 다시 찾는다. `connect()`는 하위 호환용 별칭(쓰기용)으로 남겼다. strict xfail을 지웠고 `test_open_repository_on_readonly_db`는 PASS다. 추가 테스트: `test_reads_do_not_write_and_do_not_block_writer`. 실제 DB에서 `info`/`diff`를 실행한 뒤 mtime이 바뀌지 않음을 확인했다 |
| **W2** | (a) `diff.augment_tiers.by_comp`: 양쪽에 모두 있는 덱마다 `added`/`removed`/`changed`를 담는다. markdown에 "덱별 증강 등급" 절을 추가했다. (b) old가 None이면 `has_baseline=False`, `patch_changed=None`이고 "(비교 대상 없음…)"을 표시한다. 따라서 auto diff 파일은 만들지 않는다. (c) `snapshots.content_hash` 열을 추가했다(스키마 v2, v1 DB는 첫 쓰기 때 ALTER 후 기존 행의 해시를 채운다). 해시는 저장된 값(report_json + 행 JSON, 순서 무시)으로 계산한다. 새 스냅샷이 출처의 최신 스냅샷과 해시가 같으면 **아무것도 쓰지 않고** 그 id를 반환하며 보존 정리도 하지 않는다. `RefreshResult.skipped_identical`과 notes로 알린다. 같은 fetched_at이라도 내용이 다르면(변환기가 바뀐 경우) 기존처럼 교체한다. CLI 도움말에 dedupe와 `load` = 롤백 경로를 적었다(QA 참고 4). 실제 DB에서 `refresh --no-fetch`를 실행한 결과 "스냅샷 #2와 같아 새로 쓰지 않음", v2 마이그레이션, WAL 전환을 확인했다 |
| **W4** | `_StaticView`의 `recipe`와 `is_craftable`을 `craft`와 같은 범위(DA_*·set_native)로 좁혔다. 레거시 `TFT_Item_*` 39개는 레거시 재료로 조합되고 통계에도 나오지 않으므로 이제 None/False다. advisor `JsonStatsAdapter`와 의미가 같다. 테스트: `test_recipe_table_roundtrip` 확장 |
| **R4 XP 표** | `meta.json`에 `xp_to_next`를 넣었다: {1:2, 2:2, 3:6, 4:10, 5:20, 6:36, 7:56, 8:68, 9:68}. 레벨 L→L+1이며 화면 "a/b"의 b다. `xp_to_next_observed_levels` [3,4,6], `xp_sources`(tftflow.com Set 18 표, 2026-09-22 조회), `xp_note`도 함께 넣었다. metabot과 esportstales의 해당 페이지에는 XP 표가 없어서 **단일 출처**다. 관측값 3/4/6은 일치한다. `static_extract.py` 상수에도 반영해 재생성해도 유지된다. `StaticData.xp_to_next()` 접근자를 추가했다. **주의(vision에 전달)**: `vision/parse.XP_TO_NEXT` 폴백의 7/8/9레벨 값 48/76/84는 이 표의 56/68/68과 다르다. 7레벨 이상 캡처로 확정해야 한다 |
| **R5** | `static_data.preference_key` 공개. `_preference = preference_key` deprecated 별칭 유지(`vision/templates.py`가 아직 import) |
| **keep_snapshots** | `refresh.keep_snapshots(settings)`는 `getattr(settings.stats, "keep_snapshots", None)`가 1 이상인 int면 그 값을, 아니면 `db.DEFAULT_KEEP`=5를 쓴다. `refresh(keep=None)`과 `load_json(keep=None)`이 이 함수를 쓴다. **app-integrator에 요청: `StatsCfg.keep_snapshots: int = 5`(ge=1)** |
| 기타 | `.gitignore`에 `data/stats/*.sqlite-wal`, `-shm`, `-journal`을 추가했다(WAL 부속 파일). 읽기 전용 연결이 비어 있는 `-wal`/`-shm`을 남길 수 있는데, 다음 쓰기 때 정리된다 |

pytest: `.venv/bin/python -m pytest -q -rxs` → **442 passed, 3 skipped**(live Jev). stats 계열 신규·수정 테스트: `tests/test_stats_refresh.py`(+5), `tests/test_stats_repository.py`, `tests/test_stats_qa.py`(xfail 제거).
