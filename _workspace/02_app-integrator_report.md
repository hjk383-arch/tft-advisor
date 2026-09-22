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
