# 11 QA 최종 게이트: 08 QA 이후 커밋 전 변경 전체

작성일: 2026-09-22 / 작성자: qa-validator / Windows 11, `.venv` Python 3.14 / Jev는 mock만 사용 / **커밋 안 함**
범위: `git status` 기준 수정 39개와 새 파일 18개. 입력 보고서: `08_qa_vision_1080p.md`(이전 QA 보고서), `08_stats_patch_18.3.md`, `09_jev_late_game.md`, `09_vision_augment_icons.md`, `10_app_integrator_fixes.md`

## 판정: **GATE PASS** (FAIL 0 / WARN 6)

커밋을 막을 결함은 없다. WARN 6건은 모두 동작 설계에 관한 것이라 다음 라운드에서 다뤄도 된다. 이 중 W1은 xfail(strict) 테스트로 고정해 두었다.

## 요약: PASS 14 / FAIL 0 / WARN 6

| # | 항목 | 결과 | 근거(파일:라인 / 수치) | 담당 | 수정 요청 |
|---|---|---|---|---|---|
| 1 | 08 A1 → 전투 중 "직전 추천" 표시가 advisor 결과를 바꾸지 않는가 | PASS | `report.py:227-247` `kept_view`는 `model_copy(update={"shop": keep})`로 사본만 만든다. `loop.py` `_kept_update`/`_on_advice`에서 `last_recommendation`은 원본이다. QA 테스트 `test_kept_view_never_mutates_original_and_is_idempotent`로 확인: 원본 `model_dump`가 그대로이고, 두 번 적용해도 결과가 같다. 스모크: 전투 3장에 "직전 추천(전투 중)", 모루와 악의 여단에 "(아이템 선택 중)"이 붙는다 | - | - |
| 2 | 08 A2 → 보유 증강 병합 규칙(늘기만 함, 수동 우선, 2-1/3-2/4-2 상한) | PASS | `session.py:411-432` `_augment_verdict`와 `:456-467` `_learn_owned`의 줄 거르기가 같은 세 조건(칸 수 감소, 기존 칸 불일치, 스테이지 상한)을 쓴다. `_apply_augments`(`:517-540`)는 언제나 세션 값을 advisor로 보낸다. QA 테스트 `test_advisor_augments_always_equal_session_list`: 5프레임(상한 초과, 상대 줄, 정상 확장, 짧은 판독)에서 advisor 입력이 매번 세션 목록과 같고, 버린 값 3건이 새지 않는다. `test_manual_list_survives_vision_and_learning_rows`: 수동 값은 vision에도, 학습에도 덮이지 않는다 | - | - |
| 3 | augment_learn ↔ `observe(owned_row=)` ↔ advisor `augments_owned` | PASS / **WARN W1** | 연결: `loop.py` `step` → `recognizer.last_owned_row` → `tracker.observe(owned_row=)`. learner도 연결된다(`LiveLoop.__init__`). advisor는 `features.py:182-183`에서 `confidence ≥ min_conf`만 본다. 세션 값의 신뢰도는 1.0이다. **W1**: 제시 목록이 있을 때 vision 전체 목록 판독이 **제시되지 않은** 증강으로 칸을 늘리면 `_track_augments`가 받는다. 같은 프레임의 `_learn_owned`는 이것을 "제시되지 않음"으로 거부하고 목록을 버린다(`session.py:480-484`). 두 경로가 모순이고, 받은 값은 '늘기만' 규칙 때문에 그 판 동안 고쳐지지 않는다. 재현은 `tests/test_qa11_final_gate.py::test_vision_growth_must_be_among_offered_augments`(xfail strict). 실제로 일어나려면 vision이 0.80 이상·여유 있는 점수로 오인식해야 하는데, 원본 13장에서 오인식은 0건이다 | app-integrator | §3-A1 |
| 4 | 새 판 확인 ↔ 세션 보관 ↔ augment_learn 상태 초기화 | PASS | `loop.py` `_reset_confirmed`: 0.95 이상이면 즉시, 약한 신호는 3회 또는 3초 뒤에 초기화하고 정지 화면은 1초마다 다시 판별한다. `session.reset`은 `archive()`한 뒤 `SessionData()`를 새로 만들어 offer_pool, offer_base, owned_count, learned, augments_owned를 모두 비운다. learner는 무상태이고 매처에 더한 템플릿은 의도적으로 남긴다(`augment_learn.py:185`). QA 테스트 `test_reset_clears_learning_state_and_owned_augments`가 보관 파일이 생기는 것까지 확인한다. 확인 대기 중에는 `observe`를 부르지 않아 스테이지와 증강을 병합하지 않는다 | - | - |
| 5 | 새 weights 키 ↔ config 검증 ↔ candidates 기본값 | PASS | `config.py:297-299` `CompWeights`(4, 0.30, 2.0), `:331` `PrefilterWeights.w_tempo=0.25`(합=1 검증 `:335` 밖), `weights.toml:22-24,43`의 값이 같다. `candidates.py:36-37` `late_cfg`는 설정만 읽는다(상수와 getattr 폴백 제거). grep 결과 다른 모듈에서 쓰는 상수가 남아 있지 않다. 기존 `test_late_cfg_reads_weights_with_same_defaults`가 통과한다 | - | - |
| 6 | `patch_version` 헬퍼의 일관성 | PASS | stats: `repository.py:506-508`, `refresh.py:86`, `stats/__main__.py:55`가 `latest_snapshot`을 쓴다. advisor: `stats_source.py:19,60-61`이 `patch_sort_key`를 쓴다(동률이면 파일명으로 가르고, stats는 mtime으로 가른다. 패치 키가 같은 서로 다른 파일은 사실상 없다). 실제 저장소에서 둘 다 `metatft_18.3.json`을 고른다. QA 테스트 `test_stats_and_advisor_pick_same_snapshot`: mtime을 뒤섞은 18.10/18.9/18.3/18.2b에서 둘 다 18.10을 고른다 | - | - |
| 7 | Riot 아트와 개인 캡처가 git에서 빠지는가 | PASS | `git check-ignore -v`로 확인: `augments/`, `augments_alt/`(`sources.json` 포함), `augments_screen/`, `items/`, `items_screen/`, `tests/fixtures/screens/raw/`(`.expected.json` 포함), `_state/`, `_state/sessions/`, `data/raw/`, `data/stats/*.sqlite{,-wal,-shm}`, `.env`가 모두 무시된다. `git ls-files data/templates`에는 README.md만 있다. pytest와 스모크를 돌린 뒤에도 `git status`에는 새 추적 대상이 생기지 않았다(`augments_screen/`에 쓰인 파일 0). 회귀 테스트 `test_private_assets_are_gitignored` | - | - |
| 8 | 비밀·큰 파일 | PASS / **WARN W2** | diff와 새 파일 전체에서 API 키 패턴(`sk-`, `ts_`, `TYPESAFE_API_KEY=값`, bearer)은 0건이다. 5MB를 넘는 파일은 `data/stats/metatft_18.3.json` **13.4MB** 하나다. 08 stats §3 정책상 커밋 대상이고, 18.2b(13.3MB)가 이미 추적되고 있다. **W2**: 저장소가 공개라면 MetaTFT 가공 데이터를 재배포하는 셈이고, 패치마다 약 13MB씩 늘어난다. 정책은 08 stats §3의 제안("직전 패치 1개만 유지")을 따르되, 사용자 확인을 권한다. `_workspace/07_user_answers.md:6`에 로컬 경로(`C:\Users\hjk38\Desktop\...`)가 있지만, 같은 사용자명이 이미 추적 중인 `01_stats-researcher_sources.md`에도 있어 새로 노출되는 것은 없다 | 사용자(정보) | - |
| 9 | ID 체계(새 증강 템플릿·stats 18.3 ↔ static) | PASS | `augments/` 320개, `augments_alt/` 38개, `sources.json` 49항목, `augments_screen/` 0개가 **모두 static 증강 ID**다. 18.3 stats의 DA_ ID 503개 가운데 static에 없는 6개(`DA_StarringUp`, `DA_Lineup`, `DA_18_RivalsAugmentPlus`, `DA_NestingDolls`, `DA_SubscriptionService`, `DA_Artifact_Hullcrusher`)는 모두 `data/static/18/unmapped.json`에 선언돼 있다 | - | - |
| 10 | 전체 pytest | PASS | 이번 QA 테스트를 넣기 전에 **769 passed, 3 skipped**(live Jev)를 재현했다. 넣은 뒤에는 **775 passed, 3 skipped, 1 xfailed**(W1), 66초 | - | - |
| 11 | 새로 clone한 상태에서의 pytest | PASS | 추적 파일과 추적 예정 파일만 scratch에 복사해(54MB, raw 캡처·템플릿·sqlite 없음) `PYTHONPATH`로 돌렸다: **719 passed, 59 skipped, 1 xfailed**, 실패 0. raw·템플릿에 의존하는 테스트는 모두 skip된다 | - | - |
| 12 | vision 기준선: 원본 13장 | PASS | screen_mode 13/13, stage 12/12, level 9/9, xp 9/9, gold 9/9, streak 9/9, hp 12/12, shop_odds 9/9, shop 9/9(칸 45/45), items 12/12, augments_owned 4/4. **오답 0, 미인식 0**. 08과 같다 | - | - |
| 13 | vision 기준선: 방송 7장 | PASS | screen_mode 7/7, stage 7/7, level 3/3, xp 5/5, gold 5/5, streak 5/5, hp 7/7, odds 5/5, shop 4/5(칸 24/25, 미인식 1, 틀린 ID 0), augment_offer 1/1. 05와 08 기준선과 같다 | - | - |
| 14 | `--screenshot tests/fixtures/screens/raw --jev mock` 스모크 | PASS | 13장, 종료 코드 0, 6.7초. 인식 216~468ms, 추천 0~4ms. 5-5 레벨 9에서 "초반: 방향 미정"이 사라지고 "레벨 템포 일치 … 보유 아이템·증강 신호 없음: 레벨 템포·메타로 추정"이 나온다(09 J1 반영). Victory에서는 "세션을 초기화한다"가 나온다 | - | - |
| 15 | 5-5 가운데 증강 칸 None | PASS | 라벨에 `augments_owned`가 없는 것이 맞다(내면의 야수와 내면의 야수+가 같은 그림). 인식 결과도 None이어서 advisor는 "보유 증강 없음"으로 처리한다. 자동 학습으로만 채워진다 | - | - |
| 16 | 세션이 빈 상태의 상대 보드 첫 판독 | **WARN W3** | 10 app §2에 적힌 남은 위험이다. 세션과 수동 입력이 모두 비어 있을 때 상한 안의 상대 줄을 받으면 그 판 동안 고정된다. 관전 캡처가 없어 재현하지 못했다 | vision, app-integrator | §3-V1 |
| 17 | 초기화 직후 늦게 도착한 추천 | **WARN W4**(기존) | `ThreadAdviceRunner`가 초기화 전 상태로 계산한 추천이 `_do_reset` 뒤에 도착하면 `_on_advice`가 `last_recommendation`에 이전 판의 추천을 넣는다(`loop.py` `_on_advice`에 세대 확인이 없다). 다음 준비 화면 추천이 곧 덮어쓰므로 영향은 짧다. 06부터 있던 동작이다 | app-integrator | §3-A2 |
| 18 | `kept_view`의 UNKNOWN 칸 | **WARN W5**(경미) | 지금 상점 칸이 UNKNOWN(식별 실패)이면 `cur_id=None`이 되어 "산 칸"으로 센다(`report.py:240-244`). 추천을 빼는 쪽이라 안전하지만 "산 칸 N개 제외" 문구가 틀릴 수 있다 | app-integrator | §3-A3 |
| 19 | 인식 시간(듀얼 4480x1440) | **WARN W6**(기존) | 스모크에서 인식이 216~468ms로 목표 300ms를 넘는 장이 있다. 08 V2(레터박스·듀얼 경로 비용)와 같은 원인이다 | vision | 08 §3-V2 |
| 20 | vision 증강 보고의 규칙 서술 ↔ 실제 코드 | PASS(문서 정정) | 09 vision §3.4는 "`_apply_augments`: vision 값 우선"이라고 적었지만, 뒤에 한 10 app 수정으로 **세션 값 우선**이 됐다. 코드와 테스트는 10 기준으로 일관된다. 09 보고서의 해당 문장만 옛 내용이다 | - | - |

