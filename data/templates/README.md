# data/templates — 화면 인식용 템플릿

`src/tft_advisor/vision/`이 쓰는 템플릿 이미지다. 구조: `data/templates/{세트 번호}/{종류}/{canonical_id}.png`

| 디렉터리 | 내용 | 만드는 방법 | 저장소 |
|---|---|---|---|
| `{set}/items/` | 아이템 아이콘 원본 (CommunityDragon) | `python -m tft_advisor.vision.templates fetch-items` | **커밋하지 않음** (`.gitignore`) |
| `{set}/items_screen/` | 사용자 원본 캡처에서 잘라낸 실화면 아이템 아이콘 | `python -m tft_advisor.vision.templates harvest-items SCREENSHOT LABEL.json` | **커밋하지 않음** (`.gitignore`) |
| `{set}/augments/` | 증강 글리프 원본 (CommunityDragon hexcore 아이콘). 보드 왼쪽 위 보유 증강 줄 판독용 | `python -m tft_advisor.vision.templates fetch-augments` | **커밋하지 않음** (`.gitignore`) |
| `{set}/augments_alt/` | CDragon에 아이콘이 없는(`missing-*` 자리표시) 세트 증강의 대체 출처 아이콘(tactics.tools `ap.tft.tools/img/augments/{apiName}{등급}.png`, 글리프 외곽으로 정규화한 64px) + `sources.json`(ID별 출처 URL, 같은 그림 묶음) | `fetch-augments`가 CDragon 다음에 함께 받는다(`--no-alt`로 끔, 요청 간격 `--delay` 기본 0.5초) | **커밋하지 않음** (`.gitignore`) |
| `{set}/augments_screen/` | 준비 화면 보유 증강 줄에서 잘라낸 실화면 글리프(CDragon 아이콘이 없거나 공유돼 식별 불가한 증강 보충). **실시간 루프가 증강 선택 순간 자동으로 추가한다**(제시된 3개 안에서 확정된 새 칸) | `harvest-augments SCREENSHOT LABEL.json` 또는 실시간 자동 학습 | **커밋하지 않음** (`.gitignore`) |
| `{set}/units_screen/{apiName}/*.png` | 보드·벤치 유닛 **모델 크롭**(체력바 기준 1080p 정규화 112x112) — 챔피언 이름 식별 few-shot 라이브러리(`vision.units`). `label_*` = 확인 라벨에서 수확, `auto_*` = 실시간 자동 학습(특성 패널 구속으로 강제된 칸만, 챔피언당 24장) | `python -m tft_advisor.vision.templates harvest-units SCREENSHOT LABEL.json`(라벨 `board_slots`/`bench_slots`의 `name`, `name_unconfirmed`는 저장 안 함) 또는 실시간 자동 학습(`[vision] unit_autolearn`) | **커밋하지 않음** (`.gitignore`) |
| `{set}/digits/` | HUD 숫자 글리프(흑백 이진 이미지, 0-9 / - %) | `python -m tft_advisor.vision.templates harvest-digits SCREENSHOT LABEL.json` | 커밋 가능 |

## 출처와 고지

- `items/`의 아이콘은 Riot Games의 저작물이다. [CommunityDragon](https://www.communitydragon.org/)
  (`raw.communitydragon.org/latest/game/…`, 비공식 커뮤니티 미러)에서 받는다. CommunityDragon은 별도 라이선스를 주지 않으며,
  사용은 Riot의 팬 콘텐츠 정책("Legal Jibber Jabber", https://www.riotgames.com/en/legal)을 따른다.
- `items_screen/`은 게임 화면을 잘라 낸 것이므로 역시 Riot 아트워크다.
- `augments_alt/`은 tactics.tools가 공개 페이지에서 쓰는 증강 아이콘(역시 Riot 아트워크)을 개인용으로 한 번 받아 둔 것이다.
  CDragon에 아이콘이 없는 세트 증강(18세트 48개 + CDragon 404 1개)을 식별하려고만 쓴다. 요청은 한 번에 하나씩, 간격을 두고, 이미 받은 파일은 다시 받지 않는다.
- 그래서 두 디렉터리는 **재생성 가능한 로컬 캐시**로만 두고 저장소에 올리지 않는다.
- `digits/`는 HUD 숫자 모양을 흑백으로 잘라 낸 작은 글리프(아트워크가 아닌 숫자 모양)라서 저장소에 둘 수 있다.
  원한다면 이것도 `.gitignore`에 넣고 `harvest-digits`로 재생성하면 된다.

고지 문구(README·배포물에 넣을 것):
> TFT Advisor isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially
> involved in producing or managing Riot Games properties. Riot Games, and all associated properties are trademarks or
> registered trademarks of Riot Games, Inc. Item icons are fetched at runtime from CommunityDragon.

## 규칙

- 파일명(stem)은 **정적 데이터 `data/static/{set}/items.json`의 apiName**이어야 한다. 아니면 로드할 때 건너뛰고 경고한다.
  `harvest-items`는 라벨의 한국어 이름을 apiName(묶음 대표 ID)으로 바꿔 저장하고, 바꿀 수 없는 이름은 저장하지 않고 오류로 알린다.
- `items/`와 `items_screen/`은 **ID별로 합쳐** 쓴다. 실화면 템플릿은 원본 아이콘을 대체하지 않고 추가된다
  (한 ID의 점수는 그 ID 템플릿들 중 최댓값). 실화면 템플릿이 몇 개뿐이어도 나머지 아이템은 원본 아이콘으로 인식된다.
- 새 세트: `fetch-items --set N` 후, 원본 캡처로 `harvest-items`를 돌린다.
