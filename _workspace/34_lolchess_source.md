# 34 stats-researcher: lolchess.gg 메타 덱·가이드 소스 — 1단계(가능성·정책) 결과: 차단됨

작성일 2026-09-25(조회 시각 2026-09-26 00:42Z) / 작성자 stats-researcher. UA `tft-advisor-research/0.1 (personal use)`, 요청 간격 2초.
2단계(수집기 구현)는 하지 않았다. 지시대로 차단 시 구현 전에 보고한다. 코드·데이터 변경 없음, 커밋 없음.

## 0. 결론

**lolchess.gg는 현재 사이트 전체가 AWS WAF JavaScript 챌린지 뒤에 있다. 일반 HTTP로는 어떤 페이지도 받을 수 없다(확인).**
헤드리스 브라우저나 챌린지 토큰 재사용은 봇 차단 회피라 쓰지 않는다. 사용자 결정이 필요하다(3절).

## 1. 증거

| 요청 | 결과 |
|---|---|
| `GET /robots.txt` | 200. `User-agent: * / Disallow: /search / Allow: /`, Sitemap `/sitemap.xml`. 자동 접근 자체는 robots상 허용 |
| `GET /decks` | **202, Content-Length 0**, `Server: CloudFront`, **`x-amzn-waf-action: challenge`** |
| `GET /meta` | 202, 본문 0, 같은 WAF 헤더 |
| `GET /meta?hl=ko-KR`, `GET /`, `GET /sitemap.xml` | 모두 202, 본문 0 |
| WebFetch(`/meta`) | 빈 본문(동일) |

- `x-amzn-waf-action: challenge`는 AWS WAF가 브라우저에서 JS 챌린지를 풀어 토큰 쿠키를 받으라는 응답이다. 쿠키 없는 클라이언트는 페이지를 받지 못한다.
- 2026-09-21(01 보고서)에는 `/decks`의 `__NEXT_DATA__`를 받았다. 그 뒤 WAF가 사이트 전체로 넓어진 것으로 보인다. 28 보고서(09-24)의 `/meta` 202도 같은 원인이다.
- `_next/data/...json`이나 사이트가 호출하는 API도 같은 CloudFront 뒤에 있어 경로가 같다. 별도 공개 API는 찾지 못했다(robots에 문서화된 API 없음). 이용약관은 페이지를 열 수 없어 **미확인**이다.

## 2. 데이터가 있는 곳(이전 조회로 아는 것 + 미확인)

- `/decks`(확인, 09-21): Next.js `__NEXT_DATA__.props.pageProps.dehydratedState.queries[0].state.data.metaDeckList.metaDecks[]`. 필드는 `plays, avgPlacement, topRate, winRate, placements[8], deck{champions[]{key, coreRank, items}, traits}, championStats`다. 챔피언 키가 `DA_` canonical ID와 같아 매핑이 쉽다. 증강은 `[null,null,null]`이었다. **등급(S/A/B) 필드 이름은 미확인**이다.
- `/meta` 편집자 덱(부분 확인, 01): 아이템 키가 자체 명칭(`GuardianAngel`, `SpearofShojin`)이라 `itemRefs`로 매핑해야 한다.
- "공략 더보기" 가이드 페이지의 URL 패턴, 공략 핵심 텍스트, 레벨별 추천 조합의 필드 구조: **미확인**(한 번도 받지 못함).

## 3. 선택지(사용자 결정 필요)

| 안 | 방법 | 장점 | 단점 |
|---|---|---|---|
| **A. 수동 저장 HTML 파싱(권장)** | 사용자가 평소 브라우저로 `/decks`, `/meta`, S등급 덱의 가이드 페이지들을 열고 Ctrl+S("웹페이지, HTML만")로 `data/raw/lolchess/manual/<날짜>/`에 저장한다. 수집기는 네트워크 없이 저장 파일의 `__NEXT_DATA__`를 파싱한다 | 약관·봇 차단 문제 없음. 페이지가 하루에 한 번 이하로 바뀌니 부담이 작다(파일 약 1+1+S덱 수 개). 파서는 `__NEXT_DATA__` 기반이라 안정적 | 갱신이 수동이다. 저장 전에 페이지가 완전히 로드돼야 한다 |
| B. Claude in Chrome(사용자 본인 브라우저 세션) | 사용자가 켠 Chrome에서 확장이 페이지를 열어 `__NEXT_DATA__`를 읽는다 | 반자동 | 봇 차단을 사람 브라우저로 통과시키는 자동화라 회색 지대다. 사용자가 명시적으로 허용할 때만 검토한다 |
| C. 대체 소스 | 덱 선정은 MetaTFT comps(현재 사용) 또는 OP.GG MCP `tft_list_meta_decks`(덱 10개, 레벨 5/7 보드, 등급 없음)로 한다 | 즉시 가능 | 사용자가 원한 "lolchess S등급 + 공략 핵심"이 아니다 |

**권장: A.** 시작하려면 사용자에게 **샘플 저장 파일 3종**(`/decks` 1개, `/meta` 1개, 가이드 1개)을 한 번 받아야 한다. 그걸로 구조(등급 필드, 가이드 URL 패턴, 공략 핵심·레벨별 조합 필드)를 확인하고 fixture를 만든다. 그 뒤 계획대로 구현한다.
- `stats/collectors/lolchess.py`: `load_saved(dir)`. 네트워크가 없다.
- 변환: 챔피언 키(`DA_`)는 그대로 쓰고, 한글명은 `static.champion_by_name`, 아이템은 `itemRefs`로 매핑한다. 미매핑은 `unmapped.json`에 기록한다.
- 저장: DB 스냅샷 `source="lolchess"`, `data/stats/lolchess_decks_<patch>.json`.
- 조회: `repo.lolchess_decks(tier="S")`, `deck.level_board(level)`, `deck.key_points`.
- CLI: `refresh-lolchess --from <dir>`. `refresh`는 저장 파일이 새로 있을 때만 포함한다.

## 4. 두 소스의 결합 제안(구현 후)

- lolchess S등급 덱은 "어떤 덱을 목표로 하는가"와 그 계획(레벨별 보드, 공략 핵심, 캐리 아이템)을 맡는다.
- MetaTFT는 수치를 맡는다: 유닛·아이템 성적, 아이템 보유 시 덱 성적, Early Comps 스테이지별 보드 성적.
- 조인 방법: lolchess 덱의 핵심 유닛 집합과 MetaTFT comp의 core_units를 Jaccard로 대응시켜, 덱마다 MetaTFT `comp_id`(avg_place, games)를 붙인다. 대응하는 comp가 없으면 통계 없음으로 표시한다(신뢰도를 낮춘다).
- 현재 S등급 덱 요약(이름, 캐리, 레벨 7/8 보드)은 데이터를 받지 못해 **작성하지 못했다**.

## 5. 정책 메모
- robots.txt는 `/search` 외 허용이지만, WAF 챌린지는 운영자가 비브라우저 접근을 막겠다는 의사로 본다. 우회하지 않는다.
- 증강 승률과 실시간 추천에 관한 Riot 정책 메모는 01·28 보고서와 같다.
