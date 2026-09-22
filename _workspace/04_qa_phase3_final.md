# 04 QA: Phase 3 최종 게이트 (커밋 전)

작성일: 2026-09-22 / 작성자: qa-validator / 브랜치 `phase3` (커밋하지 않음)
범위: Phase 3 QA 보고서 3건(`04_qa_stats.md`, `04_qa_advisor.md`, `04_qa_vision.md`)의 FAIL/WARN 해소 여부, fix round 이후의 경계면, 과금 안전, 커밋 위생, 전체 pytest(순서 의존성 포함).
live Jev 호출: 없음. 모든 실행에서 가짜 `TYPESAFE_API_KEY`를 설정하고 소켓·DNS 가드를 켰다. 외부 연결 시도는 0건이었다.
소스는 수정하지 않았다. 추가한 파일은 아래 2개다.
- `tests/test_phase3_final_qa.py`(10개): `db.DEFAULT_KEEP`와 설정 기본값의 일치, 삭제된 `[capture]` 키가 다시 생기지 않는지, `.gitignore` 제외 대상 8경로.
- `_workspace/qa_scripts/phase3_final_boundary.py`: E2E, 동시성, 과금, 설정 키 전수 검사.

## 판정: **Phase 3 커밋 가능 = 예 (조건 1개)**
- **조건 H1**: `_workspace/04_vision_rois_2-1_augment.png`(3.0MB)와 `_workspace/04_vision_rois_3-3.png`(3.4MB)는 스테이징하지 않는다. 또는 `.gitignore`에 `_workspace/*.png`를 추가한다.
  - 두 파일은 방송 화면 크롭에 ROI를 겹친 그림이다(Riot 아트워크와 다른 소환사 이름이 들어 있다). 스크립트로 다시 만들 수 있다.
- 차단 FAIL: 0건. pytest는 **519 passed, 3 skipped(live), 0 xfailed, 0 failed**다. 이 수치는 신규 10개를 포함한다. 신규를 빼면 509 passed이고, 순서를 바꾸거나 스위트를 나눠 돌려도 결과가 같다.

---

## 1. 해소 여부 표 (Phase 3 QA의 FAIL/WARN 전체)

상태 표기: RESOLVED(해소) / DEFERRED(이월, 사유 명시) / BLOCKED(사용자 필요)

### stats (`04_qa_stats.md`)
| 항목 | 상태 | 근거 |
|---|---|---|
| W1 읽기 경로가 DB에 씀 / 읽기 전용 DB 열기 실패 | **RESOLVED** | `db.connect_ro()`(`mode=ro`)를 쓴다. strict xfail을 지웠고 `test_open_repository_on_readonly_db`와 `test_reads_do_not_write_and_do_not_block_writer`가 PASS다. **이번 동시성 실측**(임시 DB 사본): writer 6회 적재(keep=2)와 reader 스레드 2개를 동시에 돌려 `open_repository` **44회**를 실행했다. 오류는 양쪽 모두 0이고, 보인 덱 수는 항상 57이며, 남은 스냅샷은 2다. 전체 검사 동안 실제 `stats.sqlite`의 mtime은 바뀌지 않았다 |
| W2 diff(덱별 증강 등급, 기준 없음 표시, 같은 내용 재적재) | **RESOLVED** | `augment_tiers.by_comp`, `has_baseline=False`/`patch_changed=None`, 스키마 v2의 `content_hash` 중복 제거(`skipped_identical`)를 넣었다. 테스트: `test_stats_refresh.py:88,230,237` |
| W3 어댑터와 repository의 의미 차이 | **RESOLVED** | `unit_item_stat(..., fallback_overall=)`를 두 구현에 모두 넣었다. `test_unit_item_overall_fallback_adapter_matches_repository`로 확인한다. `comps()` 순서 차이는 advisor가 자체 정렬하므로 무해하다 |
| W4 `recipe`/`is_craftable`이 레거시 `TFT_Item_*`까지 포함 | **RESOLVED** | 범위를 DA_·set_native로 좁혔다(`test_recipe_table_roundtrip` 확장) |
| 참고 4: `load`가 롤백 경로라는 점 | **RESOLVED** | CLI 도움말에 적었다 |
| jev 2: 덱 한정 unit×item 행 없음(12/984) | **RESOLVED** | 전체값으로 폴백한다(games×`[item] overall_stat_games_factor`). `test_item_stat_uses_overall_when_comp_row_missing` |
| app 1: `keep_snapshots` 설정 키 | **RESOLVED** | `[stats] keep_snapshots=5`를 `refresh.keep_snapshots()`가 직접 읽는다. `db.DEFAULT_KEEP`(write_snapshot 폴백)과 설정 기본값의 일치는 신규 테스트로 고정했다 |
| 5: `stats.primary`/`fallbacks`에 소비처 없음 | **DEFERRED** | 수집기가 metatft 하나뿐이다. 두 번째 출처가 생길 때 연결한다 |

