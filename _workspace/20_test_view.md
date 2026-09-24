# 20 app-integrator: 인식 확인 창 (test view)

작성일: 2026-09-23 / 작성자: app-integrator / 커밋하지 않음

사용자 요청: 보드에 어떤 유닛이 있는지, 벤치에 누가 있는지, 누가 어떤 아이템을 장착했는지, 쓰지 않은 아이템(아이템 벤치)이
무엇인지 실시간으로 보여 주는 작은 창. 인식이 맞는지 직접 대조하려는 용도입니다.

---

## 1. 사용법

### 켜는 방법 (우선순위: CLI > 트레이 토글 > 설정 파일)

| 방법 | 방법 설명 | 저장 여부 |
|---|---|---|
| `python -m tft_advisor --test-view` | 이번 실행을 창이 켜진 상태로 시작합니다 | 저장 안 함 |
| `python -m tft_advisor --no-test-view` | 설정이 켜져 있어도 이번 실행은 끈 채로 시작합니다 | 저장 안 함 |
| 오버레이 트레이(또는 잠금 해제 후 우클릭) 메뉴 **"인식 확인 창"** 체크 | 실행 중에 켜고 끕니다 | 플래그 없이 띄운 실행이면 `[ui] test_view`에 저장합니다. CLI 플래그를 준 실행에서는 메뉴 글자가 "인식 확인 창 (CLI 지정 — 이번 실행에만 적용)"으로 바뀌고 저장하지 않습니다 |
| `config/settings.toml` `[ui] test_view = true` | 기본값 | - |
| 설정 화면(`--setup` 또는 트레이 "설정") **"인식 확인 창 표시"** 체크박스 | `[ui] test_view`에 씁니다. 트레이에서 연 경우 재시작 없이 바로 적용합니다 | 저장 |

창의 **×** 버튼을 누르면 트레이 체크를 끈 것과 같습니다(체크도 같이 풀립니다).

### 모드별 명령

```
# 실시간 + 오버레이 + 인식 확인 창
python -m tft_advisor --test-view

# 실시간, 오버레이 없이 콘솔에 인식 확인 출력(내용이 바뀔 때만 출력)
python -m tft_advisor --live --no-overlay --test-view

# 스크린샷 1장: 콘솔에 인식 확인 출력 + 인식 확인 창(닫으면 종료)
python -m tft_advisor --screenshot "tests/fixtures/screens/raw/5-5 전투 전.png" --test-view

# 스크린샷 폴더: 이미지마다 콘솔 출력, 창에서 ←/→ 로 넘겨 봅니다
python -m tft_advisor --screenshot tests/fixtures/screens/raw --test-view

# 스크린샷, 창 없이 콘솔만(파이프 가능)
python -m tft_advisor --screenshot "tests/fixtures/screens/raw/5-5 전투 전.png" --test-view --no-overlay --no-jev | more
```

스크린샷 모드의 인식 확인은 **CLI `--test-view`로만** 켭니다(`[ui] test_view`를 켜 둬도 배치 실행이 창에서 멈추지 않게).

### 창 성질

- 테두리 없는 작은 창(폭 460px), 항상 위, **클릭 통과 아님** — 아무 곳이나 끌어서 옮깁니다.
- **포커스를 가져가지 않습니다**: `Qt.WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating`(Windows에서는 WS_EX_NOACTIVATE).
  창을 누르거나 끌어도 게임 창이 활성 상태를 잃지 않습니다. (스크린샷 모드는 일반 창이라 ←/→ 키를 받습니다.)
- 위치는 끌어 놓을 때마다 `_state/recog_window.json`에 저장하고 다음 실행에 복원합니다(`[overlay] remember_position`).
  처음에는 오버레이(기본 오른쪽 위)와 겹치지 않도록 화면 왼쪽 위에 뜹니다.
- 우클릭 메뉴: 위치 저장 / (스크린샷 여러 장) 이전·다음 장 / 닫기.
- 본문이 화면 높이의 85%를 넘으면 스크롤됩니다. 아래 줄은 1초마다 "프레임 hh:mm:ss (N초 전)"만 갱신합니다.

---

## 2. 창 목업 (실제 출력과 같은 문구 · 콘솔 출력도 동일)

