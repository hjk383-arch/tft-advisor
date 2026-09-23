# 14 app-integrator: TypeSafe API 키 보관소 (환경변수 → OS 키체인 → 폴백 파일) + 설정 화면 키 입력란

작성일: 2026-09-23 / 작성자: app-integrator / **커밋하지 않음** / **live Jev 호출: 딱 1회**(§7)
사용자 요청: *"개발자가 아닌 친구가 **자기 키**를 앱 안에서 넣을 수 있어야 한다. 환경변수를 만질 일도, 내 키를 넘길 일도 없어야 한다."*
기준: `CLAUDE.md` 고정 제약(2026-09-23 변경 이력) · `12_app_setup_dialog.md`(설정 화면) · `13_jev_toggle.md`(Jev 체크박스·트레이 토글)
안전 제약: **키는 저장소 안에 절대 두지 않는다.** 이 저장소는 공개다.

---

## 0. 요약

1. **키 보관소 모듈**을 새로 만들었다: `src/tft_advisor/credentials.py`. 앱에서 키를 읽는 길은 이제 이 모듈 하나뿐이다.
   해석 순서는 **환경변수 `TYPESAFE_API_KEY` → OS 키체인 → 폴백 파일 → 없음**.
2. **OS 키체인**은 `keyring` 패키지로 감싼다 — macOS Keychain / Windows 자격 증명 관리자 / (리눅스) Secret Service.
   키체인을 쓸 수 없는 환경에서만 **`~/.config/tft-advisor/credentials.toml`**(파일 `0600`, 디렉터리 `0700`)에 쓴다.
   **저장소 안(`config/`, `data/`, `_state/`, `logs/`, `_workspace/`)에는 아무것도 쓰지 않는다.**
3. **설정 화면에 [TypeSafe API 키] 칸**을 넣었다: 가려진 입력란(+표시/숨기기) · **[연결 테스트]**(진짜 호출 1회, 성공/실패 이유를 한국어로) · **[저장]**(어느 보관소에 넣었는지 말해 준다) · **[삭제]**.
   저장한 뒤로는 키를 **다시 보여 주지 않는다** — 가린 힌트 `sk-…abcd`만 보인다.
4. 환경변수가 설정돼 있으면 **그 사실과 우선순위를 화면에 적는다**(주황). 말없이 가리지 않는다.
5. `LiveJevBackend.key_present()`와 **실제 키 읽기**가 모두 이 모듈을 거치므로, 13번 보고서의 체크박스·트레이 토글은
   **고친 것 없이** 키체인 키를 따라온다. 키를 저장하면 그 자리에서 "Jev 실시간 판단 사용" 체크가 살아난다(재실행 불필요).
6. 테스트 **69개 추가**(`tests/test_credentials.py` 39, `tests/app/test_api_key.py` 30). 전체 **967 passed, 2 failed, 4 skipped, 1 xfailed**.
   실패 2건은 이전부터 있던 vision 건(`unnamed-3.png`)이고 이번 작업과 무관하다.

---

## 1. 친구가 하는 일 (한 번만)

```
1. typesafe.ai 에서 자기 API 키를 만든다.            ← 자기 키다. 남의 키를 받지 않는다.
2. python -m tft_advisor --setup                     (첫 실행이면 저절로 열린다)
3. [TypeSafe API 키] 칸에 키를 붙여 넣는다.          입력칸은 ●●●●로 가려져 있다([표시]로 확인 가능)
4. [연결 테스트]  → "성공 — TypeSafe에 연결했고 키가 유효하다 (711ms)."
5. [저장]         → "macOS 키체인에 저장했다 — sk-…abcd"   (입력칸이 비워진다)
6. ☑ Jev 실시간 판단 사용     ← 방금 살아난 체크박스
7. [저장 후 시작]
```

- 환경변수를 만질 일이 없다. 터미널도, 설정 파일도 건드리지 않는다.
- 키는 **그 컴퓨터의 OS 키체인**에만 들어간다. 프로그램 폴더·설정 파일·로그·보고서에는 들어가지 않는다.
- 다음 실행부터는 4~6번도 필요 없다 — 키체인에서 저절로 읽는다.
- 그만 쓰고 싶으면 설정 화면에서 **[삭제]** 한 번.

