# 10 app-integrator: 전투 중 추천 표시 · 보유 증강 병합 규칙 · 새 판 확인 · 가중치 키 · latest_json

작성일: 2026-09-22 / 작성자: app-integrator / 커밋하지 않음 / Jev는 mock만 사용
입력: `08_qa_vision_1080p.md`(A1~A3), `09_jev_late_game.md` §1, `09_vision_augment_icons.md` §3·§7, `08_stats_patch_18.3.md` §3·§5, `06_app-integrator_phase4.md`

## 0. 요약

| # | 요청 | 결과 |
|---|---|---|
| 1 | 전투 중 추천 유지 표시 (QA A1) | 목표 덱은 그대로 둔다(재계산 없음). 표시용 사본에서 **이미 산 칸(빈 칸이 됨)과 바뀐 칸(새로고침)을 빼고**, "직전 추천(전투 중)" 표시를 붙인다. 콘솔·오버레이·`--screenshot` 세 경로 모두 적용 |
| 2 | 상대 보드 관전 시 보유 증강 오염 (QA A2) | "한 판 안에서는 늘기만 한다" 규칙과 수동 입력 우선 규칙을 넣었다. 규칙에 걸린 vision 값은 advisor로 가지 않는다. 선택 순간 학습(`_learn_owned`)도 같은 규칙으로 줄을 거른다 |
| 3 | game_over 오판으로 세션 삭제 (QA A3) | 강한 신호(신뢰도 0.95 이상 = "최종 순위"+"나가기")일 때는 즉시 초기화한다. 약한 신호는 3회 연속 또는 3초 뒤 재관측될 때만 초기화한다. 정지 화면이면 1초마다 다시 판별한다. 초기화 전에 세션을 `_state/sessions/`에 보관한다(10개 유지) |
| 4 | `CompWeights` 후반 키 | `config.py`와 `weights.toml`에 실제 값을 넣었다. `late_cfg`는 이제 설정에서만 읽는다. 기본값이 같다는 것은 테스트로 고정했다 |
| 5 | `repository.latest_json` | 파일 수정 시각 대신 패치 번호를 숫자로 비교한다(`18.10` > `18.9`). 공용 헬퍼 `tft_advisor/patch_version.py`를 쓴다 |
| 6 | `shop_locked` | 보류했다(§6) |
| 7 | 테스트·스모크 | **769 passed, 3 skipped, 0 failed**(746에서 23개 늘었다). `--screenshot raw --jev mock` 13장의 한국어 출력을 확인했다 |

## 1. 전투 중 직전 추천 표시 (A1)

- 선택: QA 제안 (b)+(c)를 적용했다. (a) "전투 중 가벼운 재계산"은 advisor 계약(`engine.py` COMBAT → `session.last`)을 바꿔야 하고, 09 jev가 "A1 COMBAT 규칙 유지"를 요청했으므로 하지 않았다.
- `app/report.py`
  - `kept_view(rec, state) -> (표시용 사본, KeptInfo)`: 직전 추천의 상점 칸마다 지금 상점(병합 상태) 칸과 비교한다. 지금 칸이 빈 칸이면 산 것으로 보고 뺀다(`bought`). 다른 유닛이면 새로고침으로 보고 뺀다(`changed`). 지금 상점을 모르면(None) 거르지 않는다. 목표 덱·아이템은 건드리지 않는다. 원본 `Recommendation`(advisor 세션의 직전 추천)은 바꾸지 않는다.
  - `KeptInfo(mode, bought, changed)`: `.label`은 "직전 추천(전투 중 / 아이템 선택 중 / 화면 판별 실패)"이고, `.note()`는 "…: 준비 단계 추천을 유지한다(목표 덱 고정) · 산 칸 1개 제외 · 바뀐 칸 N개는 준비 단계에서 다시 계산"이다.
  - `format_report(..., kept=)`: 머리글 한 줄을 붙이고 `[상점 — 직전 추천(전투 중)]`으로 표시한다. 남은 칸이 없으면 "남은 추천 칸 없음"을 띄운다.
