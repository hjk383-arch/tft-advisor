"""OCR 문자열 → 값. 모두 순수 함수이고, 형식이 맞지 않으면 None(추측하지 않는다)."""
from __future__ import annotations

import re
from collections.abc import Mapping

# 흔한 OCR 혼동 (숫자 문맥에서만 적용)
_DIGIT_FIX = str.maketrans({
    "O": "0", "o": "0", "D": "0", "Q": "0",
    "l": "1", "I": "1", "|": "1", "i": "1", "!": "1",
    "Z": "2", "S": "5", "s": "5", "B": "8", "g": "9",
    "—": "-", "–": "-", "~": "-", "ㅡ": "-",
})

_DASH_FIX = str.maketrans({"—": "-", "–": "-", "~": "-", "ㅡ": "-"})

# 레벨 L에서 다음 레벨까지 필요한 경험치(화면 "a/b"의 b).
# 정본은 정적 데이터 `meta.json` `xp_to_next`(StaticData.xp_to_next(), 출처 tftflow Set 18)이고 Recognizer가 그것을 넘긴다.
# 아래는 meta에 표가 없을 때만 쓰는 대체값(같은 출처 값). 3→6, 4→10, 6→36 은 fixture로도 관측했다.
XP_TO_NEXT: dict[int, int] = {1: 2, 2: 2, 3: 6, 4: 10, 5: 20, 6: 36, 7: 56, 8: 68, 9: 68}
XP_TO_NEXT_OBSERVED = frozenset({3, 4, 6})


def _level_by_need(table: Mapping[int, int]) -> dict[int, int | None]:
    """필요량 → 레벨. 같은 필요량의 레벨이 둘 이상이면(1·2레벨=2, 8·9레벨=68) None(추정 불가)."""
    out: dict[int, int | None] = {}
    for lv, need in table.items():
        out[need] = None if need in out else lv
    return out


def digits_only(text: str) -> str:
    return re.sub(r"[^0-9]", "", text.translate(_DIGIT_FIX))


def parse_int(text: str | None, lo: int = 0, hi: int = 999) -> int | None:
    """"31", "①31", "31 " → 31. 숫자가 없거나 범위 밖이면 None. 천 단위 쉼표 허용."""
    if not text:
        return None
    t = text.translate(_DIGIT_FIX).replace(",", "")
    m = re.findall(r"\d+", t)
    if len(m) != 1:
        return None
    v = int(m[0])
    return v if lo <= v <= hi else None


def parse_stage(text: str | None) -> str | None:
    """"2-5", "2 - 5", "2—5" → "2-5". 스테이지 1~9, 라운드 1~7."""
    if not text:
        return None
    t = text.translate(_DASH_FIX)   # 글자→숫자 치환은 하지 않는다("i2-4" 가 "12-4" 가 되지 않게)
    m = re.search(r"(?<!\d)([1-9])\s*-\s*([1-7])(?!\d)", t)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def parse_level(text: str | None) -> int | None:
    """"4레벨", "레벨 4", "Lv. 4", "Level 4" → 4 (1~10)."""
    if not text:
        return None
    t = text.replace(" ", "")
    m = re.search(r"(\S{1,2})레벨", t) or re.search(r"(?:레벨|lv\.?|level)(\S{1,2})", t, re.IGNORECASE)
    if not m:
        return None
    d = digits_only(m.group(1))
    if not d:
        return None
    v = int(d)
    return v if 1 <= v <= 10 else None


def parse_xp(text: str | None, table: Mapping[int, int] | None = None) -> tuple[int, int] | None:
    """"2/10" → (2, 10). 현재 ≤ 필요량, 필요량은 XP 표(`table`, 기본 XP_TO_NEXT)의 값 중 하나여야 한다."""
    if not text:
        return None
    needs = set((table or XP_TO_NEXT).values())
    t = text.translate(_DIGIT_FIX).replace(" ", "")
    m = re.search(r"(\d{1,3})/(\d{1,3})", t)
    if m:
        cur, need = int(m.group(1)), int(m.group(2))
        if need not in needs or cur > need:
            return None
        return cur, need
    return _xp_slash_as_one(t, needs)


def _xp_slash_as_one(t: str, needs: set[int]) -> tuple[int, int] | None:
    """OCR이 "/"를 "1"로 읽은 경우("216" = 2/6, "2110" = 2/10). 유효한 분할이 정확히 하나일 때만."""
    d = re.sub(r"[^0-9]", "", t)
    if not 3 <= len(d) <= 5 or d != re.sub(r"\s", "", t):
        return None
    cands = []
    for i in range(1, len(d) - 1):
        if d[i] != "1":
            continue
        cur, need = int(d[:i]), int(d[i + 1:])
        if need in needs and cur <= need:
            cands.append((cur, need))
    return cands[0] if len(cands) == 1 else None


def level_from_xp(xp: tuple[int, int] | None, table: Mapping[int, int] | None = None) -> int | None:
    """경험치 필요량으로 레벨 추정(레벨 숫자가 가려졌을 때). 필요량이 같은 레벨이 여럿이면 None."""
    return _level_by_need(table or XP_TO_NEXT).get(xp[1]) if xp else None


def parse_odds(text: str | None) -> list[int] | None:
    """"55% 30% 15% 0% 0%" → [55, 30, 15, 0, 0]. 5개이고 합이 100(±1)일 때만."""
    if not text:
        return None
    t = text.translate(_DIGIT_FIX)
    vals = [int(v) for v in re.findall(r"(\d{1,3})\s*%", t)]
    if len(vals) != 5 or any(v > 100 for v in vals) or abs(sum(vals) - 100) > 1:
        return None
    return vals