화면에 적어 둔 안내 문구(`setup.KEY_FRIEND_NOTE`):

> Jev 실시간 판단을 쓰려면 **자기 TypeSafe API 키**가 필요하다(남의 키를 받아 쓰지 않는다).
> typesafe.ai 에서 키를 만들어 아래에 붙여 넣고 [저장]을 누르면, 키는 **이 컴퓨터의 OS 키체인**에만 들어간다 —
> 프로그램 폴더·설정 파일·로그에는 저장되지 않고, 저장한 뒤로는 다시 보이지 않는다(가린 힌트만 보인다).

---

## 2. 보관소 설계 (`src/tft_advisor/credentials.py`)

### 2.1 해석 순서

| 순위 | 출처 | 어디에 | 누가 넣나 |
|------|------|--------|-----------|
| 1 | 환경변수 `TYPESAFE_API_KEY` | 셸·CI | 셸에서 명시적으로 준 값이므로 **무조건 이긴다** |
| 2 | **OS 키체인** | macOS Keychain / Windows 자격 증명 관리자 / Secret Service. 항목 이름 `tft-advisor` / `typesafe-api-key` | 설정 화면 [저장] |
| 3 | 폴백 파일 | `~/.config/tft-advisor/credentials.toml` (`0600`, 디렉터리 `0700`) | 키체인 백엔드가 없을 때만 설정 화면 [저장] |
| 4 | 없음 | — | live를 켤 수 없다 |

환경변수를 1순위로 둔 이유: 이미 그렇게 쓰던 실행(개발·CI)을 깨지 않기 위해서다. 또 "셸에서 준 값"은
사람이 방금 명시적으로 지정한 것이므로, 앱이 저장해 둔 값이 그것을 말없이 덮으면 놀라게 된다.
대신 **설정 화면이 그 사실을 표시한다**(§3).

### 2.2 함수 (앱이 쓰는 면)

| 함수 | 하는 일 |
|------|---------|
| `resolve_api_key() -> str \| None` | **키 값을 얻는 유일한 함수.** 위 순서대로 찾는다 |
| `key_present() -> bool` | 키가 어디에든 있는가. 체크박스·트레이 토글·`create_advisor()`가 보는 값 |
| `key_info() -> KeyInfo` | **출처와 가린 힌트만**. 값은 담지 않는다(화면·로그용). `describe()`가 한 줄 문장을 준다 |
| `save_api_key(key) -> SaveResult` | 키체인 우선, 실패하면 폴백 파일. `store`(`keyring`/`file`)와 사람이 읽을 메시지를 돌려준다 |
| `delete_api_key() -> DeleteResult` | 키체인·파일 **양쪽** 삭제. 환경변수는 건드리지 않는다(셸/OS의 몫) |
| `verify_key(key=None, *, caller=None) -> VerifyResult` | 연결 테스트. `caller`를 주면 그것을 대신 부른다(테스트 주입점) |
| `mask(key) -> str` | `sk-…abcd`. **이 함수를 거치지 않은 키 값은 화면·로그에 나가지 않는다** |
| `stored_key_present()` | 환경변수를 뺀, 이 앱이 저장해 둔 키가 있는가([삭제] 버튼 활성화용) |
| `refresh()` | 프로세스 안 캐시를 비운다 |

### 2.3 결정과 이유

- **`keyring` 패키지.** macOS Keychain(Security.framework)과 Windows 자격 증명 관리자를 같은 API로 덮고,
  둘 다 OS가 암호화·접근 제어를 해 준다. 직접 `security` CLI를 부르면 macOS 전용이 되고 값이 프로세스 인자로 새어 나간다.
  `pyproject.toml`의 `advisor` extra에 넣었고(`keyring>=25.6`), 키 저장만 따로 깔 수 있게 `keyring` extra도 뒀다.
- **폴백 파일은 홈에만.** `~/.config/tft-advisor/`(또는 `XDG_CONFIG_HOME`). 저장소 안이 아니다 —
  이 저장소는 공개고, `config/`·`data/`·`_state/`는 커밋되거나 리포트에 실릴 수 있다. 테스트가 이 위치를 못박는다(§6).