- `app/loop.py`: KEEP 화면은 `LoopUpdate(kind="kept", recommendation=표시용 사본, kept=KeptInfo)`를 보낸다. `last_recommendation`에는 원본을 둔다. 준비 단계에서 계산한 추천이 **전투로 넘어간 뒤 도착하면** `_on_advice`도 현재 화면 기준 사본을 보낸다.
- `app/overlay.py`: 노란 안내 줄, 목표 덱 섹션 옆 라벨, 상점 섹션 라벨을 단다. 직전 추천의 "[구매]"는 초록 대신 흐린 색으로 칠한다. `set_data(..., kept=)`.
- `app/live.py`(콘솔): 전투 중 사본은 매번 새 객체라서 중복 출력 판정을 `(id(last_recommendation), kept, message)`로 바꿨다.
- `app/screenshot.py`: KEEP 화면이면 직전 이미지의 추천에 같은 처리를 한다. 스모크 예: `2-5 전투 시작` → "[직전 추천(전투 중): … · 산 칸 1개 제외 · 바뀐 칸 4개는 준비 단계에서 다시 계산]". 폴더가 이름순이라 직전 이미지가 다른 라운드여서 "바뀐 칸"이 많다. 실시간에서는 같은 라운드의 준비 화면과 비교한다.

## 2. 보유 증강 병합 규칙 (A2)

`app/session.py` `_track_augments(recognized, merged)`와 `_augment_verdict`. 이번 프레임에 vision이 **실제로 읽은** 값만 평가한다. 이전 코드는 병합 상태를 봐서 이어진 값도 vision으로 취급했다.

1. 기존 칸의 값은 바뀌지 않는다. 세션 목록과 앞부분이 모두 같아야 한다. 같은 그림(`learner.same_picture`)이면 같은 증강으로 보고, 이름은 세션 값을 유지한다(선택 순간 학습으로 정한 이름이 더 구체적이다).
2. 칸 수는 줄지 않는다.
3. 칸 수는 `AUGMENT_STAGES = 2-1/3-2/4-2`를 지나야 는다(`augments_allowed_at`). 스테이지를 모르면 늘리지 않는다. 세션이 비어 있을 때도 적용한다. 예: 2-5에 3칸이면 버린다.
4. 수동 입력이 우선한다. 수동 목록과 어긋나면 버리고, 수동 목록 뒤에 칸이 붙은 확장만 받는다(출처는 `tracked`). `set_augments_owned`는 언제나 덮어쓴다.

- 버린 값은 advisor로 가지 않는다. `_apply_augments`는 항상 세션 값을 얹는다. 세션이 비어 있으면 None이다. vision 값을 이번 프레임에 그대로 받았을 때만 vision 신뢰도를 살린다. 버린 횟수는 `tracker.augments_rejected`에 세고 로그에 이유를 남긴다.
- 자동 학습과의 충돌: `_learn_owned` 앞에 같은 검사를 넣었다. 칸 수가 세션보다 적거나, 스테이지 상한을 넘거나, 이미 읽힌 칸이 세션과 다르면 그 줄은 **`owned_count`도 갱신하지 않고** 학습에 쓰지 않는다. 그래서 상대 줄이 `offer_base`를 오염시키지 않는다. 학습 결과(`tracked`)는 규칙 1의 기준값이 된다.
- 계약 변경: `test_vision_augments_win_over_session`(tests/app)과 QA의 `test_owned_augments_from_vision_are_trusted_over_manual`은 "수동 우선"으로 바꿨다(이름도 바꿈).
- 관전 화면 판별 신호(내 HP 원, 닉네임 위치)는 넣지 않았다. 관전 캡처가 없어 기준을 잡을 수 없다. 남은 위험이 하나 있다. **세션이 비어 있고 수동 입력도 없을 때** 처음 읽힌 줄이 상대의 것이면 그대로 받는다. 칸 수는 스테이지 상한 안이다. 한 번 받은 뒤에는 규칙 1 때문에 고쳐지지 않으므로 수동 입력으로 바로잡아야 한다. vision에 관전 신호를 요청한다(§7).
- 규칙의 가정: 추가 증강을 주는 증강이 있으면 상한 3에 걸릴 수 있다. 세트 18에서는 확인하지 못했다. 그런 경우가 생기면 수동 입력으로 넣는다.

## 3. 새 판 확인·세션 보관 (A3)

