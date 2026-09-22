---
name: tft-stats-collect
description: "TFT 통계 데이터 수집·정규화 방법. MetaTFT, tactics.tools, lolchess.gg, OP.GG, Community Dragon 등에서 챔피언·증강·아이템·덱 통계(평균 등수, Top4, 표본 수) 및 정적 데이터(챔피언/특성/아이템/증강 목록, 아이콘) 가져오기, canonical ID 매핑, 패치별 DB 갱신 시 사용."
---

# TFT Stats Collect

## 1. 데이터 두 종류를 구분한다

| 종류 | 내용 | 변경 주기 | 권장 출처 |
|---|---|---|---|
| **정적 데이터** | 챔피언(코스트, 특성), 특성 단계, 아이템 조합표, 증강 목록, 아이콘 | 패치 | Community Dragon (`raw.communitydragon.org` 의 TFT JSON), Riot Data Dragon |
| **통계 데이터** | 덱/유닛/증강/아이템 평균 등수·Top4·빈도·표본 수 | 일 단위 | MetaTFT, tactics.tools, lolchess.gg, OP.GG |

정적 데이터가 canonical ID(`TFT{set}_Ahri` 같은 apiName)의 기준이다. 모든 통계는 이 ID로 매핑해서 저장한다. 소스마다 이름 표기가 달라(한글/영문/약칭) 매핑 없이는 조인이 불가능하다.

## 2. 소스 조사 절차
1. 각 사이트의 네트워크 요청(브라우저가 호출하는 JSON API)을 확인한다. 공개 JSON 엔드포인트가 있으면 HTML 파싱보다 훨씬 안정적이다.
2. 공식 API/MCP(OP.GG 등)가 있으면 우선 검토한다.
3. 소스별로 기록: 제공 지표, 필터(티어, 패치, 스테이지별 증강), 표본 크기, 갱신 주기, 인증 필요 여부, 이용약관/robots.txt 제약.
4. 증강은 **제시 시점(2-1, 3-2, 4-2)별 성적**이 있는 소스를 우선한다. 같은 증강도 시점에 따라 가치가 크게 다르다.
5. 소스 비교표에 **조건부 통계(아이템/증강 필터)와 스테이지별 통계·빌드업 보드 제공 여부**를 반드시 열로 넣는다. 이 둘이 없는 소스는 보조 소스로만 쓴다.
6. 결과를 `_workspace/01_stats-researcher_sources.md`에 비교표로 남기고 추천 1순위+백업 소스를 제시한다.

## 3. 저장 스키마 (초안 — 최종은 contracts.py 기준)

추천 엔진은 **"최종 덱은 보유 아이템·증강이 결정"**하고 **"라운드마다 강한 것으로 빌드업"**하는 구조다(jev-recommender 스킬 1절). 그래서 전체 승률뿐 아니라 아래 두 종류의 통계가 반드시 필요하다:
- **조건부 통계:** 아이템 보유 시 덱 성적, 증강 보유 시 덱 성적 (예: MetaTFT Data Explorer처럼 필터 조합 가능한 소스)
- **스테이지별 통계:** 스테이지/레벨별 유닛·보드 성적, 덱별 빌드업(초반/중반) 보드

```
units(unit_id, patch, avg_place, top4_rate, win_rate, games)
unit_stage(unit_id, stage, patch, avg_place, games)              # 스테이지별 유닛 성적 (빌드업용)
comp_buildup(comp_id, stage, units[], patch, avg_place, games)    # 덱별 초반/중반 빌드업 보드
comp_given_item(comp_id, item_id, patch, avg_place, games)        # 아이템 보유 시 덱 성적
comp_given_augment(comp_id, augment_id, patch, avg_place, games)  # 증강 보유 시 덱 성적
shop_odds(level, cost, prob)                                      # 레벨별 상점 확률 (정적)
unit_items(unit_id, item_id, patch, avg_place, games)            # 유닛별 아이템 성적
augments(augment_id, stage, patch, avg_place, top4_rate, games)  # stage: 2-1/3-2/4-2/all
comps(comp_id, patch, name, core_units[], avg_place, top4_rate, games, carry_units[], carry_items{})
items_recipe(item_id, component_a, component_b)                  # 정적 데이터
```

- 모든 통계 행에 `games`(표본 수)와 `patch`, `source`, `fetched_at`을 저장한다.
- 저장 형식은 SQLite 1파일(`data/stats/{patch}.sqlite`)을 기본으로 한다. 실시간 조회가 빠르고 의존성이 없다.

## 4. 수집기 구현 규칙
- `src/tft_advisor/stats/collectors/{source}.py` — 소스마다 한 파일, 공통 인터페이스 `collect(patch) -> dict[str, list[Row]]`
- 요청 간 1초 이상 간격, User-Agent 명시, 응답 원본을 `data/raw/{source}/{date}/`에 캐시(재파싱 시 재요청 불필요)
- 이름→ID 매핑 실패 항목은 버리지 말고 `unmapped.json`에 기록해 QA가 확인하게 한다
- CLI: `python -m tft_advisor.stats update [--source X] [--patch Y]`

## 5. 정책 메모
- Riot 정책상 레전드/레전드 증강 승률 표시는 금지 항목이다. 사용자가 개인용 실시간 모드를 선택했으므로 수집은 하되, 이 사실을 보고서에 기록해 둔다.
