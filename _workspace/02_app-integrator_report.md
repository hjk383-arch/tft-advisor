# 02 app-integrator: 골격 + contracts 확정 (Phase 2)

작성일: 2026-09-21 / 작성자: app-integrator / CONTRACT_VERSION = 0.1.0

## 1. 만든 파일
| 파일 | 내용 |
|---|---|
| `pyproject.toml` | 패키지 `tft_advisor`(src 레이아웃, setuptools), Python >=3.14. core=`pydantic>=2.12,<3`. extras: `vision`(mss>=10.2, numpy>=2.3, opencv-python>=4.10,<5, rapidfuzz>=3.14, rapidocr>=3.4, onnxruntime>=1.23), `advisor`(typesafe_sdk), `stats`(httpx>=0.28), `ui-qt`(PySide6>=6.10, 보류 — 기본은 stdlib tkinter), `dev`(pytest>=8.4). 스크립트 `tft-advisor` |
| `src/tft_advisor/contracts.py` | 계약 모델 전체(아래 2절) |
| `src/tft_advisor/static_data.py` | `data/static/{set}/` 로더. `load_static()` 캐시, `get/kind_of/name_ko/find_by_name/champion_by_name/augment_by_name/item_by_name/shop_special_by_name/trait_by_name/names/observed_shop_odds` |
| `src/tft_advisor/fixtures.py` | `load_expected(path) -> ExpectedScreen(state, fields)` — 정답 파일(한국어 이름) → GameState(ID). QA·vision 정확도 측정용 |
| `src/tft_advisor/config.py` | `load_settings()`/`load_weights()` — toml 키 정의(pydantic, extra=forbid). `ShopWeights.for_stage()`, `ShrinkageWeights.adjust()` |
| `config/settings.toml`, `config/weights.toml` | 키 골격 + 기본값 |
| `src/tft_advisor/__init__.py`, `__main__.py` | argparse `--screenshot PATH` / `--live`(필수 택1), `--config DIR`, `--no-jev`, `--version`. 설정·정적 데이터 로드 후 미구현 단계에서 NotImplementedError → stderr "미구현: …", exit 3 |
| `src/tft_advisor/{stats,vision,advisor,app}/__init__.py` | 소유자·계약만 적은 docstring(가짜 구현 없음). `stats/static_extract.py`는 손대지 않음(ROOT 경로 그대로 유효) |
| `tests/test_contracts.py` | 14개 테스트(4절) |
| `.venv/` | `py -3.14 -m venv .venv` + `pip install -e ".[dev]"` (pydantic 2.13.5, pytest 9.1.1) |

