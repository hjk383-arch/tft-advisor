# 21 jev-strategist: 보드·벤치 따로 믿기 + 보드 배치 추천 + 상점만 다시 채점

작성일: 2026-09-23 / 작성자: jev-strategist / 커밋하지 않음
요청(사용자 결정 3건, 같은 라운드):
1. 보드 인식이 믿을 만하면 벤치가 못 미더워도 **보드 유닛을 추천에 쓴다** + 이름을 확신한 벤치 유닛도 더한다.
2. [목표 덱] 아래에 **지금 가진 유닛 중 무엇을 보드에 올릴지** 보여 준다([보드 배치]).
3. 상점이 바뀌면(라운드 시작·새로고침, **전투 중 포함**) 상점 추천을 새로 채운다 — advisor 쪽 API.

---

## 0. 요약

| 항목 | 결과 |
|---|---|
| 보드·벤치 판정 | 따로 한다(`unit_status.owned_units`). test.png: 보드 0.85 → 5기 전부, 벤치 0.38 → 이름 확인 4기만 반영, 이름 미상 5기는 세지 않음 |
| 부분 확인 정직성 | 미확인 칸이 있으면 `units_partial`: "부족"을 확정하지 않는 문구, 사본 수는 하한(`copies_owned_at_least`, "확인 보유 N"), `buy_makes_2star`는 true 또는 "unknown", 활성 특성은 보드를 전부 알 때만, 장착분은 유닛이 전부 확인될 때만 유닛에서 모음 |
| 최종 덱 규칙 | 그대로(아이템·증강 중심, 승률 타이브레이커). 보유 유닛은 설계대로 `wb=0.30` 항으로만 기여 — §3의 순위 변화 참고(튜닝 판단 필요) |
| 보드 배치 | `advisor/board_plan.py`(코드 전용, Jev 호출 없음), `Recommendation.board_plan` 추가(선택 필드), 콘솔 `[보드 배치]` |
| 상점 재채점 | `Advisor.rescore_shop(state, previous=None)` — 1.4~2.0ms(실제 통계), Jev 새 호출 없음(캐시 적중만) |
| 캐시 키 | 상점 내용이 Jev state → state_hash에 들어간다 — 상점만 바뀌어도 캐시 재사용 안 함(테스트로 고정) |
| 테스트 | 새 24개(`test_board_trust.py` 10, `test_board_plan.py` 9, `test_shop_rescore.py` 5 + 기존 1개 갱신). 전체 스위트: 알려진 Windows 실패 4건 + 동시 작업 중인 app-integrator의 `test_game_window.py` 2건(§8) |

---

## 1. 보드·벤치 판정 규칙 (`src/tft_advisor/unit_status.py` `owned_units()`)

advisor(`features.build_view`)와 표시 문구(`units_knowledge/units_reason/units_note`)가 **같은 함수**를 쓴다.

| 한쪽(보드 또는 벤치)의 필드 신뢰도 | 그쪽에서 쓰는 유닛 |
|---|---|
| ≥ `state_min_confidence`(0.6) | `UNKNOWN_UNIT_ID`가 아니고 유닛 신뢰도 ≥ 0.6 |
| < 0.6, 출처 vision/fixture | 같은 조건 — vision이 칸마다 매긴 이름 신뢰도(`UnitOnBoard.confidence` = `unit_conf`, `min(0.95, 0.6+여유)`)는 칸 단위로 믿을 수 있다 |
| < 0.6, 출처 tracked/manual | 쓰지 않는다 — 장부가 애매해 떨어진 신뢰도는 어느 유닛 탓인지 모르고, 장부 유닛 신뢰도는 대개 1.0이라 걸러지지 않는다 |

- **유닛 단위 신뢰도는 이미 있다**(`UnitOnBoard.confidence`). 이름 미상 칸은 `unit_merge.UNKNOWN_CONFIDENCE`=0.2라 두 조건 모두로 빠진다. contracts 변경 불필요.
- 한계: 출처가 **필드 단위**(`field_source["bench"]`)라, vision 이름과 장부 이름이 섞이면(출처 `tracked`) 신뢰도가 낮은 쪽의 vision 이름도 버린다(보수적). 유닛별 출처가 필요하면 `UnitOnBoard.source: FieldSource | None = None`(선택 필드)을 app-integrator에게 제안한다 — 이번에는 만들지 않았다.
- 보드가 신뢰 불가(예 0.27)이고 출처가 vision이면 **벤치와 같은 규칙**(이름 확인 유닛만)을 적용했다. 사용자 결정은 "보드가 믿을 만하면 보드 + 이름 확인 벤치"였고, 보드가 못 미더울 때는 정해지지 않아 대칭 규칙으로 했다. 이 경우 보드를 전부 알지 못하므로 활성 특성은 계산하지 않는다.

`View` 새 필드(`advisor/features.py`): `units_known`(조금이라도 쓴다), `units_complete`(미확인 칸 없음), `board_complete`, `owned: OwnedUnits`(미확인 칸 수), `units_partial` 속성.

## 2. 부분 확인일 때 advisor가 하는 일 / 하지 않는 일

| 곳 | 완전히 앎 | 부분 확인(미확인 칸 있음) |
|---|---|---|
| `TargetComp.owned_units` | 확인된 유닛 기준 | 같음(이름 미상은 어떤 챔피언으로도 세지 않음) |
| `TargetComp.missing_units` | 확정 | "확인된 유닛 중에 없음" — 근거·보유 줄에 "부족 중 일부는 미확인 칸에 있을 수 있습니다" |
| 보드 근거 | "보유 유닛 n/m기 (핵심 k)" | "확인된 보유 유닛 n/m기 (핵심 k)" |
| Jev state `board`/`bench` | 전부 | 확인된 유닛만 + `unidentified_units: {board, bench, note}` (note: 목록에 없어도 보유했을 수 있다) |
| Jev state `active_traits` | 있음 | 보드를 전부 알 때만(미확인 보드 칸이 있으면 인원이 모자라게 나온다) |
| 상점 `copies_owned` / `buy_makes_2star` | 값 / true·false | `copies_owned_at_least`(하한) / true 또는 `"unknown"`(2성 불가는 증명 못 함) |
| 2성/3성 보너스 | 적용 | 확인된 사본으로 **증명될 때만** 적용(3개면 자동 합성되므로 확인 1성 2기면 참이다) |
| 상점 근거 | "보유 N" | "확인 보유 N" |
| 장착 아이템(보유 완성템 풀) | 유닛에서 모을 수 있음 | 유닛에서 모으지 않음(vision 판독 `items.equipped` 또는 세션 추적) — 미확인 칸의 아이템이 빠지는 것을 막는다 |

S1 질문 문구는 그대로다(`buy_makes_2star is true` 참조 — "unknown"은 참이 아니다). `QUESTIONS_VERSION` 유지(q2): 문구가 아니라 state 내용만 바뀌었고, 캐시 키는 state 해시라 자동으로 갈린다.

## 3. test.png 전/후 (`--screenshot tests/fixtures/screens/test/test.png --no-jev`)

`config/settings.toml`이 작업 중에 바뀌었다(사용자/app-integrator의 창모드 `content_box`, 21:9 등 — 내 변경 아님, 되돌리지 않음). 그대로 쓰면 이 이미 잘린 캡처에 content_box가 적용돼 "알 수 없음" 화면이 된다.
그래서 두 실행 모두 `--config <임시 디렉터리>`(HEAD의 `settings.toml` + 현재 `weights.toml`)로 돌렸다.

### 전(HEAD 규칙: 보드·벤치 둘 다 ≥ 0.6이어야 사용)
```
# TFT Advisor — 스크린샷 모드 · 이미지 1장 · Jev off · 패치 18.3

=== test.png  4480x1440  (인식 576ms · 추천 4ms) ===
준비  스테이지 2-6  레벨 5 (3/20)  골드 31  4연패  체력 76
상점 확률: 45/33/20/2/0
보드 5기: 아칼리 (0,0) · 카밀 (0,1) · 엘리스 (0,2) · 코그모 (2,0) · 카시오페아 (3,0)
벤치 9기: 1 이름 미상 · 2 이름 미상 · 3 자야 · 4 아칼리 · 5 이름 미상 · 6 이름 미상 · 7 이름 미상 · 8 자야 · 9 카밀

[목표 덱] advisor 순서 (점수로 재정렬하지 않음) — Jev 미사용
  1. 적응가 마스터 이 렝가  적합도 0.65  캐리 마스터 이  운영 lvl 7
     보유/부족: 부분 확인 — 화면 인식 9기 · 이름 미상 5기(추천에는 쓰지 않습니다)
     아이템: 지옥불 손도끼(부족) / 밤의 끝자락(조합가능) / 구인수의 격노검(부족)
     다음 빌드업(레벨 6): 마스터 이, 렝가, 요릭, 카르마, 코그모, 바이
     근거: 핵심 아이템: 밤의 끝자락 → 마스터 이 · 증강 시너지: 어수선한 마음 · 메타 평균 4.43등 · 6,829판
  2. 처형자 카직스  적합도 0.62  캐리 카직스  운영 lvl 7
     보유/부족: 부분 확인 — 화면 인식 9기 · 이름 미상 5기(추천에는 쓰지 않습니다)
     아이템: 리치베인(부족) / 밤의 끝자락(조합가능) / 정의의 손길(부족)
     다음 빌드업(레벨 6): 다이애나, 헤카림, 카직스, 르블랑, 오른, 피들스틱
     근거: 증강 시너지: 어수선한 마음 · 메타 평균 4.16등 · 46,124판 · 보유 유닛 부분 확인: 화면에서 이름을 확인한 유닛 9기, 이름 미상 5기 — 수동 확인을 권합니다
  3. 처형자 드레이븐  적합도 0.61  캐리 드레이븐  운영 Fast 9
     보유/부족: 부분 확인 — 화면 인식 9기 · 이름 미상 5기(추천에는 쓰지 않습니다)
     아이템: 처형자 상징(부족) / 구인수의 격노검(부족) / 크라켄의 분노(부족)
     다음 빌드업(레벨 6): 아칼리, 알리스타, 오른, 쉔, 바루스, 자야
     근거: 보유 아이템 적합 · 증강 시너지: 어수선한 마음 · 메타 평균 4.46등 · 50,323판

[상점]
  1. [보류] 자야 0.41 · 빌드업 — 지금 0.15 · 경로 1.00
  2. [보류] 불타는 묘목 0.06 · 지금 전력 — 지금 0.09 · 경로 0.00
  3. (빈 칸)
  4. [보류] 레오나 0.45 · 최종 덱 — 지금 0.30 · 경로 0.80
  5. [보류] 요릭 0.39 · 최종 덱 — 지금 0.13 · 경로 1.00

[아이템]
  · 밤의 끝자락 (B.F. 대검 + 쇠사슬 조끼) → 마스터 이 0.93 — 마스터 이 핵심 아이템
  · 내셔의 이빨 (거인의 허리띠 + 곡궁) 0.07 — 범용

[재료 우선순위] 쓸데없이 큰 지팡이, 연습용 장갑, 여신의 눈물, 프라이팬, 음전자 망토
```

