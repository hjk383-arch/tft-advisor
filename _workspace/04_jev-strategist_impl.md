# 04 jev-strategist: advisor 구현 보고 (Phase 3)

작성일: 2026-09-22 / 작성자: jev-strategist / 기준: `02_jev-strategist_design.md`(2026-09-22 개정, 이번에 Phase 3 행 추가), CONTRACT_VERSION 0.2.0
범위: `src/tft_advisor/advisor/`, `tests/advisor/`, `tests/fixtures/states/`, `tests/fixtures/stats/`. contracts/config/TOML과 stats/vision 코드는 수정하지 않았다. `pyproject.toml`은 `advisor` extra 한 줄만 바꿨다(아래 6절).

## 0. 요약
- 설계 §1~§8을 구현했다. 파이프라인: 신뢰도 필터 → 1차 필터 → **폴백 프록시를 항상 먼저 계산** → Jev state와 질문 묶음 → 게이트웨이(캐시·서킷·예산) → 합성 → `Recommendation`.
- Jev 모드는 세 가지다. **mock**(기본값, 결정적, 네트워크 없음), **live**(TypeSafe), **off**(`jev_disabled` 폴백). mock에서도 파이프라인 전체(state·질문·해시·캐시·합성)가 그대로 돈다.
- QA 재검증 5절의 jev 1~5를 코드와 설계 문서에 반영했다.
- 테스트: `pytest -q -rxs` 결과 **242 passed, 3 skipped**(live 3건은 기본 skip). advisor 테스트는 65개다.
- live 스모크는 6회 호출했다. 모델 `jev-1.13.0`. 연결이 열린 뒤 **225~265 ms**, 첫 호출 **600~700 ms**. 입력은 **10.0k~14.9k tok/요청**이다. 통계를 전부 쓰고 설정 기본 예산(1.2s)을 둬도 폴백 없이 통과했다.

## 1. 모듈 구성 (`src/tft_advisor/advisor/`)
| 파일 | 역할 | 설계 |
|---|---|---|
| `stats_source.py` | `AdvisorStats` Protocol(`stats.repository.StatsRepository`의 부분집합), `JsonStatsAdapter`(변환 JSON + 정적 데이터), `load_stats()`: `open_repository()`가 있으면 그것을 쓰고, 없으면 JSON 어댑터로 대체 | – |
| `features.py` | `View`(신뢰도 필터를 거친 상태), 구간 라벨, 조합표, 사본 수·2성/3성, b(x,c), 레벨 추정, 빌드업 보드 선택, `?` 처리 | §2.2, §4.1~4.3, §5.4 |
| `candidates.py` | min_games, 중복 제거(Jaccard + 같은 carry), I/A/U/S, p(c), 쿼터(직전 표시 → S 상위)를 포함한 상위 N | §2 |
| `jev_state.py` | 영어 state(JSON), `NameBook`(ID ↔ 고유 영어 이름), `state_hash` | §4, §8.3 |
| `questions.py` | 질문·레벨 문구(C1~C4, S1-owned/-noboard, S2, S3, A1, A2-hp/-nohp, A3, I1-hp/-nohp), `QUESTIONS_VERSION="q2"`, wire dict + mock 힌트(`QMeta`) | §3, §4.3 |
| `jev_client.py` | `LiveJevBackend`(AsyncTypeSafeClient + RetryPolicy), `MockJevBackend`, `JevGateway`(jev_enabled → 캐시 → 인증 비활성/서킷 → `wait_for(retry_budget)` → 예외를 FallbackReason으로 매핑) | §8 |
| `scoring.py` | `Scorer`: 덱 합성(J, m, 질량 이전, 히스테리시스, 표시 컷, 타이브레이커, undecided), TargetComp(§5.4), 상점(§5.3), 증강(§7, 반사실 재평가 포함), 아이템(§6), component_priority | §5~§7 |
| `engine.py` | `Advisor`(세션: prev_shown/resource_sig/equipped_tracked/last), 모드 분기, 질문 묶음, `create_advisor`, `advise` | §1, §8.4 |