- **원자적 쓰기 + 권한.** 임시 파일을 `os.open(..., 0o600)`으로 만들어 쓰고 `os.replace`로 바꿔 끼운다.
  중간에 죽어도 반쯤 쓰인 파일이 남지 않고, 세상에 잠깐이라도 `0644` 파일이 존재하지 않는다. 디렉터리는 `0700`.
- **프로세스 안 캐시 1개.** macOS는 키체인 항목을 읽을 때 허가를 물을 수 있다. `key_present()`는 설정 화면·메뉴가
  자주 부르므로, 한 번 읽은 값을 메모리에 들고 있는다(`refresh()`로 비우고, 저장·삭제가 저절로 비운다).
  환경변수는 매번 새로 읽는다(싸고, 실행 중에 바뀔 수 있다).
- **키체인 실패는 조용히 내려간다.** 잠겨 있거나 사용자가 거부하면 경고 로그 한 줄만 남기고 폴백 파일 → 없음으로 간다.
  앱은 죽지 않는다(에러 핸들링 원칙).
- **값은 어디에도 안 남긴다.** 로그·예외 메시지·`KeyInfo.__repr__`·`JevAnswers.to_debug()` 어디에도 키가 없다.
  `verify_key` 실패 로그는 **이유 코드와 예외 타입만** 남긴다.

### 2.4 SDK에 넘기는 길

예전에는 `AsyncTypeSafeClient(...)`에 키를 넘기지 않고 **SDK가 환경변수를 직접 읽게** 뒀다. 이제는 넘긴다:

```python
# advisor/jev_client.py — LiveJevBackend._make_client()
return AsyncTypeSafeClient(api_key=credentials.resolve_api_key(), model=..., retry=..., timeout=...)
```

환경변수만 있을 때 넘어가는 값은 예전과 같으므로 동작이 바뀌지 않고, 키체인·폴백 파일의 키도 같은 길로 흐른다.
`LiveJevBackend.key_present()`도 `credentials.key_present()`로 바뀌었다 — **13번 보고서가 예상한 그대로**,
설정 체크박스와 트레이 토글은 한 줄도 고치지 않고 키체인을 따라왔다.

---

## 3. 설정 화면의 키 칸 (`app/setup_dialog.py`)

"게임 화면 영역" 아래, "추천·오버레이"(Jev 체크박스) **바로 위**에 놓았다 — 키가 있어야 그 아래 체크가 살아나므로
읽는 순서와 원인·결과 순서가 같다.

```
┌ TypeSafe API 키 — 자기 키를 넣으면 이 컴퓨터의 OS 키체인에 저장된다 ─────────────┐
│ Jev 실시간 판단을 쓰려면 자기 TypeSafe API 키가 필요하다 … (안내 3줄)           │
│ macOS 키체인에 저장돼 있다 — sk-…abcd                        ← 상태(초록/주황/회색) │
│ [●●●●●●●●●●●●●●●●●●●●●●●●●●●●●]  [표시]                                        │
│ [연결 테스트]  [저장]  [삭제]                                                    │
│ 성공 — TypeSafe에 연결했고 키가 유효하다 (711ms).            ← 결과(초록/빨강)   │
└──────────────────────────────────────────────────────────────────────────────┘
```

| 상황 | 상태 줄 | Jev live 체크 |
|------|---------|---------------|
| 키 없음 | 회색 "저장된 TypeSafe API 키가 없다." | **비활성** + "위 [TypeSafe API 키] 칸에 자기 키를 넣고 [저장]을 누르라" |
| 키체인에 있음 | 초록 "macOS 키체인에 저장돼 있다 — sk-…abcd" | 활성 |
| 폴백 파일 | 초록 "폴백 파일에 저장돼 있다 — sk-…abcd (`~/.config/…`)" | 활성 |
| **환경변수** | **주황** "환경변수 TYPESAFE_API_KEY 의 키를 쓰고 있다 — sk-…abcd (환경변수가 가장 먼저 쓰인다)" + "여기서 저장한 키는 환경변수를 지워야 쓰인다." | 활성 |

동작 규칙:

- **입력칸은 `QLineEdit.Password`.** [표시]/[숨기기]는 **지금 입력 중인 글자**에만 해당한다.
  저장된 키는 어느 쪽이든 보이지 않는다 — 앱은 저장한 키를 화면에 되돌려 주지 않는다.
- **[저장]** → 성공하면 입력칸을 비우고 [표시] 토글도 되돌린다. 메시지에 **어느 보관소**인지 적는다
  ("macOS 키체인에 저장했다" / "macOS 키체인을 쓸 수 없어 파일에 저장했다(권한 0600) — `경로`").
  환경변수가 있는 채로 저장하면 초록이 아니라 **주황**으로 "다만 지금은 환경변수 TYPESAFE_API_KEY 가 먼저 쓰인다"를 붙인다.
- **[삭제]** → 키체인·폴백 파일 양쪽에서 지운다. 환경변수가 남아 있으면 "환경변수는 그대로다 — 셸에서 지우라"고 말한다.
- **[연결 테스트]** → §4.
- 빈 칸에 저장·공백이 섞인 키는 거부하고 이유를 적는다(복사할 때 줄바꿈이 섞이는 흔한 실수).
- 저장/삭제 뒤 `refresh_key_row()`가 상태·버튼·**Jev 체크박스**를 한 번에 맞춘다 → 요구 3번(체크박스 연동)이
  별도 배선 없이 따라온다. 앱을 다시 띄울 필요가 없다.

**콘솔 설정**(`--no-overlay` / PySide6 없음)에는 입력란이 없다. 대신 첫 줄에 키 상태 한 줄을 찍는다:
`TypeSafe API 키: 없다 — python -m tft_advisor --setup 의 설정 화면에서 넣거나, 환경변수 TYPESAFE_API_KEY 를 설정하라.`
(콘솔에서 키를 받으면 셸 히스토리·화면 스크롤백에 남는다. 그래서 받지 않는다.)

---

## 4. 연결 테스트

한 번 누르면 **Jev를 딱 한 번** 부른다. 요청은 일부러 가장 싸게 만들었다 — state 두 줄, 질문 1개, 레벨 2개
(`credentials.PING_STATE` / `PING_QUESTIONS`), 재시도 없음(`RetryPolicy(max_retries=0)`), 타임아웃 15초.

입력칸이 비어 있으면 **지금 저장된 키**로 테스트한다(저장한 키가 아직 살아 있는지 확인하는 용도).

실패 이유는 `advisor/jev_client.classify_exception()`(§8.1 판정 순서)을 **그대로 재사용**해서 문장으로 옮긴다 —
판정 규칙이 두 군데로 갈라지지 않는다.

| 이유 | 화면 문장 |
|------|-----------|
| `auth` | 실패 — 키가 거부됐다 — 키가 잘못됐거나 만료됐다. TypeSafe 대시보드에서 다시 복사해 보라. |
| `quota` | 실패 — 요청 한도(쿼터·rate limit)에 걸렸다 — **키는 유효하다.** 잠시 뒤 다시 시도하라. |
| `network` | 실패 — 네트워크에 연결하지 못했다 — 인터넷·방화벽·프록시를 확인하라. |
| `timeout` | 실패 — 응답이 제때 오지 않았다 — 네트워크가 느리거나 서버가 붐빈다. |
| `server` | 실패 — TypeSafe 서버 쪽 오류다 — **키 문제가 아니다.** |
| `sdk` | typesafe_sdk 가 설치돼 있지 않다 — `pip install -e ".[advisor]"` |
| `empty` | 테스트할 키가 없다 — 키를 입력하거나 저장한 뒤 누르라. |

**주입 가능**: `verify_key(key, caller=…)`의 `caller`가 진짜 호출을 대신한다. 설정 대화상자는
`SetupDialog(key_verifier=…)`로 그것을 받아 그대로 넘긴다 → **테스트에서 진짜 호출이 일어날 수 없다.**

---

## 5. 코드 변경

