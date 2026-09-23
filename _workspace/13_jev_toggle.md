# 13 app-integrator: Jev 실시간 판단 체크박스 (설정 화면 + 실행 중 토글)

작성일: 2026-09-23 / 작성자: app-integrator / **커밋하지 않음** / **live Jev는 한 번도 부르지 않았다**
사용자 요청: *"앱에 옵션을 넣어 달라 — `--jev live`를 쓸지 말지 체크박스로"*
기준: `12_app_setup_dialog.md`(설정 화면), `06_app-integrator_phase4.md`(루프·오버레이·스레드), `config.py`(`[advisor] jev_backend`)
안전 제약(CLAUDE.md): TypeSafe 키는 코드가 정한 한 곳(`LiveJevBackend.key_present()`, 현재 환경변수 `TYPESAFE_API_KEY`)에서만 읽는다. 기본은 mock(네트워크·과금 없음).

---

## 0. 요약

1. 설정 화면의 Jev 콤보(mock/live/off)를 **체크박스**로 바꿨다: `☐ Jev 실시간 판단 사용 (TypeSafe API 과금, 요청당 약 $0.0005)`. 켬 = `live`, 끔 = `mock`.
2. `off`는 그 아래 **고급 체크박스** 한 줄로 남겼다: `☐ 고급: 추천에서 Jev를 빼기 — 통계·규칙만 사용(off)`. 백엔드 3가지가 모두 그대로 선택된다.
3. 체크 아래 **한 줄 안내**가 상태에 따라 바뀐다. 꺼져 있으면 "끄면 mock으로 돈다 — 네트워크·과금 없음, Jev 판단은 가짜 고정값이다(추천 자체는 통계·규칙으로 계속 나온다)."
4. `TYPESAFE_API_KEY`가 없으면 live 체크를 **켤 수 없다**(비활성 + 이유 표시). 설정에 이미 `live`가 적혀 있으면 체크는 켜진 채 두고 **끌 수는 있게** 한다(말없이 값을 바꾸지 않는다).
5. **실행 중 토글**: 오버레이 트레이/우클릭 메뉴에 체크 항목 **"Jev 실시간 판단 (과금)"**. 켜면 `live`, 끄면 `mock`으로 **재시작 없이** 바꾸고 `config/settings.toml`에 저장한다.
6. 교체는 **UI 스레드 밖**에서 하고(advisor 생성이 수백 ms), 실제 갈아 끼우기는 **추천 스레드 안에서** 한다. 교체만으로는 **Jev를 부르지 않는다** — 새 백엔드는 다음 추천부터 쓰이고, 그때까지 화면의 추천은 그대로다.
7. 우선순위 **CLI > 트레이 토글 > settings.toml**. `--jev/--no-jev`로 띄운 실행은 토글이 잠기고 메뉴에 이유가 붙는다.
8. 테스트 **43개 추가**(`tests/app/test_jev_toggle.py`). 전체 **898 passed, 2 failed, 4 skipped, 1 xfailed**. 실패 2건은 이전부터 있던 vision 건이다(12 보고서 §8, 이번 작업과 무관).

---

## 1. 설정 화면 (`app/setup_dialog.py`)

"추천·오버레이" 그룹이 이렇게 바뀌었다:

```
☐ Jev 실시간 판단 사용 (TypeSafe API 과금, 요청당 약 $0.0005)
   끄면 mock으로 돈다 — 네트워크·과금 없음, Jev 판단은 가짜 고정값이다(추천 자체는 통계·규칙으로 계속 나온다).
☐ 고급: 추천에서 Jev를 빼기 — 통계·규칙만 사용(off)
오버레이 불투명도 [0.85]   오버레이 글꼴 배율 [1.00]
```

**왜 체크박스 2개인가.** 사용자가 고르고 싶은 것은 "돈이 드는 실시간 판단을 쓸까"이고, 그건 예/아니오다. `off`는 성격이 다르다 — 백엔드 선택이 아니라 **추천 파이프라인에서 Jev 단계를 빼는 것**(`fallback_reason = jev_disabled`)이고, 평소에 쓸 일이 없다(주로 진단용). 그래서 기본 시선에는 체크 한 줄만 두고, `off`는 아래 고급 줄로 내렸다. 3-지 콤보로 되돌리지 않은 이유는 "live가 과금된다"는 사실이 콤보 항목 글자 안에 숨어 버리기 때문이다. 체크박스는 라벨에 비용을 적을 수 있고, 아래 안내 한 줄로 켬/끔의 뜻을 항상 보여 줄 수 있다.

