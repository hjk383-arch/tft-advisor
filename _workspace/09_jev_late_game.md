# 09 jev-strategist: 후반 방향 표시, s09 검토, 보유 증강 반영

날짜: 2026-09-22. 수정 범위는 `src/tft_advisor/advisor/`, `tests/advisor/`, `tests/fixtures/states/`, `config/weights.toml`(주석만)이다. 커밋하지 않았다. Jev는 mock으로만 돌렸다.
pytest 결과: `-o addopts="" -q` → **726 passed, 3 skipped**(live Jev). 이전 707에 새 테스트 16개와 s14 fixture 파라미터 3개가 더해졌다.

## 1. J1 "초반: 방향 미정" 오표시 (QA 08 #18)

**재현**: `--screenshot "5-5 전투 전.png" --jev mock` 결과 목표 덱 3개 모두 근거에 "초반: 방향 미정"이 붙었고, 1위는 처형자 카직스였다.
인식된 state는 5-5, 레벨 9, 보드/벤치 미인식(None), 벤치 아이템은 소모품 2개(아이템 제거기, 재조합기)뿐이고 보유 증강도 None이었다. 이 캡처에서는 증강 줄이 읽히지 않았다.
**원인**: 자원 신호 3종(아이템, 증강, 보드)이 모두 비어 있으면 스테이지와 상관없이 undecided가 참이 됐다. mock 경로는 comp_pick 힌트, 폴백 경로는 `p_undecided=1.0`에서 그렇게 됐다. 그 결과 순위가 통계만으로 매겨졌고, lvl 7 리롤 덱인 카직스(5-5에 보통 레벨 7)가 레벨 9 플레이어의 1위가 됐다.

**수정** (계산 가능한 부분이라 전부 코드로 처리하고 Jev에는 묻지 않는다):
- `features.tempo_fit`: 덱의 `level_timing`으로 "이 스테이지에서 이 덱이 보통 도달하는 레벨"을 구해 내 레벨과 비교한다. `T = clip(1 − |level − expected| / 2)`.
- 후반은 스테이지 4 이상으로 정했다(`UNDECIDED_UNTIL_STAGE`).
  - 후반에는 undecided를 쓰지 않는다. `p_undecided=0`으로 두고, Jev comp_pick에서도 `undecided` 선택지를 뺀다.
  - 후반인데 보드를 인식하지 못했으면 **템포 항이 보드 항을 대신한다**. 가중치는 0.30으로 wb와 같다(`Scorer.comp_terms`의 `terms["tempo"]`, src="code"). 1차 필터 p(c)에도 0.25×T를 더해, 템포가 맞는 덱이 후보 N개 안에 들어오게 했다.
  - 템포 항은 보유 증강이나 아이템이 있어도 유지한다. 증강 하나가 생겼다고 템포가 사라지면 절벽처럼 순위가 튄다. 보드를 인식하면 템포 항은 빠지는데, 보드 적합(C3/U)이 이미 플랜을 반영하기 때문이다.
  - 자원 신호가 하나도 없을 때(`blind_late`)는 근거에 "보유 아이템·증강 신호 없음: 레벨 템포·메타로 추정"을 넣는다. 오버레이는 근거 앞 3개만 보여 주므로 "보드 미인식" 플래그보다 앞에 둔다. 표시 컷은 방향 미정일 때와 같은 `show_ratio_undecided`를 써서 2~3개가 보이게 한다.
- **수정 후 결과**: 1위 전쟁기계 자이라 아무무(0.78, Fast 8, 5-5에 레벨 9), 2위 지옥불 장로 드래곤, 3위 전쟁기계 애쉬. 근거는 "레벨 템포 일치: 5-5 레벨 9 (이 덱 평균 레벨 9) · 메타 평균 4.34등 · 보유 아이템·증강 신호 없음…"이다. 추천 시간은 6ms다.
- 초반(스테이지 3 이하) 동작은 바뀌지 않았다. s06(1-4)은 여전히 "방향 미정"이다.
- **설정 키**: `Weights`가 `extra="forbid"`라 weights.toml에 키를 먼저 넣으면 로드가 실패한다. 그래서 기본값은 `advisor/candidates.py` 상수에 두고, `late_cfg()`가 `getattr`로 설정값을 우선 읽게 했다. weights.toml에는 예정 키를 주석으로만 적었다. → **app-integrator에 제안**: `config.py` `CompWeights`에 `undecided_until_stage:int=4`, `w_tempo:Unit=0.30`, `tempo_span:float=2.0`, `PrefilterWeights`에 `w_tempo:Unit=0.25`를 추가해 달라.