### 후(보드·벤치 따로 + 보드 배치)
```
# TFT Advisor — 스크린샷 모드 · 이미지 1장 · Jev off · 패치 18.3

=== test.png  4480x1440  (인식 552ms · 추천 5ms) ===
준비  스테이지 2-6  레벨 5 (3/20)  골드 31  4연패  체력 76
상점 확률: 45/33/20/2/0
보드 5기: 아칼리 (0,0) · 카밀 (0,1) · 엘리스 (0,2) · 코그모 (2,0) · 카시오페아 (3,0)
벤치 9기: 1 이름 미상 · 2 이름 미상 · 3 자야 · 4 아칼리 · 5 이름 미상 · 6 이름 미상 · 7 이름 미상 · 8 자야 · 9 카밀

[목표 덱] advisor 순서 (점수로 재정렬하지 않음) — Jev 미사용
  1. 지옥불 아칼리  적합도 0.53  캐리 아칼리  운영 lvl 5
     보유 2: 아칼리, 카밀 (부분 확인 — 화면 인식 9기 반영 · 이름 미상 5기 · 부족 중 일부는 미확인 칸에 있을 수 있습니다)
     부족 5: 케일, 세주아니, 레오나, 오른, 바루스
     아이템: 고속 연사포(부족) / 정의의 손길(부족) / 무한의 대검(부족)
     다음 빌드업(레벨 6): 아칼리, 카밀, 케일, 레오나, 오른, 바루스
     근거: 증강 시너지: 어수선한 마음 · 확인된 보유 유닛 2/7기 (핵심 2) · 메타 평균 4.64등 · 17,488판
  2. 적응가 마스터 이 렝가  적합도 0.51  캐리 마스터 이  운영 lvl 7
     보유 0: - (부분 확인 — 화면 인식 9기 반영 · 이름 미상 5기 · 부족 중 일부는 미확인 칸에 있을 수 있습니다)
     부족 7: 마스터 이, 세트, 니달리, 렝가, 돌거북, 바이 외 1
     아이템: 지옥불 손도끼(부족) / 밤의 끝자락(조합가능) / 구인수의 격노검(부족)
     다음 빌드업(레벨 6): 마스터 이, 렝가, 요릭, 카르마, 코그모, 바이
     근거: 핵심 아이템: 밤의 끝자락 → 마스터 이 · 증강 시너지: 어수선한 마음 · 메타 평균 4.43등 · 6,829판
  3. 처형자 드레이븐  적합도 0.50  캐리 드레이븐  운영 Fast 9
     보유 0: - (부분 확인 — 화면 인식 9기 반영 · 이름 미상 5기 · 부족 중 일부는 미확인 칸에 있을 수 있습니다)
     부족 9: 드레이븐, 나르, 케넨, 마오카이, 이즈리얼, 아무무 외 3
     아이템: 처형자 상징(부족) / 구인수의 격노검(부족) / 크라켄의 분노(부족)
     다음 빌드업(레벨 6): 아칼리, 알리스타, 오른, 쉔, 바루스, 자야
     근거: 보유 아이템 적합 · 증강 시너지: 어수선한 마음 · 메타 평균 4.46등 · 50,323판

[보드 배치]
  기준 지옥불 아칼리 · 칸 5
  보드: 아칼리(목표 덱 캐리) · 카밀(목표 덱 핵심 · 약탈자 1→2 활성) · 코그모(적응가 1→2 활성) · 카시오페아 · 엘리스(악의 여단 2→3 활성)
  교체: 없음(지금 배치를 유지하세요)
  벤치: 자야 · 자야 · 아칼리 · 카밀
  참고: 벤치 미확인 5기는 판단하지 않았습니다 — 강한 유닛이면 직접 올려 주세요

[상점]
  1. [구매] 자야 0.53 · 빌드업 — 지금 0.15 · 경로 0.90 · 확인 보유 2
  2. [보류] 불타는 묘목 0.26 · 최종 덱 — 지금 0.09 · 경로 0.64 · 확인 보유 0
  3. (빈 칸)
  4. [구매] 레오나 0.51 · 최종 덱 — 지금 0.30 · 경로 1.00 · 확인 보유 0
  5. [보류] 요릭 0.38 · 최종 덱 — 지금 0.13 · 경로 0.97 · 확인 보유 0

[아이템]
  · 스테락의 도전 (B.F. 대검 + 거인의 허리띠) → 카밀 0.90 — 카밀 핵심 아이템
```

### 무엇이 바뀌었나
- **보유 유닛 반영**: 보드 5기(아칼리·카밀·엘리스·코그모·카시오페아)와 이름을 확인한 벤치 4기(자야×2·아칼리·카밀)를 합쳐 9기다. 이름 미상 5기는 세지 않는다. 표시 문구는 "(추천에는 쓰지 않습니다)"에서 "화면 인식 9기 반영 · 이름 미상 5기 · 부족 중 일부는 미확인 칸에 있을 수 있습니다"로 바뀌었다.
- **목표 덱 순위**: 1위가 적응가 마스터 이(0.65)에서 **지옥불 아칼리(0.53)**로 바뀌었다. 보유 유닛 항(`wb=0.30`)이 새로 켜지면서 아칼리·카밀(둘 다 핵심, 보드와 벤치에 2기씩)을 가진 덱이 올라갔다. 가중합을 정규화하므로 유닛이 없는 덱의 점수는 함께 내려갔다(0.65→0.51). 마스터 이 덱은 캐리 BIS인 밤의 끝자락을 조합할 수 있어 아이템 항이 있다. 그러나 통계 전용 프록시에서는 1성 핵심 2기(U=0.5 x 0.30 = 0.15)가 조합 가능한 BIS 1개(I=0.25 x 0.45 = 0.11)보다 크다. 설계(`wi/wa/wb` = 0.45/0.25/0.30)대로 나온 결과다. 하지만 **2스테이지에서 1성 유닛 겹침이 조합 가능한 캐리 BIS를 이기는 것이 사용자 의도("최종 덱=보유 아이템·증강 기반")와 맞는지는 판단이 필요하다**. 가중치는 바꾸지 않았다. 조정 후보는 세 가지다. (a) `prefilter.unit_saturation` 4→6. (b) 스테이지 2에서 `wb` 축소. (c) 1성은 `unit_w_*`의 절반. Jev가 켜져 있으면 `comp_board_fit`(C3)이 판단한다. 이번 실행에서 질문 수는 26개에서 34개로 늘었다.
- **상점**: 자야는 확인된 1성이 2기라 사면 2성이 되는 것이 **증명**된다. 그래서 2성 보너스(0.15)를 받아 0.41 보류에서 0.53 구매로 바뀌었다. 레오나는 1위 덱 소속이라 0.45에서 0.51 구매로 바뀌었다. 근거에 "확인 보유 N"이 붙는다.
- **아이템**: 1위 덱이 바뀌어 스테락의 도전 → 카밀(0.90)이 1순위가 되었다.
- **[보드 배치]**: 지금 보드 5기가 이미 최선이라 교체가 없다. 카밀은 약탈자 1→2, 코그모는 적응가 1→2, 엘리스는 악의 여단 2→3을 켠다(특성 패널과 같다). 벤치 미확인 5기는 판단하지 않았다고 알린다.

참고: 이 보고서를 쓰는 동안 다른 에이전트가 vision 쪽 `vision/units.py`를 동시에 바꿨다. 그래서 마지막 실행에서는 벤치 이름이 2기(아칼리·카밀)에만 붙고 벤치 신뢰도가 0.19가 되었다. 이 경우 자야는 "확인 보유 0"이 되고, 2성 보너스 없이 **보류(0.37)**로 돌아간다. 확인하지 못한 사본을 세지 않는 규칙이 그대로 동작한 것이다. 위 "후" 출력은 이번 라운드를 시작할 때의 vision 코드 기준이다.

---

## 4. 바뀐 파일

| 파일 | 변경 |
|---|---|
| `src/tft_advisor/unit_status.py` | `OwnedUnits`, `owned_units()`(보드·벤치 따로 판정), `units_knowledge/units_reason/units_note`가 같은 규칙을 쓰고 부분 반영 문구를 낸다 |
| `src/tft_advisor/advisor/features.py` | `View.units_complete/board_complete/owned/units_partial`, `build_view`가 `owned_units`를 쓴다. 장착분은 완전할 때만 유닛에서 모은다 |
| `src/tft_advisor/advisor/jev_state.py` | `unidentified_units`, 부분 확인일 때 `active_traits`/`copies_owned_at_least`/`buy_makes_2star` 처리 |
| `src/tft_advisor/advisor/scoring.py` | "확인된 보유 유닛", "확인 보유 N" 문구 |
| `src/tft_advisor/advisor/engine.py` | 장착 추적 조건(`units_complete`), `_board_plan`, `rescore_shop` |
| `src/tft_advisor/advisor/board_plan.py`(새 파일) | 보드 배치 추천 |
| `src/tft_advisor/contracts.py` | `BoardPlanEntry`/`BoardSwap`/`BoardPlan`, `Recommendation.board_plan`(선택 필드) |
| `src/tft_advisor/app/report.py` | `board_plan_lines`, `[보드 배치]` 블록(같은 파일에 app-integrator의 상점 재평가 변경도 함께 있다) |
| `_workspace/02_jev-strategist_design.md` | §4.3(b) 개정 문단 |
| 테스트 | §8 |

