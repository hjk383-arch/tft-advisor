# 08 stats-researcher: 패치 18.3 업데이트 1단계

날짜 2026-09-22 (Windows 새 PC). 이번 작업에서 고친 범위는 `src/tft_advisor/stats/`, stats 테스트, `tests/advisor/test_advisor_fixround.py`(실제 저장소 + s09 분기만), `data/static/18/unmapped.json`이다. vision, app, advisor 로직, contracts, config는 건드리지 않았다. 커밋은 하지 않았다.

## 1. 실패 3건 원인 판별

| 테스트 | 판별 | 원인 | 조치 |
|---|---|---|---|
| `test_stats_refresh::test_refresh_same_snapshot_skips_and_diff_is_empty` | **(b) 진짜 버그** (18.3 데이터에서 드러남) | 18.3에서 클러스터 424021(`primal-nidalee_ap`)과 424033(`hunter-nidalee_ap-sivir`)의 `units_string`이 같다(8유닛). R12 `assign_comp_ids`는 이전 스냅샷 보드와 Jaccard가 가장 높은 comp_id를 재사용한다. 그런데 두 쌍이 모두 1.0으로 동률이었고, 정렬 키 `(j, cid, pid)` 역순 때문에 424033이 `primal-…`을 가져갔다. 그래서 같은 원본을 다시 변환하면 두 ID가 **서로 뒤바뀌었다**. 내용 해시가 달라져 dedupe도 실패했다. 실제 운영에서는 매 refresh마다 덱 ID 2개가 번갈아 바뀐다(오버레이 히스테리시스, 덱별 증강·아이템 행이 반대 덱에 붙음) | `metatft_convert.assign_comp_ids`: Jaccard가 같으면 그 클러스터의 헤드라인 slug(또는 `slug~n`)와 같은 이전 ID를 먼저 배정한다. 반환 dict는 클러스터 ID 순서로 정렬해서 기준선이 있든 없든 JSON 순서가 같다. 회귀 테스트는 `test_stats_convert::test_comp_id_reuse_is_stable_for_identical_boards`다 |
| `test_stats_repository::test_unit_stats` | **(a) 18.2b 고정값** | `len(us) == 69`로 개수가 고정돼 있었다. 18.2b의 69개에는 정적 데이터에 없는 `TFT18_Akali/Gromp/MasterYi/NidaleeCougar` 4개가 들어 있었다. 18.3 `units.json`은 65개이고 전부 정적 챔피언이다 | 데이터에 의존하지 않는 불변식으로 바꿨다. 원본 `units.json`의 유닛 집합과 같을 것, 50개 이상일 것, games가 0보다 클 것, 정적 데이터에 없는 ID는 `report.unmapped_ids`에 있을 것. 이와 함께 버그도 고쳤다. 변환기가 정적 데이터에 없는 unit_stats ID를 `unmapped_ids`에 기록하지 않고 있었다(18.2b의 TFT18_* 4개가 누락됐었다) |
| `test_advisor_fixround::…[real-s09_hysteresis]` | **(a) 데이터 변경** (추천 로직 문제 아님) | 1스텝 1위가 근소차였다. 18.2b에서는 zyra 0.687 대 veigar 0.685(+0.002)였고, 18.3에서는 veigar 0.692 대 zyra 0.686(+0.006)이다. 두 스냅샷을 InMemory 저장소로 같은 조건에서 재현해 확인했다. mini 저장소에서는 기대값 그대로 통과한다 | 실제 저장소 + s09일 때만 `top_comp` 고정값 대신 불변식을 쓴다. 조건은 (1) 기대 덱(`juggernaut-zyra-amumu`)이 상위 2위 안이고, (2) 이후 스텝의 1위가 1스텝 1위와 같다(히스테리시스). 이미 있던 `test_s09_branch_on_repository`의 실제 저장소 완화 방식과 같다. **jev-strategist 검토를 요청한다** (5절) |
| (c) 캐시 폴더 여러 개 | 직접 원인은 아님. 잠재적 위험은 있음 | `latest_raw_dir`는 `comps_data.json`만 있으면 가장 최근 날짜를 골랐다. 지금은 09-22가 완전해서 문제가 없었다. 하지만 오늘 수집이 중간에 실패해 불완전한 폴더가 최신이 되면 refresh `--no-fetch`와 테스트 전체가 그 폴더를 쓴다. refresh 전의 8개 실패도 같은 유형이다 | `collectors/metatft.raw_dir_complete()`를 추가했다. patch, units, comps_data가 있고 모든 클러스터의 comp_details가 있어야 완전한 캐시다. `latest_raw_dir(require_complete=True)`는 완전한 캐시 중 최신을 고르고, 완전한 캐시가 없으면 최신 캐시로 폴백한다. 테스트는 `test_stats_refresh::test_latest_raw_dir_prefers_newest_complete_cache`다 |