## 2. contracts 설계 결정과 이유
- **ID**: `CanonicalId = str`(패턴 `^[A-Za-z0-9_]+$`), 별칭 `ChampionId/ItemId/AugmentId/TraitId`. 한국어 이름은 선택 필드 `name_ko`(표시용). 이름→ID는 static_data만 한다(규칙 한 곳). 특성 ID는 단계 접미사 없는 apiName.
- **불확실성 표현: `Observed[T]` 대신 "평평한 값 + 필드별 신뢰도 맵"**. `GameState`의 관측 필드는 모두 Optional(모르면 None), `confidence: dict[필드명, 0~1]`, `field_source: dict[필드명, vision|tracked|manual|fixture]`. 슬롯·유닛·아이템·증강 단위는 각 모델의 `confidence`. 이유: 정답 파일이 평평한 값이라 그대로 비교 가능, advisor가 state 만들 때 `.value` 언랩이 필요 없음. `confidence_of(field)`는 값 None→0.0, 값 있고 미기재→1.0. `is_reliable(field, threshold)`. 키 오타는 `GAME_STATE_FIELDS`로 검증.
- **ScreenMode**: loading / planning / combat / augment_select / carousel / item_select / game_over / unknown (vision 4절).
- **ShopSlot**: `kind`(champion | special | empty | unknown) + `id` + `cost`. champion/special이면 id 필수, empty/unknown이면 id 금지(validator). `GameState.shop`은 정확히 5칸 또는 None. 빈 칸을 None이 아니라 `kind=empty`로 둔 이유: "빈 칸"과 "인식 실패"를 구분.
- **GameState**: screen_mode, stage("N-M" 문자열, `stage_tuple()` 헬퍼), level, `xp=(현재, 필요량)`, gold, `streak`(부호: +연승/−연패), hp, `shop_odds`(% 정수 5개, 화면 표기 그대로), shop, board/bench(`UnitOnBoard`: id, star, items, `hex=(행,열)` 또는 `bench_slot`), `items: ItemState`(components/completed/emblems/others, `ItemRef.category`는 items.json 값), augments_owned/augment_offer(`AugmentRef`: id, name_ko, `rarity` 1~3, picked_stage), active_traits, set_number, captured_at, source_image, frame_size.
- **AugmentRef.rarity**: augments.json의 `tier`(1 실버/2 골드/3 프리즘)를 `rarity`로 이름 바꿈 — 통계 쪽 `AugmentTier.tier`(S~D 편집자 등급)와 충돌 방지. 설명 텍스트는 계약에 싣지 않고 advisor가 static_data `desc_ko`로 조회.
- **통계**: 공통 `Provenance`(source: StatSource, patch, rank_filter, fetched_at) + `PlacementStats`(avg_place, top4, win_rate, games — 모두 Optional, 비율은 0~1). `CompStats`(comp_id 안정 키, name, source_cluster_id, final_board: `CompUnit`, carry, carry_bis_items, key_traits: `TraitReq{id,count}`, **buildup: dict[레벨 4~10, list[BuildupBoard]]**, `level_timing: {레벨: "3-2"}`, `item_conditional: {아이템: PlacementStats}`, levelling). `AugmentTier`(augment_id, tier S~D, source_kind editorial|stats, comp_id None=전체 목록, source_title, games Optional). `UnitStats`(unit_id, star?, level?), `UnitItemStats`(unit_id, item_ids 1~3, place_change).
- **출력**: `Recommendation`(target_comps ≤3, shop ≤5, augment?, item?, jev_used, fallback_reason, latency_ms, state_hash, created_at, debug dict). `TargetComp`(comp_id, name, score 0~1, carry, reasons, owned_units, missing_units, `items_ready: list[ItemReadiness{item_id, status owned|craftable|missing, holder_unit_id}]`, next_buildup_board: BuildupBoard). `ShopAdvice`(slot, kind, offer_id, buy, score, reason_tag, reason). `AugmentAdvice`(choices: AugmentChoice[score, editorial_tier, reasons], pick). `ItemAdvice`(suggestions: ItemSuggestion[item_id, components, holder_unit_id, score, reason], hold).
- **ReasonTag 값은 ASCII**(`now_power/final_comp/buildup/two_star`), 한국어 표시는 `.label`("지금 전력/최종 덱/빌드업/2성 가능"). 이유: 로그·JSON 키 안정성.
- target_comps는 최소 개수를 강제하지 않음(인식 실패·폴백 시 0개 허용). 평상시 1~3개는 advisor 책임.
- 모든 계약 모델 `extra="forbid"`, `validate_assignment=True` — 모듈 경계 필드명 오타를 즉시 오류로.

## 3. stats 보고서 제안 반영
| 제안 | 반영 | 비고 |
|---|---|---|
| `DA_*` canonical ID | 반영 | 전 모델 ID 필드 |
| 레벨 기반 buildup 키 | 반영(형태 변경) | `"lv4"` 문자열 대신 **int 키 4~10**(JSON에선 "4"로 직렬화, 역직렬화 시 int 복원, 범위 validator). early/final 구분은 레벨 값으로 충분. 스테이지 변환용 `level_timing` 추가 |
| 증강 행 `source_kind`(editorial/stat), `games` nullable | 반영 | 값 이름은 `"editorial" | "stats"` |
| 증강 행 `tier`(1~3) | 부분 반영 | 1~3 등급은 정적 속성이라 `AugmentRef.rarity`로. `AugmentTier.tier`는 S~D 편집자 등급 |
| 상점 특수 상품 타입 | 반영 | `ShopSlotKind.SPECIAL`, id = shop_specials apiName. 이름 중복 시 기본 ID 우선(`DA_ThreeMe18` > `_Upgrade`) |
| 출처별 저장, `rank_filter` | 반영 | `Provenance` |
| 덱 정체성 = 핵심 유닛 집합, cluster_id 별도 | 반영 | `comp_id`(키 생성 규칙은 stats가 정함) + `source_cluster_id` |
| 소환물(Lifeblossom 등) 제외 | 문서화 | BuildupBoard docstring: 소환물 제외하고 저장 |
| 상점 확률표(5, 7~10레벨 공백) | 미반영 | 설정에 넣지 않음. `StaticData.observed_shop_odds()`로 meta.json 관측값만 노출 |
| 편집자 등급 수치 변환(S=1.0…D=0) | 반영 | `weights.toml [augment.editorial_tier_score]` |