## 2. 공개 API
```python
from tft_advisor.advisor import create_advisor, Advisor, load_stats
adv = create_advisor("mock")          # "mock" | "live" | "off" | "auto"(키가 있으면 live, 없으면 mock)
rec = adv.advise(game_state)          # 동기. Recommendation | None
rec = await adv.recommend(game_state) # 비동기(앱 루프 안에서 쓸 때)
adv.reset(); adv.close()              # 새 게임 / 종료(Jev 클라이언트 닫기)
Advisor(stats=..., settings=..., weights=..., backend=MockJevBackend(...))  # 주입(테스트·리플레이)
```
- **반환 규칙(app-integrator 필독)**
  - `loading`/`game_over`: 세션과 캐시를 초기화하고 `None`을 돌려준다.
  - `combat`/`item_select`/`unknown`: 직전 `Recommendation` 객체를 그대로 돌려준다. 직전 추천이 없으면 `None`이다.
  - `carousel`: Jev를 부르지 않는다. 직전 추천에 `component_priority`만 다시 계산해 넣은 사본을 돌려준다. 직전 추천이 없으면 planning 경로를 탄다(상점 제외).
  - `planning`: 덱 + 상점 + 아이템을 채운다. `augment_select`: 덱 + 증강 + 아이템을 채운다(`shop=[]`).
- `advise()`는 advisor 전용 이벤트 루프를 재사용한다. Jev HTTP 연결을 유지하려는 목적이다. 이미 실행 중인 asyncio 루프 안에서는 `await recommend()`를 쓴다.
- 새 state가 와서 이전 호출을 버리는 처리(§8.2)는 앱이 맡는다. 태스크를 취소하면 게이트웨이가 `CancelledError`를 삼키지 않고 그대로 전달한다.
- `debug`에 담기는 것:
  - `jev_state`, `question_ids`, `n_questions`, `jev`(model/request_id/tokens/call_ms/cached/원시 점수)
  - `candidates`(p, I, A, U, S, adj, L, 항별 avail/gate/norm/src, score, H, final)
  - `shop` 행, `augment` 행(`counterfactual_top` 포함), `item` 행
  - `resource_sig`, `equipped_tracked`, `fallback_detail`
  - 가중치만 바꿔 재계산할 수 있을 만큼 원값을 남긴다.

## 3. mock vs live
| | mock (`MockJevBackend`) | live (`LiveJevBackend`) |
|---|---|---|
| 네트워크 | 없음 | `AsyncTypeSafeClient(model=advisor.jev_model, timeout=jev_timeout_s, retry=RetryPolicy(max_retries=jev_max_retries, 0.1/0.2s, {429,5xx,529}, respect_retry_after, timeout=jev_retry_budget_s))`. 키는 SDK가 `TYPESAFE_API_KEY`에서 직접 읽는다. 코드는 키가 있는지만 확인하고 값은 읽지도 남기지도 않는다 |
| 답 | 질문마다 코드 프록시 힌트(C1=I, C2=A, C3=U, S1=S_now, S2=C_path, A1=t(a,c), I1=bis, C4=프록시 final + 자원이 없을 때 undecided)로 결정적 Score/Choice를 만든다. confidence는 0.8 | 실제 모델 |
| 테스트 기능 | `overrides={qid: 비율\|라벨}`, `confidence=`, `fail=FallbackReason\|예외`, `delay_s=`, `last_state/last_questions` | – |
- mock 결과는 폴백 결과와 수치가 거의 같다(힌트가 곧 프록시다). 목적은 앱을 API 없이 돌리는 것과 파이프라인 검증이다. Jev 판단의 품질을 대신하지 않는다.
- 폴백(§8.1)은 닫힌 9종으로 판정한다.
  - 판정 순서: `jev_disabled`(설정 또는 off) → 캐시 적중 → `auth`(키 없음/401/403이면 세션 동안 비활성) → `circuit_open`(일시적 실패 3연속이면 60초) → 호출.
  - 호출 예외는 이렇게 매핑한다: 429→`rate_limited`, 529→`overloaded`, 5xx→`server_error`, TypeSafeAPITimeoutError와 예산 초과→`timeout`, 연결 오류→`connection`, 그 밖의 4xx·응답 파싱 실패·예상 못 한 예외→`bad_request`(ERROR 로그에 질문 ID를 남긴다).
  - 폴백일 때도 `state_hash`는 보낼 예정이던 state로 계산한다.

