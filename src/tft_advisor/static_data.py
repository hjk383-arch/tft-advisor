"""정적 게임 데이터 로더 (data/static/{set}/*.json) — vision/advisor/stats/app 공용.

생성은 `stats/static_extract.py`가 한다. 이 모듈은 읽기·조회만 한다.

이름 → ID 규칙 (stats 보고서 7절)
- 챔피언: `shop_pool=true`만 조회(크립 "협곡 바위 게" 등 동명 비상점 유닛 배제)
- 증강/아이템: 같은 이름이 여럿이면 `DA_*` + `set_native=true` 우선
- 상점 특수 상품: `_Upgrade`/`_Prismatic` 변형보다 기본 ID 우선
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATIC_DIR = PROJECT_ROOT / "data" / "static"
DEFAULT_SET = 18

Record = dict[str, Any]


def _norm(name: str) -> str:
    """이름 비교용 정규화: 공백 제거 + 소문자."""
    return "".join(name.split()).lower()


def _preference(rec: Record) -> tuple[int, int, int]:
    """동명 후보 정렬 키(작을수록 우선)."""
    api = rec["apiName"]
    return (
        0 if api.startswith("DA_") else 1,
        0 if rec.get("set_native", True) else 1,
        1 if api.endswith(("_Upgrade", "_Prismatic")) else 0,
    )


class StaticData:
    """한 세트의 정적 데이터. `by_id`는 전 종류 통합 조회(ID는 종류 간 겹치지 않는다고 가정하고 검사한다)."""

    KINDS = ("champions", "items", "augments", "traits", "shop_specials")

    def __init__(self, set_number: int = DEFAULT_SET, static_dir: Path | None = None) -> None:
        self.set_number = set_number
        self.dir = (static_dir or DEFAULT_STATIC_DIR) / str(set_number)
        if not self.dir.is_dir():
            raise FileNotFoundError(f"정적 데이터 없음: {self.dir} (stats/static_extract.py 실행 필요)")
        self.tables: dict[str, list[Record]] = {k: self._load(k) for k in self.KINDS}
        self.meta: Record = json.loads((self.dir / "meta.json").read_text(encoding="utf-8"))
        self._id_index: dict[str, dict[str, Record]] = {
            k: {r["apiName"]: r for r in rows} for k, rows in self.tables.items()
        }
        self._name_index: dict[str, dict[str, list[Record]]] = {k: {} for k in self.KINDS}
        for kind, rows in self.tables.items():
            for r in rows:
                if kind == "champions" and not r.get("shop_pool"):
                    continue
                for key in ("name_ko", "name_en"):
                    if r.get(key):
                        self._name_index[kind].setdefault(_norm(r[key]), []).append(r)
        for idx in self._name_index.values():
            for cands in idx.values():
                cands.sort(key=_preference)

    def _load(self, kind: str) -> list[Record]:
        return json.loads((self.dir / f"{kind}.json").read_text(encoding="utf-8"))

    # --- ID 조회 ---
    def get(self, kind: str, api_name: str) -> Record | None:
        """종류별 ID 조회."""
        return self._id_index[kind].get(api_name)

    def kind_of(self, api_name: str) -> str | None:
        """ID가 어느 종류인지(없으면 None)."""
        for kind in self.KINDS:
            if api_name in self._id_index[kind]:
                return kind
        return None

    def name_ko(self, api_name: str) -> str | None:
        """ID → 표시용 한국어 이름."""
        kind = self.kind_of(api_name)
        return self._id_index[kind][api_name].get("name_ko") if kind else None

    # --- 이름 → 레코드 ---
    def find_by_name(self, kind: str, name: str) -> Record | None:
        """정확한(공백·대소문자 무시) 이름 일치. 퍼지 매칭은 vision 책임."""
        cands = self._name_index[kind].get(_norm(name))
        return cands[0] if cands else None

    def champion_by_name(self, name: str) -> Record | None:
        return self.find_by_name("champions", name)

    def augment_by_name(self, name: str) -> Record | None:
        return self.find_by_name("augments", name)

    def item_by_name(self, name: str) -> Record | None:
        return self.find_by_name("items", name)

    def shop_special_by_name(self, name: str) -> Record | None:
        return self.find_by_name("shop_specials", name)

    def trait_by_name(self, name: str) -> Record | None:
        return self.find_by_name("traits", name)

    def names(self, kind: str, lang: str = "ko") -> list[str]:
        """퍼지 매칭 후보 목록(챔피언은 shop_pool만)."""
        key = f"name_{lang}"
        rows = self.tables[kind]
        if kind == "champions":
            rows = [r for r in rows if r.get("shop_pool")]
        return sorted({r[key] for r in rows if r.get(key)})

    def observed_shop_odds(self) -> dict[int, list[int]]:
        """관측된 레벨별 상점 확률(%). 미관측 레벨은 없음(5, 7~10레벨 미확인)."""
        return {int(k): v for k, v in self.meta.get("observed_shop_odds_pct", {}).items()}


@cache
def load_static(set_number: int = DEFAULT_SET) -> StaticData:
    """프로세스 공용 캐시 인스턴스."""
    return StaticData(set_number)