실제 DB 확인: 수정 후 `python -m tft_advisor.stats refresh --no-fetch`를 실행하면 "내용이 최신 스냅샷 #1와 같아 새로 쓰지 않음"이 나온다. `metatft_18.3.json`을 다시 썼지만 파싱한 내용은 이전 파일과 같다(`==` True). 텍스트 차이는 `comp_ids` 순서뿐이다. 현재 DB는 #1 patch=18.3이다(comps 57, unit_stats 65, augment_tiers 2699, item_stats 142).

## 2. 18.3 정적 데이터 점검 (Community Dragon)

- `raw.communitydragon.org/latest/content-metadata.json`은 **16.18.8175716**(releases-16-18)이고 `cdragon/tft/*.json`의 Last-Modified는 2026-09-12다. 새로 받은 `ko_kr.json`과 `en_us.json`은 기존 `data/static/raw_cdragon/`과 **바이트 단위로 같다**(cmp). PBE는 16.20이다. 그래서 CDragon 기준으로 18.3 정적 변경은 아직 없다. `data/static/18/`은 그대로 두었다.
- MetaTFT 18.3 ID 대조(`_workspace/qa_scripts/id_crosscheck.py`): **새 미매핑은 0개**다. `units.json`의 65개, comps 유닛·특성·아이템, 증강 258개와 덱별 173개가 모두 정적 데이터에 있거나 `unmapped.json`에 선언돼 있다. `unmapped.json`에 `patch_18.3_check` 메모를 추가했다. Akali와 Gromp는 18.2b JSON 호환을 위해 선언을 유지한다.
- 사용자 캡처 이름 확인:
  - "불타는 묘목" = **챔피언** `DA_Cinderling18`(1코스트, shop_pool, 협곡야수·사냥꾼). 특수 상품이 아니다. 18.3 유닛 통계는 평균 4.68, 1586판이다.
  - "거대화" = 상점 특수 상품 `DA_Hugify18`(shop_specials, 변형 `_Upgrade`, `_Prismatic`). champion/item 검색에서는 None이라 충돌이 없다. 정적 데이터에 가격 필드는 없다(1골드는 화면 관측값).
  - "어수선한 마음" = 증강 `DA_ClutteredMind`(tier 2). 이름이 같은 레거시 `TFT7_Augment_ClutteredMind`가 있지만 `augment_by_name`은 set_native를 우선한다. 18.3 등급은 A다.
  - "초월" = 증강 `DA_Ascension`(tier 2). 레거시 `TFT9_Augment_Commander_Ascension`보다 우선한다. 18.3 등급은 B다. 비슷한 이름으로 증강 "불완전한 초월"(`DA_PartialAscension`)과 특수 상품 "극한의 초월"(`DA_18_UltraAscension`)이 있다. 정확한 이름으로 조회하면 문제없다.

## 3. `metatft_{patch}.json` 관리 정책

기존 규칙(`04_stats-researcher_impl.md` 10, 94행)은 "SQLite(gitignore) + 사람이 읽는 JSON `metatft_{patch}.json` 유지"다. JSON은 diff 기준선이고, QA와 스크립트의 입력이며, DB가 없을 때 `open_repository`와 `JsonStatsAdapter`의 폴백이고, `stats load`의 롤백 경로다. 이에 따라:
- **`metatft_18.3.json`: 커밋 대상**(오케스트레이터가 커밋). 새 PC에서도 DB 없이 앱과 테스트가 동작하게 하는 원천이다.
- **`metatft_18.2b.json`: 삭제하지 않고 유지**(직전 패치 기준선. 롤백은 `python -m tft_advisor.stats load data/stats/metatft_18.2b.json`). 제안: 바로 이전 패치 1개만 남기고, 그보다 오래된 것은 세트가 바뀔 때 정리한다.
- `stats.sqlite*`와 `data/raw/`는 계속 gitignore다.
- 주의: `advisor/stats_source.latest_json`은 **파일명 사전순** `max()`, refresh 기준선은 **mtime**으로 최신을 고른다. 18.2b와 18.3에서는 둘 다 18.3을 고르지만, "18.10"과 "18.9"처럼 두 자리 패치가 되면 사전순이 틀린다(5절).