```
+------------------------------------------------------------ 인식 확인 --[x]-+
| 준비  스테이지 3-2  레벨 6  골드 30  체력 70                                   |
| 인식 412ms · 읽은 묶음: board, hud, items                                      |
| 신뢰도 보드 0.70(추적) · 벤치 0.70(추적) · 아이템 0.95 · 판독 0.85             |
| [보드 3기] (이름 미상 1기 · 못 알아본 아이템 칸 1(보드+벤치))                  |
|   1행 4열  자야 ★2      무한의 대검            0.90  화면(특성 구속)          |
|   2행 2열  자이라 ★1    -                      0.85  장부                     |
|   4행 7열  이름 미상 ★1 워모그의 갑옷, ?       0.20  미상                     |
| [벤치 2/9] (이름 미상 1기)                                                     |
|   벤치 1   요릭 ★1      -                      0.85  장부                     |
|   벤치 2   (비어 있음)                                                         |
|   벤치 3   (비어 있음)                                                         |
|   벤치 4   (비어 있음)                                                         |
|   벤치 5   이름 미상 ★2 -                      0.20  미상                     |
|   벤치 6~9 (비어 있음)                                                         |
| [장착 아이템]                                                                  |
|   자야 (1행 4열): 무한의 대검                                                  |
|   이름 미상 (4행 7열): 워모그의 갑옷, ?                                        |
|   소유자 미상: 무한의 대검                                                     |
| [미사용 아이템]                                                                |
|   재료 2: B.F. 대검, 쇠사슬 조끼                                                |
|------------------------------------------------------------------------------|
| 프레임 20:59:12 (3초 전) · 드래그로 이동 · 우클릭 메뉴                        |
+------------------------------------------------------------------------------+
```

보드가 보이지 않는 화면(증강 선택·캐러셀·로딩 등)에서는 머리에 노란 글씨로
`보드가 보이지 않습니다 (화면: 증강 선택) — 아래는 마지막으로 읽은 값입니다`를 띄우고 표를 흐리게 그립니다.
보드 묶음을 이번 프레임에 다시 읽지 않았으면 `이번 프레임에서는 보드를 다시 읽지 않았습니다(직전 판독)`을 띄웁니다.
아직 아무것도 인식하지 않았으면 `아직 인식 결과가 없습니다 — 게임 화면을 기다리는 중…`입니다.

### 열 읽는 법

| 열 | 뜻 |
|---|---|
| 자리 | 보드 `N행 M열`(1행 = 내 쪽 맨 앞 줄, 계약 `hex` 0행), 벤치 `벤치 1~9`, 칸을 모르면 `자리 미상`/`벤치 ?` |
| 이름 ★ | 챔피언 한국어 이름(모르면 `이름 미상`, 빨강) + 성급(`★?` = 성급 모름) |
| 아이템 | 장착 아이템 이름. `?` = 아이콘은 있었는데 무엇인지 못 알아본 칸(`UnitSlot.item_count - len(items)`) |
| 신뢰도 | 유닛 신뢰도. `[vision] state_min_confidence`(0.6) 미만은 노랑 — advisor가 세지 않습니다 |
| 출처 | `화면`(vision이 칸에서 이름을 붙임, 괄호 = `name_source`: 특성 구속/특성+닮음/모델 비교/중복 배정/자리 미상) · `장부`(상점 구매 추적) · `수동`(사용자 입력) · `미상` |
| 머리 신뢰도 | `GameState.confidence` board/bench/items + 괄호 = `field_source`(화면/추적/수동) + 판독 = `BoardRead.confidence` |

---

## 3. 구현

| 파일 | 내용 |
|---|---|
| `src/tft_advisor/app/recog_view.py` (신규) | 순수 표시 모델: `RecogSnapshot`(LoopUpdate/스크린샷 1장 → 스냅숏), `build_view()` → `RecogView`(머리·알림·보드/벤치 `UnitRow`·장착/미사용 `ItemGroup`), `unit_source()`, `ConsolePrinter`(내용이 바뀔 때만 출력, 인식 시간·읽은 묶음 차이는 무시) |
| `src/tft_advisor/app/recog_window.py` (신규) | `RecogWindow`(PySide6, HTML 표, 내용이 같으면 다시 그리지 않음) · `RecogController`(트레이 토글·저장·× 버튼·메뉴 체크 동기화) · `show_snapshots()`(스크린샷 모드 창) |
| `app/loop.py` | `LoopUpdate.board_read`/`recog_ms` 추가. `step()`이 인식 시간을 재고 마지막 보드 판독을 기억(`last_board_read`, 새 판에 지움). `_emit()`이 advice가 아닌 갱신에 싣는다 — **추가 인식 없음** |
| `app/overlay.py` | `attach_recog()`, `_on_update_main`이 같은 갱신을 `recog.feed()`로 넘김(UI 스레드, 예외는 삼킴), 메뉴에 "인식 확인 창" 체크, 설정 화면 저장값 즉시 적용(`_apply_saved_recog`) |
| `app/live.py` | `run_live(test_view=)`, 콘솔 모드 `ConsolePrinter`, 오버레이 모드 `_make_recog()`(실패해도 오버레이는 뜸) |
| `app/screenshot.py` | `run_screenshot(test_view=, test_window=)`, `_one()`이 스냅숏 반환, `out is print`면 `ensure_utf8_stdio()` |
| `app/report.py` | `ensure_utf8_stdio()` — cp1252 파이프에서 한국어 출력 시 `UnicodeEncodeError`로 죽던 문제(`screenshot.py:64`) 수정. `__main__.main()` 맨 앞에서도 부름 |
| `__main__.py` | `--test-view` / `--no-test-view`(상호 배타, 기본 None) |
| `config.py`, `config/settings.toml` | `[ui] test_view = false` |
| `app/setup.py`, `app/setup_dialog.py` | `SetupChoice.test_view`(None이면 키를 건드리지 않음), "추천·오버레이" 묶음에 "인식 확인 창 표시" 체크박스 |