## 4. 다른 에이전트가 알아야 할 사항

**jev-strategist**
- 입력: `GameState` + `list[CompStats]` + `list[AugmentTier]` + `UnitStats`/`UnitItemStats`. 출력: `Recommendation`(2절). 원시 Jev 답·합성 중간값은 `Recommendation.debug`에.
- Jev state 구성 시 `state.is_reliable(field, settings.vision.state_min_confidence)`로 필터. 이름·증강 설명은 `load_static().name_ko(id)` / `get("augments", id)["desc_ko"]`.
- 가중치는 `load_weights()`: `comp.{wi,wa,wb,wt,show_ratio,max_shown,hysteresis_bonus}`, `shop.for_stage(n) -> {ws,wp}`, `shop.buy_threshold`, `shrinkage.adjust(x, games)`, `jev.{min_confidence,low_confidence_scale}`, `augment.{w_jev,w_editorial,editorial_tier_score}`. 키 추가가 필요하면 제안해 달라(config.py는 extra=forbid).
- `shop_odds`는 % 정수 리스트 → Jev state의 비율 dict 변환은 advisor에서.
- `buildup` 키는 레벨 int. 스테이지 기준 "다음 빌드업 보드"는 `level_timing`으로 변환.
- 폴백 시 `jev_used=False`, `fallback_reason` 채움.

**vision-engineer**
- 출력은 `GameState`. 모르면 None, 필드 신뢰도는 `confidence`, 칸 단위는 `ShopSlot.confidence`. 빈 칸 `kind=empty`, 식별 실패 `kind=unknown`(id 없음).
- 이름 매칭 후보: `load_static().names("champions"|"augments"|"items"|"shop_specials", "ko"|"en")`(챔피언은 shop_pool만). 매칭 후 ID는 `find_by_name`.
- `streak`는 부호 있는 정수. **현 정답 파일의 streak 1/2가 연승인지 연패인지 확인 필요**(현재 +로 해석).
- `라운드 2-4 아이템선택`은 `carousel`로 기재되어 있음 — 파일명과 달리 캐러셀이 맞는지, 아니면 `item_select`인지 확인 필요.
- 정답 파일 형식은 `fixtures.py` docstring 참고(`{"unknown": true}`, `{"id": ...}` 허용). 새 필드를 정답에 쓰면 GameState 필드명과 같아야 함. 정확도 측정은 `load_expected(p).fields`에 있는 필드만 비교.
- 보유 증강 수동 입력·보드 수동 입력은 `field_source="manual"`로 표기(앱 쪽 UI 담당).

## 5. pytest 결과 (실제 출력)
```
platform win32 -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\hjk38\Desktop\LOL Chess
configfile: pyproject.toml
testpaths: tests
collecting ... collected 14 items

tests/test_contracts.py::test_fixture_count PASSED                       [  7%]
tests/test_contracts.py::test_expected_json_is_game_state_subset[라운드 1-4.expected.json] PASSED [ 14%]
tests/test_contracts.py::test_expected_json_is_game_state_subset[라운드 2-1 증강선택.expected.json] PASSED [ 21%]
tests/test_contracts.py::test_expected_json_is_game_state_subset[라운드 2-1.expected.json] PASSED [ 28%]
tests/test_contracts.py::test_expected_json_is_game_state_subset[라운드 2-4 아이템선택.expected.json] PASSED [ 35%]
tests/test_contracts.py::test_expected_json_is_game_state_subset[라운드 2-5.expected.json] PASSED [ 42%]
tests/test_contracts.py::test_expected_json_is_game_state_subset[라운드 3-3.expected.json] PASSED [ 50%]
tests/test_contracts.py::test_expected_json_is_game_state_subset[라운드 3-5.expected.json] PASSED [ 57%]
tests/test_contracts.py::test_fixture_name_to_id_mapping PASSED          [ 64%]
tests/test_contracts.py::test_game_state_roundtrip_and_confidence PASSED [ 71%]
tests/test_contracts.py::test_stats_roundtrip PASSED                     [ 78%]
tests/test_contracts.py::test_recommendation_roundtrip PASSED            [ 85%]
tests/test_contracts.py::test_contract_rejects_unknown_fields_and_bad_values PASSED [ 92%]
tests/test_contracts.py::test_config_loads_and_static_data PASSED        [100%]

============================= 14 passed in 0.11s ==============================
```
(파라미터 ID는 콘솔에서 `\ub77c…`로 이스케이프되어 출력됨 — 가독성을 위해 한글로 옮김)