## 4. pytest

`.venv/Scripts/python.exe -m pytest -o addopts="" -q` → **705 passed, 3 skipped**(live Jev), 0 failed.
참고: 중간 실행 1회에서 `tests/test_config_round.py::test_item_match_margin_is_read_from_config`가 한 번 실패했다. 바로 단독으로, 그리고 전체로 다시 실행하면 통과한다. 같은 시각 qa-validator가 config/vision을 수정 중이어서 생긴 일시적 상태로 본다(stats와 무관).

## 5. 전달 사항

**jev-strategist**
1. s09 실제 저장소 기대값 수정안(1절)을 검토해 달라. zyra와 veigar의 점수 차가 0.01 이하라 패치마다 1위가 바뀔 수 있다. 픽스처가 "대천사 보유 → zyra"라는 의도를 확실히 검증하게 하려면 두 방법이 있다. (a) 실제 저장소에서는 지금처럼 불변식만 확인한다. (b) s09 1스텝 상태에 zyra 전용 신호(예: 보드에 Zyra/Amumu 추가)를 넣어 차이를 벌린다.
2. **18.3 표본 경고**: MetaTFT `patch=current`인 유닛·아이템 통계는 18.3 시작(2026-09-22T19:12Z) 뒤 약 7시간치다. 유닛 최대 표본은 10,067판이다(18.2b는 447,195). 반면 덱(`comps_data`/`comp_details`, 클러스터 424, 3일 창)은 대부분 18.2b 판이다(합 약 103만). 그래서 `patch_18.3_diff.md`의 "유닛 평균 등수 변화 9"와 아이템 변화 65는 표본이 적어 흔들리는 값일 가능성이 높다. games 기반 신뢰도 감쇠가 제대로 먹히는지 확인하고, 1~2일 뒤 refresh를 다시 하라고 권한다.
3. `advisor/stats_source.latest_json`의 사전순 `max()`는 패치 번호가 두 자리가 되면 틀린다. 패치 번호를 숫자로 파싱해 정렬하거나 mtime을 쓰도록 바꾸자고 제안한다(advisor 소관이라 건드리지 않았다).

**vision-engineer**
- "불타는 묘목"은 상점 **챔피언**(`DA_Cinderling18`, 1코스트)이다. 특수 상품으로 분류하면 안 된다. "거대화"는 `shop_specials`의 `DA_Hugify18`이다. 이름이 같은 변형 `_Upgrade`, `_Prismatic`이 있으므로 이름으로 역매핑할 때는 `preference_key`와 `set_native`를 따르라.
- 증강 "어수선한 마음"은 `DA_ClutteredMind`, "초월"은 `DA_Ascension`이다. 이름이 같은 레거시(TFT7/TFT9) 레코드가 있으니 `augment_by_name`을 쓰라.
- CDragon 아이콘/정적 데이터는 18.3에서 바뀌지 않았다(16.18과 동일). 템플릿을 다시 받을 필요가 없다.

**qa-validator**
- 새 테스트 3개: `test_comp_id_reuse_is_stable_for_identical_boards`, `test_latest_raw_dir_prefers_newest_complete_cache`, 불변식으로 바꾼 `test_unit_stats`.

## 변경 파일
- `src/tft_advisor/stats/metatft_convert.py`: comp_id 동률 처리, comp_ids 정렬, unit_stats 미매핑 기록
- `src/tft_advisor/stats/collectors/metatft.py`: `raw_dir_complete`, `latest_raw_dir(require_complete=True)`
- `tests/test_stats_convert.py`, `tests/test_stats_refresh.py`, `tests/test_stats_repository.py`, `tests/advisor/test_advisor_fixround.py`
- `data/static/18/unmapped.json`(메모 추가), `data/stats/metatft_18.3.json`(재생성. 내용 같음, comp_ids 순서만 바뀜)
