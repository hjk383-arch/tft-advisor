"""스크린샷 정답 파일(tests/fixtures/screens/*.expected.json) → GameState 변환 — QA·vision 정확도 측정 공용.

정답 파일 형식(사람이 쓰기 쉽게 한국어 이름 사용):
- 최상위 키는 GameState 필드명과 같다. `_`로 시작하거나 `note`로 시작하는 키는 메모로 무시한다.
- shop: 5칸 리스트. null = 빈 칸, {"name": 챔피언 이름, "cost"}, {"special": 특수 상품 이름, "cost", "desc"},
  {"unknown": true} = 식별 불가. 이름 대신 {"id": "DA_..."}도 허용.
- augment_offer / augments_owned: 증강 이름 문자열 또는 ID 문자열 리스트.
- 정답 파일에 없는 필드는 "정답 미기재"이며 비교 대상이 아니다 → `ExpectedScreen.fields`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import AugmentRef, FieldSource, GameState, ShopSlot, ShopSlotKind
from .static_data import StaticData, load_static

_IGNORED_PREFIXES = ("_", "note")


@dataclass(frozen=True)
class ExpectedScreen:
    """정답 GameState와 정답이 기재된 필드 집합."""

    path: Path
    state: GameState
    fields: frozenset[str]


def _is_note(key: str) -> bool:
    return key.startswith(_IGNORED_PREFIXES)


def _resolve(static: StaticData, kind: str, value: str) -> dict[str, Any]:
    rec = static.get(kind, value) or static.find_by_name(kind, value)
    if rec is None:
        raise KeyError(f"{kind}에서 찾을 수 없음: {value!r}")
    return rec


def _shop_slot(static: StaticData, raw: dict[str, Any] | None) -> ShopSlot:
    if raw is None:
        return ShopSlot(kind=ShopSlotKind.EMPTY)
    if raw.get("unknown"):
        return ShopSlot(kind=ShopSlotKind.UNKNOWN, cost=raw.get("cost"))
    if "special" in raw:
        rec = _resolve(static, "shop_specials", raw["special"])
        return ShopSlot(kind=ShopSlotKind.SPECIAL, id=rec["apiName"], name_ko=rec["name_ko"], cost=raw.get("cost"))
    rec = _resolve(static, "champions", raw.get("id") or raw["name"])
    cost = raw.get("cost", rec["cost"])
    return ShopSlot(kind=ShopSlotKind.CHAMPION, id=rec["apiName"], name_ko=rec["name_ko"], cost=cost)


def _augment(static: StaticData, value: str) -> AugmentRef:
    rec = _resolve(static, "augments", value)
    return AugmentRef(id=rec["apiName"], name_ko=rec["name_ko"], rarity=rec.get("tier"))


def load_expected(path: str | Path, static: StaticData | None = None) -> ExpectedScreen:
    """정답 파일 1개를 읽어 GameState로 만든다. 알 수 없는 최상위 키는 GameState 검증에서 오류가 난다."""
    path = Path(path)
    static = static or load_static()
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = {k: v for k, v in raw.items() if not _is_note(k)}

    if "shop" in data and data["shop"] is not None:
        data["shop"] = [_shop_slot(static, s) for s in data["shop"]]
    for key in ("augment_offer", "augments_owned"):
        if data.get(key) is not None:
            data[key] = [_augment(static, a) for a in data[key]]

    fields = frozenset(data)
    data["field_source"] = {k: FieldSource.FIXTURE for k in fields}
    data.setdefault("set_number", static.set_number)
    data["source_image"] = str(path.with_name(path.name.replace(".expected.json", ".png")))
    return ExpectedScreen(path=path, state=GameState.model_validate(data), fields=fields)