## 1. QA가 추가한 것

- `tests/test_qa11_final_gate.py`(신규, 7개: 6 pass, 1 xfail strict)
  - `test_advisor_augments_always_equal_session_list`: 버린 vision 값이 advisor로 새지 않는다.
  - `test_manual_list_survives_vision_and_learning_rows`: 수동 값은 vision에도, 학습에도 덮이지 않는다.
  - `test_reset_clears_learning_state_and_owned_augments`: 새 판에서 학습 상태를 비우고 세션을 보관한다.
  - `test_vision_growth_must_be_among_offered_augments`: **xfail(strict)**, W1 재현. 고치면 XPASS가 떠서 실패하므로 그때 xfail을 풀면 된다.
  - `test_kept_view_never_mutates_original_and_is_idempotent`
  - `test_stats_and_advisor_pick_same_snapshot`
  - `test_private_assets_are_gitignored`(git 저장소가 아니면 skip)
- 제품 코드는 고치지 않았다. W1은 규칙을 정하는 문제라 담당자에게 넘긴다(§3). 제시 목록에 없는 판독을 거부하면, 리롤 후보를 못 읽은 경우 정답을 영구히 잃을 수 있다.

## 2. 커밋될 파일 (58개, `git status --porcelain --untracked-files=all`)

- 수정 39개: `.gitignore`, `config/settings.toml`, `config/weights.toml`, `data/static/18/unmapped.json`, `data/templates/README.md`, `src/tft_advisor/{config.py, advisor/{candidates,engine,features,scoring,stats_source}.py, app/{live,loop,overlay,report,screenshot,session}.py, stats/{__main__,refresh,repository,metatft_convert}.py, stats/collectors/metatft.py, vision/{capture,change,evaluate,icons,ocr,recognizer,regions,screen_mode,templates}.py}`, `tests/`의 9개
- 새 파일 19개:
  - 코드: `src/tft_advisor/patch_version.py`, `src/tft_advisor/vision/augment_learn.py`
  - 테스트: `tests/advisor/test_advisor_late_game.py`, `tests/app/test_app_fixes10.py`, `tests/test_augment_learn.py`, `tests/test_qa08_vision_boundaries.py`, `tests/test_vision_1080p.py`, `tests/test_qa11_final_gate.py`, `tests/fixtures/states/s14_late_blind.json`
  - 데이터: `data/stats/metatft_18.3.json`(13.4MB)
  - 보고서: `_workspace/07_user_answers.md`, `07_vision_1080p_modes.md`, `07_vision_draft_labels.md`, `08_qa_vision_1080p.md`, `08_stats_patch_18.3.md`, `09_jev_late_game.md`, `09_vision_augment_icons.md`, `10_app_integrator_fixes.md`, `patch_18.3_diff.md`, 이 파일 `11_qa_final_gate.md`