CLI 스모크: `python -m tft_advisor --screenshot "tests/fixtures/screens/라운드 3-5.png"` → 설정·정적 데이터 로드 로그 후 `미구현: --screenshot: vision 인식…` exit 3. `--live`도 동일하게 exit 3. `--version` → `tft_advisor 0.1.0`.

기타 확인: 정적 데이터 5종 간 apiName 교차 중복 0건(`kind_of` 가정 성립).

## 6. 미결 / 다음 단계
- UI 백엔드(tkinter vs PySide6) 결정 보류. click-through 구현 난이도 때문에 Phase 3 착수 시 결정 필요.
- vision/advisor 무거운 의존성은 설치하지 않음(지시대로). 해당 에이전트가 `pip install -e ".[vision]"`/`".[advisor]"`.
- 세션 영속(보유 증강, 구매 추적) 저장 위치·형식은 app 루프 구현 시 정한다(`_state/session.json` 제안을 따를 예정).

---

# 2026-09-22 revision: §10a 설정 키 + CONTRACT_VERSION 0.2.0

기준: `02_jev-strategist_design.md`(2026-09-22 개정) §10a, §10, §8.1, §5.4 / `02_qa-validator_report.md` §4 app-integrator 1~6.

## R1. 변경 파일
| 파일 | 변경 |
|---|---|
| `src/tft_advisor/config.py` | §10a 키 전부 반영(아래 R2). 공통 헬퍼 `_Cfg._require_sum_one` / `_require_non_increasing`(허용 오차 1e-6), `Unit = float [0,1]`, `_stage_lookup()`(for_stage·commit_for_stage 공유). `StatsCfg.rank_filter`는 로드 시 정규화 |
| `config/weights.toml`, `config/settings.toml` | §10a 기본값 기록, 새 섹션 `[prefilter]`, `[item_fit]`, `[item]`, `[augment.commit_by_stage]`, advisor 신규 6키 |
| `src/tft_advisor/contracts.py` | 0.2.0(아래 R3) |
| `_workspace/qa_scripts/config_proposal_check.py` | `PROPOSED_*`를 §10a 표 전체(기존+신규 73키)로 교체. 거부 키 수 + config 파일 값과 §10a 기본값의 차이 수를 출력 |
| `tests/test_boundaries.py` | `test_config_accepts_design_keys`, `test_unit_item_stats_has_comp_scope`의 strict xfail을 제거했다. 앞 테스트는 §10a 스크립트 결과(거부 0, 기본값 차이 0)와 새 섹션·advisor 키를 확인하고, 뒤 테스트는 comp_id 기본값 None과 JSON 왕복을 확인한다. Eclipse xfail은 stats-researcher 몫이라 건드리지 않았다 |
| `tests/test_contracts.py` | `"jev timeout"`을 `FallbackReason.TIMEOUT`으로 고쳤다. 왕복 테스트에 신규 필드를 추가했다. 새 테스트 7개(파라미터 25건 포함): FallbackReason 닫힌 집합과 일관성, 0.2.0 필드 제약, rank_filter, `adjust(prior=)`, stage 조회, validator 거부 케이스, 경계 허용 케이스 |

## R2. 설정 키 (§10a, 신규 53 = weights 47 + settings 6)
- `[comp]` +5: stat_avg_best / stat_avg_worst([1,8]), show_ratio_undecided, undecided_min_p, tie_eps([0,0.2]). 기존 wi/wa/wb/hysteresis_bonus에 [0,1] 범위를 추가했다
- `[prefilter]` 15 (`PrefilterWeights`), `[item_fit]` 7 (`ItemFitWeights`), `[item]` 6 (`ItemWeights`): 새 섹션
- `[shop]` +10: jev_share_now/path, two/three_star_bonus, hp_danger_shift([0,0.5]), mu_core/final/next_buildup/cur_buildup, special_fallback_score
- `[augment]` +4: w_comp, unlisted_score, tie_eps, `commit_by_stage: dict[int≥1, [0,1]]`(비어 있으면 거부, TOML 키 "2"는 int로 변환). editorial_tier_score 값은 [0,1]이고 누락 등급은 기본값으로 채운다
- `[advisor]` +6: `jev_model`(패턴 `^jev-(latest|\d+\.\d+\.\d+)$`, 기본 `"jev-latest"`), jev_timeout_s, jev_retry_budget_s, jev_max_retries([0,3]), circuit_fail_threshold(≥1), circuit_cooldown_s(≥0). max_candidate_comps 상한은 20
- **model_validator**
  - 합=1: comp(wi+wa+wb), prefilter(w_item+w_aug+w_unit+w_stat), item(w_bis+w_jev+w_stat), augment(w_jev+w_editorial), shop.stage_weights(ws+wp)
  - 비증가 사다리: prefilter unit_w_core ≥ unit_w_final ≥ unit_w_buildup, item_fit carry_bis ≥ core_unit ≥ usage, item_fit emblem_key_trait ≥ emblem_other, shop mu_core ≥ mu_final ≥ mu_next_buildup ≥ mu_cur_buildup
  - 순서: stat_avg_best < stat_avg_worst, show_ratio_undecided ≤ show_ratio, jev_timeout_s ≤ jev_retry_budget_s < timeout_s
