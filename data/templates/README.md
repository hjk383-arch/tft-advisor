# data/templates — 화면 인식용 템플릿

`src/tft_advisor/vision/`이 쓰는 템플릿 이미지다. 구조: `data/templates/{세트 번호}/{종류}/{canonical_id}.png`

| 디렉터리 | 내용 | 만드는 방법 | 저장소 |
|---|---|---|---|
| `{set}/items/` | 아이템 아이콘 원본 (CommunityDragon) | `python -m tft_advisor.vision.templates fetch-items` | **커밋하지 않음** (`.gitignore`) |
| `{set}/items_screen/` | 사용자 원본 캡처에서 잘라낸 실화면 아이템 아이콘 | `python -m tft_advisor.vision.templates harvest-items SCREENSHOT LABEL.json` | **커밋하지 않음** (`.gitignore`) |
| `{set}/digits/` | HUD 숫자 글리프(흑백 이진 이미지, 0-9 / - %) | `python -m tft_advisor.vision.templates harvest-digits SCREENSHOT LABEL.json` | 커밋 가능 |

## 출처와 고지

- `items/`의 아이콘은 Riot Games의 저작물이다. [CommunityDragon](https://www.communitydragon.org/)
  (`raw.communitydragon.org/latest/game/…`, 비공식 커뮤니티 미러)에서 받는다. CommunityDragon은 별도 라이선스를 주지 않으며,
  사용은 Riot의 팬 콘텐츠 정책("Legal Jibber Jabber", https://www.riotgames.com/en/legal)을 따른다.
- `items_screen/`은 게임 화면을 잘라 낸 것이므로 역시 Riot 아트워크다.
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