- 이미지·sqlite·캡처·Riot 아트는 **0개**다. `.png`는 한 개도 추적 대상에 오르지 않는다.
- 줄바꿈: 여러 파일에서 "LF will be replaced by CRLF" 경고가 나온다. autocrlf 정규화 때문이고 내용 diff에는 영향이 없다.

## 3. 담당 에이전트별 할 일 (다음 라운드, 커밋을 막지 않음)

### app-integrator
- **A1 (W1)**: 제시 목록(`offer_pool`)이 있는데 vision 전체 목록 판독이 제시되지 않은 증강으로 칸을 늘리면 어떻게 할지 정하자. 제안은 두 가지다. (a) `_augment_verdict`에서 늘어난 칸이 `offer_pool`의 같은 그림 가운데 하나일 때만 받는다. 이때 `_learn_owned`의 "제시되지 않음 → 목록 버림"을 "목록 유지 + 다음 증강 라운드에서 만료"로 바꿔야 한 프레임 뒤에 우회되지 않는다. (b) 같은 값이 N프레임 연속으로 나올 때만 받는다. 어느 쪽이든 `tests/test_qa11_final_gate.py`의 xfail을 푼다.
- **A2 (W4)**: `_do_reset` 때 세대 번호를 올리고, `_on_advice`는 이전 세대의 결과를 버리자.
- **A3 (W5)**: `kept_view`에서 UNKNOWN 칸은 "산 칸"이 아니라 "바뀐 칸"(또는 유지)으로 세자.

### vision-engineer
- **V1 (W3)**: 관전 신호(10 app §7). 준비 단계에 상대 보드를 보는 캡처가 필요하다.
- 08 V2(인식 시간)는 그대로 남아 있다.

### 사용자 확인(정보)
- `metatft_{patch}.json`(패치당 약 13MB)을 공개 저장소에 계속 커밋할지 정해 달라(W2).
- 1080p 증강 선택 화면과 그 직후 준비 화면 한 쌍을 캡처해 주면 선택 순간 학습을 실제 화면으로 끝까지 검증할 수 있다.

## 4. 재현 명령

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -o addopts="" -q           # 775 passed, 3 skipped, 1 xfailed
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m tft_advisor.vision.evaluate tests/fixtures/screens/raw
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m tft_advisor.vision.evaluate
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m tft_advisor --screenshot tests/fixtures/screens/raw --jev mock
git -c core.quotepath=off check-ignore -v data/templates/18/augments_alt/sources.json _state/sessions/x.json
```
