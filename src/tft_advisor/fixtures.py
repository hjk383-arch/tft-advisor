"""스크린샷 정답 파일(tests/fixtures/screens/*.expected.json) → GameState 변환 — QA·vision 정확도 측정 공용.

정답 파일 형식(사람이 쓰기 쉽게 한국어 이름 사용):
- 최상위 키는 GameState 필드명과 같다. `_`로 시작하거나 `note`로 시작하는 키는 메모로 무시한다.
- shop: 5칸 리스트. null = 빈 칸, {"name": 챔피언 이름, "cost"}, {"special": 특수 상품 이름, "cost", "desc"},
  {"unknown": true} = 식별 불가. 이름 대신 {"id": "DA_..."}도 허용.
- augment_offer / augments_owned: 증강 이름 문자열 또는 ID 문자열 리스트.
- items(보유 아이템 = 아이템 벤치, 미장착분): `{"components"|"completed"|"emblems"|"others": [이름|apiName, …]}`
  또는 이름 평면 리스트. 이름은 vision `item_ids.ItemCatalog.resolve`로 **묶음 대표 ID**로 바꾼다(vision 출력과 같은 ID,
  예: "자석 제거기" → `DA_Consumable_ItemRemover`). 분류는 정적 데이터 `category`를 따르고, 라벨 버킷과 다르면 오류.
- board_slots / bench_slots(보드·벤치 유닛 = vision `board` 묶음 정답): 유닛이 **있는 칸만** 적는다.
  `board_slots`: `[{"hex": [줄, 칸], "star": 1|2|3|null, "items": [이름|apiName, …]}, …]` (줄 0 = 내 쪽 맨 앞, 칸 0 = 왼쪽)
  `bench_slots`: `[{"slot": 0~8, "star": …, "items": […]}, …]`
  `star`/`items`/`hex`/`slot`은 모두 선택이다(모르면 빼거나 null). `ExpectedScreen.extras`에 담기며
  아이템 이름은 대표 ID로 바뀐다. 챔피언 정체(unit_id)는 라벨에 넣지 않는다 — vision이 읽지 않는 값이다.
- item_bench: 아이템 벤치 10칸(위→아래) 이름|apiName|null 리스트. GameState 필드가 아니므로
  `ExpectedScreen.extras["item_bench"]`에 대표 ID(빈 칸 None)로 담는다(harvest-items용). `items`가 없으면
  item_bench로 `items`를 채운다(비교 대상이 된다). 둘 다 있으면 같은 아이템 묶음(다중집합)이어야 한다.
- 해석할 수 없는 이름은 KeyError(라벨 글자를 ID로 쓰지 않는다).
- 정답 파일에 없는 필드는 "정답 미기재"이며 비교 대상이 아니다 → `ExpectedScreen.fields`.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .contracts import AugmentRef, FieldSource, GameState, ItemRef, ItemState, ShopSlot, ShopSlotKind
from .static_data import StaticData, load_static

if TYPE_CHECKING:
    from .vision.item_ids import ItemCatalog

_IGNORED_PREFIXES = ("_", "note")
_EXTRA_KEYS = ("item_bench", "board_slots", "bench_slots")   # GameState 필드가 아닌 정답 키 → ExpectedScreen.extras
ITEM_BUCKETS = ("components", "completed", "emblems", "others")
# items.json category → ItemState 버킷 (vision recognizer와 같은 규칙: 나머지는 others)
_CATEGORY_BUCKET = {"component": "components", "completed": "completed", "emblem": "emblems"}
ITEM_BENCH_SLOTS = 10


@dataclass(frozen=True)
class ExpectedScreen:
    """정답 GameState와 정답이 기재된 필드 집합. extras = GameState 밖의 정답(현재 `item_bench`: 대표 ID|None 리스트)."""

    path: Path
    state: GameState
    fields: frozenset[str]
    extras: dict[str, Any] = field(default_factory=dict)


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


def _item_catalog(static: StaticData) -> ItemCatalog:
    # vision extra(numpy·rapidfuzz)가 필요하므로 items/item_bench가 있을 때만 import한다.
    from .vision.item_ids import ItemCatalog

    return ItemCatalog(static)


def _item_id(cat: ItemCatalog, label: str) -> str:
    api = cat.resolve(label)
    if api is None:
        raise KeyError(f"items에서 찾을 수 없음: {label!r}")
    return api


def _item_ref(static: StaticData, cat: ItemCatalog, api: str) -> ItemRef:
    return ItemRef(id=api, name_ko=cat.display_name(api), category=(static.get("items", api) or {}).get("category"))


def _bucket_of(static: StaticData, api: str) -> str:
    return _CATEGORY_BUCKET.get((static.get("items", api) or {}).get("category"), "others")


def _items(static: StaticData, cat: ItemCatalog, raw: dict[str, list[str]] | list[str]) -> ItemState:
    state = ItemState()
    if isinstance(raw, list):
        labelled = [(None, x) for x in raw]
    elif isinstance(raw, dict):
        bad = set(raw) - set(ITEM_BUCKETS)
        if bad:
            raise ValueError(f"items 버킷은 {ITEM_BUCKETS} 중 하나여야 한다: {sorted(bad)}")
        labelled = [(b, x) for b, xs in raw.items() for x in (xs or [])]
    else:
        raise ValueError(f"items는 버킷 dict 또는 이름 리스트여야 한다: {raw!r}")
    for bucket, label in labelled:
        api = _item_id(cat, label)
        actual = _bucket_of(static, api)
        if bucket is not None and bucket != actual:
            raise ValueError(f"items.{bucket}의 {label!r}({api})는 정적 데이터상 {actual}이다")
        getattr(state, actual).append(_item_ref(static, cat, api))
    return state


def _item_bench(cat: ItemCatalog, raw: list[str | None]) -> list[str | None]:
    if not isinstance(raw, list) or len(raw) > ITEM_BENCH_SLOTS:
        raise ValueError(f"item_bench는 {ITEM_BENCH_SLOTS}칸 이하 리스트여야 한다: {raw!r}")
    return [None if x is None else _item_id(cat, x) for x in raw]


def _unit_slots(cat: ItemCatalog, raw: list[dict[str, Any]], *, on_bench: bool) -> list[dict[str, Any]]:
    """board_slots / bench_slots 라벨 → 정규화(아이템 이름 → 대표 ID, 자리 → tuple/int)."""
    key = "bench_slots" if on_bench else "board_slots"
    if not isinstance(raw, list):
        raise ValueError(f"{key}는 리스트여야 한다: {raw!r}")
    out: list[dict[str, Any]] = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ValueError(f"{key}[{i}]는 dict여야 한다: {r!r}")
        star = r.get("star")
        if star is not None and star not in (1, 2, 3):
            raise ValueError(f"{key}[{i}].star는 1~3 또는 null이어야 한다: {star!r}")
        slot: dict[str, Any] = {"star": star,
                                "items": [_item_id(cat, x) for x in (r.get("items") or [])]}
        if on_bench:
            n = r.get("slot")
            if n is not None and not 0 <= int(n) <= 8:
                raise ValueError(f"bench_slots[{i}].slot은 0~8이어야 한다: {n!r}")
            slot["slot"] = None if n is None else int(n)
        else:
            h = r.get("hex")
            if h is not None and (len(h) != 2 or not (0 <= h[0] <= 3 and 0 <= h[1] <= 6)):
                raise ValueError(f"board_slots[{i}].hex는 [0~3, 0~6]이어야 한다: {h!r}")
            slot["hex"] = None if h is None else (int(h[0]), int(h[1]))
        out.append(slot)
    return out


def load_expected(path: str | Path, static: StaticData | None = None) -> ExpectedScreen:
    """정답 파일 1개를 읽어 GameState로 만든다. 알 수 없는 최상위 키는 GameState 검증에서 오류가 난다."""
    path = Path(path)
    static = static or load_static()
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = {k: v for k, v in raw.items() if not _is_note(k)}
    extras_raw = {k: data.pop(k) for k in _EXTRA_KEYS if k in data}
    extras: dict[str, Any] = {}
    if data.get("items") is not None or extras_raw.get("item_bench") is not None:
        cat = _item_catalog(static)
        if data.get("items") is not None:
            data["items"] = _items(static, cat, data["items"])
        if extras_raw.get("item_bench") is not None:
            bench = extras["item_bench"] = _item_bench(cat, extras_raw["item_bench"])
            from_bench = _items(static, cat, [x for x in bench if x is not None])
            if "items" not in data:
                data["items"] = from_bench
            elif data["items"] is not None and Counter(data["items"].all_ids()) != Counter(from_bench.all_ids()):
                raise ValueError(f"{path.name}: items와 item_bench의 아이템이 다르다")
    elif "item_bench" in extras_raw:
        extras["item_bench"] = None
    for key, on_bench in (("board_slots", False), ("bench_slots", True)):
        if extras_raw.get(key) is not None:
            extras[key] = _unit_slots(_item_catalog(static), extras_raw[key], on_bench=on_bench)

    if "shop" in data and data["shop"] is not None:
        data["shop"] = [_shop_slot(static, s) for s in data["shop"]]
    for key in ("augment_offer", "augments_owned"):
        if data.get(key) is not None:
            data[key] = [_augment(static, a) for a in data[key]]

    fields = frozenset(data)
    data["field_source"] = {k: FieldSource.FIXTURE for k in fields}
    data.setdefault("set_number", static.set_number)
    data["source_image"] = str(path.with_name(path.name.replace(".expected.json", ".png")))
    return ExpectedScreen(path=path, state=GameState.model_validate(data), fields=fields, extras=extras)