## 2. s09 불변식 검토 (stats 08 §5-1)

**판정: (a) 불변식 방식을 채택하되, 불변식만으로는 부족해 보강했다.**
- 1스텝 보유 아이템(대천사의 지팡이 + 보석 건틀릿)은 베이가 BIS에도 들어간다. 그래서 자이라/베이가 차이가 0.01 이하인 것은 데이터상 정당하다. 고정된 1위를 요구하면 패치마다 테스트가 깨진다. (b)처럼 자이라 전용 신호를 추가하면 fixture의 의미가 바뀌고 mini 기대값에도 영향이 가서 택하지 않았다.
- **문제**: 2스텝은 재료(연습용 장갑)만 추가돼 만들 수 있는 완성템이 없다. 그래서 무보너스 점수가 1스텝과 **완전히 같다**(실측 veigar 0.6917 / zyra 0.6863 두 스텝 동일). "스텝 간 1위 유지"는 히스테리시스 보너스가 0이어도 통과하므로 설계 의도를 검증하지 못한다.
- **보강**:
  1. `test_fixture_expectations_on_repository` 실제 저장소 + s09 분기에서 이후 스텝이 `sig_unchanged=True`인지 확인하도록 했다. docstring에 검토 결과도 적었다.
  2. 새 테스트 `test_hysteresis_holds_near_tie_and_follows_clear_change`(mini와 real 두 저장소)는 데이터와 무관하게 동작한다. 1스텝 1위 덱의 `comp_item_fit` mock 답만 낮춰 무보너스 2위가 δ만큼 앞서게 만든다.
     - δ가 tie_eps(0.02)와 보호폭 bonus×(1−other_share)=0.0375 사이이면 1위가 유지되고 "직전 추천 유지"가 표시된다.
     - δ > 0.0375 + tie_eps이면 새 1위를 따른다. 1위가 고착되지 않는다는 뜻이다.
     - `hysteresis_weight`를 0으로 패치하면 이 테스트가 실패하는 것을 확인했다. δ ≤ tie_eps 구간은 comp_pick 타이브레이커가 따로 막기 때문에 그 구간은 쓰지 않는다.

## 3. 보유 증강 반영 확인

실제 18.3 저장소, mock Jev 기준이다(`test_advisor_late_game.py`).
- **목표 덱 점수**: 5-5 레벨 9, 보드 미인식 상태에서 초월(`DA_Ascension`)을 추가하면 자이라 0.782에서 0.839로, 장로 드래곤 0.743에서 0.761로 오른다. 덱별 편집 등급이 자이라 S, 장로 A이기 때문이다. 근거에는 "증강 시너지: 초월"이 나온다.
  - 어수선한 마음(A, 모든 덱에서 같은 등급)은 덱 간 차이를 거의 만들지 않는다. 기대한 동작이다.
  - **관찰(미변경)**: 증강 프록시 A(c)는 보유 증강 적합의 **평균**이다. 그래서 범용 증강을 하나 더 가지면 특정 덱용 증강의 효과가 희석된다. 초월만 가졌을 때 자이라 0.839가 초월+어수선한 마음이면 0.814가 된다. 순위는 유지된다. C2 질문의 레벨 문구("At least one augment…")는 max에 가까운 의미라, 프록시를 0.5·max+0.5·mean으로 바꾸는 방안을 튜닝 후보로 둔다.