- `app/loop.py` `_handle`/`_reset_confirmed`/`_recheck_due`/`_do_reset`
  - `screen_mode` 신뢰도가 `app.reset_strong_confidence`(0.95) 이상이면 즉시 초기화한다. 현재 vision 기준으로 "최종 순위"와 "나가기"가 모두 보이는 game_over다. 로딩 화면(0.6)과 "나가기"만 보이는 경우(0.8)는 확인 대기로 간다.
  - 확인 대기 중에는 세션 상태를 바꾸지 않고 `kind="kept"`와 `message="새 판 확인 중(…)"`을 보낸다. `reset_confirm_frames`(3)회 연속 관측되거나, 첫 관측 뒤 `reset_confirm_s`(3초)가 지나 다시 관측되면 초기화한다. 다른 화면이 한 번이라도 나오면 대기를 취소한다.
  - 정지 화면(게임 종료·로딩)은 변화 감지에 걸리지 않는다. 그래서 대기 중에는 `reset_recheck_s`(1초)마다 `stage` 묶음(화면 판별)만 강제로 다시 읽는다.
  - 보조 신호: 세션 스테이지가 2-1 이상인데 1-x가 읽히면(`SessionTracker.looks_like_new_game`, 로딩 화면을 놓친 경우) 같은 확인을 거쳐 새 판으로 본다. 대기 중에는 스테이지를 병합하지 않는다. 그래야 OCR 오독 한 번으로 세션 스테이지가 내려가지 않는다.
- `app/session.py` `reset()` → `archive()`: 지우기 전에 `session.json`을 `{state_dir}/sessions/session_YYYYmmdd_HHMMSS_ffffff.json`으로 복사한다. 보관본은 이름순(= 시간순)으로 `app.session_archive_keep`(10)개만 남긴다. 쌓인 것이 없는 세션(프레임 0, 증강 없음)은 보관하지 않는다. 경로는 `tracker.last_archive`에 남고, 콘솔에 "[새 판] 세션을 초기화했다 (이전 세션 보관: …)"로 출력한다. 되살리기 명령은 아직 없다. 필요하면 그 파일을 `_state/session.json`으로 복사하면 된다(2시간 안이면 `load()`가 받는다).
- 새 설정 키(`config.py` `AppCfg`, `settings.toml [app]`에 한국어 주석): `reset_strong_confidence=0.95`, `reset_confirm_frames=3`, `reset_confirm_s=3.0`, `reset_recheck_s=1.0`, `session_archive_keep=10`.
- `--screenshot` 경로는 그대로다. advisor가 GAME_OVER에서 자기 세션을 바로 비운다. 정지 이미지 한 장이라 확인할 방법이 없다.

## 4. 가중치 설정 키 (09 J1 요청)

- `config.py` `CompWeights`: `undecided_until_stage:int=4 (1~9)`, `w_tempo:Unit=0.30`, `tempo_span:float=2.0 (0<·≤9)`. `PrefilterWeights`: `w_tempo:Unit=0.25`(합=1 제약 밖의 가산 항).
- `config/weights.toml`: 주석이던 예정 키를 실제 값으로 옮겼다.
- `advisor/candidates.py`: 상수 4개와 `getattr` 폴백을 지웠다. `late_cfg(w)`는 이제 `w.comp.*`와 `w.prefilter.w_tempo`만 읽는다. 다른 곳에서 상수를 import하지 않음을 확인했다.
- 테스트 `test_late_cfg_reads_weights_with_same_defaults`: `Weights()`와 `load_weights()` 모두 (4, 0.30, 2.0, 0.25)이고, toml 덮어쓰기가 반영된다. advisor 회귀 테스트(s14 등)가 모두 통과하므로 동작 변화는 없다.

## 5. `latest_json` 패치 번호 비교 (stats 소관, 요청에 따라 수정)

- 새 파일 `src/tft_advisor/patch_version.py`: `patch_sort_key`(jev의 구현을 그대로 옮김), `snapshot_sort_key`(패치 키, mtime, 파일명), `latest_snapshot(dir, prefix)`. stats가 advisor를 import하지 않도록 최상위에 두었다.
- `advisor/stats_source.py`: 자체 정의를 지우고 `patch_version.patch_sort_key`를 다시 내보낸다. 기존 호출과 테스트는 그대로 동작한다.
- `stats/repository.latest_json`, `stats/refresh.py`(이전 스냅샷 기준선), `stats/__main__.py cmd_load`(파일이 없으면 ValueError 대신 안내 후 2 반환) 세 곳 모두 `latest_snapshot`을 쓴다. mtime은 같은 패치끼리 가를 때만 쓴다.
- 테스트: mtime을 뒤집어도 `18.10` > `18.9`가 되고 `18.2b`는 둘보다 작다. 디렉터리가 없으면 None이다.

## 6. 보류: `shop_locked` 계약 필드

`GameState.shop_locked: bool | None` 제안(09 vision §4)은 **이번에 추가하지 않았다**. 잠긴 상태(닫힌 자물쇠)의 캡처가 없어 vision이 판별할 수 없다. 사용자에게서 상점을 잠근 캡처 1장을 받으면 contracts에 필드를 추가하고, vision 판별과 advisor 활용("잠금 상태면 이번 라운드 상점 유지")을 함께 넣는다.

