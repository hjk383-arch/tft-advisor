"""아이템 ID 묶음·대표 ID·이름 → ID 해석 (vision 전용, 정적 데이터 `items.json` 기반).

- **묶음(group)**: 화면에서 구별할 수 없는 변형을 하나로 본다. 마크업을 뗀 이름이 같거나(`자석 제거기 <rules>(7회 사용
  가능!)</rules>` = `자석 제거기`) 아이콘 파일이 같으면(DA_/레거시 동일 아이콘) 같은 묶음이다(union-find).
  단 category가 같을 때만 묶는다(찬란한 아이템·유물은 아이콘/이름을 공유해도 다른 아이템).
- **대표 ID**: 묶음 안에서 static_data 우선순위(DA_·set_native·기본 ID) 1위, 동률이면 이름에 마크업이 없는 것.
  vision은 항상 대표 ID를 출력한다(QA04-V6: 사용 횟수 변형 `…_UsesLeft7`처럼 틀린 사실을 담은 ID를 내지 않는다).
- **이름 해석**: 라벨/사용자 입력의 표시 이름(한국어·영어, 공백·대소문자·마크업 무시) 또는 apiName → 대표 ID.
  해석할 수 없으면 None(라벨 텍스트를 ID로 쓰지 않는다, QA04-V1).
"""
from __future__ import annotations

from typing import Any

from ..static_data import StaticData, _norm, preference_key
from .matching import strip_markup


def _has_markup(rec: dict[str, Any]) -> bool:
    return "<" in (rec.get("name_ko") or "") or "<" in (rec.get("name_en") or "")


class ItemCatalog:
    def __init__(self, static: StaticData) -> None:
        self.static = static
        items = static.tables["items"]
        parent: dict[str, str] = {r["apiName"]: r["apiName"] for r in items}

        def find(a: str) -> str:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        first_by: dict[tuple[str, str, str], str] = {}
        for r in items:
            cat = r.get("category") or ""
            keys = [("icon", cat, r["icon"].lower())] if r.get("icon") else []
            name = strip_markup(r.get("name_ko"))
            if name:
                keys.append(("name", cat, _norm(name)))
            for k in keys:
                if k in first_by:
                    union(first_by[k], r["apiName"])
                else:
                    first_by[k] = r["apiName"]

        members: dict[str, list[dict[str, Any]]] = {}
        for r in items:
            members.setdefault(find(r["apiName"]), []).append(r)
        self._rep: dict[str, str] = {}
        for rows in members.values():
            best = min(rows, key=lambda r: (preference_key(r), _has_markup(r), r["apiName"]))
            for r in rows:
                self._rep[r["apiName"]] = best["apiName"]

        self._by_name: dict[str, str] = {}
        for r in sorted(items, key=lambda r: (preference_key(r), _has_markup(r))):
            for key in ("name_ko", "name_en"):
                name = strip_markup(r.get(key))
                if name:
                    self._by_name.setdefault(_norm(name), self._rep[r["apiName"]])

    def rep(self, api_name: str) -> str:
        """대표 ID(모르는 ID는 그대로)."""
        return self._rep.get(api_name, api_name)

    def same_item(self, a: str, b: str) -> bool:
        """화면상 같은 아이템인가(묶음 비교). fixture 정답 비교용."""
        return self.rep(a) == self.rep(b)

    def resolve(self, label: str | None) -> str | None:
        """apiName 또는 표시 이름 → 대표 ID. 해석 불가면 None."""
        if not label:
            return None
        if self.static.get("items", label) is not None:
            return self.rep(label)
        return self._by_name.get(_norm(strip_markup(label) or ""))

    def display_name(self, api_name: str) -> str | None:
        """마크업을 뗀 한국어 표시 이름."""
        return strip_markup((self.static.get("items", api_name) or {}).get("name_ko"))
