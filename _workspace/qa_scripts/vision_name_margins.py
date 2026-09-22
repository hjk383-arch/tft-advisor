"""QA(vision): 이름 퍼지 매칭 안전성.

    .venv/bin/python _workspace/qa_scripts/vision_name_margins.py

1) fixture 정답 이름(상점 챔피언·특수 상품·증강)이 data/static/18 ID로 해석되는지.
2) 매처별(상점=champions+shop_specials, 증강, 특성) 후보 이름 쌍의 자모 fuzz.ratio 최대치 — 서로 다른 ID인 두 이름이
   수락 임계(min_score=85) 이상이면 한쪽 이름의 "정확한" OCR이 다른 ID로 갈 수 있다(위험).
   relaxed 경로(≥60 & margin≥15)는 margin 조건이 1·2위 차를 요구하므로 쌍 점수 ≥ 60인 이웃이 있으면 그 이름을 OCR로
   약간 틀리게 읽었을 때 relaxed 수락이 막히거나 잘못 갈 수 있다 → 쌍 점수 분포를 보고한다.
3) 적대적 변형: 각 이름에서 자모 1개 치환/삭제(한국어 OCR 전형 오류)를 모든 위치에 적용해 matcher.match()가
   **다른 ID를 수락**하는 경우를 센다(confident wrong match). 음절 재조합이 불가능한 자모열이라도 matcher는 자모열로 비교하므로
   여기서는 자모 단위 변형을 NameMatcher 내부 키 공간에 직접 넣는다(best()의 to_jamo 입력 우회를 위해 _query_jamo 사용).
"""
from __future__ import annotations

import itertools
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rapidfuzz import fuzz, process  # noqa: E402

from tft_advisor.fixtures import load_expected  # noqa: E402
from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision.matching import NameMatcher, to_jamo  # noqa: E402

S = load_static()
MIN = 85.0


def fixture_ids() -> list[str]:
    out = []
    for p in sorted((ROOT / "tests/fixtures/screens").glob("*.expected.json")):
        e = load_expected(p, S)
        for s in e.state.shop or []:
            if s.id:
                assert S.get("champions", s.id) or S.get("shop_specials", s.id), s.id
                out.append(f"{p.stem[:10]}: {s.name_ko} -> {s.id}")
        for a in e.state.augment_offer or []:
            assert S.get("augments", a.id), a.id
            out.append(f"{p.stem[:10]}: {a.name_ko} -> {a.id}")
    return out


def resolve(m: NameMatcher, i: int) -> str | None:
    kind, name = m._names[i]
    r = S.find_by_name(kind, name)
    return r["apiName"] if r else None


def pair_scan(m: NameMatcher, label: str) -> dict:
    ids = [resolve(m, i) for i in range(len(m._keys))]
    close = []
    for i, j in itertools.combinations(range(len(m._keys)), 2):
        if ids[i] == ids[j]:
            continue
        sc = fuzz.ratio(m._keys[i], m._keys[j])
        if sc >= 60:
            close.append((round(sc, 1), m._names[i][1], m._names[j][1], ids[i], ids[j]))
    close.sort(reverse=True)
    return {"matcher": label, "n_keys": len(m._keys), "pairs_ge_85": [c for c in close if c[0] >= MIN],
            "pairs_60_85": len([c for c in close if c[0] < MIN]), "top10": close[:10]}


def match_jamo(m: NameMatcher, q: str):
    """NameMatcher.match와 같은 규칙을 자모열 입력에 적용."""
    res = process.extract(q, m._keys, scorer=fuzz.ratio, limit=12)
    if not res:
        return None
    _, top, ti = res[0]
    rid = resolve(m, ti)
    second = 0.0
    for _, s, i in res[1:]:
        if resolve(m, i) not in (None, rid):
            second = s
            break
    ok = top >= m.min_score or (top >= m.min_score - 25 and top - second >= 15)
    return (rid, top, top - second) if ok else None


JAMO_POOL = "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅓㅗㅜㅡㅣㅐㅔ"


def adversarial(m: NameMatcher, label: str, only_ko: bool = True) -> dict:
    wrong, tried = [], 0
    ids = [resolve(m, i) for i in range(len(m._keys))]
    for i, key in enumerate(m._keys):
        kind, name = m._names[i]
        if only_ko and not any("가" <= c <= "힣" for c in name):
            continue
        variants = set()
        for p in range(len(key)):
            variants.add(key[:p] + key[p + 1:])                     # 삭제
            for c in JAMO_POOL:
                if c != key[p]:
                    variants.add(key[:p] + c + key[p + 1:])       # 치환
        for v in variants:
            if len(v) < 2:
                continue
            tried += 1
            r = match_jamo(m, v)
            if r is not None and r[0] != ids[i]:
                wrong.append((name, v, r[0], round(r[1], 1), round(r[2], 1)))
    by_name = Counter(w[0] for w in wrong)
    return {"matcher": label, "variants": tried, "wrong_accepted": len(wrong),
            "rate": round(len(wrong) / max(1, tried), 5), "worst_names": by_name.most_common(10), "examples": wrong[:10]}


def main() -> int:
    print("fixture names → IDs:")
    for line in fixture_ids():
        print("  ", line)
    shop = NameMatcher(S, ("champions", "shop_specials"), MIN)
    aug = NameMatcher(S, ("augments",), MIN)
    tr = NameMatcher(S, ("traits",), MIN)
    report = {"pairs": [], "adversarial": []}
    for m, lab in ((shop, "shop"), (aug, "augments"), (tr, "traits")):
        ps = pair_scan(m, lab)
        report["pairs"].append(ps)
        print(f"\n[{lab}] keys={ps['n_keys']} pairs≥85(다른 ID)={len(ps['pairs_ge_85'])} pairs60~85={ps['pairs_60_85']}")
        for c in ps["top10"]:
            print("   ", c)
    for m, lab in ((shop, "shop"), (aug, "augments")):
        a = adversarial(m, lab)
        report["adversarial"].append(a)
        print(f"\n[{lab}] 자모 1개 변형 {a['variants']}건 중 다른 ID 수락 {a['wrong_accepted']} ({a['rate']:.3%})")
        print("   worst:", a["worst_names"])
        for e in a["examples"]:
            print("   ", e)
    out = ROOT / "_workspace" / "qa_scripts" / "vision_name_margins.out.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
