# 31 — 목표 덱 클릭 고정 · app-integrator

사용자 요청: 목표 덱을 클릭하면 그 덱 기준으로 모든 추천을 계산(고정), 같은 덱을 다시 클릭하면 고정 해제.

## 1. 누르는 곳 (3곳, 같은 상태를 공유)
- **오버레이 옆 "📌 목표 덱" 띠** (`app/deck_chooser.DeckChooser`): 별도의 작은 창. 항상 위 · 테두리 없음 ·
  `WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating` · 클릭 통과 아님. 오버레이 **왼쪽**, [목표 덱] 줄 높이쯤에
  세로 버튼(덱마다 하나: "1 처형자 카직스", 고정된 덱은 "📌 2 … 고정", 노란 강조). 왼쪽에 자리가 없으면 오른쪽.
  오버레이를 따라 움직이고 함께 숨는다. 목표 덱이 없으면 띠를 숨긴다.
- **인식 확인 창**: 머리줄 아래 "목표 덱 고정:" 가로 버튼 줄(`DeckButtons`, 같은 컨트롤러).
- **트레이/우클릭 메뉴** "목표 덱 고정 ▸": 1/2/3(체크 = 고정됨) + "고정 해제". 열 때마다 지금 목표 덱으로 다시 채운다.

### 왜 별도 창인가 (줄 영역만 마우스 받기 대신)
잠금 상태 오버레이는 `WS_EX_TRANSPARENT`(창 전체 클릭 통과)라 hit-test 메시지 자체를 받지 않는다. 줄 영역만 받으려면
클릭 통과를 끄고 WM_NCHITTEST를 가로채 줄 위치를 계산해야 하는데, 리치 텍스트 QLabel의 줄 위치는 글꼴·배율·줄바꿈마다
달라지고 계산이 틀리면 게임 클릭을 가로챈다. 종료 손잡이(24 보고 `QuitHandle`)와 같은 별도 창 방식이 플랫폼 차이 없이 튼튼하다.

## 2. 흐름 (스레드)
UI 스레드: 버튼 → `PinController.click(comp_id)` → `toggle_target`(같은 덱이면 None) → `LiveLoop.request_pin(comp_id, name)`
- 세션 기록 `SessionTracker.set_pinned_comp` → `_state/session.json`(`pinned_comp_id`, `pinned_comp_name`) 즉시 저장
- 추천 러너에 `set_pin(comp_id)` (값만 넘김)
- `last_state`가 있으면 `runner.submit(readvise_state(state))` — **새 캡처·인식 없음**. 전투·아이템 선택·unknown·캐러셀
  화면이면 준비 단계로 바꿔 계산(advisor 계약상 그 화면은 직전 추천 유지라). 표시는 기존 `_on_advice`가 지금 화면 기준
  kept_view로 거른다.

추천 스레드: advise/상점 재평가 직전에 `_PinSync.sync(advisor)` → `pinning.apply_pin(advisor, comp_id)` =
`advisor.set_pinned_comp(comp_id)`. advisor가 바뀌면(Jev 토글) 새 advisor에 다시 반영. 한 번도 고정하지 않았으면 부르지 않는다.
`set_pinned_comp`가 없는 advisor는 경고 로그 1회 후 무시(어댑터). jev-strategist의 `Advisor.set_pinned_comp`는 이미 들어와 있고
mock advisor로 확인: 3번째 덱 고정 → 그 덱이 1위 + `pinned_comp_id` 채워짐, 해제 → 원래 순서.

## 3. 표시
[목표 덱] 머리: `pinning.header_label` — 고정했고 추천이 그 기준(`rec.pinned_comp_id == 고정`)이면 "📌 사용자 고정",
요청했지만 아직 그 기준 추천이 오지 않았으면 "📌 고정 적용 중…"(advisor가 통계에 없는 덱이라 무시한 경우도 이 표시로 남는다).
상태줄에 "목표 덱 고정: <이름>" / "목표 덱 고정 해제". 고정한 덱이 목표 덱 목록 밖이면 띠 맨 아래에 해제용 버튼을 붙인다.

## 4. 영속·초기화
- 앱을 판 중간에 다시 켜면 `LiveLoop.__init__` → `_restore_pin()`이 세션 값을 러너에 넘기고, `live.make_pin`이 그 값으로
  컨트롤러를 시작한다(첫 추천부터 반영).
- 새 판(`_do_reset`): `tracker.reset()`이 세션을 비우고 러너 고정도 None, 오버레이는 reset 갱신에서 `pin.clear()`.

## 5. 계약
`Recommendation.pinned_comp_id: str | None = None` 추가(선택 필드, contracts.py). UI는 `getattr(rec, "pinned_comp_id", None)`으로 읽는다.
영향 모듈: advisor(채움 — 이미 반영), app(읽음).

## 6. 파일
- 새: `src/tft_advisor/app/pinning.py`(Qt 없음: 어댑터·토글·버튼 목록·머리 표시), `src/tft_advisor/app/deck_chooser.py`(Qt),
  `tests/app/test_pin_target_deck.py`(11개)