| 파일 | 변경 |
|------|------|
| `src/tft_advisor/credentials.py` | **새 파일**(약 400줄). 보관소 전부 |
| `src/tft_advisor/advisor/jev_client.py` | `key_present()` → `credentials.key_present()`, `_make_client()`이 `api_key=`를 넘긴다, 문서 갱신 |
| `src/tft_advisor/app/setup.py` | `jev_key_present()` → `credentials`, `JEV_NO_KEY_NOTE`가 키 칸을 가리킨다, `KEY_GROUP_TITLE`·`KEY_FRIEND_NOTE`·`KEY_ENV_NOTE`, `key_status_line()`(콘솔) |
| `src/tft_advisor/app/setup_dialog.py` | **[TypeSafe API 키] 그룹**(입력칸·표시 토글·연결 테스트·저장·삭제·상태·결과), `refresh_key_row()`·`save_key()`·`delete_key()`·`test_key()`, `creds=`/`key_verifier=` 주입 인자, `_sync_jev()`가 주입된 보관소를 본다 |
| `src/tft_advisor/app/overlay.py` | 트레이 메뉴 문구 `— TypeSafe API 키 없음 (설정에서 입력)` (예전엔 `TYPESAFE_API_KEY 없음`) |
| `src/tft_advisor/app/jev_toggle.py` · `advisor/engine.py` · `advisor/__init__.py` · `__main__.py` | 문서·경고 문구를 새 해석 순서로 |
| `pyproject.toml` | `advisor` extra에 `keyring>=25.6`, 별도 `keyring` extra |
| `config/settings.toml` | 머리말: 키는 여기 두지 않는다 + 새 순서 |
| `.gitignore` | `credentials.toml` (실수로 들어와도 커밋되지 않게 하는 안전망) |
| `tests/conftest.py` | **새 파일**. autouse로 **모든 테스트**의 키체인·홈 디렉터리를 격리한다 |
| `tests/test_credentials.py` · `tests/app/test_api_key.py` | **새 파일**, 69개 |
| `tests/app/test_jev_toggle.py` | 트레이 메뉴 문구가 바뀐 줄 1개 수정 |

---

## 6. 테스트 (69개 추가)

**진짜 키체인도, 진짜 홈 디렉터리도, 진짜 Jev도 쓰지 않는다.**
`tests/conftest.py`의 autouse 픽스처가 **모든** 테스트에 `TFT_ADVISOR_KEYRING=0`과 임시 `TFT_ADVISOR_CONFIG_HOME`을
걸어 둔다 — 테스트가 사용자의 macOS 키체인을 열거나 허가 창을 띄우는 일이 구조적으로 불가능하다.

### `tests/test_credentials.py` — 39개

| 묶음 | 개수 | 내용 |
|------|------|------|
| 해석 순서 | 7 | 없음 · **env > keyring** · **keyring > file** · **file > none** · 순서 상수 · 공백만 있는 값은 없는 것 · 앞뒤 공백 제거 |
| 키체인 | 8 | 저장→읽기 왕복 · 저장 메시지가 보관소를 밝힌다 · 삭제 · 지울 것 없음 · **삭제가 환경변수를 건드리지 않는다** · 읽기 실패 시 조용히 폴백 · 쓰기 실패 시 파일로 · 스위치로 끄기 |
| 캐시 | 1 | 두 번째 읽기는 키체인을 다시 묻지 않는다, `refresh()`가 비운다 |
| 폴백 파일 | 7 | **저장소 밖**(`config`·`data`·`_state`·`_workspace`·`logs` 어디에도 없음) · **0600 파일 / 0700 디렉터리** · 덮어써도 권한 유지 · 삭제 · 깨진 파일에도 안 죽는다 · 따옴표·백슬래시 왕복 · `XDG_CONFIG_HOME` |
| 가리기 | 3 | `sk-…abcd` · 짧은 키 · `KeyInfo`에 값이 없다 |
| 연결 테스트 | 8 | 정확히 1회 호출 · 저장된 키 사용 · 키 없음 · **이유 5가지**(auth/quota/network/timeout/server) · 실패 메시지·로그에 키 없음 · SDK 없음 · 질문이 1개·레벨 2개인지 |
| 유출 금지 | 2 | §6.1 |
| 거부 | 1 | 빈 값·공백 섞인 키 |

### `tests/app/test_api_key.py` — 30개 (헤드리스 Qt)