## 4. live 스모크 결과 (2026-09-22, 총 6회 호출)
| 호출 | 조건 | 질문 수 | Jev call_ms | 추천 전체 ms | 입력 tok | 결과 |
|---|---|---|---|---|---|---|
| 1 | s01 planning, mini 통계, 첫 호출 | 27 | 676 | 1256* | 11,838 | jev_used, 1위 zyra |
| 2 | 같은 advisor, gold+1(연결 유지 상태) | 27 | **263** | 270 | 11,838 | jev_used |
| 3 | s05 augment_select, 새 advisor | 45 | 702 | 710 | 14,889 | jev_used |
| 4 | s11 MVP(hp/board 없음), 연결 유지 상태 | 20 | **225** | 231 | 9,991 | jev_used |
| 5 | s01, **전체 통계 + 설정 기본 예산(1.2/1.5/2.0s)**, 첫 호출 | 27 | 607 | 616 | 11,955 | jev_used(폴백 없음) |
| 6 | s05 재호출(답 품질 기록용) | 45 | – | – | – | `_workspace/04_jev_live_s05_answers.json` |

\* 첫 호출의 "전체"에는 SDK import와 클라이언트 생성(약 0.55s)이 들어 있다. 앱 시작 시 한 번 미리 호출해 두는 것을 권장한다(7절).
- 모델 응답: `jev-1.13.0`. 모든 질문 ID에 답이 왔다(Score·Choice 형식과 인덱스 참조 모두 400/422 없음).
- 답 품질 스팟 체크(s05, live):
  - `aug_pick` = "Nature's Shelter"(Elderwood 특성 증강), 확률 0.64.
  - A1(Elderwood 증강 → Elderwood Aphelios)은 2.9/3, confidence 0.90.
  - `comp_pick` = "Elderwood Aphelios", 확률 0.68.
  - 최종 `augment.pick`도 같다.
  - 다만 A2(`aug_standalone`)의 confidence가 0.30~0.57로 낮다. 그래서 절반 정도 gate가 축소된다(튜닝 대상).
- 토큰: planning 11.8~12.0k, augment 14.9k, MVP 10.0k. 설계 §3.2 추정(7~10k / 10.5~14k)보다 약간 크다. 요청당 비용은 약 $0.0005~0.0006이다.
- 코드 경로 지연(mock, 전체 통계 57덱): s01 중앙값 11.6 ms(최대 26 ms), s05 6.6 ms. 목표 < 2s 가운데 코드 몫은 무시해도 될 수준이다.

## 5. 테스트
실행 명령: `.venv/bin/python -m pytest -q -rxs`. 결과는 **242 passed, 3 skipped**(skip = live)이고 advisor 테스트는 65개다.
- live 실행: `TFT_LIVE_JEV=1 .venv/bin/python -m pytest tests/advisor/test_advisor_live.py -m live -rxs -s`. 마커 `live`는 `tests/advisor/conftest.py`에서 등록한다.
- `tests/fixtures/stats/mini_18.json`(1.6MB): 실제 스냅샷에서 뽑은 11덱과 그 덱의 증강 등급, 단일 아이템 UnitItemStats다. 통계를 다시 수집해도 기대값이 흔들리지 않게 만든 파일이다. 재생성: `tests/fixtures/stats/build_mini.py`.
- `tests/fixtures/states/` 14개(설계 §9 형식에 `steps`/`branch`/`jev.fail` 추가):

| 파일 | 검증 |
|---|---|
| s01 / s02 | 같은 보드에 AP 아이템이면 zyra, AD 아이템이면 elderwood-aphelios. **1위 덱과 carry가 다르다** |
| s03 | 2-1 상점: 저코 빌드업 3종의 점수 > 4코 캐리. 1위 태그는 now_power |
| s04 | 4-1: 목표 덱 핵심 4코(센티널)가 1위, 태그 final_comp |
| s05 | 증강 선택: Elderwood 특성 증강을 고른다. 반사실 1위 덱에 Elderwood 특성이 있다 |
| s06 | 1-4 빈 상태: undecided(P=0.6) 완화로 3개 표시. stat_norm 순, item/augment=None |
| s07a / b | 비BIS 재료: HP 90이면 hold, HP 18이면 hold 안 함 |
| s08 | 529 연속: `overloaded` 폴백, 1위는 s01과 같다. 2s 미만 |
| s09 (+branch) | 재료만 추가하면 시그니처가 같아 1위 유지. B 캐리 BIS(스트라이커 도리깨 + 기원자 상징)를 추가하면 invoker-ahri가 1위 |
| s10 | 신뢰도 낮은 상점 칸과 아이템: S1/S2/C1/I1 질문 없음, buy=False, "아이템 미인식", 모두 missing |
| s11 | MVP: health/board/bench/active_traits/copies 키 없음. S1-noboard·I1-nohp 문구, two_star 없음, owned/missing `[]` + "보드 미인식", hold=True, next_buildup 레벨 7 |
| s12 | 장착 추적: 벤치에서 사라진 대천사가 owned로 유지되고 C1 state의 completed_items에 남는다 |
| s13 | QA N5a: others의 유물 리치베인이 owned로 잡히고 state completed_items에 들어간다 |

