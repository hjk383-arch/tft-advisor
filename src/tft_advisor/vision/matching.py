"""OCR 이름 → canonical ID 퍼지 매칭 (rapidfuzz, 정적 데이터 `data/static/{set}/`).

한국어는 음절 단위로 비교하면 한 획 차이("워윅" vs OCR "위월")가 음절 전체 불일치가 되므로
**자모 분해** 후 비교한다. 후보: 정적 데이터의 name_ko + name_en (+ aliases 없음).

숫자·등급 토큰은 퍼지 비교에서 떼어 **정확히 일치**해야 한다(QA04-V2).
- "판도라의 아이템 I/II/III", "1~5단계 집결"처럼 숫자 한 글자만 다른 이름이 많다. 퍼지 점수로는 91~97점이라
  구별할 수 없고, OCR이 숫자를 잘못 읽으면 확신하는 오답이 된다.
- 이름 = (기본 이름 자모열, 숫자 서명). 서명은 아라비아 숫자열 + 끝의 로마 숫자 등급(I→1, II→2 …).
  OCR 입력의 끝 등급 토큰은 `l | 1 ! i`를 `I`로 정규화한 뒤 로마 숫자로 해석한다("Il" → II).
- 점수 = 기본 이름 fuzz.ratio − (서명이 다르면 SIG_PENALTY). 서명이 틀린 후보끼리는 같은 벌점을 받으므로
  "6단계 집결"은 5개 후보가 동점(margin 0)이 되어 기권한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from rapidfuzz import fuzz, process

from ..static_data import StaticData

_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = ["", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"]
# 겹모음·겹받침은 획 단위로 풀어 OCR 혼동(ㅝ↔ㅟ, ㅐ↔ㅔ 등)의 비용을 줄인다.
_SPLIT = {
    "ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅣ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ", "ㅟ": "ㅜㅣ", "ㅢ": "ㅡㅣ",
    "ㄳ": "ㄱㅅ", "ㄵ": "ㄴㅈ", "ㄶ": "ㄴㅎ", "ㄺ": "ㄹㄱ", "ㄻ": "ㄹㅁ", "ㄼ": "ㄹㅂ", "ㄽ": "ㄹㅅ",
    "ㄾ": "ㄹㅌ", "ㄿ": "ㄹㅍ", "ㅀ": "ㄹㅎ", "ㅄ": "ㅂㅅ",
}


def to_jamo(text: str) -> str:
    """한글 음절 → 자모 나열(겹자모는 풀어서). 공백·구두점 제거, 영문 소문자화."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(_CHO[code // 588])
            jung = _JUNG[(code % 588) // 28]
            jong = _JONG[code % 28]
            out.append(_SPLIT.get(jung, jung))
            out.append(_SPLIT.get(jong, jong))
        elif ch.isalnum():
            out.append(ch.lower())
    return "".join(out)


_MARKUP = re.compile(r"<[^>]*>.*?</[^>]*>|<[^>]*>")
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
# 후보(정적 데이터) 이름 끝의 등급 토큰: 공백 뒤 로마 숫자
_CAND_TIER = re.compile(r"\s(I|II|III|IV|V)\s*$")
# OCR 입력 끝의 등급 토큰: 공백 또는 한글 뒤, I와 혼동되는 글자 포함
_QUERY_TIER = re.compile(r"(?:(?<=\s)|(?<=[가-힣]))([IVXl|!1ⅰⅠ]{1,4})\s*$")
_TIER_FIX = str.maketrans({"l": "I", "|": "I", "!": "I", "1": "I", "ⅰ": "I", "Ⅰ": "I"})

SIG_PENALTY = 30.0      # 숫자 서명 불일치 벌점. relaxed 임계(60)보다 커서, 서명이 틀린 후보는 1위여도 margin이 생기지 않는다
MIN_MARGIN = 10.0       # 고득점(≥ min_score) 수락에도 필요한 2위와의 차 (QA04-V2. 8→10: 두 음절 이름 자모 1개 오류 오답 제거, 근거는 impl 보고서 Fix round)
RELAXED_MARGIN = 15.0   # 60~85점 수락에 필요한 2위와의 차
RELAXED_DROP = 25.0     # relaxed 구간 하한 = min_score - 25


def strip_markup(name: str | None) -> str | None:
    """정적 데이터 이름의 `<rules>(7회 사용 가능!)</rules>` 같은 마크업 제거(표시·매칭용)."""
    if name is None:
        return None
    return re.sub(r"\s+", " ", _MARKUP.sub("", name)).strip()


def split_numerals(name: str, *, query: bool = False) -> tuple[str, tuple[int | str, ...]]:
    """이름 → (숫자·등급을 뺀 기본 이름, 숫자 서명).

    `query=True`(OCR 입력)이면 끝 등급 토큰의 혼동 글자를 I로 바꾼다. 로마 숫자로 해석할 수 없는 토큰은
    서명에 "?"로 남겨 어느 후보와도 일치하지 않게 한다(등급을 모르면 기권).
    """
    tier: int | str | None = None
    text = name.strip()
    m = (_QUERY_TIER if query else _CAND_TIER).search(text)
    if m:
        tok = m.group(1).translate(_TIER_FIX) if query else m.group(1)
        tier = _ROMAN.get(tok, "?")
        text = text[: m.start(1)]
    digits: list[int | str] = [int(d) for d in re.findall(r"\d+", text)]
    base = re.sub(r"\d+", " ", text)
    sig = tuple(digits + ([tier] if tier is not None else []))
    return base, sig


@dataclass(frozen=True)
class NameMatch:
    record: dict[str, Any]
    kind: str
    score: float          # 0~100 (기본 이름 자모 fuzz.ratio − 서명 불일치 벌점)
    margin: float         # 1위 - 2위(다른 ID) 점수 차
    query: str

    @property
    def api_name(self) -> str:
        return self.record["apiName"]


class NameMatcher:
    """종류별 이름 목록에 대한 퍼지 매처.

    수락 규칙(QA04-V2): 2위(다른 ID)와의 차가 항상 필요하다.
    - score ≥ min_score(85) 이고 margin ≥ min_margin(8), 또는
    - score ≥ min_score − 25 이고 margin ≥ relaxed_margin(15).
    동명 후보는 static_data 우선순위(DA_·set_native)를 따른다. 레거시(set_native=false) 레코드 중 기본 이름이
    현 세트 레코드와 같은 것(예: TFT6 "판도라의 아이템" vs DA "판도라의 아이템 I")은 후보에서 뺀다.
    """

    def __init__(self, static: StaticData, kinds: tuple[str, ...], min_score: float = 85.0,
                 min_margin: float = MIN_MARGIN, relaxed_margin: float = RELAXED_MARGIN) -> None:
        self.min_score = min_score
        self.min_margin = min_margin
        self.relaxed_margin = relaxed_margin
        self._static = static
        self._keys: list[str] = []                      # 기본 이름 자모열
        self._sigs: list[tuple[int | str, ...]] = []    # 숫자 서명
        self._names: list[tuple[str, str]] = []         # (kind, 원래 이름)
        self._ids: list[str] = []                       # 해석된 apiName
        self._recs: dict[str, dict[str, Any]] = {}
        seen: set[tuple[str, str, tuple]] = set()
        for kind in kinds:
            rows = [r for r in static.tables[kind] if not (kind == "champions" and not r.get("shop_pool"))]
            native_bases = {to_jamo(split_numerals(strip_markup(r.get(k)) or "")[0])
                            for r in rows if r.get("set_native") is True for k in ("name_ko", "name_en")}
            for rec in rows:
                for key in ("name_ko", "name_en"):
                    name = strip_markup(rec.get(key))
                    if not name:
                        continue
                    base, sig = split_numerals(name)
                    j = to_jamo(base)
                    if not j:
                        continue
                    if rec.get("set_native") is False and j in native_bases:
                        continue   # 레거시 동명/유사명 → 현 세트 레코드가 대신한다
                    best = static.find_by_name(kind, name) or rec
                    if (kind, j, sig) in seen:
                        continue
                    seen.add((kind, j, sig))
                    self._keys.append(j)
                    self._sigs.append(sig)
                    self._names.append((kind, name))
                    self._ids.append(best["apiName"])
                    self._recs.setdefault(best["apiName"], best)

    def accepts(self, m: NameMatch) -> bool:
        if m.score >= self.min_score and m.margin >= self.min_margin:
            return True
        return m.score >= self.min_score - RELAXED_DROP and m.margin >= self.relaxed_margin

    def match(self, text: str | None) -> NameMatch | None:
        """최선 후보(수락 규칙 미달이면 None)."""
        m = self.best(text)
        return m if m is not None and self.accepts(m) else None

    def best(self, text: str | None) -> NameMatch | None:
        """수락 여부와 무관한 1위(디버그·보고용)."""
        if not text:
            return None
        base, sig = split_numerals(text, query=True)
        return self.best_parts(to_jamo(base), sig, text)

    def best_parts(self, q: str, sig: tuple[int | str, ...], text: str = "") -> NameMatch | None:
        """자모열과 서명으로 직접 조회(적대 변형 검사용)."""
        if len(q) < 2 or not self._keys:
            return None
        scores = process.cdist([q], self._keys, scorer=fuzz.ratio, dtype=np.float32)[0]
        penalty = np.array([0.0 if s == sig else SIG_PENALTY for s in self._sigs], dtype=np.float32)
        scores = scores - penalty
        order = np.argsort(-scores, kind="stable")
        ti = int(order[0])
        top_id = self._ids[ti]
        second = 0.0
        for i in order[1:64]:
            if self._ids[int(i)] != top_id:
                second = float(scores[int(i)])
                break
        top = float(scores[ti])
        kind = self._names[ti][0]
        return NameMatch(record=self._recs[top_id], kind=kind, score=top, margin=top - max(0.0, second), query=text)