- **증강 선택(augment_select)**: 각 증강의 `best` 덱과 `counterfactual_top`은 현재 후보(보유 아이템·증강 기반 rel)를 따른다.
  - AP 아이템일 때 best는 베이가/자이라, AD 아이템(무한의 대검+최후의 속삭임)일 때는 애쉬 계열로 바뀐다.
  - 초월을 보유하면 불완전한 초월의 best가 자이라로 모이고 점수도 오른다(0.8713에서 0.8775).
  - Jev 상태의 `resources`에는 완성템과 보유 증강 설명이 들어가 A1/A2 질문이 참조한다.
- **static에 없는 증강**: 18.3 stats 전용 ID 5개(`DA_Lineup` 등)나 임의 ID를 넣어 확인했다.
  - 이름은 ID에서 뽑은 문자열, 설명은 "unknown (new augment)"로 나가고 크래시는 없다.
  - 제시 증강일 때는 기존대로 gate가 낮아진다(0.4125, 1위가 되지 않음).
  - **수정**: 보유 증강 설명이 **모두** 없거나 의미를 잃은 경우, 덱 점수의 Jev `comp_augment_fit` gate(와 반사실 계산)를 `low_confidence_scale`로 낮추도록 했다(`Scorer.owned_aug_desc_lost`). 전에는 설명 없는 증강으로 한 Jev 판단이 full gate로 들어갔다.
  - 편집 등급이 없는 증강은 `unlisted_score`/`aug_neutral` 0.5로 처리되어 안전하다.
- 연패 부호(07 답변 1)는 vision의 `STREAK_SIGN_FACTOR`와 advisor의 `streak_text`(양수=연승)가 서로 맞다. 변경은 없다.

## 4. `latest_json` 사전순 문제

- advisor 쪽 `stats_source.default_stats_path`가 사전순 `max()`였다. `patch_sort_key`를 새로 만들어 숫자 조각은 정수로 비교하게 했다. "18.10" > "18.9", "18.3" > "18.2b" > "18.2"가 된다. 테스트: `test_patch_sort_key_numeric`, `test_default_stats_path_two_digit_patch`.
- stats 쪽 `stats/repository.latest_json`은 mtime 기준이다. git checkout이나 복사를 하면 mtime이 뒤섞여 틀릴 수 있다. stats-researcher 소관이라 손대지 않았다. → **stats-researcher에 제안**: `advisor.stats_source.patch_sort_key`를 재사용해 (패치 키, mtime) 순으로 정렬하라.

## 5. 변경 파일

- `src/tft_advisor/advisor/features.py`: `resource_availability`, `is_late`, `tempo_fit`
- `src/tft_advisor/advisor/candidates.py`: 후반 상수와 `late_cfg`, `tempo_active`, `late_blind`, `Candidate.T/T_exp`, 1차 필터 템포 가산
- `src/tft_advisor/advisor/scoring.py`: 템포 항, 후반 undecided 금지, `blind_late` 문구와 표시 컷, `owned_aug_desc_lost` gate
- `src/tft_advisor/advisor/engine.py`: 후반 comp_pick에서 undecided 제외, debug에 `blind_late`, `T`, `T_exp` 추가
- `src/tft_advisor/advisor/stats_source.py`: `patch_sort_key`, `default_stats_path`
- `tests/fixtures/states/s14_late_blind.json`(새 파일, 5-5 캡처 인식 결과), `tests/advisor/test_advisor_late_game.py`(새 파일, 16개), `tests/advisor/test_advisor_fixtures.py`(expect 키 `blind_late`), `tests/advisor/test_advisor_fixround.py`(s09 sig_unchanged 확인, 검토 기록)
- `config/weights.toml`: 예정 키 주석만 추가

## 6. 남은 일과 요청

- **vision**: 5-5 캡처에서 보유 증강 줄(어수선한 마음 / ? / 초월)이 None이다. 2-6에서는 읽힌다. 실시간 모드에서는 세션이 앞서 읽은 증강을 유지하므로 영향이 작지만, 단일 스크린샷에서는 자원 신호가 사라진다.
- **app-integrator**: §1의 config.py 필드 추가, 그리고 A1 COMBAT 규칙은 그대로 유지한다.
- **튜닝 후보**: 증강 프록시 평균 희석(§3), `tempo_span`(2레벨). 실제 사용 로그로 확인할 것.