- 공통 불변식(모든 스텝):
  - 계약 재검증 통과, target ≤ 3, score ∈ [0,1]
  - state_hash는 64자 hex, latency ≥ 0, created_at은 UTC
  - `jev_used ⇔ fallback_reason is None`
  - 질문 인덱스가 state 범위 안에 있다
  - 출력에 API 키 문자열이 없다
- 단위 테스트(`test_advisor_units.py`)가 다루는 것:
  - hp 구간, 레벨 추정(공집합 규칙 포함), next_buildup, 조합(같은 재료 2개 규칙), 사본·2성, `?` 치환
  - b(x,c) 사다리, 신뢰도 필터(board만 있으면 "모름"), 1차 필터 쿼터·중복 제거
  - 자원이 없을 때 질량이 통계로 넘어가는지, 낮은 confidence에서 gate가 축소되는지
  - items_ready: 유물, 대천사 2개, 재료 중복 사용 방지
  - 상점 골드 누적, 2성 보너스(보유 유닛을 알 때만), hp_danger_shift(hp를 모르면 미적용)
  - hold 규칙 5종(Jev hold + critical이면 무시 포함), 재료 비중복 제안, 증강 폴백, 신규 증강 설명 없음
  - 히스테리시스, 시그니처에서 재료 제외, component_priority, -nohp 변형과 키 생략, main_carry=CompStats.carry
  - HTTP 상태 9종과 기타 예외 매핑, 캐시/비활성, 서킷(3회 → open → 쿨다운 후 재시도), auth 세션 비활성, 예산 타임아웃, 키 없는 live → auth 폴백
  - 모드(combat/unknown/item_select 유지, carousel은 Jev 없이, game_over 초기화), 장착 추적과 판매 복귀, 캐시 적중, 키 유출 없음(로그 포함), 전체 통계 지연 < 500 ms
- stats 저장소 연동 확인: `load_stats()`는 이제 stats-researcher의 `open_repository()`(InMemoryStatsRepository)를 쓴다. 같은 스냅샷에서 JSON 어댑터와 비교하니 fixture 16스텝 전부 출력이 **완전히 같았다**.

## 6. QA 재검증 요청 처리 (5절 jev-strategist)
| # | 요청 | 처리 |
|---|---|---|
| 1 | 유물·찬란한 BIS | 자원 풀 P, I(c), equipped_tracked 추적, C1 state `completed_items`에 `ItemState.others` 중 artifact/radiant를 넣었다(category가 None이면 정적 데이터로 판정). 조합표에 없는 BIS는 craftable이 되지 않고 component_priority에서 건너뛴다. 설계 §2.2, §4.3(c), §5.4에 반영했다. 테스트: s13, `test_items_ready_artifact_owned_and_never_craftable` |
| 2 | §4.2 role / main_carry | `main_carry` = `CompStats.carry`. final_board role은 `main carry`/`secondary carry`/tank/support, `tanks` = role==tank, role None은 키를 뺀다. 설계 §4.2 문단을 교체했다. 테스트: `test_main_carry_is_compstats_carry` |
| 3 | 레벨 추정 공집합 | `L = max(1, min(level_timing) − 1)`. U(c), state buildup, C_path는 덱별 L을 쓰고 S_now는 덱별 추정의 최빈값을 쓴다. 설계 §5.4에 반영. 테스트: `test_estimate_level_rules` |
| 4 | S3 `?` | `?`→`X`로 바꾼다. 치환 뒤 숫자가 없으면 S3 gate를 강제로 낮춘다(증강 A1/A2도 같은 규칙). 증강 설명은 `desc_en`(`?` 없음) → `desc_en_opgg` → `desc_en`(치환) 순으로 고른다. 설계 §0, §3(S3)에 반영 |
| 5 | 문서 수치 | §2.1 604/713/772/919, §7 덱별 등급 32덱(R13 기각 5), 신규 증강 5개, desc `?` 193/597(OP.GG로 대체한 뒤 48). `design_refs_check.py` 결과: config 참조 64개 모두 존재, 계약 참조 누락은 `item_offer`(보류분) 하나, FallbackReason 일치 |