- `AugmentWeights.commit_for_stage(s)`는 `ShopWeights.for_stage(s)`와 같은 `_stage_lookup`을 쓴다. s 이하 키 중 최댓값의 값을 쓰고, 그런 키가 없거나 s=None이면 최소 키의 값을 쓴다(1→0.3, 5+→0.9)
- `ShrinkageWeights.adjust(x, games, prior=None)`: (g·x + k·p)/(g + k)이고 p는 prior 또는 prior_avg_place다. g+k=0이면 x를 돌려준다. prior 범위는 검증하지 않는다(`prior=0.0` 용도). prior=None이면 기존 동작과 같다

## R3. 계약 0.2.0 (§10 결정 1~7)
| # | 반영 |
|---|---|
| 1 GameState.item_offer | 설계 결정대로 **미반영(보류)** |
| 2 | `Recommendation.component_priority: list[ItemId]`(max_length=10) |
| 3 | `TargetComp.levelling: str \| None` |
| 4 | `CompStats.item_usage: dict[ItemId, float ≥ 0]`(상한 없음, 1.38 허용) |
| 5 | `CompUnit.role: Literal["carry","tank","support"] \| None` |
| 6 | `UnitItemStats.comp_id: str \| None`(None = 전체 통계) |
| 7 | `FallbackReason(StrEnum)` 9종, `Recommendation.fallback_reason: FallbackReason \| None`, validator로 `jev_used == (fallback_reason is None)` 확인. **주의: 이제 jev_used=False이면 사유가 필수다** |
- rank_filter: `contracts.rank_filter_set()`(frozenset, 대문자, 공백 제거), `normalize_rank_filter()`(정렬 후 콤마 결합), `same_rank_filter()`를 추가했다. `Provenance.rank_filter`와 `settings.stats.rank_filter`는 검증 시 정규형으로 저장된다.

## R4. 다른 에이전트 영향
- **stats-researcher**: `stats/metatft_convert.py`에 `normalize_rank_filter`/`same_rank_filter`를 따로 두고 있다. 의미는 같지만 규칙을 한 곳에 두려면 `tft_advisor.contracts`의 함수를 import하도록 권한다(계약이 어차피 저장 시 정규화한다). `UnitItemStats.comp_id`, `CompStats.item_usage`, `CompUnit.role`을 채울 수 있다.
- **jev-strategist(advisor 구현)**: 폴백 시 반드시 `FallbackReason`을 넣어야 한다(없으면 ValidationError). 조회에는 `weights.augment.commit_for_stage(n)`, `shrinkage.adjust(..., prior=)`, `settings.advisor.jev_model`을 쓴다.
- **app(오버레이)**: `TargetComp.levelling`, `Recommendation.component_priority`를 표시할 수 있다. 오버레이 배치는 미결정이다.

## R5. 검증 (실제 출력)
```
$ .venv/bin/python _workspace/qa_scripts/config_proposal_check.py
§10a keys checked: 73
rejected keys: 0
config files differ from §10a defaults: 0

$ .venv/bin/python -m pytest -q -rxs
56 passed, 7 skipped in 0.58s
```
skip 7건은 모두 `MetaTFT 원본 캐시 없음`이다(이 macOS 체크아웃에는 `data/raw/metatft/2026-09-21`이 없다). 원본 캐시 기반 변환 테스트는 이 환경에서 다시 확인하지 못했다.

---

# 2026-09-22 Phase 3 config round (app-integrator)

