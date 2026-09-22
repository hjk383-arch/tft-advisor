# TypeSafe Python SDK 요약 (2026-09-21 문서 기준 — 구현 전 최신 문서와 대조할 것)

- 패키지: `pip install typesafe_sdk`
- API 키: 환경변수 `TYPESAFE_API_KEY` (코드에 하드코딩 금지)
- HTTP: `POST https://api.typesafe.ai/v1/systemone`, Bearer 인증, `model: "jev-latest"`

```python
from typesafe_sdk import TypeSafeClient, AsyncTypeSafeClient
from typesafe_sdk import Noul, Choice, Score   # 정확한 import 경로는 sdk/python/api/types/questions.md 확인

client = TypeSafeClient()
result = client.system_one(
    state,                      # str 또는 JSON 직렬화 가능한 객체 (문서 확인)
    {
        "billing": Noul(instructions="Is this about billing?"),
        "tone": Choice(instructions="What is the tone?", criteria={"calm": None, "angry": None}),
        "urgency": Score(instructions="How urgent is this?", criteria=["low", "medium", "high"]),
    },
)
result.nouls["billing"].noul      # 0~1 확률
result.choices["tone"].choice     # 선택된 옵션
result.scores["urgency"].score    # 0 ~ (레벨수-1), 레벨 사이 실수 가능

# async
async with AsyncTypeSafeClient() as client:
    result = await client.system_one(state, questions)
```

## 응답 필드 (HTTP API 기준)
- Score: `probabilities`(레벨별), `score`(기대 위치), `legend`, `confidence`(0~1, 분포 집중도)
- Choice: 선택 + 옵션별 분포 + `confidence`
- Noul: yes 확률 (별도 confidence 없음, 0.5 근처 = 불확실)
- `usage`: 토큰 사용량

## 제약
- Choice 옵션 최대 255개, Score 레벨 2~10개
- 429(rate limit), 529(overload) → 지수 백오프 재시도 (SDK `retry` 파라미터)
- 입력은 텍스트만 (이미지/오디오 불가)