- `pyproject.toml` `advisor` extra: `"typesafe_sdk"` → `"typesafe_sdk>=0.7.1,<0.8"`(0.7.1에서 검증했다. 0.x라 마이너 버전에 상한을 둔다). SDK는 `httpx2`와 `tenacity`를 끌어온다.

## 7. 설계와 다르거나 단순화한 점 (qa 확인 요청)
1. 상징의 `bis` 1.0 조건을 설계 §6-2의 "+1명이면 구간 도달"까지 따지지 않고 b(x,c)의 "상징 특성 ∈ key_traits"로 단순화했다. 활성 특성 수를 알 때 정밀화할 수 있다.
2. S_now에서 L < 4이면 레벨 4 보드를 쓴다(buildup은 4~10만 있다). L을 모르면 S_now = 0이다.
3. s09 branch는 B 덱으로 넘어가도록 완성템 2개(스트라이커 도리깨 + 기원자 상징)를 넣었다. 설계 문구는 1개다. 히스테리시스 보너스 자체(H, final = score + bonus, 시그니처가 바뀌면 H=0)는 단위 테스트로 직접 검증한다. 직전에 표시된 덱 **전부**가 보너스를 받으므로, 표시된 덱끼리의 순위는 보너스로 바뀌지 않는다(설계 그대로).
4. 벤치에 있는 완성템은 `ItemSuggestion(components=[])`으로 보유자를 추천한다(§6-9). score = bis(x)다.
5. 신뢰도가 낮은 상점 칸은 `ShopAdvice(kind=원래 kind, offer_id=id, buy=False, score=0, reason="인식 불확실")`로 낸다.
6. mock 모드의 `comp_pick` undecided 힌트는 자원이 하나도 없을 때 P≈0.6이 되게 잡았다(임계값 0.5를 넘긴다). 폴백 모드에서는 "자원 신호 전무"면 undecided로 본다.
7. 첫 호출 지연(SDK import + TLS)이 약 1.2s다. 앱 시작 시 `Advisor`를 만들고 가벼운 요청을 한 번 보내 두는 것을 권장한다(app-integrator).

## 8. 계약·설정 변경 요청 (app-integrator 소유, 필수 아님)
| # | 대상 | 요청 | 이유 |
|---|---|---|---|
| 1 | settings `[advisor]` | (선택) `jev_backend: Literal["live","mock","off"] = "live"` 추가 | 앱·CLI가 코드 수정 없이 mock으로 돌 수 있게. 지금은 `create_advisor(mode)` 인자와 `jev_enabled`만 있다. `--no-jev`는 `"off"`로 연결하면 된다 |
| 2 | 계약 | 없음(0.2.0으로 충분) | carousel·combat는 직전 추천을 재사용하므로 "Jev 불필요" 사유가 필요 없다. `Recommendation | None` 반환 규칙은 2절 참고 |
| 3 | 참고 | `GameState.item_offer` | 계속 보류(§10-1). item_select 모드는 직전 추천을 유지한다 |
| 4 | stats-researcher 참고 | – | `open_repository()` 연동을 확인했다(출력 동일). advisor가 쓰는 메서드는 `stats_source.AdvisorStats`에 있는 것뿐이다(`augment_tier` 정확 범위 + 자체 폴백, `unit_item_stat(unit, item, comp_id)` 단일 아이템) |

## 9. 남은 과제
- live 답으로 가중치 튜닝(qa): A2 confidence가 낮다. S1/S2의 Jev 몫(λ)을 검증해야 한다. `debug.candidates`와 `jev.scores`로 재계산할 수 있다.
- live 답을 리플레이하는 백엔드(설계 §9의 `jev_replay`)는 만들지 않았다. `_workspace/04_jev_live_s05_answers.json`에 원시 답을 기록해 두었다. 필요하면 `MockJevBackend(overrides=...)`로 재생할 수 있다.

---

## Fix round (Phase 3 QA 반영, 2026-09-22)

기준: `04_qa_advisor.md`(jev-strategist 1~5), `04_qa_stats.md` jev 1·2. live Jev 호출 없음(mock/off만). contracts/config/TOML·stats·vision 파일은 수정하지 않았다. 설계 문서 변경 이력에 "Phase 3 fix" 행을 추가했다.