기준: `04_stats-researcher_impl.md` §9·Fix round, `04_jev-strategist_impl.md` §8·Fix round §5~6, `04_vision-engineer_impl.md` §7·F10, QA `04_qa_stats.md`/`04_qa_advisor.md`/`04_qa_vision.md`의 app-integrator 항목. CONTRACT_VERSION은 그대로 0.2.0(contracts.py 변경 없음).

## C1. 새 설정 키
| 키 | 기본 | 범위·제약 | 읽는 곳 |
|---|---|---|---|
| `[stats] keep_snapshots` | 5 | int ≥ 1 | `stats/refresh.keep_snapshots()` — getattr 폴백을 지우고 `settings.stats.keep_snapshots`를 직접 읽는다 |
| `[comp] hysteresis_other_share` | 0.25 | [0,1] | `advisor/scoring.Scorer.hysteresis_weight` (상수 `HYSTERESIS_OTHER_SHARE` 삭제) |
| `[item] overall_stat_games_factor` | 0.25 | [0,1] | `advisor/scoring.Scorer.item_stat` (상수 `OVERALL_ITEM_STAT_GAMES_FACTOR` 삭제) |
| `[advisor] jev_backend` | `"mock"` | `"mock"｜"live"｜"off"` (`"auto"`는 거부) | `advisor/engine.create_advisor` |
| `[vision] content_box` | 없음(None) = 프레임 전체 | `[x1,y1,x2,y2]` 비율, 각 [0,1], x1<x2, y1<y2. `[]`도 None | `Recognizer.recognize(content=None)` → `cfg.content_px(w,h)`. `ChangeDetector.update(content=cfg.content_px(w,h))`(app) |
| `[vision] ocr_backend` | `"auto"` | `auto｜onnxruntime｜openvino｜none` | `ocr.create_ocr(backend=)` ← `Recognizer.__init__` |
| `[vision] name_fuzzy_min_margin` | 10 | [0,100], ≤ relaxed | `NameMatcher(min_margin=)` (상점·증강·특성 3개 모두) |
| `[vision] name_fuzzy_relaxed_margin` | 15 | [0,100] | `NameMatcher(relaxed_margin=)` |
| `[vision] item_match_margin` | 0.05 | [0,1] | `Recognizer._read_items` (상수 `ITEM_MIN_MARGIN` 삭제) |
| `[vision] change_threshold` | 24 | [0,255] | `ChangeDetector.from_cfg(profile, cfg)` (신규 classmethod) |
| `[vision] change_stable_frames` | 2 | int [1,30] | `ChangeDetector.from_cfg` |
| `[vision] capture_fps` | 4 | (0,30] | app 루프(Phase 4) |
| `[vision] traits_every_s` | 3 | > 0 | app 루프(Phase 4): 이 주기마다 `groups`에 "traits" 추가 |

- **`[capture] poll_interval_ms`, `stable_frames` 삭제.** 아무 코드도 읽지 않았고, `capture_fps`·`change_stable_frames`와 같은 뜻이라 두 곳에 두면 어긋난다(기본값도 500ms/3 vs 250ms/2로 이미 달랐다). `[capture]`에는 `monitor`만 남는다(≥0). 모르는 키는 거부되므로 옛 키를 쓴 설정 파일은 로드 오류로 바로 드러난다.
- vision 모듈 기본값(`matching.MIN_MARGIN/RELAXED_MARGIN`, `ChangeDetector` 기본 인자)은 설정 없이 쓰는 경로용으로 그대로 두고, 설정 기본값과 같음을 테스트로 고정했다.
- 보정 계수(streak/hp/traits factor)와 `ocr_lang`은 QA R2 판정대로 설정에 넣지 않았다.

### content_box 결정과 이유
- vision은 "필수"라고 했지만 **원본 캡처가 아직 없어서 올바른 값을 정할 근거가 없다.** 그래서 필수 값 대신 **선택 값 + 명시적 기본 의미**로 했다: 키 생략(또는 `[]`, `[0,0,1,1]`) = 프레임 전체. 전체 화면 1920x1080(사용자 기본 환경)은 설정 없이 맞고, 창모드에서 빠뜨리면 QA #8대로 틀린 값이 아니라 None/UNKNOWN으로 안전하게 실패한다.
- **픽셀이 아니라 비율 (x1,y1,x2,y2)** 로 받는다. 창·모니터 해상도가 바뀌어도 값이 유효하고, 범위 검증이 단순하다(0≤x1<x2≤1). vision의 기존 인자 형식(left, top, width, height px)은 `VisionCfg.content_px(frame_w, frame_h)`가 변환한다(전체 프레임이면 None → vision 기존 경로와 완전히 같음).
- 우선순위: `recognize(content=…)` 명시 인자 > 설정. evaluate/테스트는 설정 기본값(None)이라 기준선이 그대로다(screen_mode 7/7 … shop 칸 24/25 재확인).
- 자동 탐지는 없다. 원본 캡처가 오면 창모드 캡처로 값 예시를 settings.toml 주석에 갱신한다.