### advisor (`04_qa_advisor.md`)
| 항목 | 상태 | 근거 |
|---|---|---|
| **1f FAIL** 상징 BIS 과대평가 | **RESOLVED** | `features.emblem_advances`를 추가했다. 실제 57덱에서 key(1.0)를 받는 (상징, 덱) 쌍이 274에서 68로 줄었다. s11 `hold`는 mini와 실제 저장소에서 같다(`test_s11_hold_same_on_mini_and_real`, `test_real_emblem_overscoring_gone`) |
| 1g 히스테리시스가 1위 널뛰기를 막지 못함 | **RESOLVED** | H: 직전 1위 1, 2·3위 `[comp] hysteresis_other_share`(0.25). strict xfail을 해제했고 `test_hysteresis_protects_previous_top1`이 PASS다 |
| 1i `timeout_s` 미사용 | **RESOLVED** | 전체 예산에서 경과 시간과 0.1s를 뺀 값을 `ask(budget_s=)`에 넘긴다. `test_recommend_passes_overall_budget`, `test_gateway_budget_limits_wait` |
| 3w-a 직전 추천이 없는 carousel이 Jev를 호출 | **RESOLVED** | Jev를 부르지 않고 통계 전용으로 처리한다. E2E의 2-4 carousel 화면이 `jev_used=False`다. `test_carousel_without_previous_does_not_call_jev` |
| 3w-b carousel 결과가 `session.last`에 저장되지 않음 | **RESOLVED** | `test_carousel_result_becomes_last` |
| 3w-c UI가 `target_comps`를 점수로 다시 정렬하면 안 됨 | **DEFERRED(Phase 4)** | UI가 아직 없다. 계약은 `02_app-integrator_report.md` C3.2에 기록돼 있다. UI를 구현할 때 테스트로 고정한다 |
| 5w `create_advisor("auto")`가 키만 있으면 곧바로 live 과금 | **RESOLVED** | `[advisor] jev_backend="mock"`이 기본값이고 `"auto"`는 이 설정을 따른다. 아래 3절에서 실측했다 |
| 1d 반사실 계산에 `force_low` 미적용 | **RESOLVED** | fix round에서 적용했다 |
| 1j s09 branch를 완성템 1개로 줄이기(선택) | **DEFERRED** | 선택 항목이다. 더 중요한 관련 사항: **실제 통계에서는 s09 branch가 뒤집히지 않는다**(elderwood-ezreal 0.799 vs invoker-ahri 0.791, `item_saturation` 포화). 이는 튜닝 과제이며 Phase 4로 넘긴다. 실제 저장소 테스트는 "상위 2 진입과 순위 상승"만 확인한다 |