| 묶음 | 개수 | 내용 |
|------|------|------|
| 입력칸 | 4 | 기본이 `Password` · 표시/숨기기 토글 · 버튼 활성화 규칙 · 저장된 키가 있을 때 [삭제] 활성 |
| 상태 줄 | 4 | 키 없음 · 키체인(가린 힌트만, 원문 없음) · 폴백 파일 · **환경변수 우선 표시** |
| 저장/삭제 | 6 | 저장 + 보관소 이름 · **저장 뒤 어느 위젯에도 키 원문이 없다** · 빈 칸 거부 · 저장 실패 보고 · 환경변수 있을 때 우선순위 경고 · 삭제 |
| 연결 테스트 | 6 | 성공 · 빈 칸이면 저장된 키 · 실패 이유 3가지 · **주입한 caller가 딱 한 번 불린다** · 창을 열기만 해서는 호출 0회 |
| **체크박스 연동** | 6 | 키 없음 → 비활성 + 키 칸을 가리키는 안내 · 키체인 키 → 활성 · 환경변수 키 → 활성 · **저장하면 그 자리에서 활성**(→ `jev_backend = "live"`) · 삭제하면 다시 비활성 · 설정이 live인데 키가 없으면 켜진 채 두되 끌 수 있다 |
| 유출 금지 | 1 | §6.1 |
| 진짜 배선 | 1 | 진짜 `credentials` + 가짜 키체인: 대화상자 [저장] → `LiveJevBackend.key_present()`가 True → [삭제] → False |

### 6.1 "키가 새지 않는다" 테스트

`test_the_key_never_reaches_settings_logs_or_reports` — 센티넬 키를 키체인·환경변수에 넣고 저장·추천 1회를 돌린 뒤:

1. 설정 화면의 저장 경로가 쓴 `settings.toml`에 없다(`api_key`라는 낱말도 없다).
2. `Recommendation.model_dump_json()`(디버그 덤프 포함)에 없다.
3. `caplog`(DEBUG 레벨 전체 로그)에 없다.
4. **저장소의 `config/` · `_workspace/` · `src/` · `tests/` · `logs/` · `_state/` · `data/` 안 모든 텍스트 파일**을 훑어 없다.
   (센티넬 문자열은 조각을 이어 붙여 만든다 — 테스트 파일 자신에도 온전한 형태로 남지 않는다.)
5. `test_the_repository_holds_no_credentials_file` — 저장소 어디에도 `credentials.toml`이 없다.

대화상자 쪽 `test_saving_the_dialog_never_writes_the_key_to_settings`는 [저장]→[저장만]까지 한 바퀴 돌린 뒤
`settings.toml`·`setup.json`·임시 디렉터리의 모든 `.json/.toml/.log/.jsonl`·로그를 다시 검사한다.

### 6.2 전체 결과

```
967 passed, 2 failed, 4 skipped, 1 xfailed  (2분 58초)
```

기준선(13 보고서)은 `898 passed, 2 failed, 4 skipped, 1 xfailed`였다. 늘어난 **69개가 이번에 추가한 것**이고
(기존 테스트는 트레이 메뉴 문구 변경 때문에 1줄만 고쳤다), **새로 깨진 테스트는 없다**.
실패 2건은 `tests/test_vision_1080p.py`의 `unnamed-3.png`(PvE 전투를 planning으로 판별) — 이전부터 있던 vision 건이고
이번 작업과 무관하다. 사용자 캡처·라벨은 건드리지 않았다.

> 참고(이번 작업과 무관): `pytest tests/app/A.py tests/B.py tests/app/C.py`처럼 `tests/`와 `tests/app/`을 **번갈아**
> 인자로 주면 `tests/app/conftest.py`의 픽스처(`qapp`)가 뒤 파일에서 보이지 않는 pytest 수집 특성이 있다.
> `tests/conftest.py`를 지우고 돌려도 똑같이 나는 **기존 현상**이고, 전체 실행(`pytest`)과 파일 하나씩 실행은 멀쩡하다.

---

## 7. 실제 배선 스모크 (이 머신)