매핑 규칙은 Qt 없이 테스트되는 순수 함수다(`app/setup.py`):

| 함수 | 내용 |
|------|------|
| `jev_checks(backend)` | `"live" → (True, False)` · `"off" → (False, True)` · `"mock" → (False, False)` |
| `jev_backend_from_checks(live, off)` | off가 이긴다 → `"off"`, 아니면 live면 `"live"`, 아니면 `"mock"` |
| `jev_key_present()` | 키가 있는가. `advisor.jev_client.LiveJevBackend.key_present()`를 그대로 쓴다 — 키 판정을 한 군데로 모았으므로 CLAUDE.md의 새 제약(환경변수 → OS 키체인 폴백)이 그 함수에 들어오면 체크박스·트레이 토글이 **고칠 것 없이** 따라온다 |

상태별 동작:

| 상황 | live 체크 | 안내문 |
|------|-----------|--------|
| 키 있음 | 켤 수 있다 | 켜면 "화면이 바뀔 때마다 TypeSafe Jev를 호출한다(과금)" |
| **키 없음** | **비활성** | "환경변수 TYPESAFE_API_KEY 가 없어 live를 켤 수 없다 — 키를 설정하고 앱을 다시 실행하라." |
| 키 없음 + 설정이 `live` | 켜진 채 **활성**(끌 수 있다) | 같은 안내. 한 번 끄면 다시 켤 수 없다 |
| off 체크 | 비활성 | "off — Jev를 아예 부르지 않는다(fallback_reason = jev_disabled)" |

콘솔 설정(`--no-overlay` / PySide6 없음)은 예전처럼 Jev를 묻지 않는다 — 설정 파일의 값을 그대로 다시 쓴다(화면 감지가 그 UI의 목적이다).

---

## 2. 실행 중 토글 (`app/jev_toggle.py` — 새 파일)

트레이 아이콘 메뉴(및 잠금 해제 시 창 우클릭 메뉴):

```
표시/숨기기      Ctrl+Shift+O
이동 잠금        Ctrl+Shift+L
위치 저장        Ctrl+S
─────────────────────────────
☑ Jev 실시간 판단 (과금)          ← 새로 생긴 체크 항목
설정(화면 자동 감지)…
불투명도 + / −
─────────────────────────────
종료             Ctrl+Q
```

`JevSwitcher.switch(name)` 한 번이 하는 일:

1. 막힌 경우인지 본다 — CLI 고정이면 거부, `live`인데 키가 없으면 거부(이유를 그대로 돌려준다).
2. `create_advisor(name, settings=...)`로 **새 advisor를 만든다**(통계 DB·정적 데이터 로드. 그래서 UI 스레드에서 하지 않는다).
3. `LiveLoop.set_advisor(new)` → `ThreadAdviceRunner.set_advisor(new)`. 실제 교체는 **추천 스레드가 다음 한 바퀴를 돌 때** 일어나고, 그 스레드가 옛 advisor를 닫는다(`Advisor`는 전용 이벤트 루프를 하나 쓰므로 만든 스레드에서 닫아야 한다). 계산 중인 호출은 끊지 않는다.
4. `save_settings({"advisor": {"jev_backend": name}})` — **설정 화면과 같은 저장 경로**(주석·순서 보존, 쓰기 전 검증, `settings.toml.bak` 백업). 저장이 실패해도 이번 실행에는 적용하고 상태줄에 그렇게 적는다.
5. 메모리의 `Settings.advisor.jev_backend`도 갱신한다(트레이에서 연 설정 화면이 지금 값을 보여 주도록).

**워밍업하지 않는다.** `live.warm_up()`은 앱 시작 때 한 번만 한다. 토글로 `live`를 켠 순간 Jev를 부르면 사용자가 의도하지 않은 과금이 생긴다 — 새 백엔드는 **다음 화면 변화(다음 추천)** 부터 쓰인다. 그 사이 오버레이에는 직전 추천이 그대로 남는다(`loop.last_recommendation`을 건드리지 않는다).