- 수정: `contracts.py`(필드 1), `app/session.py`(SessionData 2필드 + `set_pinned_comp`), `app/loop.py`(`_PinSync`, 러너 `set_pin`,
  `LiveLoop.request_pin/_restore_pin/_send_pin/pinned_*`, `_do_reset` 한 줄 — `_groups`·구매 경로는 건드리지 않음),
  `app/overlay.py`(attach_pin·띠 배치·머리 표시·메뉴), `app/recog_window.py`(버튼 줄), `app/live.py`(`make_pin` 배선)
- report.py·ledger.py 수정 없음.

## 7. 테스트
`PYTHONIOENCODING=utf-8 pytest`: 신규 11개 통과. 전체에서 실패는 알려진 Windows 4건 + `tests/test_vision_units.py::test_every_champion_in_the_set_is_used_at_least_once`
(vision-engineer가 작업 중인 파일 — 이번 변경과 무관).

## 8. 남은 것
- 실제 게임 위에서 띠 창이 포커스를 뺏지 않는지(24의 종료 손잡이와 같은 플래그라 같은 동작 예상) 사용자 확인 필요.
- 목표 덱 이름이 길면 8자에서 "…"로 자른다(툴팁에 전체 이름).

---

## 9. 후속 수정 — 장부 이름을 칸에 순서대로 붙이던 문제 (live3 "벤치 2 카밀(장부)")
원인: `unit_merge._assign`이 이름 없는 vision 칸에 장부 유닛을 "성급 맞는 것 → 남은 순서"로 붙였다. 장부는 **누구를** 가졌는지는
알지만 **어느 칸**인지는 모른다.

수정(`app/unit_merge.py`, 모듈 docstring 규칙 2): 장부 이름은 **유일하게 정해질 때만** 칸에 붙인다.
- 남은 칸 수 = 남은 장부 유닛 수이고 장부 유닛이 전부 같은 챔피언(1칸·1기 포함)
- vision ★s(s≥2) 칸 수 = 장부 ★s 유닛 수이고 그 유닛들이 한 챔피언(예: 사본 3개 이상이 아칼리뿐 → ★2 칸 = 아칼리)
- ★1도 같은 조건이되 남은 칸의 성급을 전부 읽었을 때만
- 하나를 정하면 남은 칸으로 다시 확인(★2 확정 → 남은 1칸·1기 확정)
- 구매 증거(수집기·구매 칸)로 자리를 정하는 규칙은 넣지 않았다 — 장부 `Body`에 칸 정보가 없다. 벤치 기억(vision `bench_memory`)이 구매 칸을 주면 추가할 수 있다.

정해지지 않은 장부 유닛은 **자리 미상** `UnitOnBoard`(hex/bench_slot None, 장부 성급, 아이템 없음)로 내보내고, 그만큼 이름 없는 칸이
목록에서 빠진다(개수 = vision 개수 그대로). 장착 아이템은 `items.equipped`에 소유자 없이 남는다.
- **계약 변경 없음**: 자리 미상 유닛은 기존 규칙 5·7과 같은 표현이다. advisor `unit_status.owned_units`/`relaxed_view`는 이름·신뢰도만
  보므로 보유 유닛으로 그대로 센다(테스트로 확인). 판매 추천은 `bench_slot=None`인 `SellAdvice`가 된다(칸 번호 없이 "판매: 카밀").
- 한계: 이름 없는 칸이 보드·벤치 양쪽에 있으면 자리 미상 유닛의 **쪽**(보드/벤치)은 칸 순서(보드 먼저)로 나눈 추정이다. 한쪽에만 있으면 정확하다.
  → jev-strategist 참고: 보드 배치·판매는 `hex`/`bench_slot`이 None인 장부 유닛을 "칸 모름"으로 다뤄야 한다(현재도 그렇게 동작).
- `MergeResult.unplaced`(자리 미상 수), note "장부 보유 자리 미상 N기".

인식 확인 창(`app/recog_view.py`): 판독이 있으면 상태에 없는 판독 칸을 "이름 미상"(판독의 성급·아이템)으로 그리고, 자리 미상 장부 유닛은
`RecogView.ledger_note` = "장부 보유(자리 미상): 쉔 · 카밀 · 라칸" 한 줄로 보인다(창·콘솔 둘 다). 판독이 없으면(장부만) 예전처럼 "벤치 ?" 행.

## 10. 오버레이 보드 배치 표시
- "판매:" 줄은 WARN(노랑) — "교체:"와 같은 강조.
- `plan.stale`이면 섹션 머리 "[보드 배치] (직전)"(첫 줄에도 report가 "(직전)")이고 모든 줄을 `STALE`(더 흐린 회색)로 그린다. 판매 강조도 끈다.

## 11. 확인
- `tests/app/test_board_wiring.py::test_live_loop_passes_the_board_read_to_the_session`: 합쳐진 트리(vision-engineer의 `_note_unit_change`/강제 재판독 +
  목표 덱 고정)에서 통과. 앞서 실패는 가짜 tracker에 `data.pinned_comp_id`가 없어서였고 `getattr`로 고쳤다.
- 새 `tests/app/test_ledger_placement.py` 8개(live3 시나리오, ★2 확정, 연쇄 확정, ★1 조건, 인식 확인 창, 오버레이 색, ID 안 지어냄).
- 전체 `PYTHONIOENCODING=utf-8 pytest`: 실패는 알려진 Windows 4건뿐.