스레드: 루프(캡처 스레드) → `window.on_loop_update` → Qt 시그널(Queued) → UI 스레드 `_on_update_main` → `RecogController.feed` → `RecogWindow.show_snapshot`.
창은 UI 스레드에서만 만집니다. 렌더는 인식이 일어난 갱신(변화 기반, 최대 capture_fps)마다 HTML 문자열 1개를 만들고, 같으면 `setText`를 건너뜁니다.

### 이름 출처를 정하는 방법 (계약 변경 없음)

`UnitOnBoard`에는 칸별 출처 필드가 없습니다. 계약을 바꾸지 않고, 같은 자리(`hex`/`bench_slot`)의 `BoardRead` 칸과 대조합니다.

1. `UNKNOWN_UNIT_ID` → 미상
2. 같은 자리 판독 칸의 `unit_id`가 같다 → 화면(+ `name_source`)
3. 자리 미상 보드 유닛이 `BoardRead.unplaced`에 있다 → 화면(자리 미상)
4. 그 밖: `field_source` board/bench가 vision이면 화면, manual이면 수동, 나머지는 장부

스크린샷 모드에서는 장부가 비어 있어 이름이 있으면 모두 vision입니다. 제약: 실시간에서 이번 프레임에 보드 묶음을 안 읽었으면
직전 판독과 대조합니다(세션 병합이 쓰는 것과 같은 판독이라 어긋나지 않습니다).

**계약 제안(보류)**: 칸별 출처를 advisor·로그에서도 쓰고 싶어지면 `UnitOnBoard.name_source: FieldSource | None = None`을
추가하고 `unit_merge.merge_units`에서 채우는 것이 정석입니다. 이번에는 필요 없어 추가하지 않았습니다.

---

## 4. 테스트

`tests/app/test_recog_view.py` (신규 20개)
- 표시 모델: 자리·이름·성급·아이템·출처(화면/장부/미상)·못 읽은 아이템 `?`, 벤치 9칸(빈 칸 포함), 장착 소유자별/소유자 미상, 미사용 종류별,
  보드가 안 보이는 화면 알림, 값이 없을 때 "읽지 못했습니다", field_source 폴백·`unplaced`
- 루프: `LoopUpdate.board_read`/`recog_ms`가 인식 갱신에만 실림(advice 갱신에는 없음)
- 콘솔: 인식 시간만 바뀐 갱신은 다시 찍지 않음, `_run_console(test_view=True)` 출력
- Qt(offscreen): 창 렌더·같은 내용 재렌더 생략·위치 저장/복원, 포커스 안 가져감 플래그·클릭 통과 아님,
  컨트롤러 저장(CLI 지정 실행은 저장 안 함)·× 버튼, 오버레이 메뉴 체크 ↔ 창 동기화
- CLI 플래그·라우팅, `SetupChoice.updates()["ui"]`, cp1252 파이프 재설정
- 실제 캡처(`5-5 전투 전.png`) `--screenshot --test-view` 출력

전체: `PYTHONIOENCODING=utf-8 pytest` → **1112 passed, 3 skipped, 1 xfailed, 4 failed**
(4건은 기존 Windows 실패: API 키 문구, 0600 권한 2건, 설정 대화상자 권한 상자 — 이번 변경과 무관).

수동 확인: `--screenshot "…/5-5 전투 전.png" --test-view --no-overlay --no-jev | cat`가 cp1252 오류 없이 보드 9기·벤치 9칸·
장착 아이템 13개(소유자별 5묶음)·미사용(아이템 제거기, 재조합기)을 출력합니다. 이 캡처는 vision 이름 식별 결과가 아직 없어
18기 모두 `이름 미상`입니다(vision-engineer 19 작업 진행 중).

---

## 5. 다른 에이전트에게

- vision-engineer: `UnitSlot.name_source`, `item_count`, `BoardRead.unplaced`/`unresolved_items`/`confidence`를 읽습니다(수정 없음).
  속성 이름을 바꾸면 `recog_view.find_slot/unit_source/unit_row/build_view`를 같이 고쳐야 합니다.
- qa-validator: 창 문구는 합쇼체·명사형 라벨(15 규약). 실제 게임에서 창이 포커스를 가져가지 않는지(Windows 전체화면 창모드) 확인이 필요합니다.