UI 쪽(`app/overlay.py`):
- 전환은 작업 스레드에서 돌고, 결과는 `jev_switched` **Qt 시그널(QueuedConnection)** 로 UI 스레드에 돌아온다. 상태줄의 `StatusInfo.backend`가 바뀌고(`패치 18.3 · live · …`), 트레이 풍선으로 결과 문장을 띄운다.
- 전환 중에는 상태줄 꼬리에 "Jev 전환 중… (live)", 실패하면 "Jev 전환 실패 — …"를 보여 준다(잠금 안내 문구는 기억해 뒀다가 되돌린다).
- 메뉴는 열 때마다 새로 만들어지고 트레이 메뉴는 한 번만 만들어지므로, 만들어 둔 체크 항목들을 목록으로 들고 있다가 백엔드가 바뀌면 함께 맞춘다(이미 지워진 메뉴는 걸러낸다).
- 트레이 **"설정"** 에서 Jev를 바꿔 저장하면 재시작 없이 그 값을 적용한다(파일은 대화상자가 이미 썼으므로 다시 쓰지 않는다). 화면 관련 설정은 여전히 재시작이 필요하고, 안내문을 "화면 설정은 다시 시작해야 적용된다"로 고쳤다.

콘솔 모드(`--no-overlay`)에는 토글이 없다(메뉴가 없다). 설정 파일이나 CLI로 고른다.

---

## 3. 우선순위: CLI > 트레이 토글 > settings.toml

| 실행 | 시작 백엔드 | 트레이 토글 |
|------|-------------|-------------|
| `python -m tft_advisor` | `[advisor] jev_backend`(기본 mock) | 쓸 수 있다. 바꾼 값을 `settings.toml`에 저장 → 다음 실행에도 유지 |
| `--jev live` / `--jev mock` / `--jev off` / `--no-jev` | CLI 값(이번 실행 고정) | **잠긴다.** 메뉴에 `Jev 실시간 판단 (과금) — CLI --jev live 로 고정`이라고 적고 비활성 |
| `TYPESAFE_API_KEY` 없음 | 그대로 | live로 켜는 것만 막힌다(`— TYPESAFE_API_KEY 없음`) |

CLI 플래그는 "이번 실행은 이걸로 돌려라"는 명시적 지시이므로 실행 중에 뒤집지 않는다(그리고 설정 파일도 건드리지 않는다). 플래그 없이 띄운 보통 실행에서만 토글이 산다 — 그게 사용자가 요청한 경로다. `--jev`를 준 채로 바꾸고 싶으면 플래그 없이 다시 띄우거나 설정 화면에서 바꾼다.

`jev_backend(args)`가 `"auto"`(= 플래그 없음)를 돌려주면 잠그지 않는다. `run_live()`가 그 판정을 `_run_overlay(..., jev_cli=...)`로 넘긴다.

---

## 4. 코드 변경

| 파일 | 변경 |
|------|------|
| `src/tft_advisor/app/jev_toggle.py` | **새 파일**(약 180줄). `JevSwitcher`(교체·저장·차단 규칙), `SwitchResult`, 메뉴 문구 |
| `src/tft_advisor/app/setup.py` | `jev_key_present()` · `jev_checks()` · `jev_backend_from_checks()` + 안내 문구 상수 |
| `src/tft_advisor/app/setup_dialog.py` | Jev 콤보 → 체크박스 2개 + 안내 라벨(`_sync_jev`), `current_choice`/`_load_settings_into_widgets` 갱신 |
| `src/tft_advisor/app/loop.py` | `LiveLoop.set_advisor()`, `ThreadAdviceRunner.set_advisor()`(추천 스레드 안에서 교체 + 옛 advisor 닫기, `advisor_swapped` 이벤트), `InlineAdviceRunner.set_advisor()`, `_close_advisor()` |
| `src/tft_advisor/app/overlay.py` | `jev=` 인자, 체크 메뉴 항목, `set_jev_live()` · `_on_jev_switched()` · `_sync_jev_actions()`, 상태줄 꼬리 관리, 설정 화면 Jev 값 즉시 적용 |
| `src/tft_advisor/app/live.py` | `_run_overlay(..., jev_cli=)`에서 `JevSwitcher`를 만들어 창에 붙인다 |
| `src/tft_advisor/__main__.py` | `--jev` 도움말·docstring에 우선순위 명시 |
| `config/settings.toml` | `jev_backend` 주석에 체크박스·트레이 토글·우선순위 |
| `tests/app/test_jev_toggle.py` | **새 파일**, 테스트 43개 |
| `tests/app/test_setup.py` | 기존 `test_dialog_overlay_and_jev_overrides`가 없어진 콤보 대신 체크박스를 쓰도록 2줄 수정(이번 UI 변경 때문) |

---

## 5. 테스트

`tests/app/test_jev_toggle.py` — 43개, 전부 헤드리스(`QT_QPA_PLATFORM=offscreen`). **live Jev 호출은 하나도 없다**: advisor는 가짜이고, 전환기의 `builder`/`saver`를 갈아 끼운다. 키가 필요한 검사는 `monkeypatch.setenv("TYPESAFE_API_KEY", …)`로 **있다고만** 만든다.