### 1. [FAIL 1f] 상징 BIS 규칙 — 해결
- `features.emblem_advances(t, c, stats)`를 새로 두고 `item_fit`의 상징 분기에서 쓴다. 규칙(설계 §2.2 "상징 규칙"):
  `req`=key_traits의 목표 인원, `n`=최종 보드 유닛 중 그 특성 수, `T=max(req,n)`. **`req ≥ 2 ∧ (n < req ∨ T+1 ∈ trait_breakpoints)`일 때만 `emblem_key_trait`**, 아니면 `emblem_other`.
  "구간 쪽으로 다가감"은 key로 치지 않는다. 최고 구간이 아닌 거의 모든 특성이 해당돼 과대평가를 그대로 재현하기 때문이다. 기준 인원은 현재 보드가 아니라 덱 최종 보드다. I(c)·bis·holder가 같은 b(x,c)를 공유해야 하기 때문이다.
- 실제 57덱에서 상징 경로로 key(1.0)를 받는 (상징, 덱) 쌍: **274 → 68**. 1개 이상 받는 덱: 57 → 42.
  - 0이 된 특성: Juggernaut 29→0, Vanguard 25→0, FloraFatalis 23→0, Defender 21→0, Brawler 21→0, Spellweaver 18→0, Primal 13→0, Blossom 13→0, Elderwood 11→0, Fae 10→0, Blackthorn 8→0.
  - 남은 특성: 구간 간격이 1인 특성(Inferno 13, Lunar 13, Invoker 12, Hunter 12, Rapidfire 8, Executioner 6, Coven 2)과 보드가 목표 인원에 못 미치는 경우(Slayer 2).
  - Sprykin(blackthorn-veigar 3→4, 구간 3/5/7)은 `emblem_other`가 됐다. 그래서 **s11 `hold=True`가 mini와 실제 `open_repository()`에서 같다.** QA 스크립트 4b는 전 스텝 OK, FAILS 0이다.
- 테스트:
  - `test_item_fit_ladder` 기대값 변경: Juggernaut 6/6은 other, Hunter 2→3은 key.
  - `test_emblem_advances_rule`(mini 전 덱 규칙 대조), `test_real_emblem_overscoring_gone`, `test_s11_hold_same_on_mini_and_real`.

### 2. [WARN 1g] 히스테리시스 — 설계 수정 + 구현
- H(c)는 시그니처가 불변일 때만 적용된다. **직전 1위 = 1, 직전 2·3위 = 0.25, 그 외 0**(`Scorer.hysteresis_weight`).
- 직전 1위가 보호 중이면(order[0]의 H=1) comp_pick 타이브레이커로 1·2위를 뒤집지 않는다.
- 설계 §5.2·§8.4를 갱신했다. 비율 0.25는 코드 상수 `scoring.HYSTERESIS_OTHER_SHARE`다. weights에 `[comp] hysteresis_other_share`가 생기면 코드가 그 값을 읽는다(`getattr`).
- `test_hysteresis_protects_previous_top1`의 strict xfail 마커를 지웠고 **통과한다**. 추가 테스트: `test_hysteresis_weights`.

### 3. 경미 항목
- **timeout_s**: `recommend()`가 `budget = timeout_s − 경과 − 0.1s`(`engine.CODE_RESERVE_S`)를 `JevGateway.ask(budget_s=)`에 넘긴다.
  - `wait_for` 한도는 `min(jev_retry_budget_s, budget)`이다. 남은 시간이 0 이하면 호출 없이 `timeout` 폴백을 돌려주고, 이 경우는 서킷 실패로 세지 않는다.
  - 설계 §8.2·§10a를 갱신했다. 테스트: `test_gateway_budget_limits_wait`, `test_recommend_passes_overall_budget`.
- **carousel**:
  - 직전 추천이 없으면 `_full(use_jev=False)`로 처리한다. 상점·증강은 제외하고 `jev_used=False`, `fallback_reason=jev_disabled`이며 Jev 호출은 0이다.
  - 직전 추천이 있을 때 carousel 결과(component_priority 갱신본)를 `session.last`에 저장한다. 그래서 carousel 직후 combat은 갱신본을 돌려준다.
  - 설계 §1.1을 갱신했다. 테스트: `test_carousel_without_previous_does_not_call_jev`, `test_carousel_result_becomes_last`.