### vision (`04_qa_vision.md`)
| 항목 | 상태 | 근거 |
|---|---|---|
| **#7 FAIL (V2)** 이름을 확신하고 잘못 매칭 | **RESOLVED** | 숫자·등급 토큰은 정확히 일치해야 하고, 모든 점수 구간에 margin을 요구한다(10/15). 적대 변형에서 오답 수락이 상점 109→5, 증강 1,579→0으로 줄었다. strict xfail 2개를 해제했고 둘 다 PASS다 |
| └ 잔여: 등급 토큰 OCR 변형의 8.6%가 다른 등급으로 수락됨 | **BLOCKED(사용자 캡처 #5)** | 텍스트만으로는 막을 수 없다. 카드 테두리 색과 `tier`를 교차 확인해야 하는데, 그러려면 원본 캡처가 필요하다 |
| **#15 FAIL (V1)** harvest가 한국어 파일명을 만들어 ValidationError | **RESOLVED** | `ItemCatalog.resolve`로 이름을 ID로 바꾸고, 템플릿은 ID별로 합치며, ID가 아닌 stem은 경고 후 무시한다. `test_harvest_items_maps_korean_labels_to_ids_and_supplements_cdragon`, `test_item_template_stems_are_static_item_ids` |
| #10 L1 라이선스(Riot 아이콘 커밋 위험) | **RESOLVED** (일부 이월) | `.gitignore`에 `data/templates/*/items/`와 `items_screen/`을 넣었다(212개 무시 확인). `data/templates/README.md`에 고지를 적었다. openvino-telemetry 안내는 **DEFERRED(Phase 4, app)** |
| #11 V3 shop 필드 신뢰도 이중 차감 | **RESOLVED** | 필드 신뢰도를 식별된 칸의 최댓값으로 바꿨다. E2E에서 3-5 상점이 이제 advisor에 들어간다(구매 추천 [1,2,3,4]) |
| #12 V4 streak 신뢰도가 임계에 걸림 | **RESOLVED** | 계수 0.5로 임계에서 떼어 제외했다. 부호 확정은 **BLOCKED(캡처 #8)** |
| #14 V7 성능 | **부분 해소 → DEFERRED(Phase 4)** | 한 줄 인식 우선, 배치 처리, 묶음 단위 부분 인식, `change.py`를 넣었다. 이번 실측(이 Mac, 기본 묶음): 프레임당 **0.47~0.95s**, advisor 5~10ms. 루프 전형 경로(stage+hud+shop)는 약 0.22s다(impl 보고). 인식 전체 300ms 목표는 여전히 미달이다. Windows onnxruntime 수치가 없고, 앱 루프에서 측정해야 한다 |
| #16 V6 자석 제거기 ID와 마크업 | **RESOLVED** | 대표 ID `DA_Consumable_ItemRemover`로 내고, 표시 이름의 마크업을 뗀다. state fixture의 Recommendation 전부에서 마크업 0건 |
| └ S1: 정적 데이터 `items.json`의 name_ko/en 마크업 | **DEFERRED(Phase 4, stats)** | stats fix round에 들어가지 않았고, **22건이 아직 남아 있다**(`TFT16_Consumable_GwensScissors_*` 등). 지금은 vision이 읽을 때 떼어 내므로 무해하다. UI가 static 이름을 직접 표시하기 전에 `static_extract`에서 제거해야 한다 |
| #17 V8 캡처 요청서 | **RESOLVED** | 8건 모두 반영했다(impl F9) |
| #9 arm64 mac 주석(선택) | **DEFERRED(사소)** | pyproject에 "arm64 mac은 macOS 14 이상" 주석이 없다. 동작에는 영향이 없다 |
| #8 / R2 content_box | **RESOLVED(설정)** / 자동 탐지 DEFERRED | `[vision] content_box`(비율, 생략 시 프레임 전체)를 추가했고 `Recognizer`가 `cfg.content_px`로 읽는다. 창모드 값의 근거가 되는 원본 캡처가 필요하다 |
| R1a/R1b fixtures items / item_bench | **RESOLVED** | `fixtures.py`가 ItemCatalog 대표 ID를 쓰고 `extras["item_bench"]`를 둔다(`test_config_round.py` 9건) |
| R3 augments_owned 추적 | **DEFERRED(Phase 4 app)** | 계약만 기록했다(C3.1: 1위 픽을 가정하지 않고, HUD 판독이 필요). 캡처 #6이 필요하다 |
| R4 XP 표 | **RESOLVED** / 7레벨 이상 **BLOCKED(캡처 #2)** | `meta.json` `xp_to_next`를 쓴다. 출처가 하나(tftflow)이고 관측으로 확인한 레벨은 3/4/6뿐이다 |
| R5 `preference_key` 공개 | **RESOLVED** | 사소한 정리 과제: deprecated 별칭 `_preference`의 사용처가 이제 0인데 `static_data.py:41` 주석은 "templates.py가 아직 import"라고 적혀 있다(주석이 낡았다). Phase 4에서 지운다 |
| V5 COMBAT 판별 | **BLOCKED(캡처 #7)** | 전투 프레임은 planning으로 판별된다. 변화 감지로 재계산을 억제한다 |
| #19 실제 인식 정확도 | **BLOCKED(사용자)** | 원본 1920x1080 캡처가 없다. fixture 7장은 회귀 기준선일 뿐이다 |

---

## 2. 경계면 결과 (fix round 이후)

스크립트: `.venv/bin/python _workspace/qa_scripts/phase3_final_boundary.py` → **PROBLEMS 0**(121s)

### 2a. vision → GameState → advisor(실제 `open_repository()` + `create_advisor("mock", stats=repo)`)
저장소: 패치 18.2b, 57덱, 로드 1.05s. 7장 모두 GameState 왕복 검증과 Recommendation 왕복 검증을 통과했다. **state와 추천에 나온 ID는 모두 static에 있고, comp_id는 모두 저장소에 있다**(불일치 0).

| 화면 | 모드 | vision s | advisor ms | 추천 ID 수 | top 덱 | jev_used | 상점 구매 | 증강 |
|---|---|---|---|---|---|---|---|---|
| 1-4 | planning | 0.95 | 10.0 | 43 | executioner-khazix… | T | – | – |
| 2-1 증강선택 | augment_select | 0.57 | 5.9 | 43 | executioner-khazix… | T | – | DA_SeraphimsStaff |
| 2-1 | planning | 0.65 | 6.6 | 46 | executioner-khazix… | T | [0,2,3,4] | – |
| 2-4 아이템선택 | carousel | 0.47 | 4.9 | 39 | executioner-khazix… | **F**(직전 추천 없음 → 통계 전용, 3w-a) | – | – |
| 2-5 | planning | 0.50 | 5.7 | 44 | executioner-khazix… | T | [0,1] | – |
| 3-3 | planning | 0.61 | 6.1 | 56 | executioner-khazix… | T | [1,3,4] | – |
| 3-5 | planning | 0.55 | 6.5 | 59 | executioner-khazix… | T | **[1,2,3,4]**(V3 해소. 이전에는 상점 누락) | – |

### 2b. state fixture 14개(steps와 s09 branch 포함) × {mock, off, `create_advisor()`}
실행 54회. 예외 0, None 0, 계약·ID·comp_id 불일치 0이다. `off` 모드의 `jev_used`는 모두 False다. fixture expect 규칙을 실제 저장소로 확인하는 일은 pytest `test_advisor_fixround.py`(`real_stats`)가 맡으며 PASS다.

### 2c. stats 읽기 전용 접근과 동시 refresh
§1 W1 행을 참고한다. 적재 1회에 5.9~27.5s가 걸렸는데, reader 2개가 GIL과 CPU를 두고 경합한 탓이다. 잠금 대기 오류는 0건이었다.

### 2d. 설정 ↔ 코드
- `config_proposal_check.py`: 검사한 키 85, 거부된 키 0, 설정 파일이 §10a 기본값과 다른 경우 0.
- `advisor_config_usage.py`: `weights.shrinkage.k`와 `weights.augment.w_editorial`이 "미사용"으로 표시된다. **둘 다 오탐이다.** `k`는 `ShrinkageWeights.adjust()`(config.py 안)가 읽고, `w_editorial`은 `1-w_jev`로 쓰이며 validator가 합=1을 강제한다.
- 전체 키 전수 검사(`src/` 중 config.py 제외, `.key`나 `"key"` 형태로 참조되는지): 참조가 없는 키는 아래와 같다. **dead key는 0**이다.
  - 간접으로 읽힘: `vision.content_box`(`cfg.content_px()`), `shop.stage_weights`(`for_stage`), `augment.commit_by_stage`(`commit_for_stage`), `augment.w_editorial`.
  - Phase 4 app 루프·UI가 소비할 예정(문서화됨): `app.data_dir`, `app.log_dir`, `capture.monitor`, `vision.capture_fps`, `vision.traits_every_s`, `ui.opacity`, `ui.click_through`, `logging.recommendation_log`, `logging.save_frame_on_error`.
  - 수집기가 하나뿐이라 소비처 없음: `stats.primary`, `stats.fallbacks`.
- 새 키를 코드에 하드코딩한 중복: `HYSTERESIS_OTHER_SHARE`, `OVERALL_ITEM_STAT_GAMES_FACTOR`, `ITEM_MIN_MARGIN`은 삭제됐다.
  - 남은 모듈 기본값은 설정 없이 쓰는 경로용이다. `matching.MIN_MARGIN/RELAXED_MARGIN`과 `ChangeDetector(threshold=24, stable_frames=2)`는 `test_vision_defaults_match_module_defaults`가 고정한다. `db.DEFAULT_KEEP`은 이번에 신규 테스트로 고정했다.
- 삭제한 `[capture] poll_interval_ms`/`stable_frames`: 코드에서 읽는 곳 0, TOML 대입 0이다. 남은 언급은 설명 주석(`config.py:50-51`, `settings.toml:11`)과 삭제를 확인하는 테스트뿐이다. `CaptureCfg`의 필드는 `{monitor}`뿐이고, 옛 키를 쓰면 로드 오류가 난다(`test_config_round.py:79`).

---

## 3. 과금 안전
| 검사 | 결과 |
|---|---|
| 가짜 `TYPESAFE_API_KEY`를 설정하고 기본 설정으로 `create_advisor()`, `create_advisor("auto")`, 모듈 `advise(state, "auto")` 실행 | 백엔드 **mock / mock**, 외부 connect·DNS 시도 **0** |
| `create_advisor("live")` 생성만 하고 호출하지 않음(가드 아래) | 네트워크 0, 로그에 키 없음 |
| 전체 pytest(509 + 신규 10)를 키 설정과 `-p no_network_plugin`으로 실행 | blocked attempts = **0** |
| 루트 로거를 DEBUG로 두고 전 구간 로그와 Recommendation JSON에서 키 문자열 검색 | **0건** |
| 저장소 전체(.venv/.git/raw 제외)에서 키 형태 문자열(`sk-/ts-…`, `TYPESAFE_API_KEY = "…"`) 검색 | 0건. `_workspace/04_jev_live_s05_answers.json`에는 request_id와 토큰 수만 있고 key·authorization 문자열은 0건 |

---

## 4. 커밋 위생 (`git status`)
- `.gitignore` 제외 확인(신규 테스트로 고정): `data/stats/*.sqlite` 및 `-wal/-shm/-journal`(현재 `stats.sqlite` 18MB와 `-shm`/`-wal`이 무시되는 것을 확인), `data/raw/`, `data/templates/*/items/`(212개 무시), `items_screen/`, `.env`, `*.key`, `.venv`, `__pycache__`, `.pytest_cache`, `logs/`, `.claude/settings.local.json`.
- 커밋에 들어갈 파일 중 1MB를 넘는 것:

| 파일 | 크기 | 상태 | 판단 |
|---|---|---|---|
| `data/stats/metatft_18.2b.json` | **12.6MB (>5MB)** | 수정(이미 추적 중, 75233b9) | 커밋 가능. 변환 산출물(holds/builds 분리 R16)이고 diff는 약 52만 줄이다. 앱의 JSON 폴백 경로이자 advisor 테스트 입력이다. 크기가 커지는 추세라 Phase 4에서 LFS나 추적 제외를 검토할 수 있다 |
| `_workspace/04_vision_rois_3-3.png` | 3.4MB | 미추적 | **스테이징 금지 (H1)**: 화면 크롭에 오버레이한 그림 |
| `_workspace/04_vision_rois_2-1_augment.png` | 3.0MB | 미추적 | **스테이징 금지 (H1)** |
| `tests/fixtures/stats/mini_18.json` | 1.6MB | 미추적 | 커밋 필요(advisor 테스트 입력) |

- 그 밖의 미추적 파일은 모두 커밋 대상이다: `src/`의 신규 모듈, `tests/`, `tests/fixtures/states/`, `_workspace/` 보고서와 QA 스크립트, `data/templates/README.md`.
- 참고: fixture 방송 스크린샷 7장(각 약 3.5MB)은 Phase 1 커밋 8ea08da에 이미 들어 있다. 캡처 요청서의 권고대로 이 저장소는 공개하지 않는다(다른 소환사 이름이 보인다).

---

## 5. pytest (가짜 키 + 네트워크 가드, `-o addopts=`)
pytest-randomly는 설치돼 있지 않다. 대신 스위트를 나눠 돌리고, 파일 순서를 역순으로 돌리고, 해시 시드를 바꿔 돌렸다.

| 실행 | 결과 |
|---|---|
| 전체 | **509 passed, 3 skipped**(live), 94s |
| `tests/advisor`만 | 223 passed, 3 skipped |
| vision(`test_vision.py`, `test_vision_qa04.py`)만 | 92 passed |
| stats 5개 파일만 | 67 passed |
| config + contracts + boundaries만 | 127 passed |
| **파일 역순** 전체 | 509 passed, 3 skipped (순서 의존성 없음) |
| `PYTHONHASHSEED=999`로 advisor + boundaries | 241 passed, 3 skipped |
| 신규 `tests/test_phase3_final_qa.py` | 10 passed → 합계 **519 passed** |

xfail은 0이다. Phase 3 QA의 strict xfail 4건(stats W1, advisor 1g, vision V2 2건)이 모두 해제됐다.

---

## 6. Phase 4로 넘기는 항목 / 사용자 대기

### Phase 4 (구현)
1. app 루프: capture_fps, 변화 감지(`ChangeDetector.from_cfg`), 묶음 단위 부분 인식과 병합, traits_every_s, 백그라운드 advisor, Windows onnxruntime 성능 측정(#14, 목표는 인식 300ms 미만, 전체 2s 미만).
2. UI: `target_comps`를 advisor 순서 그대로 표시(3w-c), 캐러셀(통계) 표시, ui/logging/app 키 연결. `--no-jev`를 `"off"`로 연결.
3. augments_owned 세션 추적(R3, HUD 판독 뒤에).
4. S1: `static_extract`에서 items name_ko/en의 마크업 22건을 제거한다(stats).
5. 튜닝: s09 branch가 실제 통계에서 뒤집히지 않는 문제(`item_saturation`, carry BIS 가중)(jev).
6. 사소: openvino-telemetry 안내(L1), pyproject arm64 주석(#9), `_preference` 별칭과 낡은 주석 제거, `stats.primary/fallbacks` 연결(수집기가 추가될 때), s09 fixture를 완성템 1개로 줄이기(선택), `metatft_*.json` 추적 정책.

### 사용자 대기 (BLOCKED)
- 원본 1920x1080 캡처(요청서 ★ 항목): 실제 인식 정확도(#19), 증강 등급 혼합 #5(V2 잔여 8.6%), 연패 #8(streak 부호 → 계수 0.8), 전투 #7(V5), 증강 선택 직후 HUD #6(R3), 7레벨 이상 #2(XP 표 확정), 창모드 캡처(content_box 예시).

## 재실행
```
PYTHONPATH=_workspace/qa_scripts TYPESAFE_API_KEY=fake .venv/bin/python -m pytest -p no_network_plugin -rxs   # 519 passed, 3 skipped
.venv/bin/python _workspace/qa_scripts/phase3_final_boundary.py [--json OUT]                                  # PROBLEMS: 0
.venv/bin/python _workspace/qa_scripts/config_proposal_check.py                                               # 85 / 0 / 0
.venv/bin/python _workspace/qa_scripts/advisor_config_usage.py                                                # 오탐 2(k, w_editorial)
```