### jev_backend 결정
- 기본 `"mock"`: 네트워크·과금 없음. `create_advisor()`/`create_advisor("auto")`는 이제 **설정값을 따른다**. `TYPESAFE_API_KEY`가 있다는 이유만으로 live가 되지 않는다(QA advisor 5w).
- 명시 `create_advisor("mock"|"live"|"off")`는 설정보다 우선(테스트·리플레이·CLI 오버라이드 경로 유지). `"live"`인데 키가 없으면 경고 로그 후 게이트웨이가 `auth` 폴백(네트워크 호출 없음).
- `"off"`와 기존 `jev_enabled=false`는 결과가 같다(`fallback_reason=jev_disabled`). `jev_enabled`는 게이트웨이 스위치로 남긴다.
- **CLI (Phase 4)**: `--no-jev` → `create_advisor("off")`(설정보다 우선). `__main__.py` help 문구만 갱신했다. live를 CLI로 켜는 플래그(`--jev live`)는 만들지 않고 설정 파일로만 켜는 것을 제안한다(실수 과금 방지).

## C2. fixtures.py (vision R1a/R1b)
- `items`: `{"components"|"completed"|"emblems"|"others": [이름|apiName]}` 또는 평면 리스트. `vision.item_ids.ItemCatalog.resolve`로 **묶음 대표 ID**로 바꾼다 → vision 출력과 ID가 바로 같다(자석 제거기 = `DA_Consumable_ItemRemover`). 버킷은 정적 데이터 `category`로 정하고, 라벨 버킷이 다르면 ValueError. 해석 못 하는 이름은 KeyError.
- `item_bench`: GameState 밖 → `ExpectedScreen.extras["item_bench"]`(10칸 이하, 대표 ID|None). `items`가 없으면 item_bench로 `items`를 채워 정확도 비교 대상이 된다. 둘 다 있으면 다중집합이 같아야 한다.
- `ItemCatalog`은 numpy·rapidfuzz(vision extra)를 import하므로 items/item_bench가 있을 때만 지연 import한다(현 fixture 7장은 영향 없음).
- `vision/evaluate._norm`에 `items` → 대표 ID 정렬 리스트 비교를 넣었다(ItemState 전체 비교는 신뢰도 차이로 항상 wrong이 된다).
- 캡처 요청서 부록 B의 "fixtures 미반영" 메모를 반영 완료로 고쳤다. `templates.harvest-items`는 원본 JSON의 `item_bench`를 그대로 읽는다(같은 해석기라 결과 동일).