| 묶음 | 개수 | 내용 |
|------|------|------|
| 매핑 규칙 | 4 | 3가지 백엔드 ↔ 체크 왕복 · off 우선 |
| 설정 대화상자 | 12 | 체크 조합 3가지 저장 왕복(파일까지) · 저장값 → 체크 복원 3가지 · 키 없음 비활성 + 이유 · 키 있음 활성 · 키 없는데 설정이 live · off가 live를 잠금 · mock 안내문 |
| `JevSwitcher` | 10 | 생성·교체·저장 · **실제 `settings.toml` 저장**(주석 보존·`.bak`) · 교체 후 호출 0회, 다음 추천에서 1회 · 표시 중 추천 유지 · 키 없이 live 거부 · off는 키 불필요 · CLI 잠금 · 저장 실패해도 적용 · 생성 실패 시 옛 백엔드 유지 · 같은 값은 무동작 · 작업 스레드에서 실행 |
| 루프 교체 | 2 | `ThreadAdviceRunner`가 **자기 스레드에서** 교체하고 옛 advisor를 닫는다 · `LiveLoop.set_advisor`가 루프·러너를 모두 바꾼다 |
| 오버레이 | 6 | 체크 항목 존재 · 토글 → 백엔드·상태줄 변경(양방향) · CLI 잠금 표시 · 키 없음 표시 · 전환기 없으면 항목 없음 · 설정 화면 값 즉시 적용 |
| CLI 우선순위 | 9 | `jev_backend(args)` 4조합 · `run_live`가 플래그 있을 때만 잠금을 넘긴다 4조합 · `_run_overlay`가 전환기를 창에 붙인다 |

### 전체 결과

```
898 passed, 2 failed, 4 skipped, 1 xfailed  (2분 54초)
```

기준선(12 보고서)은 `855 passed, 2 failed, 4 skipped, 1 xfailed`였다. 늘어난 **43개가 이번에 추가한 것**이고(기존 테스트는 UI 변경에 맞춰 1개만 2줄 고쳤다), **새로 깨진 테스트는 없다**. 실패 2건은 `tests/test_vision_1080p.py`의 `unnamed-3.png`(PvE 전투를 planning으로 판별) — 이전부터 있던 vision 쪽 문제이고 사용자 캡처·라벨은 건드리지 않았다.

### 실제 배선 스모크(이 머신, 키 없음)

진짜 `create_advisor` + 진짜 `settings.toml` 저장으로 한 번 돌렸다(키가 없으므로 `off`로 전환):

- 메뉴: `Jev 실시간 판단 (과금) — TYPESAFE_API_KEY 없음` / 비활성 — 의도대로.
- `switch("off")` → 로그 `Jev 백엔드 교체: mock → off`(추천 스레드), `설정 저장: …/config/settings.toml`, `.bak` 생성, 다시 읽은 설정 `off`.
- 상태줄: `패치 ? · off · 추천 없음 · 갱신 없음`.
- 설정 대화상자(offscreen): 키 없음 → live 비활성 + 안내, off 체크 → `off`, 키 넣고 live 체크 → `live`.

---

## 6. 남은 것 / 다음 제안

1. 사용자가 `TYPESAFE_API_KEY`를 넣고 실제로 `live`를 켠 상태에서 한 판 돌려 **첫 호출 지연**(SDK import + TLS, 약 1.2s)이 다음 추천에서 얼마나 느껴지는지 확인. 필요하면 전환 직후 "다음 추천은 조금 느릴 수 있다"는 안내를 한 줄 넣는다(워밍업은 과금이므로 자동으로 하지 않는다).
2. 콘솔 모드(`--no-overlay`)에는 토글이 없다. 필요하면 키 입력(예: `j`) 방식을 붙일 수 있으나, 지금은 CLI·설정 파일로 충분하다고 봤다.
3. CLAUDE.md가 2026-09-23에 "환경변수 우선, 없으면 OS 키체인"으로 바뀌었다. 아직 코드에는 키체인 경로가 없다(`LiveJevBackend.key_present()`는 환경변수만 본다). 이 체크박스·토글은 그 함수만 보므로, 키체인이 들어오면 자동으로 같이 동작한다 — 다만 "키를 설정하고 앱을 다시 실행하라"는 안내 문구는 그때 설정 화면의 키 입력란을 가리키도록 고치는 게 좋다.
4. 오버레이에 이번 판 Jev 호출 횟수·누적 비용 추정을 표시하면 "과금"이 눈에 보인다(추천 로그 `logs/recommendations.jsonl`에 이미 재료가 있다).
