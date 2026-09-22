# 04 qa-validator: advisor 모듈 점진 QA (Phase 3)

작성일: 2026-09-22 / 범위: `src/tft_advisor/advisor/`만 / 기준: `02_jev-strategist_design.md`(§2~§8, §4.3, §5.4, §8.1, §10a), `04_jev-strategist_impl.md`, `04_stats-researcher_impl.md`
live Jev 호출: **안 함**. 네트워크 가드(`_workspace/qa_scripts/no_network_plugin.py`)를 켜고 돌렸다. `TYPESAFE_API_KEY`가 설정된 상태와 없는 상태 모두에서 연결 시도는 0건이었다.

## 요약: PASS 16 / FAIL 1 / WARN 3 (PASS 행 중 1d·1i에 경미 지적 포함)

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1a | 1차 필터: min_games, dedupe(Jaccard+같은 carry), p(c)=Σw·{I,A,U,S}, 쿼터(직전 표시 → S 상위 min(quota,N)) | PASS | `candidates.py:35-43,121-159`. §2.1~2.3와 일치 | – | – |
| 1b | 덱 합성 J/m/질량 이전, final=min(1, s+bonus·H), 표시 컷(show_ratio / undecided), tie_eps 타이브레이커, 폴백 undecided | PASS | `scoring.py:126-176`. §5.2와 식이 같다 | – | – |
| 1c | 상점: S_now(레벨 buildup count 가중 max 정규화), C_path μ 사다리(core>final>next>cur, 덱마다 첫 번째 일치), λn/λp·gate, 2성/3성 bonus(보유 유닛 알 때만), hp_danger_shift(unknown이면 미적용), 골드 누적, 특수 상품 | PASS | `scoring.py:281-399`. L<4일 때 레벨 4 보드를 쓰는 것(자체 보고 #2)은 buildup이 4~10뿐이라 타당하다 | – | – |
| 1d | 증강: fit_comp=max[(1-commit)+commit·rel]·fit, jev_aug=w_comp·fit+(1-w_comp)·A2, ed 조회 순서, A1·A2 모두 gate<1이면 w_jev 축소, tie_eps+aug_pick, 반사실 C2' | PASS (경미) | `scoring.py:404-500`. `w_editorial` 대신 `1-w_jev`를 쓰지만 config validator가 합=1을 강제하므로 값은 같다. `counterfactual_top`(`scoring.py:483`)은 설명 손실 증강에 `force_low`를 주지 않는다(본 점수 경로는 준다) | jev-strategist | (선택) `jscore(..., force_low=lost)`를 반사실 계산에도 적용 |
| 1e | 아이템: b(x,c) 순서, item_score=w_bis·bis+w_jev·gate·pj(+(1-gate) 질량을 bis로)+w_stat·st, holder 규칙, 재료 비중복 탐욕, hold(Jev/코드, critical이면 무시, unknown=moderate) | PASS | `features.py:222-233`, `scoring.py:505-603` | – | – |
| **1f** | **상징 BIS 단순화(자체 보고 #1)**: "+1명이면 구간 도달" 조건 없이 "특성 ∈ key_traits"이면 b=1.0 | **FAIL (중간, 실제 결함)** | `features.py:230-232`. 실제 통계 key_traits는 덱당 3~11개이고 **count 1인 곁가지 특성도 포함**한다(예 blackthorn-veigar: Battlemage 1). 그래서 Juggernaut 상징은 53덱 중 **27덱**, Vanguard 24, Brawler 23에서 캐리 BIS와 같은 1.0을 받는다. 재현: s11(3-2, 뒤집개+음전자 망토)은 mini 통계에서 `hold=True`지만, **실제 저장소에서는 Sprykin 상징 bis=0.94 → hold=False, 조합을 권한다.** 조합 가능한 상징이 I(c)를 올려 Sprykin 덱(blackthorn-veigar)을 2위로 끌어올리고(자기 강화), 그 결과 rel이 높아져 bis가 1.0에 가까워진다 | jev-strategist | `item_fit`의 상징 규칙을 좁힌다. 예: (a) 덱 최종 보드에서 그 특성 count+1이 `trait_breakpoints`의 다음 구간에 닿을 때만 `emblem_key_trait`, 아니면 `emblem_other`. 보드를 알면 현재 활성 count 기준으로 §6-2를 그대로 적용한다. (b) 또는 count ≥ 2인 특성만 key로 본다. 바꾼 뒤 s11 기대값을 실제 저장소에서도 확인한다(`advisor_qa.py` 4b절) |
| 1g | 히스테리시스(자체 보고 #3): 직전 표시 덱 **전부**에 같은 bonus | WARN (설계 한계. 구현은 명세와 같다) | `scoring.py:141-142`. 재현: s09 step1은 zyra-amumu 0.687, spellweaver-veigar 0.685(둘 다 표시). 시그니처가 같은 다음 요청에서 2위가 bonus/2만큼 추월하면 **1위가 바뀐다**(H=1이 세 덱 모두). bonus는 "표시 vs 미표시" 경계만 지키고, 사용자가 보는 1위 널뛰기(SKILL §6의 의도)는 막지 못한다. 고정: `tests/advisor/test_advisor_qa04.py::test_hysteresis_protects_previous_top1`(xfail strict) | jev-strategist(설계+구현) | 직전 1위에 bonus를 전부 주고 나머지 표시 덱에는 비율(예 0.5)이나 0을 준다. 또는 순위 가중 H(c)=1/(rank). 고치면 xfail 마커를 지운다 |
| 1h | 수축: 덱 adj=shrink(avg,g,prior_avg_place), 조건부=shrink(·, prior=덱 adj), place_change prior=0 | PASS | `candidates.py:63-72`, `scoring.py:528`, `config.py:255` | – | – |
| 1i | §10a 설정 키를 코드가 읽는지 | PASS / WARN(경미) | `advisor_config_usage.py`: weights 47키 전부 읽는다(`w_editorial`은 위 1d처럼 간접). settings `[advisor]`에서 **`timeout_s`(추천 1회 전체 예산 2.0s)는 어디서도 읽지 않는다**. 실측 최악 경로는 Jev 예산 1.5s + 코드 <10ms라 실해는 없다 | jev-strategist | `recommend()`에 `timeout_s` 가드를 두거나(예산에서 코드 경로 시간을 빼고 `wait_for`에 넘기기) 설계 §10a에서 "문서상 예산"으로 명시 |
| 1j | s09 전환 테스트에 완성템 2개(자체 보고 #3 앞부분) | PASS (결함 아님) | 스트라이커 도리깨 **1개만** 넣어도 invoker-ahri가 1위다(0.784 vs 0.669). 기원자 상징 1개만 넣어도 1위(0.800). 설계 문구대로 1개로 충분하다. 회귀: `test_s09_single_bis_item_already_switches` | jev-strategist | (선택) fixture branch를 설계 문구대로 1개로 줄인다 |
| 2 | None 필드 × FallbackReason: 모든 경로가 계약 검증 통과 + 올바른 reason + `jev_used ⇔ reason None` | PASS | 17개 상태 변형(hp/board/bench/items/shop/level/stage/gold/augments/active_traits 각각 None, MVP, **전 필드 None**, 낮은 신뢰도, 장착만, augment_select 및 그 MVP) × 10(Jev 성공 + 9종) = **170조합, 예외 0, reason 불일치 0**. 폴백 덱 reasons에 "Jev 미사용"이 들어간다. 회귀: `test_missing_fields_x_fallback_reason`(110 케이스) | – | – |
| 2b | 타임아웃·재시도 예산·서킷 | PASS | mock 지연 5s → `timeout` 폴백, 경과 **1.51s**(예산 1.5s, 전체 2.0s 미만). server_error 3연속 → 이후 `circuit_open`, 백엔드 호출 3회에서 멈춤. 바깥 `CancelledError`는 그대로 전달한다(`jev_client.py:320`). RetryPolicy 인자는 §8.2와 일치한다(`jev_client.py:148-157`) | – | – |
| 3 | 화면 모드 계약 | PASS | loading/game_over → None, 세션·캐시·prev_sig 초기화. 직전 추천이 없으면 combat/item_select/unknown → None, 있으면 **같은 객체**. carousel → Jev 호출 0, 덱·상점·state_hash 동일, component_priority만 다시 계산. augment_select → `shop=[]`. 회귀: 기존 `test_modes_keep_reset_and_carousel` + `test_modes_without_previous`, `test_loading_resets_cache_and_session` | – | – |
| 3w | 모드 계약의 앱 루프 적합성 | WARN (경미 2건) | (a) 직전 추천이 없는 carousel(게임 시작 1-1)은 planning 경로라 **Jev를 1회 부른다**(질문은 comp_pick 1개 수준). 설계 §1.1의 "carousel: Jev 호출 없음"과 다르다. (b) carousel 사본은 `session.last`에 저장하지 않는다. 그래서 carousel 직후 combat은 **carousel 전 추천**(이전 component_priority)을 돌려준다. (c) 타이브레이커로 1·2위가 바뀌면 `target_comps[0].score < target_comps[1].score`가 될 수 있다(명세상 정상. UI는 점수로 다시 정렬하면 안 된다) | jev-strategist (a,b) / app-integrator (c) | (a) 직전 추천이 없는 carousel은 `backend` 없이(폴백 계산만) 처리하거나 `jev_disabled`가 아닌 별도 경로로 둔다. (b) carousel 결과를 `session.last`에 넣는다. (c) UI는 `target_comps` 순서를 그대로 표시한다 |
| 4 | 실제 `open_repository()` 연동 | PASS | 패치 18.2b, 57덱(min_games 후 53), 로드 1.3s. fixture 16스텝 × {mock, off} 모두 예외 없음, 계약 통과. **제작 불가 BIS가 craftable로 표시된 건 0**, component_priority는 전부 재료. 코드 지연 3~9ms. fixture expect 규칙을 실제 통계에 적용해도 **s11 `hold` 하나를 뺀 전부 성립**한다(s01 zyra-amumu ≠ s02 elderwood-aphelios, s04 센티널 final_comp, s05 Elderwood 증강 등). s11 hold 실패는 1f 때문이다. 회귀: `test_real_repository_all_fixtures`, `test_real_repository_ap_vs_ad_items_differ` | – | – |
| 5 | mock 결정성 / 기본 실행 네트워크 없음 / live 게이트 | PASS | 전 fixture 출력 다이제스트가 `PYTHONHASHSEED` 0/1/12345에서 같다(`8a663d53…`). 네트워크 가드 연결 시도 0(키 설정 상태 포함). live 3건은 `TFT_LIVE_JEV=1`일 때만 돈다(`tests/advisor/conftest.py:27-33`). 테스트는 `auto` 모드를 쓰지 않는다 | – | – |
| 5w | `create_advisor("auto")` | WARN (정보) | 키가 있으면 live가 된다. 앱이 기본값 `auto`로 부르면 사용자 환경(키 설정됨)에서는 곧바로 과금 호출이 난다. 의도된 동작이다 | app-integrator | 앱 CLI 기본값과 `--no-jev`→`"off"` 연결을 명시(impl 보고 §8-1 `jev_backend` 설정 제안 참고) |
| 6 | API 키는 `TYPESAFE_API_KEY`에서만 / 누출 없음 | PASS | advisor는 `os.environ`에서 존재 여부만 본다(`jev_client.py:143`). 클라이언트 생성 시 `api_key`를 넘기지 않아 SDK가 같은 환경변수를 읽는다(SDK `_core/config.py`). 로그·debug·예외 detail에 키 값 없음(테스트 `test_api_key_never_in_output`). 저장소 전체(.venv/data 제외)와 `_workspace/*.json`에서 키 형태 문자열 0건. `TYPESAFE_LOG_LEVEL`은 코드에서 설정하지 않는다 | – | – |
| 7 | 전체 pytest | PASS | `.venv/bin/python -m pytest` → **376 passed, 3 skipped(live), 4 xfailed**. xfail은 advisor-2(이번 추가) 1건, stats-1 1건, vision QA04-V2 2건(다른 QA 소관). advisor 테스트는 68(live 3 포함) → **185**(신규 `test_advisor_qa04.py` 117, 그중 xfail 1) | – | – |
| 8 | stats 경계(advisor 조회 ↔ 저장소 API) | PASS | `AdvisorStats` Protocol을 `open_repository()` 결과가 만족한다. `augment_tier` 정확 범위 + advisor 자체 폴백(`scoring.py:433-441`, `candidates.py:54-60`), `unit_item_stat(unit,item,comp_id)`는 덱 한정 'holds' 행(설계 §6-3 의미 일치). `shop_odds(level)`의 ValueError는 level∈[1,10] 계약 제약으로 도달하지 않는다 | – | – |

## 자체 보고 단순화 판정
1. **상징 BIS "+1 구간 도달" 무시 → 실제 결함(1f, FAIL).** 실제 key_traits가 곁가지 특성까지 넓게 담고 있어서, 상징이 절반 가까운 덱에서 캐리 BIS와 같은 1.0을 받는다. mini 통계에서는 드러나지 않고 실제 통계에서 s11 hold가 뒤집힌다.
2. **s09 전환에 완성템 2개 → 결함 아님.** 1개로도 전환된다(1j). fixture를 설계 문구에 맞추는 것은 선택 사항이다.
3. **히스테리시스가 표시 덱끼리 효과 없음 → 구현은 명세와 같지만 설계 한계(1g, WARN).** "1위 널뛰기 방지"라는 목적을 달성하지 못하므로 설계 수정을 요청한다.

## 담당별 수정 요청
### jev-strategist
1. **[FAIL 1f]** `features.py:230-232` 상징 적합도: 덱 최종 보드 특성 count+1이 다음 breakpoint에 닿을 때(보드를 알면 현재 활성 count 기준)만 `emblem_key_trait`, 아니면 `emblem_other`. 또는 count≥2 특성만 key로 본다. 확인 명령: `.venv/bin/python _workspace/qa_scripts/advisor_qa.py`의 "4b" 절에서 s11 `hold` 성립 여부.
2. **[WARN 1g]** 히스테리시스를 직전 1위 중심(또는 순위 가중)으로 바꾸고 설계 §5.2와 §8.4를 갱신한다. `test_hysteresis_protects_previous_top1`의 xfail 마커를 지운다.
3. [WARN 3w-a,b] 직전 추천이 없는 carousel에서 Jev를 호출하지 않는다. carousel 결과를 `session.last`에 반영한다.
4. [WARN 1i] `settings.advisor.timeout_s`를 전체 예산 가드로 쓰거나, 쓰지 않는 키라고 문서에 명시한다.
5. (선택) `counterfactual_top`에 `force_low` 적용. s09 branch를 완성템 1개로 줄인다.

### app-integrator
1. `target_comps`는 advisor 순서를 그대로 표시한다(타이브레이커 적용 후 점수가 단조가 아닐 수 있다).
2. 앱 기본 Jev 모드와 `--no-jev`→`"off"` 매핑을 명시한다(`auto`는 키가 있으면 live 과금). 현재 `src/`에서 advisor를 부르는 앱 코드는 아직 없다(통합 전).

### stats-researcher
- 없음(advisor 경계 PASS). s11 차이는 데이터 결함이 아니라 advisor 규칙 문제다.

## vision 관련(범위 밖, 별도 기록)
- vision 진행 중 실패는 이번 실행에서 없었다. `tests/test_vision_qa04.py` xfail 2건(QA04-V2 margin)은 vision QA 소관이다.

## 산출물
- 테스트: `tests/advisor/test_advisor_qa04.py`(실제 저장소 3, 결측×폴백 110, 모드 2, s09 1, 히스테리시스 xfail 1)
- 스크립트:
  - `_workspace/qa_scripts/advisor_qa.py`: 실제 저장소, 실제 저장소에 적용한 expect, 매트릭스, 타임아웃/서킷, 모드, 히스테리시스, 결정성 `--digest`
  - `_workspace/qa_scripts/advisor_config_usage.py`: §10a 키 사용 확인
  - `_workspace/qa_scripts/no_network_plugin.py`: pytest 네트워크 가드. `PYTHONPATH=_workspace/qa_scripts pytest -p no_network_plugin`
