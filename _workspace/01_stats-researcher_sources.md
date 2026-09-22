# 01 stats-researcher: 통계 소스 조사 + 정적 데이터 추출 (Phase 1)

작성일: 2026-09-21 / 작성자: stats-researcher
원칙: 실제로 요청해 확인한 것만 적었다. 확인하지 못한 것은 "미확인"으로 표기했다. 요청 간격은 1.2초, UA는 `tft-advisor-research/0.1 (personal use)`를 썼다.

---

## 0. 핵심 결론 (먼저 읽을 것)

1. **현재 세트는 Set 18(mutator `TFTSet18`), 패치는 18.2b.** 패치 18.2b는 2026-09-15 시작이다(MetaTFT `/tft-stat-api/patch`). tftactics.gg와 lolchess.gg도 "18.2b / v18.2b (시즌 18)"로 표시한다.
2. **Set 18의 canonical ID는 `DA_` 접두사다.** 예: `DA_18_Zyra`, `DA_Amumu18`, `DA_ArchangelsStaff`, `DA_Hustler`. `TFT18_*` 형식이 아니다. MetaTFT, tactics.tools, OP.GG, lolchess.gg(/decks)가 모두 이 ID를 그대로 쓴다. 그래서 CDragon apiName과 거의 1:1로 조인된다(4절).
3. **Set 18에서는 증강 성적 통계가 어느 소스에도 없다.**
   - MetaTFT: explorer `augments` 컬럼이 null이다. comp_details `augments`는 `aug:""` 한 줄뿐이다. `/tft-stat-api/augments`는 500을 반환한다. `augment=DA_Hustler` 필터를 걸면 표본이 0이다.
   - tactics.tools: `/augments` 페이지의 `augsData`가 singles/pairs/trios 모두 빈 배열이다. 유닛 상세의 `augments/aug1s/aug2s/aug3s`도 빈 배열이다.
   - lolchess.gg: 메타 덱의 `augments`가 `[null,null,null]`이다.
   - 해석(추정): Riot이 매치 데이터에서 증강을 빼서 통계 사이트가 증강을 수집할 수 없는 상태로 보인다. Riot Mortdog이 "증강을 게임 종료 화면·매치 기록, 즉 통계 사이트에서 제거한다"고 공지한 적이 있다([X 게시물](https://x.com/Mortdog/status/1856785428852216007?lang=en)). 과거에 되돌린 이력도 있다([Dot Esports](https://dotesports.com/tft/news/mortdog-admits-tfts-ban-on-augment-stats-sites-was-naive-as-riot-changes-course)). Set 18에서 적용된 정확한 공지는 **미확인**이다. 다만 세 소스가 모두 비어 있다는 사실은 확인했다.
   - 결과적으로 **"증강 보유 시 덱 성적"과 "증강 제시 시점별 성적"은 현재 얻을 수 없다.** 대신 쓸 수 있는 것은 사람이 만든 티어(S/A/B/C)다. MetaTFT `augments_tiers`(전체)와 `comp_augment_tiers`(덱별)가 있고, 사용자 화면의 OP.GG 오버레이 S/C/A도 같은 성격이다.
4. **추천 1순위는 MetaTFT, 백업은 tactics.tools, 보조는 OP.GG MCP다.** 조건부(아이템) 통계와 레벨별 빌드업 보드를 모두 공개 JSON으로 주는 소스는 MetaTFT 하나다.

---

## 1. 현재 세트와 정적 데이터 추출

### 1.1 세트 식별
- 원본: `data/static/raw_cdragon/ko_kr.json`, `en_us.json`. 재다운로드하지 않았다.
- `setData` 중 `mutator == "TFTSet{n}"`인 최대 n이 18이다. `sets["18"].name`은 "Set10"으로 잘못 들어가 있어서 쓰지 않았다.
- Set 18 원본 풀: 챔피언 91(상점 풀 74), 특성 36, 아이템 771(상점 특수 상품 345 포함), 증강 592.

### 1.2 산출물 (`data/static/18/`)
| 파일 | 내용 |
|---|---|
| `champions.json` | apiName, name_ko/en, cost, traits(apiName)/traits_ko/traits_en, `shop_pool`(특성 있음 && 코스트 1~5), 스킬 설명, 아이콘 경로 |
| `traits.json` | apiName, name_ko/en, breakpoints(예: 나무정령 [3,5,7,9,11]), styles, 설명 |
| `items.json` | 426개. `category`: component 20 / completed 72 / emblem 21 / tactician 6 / artifact 83 / radiant 82 / consumable 57 / assist_reward 61 / other 24. `composition`(재료 2개), `set_native`, `aliases`(같은 한국어 이름의 다른 ID) |
| `augments.json` | 592개. name_ko/en, desc_ko/desc_en(변수 치환, 치환 못 한 값은 `?`), `tier`(1 실버 / 2 골드 / 3 프리즘), `aliases`, `opgg_listed` |
| `shop_specials.json` | 345개. Set 18 상점 특수 상품. 예: "3단계와 함께" = `DA_ThreeMe18`, "무작위 3단계 2성 챔피언 1명을 획득합니다." `_Upgrade`/`_Prismatic` 변형 포함 |
| `name_check.json` | 스크린샷 이름 매칭 결과 |
| `meta.json` | 개수, 증강 티어 검증 결과, 관측된 상점 확률 |
| `unmapped.json` | 통계 소스에는 있고 스냅샷에는 없는 ID |

재실행 명령:
`python src/tft_advisor/stats/static_extract.py [--set 18] [--opgg-augments data/raw/opgg_mcp/2026-09-21/augments_ko.json]`

### 1.3 스크린샷 이름 매칭: 42/42 모두 찾음
- 챔피언 17/17: 카밀, 워윅, 카르마, 아칼리, 조약돌(`DA_18_Sentry`), 자야, 바루스, 오른, 티모, 니달리(`DA_Nidalee18_AP`), 람머스, 라칸, 요릭, 헤카림, 알리스타, 바위 게(`DA_Scuttlecrab18`, 2코스트), 렉사이. 모두 찾음.
  - 주의: 이름이 같은 비상점 유닛이 있다. "협곡 바위 게"(`TFT9_SLIME_Crab`, 크립)와 "어스름늑대"가 크립과 상점 유닛 양쪽에 있다. 이름으로 찾을 때는 `shop_pool=true`만 봐야 한다.
- 특성 21/21: 모두 찾음.
- 증강 3/3: 고위천사의 지팡이 = `DA_SeraphimsStaff`(골드), 출정 = `DA_Warpath`(골드), 수완가 = `DA_Hustler`(골드). 모두 찾음.
  - 같은 이름의 `TFT_Augment_SeraphimsStaff`, `TFT_Augment_Warpath`, `TFT6_Augment_HyperRoll`도 풀에 있다. MetaTFT·OP.GG가 `DA_*`를 쓰므로 **`DA_*`를 라이브 ID로 쓴다.** 나머지는 aliases로 연결해 두었다.
- 상점 특수 상품 1/1: "3단계와 함께". 9골드라는 가격 정보는 CDragon에 없다.

### 1.4 증강 티어 검증
- CDragon에는 티어 필드가 없다. 그래서 tags 해시로 추정했다: `{d11fd6d5}` = 실버, `{ce1fd21c}` = 골드, `{cf1fd3af}` = 프리즘. 592개 모두 이 셋 중 정확히 하나를 가진다.
- OP.GG MCP `tft_list_augments(ko_KR)`의 tier와 **248/248 일치**했다. 아이콘 파일명 접미사(-i/-ii/-iii)와는 465 일치, 14 불일치였다. 아이콘 재사용 때문에 생긴 불일치라서 해시 쪽을 채택했다.

### 1.5 상점 확률
- CDragon JSON에는 없다. 화면에서 관측한 3레벨 75/25/0/0/0, 4레벨 55/30/15/0/0, 6레벨 30/40/25/5/0만 `meta.json`에 넣었다.
- **5, 7~10레벨 값은 미확인이라 채우지 않았다.** 스크린샷을 더 모으거나 확인된 출처를 찾아야 한다.

### 1.6 Set 18 아이템 구조에서 알아둘 점
- 재료에 **프라이팬(`DA_Component_FryingPan`)** 이 추가됐다. 상징은 뒤집개 조합과 프라이팬 조합으로 나뉜다. 예: 검은 가시 상징 = 뒤집개 + 거인의 허리띠, 싸움꾼 상징 = 프라이팬 + 거인의 허리띠.
- 전략가 아이템은 3종이다: 뒤집개+프라이팬 = 망토, 뒤집개×2 = 왕관, 프라이팬×2 = 방패.
- 조합법이 없는 상징도 있다: 악의 여단, 엄호대, 전쟁기계, 치명적인 꽃(`composition: []`).
- `TFT_Item_*` 범용 ID도 같은 이름으로 들어 있다. 통계 소스는 모두 `DA_*`를 쓰는 것을 확인했다.

---

## 2. 소스 비교표

| 소스 | 접근 방식 | 인증 | 덱 평균등수/Top4/표본 | 유닛·아이템 | **조건부(아이템)** | **조건부(증강)** | **스테이지·레벨별 / 빌드업 보드** | 증강 시점별 | 티어 필터 | 갱신 | robots.txt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **MetaTFT** | 공개 JSON (`api-hc.metatft.com`) | 없음 | O (comps_data: count, avg) | O (units/items: 1~8등 분포) | **O**: explorer `item_unique`, `unit_item_unique`, 덱별 `itemNames[].units` | X (Set 18 데이터 없음) | **O**: 덱별 `early_options`(레벨 4/5/6 보드, avg·win·count), `options`(레벨 7~10 최종 보드), `levels`(레벨업 라운드) | X | O (`rank=`, 덱 상세 `ranks`) | 약 1시간(응답 updated가 조회 수 분 전) | 전체 허용 |
| **tactics.tools** | 공개 JSON (`d3.tft.tools/stats2`, `api.tft.tools`) + Next.js `__NEXT_DATA__` | 없음 | O (comps: count, place, top4, win) | O (general: 유닛/특성/아이템) | **부분**: 유닛 상세의 items/itemPairs/itemTrios(유닛+아이템), 동반 유닛/특성 | X (빈 배열) | X (확인 못 함) | 구조는 있으나(aug1s/2s/3s) 비어 있음 | O (`rankGroup`, 값의 의미는 미확인) | lastUpdated가 조회 약 1시간 전 | 허용(Disallow 없음) |
| **OP.GG MCP** | MCP Streamable HTTP `https://mcp-api.op.gg/mcp` | 없음(실제 호출 성공) | O (상위 10덱 avgPlacement, top4Rate, compsCount) | O (챔피언 아이템 빌드, 아이템별 추천 챔피언) | 부분: 아이템→챔피언 성적 | X | **부분**: 덱별 `early`(레벨 5 보드), `middle`(레벨 7 보드) + play/win | X | 미확인 | `gameStatDateTime: 2026-09-10`(18.2b 이전일 수 있어 최신성 의심) | op.gg 전체 Allow |
| **lolchess.gg** | Next.js `__NEXT_DATA__`(React Query dehydrated) | 없음 | O (`/decks`: plays, avgPlacement, topRate, winRate, placements, dt=3일, tierId=1) | O (덱별 championStats) | 미확인 | X (`augments:[null,null,null]`) | 미확인 | X | O (`tierId`) | updatedAt이 조회 약 6분 전 | `/search`만 금지 |
| **tftactics.gg** | SPA(JS 렌더링) | - | X (편집자 티어리스트만 보임) | 미확인 | X | X | X | X | - | 패치 단위 | 전체 허용 |
| Community Dragon | 정적 JSON | 없음 | - | 정적 데이터 | - | - | - | - | - | 패치 | - |

표본 규모(3일, 다이아 이상, 2026-09-21 조회): MetaTFT explorer는 1,102,328 참가자 행이다(표본 게임 139,296 × 8). tactics.tools general은 totalEntries 1,471,382(티어 그룹 1)다. lolchess.gg `/decks`는 plays 61,679다.

이용약관: MetaTFT, tactics.tools, lolchess.gg 모두 **미확인**이다. 약관 페이지가 SPA라 본문을 가져오지 못했다. 개인용이므로 캐시를 쓰고 1초 이상 간격을 두는 방식으로 대응한다. OP.GG MCP는 AI 에이전트 연동용으로 공식 공개된 서버다([opgg-mcp](https://github.com/opgginc/opgg-mcp)).

---

## 3. 추천

### 1순위: MetaTFT
- jev-recommender 1절의 두 축을 모두 채우는 유일한 소스다.
  - 최종 덱 ← 자원(아이템): `comp_details.itemNames[]`로 "덱 c에서 아이템 i를 가진 경우의 avg와 count"를 얻고, 유닛별 분해(`units[]`, `place_change`)까지 된다. 이것이 `comp_given_item`이다. explorer로 아이템 여러 개, 유닛, 특성, 레벨을 조합한 조건도 걸 수 있다.
  - 빌드업: `early_options`(레벨 4/5/6 보드별 avg, win, count), `options`(레벨 7~10), `levels`(레벨별 전형적인 레벨업 라운드, 예: 5레벨 2-5, 6레벨 3-2, 7레벨 3-5, 8레벨 4-2, 9레벨 6-1). 이것이 `comp_buildup`이다.
- 덱 클러스터 57개가 있고, 덱마다 표본 수(`overall.count`)와 BIS 빌드(`builds[]`: 유닛, 아이템 3개, avg, count, place_change)가 있다.
- ID가 CDragon과 그대로 맞는다(4절).

### 백업: tactics.tools
- MetaTFT가 막히면 여기로 전환한다. 덱(count, place, top4, win), 유닛·특성·아이템 전체 통계, 유닛+아이템 1/2/3개 조합 성적을 준다.
- 스테이지별 빌드업 보드는 확인하지 못했다. 그래서 빌드업 축은 이 소스만으로 채울 수 없다.

### 보조: OP.GG MCP
- **증강 메타데이터**(라이브 목록, 한국어 이름·설명·티어, 이미지 URL)의 가장 깔끔한 출처다. CDragon 스냅샷에 없는 신규 증강(`DA_CalculatedLoss` 등 5개)도 여기서 채울 수 있다.
- 덱 early(레벨 5)/middle(레벨 7) 보드는 교차검증용으로 쓴다.

### 증강 판단 (통계 공백 대응)
- 증강은 통계 대신 다음 세 가지를 조합한다.
  1. MetaTFT `comp_augment_tiers`: 덱별 증강 S~D 등급. `source_title` 예 "ZYRA > Juggernaut > Lvl 8 push".
  2. MetaTFT `augments_tiers`: 전체 증강 티어리스트. 편집자 "META Spencer"가 작성했고 2026-09-21에 갱신됐다.
  3. Jev의 `augment_fit` 판단. 증강 설명 텍스트는 `augments.json`의 `desc_ko`를 쓴다.
- 이 등급은 **사람이 매긴 값이지 표본 기반 통계가 아니다.** 저장할 때 `games=null`, `source_kind="editorial"`로 구분해야 한다.

---

## 4. ID 매핑 검증 (2026-09-21 응답 기준)
| 대상 | 결과 |
|---|---|
| MetaTFT units 69 | 65 매핑. 미매핑 4: `TFT18_Akali`, `TFT18_Gromp`, `TFT18_MasterYi`, `TFT18_NidaleeCougar`. 형태가 바뀌는 유닛의 변형 ID로 보인다. OP.GG enum에도 같은 ID가 있다 |
| MetaTFT 덱 유닛·특성 | 전부 매핑됨. 특성은 `DA_Juggernaut18_3`처럼 `_{단계}` 접미사를 떼고 맞춘다 |
| tactics.tools 유닛 74 / 특성 | 전부 매핑됨. 특성 키는 `DA_18_Invoker__2` 형식(`__{단계}`) |
| tactics.tools 아이템 126 | 125 매핑. 미매핑: `DA_Artifact_Hullcrusher` |
| MetaTFT 증강 티어 258 | 248 매핑. 미매핑 10개(`DA_StarringUp`, `DA_Lineup`, `DA_NestingDolls`, `DA_SubscriptionService` 등). **CDragon 스냅샷이 18.2b보다 오래됐을 가능성이 있다** |
| 빌드업 보드의 소환물 | `DA_Elderwood18_Lifeblossom`, `DA_Elderwood18_StonebarkTree`는 나무정령 특성의 소환물이라 champions.json에 없다. 보드 비교에서 제외하거나 별도 테이블을 둔다 |

미매핑 목록은 `data/static/18/unmapped.json`에 있다(QA 확인용).

---

## 5. 엔드포인트와 응답 구조

원본 캐시: `data/raw/{metatft,tactics_tools,opgg_mcp}/2026-09-21/`

### MetaTFT (`https://api-hc.metatft.com`, 대체 호스트 `api-hc2.metatft.com`)
공통 쿼리: `queue=1100&patch=current&days=3&rank=CHALLENGER,DIAMOND,GRANDMASTER,MASTER&permit_filter_adjustment=true`

| 엔드포인트 | 응답 요약 |
|---|---|
| `/tft-stat-api/patch` | `{patch:"18.2", b_patch_version:"b", start, count}` |
| `/tft-stat-api/units?{공통}` | `results[]: {unit, places[8]}`. 1~8등 횟수 → games, avg, top4 계산 |
| `/tft-stat-api/items?{공통}` | `results[]: {itemName, places[8]}` |
| `/tft-stat-api/augments_tiers` | 편집자 티어리스트 `content.content.tierList[]: {content:[{id,type}]}`. 티어 5단(27/87/123/21/0개) |
| `/tft-comps-api/comps_data?queue=1100` | `results.data.cluster_details{id}`: units_string, traits_string, name[], overall{count,avg}, builds[], build_items{item:{count,avg,pcnt}}, trends[], levelling("Fast 8" 등), difficulty |
| `/tft-comps-api/latest_cluster_info` | 클러스터 ID(현재 424)와 생성·갱신 시각 |
| `/tft-comps-api/comp_details?comp={id}&cluster_id={cid}` | **핵심.** placements, final_levels[], unit_stats[](별별·아이템 수별 avg), builds[300], itemNames[]{count, avg, units[]{avg, place_change}}, traits[]{levels[]}, `early_options{4,5,6}`[]{unit_list, count, avg, win, level}, `options{7..10}`[], `levels`[]{stage, round, level}, `ranks`[], rerolls, positioning, counters[] |
| `/tft-comps-api/comp_options?cluster_id={cid}` | 전체 덱의 레벨 7~10 보드 옵션(4.8MB) |
| `/tft-comps-api/comp_augment_tiers` | 덱별 `augments[]{id, tier(S~D)}`, source_title |
| `/tft-comps-api/comp_builds`, `/unit_items_processed` | 유닛×아이템 빌드. 구조 미확인(호출 안 함) |
| `/tft-explorer-api/{total, units_unique, augments, level, server, rank}?formatnoarray=true&compact=true&{공통}&{필터}` | `data[]{placement_count[8]}` 등. 필터: `unit_tier_numitems_unique=DA_18_Zyra-1_.*_.*`, `item_unique=DA_ArchangelsStaff-1`(**`-개수` 필수**), `unit_item_unique=DA_18_Zyra-1%26DA_ArchangelsStaff-1`, `trait=DA_18_Lunar_2`, `level=8-any`, 부정 필터는 `!` 접두사 |

검증 예: 아크엔젤을 가진 참가자는 24,300/25,146/…(1~8등)이다. 자이라가 아크엔젤을 든 경우는 5,998/6,457/…다. 조건부 필터가 실제로 동작한다.

### tactics.tools
- `https://d3.tft.tools/stats2/general/{queue}/{patchCode}/{rankGroup}`, 예: `/general/1100/16181/1`
  - `{totalEntries, lastUpdated, units{id:{count, place, top4, won, topItems, star*}}, traits{"id__단계":…}, items[]{itemId, count, place, top4, won, adjDelta, topUsers}}`
  - patchCode 16181은 페이지 `__NEXT_DATA__.props.pageProps.aperture.patch._0`에서 얻는다. 인코딩 규칙은 미확인이다. `current`를 넣으면 400이 난다.
- `https://d3.tft.tools/stats2/unit/{queue}/{unitId}/{patchCode}/{rankGroup}`: base, placeDistribution, regionStats(kr 포함), dateStats, units(동반 유닛 delta), traits, items/itemPairs/itemTrios(adjDelta), starLevelData, itemCountData, augments/aug1s~3s(현재 빈 배열)
- 덱: `https://tactics.tools/team-compositions`의 `__NEXT_DATA__.initialData.groups[].full.comps[]{units, count, place, top4, win}`. JSON API는 `https://api.tft.tools/team-compositions/{rankGroup}/{patchCode}`(번들에서 확인, 직접 호출은 안 함)

### OP.GG MCP (`POST https://mcp-api.op.gg/mcp`, JSON-RPC, `initialize` → `tools/call`)
- `tft_list_meta_decks`: 10덱. 각 덱에 `stat.deck{avgPlacement, top4Rate, winRate, compsCount, pickRate}`, `stat.opTier`, `early{level:"5", units[], play, win, lose}`, `middle{level:"7", …}`, 최종 `units[]{key, items, tier, isCore}`. 이름 27개 언어(ko_KR 포함). `metadata.gameStatDateTime`
- `tft_list_augments{lang}`: headers [apiName, desc, name, tier(silver/gold/prism), imageUrl], 283행
- `tft_get_champion_item_build{champion_id}`: `[{itemNames[3], itemCount, avgPlacement, top4Rate, winRate}]`
- `tft_list_champions_for_item{item_id}`: `[{characterId, totalCount, avgPlacement, top4Rate}]`
- `tft_list_item_combinations{lang}`: 호출 안 함

### lolchess.gg
- `https://lolchess.gg/decks`의 `__NEXT_DATA__.props.pageProps.dehydratedState.queries[0].state.data.metaDeckList.metaDecks[]{plays, avgPlacement, topRate, winRate, placements[8], deck{champions[]{key, coreRank, items}, traits}, championStats}` (dt=3, tierId=1, patch 1802)
- `/meta`는 편집자 가이드 덱이다. 아이템 키가 자체 명칭(`GuardianAngel`, `SpearofShojin`)이라 `itemRefs`로 매핑해야 한다.

---

## 6. 리스크와 대응
| 리스크 | 대응 |
|---|---|
| 비공식 API 구조 변경이나 차단(MetaTFT, tactics.tools) | 원본을 `data/raw/{source}/{date}/`에 캐시한다. 수집은 패치 또는 하루 1~수회로 제한하고, 실시간 루프에서는 로컬 SQLite만 조회한다. 실패하면 1회 재시도 후 tactics.tools, 그다음 OP.GG MCP로 전환한다 |
| MetaTFT 클러스터 ID가 재계산 때마다 바뀜(현재 424, 덱 ID 424xxx) | 덱은 클러스터 ID가 아니라 **핵심 유닛 집합과 특성**으로 정체성을 저장한다. 수집 시점의 cluster_id도 함께 기록한다 |
| 증강 통계 공백 | 편집자 등급과 Jev로 대체한다(3절). 수집기는 증강 필드가 다시 채워지는지 매 수집 때 감지하고, 채워지면 알린다 |
| CDragon 스냅샷이 오래됨(증강 10개, 아이템 1개, 유닛 변형 4개 미매핑) | OP.GG MCP 증강 목록으로 보완한다. 다음 패치 때는 CDragon `latest`를 다시 받는다(이번에는 지시에 따라 재다운로드하지 않았다) |
| OP.GG MCP 데이터의 최신성 의심(gameStatDateTime 09-10) | 덱 통계는 교차검증에만 쓰고 메타데이터 위주로 쓴다 |
| 티어 필터가 소스마다 다름(MetaTFT는 rank 문자열, tactics.tools는 rankGroup 숫자로 의미 미확인, lolchess는 tierId=1) | 저장 행에 `source`와 `rank_filter`를 넣는다. 수치는 합치지 않고 출처별로 함께 둔다 |
| 정책 | Riot 정책상 실시간 게임 상태 기반 추천과 증강 승률 표시는 금지 항목이다. 사용자가 개인용 실시간 모드를 선택했다(CLAUDE.md). 현재는 증강 승률 자체가 수집되지 않는다 |

---

## 7. jev-strategist와 app-integrator가 알아야 할 것

**jev-strategist**
- 6절 공식의 `comp_given_augment`는 **현재 채울 수 없다.** `comp_augment_fit`와 `augment_fit`은 Jev 판단에 편집자 등급(S=1.0, A=0.75, B=0.5, C=0.25, D=0 같은 변환 제안)을 약하게 섞는 방식으로 설계해 달라. `games` 기반 표본 수축은 적용하지 않는다.
- `comp_given_item`은 MetaTFT `comp_details.itemNames`로 공급할 수 있다(덱 × 아이템 → avg, count, 유닛별 분해).
- 빌드업 보드는 **스테이지가 아니라 레벨 단위**로 제공된다(early 4/5/6, options 7~10). 스테이지로 바꾸려면 덱별 `levels` 표(레벨 → 전형적인 라운드)를 쓴다. state의 `buildup`은 `{"lv4":[…], "lv5":[…], "lv6":[…], "final_lv8":[…]}`처럼 레벨 키로 두는 것을 제안한다. 스키마를 바꿔야 하므로 오케스트레이터 승인이 필요하다.
- "스테이지별 유닛 성적(unit_stage)"을 직접 주는 소스는 없다. early 보드의 avg와 win으로 간접 추정해야 한다.
- 조건부 통계는 모두 **게임 종료 시점**의 보유 아이템·보드 기준이다. "중간에 이 아이템을 가졌을 때"가 아니다.

**app-integrator**
- `contracts.py`에 반영할 제안(아직 반영하지 않음):
  - ID는 `DA_*` 문자열을 canonical로 쓴다.
  - 증강 행에 `tier`(1~3), `source_kind`(stat|editorial), `games` nullable을 둔다.
  - 빌드업 키는 레벨 기반으로 한다.
  - 상점 특수 상품(`shop_specials`) 타입을 추가한다. 상점 슬롯은 챔피언이 아닐 수 있다.
- 상점 확률표는 5, 7~10레벨이 비어 있다. 비전 스크린샷이나 확인된 출처가 필요하다.
- 한국어 이름 → ID 조회는 `shop_pool=true` 챔피언 중에서만 한다. "어스름늑대"는 크립 중복이 있다. 증강은 `DA_` ID를 우선한다(이름 중복 aliases 있음).
- 수집은 실시간 루프 밖에서 한다(패치 또는 하루 단위 배치). 실시간 루프에서는 SQLite만 읽는다.

---

## 8. 산출물 경로
- 스크립트: `C:\Users\hjk38\Desktop\LOL Chess\src\tft_advisor\stats\static_extract.py`
- 정적 데이터: `C:\Users\hjk38\Desktop\LOL Chess\data\static\18\` (champions, traits, items, augments, shop_specials, name_check, meta, unmapped)
- 원본 캐시: `C:\Users\hjk38\Desktop\LOL Chess\data\raw\metatft\2026-09-21\`, `…\tactics_tools\2026-09-21\`, `…\opgg_mcp\2026-09-21\`

출처: [Mortdog X 게시물](https://x.com/Mortdog/status/1856785428852216007?lang=en), [Dot Esports](https://dotesports.com/tft/news/mortdog-admits-tfts-ban-on-augment-stats-sites-was-naive-as-riot-changes-course), [opgg-mcp GitHub](https://github.com/opgginc/opgg-mcp), [OP.GG TFT 메타 덱](https://op.gg/tft/meta-trends/comps)
