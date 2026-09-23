# 09 vision-engineer: 증강 아이콘 보충 · 5-5 가운데 증강 · 선택 순간 자동 학습 · 상점 잠금 버튼

작성일: 2026-09-22 / 작성자: vision-engineer / Windows 11, `.venv` Python 3.14 / 커밋하지 않음
입력: `07_vision_1080p_modes.md`, `07_user_answers.md`, `08_stats_patch_18.3.md`, `06_app-integrator_phase4.md` §2.5, `08_qa_vision_1080p.md`(A2·#8)

---

## 0. 요약

1. **세트 증강 255개 모두 그림을 확보했다.** CDragon 206개(그림 공유 포함) + tactics.tools 49개. 그림이 없는 증강은 0개다(QA #8 기준 81개가 없었다). `fetch-augments` 명령 하나로 받는다. 요청 간격은 0.5초, gitignore 로컬 캐시다.
2. **"404 4개"는 우리 버그였다.** 정적 데이터에 OP.GG 전체 URL로 들어간 아이콘 4개에 CDragon 주소를 덧붙이고 있었다. 3개는 CDragon hexcore에 같은 파일명으로 있다. `DA_ForgeAFriend` 1개는 tactics.tools에서 받았다.
3. **5-5 가운데 증강은 "내면의 야수" 계열로 확인했다**(원시 특성 증강). 점수는 0.844이고 2위 종결자 협곡야수는 0.723이다(차 0.121). 다만 "내면의 야수"(니달리/시비르)와 "내면의 야수+"가 **같은 그림**이라 그림만으로는 이름을 하나로 정할 수 없다. 그래서 규칙대로 `augments_owned=None`을 유지했다. 라벨에는 후보를 기록했다.
4. **선택 순간 자동 학습을 구현했다.** 증강 선택 화면의 후보 3개는 리롤로 바뀐 것까지 모아 둔다. 준비 화면 증강 줄에 새 칸이 **정확히 1개** 생기면 그 칸을 그 후보들 안에서만 비교해 확정한다. 확정되면 `augments_owned`에 반영하고 칸 그림을 `augments_screen/`에 저장한다. 다음 판부터는 전체 목록 매칭에서도 인식된다. 위 3번의 "내면의 야수 vs +" 같은 경우도, 선택지에 둘 중 하나만 나왔으면 확정된다.
5. **상점 잠금 버튼**: 사용자 확인(열린 자물쇠)에 맞춰 이전 보고서의 "정체 불명 카운터"를 정정한다. 잠긴 상태 캡처가 없어 판별은 넣지 않았다(§5, 계약 제안).
6. 테스트: **746 passed, 3 skipped, 0 failed**(시작 기준 705 → QA·jev 추가분 포함, 이번 추가 20개). 원본 13장과 방송 7장 평가 수치는 그대로다(오답 0).

---

## 1. A. 빠진 증강 아이콘 채우기

### 1.1 조사한 소스 (18세트 `missing-*` 48개 + OP.GG URL 4개)

| 소스 | 주소 형식 | 결과 | 채택 |
|---|---|---|---|
| CommunityDragon latest | `raw.communitydragon.org/latest/game/assets/maps/tft/icons/augments/hexcore/` | 목록 1117개를 전수 조사했다. `missing-*` 48개의 이름(verticality, capital-gains, loaded-dice, nesting-anvils 등)은 **없다**. OP.GG URL 4개 중 3개(`calculatedloss2`, `constructacompanion_iii`, `double-trouble-ii`)는 같은 파일명이 **있다** | 기존 + 파일명 매핑 3개 |
| CommunityDragon pbe | 같은 경로 `/pbe/` | latest와 파일 목록이 같다(1117) | 추가분 없음 |
| OP.GG | `c-tft-api.op.gg/img/set/18/tft-augment/*.png` | 개별 아이콘은 직접 받으면 403이다. OP.GG MCP 원본(`augments_ko.json`)에서도 missing 증강은 `Missing-T*.png`다 | 불가 |
| **tactics.tools** | `ap.tft.tools/img/augments/{apiName}{등급}.png` (사이트 번들 `_app-*.js`의 URL 규칙, ID는 `ap.tft.tools/static/s18/data.js`) | **49/49 받았다**(48 + ForgeAFriend). 256px webp, 게임과 같은 등급 색 글리프에 빛 번짐이 있다. 11개 묶음은 바이트가 같다(X/X+/X++ 변형) | **채택** |
| MetaTFT CDN | `cdn.metatft.com/file/metatft/augments/{apiname 소문자}.png` | 49/49 있지만 **단색(자홍) 네온 스타일**이다. 게임 칸과 점수가 0.27~0.42로 식별에 쓸 수 없다 | 조사만 |
| lolchess.gg | `lolchess.gg/guide/augments` | 404(클라이언트 렌더링), 이미지 목록을 얻지 못함 | 불가 |

부담 관리: 요청은 한 번에 하나씩 `--delay`(기본 0.5초) 간격으로 보낸다. 이미 받은 파일은 다시 받지 않는다(`sources.json`의 sha1 재사용). 전체 첫 수집은 약 40초다(CDragon 기존 317개 파일은 건너뜀).

### 1.2 커버리지 (18세트 증강 `set_native` 255개)

| 구분 | 개수 | 비고 |
|---|---|---|
| CDragon 그림 | 206 | 기존 203 + OP.GG URL 파일명 매핑 3 |
| tactics.tools 그림 | 49 | missing 48 + `DA_ForgeAFriend`(CDragon 404) |
| **그림 없음** | **0** | 이전: 51(missing 47 + 404 4, 07 보고서) |
| 그림은 있지만 이름이 다른 증강과 **같은 그림** | 61개 / 26그림 | 전체 목록 판독은 None(안전). **선택 순간 학습으로 확정 가능**(후보 안에서 유일하면) |

같은 그림 묶음의 예: 내면의 야수 / 내면의 야수+, 잔류 마법 / + / ++, 챔피언 배달 / + / ++, 추가 선물 / +, 수호령 환급 / +, 마트료시카 모루 / +, 가지 뻗기 / +(CDragon).

### 1.3 저장·인식 방식

- `data/templates/18/augments_alt/{apiName}.png`: 그림 38개(같은 바이트는 대표 1개). 저장 형태는 **글리프 외곽으로 정규화한 64px BGR**이다. `sources.json`에는 ID별 URL, sha1, 같은 그림 묶음 `group`을 둔다. gitignore와 README 출처 고지에 추가했다.
- **글리프 정규화 경로**(`icons.glyph_normalize`): tactics.tools 그림은 글리프가 프레임의 약 0.81을 차지한다. CDragon은 약 0.92, 게임 칸은 약 0.95다. 그래서 기존 고정 기하(±4px)로는 정답도 0.42~0.50밖에 나오지 않았다. 칸과 템플릿을 모두 "배경과 다른 픽셀의 외곽 정사각형"으로 맞춰 비교하면 정답은 0.83~0.86, 다른 증강의 최고점은 0.72 이하다(1080p 실캡처, §2 표).
- 대체 출처 그림은 `AugmentIconMatcher(glyph_templates=)`로 **CDragon이 없는 증강에만** 쓴다. tactics.tools 그림으로는 초월과 불완전한 초월이 0.829와 0.803으로 거의 같다. CDragon은 0.951과 0.878로 가른다. 그래서 CDragon이 있는 증강에는 대체 그림을 섞지 않는다.
- 수락 규칙: 1위가 글리프 경로에서 나왔으면 1·2위 차를 **0.10 이상**으로 요구한다(`AUGMENT_GLYPH_MARGIN`). CDragon 경로는 기존 0.05 그대로다. 점수 하한은 둘 다 0.80이다.
- 그림 동일성(`augment_visual_keys`): CDragon은 아이콘 경로 기준이다. OP.GG URL은 같은 파일명의 hexcore 경로와 같은 키로 본다. 대체 출처는 `group` 기준이다. 그림이 전혀 없는 증강은 자기 자신만 가진다. `_resolve_augment_icon`의 "이름이 다른 같은 그림이면 None" 규칙이 이제 missing 증강에도 맞게 동작한다. 예전에는 missing 증강들이 `missing-t2.tex` 하나를 공유해 전부 모호했다.

---

## 2. B. 5-5 가운데 증강 식별

### 2.1 칸 순서 = 선택 순서

2-2부터 2-7까지는 칸이 1개이고 어수선한 마음이다(4장). 5-1(Anvil)과 5-5는 칸이 3개이고 **왼쪽**이 어수선한 마음, 오른쪽이 초월이다. 즉 새 증강은 오른쪽에 붙는다. 그러므로 가운데는 2번째 선택(3-2), 오른쪽은 3번째 선택(4-2)이다. 칸이 2개인 캡처가 없어 가운데와 오른쪽 사이의 순서는 직접 보지 못했다. 오른쪽에 붙는 규칙과는 일치한다.

### 2.2 점수 (칸 `5-5 전투 전` / 사용자 크롭 `Screenshot 2026-09-22 225106.png`, 26px 저해상도)

| 칸 | 1위 | 2위(다른 그림) | 차 | 경로 | 판정 |
|---|---|---|---|---|---|
| 왼쪽 | 어수선한 마음 0.893 / 0.863 | Scapegoat(레거시) 0.644 / 0.674 | 0.25 / 0.19 | CDragon | 확정 |
| **가운데** | **내면의 야수 계열 0.844** / 0.798 (tactics 원본 256px로는 0.855) | 종결자 협곡야수 0.723 / 0.723 | **0.121** / 0.075 | 글리프 | 계열 확정, 이름 모호 |
| 오른쪽 | 초월 0.951 / 0.925 | 불완전한 초월 0.878 / 0.860 | 0.073 / 0.065 | CDragon | 확정 |

참고로 CDragon만 쓸 때 가운데 칸의 최고점은 DA_SpreadingRoots 0.530이었다(07 보고서의 0.53). 사용자 크롭은 해상도가 낮아(칸 26px) 가운데 칸 점수가 0.798로 임계 0.80 바로 아래다. 실제 1080p 칸(38px)은 0.844다.

- 18.3 세트 목록 안에서 가운데 칸의 **후보 상위 3**: ① 내면의 야수(`DA_18_PrimalAugment_Nidalee`/`_Sivir`), ② 내면의 야수+(`DA_18_PrimalAugmentPlus_Nidalee`/`_Sivir`) — ①과 같은 그림, ③ 종결자 협곡야수(`DA_18_RiftbeastTraitAugment`) 0.723.
- 네 변형 모두 tier 2(골드)이고, 칸의 금색 글리프와 맞는다. 18.3 MetaTFT 통계에도 네 변형이 모두 있다(덱 `primal-nidalee_ap` 등).
- **결론: None을 유지하고 라벨에 후보를 기록했다.** "내면의 야수"와 "내면의 야수+"는 이름이 다르다. 효과도 다르다(+는 바이·니달리/시비르를 즉시 줌). 그림으로는 둘을 가를 수 없다. `5-5 전투 전.expected.json`에 `_augments_owned_candidates`와 `_comment`를 기록했다. `augments_owned` 라벨은 넣지 않았다. 인식 결과 None이 정답이다. 템플릿은 등록하지 않았다. 한 이름으로 저장하면 다음 판에 다른 쪽을 오답으로 만든다.
- 실시간에서는 C의 학습으로 풀린다. 3-2 선택지에 둘 중 하나만 나왔다면 그 이름으로 확정된다(회귀 테스트 `test_raw_5_5_middle_cell_is_beast_within_family`: 후보 [내면의 야수(시비르), 종결자 협곡야수, 4단계 집중] → 내면의 야수로 확정, [야수, 야수+, …] → None).
- 사용자에게 물을 필요는 없다. 원하면 "내면의 야수"인지 "+"인지만 확인하면 라벨을 완성할 수 있다.

---

## 3. C. 선택 순간 자동 학습

### 3.1 흐름

```
증강 선택 화면(augment_select)                          준비 화면(planning, 보유 증강 줄 판독)
  recognized.augment_offer(3개 모두 인식된 때만)            Recognizer.last_owned_row = OwnedRow(cells, ids, scores)
  → SessionTracker.offer_pool에 추가(리롤 포함, 중복 제외)   → SessionTracker._learn_owned
  → 첫 후보일 때 offer_base = owned_count(선택 전 칸 수)      칸 수 n == offer_base      : 대기(아직 줄에 안 나타남)
  → 스테이지가 다른 새 라운드면 이전 목록은 버림            n == offer_base + 1      : 새 칸 = 오른쪽 끝
                                                            ├ vision이 이미 ID를 읽음 → 제시 목록과 같은 그림이면 기록, 아니면 학습 안 함
                                                            └ 못 읽음 → AugmentLearner.decide(칸, 후보)
                                                                 확정 → commit: 매처에 즉시 추가 + augments_screen/{api}.png 저장
                                                                        augments_owned = 이전 칸 ID들 + [api] (source="tracked")
                                                            그 밖(2칸 이상 늘어남·줄어듦) : 학습 안 함, 목록 버림
```

- vision은 여전히 프레임 단위로 무상태다. 판별(`vision/augment_learn.AugmentLearner.decide/commit`)은 vision에 있다. "언제 학습하나"와 영속은 app 세션(`SessionTracker`)이 맡는다.
- `Recognizer`가 칸 단위 결과를 `last_owned_row`로 내놓는다. `LiveLoop.step`은 그것을 `tracker.observe(..., owned_row=)`로 넘기고, `LiveLoop.__init__`은 `recognizer.augment_learner`를 `tracker.learner`로 연결한다. app 쪽 수정은 이 3줄뿐이다.
- 이전 칸 ID는 이번 프레임에서 vision이 읽은 칸 ID를 먼저 쓰고, 없으면 세션이 아는 목록(길이가 n-1일 때)을 쓴다. 하나라도 모르면 목록은 갱신하지 않는다. 그래도 템플릿은 저장한다.

### 3.2 오탐 방지 조건 (학습하지 않는 경우)

1. 제시 목록이 없다(증강 선택 화면을 못 봤거나 후보 3개를 다 읽지 못했다).
2. 선택 전 칸 수를 모른다(앱을 증강 선택 중에 켰다 → `offer_base=None`).
3. 새 칸이 2개 이상이거나 칸이 줄었다.
4. vision이 새 칸을 **제시되지 않은** 증강으로 읽었다(상대 보드 관전 등).
5. 같은 그림의 **다른 이름** 증강이 함께 제시됐다(예: 내면의 야수와 내면의 야수+). 반대로 공유 그림 증강도 제시 목록 안에서 유일하면 확정한다.
6. 점수·차이 조건 미달(§3.3). 이때는 목록을 유지하고 다음에 줄이 다시 읽힐 때 재시도한다. 새 라운드가 오면 목록은 버려진다.

### 3.3 임계값과 근거 (`vision/augment_learn.py`)

실측: 1080p 실캡처 17칸 + 사용자 크롭 3칸.
- 정답 점수: CDragon 경로 0.863~0.951, 글리프 경로 0.798~0.861.
- 다른 증강의 최고점: CDragon 경로에서 317개 중 0.644~0.674, 글리프 경로에서 49개 중 0.723~0.764(육각 특성 글리프끼리).

| 상수 | 값 | 근거 |
|---|---|---|
| `LEARN_MIN` | **0.70**(전체 목록 0.80보다 낮춤) | 정답이 반드시 후보 3개 안에 있고 **후보 모두 템플릿이 있을 때만** 쓴다. 전체 목록 임계 0.80의 이유는 "템플릿 없는 증강이 엉뚱한 템플릿과 0.7대로 맞는" 위험인데, 이 경우에는 그 위험이 없다. 정답 최저 0.798 > 0.70 |
| `LEARN_FULL_MIN` | 0.80 | 후보 중 템플릿이 없는 것이 있으면 전체 목록 임계를 그대로 쓴다(육각 특성 글리프 오답 0.723~0.764 차단) |
| `LEARN_MARGIN` | 0.05 | CDragon·실화면 경로의 후보 간 차이. 가장 가까운 실측 쌍: 초월 0.951 vs 불완전한 초월 0.878 = 0.073 |
| `LEARN_GLYPH_MARGIN` | 0.10 | 글리프 경로. 내면의 야수 0.844 vs 종결자 협곡야수 0.723 = 0.121. 정규화가 비슷한 모양끼리의 점수도 올리므로 더 크게 둔다 |
| `ELIMINATION_MAX` / `_CONF` | 0.60 / 0.60 | 소거법: 템플릿 없는 후보가 **정확히 1개**이고 템플릿 있는 후보가 모두 0.60 미만이면 그 1개로 확정하고, 신뢰도는 0.6으로 둔다. 다른 증강의 최고점(전체 317개 기준 0.674)보다 낮은 기준이라 후보 3개 안에서는 보수적이다. 지금은 세트 증강 전부에 그림이 있어 거의 쓰이지 않는다(정적 데이터에 없는 새 증강 대비) |

### 3.4 세션 영속·초기화 (app 병합 규칙과 맞춤)

- `SessionData` 새 필드 `offer_pool`, `offer_pool_stage`, `offer_base`, `owned_count`, `learned`(ID·스테이지·후보·근거·점수·템플릿 경로)를 `_state/session.json`에 저장한다. `SESSION_VERSION`은 1 그대로다. 필드가 없는 이전 파일도 읽힌다(테스트로 고정).
- 새 판(`loading`/`game_over` → `reset`)에서는 전부 비운다. **저장된 실화면 템플릿은 판과 무관하므로 남긴다.**
- `augments_owned` 병합 규칙은 바꾸지 않았다(`_CARRY_FIELDS`: vision 값이 있으면 쓰고 없으면 유지, `_apply_augments`: vision이 None이면 세션 값). 학습 결과는 `augments_source="tracked"`다.
- `GROUP_READ_MODES`는 바뀌지 않았다. 학습의 전제("augment" = 증강 선택 화면만, "owned" = 준비 화면만, `augment_offer`는 TRANSIENT)를 계약 테스트로 고정했다(`test_learning_contract_offer_is_augment_select_only_and_owned_is_planning_only`).

### 3.5 저장되는 템플릿

`data/templates/18/augments_screen/{apiName}.png`(gitignore). `augment_cell_template` 기하(칸 38px → 가운데 36px)라 자기 자신과 약 1.0으로 맞는다. 이미 있으면 덮어쓰지 않는다(처음 확정한 그림 유지). 같은 판 안에서도 매처에 바로 추가된다. 전체 목록으로 인식할 때도 `_resolve_augment_icon`의 그림 동일성 규칙이 그대로 적용된다. 그래서 "내면의 야수"로 저장된 칸이 다음 판에 나와도 이름이 모호하면 None이고, 다시 선택 순간 학습이 정한다.

---

## 4. D. 상점 잠금 버튼 (정정)

- 이전 보고서(05 §표, 07 §11-1)의 **"정체 불명 HUD 카운터 '6'"은 상점 잠금 버튼(열린 자물쇠 아이콘)이다**(사용자 확인 + 오케스트레이터 확대 확인). 1080p 원본 13장 중 HUD가 보이는 10장이 모두 같은 열린 자물쇠다. OCR이 자물쇠 모양을 "6"으로 읽은 것이다.
- 위치(1080p, 16:9): 버튼 안쪽(청록 테두리)은 x 0.779~0.813, y 0.820~0.850이다. 자물쇠 글리프는 x 0.792~0.799, y 0.827~0.844다. 상점 5번째 칸 위, 하단 HUD 오른쪽 끝에 있다.
- **판별은 넣지 않았다.** 잠긴 상태(닫힌 자물쇠) 캡처가 없어서 기준을 잡을 수 없고, contracts에도 필드가 없다.
- **계약 제안(app-integrator 경유)**: `GameState.shop_locked: bool | None`. 쓰임: 잠금 상태면 "이번 라운드 상점 유지"로 보고, advisor가 "잠금 풀고 새로고침" 같은 조언을 할 수 있다. 구현은 간단하다. 열린 자물쇠 템플릿과 비교해 ≥0.9면 False, 닫힌 자물쇠 템플릿과 ≥0.9면 True, 그 밖은 None. 사용자에게 **상점을 잠근 상태의 캡처 1장**만 받으면 된다.

---

## 5. E. 테스트

- 전체: `.venv/Scripts/python.exe -m pytest -o addopts="" -q` → **746 passed, 3 skipped, 0 failed**.
- 새 파일 `tests/test_augment_learn.py`(18개, 합성 글리프 사용, Riot 아트 없이 돈다):
  - 글리프 정규화 경로: 작은 글리프(대체 출처 형태)가 고정 기하로는 안 맞고 정규화로는 맞는다. 정규화의 크기·위치 불변성.
  - 그림 동일성 키: 대체 출처 묶음, 그림 모르는 missing 증강은 따로, OP.GG URL = CDragon 파일명. `sources.json`이 없거나 깨졌을 때.
  - `decide`: 후보 안 확정(임계 0.70), 제시되지 않은 그림은 거부, 같은 그림의 다른 이름이 함께 제시되면 거부, 하나만 제시되면 확정(Sivir ID로), 소거법(템플릿 없는 후보가 1개일 때만), 비슷한 두 그림이 함께 제시되면 차이 부족으로 거부.
  - `commit`: 저장, 이미 있으면 덮어쓰지 않음, 매처 즉시 반영.
  - 세션 흐름: 제시 → 리롤 → 대기 → 새 칸 → 확정·저장·`session.json` 기록 → 같은 판에서 전체 목록 인식. 오탐 방지: 제시 목록 없음, 새 칸 2개, 선택 전 칸 수 모름, vision이 제시되지 않은 ID로 읽음(+ 제시된 ID로 읽었을 때 "vision" 근거로 기록). 새 라운드에서 이전 목록 버림, 저장·복원, reset으로 비움.
  - `LiveLoop`가 인식기의 learner를 세션에 연결.
  - `fetch_augment_alts`(가짜 다운로드): 같은 그림 → 파일 1개와 `group`, manifest 파일 일치.
  - 인식기: 칸별 결과 `last_owned_row`(모르는 칸 None, 필드 전체 None), 학습 후 같은 판에서 필드 완성, 줄을 읽지 않은 프레임은 None.
  - 실캡처 회귀(파일·캐시 없으면 skip): 5-5 가운데 칸 = 내면의 야수 계열(글리프, ≥0.80, 차 ≥0.10), 필드 None, 후보 제약으로 확정/거부.
- `tests/app/test_session.py` +2: 학습 전제 계약(READ_MODES), 새 세션 필드 저장·복원, 이전 형식 파일 호환.
- 평가 수치(변경 후): 원본 13장은 전 필드 오답 0·미인식 0이다(augments_owned 4/4, 상점 칸 45/45). 방송 7장은 05 기준선과 같다(상점 칸 24/25, 틀린 ID 0).

---

## 6. 변경 파일

| 파일 | 변경 |
|---|---|
| `src/tft_advisor/vision/augment_learn.py` (신규) | `augment_visual_keys`, `load_alt_manifest`, `OwnedRow`, `AugmentLearner.decide/commit`, 임계값 상수 |
| `src/tft_advisor/vision/icons.py` | `IconMatch.via_glyph`(기본 False, 아이템 매처 무영향), `glyph_normalize`, `AugmentIconMatcher(glyph_templates=)`·`add`·`scores`·`from_dirs(glyph_dirs=)` |
| `src/tft_advisor/vision/recognizer.py` | 대체 출처 디렉터리 로드(기본 디렉터리일 때만), 그림 동일성 기반 `_augment_icon_owners`/`_resolve_augment_icon`, `AUGMENT_GLYPH_MARGIN`, `_read_augments_owned`가 칸별 결과를 `last_owned_row`로 남김(필드 규칙은 그대로: 한 칸이라도 모르면 None), `augment_learner`, `augment_alt_dir()` |
| `src/tft_advisor/vision/templates.py` | `fetch-augments` 확장(`--no-alt`, `--delay`), `fetch_augment_alts`(tactics.tools, 자리표시 검사, 같은 그림 묶음, `sources.json`), OP.GG URL → CDragon 파일명 매핑(404 수정), 요청 간격 |
| `src/tft_advisor/app/session.py` | 학습 필드 5개 + 영속, `observe(owned_row=)`, `_track_offer_pool`, `_learn_owned`, `learner` 속성, docstring 규칙 |
| `src/tft_advisor/app/loop.py` | learner 연결 1줄, `last_owned_row` 전달 2줄 |
| `.gitignore`, `data/templates/README.md` | `data/templates/*/augments_alt/`, 출처 고지(tactics.tools), `augments_screen/` 자동 학습 설명 |
| `tests/test_augment_learn.py` (신규), `tests/app/test_session.py` | §5 |
| `tests/fixtures/screens/raw/5-5 전투 전.expected.json` (gitignore) | `_comment`, `_augments_owned_candidates` |
| `data/templates/18/augments/`(+3, 320개), `augments_alt/`(38 + sources.json) | 이 PC 로컬 캐시(gitignore) |

QA 파일 `tests/test_qa08_vision_boundaries.py`는 건드리지 않았다. 그 테스트가 쓰는 비공개 함수 `_augment_icon_owners(static)` 호출 형태는 호환되게 유지했다(`keys` 인자 생략 가능). 편집 전후로 대상 파일을 다시 읽고 스냅샷과 대조해, 다른 에이전트 변경을 덮어쓰지 않았음을 확인했다. 줄바꿈도 파일별 원래 형식을 유지했다. 예외로 `icons.py`는 원래 CRLF와 LF가 섞여 있었는데 이번 편집으로 LF로 통일됐다(git autocrlf 정규화 대상이라 내용 diff에는 영향이 없다).

---

## 7. 전달

**app-integrator**
1. `SessionTracker.observe(recognized, groups, owned_row=None)`에 인자가 하나 늘었다(기본값이 있어 기존 호출 호환). `LiveLoop`가 `recognizer.last_owned_row`와 `recognizer.augment_learner`를 연결한다. `--screenshot` 경로는 학습하지 않는다(제시 → 선택 흐름이 없다).
2. QA A2(상대 보드 관전): 학습 경로에는 "제시된 후보 안에서만 + 새 칸 정확히 1개" 조건이 있어 영향이 작다. 하지만 **vision 전체 목록 값이 수동값을 덮는 기존 규칙**은 그대로다. A2의 "한 판 안에서는 추가만" 불변식을 넣을 때 `owned_count`/`learned`를 참고하면 된다.
3. 오버레이 표시 제안: 학습으로 확정된 증강에는 "(선택 확인)" 같은 표시를 달자. `data.learned[-1]["reason"]`이 "match"·"elimination"·"vision"이다.
4. 계약 제안: `shop_locked`(§4).

**jev-strategist**: `augments_owned`가 5스테이지에서 None으로 남는 일이 줄어든다. 학습 결과는 `field_source=tracked`, 신뢰도 1.0으로 들어간다(기존 `_apply_augments`). 소거법으로 정한 칸도 목록에서는 1.0이다. 칸별 신뢰도는 세션 `learned`에만 남는다. 필요하면 `AugmentRef.confidence`로 넘기도록 app과 조정하자.

**qa-validator**
- 새 경계: ① `vision.augment_learn.OwnedRow` ↔ `SessionTracker._learn_owned`, ② 그림 동일성 키 ↔ `_resolve_augment_icon`(missing 증강이 이제 모호로 한꺼번에 묶이지 않는다), ③ `augments_alt/sources.json` 형식.
- 실캡처 검증이 필요한 것: 1080p **증강 선택 화면**(3-2 또는 4-2)과 그 직후 준비 화면 한 쌍. 이것으로 실제 학습 한 번을 끝까지 재현할 수 있다(지금은 합성 데이터와 5-5 칸 + 가상 후보로만 검증).
- QA #8의 "81개 템플릿 없음"은 이제 0개다. 남은 것은 같은 그림을 쓰는 서로 다른 이름 61개(26그림)이고, 선택 순간 학습 대상이다.

**stats-researcher(정보)**: 정적 데이터 증강 `icon`에 OP.GG 전체 URL 4개가 섞여 있다(`DA_CalculatedLoss`, `DA_ConstructACompanion`, `DA_DoubleTrouble`, `DA_ForgeAFriend`). vision은 파일명으로 CDragon 경로에 매핑해 처리한다. 다음 정적 추출 때 CDragon 경로로 통일하면 좋다. `DA_ForgeAFriend`는 CDragon에 없다.

---

## 8. 남은 것

1. 내면의 야수 / 내면의 야수+ 구별: 그림으로는 불가능하다. 실시간에서는 선택 순간 학습으로 풀리고, 5-5 fixture는 사용자 한마디로 라벨을 완성할 수 있다(선택).
2. 1080p 증강 선택 화면 캡처: 학습 경로를 실제로 한 번 재현하고, 1080p 증강 이름 ROI를 확인한다(07 §11-2에서 이월).
3. 상점 잠금 상태 캡처 1장 → `shop_locked`(계약 합의 후).
4. 실버·프리즘 등급 글리프는 실캡처 표본이 아직 없다. tactics.tools 그림은 등급 색이 있어 색 비교에 유리할 것으로 본다(미검증).