1. **live Jev 호출 1회** — `credentials.verify_key()`를 이 머신의 환경변수 키로 한 번 불렀다(연결 테스트 경로 검증).
   결과: `성공 — TypeSafe에 연결했고 키가 유효하다 (711ms).` **이번 작업에서 live 호출은 이것 하나뿐이다.**
2. **진짜 macOS 키체인 왕복** — 더미 값(`dummy-not-a-real-key-0000`)으로 저장 → 읽기(`source: keyring`) →
   `LiveJevBackend.key_present() == True` → 삭제 → 다시 없음. 허가 창은 뜨지 않았고 항목은 남기지 않았다.
   (사용자의 진짜 키는 건드리지 않았다.)
3. **진짜 설정 화면(offscreen)** — 환경변수가 설정된 이 머신에서 열면:
   상태 줄 `환경변수 TYPESAFE_API_KEY 의 키를 쓰고 있다 — api…8a92 (환경변수가 가장 먼저 쓰인다)` + 우선순위 안내,
   Jev live 체크 **활성**, [연결 테스트] 활성 / [저장]·[삭제] 비활성(입력칸이 비었고 저장된 키가 없으므로), 입력칸 `EchoMode.Password`.

---

## 8. 플랫폼 노트

| 플랫폼 | 키체인 | 비고 |
|--------|--------|------|
| **macOS** | 로그인 키체인의 일반 암호 `tft-advisor` / `typesafe-api-key` | 키체인 접근 앱으로 눈으로 확인·삭제할 수 있다. 앱(터미널/Python)이 바뀌면 처음 한 번 허가를 물을 수 있다 — "항상 허용"을 누르면 끝이다. 캐시 덕에 실행당 최대 한 번만 묻는다 |
| **Windows** | 자격 증명 관리자(일반 자격 증명) | `keyring`의 `WinVaultKeyring`. 같은 이름으로 저장된다 |
| **Linux** | Secret Service(GNOME Keyring / KWallet) | 데스크톱이 없으면 백엔드가 없다 → 자동으로 폴백 파일 |
| 백엔드 없음 | — | `~/.config/tft-advisor/credentials.toml` (`0600`/`0700`). 화면이 "파일에 저장했다"고 경로까지 말해 준다 |

- 리눅스 폴백 파일은 **암호화되지 않는다**(파일 권한만). 화면 문구가 그 사실을 감추지 않도록 보관소 이름을 그대로 적는다.
- 친구가 윈도우여도 절차는 §1과 같다 — [저장] 메시지만 "Windows 자격 증명 관리자에 저장했다"로 바뀐다.

---

## 9. 남은 것 / 다음 제안

1. **오버레이 트레이에서 바로 키 넣기.** 지금은 트레이 → "설정(화면 자동 감지)…" → 키 칸이다. 한 단계다.
   키만 다루는 작은 창을 트레이에 직접 달 수도 있지만, 설정이 두 군데로 갈라지므로 지금 구조가 낫다고 봤다.
2. **연결 테스트가 UI 스레드에서 돈다.** 최대 15초 창이 멈출 수 있다(실측 0.7초). 버튼을 비활성으로 바꾸고
   "연결 테스트 중…"을 먼저 그리므로 죽은 창처럼 보이지는 않는다. 느린 회선에서 거슬리면 `JevSwitcher`처럼
   작업 스레드 + 시그널로 옮기면 된다(구조는 이미 같은 모양이다).
3. **키 교체 알림.** 키를 바꿔 저장해도 실행 중인 `LiveJevBackend`는 이미 만들어 둔 SDK 클라이언트를 계속 쓴다
   (클라이언트는 첫 호출에 만들어지고 앱 수명 동안 유지된다). 설정 화면에서 키를 바꾼 뒤에는 앱을 다시 띄우는 것이 확실하다 —
   필요하면 저장 시 `loop.set_advisor(create_advisor(...))`로 갈아 끼우는 한 줄을 붙일 수 있다(13번의 전환기와 같은 길).
4. **`_state/`의 `setup.json`**에는 키를 쓰지 않는다(§6.1이 검사한다). 다만 나중에 "마지막 연결 테스트 시각" 같은 것을
   남기고 싶으면 그 파일이 적당하다 — **가린 힌트까지만** 남기는 것을 권한다.