## C3. Phase 4 계약 메모 (기록만, 구현 안 함)
1. **augments_owned 추적은 app 루프 세션 상태**(vision은 무상태). 증강 화면의 `augment_offer`만으로는 사용자가 무엇을 골랐는지 알 수 없다 → **추천 1위를 골랐다고 가정하지 않는다.** 확정 신호는 HUD 보유 증강 판독(vision, 캡처 #6 필요) 또는 선택 카드 강조 감지. 새로고침이 있으면 마지막으로 본 offer 기준. 확정되면 `field_source["augments_owned"]="tracked"`, 판독 전에는 None(advisor §4.3이 빈 목록으로 처리). 수동 입력 UI를 두면 `"manual"`. 세션은 loading/game_over에서 초기화(`_state/session.json` 제안 유지).
2. **UI는 `Recommendation.target_comps`를 advisor 순서 그대로 표시한다.** 타이브레이커·히스테리시스 보호 때문에 `score`가 단조가 아닐 수 있다. 점수로 재정렬 금지. 표시 개수는 `min(comp.max_shown, ui.max_target_comps)`가 이미 적용된 목록 길이를 따른다.
3. **advisor 화면 모드 계약**(`Advisor.advise/recommend` 반환):
   - `loading`/`game_over`: advisor가 세션·캐시를 초기화하고 `None` → app도 세션(보유 증강 추적, 직전 GameState 병합본)을 초기화하고 오버레이를 비운다.
   - `combat`/`item_select`/`unknown`: **직전 Recommendation 객체를 그대로** 반환(없으면 None) → app은 동일 객체면 다시 그리지 않는다.
   - `carousel`: Jev 호출 없음. 직전 추천이 있으면 `component_priority`만 다시 계산한 사본, 없으면 통계 전용(`jev_used=False`, `fallback_reason=jev_disabled`, `shop=[]`, augment 없음). UI는 이 경우를 "Jev 미사용"이 아니라 "캐러셀(통계)"로 표시(`debug.fallback_detail` 참고).
   - `planning`: 덱+상점+아이템. `augment_select`: 덱+증강+아이템(`shop=[]`). 증강·아이템 추천은 해당 화면에서만 표시.
4. **루프 뼈대**: `capture_fps`로 캡처 → `ChangeDetector.from_cfg(profile, settings.vision).update(img, content=cfg.content_px(w,h))` → 바뀐 묶음만 `recognize(groups=…)`(content는 설정에서 자동) → `FIELD_GROUP` 기준으로 직전 GameState에 병합 → "stage" 묶음이 바뀌면 전체 인식. `traits_every_s`마다 "traits" 추가. 추천은 백그라운드 스레드(advisor 전용 이벤트 루프), 새 state가 오면 이전 태스크 취소(`CancelledError` 전달됨). 앱 시작 시 Advisor 생성 + 워밍업 1회(jev 권고 §7-7, live일 때만 의미).
5. 기타 미결(이월): UI 백엔드 결정, L1 openvino-telemetry 안내, `--screenshot` 경로 연결.

## C4. 변경 파일
- 설정: `src/tft_advisor/config.py`, `config/settings.toml`, `config/weights.toml`
- 연결: `src/tft_advisor/stats/refresh.py`(+`db.py` 주석), `src/tft_advisor/advisor/{scoring,engine,__init__}.py`, `src/tft_advisor/vision/{recognizer,ocr,change,evaluate}.py`, `src/tft_advisor/__main__.py`(help 문구), `src/tft_advisor/fixtures.py`
- 테스트: 신규 `tests/test_config_round.py`(64건: 새 키 기본값·파일값 13, 범위 거부 22, content_box 거부 8·변환·경계, jev_backend 기본/키 무시/명시 우선 6, vision 연결 3, fixtures items/item_bench 9, evaluate 1). 수정 `tests/test_stats_refresh.py`(keep_snapshots를 Settings로), `tests/advisor/test_advisor_fixround.py`(상수 → weights, `test_hysteresis_other_share_from_weights` 추가)
- QA 스크립트: `_workspace/qa_scripts/config_proposal_check.py`에 Phase 3 키 12개(content_box 제외) 추가
- 문서: `_workspace/04_vision-engineer_capture_request.md` 부록 B 한 줄

## C5. 다른 에이전트 영향
- **vision-engineer**: `Recognizer`가 `cfg`에서 margin·item margin·ocr_backend·content_box를 읽는다. `ChangeDetector.from_cfg` 추가, `create_ocr(lang, backend="auto")`. 상수 `ITEM_MIN_MARGIN` 삭제. F10 표의 "연결하려면 인자 추가 필요"(ocr_backend)는 이번에 넣었다(명시 런타임을 못 쓰면 경고 후 NullOcr — 조용히 다른 런타임으로 바꾸지 않음).
- **jev-strategist**: `HYSTERESIS_OTHER_SHARE`/`OVERALL_ITEM_STAT_GAMES_FACTOR` 상수 삭제, weights에서 읽는다. `create_advisor("auto")` 의미 변경(키 → 설정).
- **stats-researcher**: `keep_snapshots()`가 설정을 직접 읽는다(SimpleNamespace 입력은 더 이상 지원 안 함).
- **qa-validator**: `config_proposal_check.py` 키 수 73 → 85.

## C6. 검증 (실제 출력)
```
$ .venv/bin/python _workspace/qa_scripts/config_proposal_check.py
§10a keys checked: 85 (Phase 3 config round 키 포함)
rejected keys: 0
config files differ from §10a defaults: 0

$ .venv/bin/python -m pytest -rxs
SKIPPED [3] tests/advisor/test_advisor_live.py: live Jev 호출: TFT_LIVE_JEV=1 로 실행
509 passed, 3 skipped in 95.71s
```
`python -m tft_advisor.vision.evaluate`: 기준선 불변(screen_mode 7/7, stage 7/7, level 3/3, xp 5/5, gold 5/5, streak 5/5, hp 7/7, odds 5/5, shop 4/5·칸 24/25, augment 1/1). live Jev 호출 없음.