vision/, app/recog_*, overlay.py, setup_dialog.py, loop.py, config.py, config/*.toml은 건드리지 않았다.

## 5. 지연 · Jev 영향
- 보드 유닛이 쓰이면 `comp_board_fit_k`(C3) 질문이 덱 후보마다 붙는다(test.png 26개 → 34개). 한 요청 안에 묶이므로 왕복 수는 같고, 입력 토큰은 늘어난다(보드/벤치 목록 + 8문항).
- 보드 배치는 코드 계산이라 Jev 비용이 없다. `rescore_shop`은 1.4~2.0ms이고 Jev를 부르지 않는다.
- 통계 전용 추천 전체: 3~6ms(변화 없음).

## 6. 보드 배치 추천 (`src/tft_advisor/advisor/board_plan.py`)

### 6.1 입력 · 출력
- 입력:
  - `View`: 확인된 보드/벤치 유닛, 성급, 장착 아이템, 미확인 칸 수, 레벨
  - 1위 목표 덱(`CompStats`): carry, is_core, final_board, key_traits
  - 그 덱의 현재·다음 레벨 빌드업 보드(`board_at`)
  - 통계 `s_now_table`: 현재 레벨 빌드업 보드에 그 유닛이 나오는 비중
  - 정적 특성표(구간)와 장착 상징(특성 추가)
- 출력: `Recommendation.board_plan: BoardPlan | None`. Jev를 부르지 않으므로 추가 비용이 없다. 추천 1회 안에서 수백 μs가 걸린다.

### 6.2 점수(결정적 탐욕)
칸 수는 레벨이다. 보드 인원이 레벨보다 많으면 그 인원을 쓰고, 레벨을 모르면 현재 보드 인원을 쓰며 안내를 붙인다. 열린 칸 = 칸 수 − 미확인 보드 유닛 수.
매 단계마다 한계 점수가 가장 큰 유닛을 고른다.

| 항 | 값 | 근거 문구 |
|---|---|---|
| 목표 덱 캐리 / 핵심 / 최종 보드 / 빌드업(현재·다음 레벨) | 3.0 / 2.0 / 1.2 / 0.8 (하나만) | "목표 덱 캐리" / "목표 덱 핵심" / "목표 덱 유닛" / "빌드업 유닛" |
| 성급 2 / 3 | 1.5 / 3.5 | "2성" / "3성" |
| 장착 아이템 | 개당 0.7 | "아이템 보유자" / "아이템 N개" |
| 코스트 | 코스트 x 0.3 | - |
| 통계 s_now | x 1.0 | ≥ 0.5면 "현 레벨 통계 상위" |
| 특성 구간 도달(지금 라인업 기준) | 1.0(목표 덱 핵심 특성이면 x1.5) | "{특성} n→n+1 활성" |
| 구간은 아니지만 진행 | 0.25(x1.5) | - |
| 고유 특성(구간 [1]) | 0.2 | - |
| 이미 보드에 있음 | +0.5 (작은 차이로 교체를 권하지 않는다) | - |
| 같은 챔피언이 이미 라인업 | −2.5, 목표 덱 소속 점수도 빼고 특성 이득 없음 | 벤치 근거 "같은 챔피언이 보드에 있습니다(합성 대기)" |

- 빈 칸이 남으면 점수가 낮은 유닛이라도 올린다(빈 칸보다 낫다). 유닛이 모자라면 `free_slots`를 채우고 "상점에서 유닛을 사서 채우세요"라고 안내한다.
- 교체: 올릴 유닛(점수 높은 순)과 내릴 보드 유닛(점수 낮은 순)을 짝짓는다. 짝이 없는 올릴 유닛은 "빈 칸에 X 올리기"가 된다.
- **부분 확인**:
  - 미확인 보드 유닛은 자리를 차지한 채 "그대로" 둔다(내리라고 하지 않는다).
  - 미확인 벤치 유닛은 판단하지 않고 안내만 한다.
  - 이름 미상 유닛은 계획 어디에도 챔피언으로 나오지 않는다.
- 앞/뒤 라인 균형: 정적 데이터의 `role`이 전부 None이라 쓰지 않는다. 데이터가 생기면 항을 추가한다.
- 상수는 `BoardPlanWeights`(모듈 상수)에 있다. **`config/weights.toml [board_plan]`으로 옮길 후보**다. 이번에는 app-integrator가 `config.py`를 동시에 수정하고 있어 건드리지 않았다.

### 6.3 계약 추가(app-integrator 참고, 선택 필드 · 기존 호환)
`src/tft_advisor/contracts.py`에 추가했다. 기존 필드는 바꾸지 않았다.
```python
class BoardPlanEntry(ContractModel):
    unit_id: ChampionId
    star: Star | None = None
    on_board: bool                                   # 지금 보드에 있다
    action: Literal["keep", "field", "bench", "stay"] # 보드 유지 / 벤치→보드 / 보드→벤치 / 벤치 유지
    score: float = 0.0
    reason: str | None = None                        # 예 "목표 덱 핵심 · 적응가 1→2 활성"

class BoardSwap(ContractModel):
    field_unit_id: ChampionId                        # 벤치에서 올릴 유닛
    bench_unit_id: ChampionId | None = None          # 보드에서 내릴 유닛(None = 빈 칸에 올린다)

class BoardPlan(ContractModel):
    comp_id: str | None = None                       # 기준 목표 덱(= target_comps[0].comp_id)
    slots: int | None = None                         # 보드 칸 수
    lineup: list[BoardPlanEntry]                     # 보드에 둘 유닛(선정 순)
    bench: list[BoardPlanEntry]                      # 벤치에 둘 유닛(점수 높은 순)
    swaps: list[BoardSwap]
    free_slots: int = 0
    unknown_on_board: int = 0                        # 미확인(이름 미상·낮은 신뢰도) 보드 유닛 — 자리만 차지
    unknown_on_bench: int = 0
    notes: list[str]                                 # 합쇼체 한계 안내

class Recommendation(...):
    board_plan: BoardPlan | None = None              # 없으면 표시하지 않는다
```
- 오버레이 렌더링: `app/report.py`의 순수 함수 `board_plan_lines(plan, names, comp_name=None) -> list[str]`를 그대로 쓰면 콘솔과 문구가 같아진다. 줄 구성은 다음과 같다.
  - "기준 … · 칸 N · 빈 칸 K"
  - "보드: …"
  - "교체: 벤치 X ↔ 보드 Y / 빈 칸에 X 올리기" 또는 "교체: 없음(지금 배치를 유지하세요)"
  - "벤치: …"
  - "참고: …"
  
  오버레이가 좁으면 "교체:" 줄만 보여 줘도 핵심은 전달된다.
- 콘솔(`format_report`): `[목표 덱]` 블록 바로 아래에 `[보드 배치]`를 둔다. overlay.py / recog_window.py / setup_dialog.py는 건드리지 않았다.
- `rescore_shop`은 `board_plan`을 바꾸지 않는다(직전 값 유지).

## 7. 상점만 다시 채점 — `Advisor.rescore_shop` (app-integrator가 loop.py에서 호출)

```python
def rescore_shop(self, state: GameState, previous: Recommendation | None = None) -> Recommendation | None
```
- **언제 부르나**: 상점 카드가 직전 추천이 모르는 것으로 바뀌었을 때다. 라운드 시작 새 상점, 새로고침이 해당하며 **전투·아이템 선택 화면도 포함**한다. 동기 함수라 이벤트 루프가 필요 없다. 실제 통계로 **1.4~2.0ms**가 걸린다(s03/s04 fixture, 5회 측정).
- **무엇을 바꾸나**: `shop`만 바꾼다.
  - `target_comps`·`board_plan`·`item`·`augment`·`component_priority`·`jev_used`·`fallback_reason`은 `previous`(생략하면 세션의 직전 추천) 그대로다.
  - `latency_ms`/`created_at`는 이번 값이다.
  - `debug["shop_rescore"] = {mode, state_hash, jev_cached, ms, shop_jev_state}`를 남긴다.
- **채점 방식**:
  - 새 View를 만든다(골드·레벨·보유 유닛·체력 반영).
  - 1차 필터에 직전 표시 덱을 `prev_shown`으로 넘긴다.
  - 상점은 코드·통계로 채점한다(`Scorer.shop_advice`: S_now·C_path·2성 보너스·골드 누적 구매 컷).
  - 경로 가중(`rel`)은 **직전 요청의 덱 점수**(`debug["candidates"][].final`)를 그대로 쓴다. 그래서 목표 덱이 흔들리지 않는다.
- **Jev**: 새로 호출하지 않는다. 이 상태로 만든 요청(planning과 같은 state 구성)이 게이트웨이 캐시에 **이미 있을 때만** 그 답(상점 Jev 판단 포함)을 쓴다(`jev_cached=True`). 전투 중 새 카드는 보통 캐시에 없으므로 통계로 채점한다. 다음 준비 단계의 전체 추천이 Jev 판단으로 다시 채운다.
- **세션**: 결과를 `session.last`로 저장한다. 그래서 이후 전투 화면의 `recommend()`(직전 추천 반환)도 새 상점을 돌려준다.
- **반환**: 직전 추천이 없으면 `None`이다. 이때 loop는 다음 준비 단계의 전체 추천을 기다린다. 상점 신뢰도가 미달이면 `previous`를 그대로 돌려준다.
- **준비 단계**: 상점만 바뀌면 지금처럼 `recommend()`(전체 추천)를 부르면 된다. **상점 내용은 Jev state의 `shop`을 거쳐 `state_hash`에 들어가므로** 캐시가 옛 상점 답을 돌려주지 않는다. `test_shop_is_part_of_the_cache_key`가 이를 고정한다: 상점만 다른 두 상태는 해시가 다르고 mock 호출이 2회이며, 같은 상태를 다시 요청하면 캐시를 쓴다. 로그의 `cached=True`는 같은 상태의 프레임이 반복된 경우다. Jev live 비용을 아끼려면 준비 단계에서도 먼저 `rescore_shop`으로 바로 표시하고 전체 추천으로 덮을 수 있다(선택).
- 호출 예(loop.py, app-integrator 소유):
  ```python
  if shop_needs_rescore(last_rec, state):          # 새 상품이 보이면(라운드 시작·새로고침)
      rec = advisor.rescore_shop(state, last_rec) or last_rec
  ```

## 8. 테스트

| 파일 | 내용 |
|---|---|
| `tests/advisor/test_board_trust.py`(10) | 보드 신뢰+벤치 불신(이름 확인 벤치만, 이름 미상·신뢰도 0.5 제외) / 둘 다 신뢰(완전) / 둘 다 신뢰지만 미상 1칸(부분) / 보드 불신 vision(이름 확인만, 특성 계산 안 함) / 불신 tracked(아무것도 안 씀, 보드만 믿으면 보드만) / 부분 확인이면 유닛에서 장착분 안 모음 / 문구 합쇼체 · "벤치 미인식" / 엔진 끝까지 3종(보드 신뢰+벤치 불신, 둘 다 신뢰, 보드 불신) — `unidentified_units`, `copies_owned_at_least`, `buy_makes_2star` "unknown", "확인 보유 N", "미확인 칸" 근거 |
| `tests/advisor/test_board_plan.py`(9) | 레벨 칸 제한 + 캐리 교체 / 이미 최선이면 교체 없음 / 빈 칸 보고·채우기 / 부분 확인(미상은 자리만, 이름 없음, 안내) / 중복 챔피언은 벤치(합성 대기) / 성급·아이템 우선 / 레벨 미인식 / 보드 없음·이름 전무 → None / 엔진·리포트 연결(`[목표 덱]` < `[보드 배치]` < `[상점]`) |
| `tests/advisor/test_shop_rescore.py`(5) | 상점이 캐시 키에 들어감 / 전투 중 재채점: Jev 호출 0, < 100ms, 나머지 필드 불변, 이후 combat `recommend()`가 새 상점 / Jev 끈 전체 추천과 같은 상점 결과 / 같은 상태면 캐시 답 사용 / 직전 추천 없음·상점 미인식 |
| `tests/advisor/test_advisor_units.py` | `test_view_reliability_filter`: 사용자 결정에 따라 "보드만 있고 벤치 None → 모름"을 "보드는 쓰되 부분 확인"으로 갱신 |

전체 스위트는 **프로젝트 사본 + HEAD `settings.toml`**에서 돌렸다(`PYTHONPATH=사본/src`). 사용자 설정 파일과 동시 작업의 영향을 피하기 위해서다.
실패는 두 종류다.
- 알려진 Windows 실패 4건: `test_api_key` 상태 힌트, `test_setup` 권한 상자, `test_credentials` 0600 두 건.
- `tests/app/test_game_window.py` 2건: app-integrator가 이 라운드에 새로 만드는 파일(`app/game_window.py`, 미추적)의 테스트다. HEAD `settings.toml`에 그쪽 새 키가 없어서 실패한다(내 변경과 무관).

실제 작업 폴더에서는 바뀐 `config/settings.toml`(monitor=1, jev_backend=live, 3440x1432, content_box) 때문에 설정 기본값 테스트 10여 건이 더 실패한다. 이것도 내 변경과 무관하다.

## 9. 요청 사항 · 남은 일
- **app-integrator**:
  1. 오버레이에 `board_plan`을 렌더링한다(`board_plan_lines` 재사용 권장).
  2. loop.py에서 상점이 바뀌면 `rescore_shop`을 호출한다(§7).
  3. 선택: `UnitOnBoard.source`(유닛별 출처)를 추가하면 섞인 출처에서도 vision 이름을 살릴 수 있다.
  4. `BoardPlanWeights`를 `weights.toml [board_plan]`으로 옮길 때 `config.py`에 모델을 추가한다.
- **사용자 판단**: §3의 목표 덱 순위 변화(1성 겹침 vs 조합 가능 캐리 BIS)가 의도와 맞는지 확인이 필요하다. 맞지 않으면 `unit_saturation`/`wb`/1성 할인 중 하나로 조정한다.
- **vision-engineer**: 벤치 이름 커버리지가 곧 추천 품질이다. 자야 2기를 알아보면 2성 구매 추천이 켜진다.

## 10. 초반 유닛 = 빌드업: 목표 덱 선정에서 스테이지별 유닛 가중 (2026-09-24)

사용자 결정(§9 질문에 대한 답): **"2~3스테이지 유닛은 빌드업이다. 지나가는 유닛이다."** 그래서 초반 보유 유닛은 목표 덱 선정을 끌지 않는다. CLAUDE.md 규칙(최종 덱 = 보유 아이템·증강, 승률은 타이브레이커)과 맞춘다.

### 10.1 무엇이 바뀌나
- 적용 범위는 **목표 덱 선정에만** 한정한다.
  - Scorer 보드 항 `wb`와 1차 필터 `w_unit`에 `deck_board_scale(스테이지)`를 곱한다.
  - 줄어든 몫은 아이템·증강 항에 wi:wa(w_item:w_aug) 비율로 옮긴다(`redistribute`, 합 = 1 유지).
  - U 프록시에서 1성 유닛 1기의 기여는 `deck_one_star(스테이지)`배다.
  - 아이템을 든 1성은 최소 `deck_item_holder`(0.8)배, 2성 이상은 할인 없이 x `unit_star_mult`다.
- 적용하지 않는 곳은 두 군데다.
  - **상점**: 2성·3성 사본 보너스와 "보유 N"은 그대로다.
  - **보드 배치**: 유닛을 그대로 쓴다(§10.3). 둘 다 지금 라운드 문제이기 때문이다.
- Jev: C3(`comp_board_fit`)·C4(`comp_pick`) 문구에 "2~3스테이지 1성은 약한 증거, 2성·아이템 보유자 위주" 한 문장을 더했다. `QUESTIONS_VERSION` q2 → **q3**.
- debug에 `unit_stage = {board_scale, one_star, item_holder, weights{item,augment,board,tempo}}`를 남긴다.

### 10.2 스케줄 (`config/weights.toml [unit_stage]`, `config.UnitStageWeights`)
| 키 | 1 | 2 | 3 | 4 | 5+ | 용도 |
|---|---|---|---|---|---|---|
| `deck_board_scale` | 0.15 | 0.15 | 0.25 | 0.6 | 1.0 | 목표 덱: 보드 항 배수 |
| `deck_one_star` | 0.25 | 0.25 | 0.35 | 0.7 | 1.0 | 목표 덱: 1성 1기 배수 |
| `plan_comp_scale` | 0.3 | 0.3 | 0.5 | 0.8 | 1.0 | 보드 배치: 목표 덱 소속 가산·핵심 특성 가산분 배수 |
| `plan_now_scale` | 2.0 | 2.0 | 1.5 | 1.2 | 1.0 | 보드 배치: 지금 강함(s_now·스테이지 통계·추천 보드) 배수 |

- 조회 규칙: 스테이지 이하 키 중 최댓값의 값을 쓴다.
  - `interpolate_rounds = true`면 다음 스테이지 값으로 `(라운드−1)/7`만큼 보간한다.
  - `deck_board_scale` 예: 2-6 0.22, 3-7 0.55, 4-1 0.6, 4-7 0.94, 5-1 1.0. 4스테이지 동안 올라간다.
  - 스테이지 미인식이면 최대 키 값이다(예전처럼 전부 반영).
- 2-6 실효 가중 wi/wa/wb는 0.45/0.25/0.30에서 **0.60/0.33/0.07**이 된다.
- 4-1 값을 0.5에서 0.6으로 올렸다. 0.5에서는 s04(4-1, 달의 아펠리오스 확정) 상점 1위가 센티널/라칸 0.0003 차이로 뒤집혔다.

### 10.3 보드 배치: 초반은 지금 강한 유닛 + 전환 경로
- 목표 덱 소속 가산(캐리 3.0/핵심 2.0/최종 1.2/빌드업 0.8)과 핵심 특성 배수의 가산분에 `plan_comp_scale`을 곱한다.
- s_now와 스테이지 통계에는 `plan_now_scale`을 곱한다.
- 성급·아이템·특성 활성은 그대로 둔다.
- 따라서 2스테이지에는 최종 덱에 없는 2성이 1성 목표 덱 핵심보다 먼저 올라간다. 5스테이지에서는 반대가 된다(테스트로 고정).
- 전환 경로 `BoardPlan.transition: BoardTransition`(계약 추가, 선택 필드)은 네 가지로 나눈다. 같은 내용을 notes에 "전환: …" 한 줄로도 넣는다.
  - `keep`: 라인업 중 목표 덱 최종 보드 유닛
  - `bridge`: 현재 레벨 이상 빌드업 보드에만 나오는 유닛(다리)
  - `placeholder`: 지금 전력용(교체 예정)
  - `next_targets`/`next_level`: 보드가 있는 다음 레벨의 빌드업 보드에서 아직 없는 유닛

### 10.4 test.png(2-6) 전/후 (`--screenshot … --no-jev --config <HEAD settings.toml + 현재 weights.toml>`)
- 전(§9 상태): 1위 **지옥불 아칼리 0.53**(1성 아칼리·카밀 겹침), 2위 적응가 마스터 이 0.51.
  - 상점 레오나 [구매](아칼리 덱 최종).
  - 아이템: 스테락 → 카밀.
- 후:
```
  1. 적응가 마스터 이 렝가  적합도 0.62  캐리 마스터 이  운영 lvl 7
     아이템: 지옥불 손도끼(부족) / 밤의 끝자락(조합가능) / 구인수의 격노검(부족)
     근거: 핵심 아이템: 밤의 끝자락 → 마스터 이 · 증강 시너지: 어수선한 마음 · 메타 평균 4.43등 · 6,829판
  2. 검은 가시 워윅  적합도 0.61  (핵심 아이템: 스테락의 도전, 거인의 결의 → 워윅)
  3. 처형자 드레이븐  적합도 0.58
[상점]  자야 [구매] 0.56(확인 보유 2 — 2성 보너스 그대로) · 레오나 [보류] 0.46
[아이템] 스테락의 도전 → 니달리 0.97 · 거인의 결의 → 렝가 0.95   (둘 다 마스터 이 덱 핵심 아이템)
```
- 1위가 아이템 기반 덱(밤의 끝자락 조합 가능)으로 돌아왔다.
- 아칼리 덱은 표시 3위 밖으로 밀렸다. 2-6에서 아칼리 덱 보드 항은 0.07 x U(1성 2기 → 0.125)로 약 0.01이다.

### 10.5 테스트 (`tests/advisor/test_unit_stage.py`, 10개)
- 스케줄 조회·보간·미인식, 재분배 합 보존, toml 값
- **같은 보유 유닛, 스테이지 2 vs 5**: U 0.25 미만 vs 1.0
- 2성·아이템 든 1성은 초반에도 센다
- **보석 건틀릿 + zyra 덱 1성 핵심 4기**: 2-3은 아이템 덱(spellweaver/invoker)이 1위, 5-1은 zyra 덱이 1위(off·mock 둘 다)
- **상점**은 2스테이지에도 사본 2개 → 2성 보너스·"보유 2"
- **보드 배치**는 2스테이지에도 보유 유닛으로 채운다. 2스테이지에는 2성 무관 유닛, 5스테이지에는 1성 목표 덱 핵심을 올린다. 전환 경로도 확인한다.

기대값 조정:
- `s02_board_ad_items.json` `top_comp_in`에 `adaptor-masteryi_ad-rengar`를 추가했다.
  - 이유: 3-2에서 IE+구인수(AD) 아이템 적합이 lunar-aphelios와 같고(I=0.85), 2성 요릭(마스터 이 빌드업)이 할인 없이 센다. 픽스처 설명("AD 캐리 덱")에 맞는 결과다.
- `test_board_trust.py::test_advise_uses_board_units_when_bench_unreliable`은 stage를 5-1로 바꿨다. "보유 유닛이 목표 덱에 반영"을 검증하는 테스트라, 유닛이 전부 반영되는 구간에서 돌린다.

## 11. 보드 배치 = 스테이지별 실제 보드 통계 (MetaTFT Early Comps, 2026-09-24)

사용자 요구: "지금 무엇을 보드에 올릴지"는 인터넷의 라운드별 승리 보드 데이터에 근거해야 한다. 원천은 stats-researcher의 `repo.stage_stats`(`_workspace/28_stage_boards_sources.md` §4)다.

### 11.1 구성
- `src/tft_advisor/advisor/stage_boards.py`(신규)
  - `StageBoardSource` 프로토콜과 `MetaTftStageBoards`(StageStats 덕 타이핑, stats 모듈을 import하지 않는다)를 둔다.
  - `Advisor.stage_boards`는 `MetaTftStageBoards.from_stats(stats, w.board_plan)`로 만든다. stage_stats가 없거나 비면 None이 되고, 항이 0이라 예전 동작 그대로다(mini 통계 테스트 전부).
- `unit_signal(unit, star, stage, level)`
  - 성급 행을 먼저 쓰고(표본 < `stage_unit_min_games`이면 전 성급 행) `delta`(같은 스테이지 기준선 대비)를 표본으로 수축한다: delta x g/(g+`stage_shrink_k`=500).
  - 신호 = clip(−수축 delta / `stage_delta_span`(0.3), −1, 1).
  - 점수 = `stage_unit`(0.8) x 신호 x `plan_now_scale`. 나쁜 유닛은 감점된다.
  - 근거 문구는 좋을 때만 붙는다: "통계: 2스테이지 1성 평균 등수 −0.11".
- `best_board(owned, stage, level, comp, comp_scale)` = **"지금 이 스테이지 추천 보드"**
  - 대상: 유닛 수 == 칸 수인 실제 보드. variation을 우선하고, 없을 때만 cluster를 본다. 표본 ≥ `board_min_games`(100), 없는 유닛 ≤ `board_max_missing`(2).
  - 점수 = 품질(수축 delta) + `board_cover`(1.0) x 보유 비율 + `board_link`(0.5) x `plan_comp_scale` x 목표 덱 연결(comp_links).
  - 초반은 연결 가중이 작아 지금 가장 강한 보드를 고르고, 후반은 목표 덱으로 이어지는 보드를 고른다.
  - 고른 보드에 든 보유 유닛은 `board_member`(0.6) x `plan_now_scale`만큼 가산("추천 스테이지 보드")을 받는다.
- `next_hint(lineup, stage)`
  - 라인업과 가장 닮은 클러스터(`cluster_for`)에서 `transitions` 중 목표 덱 연결 ≥ `trans_link_min`인 경로를 우선하고, 그중 비율이 가장 큰 경로를 고른다. 없으면 전체에서 비율 최대 경로다.
  - notes에 "다음 스테이지: 이 보드는 보통 +○○·○○ 쪽으로 이어집니다(경로 58% · 평균 4.40등)"를 넣는다.
- 계약: `BoardPlan.stage_board: StageBoardHint | None`(선택 필드, 기존 호환)
  - 필드: stage, kind, units, owned, games, avg_place, delta, comp_link, next_stage/next_units/next_share/next_avg_place
  - 같은 내용이 notes 두 줄("지금 이 스테이지 추천 보드: …", "다음 스테이지: …")로도 들어간다. 그래서 지금 `board_plan_lines`의 "참고:" 줄로 그대로 보인다.
- **가중치 이전**: 모듈 상수 `BoardPlanWeights`를 `config.BoardPlanWeights` / `weights.toml [board_plan]`으로 옮겼다(§6.2 표 값 그대로 + 스테이지 통계 키). 엔진이 `self.w.board_plan`을 넘긴다.
- 주의(28 §0·§4): top4가 없다(avg_place·win_rate·round_win_rate). 연승 보드일수록 좋아 보이는 상관 관계다. 그래서 가중을 작게(유닛 최대 ±0.8 x now 배수) 두고 표본으로 수축했다. 스테이지 5는 생존자 기준선(3.66)이라 반드시 delta로만 비교한다.
- 지연: test.png 추천 5ms(변화 없음). 보드 목록이 스테이지당 300~430행이라 조회 비용은 무시할 수준이다.

### 11.2 test.png(2-6) 보드 배치 전/후
전(§10 적용 후, 스테이지 통계 없음):
```
  보드: 코그모(빌드업 유닛) · 아칼리(적응가 1→2 활성) · 카밀(약탈자 1→2 활성) · 카시오페아 · 엘리스(악의 여단 2→3 활성)
  교체: 없음(지금 배치를 유지하세요)
```
후:
```
[보드 배치]
  기준 적응가 마스터 이 렝가 · 칸 5
  보드: 카시오페아(추천 스테이지 보드 · 통계: 2스테이지 1성 평균 등수 −0.11) · 카밀(추천 스테이지 보드 · 통계: 2스테이지 1성 평균 등수 −0.05) · 엘리스(악의 여단 2→3 활성 · 추천 스테이지 보드) · 코그모(빌드업 유닛 · 통계: 2스테이지 1성 평균 등수 −0.07) · 아칼리(적응가 1→2 활성 · 약탈자 1→2 활성)
  교체: 없음(지금 배치를 유지하세요)
  벤치: 자야 · 자야 · 카밀 · 아칼리
  참고: 벤치 미확인 5기는 판단하지 않았습니다 — 강한 유닛이면 직접 올려 주세요
  참고: 지금 이 스테이지 추천 보드: 케이틀린·카밀·카시오페아·엘리스·라칸 (2스테이지 실제 보드 160판 · 평균 등수 −0.27 · 보유 3/5) — 케이틀린·라칸은(는) 상점에서 구하시면 됩니다
  참고: 다음 스테이지: 이 보드는 보통 +케이틀린·라칸 쪽으로 이어집니다(경로 58% · 평균 4.40등)
  참고: 전환: 다리 코그모·아칼리 / 지금 전력용(교체 예정) 카시오페아·카밀·엘리스 / 레벨 6 목표 마스터 이·렝가·요릭·카르마·바이
```
- 라인업은 그대로다(보유 6기 중 5칸 — 자야가 스테이지 2 1성 delta +0.17로 가장 약하다).
- 근거가 실제 보드 통계로 바뀌었다. 추천 보드와 다음 스테이지 경로가 같은 방향(케이틀린·라칸)을 가리킨다.
- 레벨 5 크기의 2스테이지 보드 중 보유 유닛으로 4/5 이상 되는 보드(표본 ≥ 100)는 없다. 그래서 `board_max_missing`을 1에서 2로 올렸다.

### 11.3 테스트 (`tests/advisor/test_stage_boards.py`, 9개, 픽스처 `tests/fixtures/stats/metatft_early/` + mini, 네트워크 없음)
- 유닛 신호 = 수축 delta(값·부호·"3스테이지 … −" 문구), 표본 적으면 약해짐, 성급 행 → 전 성급 폴백, 모르는 유닛 None
- 추천 보드
  - 베이가만 없는 보유 → 3스테이지 1위 variation(7,973판)
  - 겹침 없음·스테이지 없음 → None
  - 연결 가중: 초반(comp_scale 0) SPELL, 목표 덱 연결 강하면 COVEN 계열(link > 0.5)
- 다음 스테이지: 클러스터 15 → 3스테이지 클러스터 1(비율 0.676), 스테이지 5 → None
- 빈 StageStats·속성 없음 → 소스 None, 모든 조회 None
- 보드 배치: 보드 COVEN, 벤치 SPELL(3-2) → SPELL로 교체, `stage_board` 힌트·"7,973판"·"보유 5/5"·"평균 등수 −". 소스 None/빈 소스면 결과 동일
- 엔진: 스테이지 통계 있는 저장소 → `Advisor.stage_boards` 연결, `rec.board_plan.stage_board.stage == 3`. mini 어댑터 → None

### 11.4 전체 스위트
`PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest -o addopts="" -q` → **1307 passed**, 3 skipped, 1 xfailed, 4 failed. 실패 4건은 알려진 Windows 실패다(`test_api_key` 힌트, `test_setup` 권한 상자, `test_credentials` 0600 두 건).

### 11.5 요청 · 남은 일
- **app-integrator**
  - `board_plan_lines`는 notes를 "참고:"로 이미 보여 준다. 오버레이가 좁으면 "지금 이 스테이지 추천 보드" 줄을 우선하라.
  - 구조화 렌더링을 원하면 `BoardPlan.stage_board`/`transition`을 쓰고, 같은 notes 줄은 빼면 된다.
- **qa-validator**: 새 필드 `BoardPlan.transition`/`stage_board`, `Weights.unit_stage`/`board_plan` 경계, `QUESTIONS_VERSION` q3.
- **stats-researcher**
  - 레벨 5 크기 2스테이지 variation이 12개(표본 ≥ 100)뿐이라 추천 보드가 "보유 3/5"로 자주 나올 것이다.
  - variation 최소 표본(현재 30)이나 레벨 ± 1 보드 제공을 검토해 달라.
  - 18.3b 갱신 뒤 값이 바뀌는지 다시 볼 것.
- **튜닝 후보**: `stage_unit`(0.8), `board_member`(0.6), `stage_delta_span`(0.3), `board_max_missing`(2). 실전 로그(`debug`)로 조정한다.

## 12. QA 27 W4(문구 정직성) · W3(아이템 보유자 덱) 수정 — jev-strategist, 2026-09-24

### 12.1 W4-1 "평균 등수 −0.11" → 기준선 대비 문구
- delta = avg_place − 같은 스테이지 기준선(음수가 좋다)이다. 절대 등수처럼 읽히지 않게 문구를 바꿨다.
- 새 함수 `stage_boards.vs_baseline(delta)`: 음수 → "평균보다 0.11등 높음", 양수 → "평균보다 0.27등 낮음", |delta| < 0.005 → "평균과 비슷함".
- 유닛 근거(`UnitSignal.reason`)
  - 전: "통계: 2스테이지 1성 평균 등수 −0.11"
  - 후: "통계: 2스테이지 1성 · 평균보다 0.11등 높음(1,234판)"(표본 수 포함)
- 추천 보드 줄(`board_plan._pick_note`)
  - 전: "… 7,973판 · 평균 등수 −0.46 · 보유 5/5"
  - 후: "… 7,973판 · 평균보다 0.46등 높음 · 보유 5/5"
- 다음 스테이지 줄의 "평균 4.40등"은 절대값이라 그대로 두었다.

### 12.2 W4-2 `next_hint` 최소 일치도
- `NextHint`에 `match`(라인업 ↔ 출발 클러스터 Jaccard)와 `close` 필드를 더했다.
- 새 가중 `[board_plan] trans_match_min = 0.5`, `trans_similar_min = 0.25`(config.py · weights.toml).
  - Jaccard ≥ 0.5 → "다음 스테이지: 이 보드는 보통 …"
  - 0.25 ≤ Jaccard < 0.5 → "다음 스테이지: 비슷한 보드는 보통 …"
  - < 0.25(예: 4기 중 1기만 겹침 = 1/7) → 힌트 없음(None)
- `StageBoardHint` 계약은 바꾸지 않았다(next_* 필드 그대로).

### 12.3 W3 아이템 보유자 덱 일치(선택 항목)
- 원인: `bis(x)`는 모든 후보 덱 중 `rel × item_fit` 최대값이다. 그런데 `holder_for(x)`는 늘 1위 덱에서 보유자를 찾았다.
  - 그래서 스테락처럼 적합도는 2위 덱에서 오고, 보유자(니달리)는 1위 덱에서 나오는 불일치가 생겼다.
- 수정(`scoring.py`)
  - `bis_source(x)`: (bis, 출처 덱)을 준다. 동률이면 1위 덱을 우선한다. `bis()`는 이 값을 그대로 쓴다(값 불변).
  - `item_holder(x)` → (표시 보유자, 덱 이름)
    - 출처가 1위 덱이면 예전 동작과 같다.
    - 출처가 **표시된** 2·3위 덱이고 그 덱에 보유자가 있으면, 그 덱의 보유자를 보여 주고 근거에 덱 이름을 붙인다. 예: "렉사이 핵심 아이템(주문술사 베이가)", 벤치 완성템은 "렉사이에게(주문술사 베이가)".
    - 출처가 표시되지 않은 덱이거나 그 덱에 보유자가 없으면 예전처럼 1위 덱 보유자를 쓴다. 화면에 없는 덱 이름은 꺼내지 않는다.
  - **점수는 바꾸지 않았다**
    - bis 값, st(아이템 통계)는 예전처럼 1위 덱 보유자 기준이다.
    - 탐욕 선택도 그대로다. 달라진 것은 `holder_unit_id`, `reason`, `debug["item"].rows[].deck`뿐이다.
- 참고(mini, 3-2 재료 6개 탐침): 스테락의 bis 출처는 표시되지 않은 `juggernaut-elderdragon`이다. 그래서 보유자는 예전처럼 1위 덱의 니달리다.
  - test.png에서처럼 출처가 표시된 2위 덱이면 그 덱의 보유자와 덱 이름이 나온다.
  - 표시되지 않은 덱이 출처인 경우의 불일치는 남는다. 이 경우를 없애려면 bis를 표시 덱으로 제한해야 하는데, 그러면 점수가 바뀐다(사용자 판단, 27 §3.1 (a)).

### 12.4 테스트
- `tests/advisor/test_stage_boards.py`
  - 기존 2개 문구 기대값 갱신: 유닛 근거 전체 문자열, 추천 보드 "평균보다 …등 높음"·"평균 등수" 없음
  - `test_next_hint_follows_transitions`에 match 1.0·close 추가
  - 새 테스트 `test_vs_baseline_wording`
  - 새 테스트 `test_next_hint_wording_depends_on_how_well_the_lineup_matches`: 0.5 → "이 보드는", 0.4 → "비슷한 보드는", 1/7 → None, 임계값 설정 반영
- `tests/advisor/test_advisor_units.py::test_item_holder_comes_from_the_deck_whose_fit_was_used`
  - 표시 2·3위 출처면 그 덱의 보유자·덱 이름이 나온다(탐침에서 3건 이상).
  - 그 밖에는 예전 보유자가 나온다.
  - 근거에 덱 이름이 붙는다.
  - st는 1위 덱 보유자 기준 그대로다.
- 전체: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest -o addopts="" -q` → **1322 passed**, 3 skipped, 1 xfailed, 4 failed. 실패 4건은 알려진 Windows 실패다(api_key 힌트, setup 권한 상자, credentials 0600 두 건).


## 13. 아이템 추천 = 1위 덱 캐리 BIS·핵심 아이템 먼저 (QA 27 §3.1 W3 후속) — jev-strategist, 2026-09-25

사용자 결정: 아이템 추천은 **1위 목표 덱의 캐리 BIS·핵심 아이템**을 중심으로 한다. CLAUDE.md 원칙(최종 덱 = 보유 아이템·증강, 아이템은 그 덱을 향해 쌓는다)과 맞춘다.

### 13.1 문제 (test.png, 2-6, 재료 B.F. 대검 · 쇠사슬 조끼 · 곡궁 · 거인의 허리띠)
- 1위 덱은 적응가 마스터 이 렝가이고, 캐리 BIS 밤의 끝자락(대검+조끼)을 지금 만들 수 있다.
- 예전 [아이템]은 점수 순 탐욕 선택이었다. 스테락(대검+허리띠, bis 0.986, 2위 워윅 덱 출처)과 거인의 결의(조끼+곡궁)가 먼저 뽑혀 밤의 끝자락 재료를 가져갔다.

### 13.2 변경 (`advisor/scoring.py`, 코드 전용)
- **역할**: `top_item_role(x)`는 1위 덱 기준으로 `"carry"`(캐리 BIS) / `"core"`(item_fit ≥ `item_fit.used_by_min`: 다른 핵심 유닛 아이템, 핵심 특성 상징) / None을 돌려준다.
- **재료 배분** `allocate_components`는 탐욕 선택을 대신한다. 재료 ≤ `item.exact_max_components`(10)이면 가능한 부분 매칭을 전부 본다(10개 = 9,496가지, 최악 약 37ms). 사전식으로 다음을 최대화한다.
  1. 1위 덱 캐리 BIS 수. 이미 가진 것은 빼고, 같은 아이템은 덱이 아직 필요한 개수까지만 센다.
  2. 1위 덱 다른 핵심 아이템 수
  3. 보조 점수 합. 다른 덱·범용 아이템만 해당하고, 조건은 두 가지다.
     - 점수 ≥ `item.secondary_min_score`(0.5)
     - 아직 없는 1위 덱 캐리 BIS의 재료를 쓰지 않는다(예약 재료)
     조건을 못 채우면 만들지 않고 보관한다(목록에서 빠진다).
  4. 고른 수가 적은 쪽
- 재료가 이 한도보다 많으면 탐욕으로 고른다. 순서는 캐리 → 핵심 → 점수다.
- **1위 덱 아이템을 하나도 못 만들 때**(mode `fallback`): 예전처럼 점수 합이 가장 큰 배분을 고른다.
  - stage < `item.tempo_until_stage`(4)이고 보관(hold)이 아니면 "1위 덱 아이템은 아직 만들 수 없어 지금 전력용으로 권해 드립니다 · 지금 보드의 ○○에게"라고 쓴다. 재료를 끝까지 들고 있지 않게 하려는 것이다.
  - 보드에 근거 있는 유닛이 없으면 보유자를 비운다. 이때 문구는 "지금 보드의 알맞은 유닛에게(나르용 · 처형자 드레이븐)"이다.
  - 후반이면 "1위 덱 아이템은 지금 만들 수 없습니다 · 만든다면 …"이라고 쓴다.
  - hold일 때는 "권해 드립니다"를 쓰지 않는다. 그래서 hold 판정을 문구보다 먼저 계산하도록 순서를 옮겼다(hold 규칙 자체는 그대로다).
- **보유자**
  - 1위 덱 아이템: `holder_for`(1위 덱 캐리/핵심 유닛)를 쓴다.
  - 그 유닛이 아직 없고(보유 유닛 판독 있음) 보드에 알맞은 유닛이 있으면 "마스터 이 확보 전까지 카밀에게 임시로"라고 쓴다. `holder_unit_id`는 1위 덱 보유자 그대로다.
  - 임시 보유자는 `temp_holder`가 고른다. 순위는 다음과 같다.
    1. 표시 덱에서 그 아이템을 드는 유닛
    2. 유닛+아이템 전체 통계 값(수축 후 > 0.5). 1위 덱 최종·빌드업 유닛이면 `item.temp_holder_top_bonus`(0.05)를 더한다.
    3. 코스트
    근거가 없는 유닛은 고르지 않는다. `item.temp_holder = false`로 끌 수 있다.
  - 벤치 완성템의 보유자 추천에도 같은 임시 문구를 쓴다(1위 덱 기준 아이템일 때).
- **문구(존댓말)**
  - "1위 덱(적응가 마스터 이 렝가) 캐리 아이템 · …" / "1위 덱(…) 핵심 아이템 · …"
  - 보조: "보조: 워윅 핵심 아이템(검은 가시 워윅)" / "보조: 달빛 아펠리오스 니달리용 아이템(1위 덱 재료와 겹치지 않음)" / "보조: 범용 아이템(…)"
- **debug["item"]**: `mode`(top/fallback), `top_comp`, `need`(아직 필요한 캐리 BIS·핵심), `rows[].role`, `picked[]`(item, kind = top/secondary/fallback, holder, reason)를 더했다.
- **점수 공식**(bis·st·Jev 합성)과 hold 규칙은 바꾸지 않았다. 바뀐 것은 배분(무엇을 만들지), 순서, 보유자 문구다.

### 13.3 Jev
- I1(`item_pick`) 문구를 개정했다. 추가한 문장: "Prefer a core item of the main carry of the candidate comp that best fits the player's items and augments; choose an item for another comp only if it does not use a component that carry needs." 이에 맞춰 `QUESTIONS_VERSION` q3 → **q4**로 올렸다(캐시 키 변경).
- 배분 규칙은 코드가 결정한다. Jev 확률은 예전처럼 점수(pj)에만 들어가고, 보조·fallback 순서에 영향을 준다.
  - 1위 덱은 같은 요청의 comp 질문 결과로 정해진다. 그래서 질문 시점에 Jev에게 "1위 덱"을 지정할 수 없고, 문구는 "자원에 가장 맞는 후보 덱"으로 쓴다.
- mock 힌트(`engine.py` item_pick)도 1위 덱 캐리 +0.3, 핵심 +0.1로 맞췄다(mock 전용).

### 13.4 설정 (`config/weights.toml [item]`, `config.ItemWeights`)
| 키 | 기본값 | 뜻 |
|---|---|---|
| `secondary_min_score` | 0.5 | 1위 덱 아이템이 있을 때 보조 아이템 최소 점수 |
| `tempo_until_stage` | 4 | 1위 덱 아이템이 없을 때 이 스테이지 전까지 '지금 전력용' |
| `exact_max_components` | 10 (2~12) | 전체 탐색 한도 |
| `temp_holder` | true | 임시 보유자 문구 |
| `temp_holder_top_bonus` | 0.05 | 임시 보유자 순위에서 1위 덱 유닛 가산 |

### 13.5 전/후 (test.png, `--screenshot tests/fixtures/screens/test/test.png --no-jev --no-overlay`)
전:
```
[아이템]
  · 스테락의 도전 (B.F. 대검 + 거인의 허리띠) → 워윅 0.97 — 워윅 핵심 아이템(검은 가시 워윅)
  · 거인의 결의 (쇠사슬 조끼 + 곡궁) → 워윅 0.95 — 워윅 핵심 아이템(검은 가시 워윅)
```
후:
```
[아이템]
  · 밤의 끝자락 (B.F. 대검 + 쇠사슬 조끼) → 마스터 이 0.93 — 1위 덱(적응가 마스터 이 렝가) 캐리 아이템 · 마스터 이 확보 전까지 카밀에게 임시로
```
- 곡궁+허리띠(내셔의 이빨, 점수 0.075)는 만들지 않는다. 곡궁은 1위 덱 캐리 BIS 구인수의 재료(예약)이고, 점수도 보조 기준 미만이다.
- 임시 보유자로 카밀을 고른 근거는 카밀+밤의 끝자락 전체 통계 −0.19등(2,307판)이다. 코그모는 1위 덱 빌드업 유닛이지만 통계가 없어(0.5) 가산 0.05만으로는 카밀(0.64)을 넘지 못한다.
- 목표 덱·상점·보드 배치는 변화가 없다. 추천 시간은 6~8ms다.

### 13.6 테스트
- 새 fixture 4개(`tests/fixtures/states/`). `expect`에는 실제 저장소에서도 성립하는 불변식만, `expect_mini`에는 mini 통계의 구체값을 둔다. fixround(실제 저장소)는 `expect`만 본다.
  - `s15_item_top_carry_first`
    - 3-2, 재료 곡궁·지팡이·장갑·프라이팬
    - 1위 덱 핵심 속사포 상징(0.925)이 캐리 BIS 구인수(0.924)보다 점수가 높고 곡궁을 공유한다
    - 결과: 구인수 → 아펠리오스가 먼저 나오고, 처형자 상징은 "보조:"다
  - `s16_item_none_for_top_late`: 4-2, 대검+음전자 → 피바라기, "1위 덱 아이템은 지금 만들 수 없습니다"
  - `s17_item_early_tempo_slam`: 2-5, 조끼+곡궁 → 거인의 결의 → 보드의 오른, "지금 전력용", hold false
  - `s18_item_temp_holder`: 2-5, 조끼+허리띠 → 태양불꽃 망토 → 렉사이, "렉사이 확보 전까지 오른에게 임시로"
- `test_advisor_fixtures.py`
  - 새 expect 키: `item_top_rule`, `item_mode`, `item_first`, `item_first_holder`, `item_first_reason_has`, `item_absent`, `item_reason_has`
  - `expect_mini` 병합을 더했다.
  - `check_invariants`가 모든 fixture 추천에 `check_item_top_rule`을 적용한다. 규칙 세 가지:
    - 아직 없는 1위 덱 캐리 BIS를 만들 수 있으면 그중 하나가 첫 추천이다.
    - 1위 덱 아이템이 보조보다 앞선다.
    - fallback이면 1위 덱 아이템이 없다.
- `test_advisor_units.py` 새 테스트 11개:
  - 다른 덱 아이템(보석 건틀릿, 점수를 1.0으로 올림)이 재료를 공유해도 캐리 BIS가 먼저다(예전 탐욕이라면 건너뛰었음을 함께 확인)
  - 1위 덱 핵심 < 캐리 BIS
  - 보조는 예약 재료를 쓰지 않는다(재료 4개 조합 210개 전수)
  - 중복 캐리 BIS는 필요한 개수만 1위 덱 아이템으로 센다
  - 재료 12개 탐욕 경로
  - 전체 탐색 = 무차별 대입 최댓값
  - 초반 템포 슬램(보드 유닛 보유자)
  - 후반 fallback 문구
  - hold와 "권해 드립니다"가 같이 나오지 않음
  - 임시 보유자(보유 시 없음, 설정 끔)
  - I1 q4 문구
- 전체: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -o addopts="" -q` → **1345 passed**, 3 skipped, 1 xfailed, 4 failed. 실패 4건은 알려진 Windows 실패다(api_key 힌트, setup 권한 상자, credentials 0600 두 건).

### 13.7 남은 점 · 사용자 판단
- 1위·2위 덱이 근소차이면(test.png는 0.62 대 0.61) 1위가 바뀔 때 아이템 추천도 통째로 바뀐다. 히스테리시스(§5.2)가 널뛰기를 줄이지만, 두 덱이 재료를 두고 경쟁하는 상황에서는 여전히 민감하다.
- 남은 재료를 "보관합니다(구인수의 격노검용 곡궁)"처럼 알려 주려면 `ItemAdvice`에 메모 필드가 필요하다(contracts, app-integrator). 이번에는 넣지 않았다.
- 임시 보유자는 유닛+아이템 전체 통계에 기대므로, 통계가 없는 유닛은 후보가 되지 않는다. 그런 유닛만 보드에 있으면 임시 문구 없이 "마스터 이에게"로 나온다.


## 14. 판매 추천 · 보드 배치 섹션 유지 · 사용자 고정 덱 — jev-strategist, 2026-09-25

사용자 요청 세 건(같은 라운드, 커밋하지 않음):
1. "돈관리도 중요하기 때문에, 필요없는 기물이 벤치나 보드에 있으면 팔라고 말해줘야해"
2. "보드배치 추천이 가끔씩 사라지는데 왜 그러는거야"
3. 오버레이에서 목표 덱을 눌러 **고정**하면 모든 추천이 그 덱을 따른다(다시 누르면 해제). UI는 app-integrator가 이 API로 만든다.

모두 코드·통계 전용이다. Jev 호출은 늘지 않는다.

### 14.1 판매 추천 (`src/tft_advisor/advisor/sell.py`, `config/weights.toml [sell]` = `config.SellWeights`)

**판매가**: 1코스트 = 사본 수(1/3/9). 2코스트 이상은 코스트 x 사본 수 − (성급 − 1)이다(2성 3c−1, 3성 9c−2). 이 규칙은 저장소의 `app/ledger.py sell_value`(구매·판매 추적이 쓰는 규칙)와 같다. advisor는 app을 import하지 않으므로 `unit_sell_value`를 따로 두고, 테스트로 두 함수가 모든 코스트·성급에서 같음을 고정했다.

**이자**: min(5, 골드 // 10).
- 팔아서 구간이 오르면 "팔면 30골드 → 이자 +1"이라고 쓴다.
- 못 닿지만 `interest_near`(3) 이내면 "이자 구간 30골드까지 1 남음"이라고 쓴다.
- 골드를 모르거나 이미 최대 이자면 쓰지 않는다.

**지키는 유닛**(하나라도 해당하면 팔라고 하지 않는다):

| 규칙 | 설명 |
|---|---|
| 이름 미상·낮은 신뢰도 | `View`에 들어오지 않으므로 애초에 후보가 아니다. 수만 "미확인 유닛 N기는 판단하지 않았습니다"로 알린다 |
| 보드 배치 라인업 | 지금 올릴 유닛(`BoardPlan.lineup`)은 (id, 성급, 보드 여부)로 인스턴스를 하나씩 소비해 지킨다 |
| 1위 덱 경로 | 최종 보드와 캐리. `late_from_stage`(4) 전에는 현재 ~ +`buildup_levels_ahead`(1) 레벨 빌드업 보드도 지킨다 |
| 표시된 2·3위 덱 | `protect_shown_until_stage`(3) 이하에서 최종 보드 유닛을 지킨다(아직 덱이 정해지지 않았다) |
| 1성 쌍 | 확인된 1성이 2기 이상이고, 후반 전이고, 그 코스트 상점 확률이 `pair_min_odds`(10%) 이상이면 지킨다. 아니면 "2성 가능성이 낮습니다(상점 5코스트 0%)" 또는 "후반이라 2성 대기보다 골드가 낫습니다"라고 쓴다 |
| 초반 벤치 2성 이상 | `keep_bench_star2_until_stage`(3) 이하에서 교체 대기로 둔다. 벤치가 가득 찼거나 구매 자리가 모자라면 예외다 |

**후보 근거**(합쇼체):
- 기본: "목표 덱·빌드업에 없습니다", 후반이면 "최종 덱에 없는 유닛입니다".
- 2성 옆의 1성: "2성이 이미 있어 3성은 어렵습니다".
- 보드 유닛: 앞에 "보드에서 빼도 되는 유닛 · "을 붙인다.
- 아이템을 든 유닛: 끝에 "· 아이템 N개는 벤치로 돌아옵니다"를 붙인다. 팔아도 아이템은 잃지 않으므로 판매를 막지 않는다.

**보여 주는 조건**:
- 초반(스테이지 ≤ `early_until_stage` = 2): 쌍이나 1성 싱글을 들고 있는 것은 정상이라 조용히 둔다. 다음 중 하나일 때만 추천한다.
  - 벤치 압박: 이름 미상을 포함한 벤치 수 ≥ `bench_near_full`(8)이면 `early_max`(2)기까지.
  - 상점 [구매]에 벤치 자리가 모자랄 때. 확인된 1성이 2기인 유닛을 사면 바로 합성되므로 자리가 필요 없다.
  - `interest_max_units`(2)기 이하를 팔아 다음 이자 구간에 닿을 때.
- 3스테이지 이상: 후보를 전부 보여 준다.
- 파는 순서: 벤치 먼저 → 보드 배치 점수 낮은 순 → 판매가 낮은 순.

**계약 추가**(선택 필드, 기존 호환 — `contracts.py`):
```python
class SellAdvice(ContractModel):
    unit_id: ChampionId
    star: Star | None = None
    where: Literal["board", "bench"]
    hex: tuple[int, int] | None = None          # where=board일 때 화면에서 읽은 칸
    bench_slot: int | None = None               # where=bench일 때 0~8
    gold: int | None = None                     # 판매가(코스트 모르면 None)
    items: list[ItemId] = []                    # 팔면 아이템 벤치로 돌아온다
    reason: str | None = None

class BoardPlan(...):                           # 추가 필드
    sell: list[SellAdvice] = []                 # 파는 순서
    sell_gold_total: int = 0
    interest_note: str | None = None            # "팔면 30골드 → 이자 +1" / "이자 구간 30골드까지 1 남음"
    sell_notes: list[str] = []                  # "벤치 9/9 가득 참 — 상점 구매 1기 자리가 모자랍니다" · "미확인 유닛 5기는 판단하지 않았습니다"
    stale: bool = False                         # §14.2
    low_trust: bool = False                     # §14.2
```

**표시**(`app/report.py`): 새 순수 함수 `sell_lines(plan, names)`. `board_plan_lines`가 "벤치:" 줄 다음에 붙이므로 오버레이도 그대로 보인다.
```
판매: 알리스타 · 쉔 (+4골드 · 팔면 30골드 → 이자 +1)
판매 이유: 알리스타 — 목표 덱·빌드업에 없습니다 / 쉔 — 목표 덱·빌드업에 없습니다 · 아이템 1개는 벤치로 돌아옵니다
판매 참고: 벤치 9/9 가득 참 · 미확인 유닛 5기는 판단하지 않았습니다
```
판매할 유닛도 안내도 없으면 줄이 없다. 초반에 조용할 때가 여기에 해당한다.

**건너뛰는 경우**:
- `rescore_shop`은 판매를 다시 계산하지 않는다(직전 계획 그대로). 새로 산 유닛은 라인업에 없어서 판매 대상으로 잘못 뜰 수 있기 때문이다. 다음 준비 단계의 전체 추천이 새로 채운다.
- `stale`·`low_trust` 계획에는 판매를 붙이지 않는다.

### 14.2 보드 배치가 사라지던 원인과 수정

원인은 세 가지였다.
1. `plan_board`가 `not view.units_known`이면 None을 돌려줬다. 실전에서는 vision 이름과 장부가 섞여 출처가 `tracked`가 되고, (추정) 이름 상한 0.75 때문에 필드 신뢰도가 0.50까지 내려간다. 그러면 `owned_units`가 한 유닛도 쓰지 않아(§1 표: tracked·저신뢰 = 사용 안 함) 섹션이 통째로 사라졌다.
2. augment_select 화면은 보드를 못 읽는 경우가 많은데, 이때 `_full`이 `board_plan=None`을 만들었다. carousel은 직전 추천을 복사했다.
3. 보드를 못 읽은 프레임(state.board None).

수정:
- **신뢰도 하락**: `board_plan.relaxed_view(view)`를 추가했다. 필드 신뢰도·출처와 무관하게, 칸마다 이름 신뢰도가 임계값(0.6) 이상인 유닛만 쓴다. 이름 미상·낮은 신뢰도 칸은 "미확인"으로 자리만 센다(내리지도, 팔지도 않는다). 계획에는 `low_trust=True`와 notes 첫 줄 "일부 유닛 미확인 — 이름을 확인한 유닛 기준입니다(보드·벤치 신뢰도 낮음)"이 붙는다. 이 View는 보드 배치 전용이다. 목표 덱·상점은 기존 `owned_units` 규칙을 그대로 쓴다. 판매 추천은 하지 않는다.
- **이번 화면에서 계획을 못 세움**(보드 None, 이름 전무, augment_select, carousel): 세션의 직전 계획을 `stale=True`로 이어 간다(`board_plan.stale_copy`, 판매는 비운다). `board_plan_lines` 첫 줄 앞에 "(직전)"이 붙는다. 전투·item_select 화면은 원래 직전 추천을 그대로 돌려준다. `rescore_shop`도 직전 계획을 유지한다.
- **지우는 때**: 새 판(`reset()` — loading/game_over 화면, loop의 새 판 감지)뿐이다. 직전 추천이 없는 첫 화면에서 이름을 하나도 모르면 예전처럼 None이다.
- 참고: 신뢰도가 낮은 tracked 출처에서는 장부 이름의 유닛 신뢰도가 대개 1.0이다. 그래서 relaxed 계획은 장부 이름을 믿는다. "저신뢰" 문구와 판매 생략으로 이 한계를 표시한다. 유닛별 출처(`UnitOnBoard.source`, §1)가 생기면 vision 이름만 쓰도록 좁힐 수 있다.

### 14.3 사용자 고정 덱 API (app-integrator가 UI에서 호출)

```python
Advisor.set_pinned_comp(comp_id: str | None) -> None   # 고정 / None이면 해제. 스레드 안전(threading.Lock)
Advisor.pinned_comp_id -> str | None                   # property. 지금 고정된 덱(설정값 그대로)
Recommendation.pinned_comp_id: str | None = None       # 이 추천에 실제로 적용된 고정 덱(통계에 없는 덱이면 None)
```
- **저장 · 해제**
  - 고정값은 advisor 세션(`Session.pinned`)에 있다.
  - `reset()`(새 판)이면 풀린다.
  - 통계에 없는 comp_id는 추천 때 무시하고 WARNING 로그를 남긴다. 이때 `Recommendation.pinned_comp_id`는 None이다.
- **순위**
  - 고정 덱은 점수와 무관하게 `target_comps[0]`이다.
  - 아래에 점수 순 대안이 최대 2개 붙는다(`max_target_comps` − 1).
  - 근거 첫 줄은 고정 덱이 "사용자 고정 덱", 대안이 "대안 덱(고정 덱 아래)"이다.
  - 고정 중에는 '초반: 방향 미정'과 "직전 추천 유지" 표시를 끈다.
- **후보에 없을 때**: 1차 필터 후보에 없으면 통계에서 직접 불러 `score_candidate`로 후보에 넣는다. 후보 수 상한은 유지하고, 가장 낮은 후보를 뺀다.
- **따라가는 것**
  - `Scorer.shown[0]`이 고정 덱이 되므로 보드 배치·판매·아이템(1위 덱 캐리 BIS 우선 배분 §13)·빌드업(`next_buildup_board`)이 모두 고정 덱을 따른다.
  - 상점 경로 가중(`rel`): 고정 덱 1.0, 나머지는 x `[comp] pin_other_rel`(0.5)이다. mini 예(3-2): 자이라 덱 고정 시 세주아니 0.31→0.36, 요릭 0.45→0.52, 베이가 0.93→0.74.
- **Jev · 캐시**
  - Jev state에 `user_pinned_comp: {comp: <후보 라벨>, note: "The player has locked this comp …"}`를 적는다.
  - 이 값이 state 해시에 들어가므로 고정/해제가 캐시 키를 가른다. 해제하면 예전 해시로 돌아가 캐시를 다시 쓴다.
  - 고정 중에는 덱 선택 질문(C4 `comp_pick`)을 묻지 않는다. 나머지 질문은 그대로 한 요청에 묶인다. 추가 호출은 없다.
- **화면별 동작**
  - `rescore_shop`: 고정 덱을 후보에 넣고, 직전 점수로 `rel`을 덮은 뒤 `apply_pin_rel`로 고정 가중을 다시 적용한다.
  - 전투·item_select 화면에서 고정이 바뀌면(`last.pinned_comp_id` ≠ 현재 고정) Jev 없이(`use_jev=False`) 목표 덱·아이템·보드 배치를 다시 계산한다. 상점은 직전 값을 둔다. 이 추천은 "Jev 미사용"으로 표시되고, 다음 준비 단계에서 Jev로 다시 채운다.
  - carousel도 고정이 바뀌었으면 새로 계산한다.

### 14.4 전/후 (`--screenshot … --no-jev --no-overlay`)

**test.png**(2-6, 골드 31, 벤치 9/9 중 미확인 5):
- 목표 덱·상점·아이템은 변화가 없다.
- [보드 배치]에 한 줄이 더해졌다.
  ```
  판매 참고: 벤치 9/9 가득 참 · 팔 만한 확인 유닛이 없습니다 · 미확인 유닛 5기는 판단하지 않았습니다
  ```
- 확인된 벤치 유닛(자야 x2, 카밀, 아칼리)은 모두 1성 쌍이다(보드의 카밀·아칼리와 합쳐 2기). 2-6 레벨 5 확률에서 2성 가능성이 있으므로 지켰다. 상점의 자야 [구매]는 사는 즉시 2성이 되므로 자리가 모자라다고 하지 않는다.

**live3 2-2 준비.png**(1920x1080, 골드 7, 벤치 6기 전부 미확인):
- 판매 줄은 없다. 초반이고 압박이 없으며, 확인된 벤치 유닛도 없다.
- 전: 보드 이름 0기라 [보드 배치]가 없었다.
- 후: 이번 실행에서는 vision 쪽 동시 작업(`vision/units.py`·`bench_memory.py`, 내 변경 아님)으로 보드 이름 4기가 읽혀 [보드 배치]가 나온다(바루스★2·피들스틱·아칼리·세주아니, 교체 없음). 이 차이는 내 변경 때문이 아니다. 이름이 전무하고 직전 추천도 없는 한 장짜리 스크린샷이면 여전히 None이다.

### 14.5 테스트
- `tests/advisor/test_sell.py`(16개)
  - 판매가 = ledger 규칙, 이자 문구
  - 초반 벤치 가득(싱글 ≤ 2기, "9/9 가득 참", 미확인 안내, 벤치 칸 번호) / 초반 조용함
  - 쌍 유지(2코스트 33%) vs 5코스트 0% 쌍 판매 / 이름 미상은 절대 판매하지 않음
  - 이자 구간(28골드 + 알리스타 2 → "팔면 30골드 → 이자 +1")
  - 후반 최종 덱 밖(쌍·2성 포함) 판매, 최종 덱·라인업 유지, "이자 구간 50골드까지 1 남음"
  - 아이템 보유자("아이템 1개는 벤치로 돌아옵니다") / 중반 벤치 2성 유지
  - 상점 구매 자리 부족(사면 바로 합성되는 구매는 제외) / 끔 설정 · 리포트 줄 · 합쇼체
  - §14.2: 신뢰도 0.50 tracked → 계획 유지(`low_trust`, 문구, 미확인 자리, 판매 없음) / 보드 못 읽음 → 직전 계획 `stale` + "(직전)" / augment·carousel·combat이 직전 계획 유지 / 새 보드면 새 계획, `reset` 뒤 None
- `tests/advisor/test_pinned_comp.py`(9개)
  - 고정 → s03(2-1)·s04(4-1) 모두 1위, 근거 문구, comp_pick 없음, Jev state, 보드 배치 기준 덱
  - 해제 → 점수 순서, 후보 밖 덱(lunar-aphelios-kayle) 불러오기, reset이 해제
  - 모르는 덱 무시 + 로그 / 캐시 키(고정 시 호출 +1, 해제 시 캐시 재사용)
  - rescore_shop이 고정을 따름(자이라 덱 유닛 ↑, 베이가 ↓) / 전투 중 고정 → Jev 호출 없이 1위 교체·상점 유지 / 스레드 안전
- 전체: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -o addopts="" -q` → 1379 passed, 3 skipped, 1 xfailed, 6 failed.
  - 알려진 Windows 실패 4건: api_key 힌트, setup 권한 상자, credentials 0600 두 건.
  - `tests/app/test_board_wiring.py::test_live_loop_passes_the_board_read_to_the_session`: app-integrator가 작업 중인 `loop.py:652`가 가짜 advisor(`D`)의 `pinned_comp_id`를 읽는다. 테스트 더블에 속성이 없어서 실패한다(그쪽 작업).
  - `tests/test_vision_units.py::test_every_champion_in_the_set_is_used_at_least_once`: vision 동시 작업 중이다. 단독으로 다시 돌리면 통과한다.

### 14.6 남은 일 · 요청
- **app-integrator**
  - 오버레이에서 "판매:" 줄을 강조(WARN 색)할지 결정한다. 지금은 "교체:"만 강조된다.
  - `BoardPlan.stale`이면 섹션을 흐리게 보여 준다.
  - `test_board_wiring`의 가짜 advisor에 `pinned_comp_id`를 추가하거나, loop에서 `getattr(..., None)`을 쓴다.
- **vision-engineer**: 유닛별 출처(`UnitOnBoard.source`)가 있으면 §14.2의 저신뢰 계획을 vision 이름으로만 좁힐 수 있다.
- **튜닝 후보**
  - `[sell] pair_min_odds`, `early_max`, `bench_near_full`, `late_from_stage`
  - `[comp] pin_other_rel`: 0이면 고정 덱만 본다.

## 15. 추정 이름은 판단하지 않는다 — jev-strategist, 2026-09-25

**버그**: test.png 벤치 1을 vision이 "세주아니 (추정)"으로 읽는다. 라이브러리 닮음 하나로만 붙인 이름이다(`SlotName.corroborated=False`, 신뢰도 상한 0.75). 눈으로 보면 레오나일 가능성이 높다. 필드 임계값 0.6은 넘으므로 advisor가 확정 이름처럼 썼다. 그래서 [보드 배치]가 "벤치: … 세주아니 …", "판매: 세주아니 (+2골드) — 목표 덱·빌드업에 없습니다"를 보여 줬다. 같은 화면의 자야 2기도 0.75 추정 이름이었다. 이 때문에 상점 자야가 "확인 보유 2 → [구매]"(사면 2성)로 나왔다.

**규칙**: 유닛 신뢰도가 `[board_plan] min_confidence`(기본 0.8, 추정 상한 0.75보다 높다) 미만이면 **이름 미상과 똑같이** 다룬다.
- 판매 후보가 아니다. `[sell] min_confidence`(0.8)로 한 번 더 거른다.
- 벤치로 내리거나 보드에 올리지 않는다. 보드 쪽은 자리만 차지한다.
- 사본 수(1성 쌍 · 사면 2성), 상점 "보유 N", 목표 덱 보유·부족, Jev state `copies_owned*`·`unidentified_units`에 넣지 않는다.
- 문구에서는 미확인 수에 포함해 밝힌다.
  - "미확인 유닛 7기(추정 이름 3기 포함)는 판단하지 않았습니다"
  - "벤치 미확인 N기(추정 이름 M기 포함)는 …"
  - "이름 미상 N기(추정 이름 M기 포함)"

**신호**: `UnitOnBoard`에는 출처·뒷받침 플래그가 없다. `confidence`(vision `unit_conf` 또는 장부 min(body, 칸))만 넘어온다. 그래서 신뢰도로만 가른다.
- 확정으로 쓰는 경우: 뒷받침된 라이브러리 이름(≤ 0.92), 장부·수동(1.0, 자리가 정해진 칸 0.85), vision 집합 풀이 자리 미상(0.8).
- 한계 1: 뒷받침됐어도 신뢰도가 0.8 미만인 이름은 추정으로 빠진다. 예로 특성 풀이 `set_conf x 0.85`가 낮은 경우가 있다.
- 한계 2: 장부 유닛이 칸을 못 정한 자리(`SLOT_CONF_LOOSE` 0.6)에 붙으면 함께 빠진다.
- 둘 다 보수적인 쪽(판단하지 않음)으로 틀린다. **app-integrator 요청**: `UnitOnBoard.corroborated: bool | None`(또는 `name_source`)를 추가하면 이 기준을 "corroborated 또는 장부·수동"으로 바꿀 수 있다.

**구현**:
- `unit_status.owned_units(state, threshold, unit_threshold=None)`
  - `unit_threshold` 미만인 이름 있는 칸은 hidden으로 센다.
  - `OwnedUnits.board_guessed`·`bench_guessed`·`guessed`를 추가했고, `gap_text`에 "(추정 이름 N기 포함)"을 붙인다.
  - `units_knowledge`·`units_reason`에도 같은 인자를 추가했다.
  - `units_note`는 기본값이 `CONFIRMED_NAME_THRESHOLD`(0.8)다. 그래서 표시 문구의 "N기 반영"이 advisor가 실제로 쓴 수와 맞는다.
  - 인자를 넘기지 않은 `owned_units`·`units_reason`은 예전과 같다.
- `features.build_view(..., unit_min_conf)` → `View.unit_min_conf`. 엔진은 `[board_plan] min_confidence`를 넘긴다(`_view`, `rescore_shop`). 목표 덱·상점·Jev·보드 배치·판매가 모두 이 View를 쓴다.
- `board_plan.relaxed_view`(저신뢰 계획)도 `unit_min_conf`로 거른다.
- `sell.sell_advice`는 `[sell] min_confidence` 미만을 후보·사본 수에서 뺀다.
- `app/report.py units_lines`: 0.6 ≤ 신뢰도 < 0.8인 이름에 " (추정)"을 붙인다(인식 확인 창과 같은 표시).
- 설정: `config.BoardPlanWeights.min_confidence`, `config.SellWeights.min_confidence`, `weights.toml [board_plan]`·`[sell] min_confidence = 0.8`.

**전/후**(test.png, `--jev mock`):
- 비교 방법: 같은 GameState(보드 5기 0.85, 벤치 세주아니·자야·자야 0.75 추정, 아칼리·카밀 0.85, 미상 4)를 두 번 돌렸다. 한 번은 `min_confidence` 0.6(= 예전 동작), 한 번은 0.8이다.
- vision 동시 작업으로 실제 CLI 실행에서는 자야 2기가 이미 "이름 미상"으로 바뀌어 있었다. 그래서 인식을 고정하고 advisor만 비교했다. CLI에서도 판매 줄과 세주아니 벤치 표시는 똑같이 사라진다.
```
전:  벤치: 자야 · 자야 · 세주아니 · 카밀 · 아칼리
     판매: 세주아니 (+2골드)
     판매 이유: 세주아니 — 목표 덱·빌드업에 없습니다
     판매 참고: 벤치 9/9 가득 참 · 미확인 유닛 4기는 판단하지 않았습니다
     [상점] 1. [구매] 자야 0.56 · 빌드업 — … · 확인 보유 2
후:  벤치: 카밀 · 아칼리
     판매 참고: 벤치 9/9 가득 참 · 팔 만한 확인 유닛이 없습니다 · 미확인 유닛 7기(추정 이름 3기 포함)는 판단하지 않았습니다
     참고: 벤치 미확인 7기(추정 이름 3기 포함)는 판단하지 않았습니다 — 강한 유닛이면 직접 올려 주세요
     [상점] 1. [보류] 자야 0.41 · 빌드업 — … · 확인 보유 0
     목표 덱: (부분 확인 — 화면 인식 7기 반영 · 이름 미상 7기(추정 이름 3기 포함) · …)
     상태 줄: 벤치 9기: 1 세주아니 (추정) · … · 3 자야 (추정) · … · 8 자야 (추정) · 9 카밀
```
목표 덱 순위·보드 라인업·교체·아이템은 변하지 않았다. 상점 레오나의 경로 가중만 0.85에서 0.84로 바뀌었다.

**테스트**: `tests/advisor/test_guessed_units.py`(7개).
- 기본 임계값이 추정 상한보다 높다.
- 0.75 이름은 팔지 않고, 0.85 이름은 판다(미확인 문구 포함).
- 보드의 추정 이름은 교체·판매 대상이 아니다. 벤치의 추정 이름은 라인업에 들어가지 않는다.
- 확인 1 + 추정 1은 쌍이 아니다. 확인 2(0.85)는 쌍으로 지킨다.
- `owned_units`·`units_reason`·`units_note` 문구를 확인한다.
- 엔진 끝까지: 추정 세주아니 2기는 상점 "보유 2"가 아니고 목표 덱 보유도 아니다. 0.85 2기는 "보유 2"다.
- 리포트 "(추정)" 표시를 확인한다.

`test_board_trust.board_ok_bench_low`의 벤치 세주아니 0.7은 0.85로 올렸다. 이 fixture는 "확인 2기"를 뜻하므로 새 규칙에서는 0.8 이상이어야 한다.

전체 결과(수정 뒤): 1440 passed, 3 skipped, 2 xfailed, 4 failed. 새 규칙 때문에 실패했던 2건(위 fixture)은 고쳤다. 남은 4건은 알려진 Windows 4건이다(api_key 힌트, setup 권한 상자, credentials 0600 두 건). `tests/fixtures/states/*.json`에는 0.6~0.8 이름 유닛이 없어서 fixture 추천은 변하지 않는다.