- (선택 1d) `counterfactual_top`에도 설명 손실 증강의 `force_low`를 적용했다.

### 4. stats QA jev 1·2
- **실제 저장소로도 advisor 스위트 실행**: 신규 `tests/advisor/test_advisor_fixround.py`에 마커 `real_stats`를 conftest에 등록했다. fixture 전체 기대값(§9)을 두 저장소로 매개변수화해 돌린다.
  - `open_repository()`(실제 SQLite, 57덱)
  - `InMemoryStatsRepository.from_doc(mini)`
  - 기존 JSON 어댑터 경로는 `test_advisor_fixtures.py`가 그대로 맡는다.
  - 선택 실행: `-m real_stats`.
- **s09 branch(완성템 2개 추가 → invoker-ahri 1위)는 실제 통계에서 근소차로 성립하지 않는다.**
  - 결과는 elderwood-ezreal 0.799 vs invoker-ahri 0.791이다. 보드 6유닛이 ezreal에 맞고(U 0.65 vs 0.25), 완성템 4개가 두 덱 모두 I(c)를 포화시킨다(`item_saturation=2`).
  - 이번 변경과는 무관하다(ezreal에서 Invoker 상징은 원래도 other였다). 실제 저장소 테스트는 "invoker-ahri 상위 2 진입 + 순위 상승"만 확인한다.
  - **튜닝 과제**: 보유 BIS 개수 차이가 포화로 사라진다. `item_saturation` 상향 또는 carry BIS 가중 검토가 필요하다(TOML 소관이라 이번에 바꾸지 않았다).
- **unit×item 덱 한정 행 없음(12/984) → 전체값 폴백**:
  - `AdvisorStats.unit_item_stat`에 `fallback_overall` 키워드를 추가했다(repository와 같은 시그니처).
  - `JsonStatsAdapter`도 comp_id=None 또는 폴백일 때 덱 한정 행을 games 가중 평균한 파생값을 준다(repository `_aggregate`와 같은 규칙, W3 의미 차이 해소).
  - `Scorer.item_stat`은 전체 행이면 games×0.25(`OVERALL_ITEM_STAT_GAMES_FACTOR`) 후 같은 shrink(prior 0)를 건다. debug `item_stat_overall`에 기록한다.
  - 실제 데이터에서 12쌍 모두 폴백 행이 있다. 테스트: `test_unit_item_overall_fallback_adapter_matches_repository`, `test_item_stat_uses_overall_when_comp_row_missing`.

### 5. 필요 설정 키(제안, config/TOML 소유자에게)
- `[comp] hysteresis_other_share = 0.25`(float, [0,1]). 코드는 키가 있으면 읽는다.
- `[item] overall_stat_games_factor = 0.25`(float, [0,1]). 현재는 코드 상수다.
- (선택) `[item_fit] emblem_toward`: "구간 쪽으로 다가감" 중간 등급이 필요해지면 쓴다. 지금은 쓰지 않는다.

### 6. app-integrator 전달(구현하지 않음)
1. `Recommendation.target_comps`는 **advisor 순서 그대로** 표시한다. 타이브레이커 때문에 점수가 단조가 아닐 수 있다. 점수로 다시 정렬하지 않는다.
2. `create_advisor("auto")`는 `TYPESAFE_API_KEY`가 있으면 곧바로 live(과금)가 된다. 명시적 설정 **`[advisor] jev_backend = "mock" | "live" | "off"`(기본 `"mock"` 또는 `"off"`)** 를 제안한다. `--no-jev`는 `"off"`로 매핑하고, live는 사용자가 명시할 때만 켠다.
3. carousel(직전 추천 없음)은 이제 `jev_used=False, fallback_reason=jev_disabled`를 돌려준다. UI의 "Jev 미사용" 표시가 이 경우 "캐러셀(통계)"로 보이도록 `debug.fallback_detail`을 참고할 수 있다.

### 7. 테스트
- `.venv/bin/python -m pytest -q -rxs tests/advisor`: **222 passed, 3 skipped**(live). xfail 0.
- 전체 실행(최종): 첫 실행 때 vision 병렬 수정 중이라 vision 5건이 실패했다. 재실행 결과는 **443 passed, 3 skipped**(xfail 0)다.
- QA 스크립트 `_workspace/qa_scripts/advisor_qa.py`: 4b 전 스텝 OK, 매트릭스 170 OK, FAILS 0.
