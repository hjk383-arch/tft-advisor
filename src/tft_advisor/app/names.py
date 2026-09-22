"""표시용 이름 — 정적 데이터의 한국어 이름을 UI 문자열로 바꾼다.

UI는 canonical ID(`DA_18_Xayah`)가 아니라 사람이 읽는 이름("자야")을 보여야 한다. 규칙은 한 곳에만 둔다.

- `display_name(id)` = `StaticData.name_ko(id)`. 없으면 영문 이름, 그것도 없으면 ID 그대로.
- 정적 데이터 `items.json`의 name_ko 일부(22건)에 클라이언트 마크업이 남아 있다(`그웬의 가위 <rules>(2회 사용 가능!)</rules>`).
  `static_extract`에서 제거되기 전까지 표시 단계에서 떼어 낸다(04_qa_phase3_final.md S1).
"""
from __future__ import annotations

import re

from ..static_data import StaticData, load_static

_MARKUP = re.compile(r"<[^>]*>")
_SPACES = re.compile(r"\s{2,}")


def strip_markup(text: str) -> str:
    """클라이언트 태그(`<rules>…</rules>` 등)를 떼고 공백을 정리한다."""
    return _SPACES.sub(" ", _MARKUP.sub("", text)).strip()


class NameBook:
    """ID → 표시 이름. 조회 결과를 캐시한다(오버레이가 프레임마다 부른다)."""

    def __init__(self, static: StaticData | None = None) -> None:
        self.static = static or load_static()
        self._cache: dict[str, str] = {}

    def name(self, canonical_id: str | None) -> str:
        if not canonical_id:
            return "?"
        hit = self._cache.get(canonical_id)
        if hit is not None:
            return hit
        text = self.static.name_ko(canonical_id)
        if not text:
            kind = self.static.kind_of(canonical_id)
            row = self.static.get(kind, canonical_id) if kind else None
            text = (row or {}).get("name_en") or canonical_id
        text = strip_markup(str(text)) or canonical_id
        self._cache[canonical_id] = text
        return text

    def names(self, ids: list[str] | None) -> list[str]:
        return [self.name(i) for i in (ids or [])]

    def joined(self, ids: list[str] | None, sep: str = ", ", limit: int | None = None) -> str:
        out = self.names(ids)
        if limit is not None and len(out) > limit:
            return sep.join(out[:limit]) + f" 외 {len(out) - limit}"
        return sep.join(out)