## 7. 다른 에이전트 전달

- **vision-engineer**: 관전 화면 판별 신호를 요청한다. 내 HP 원, 닉네임 강조, "관전 중" 표시 가운데 하나를 쓰면 된다. 준비 단계에 상대 보드를 보는 캡처가 필요하다. 세션이 비어 있을 때의 첫 판독 위험(§2)을 없애 준다. `recognized.augments_owned`에 관전 플래그를 달거나 GameState 필드로 제안해 달라(계약 변경은 app이 한다).
- **jev-strategist**: `late_cfg`가 설정만 읽는다. 상수를 다시 만들지 말고 weights.toml에서 조정하라. A1은 COMBAT 규칙을 유지했다. 전투 중 재계산을 원하면 "상점 칸만 코드로 재채점(Jev 없음), 목표 덱 고정" API를 advisor에 제안해 달라.
- **qa-validator**: 새 경계는 다음과 같다. ① `LoopUpdate.kept`와 표시용 사본(원본 불변), ② `_augment_verdict`와 `_learn_owned`의 줄 거르기, ③ 새 판 확인 대기와 정지 화면 재판별, ④ `_state/sessions/` 보관·정리, ⑤ `patch_version` 공용 헬퍼. 실캡처가 필요한 것은 ESC/설정 메뉴 화면(game_over 오판 재현)과 관전 화면이다.
- **stats-researcher(정보)**: `latest_json`·`refresh` 기준선·`cmd_load`를 패치 번호 기준으로 바꿨다(§5).

## 8. 변경 파일

| 파일 | 변경 |
|---|---|
| `src/tft_advisor/app/report.py` | `KEPT_LABELS`, `KeptInfo`, `kept_view`, `format_report(kept=)` |
| `src/tft_advisor/app/loop.py` | 새 판 확인(`_reset_confirmed`/`_recheck_due`/`_do_reset`), `LoopUpdate.kept`, `_kept_update`, `_on_advice` 사본, docstring |
| `src/tft_advisor/app/session.py` | `AUGMENT_STAGES`, `stage_key`, `augments_allowed_at`, `_augment_verdict`, `_same_augment`, `_track_augments(recognized, merged)`, `_apply_augments`(세션 값 우선), `_learn_owned` 줄 거르기, `archive()`, `looks_like_new_game`, `archive_keep`, `augments_rejected` |
| `src/tft_advisor/app/overlay.py` | `kept` 표시, 확인 대기 메시지 |
| `src/tft_advisor/app/live.py` | `archive_keep` 연결, 콘솔 중복 판정·`kept`·보관 경로 출력 |
| `src/tft_advisor/app/screenshot.py` | KEEP 화면 `kept_view` |
| `src/tft_advisor/config.py`, `config/settings.toml` | `AppCfg` 새 판 확인·보관 키 5개 |
| `src/tft_advisor/config.py`, `config/weights.toml` | `CompWeights` 3개, `PrefilterWeights.w_tempo` |
| `src/tft_advisor/advisor/candidates.py` | 상수 제거, `late_cfg`가 설정만 읽음 |
| `src/tft_advisor/patch_version.py`(신규) | 패치 번호 정렬 공용 헬퍼 |
| `src/tft_advisor/advisor/stats_source.py` | `patch_sort_key` 다시 내보내기 |
| `src/tft_advisor/stats/repository.py`, `stats/refresh.py`, `stats/__main__.py` | `latest_snapshot` 사용 |
| `tests/app/test_app_fixes10.py`(신규, 22개) | §1~§5 |
| `tests/app/test_session.py`, `tests/test_qa08_vision_boundaries.py` | 증강 계약 변경 반영 +1개 |

## 9. 검증

- `.venv/Scripts/python.exe -m pytest -o addopts="" -q` → **769 passed, 3 skipped, 0 failed**.
- `PYTHONIOENCODING=utf-8 python -m tft_advisor --screenshot tests/fixtures/screens/raw --jev mock` → 13장, 종료 코드 0. 전투 3장에 "직전 추천(전투 중)"이 표시되고 산 칸·바뀐 칸이 빠졌다. 모루와 악의 여단에는 "직전 추천(아이템 선택 중)"이 붙는다. Victory는 "세션을 초기화한다"(screenshot 경로는 그대로)이다. 한국어 문구는 자연스럽다.
- 줄바꿈: `loop.py`·`report.py`·`overlay.py`는 이번 편집으로 LF가 됐다. git autocrlf 정규화 대상이라 `git diff --stat`에는 내용 변경만 잡힌다.
