"""vision-engineer(Fix round, QA04-V2): NameMatcher margin 튜닝과 적대 변형 재검사.

    .venv/bin/python _workspace/qa_scripts/vision_name_tuning.py

QA 스크립트(vision_name_margins.py)와 같은 자모 1개 치환·삭제 변형을 **현재 NameMatcher**(숫자 서명 분리 + margin)에
넣는다. 기본 이름 자모열만 변형하고 서명은 그대로 둔다(best_parts). 추가로 등급 토큰 OCR 변형(I↔l/1/|, 누락)을 텍스트로 넣는다.
min_margin 후보별로 (다른 ID 수락 = 확신 오답, 맞는 ID 수락, 기권) 비율을 출력한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tft_advisor.static_data import load_static  # noqa: E402
from tft_advisor.vision.matching import NameMatcher, NameMatch  # noqa: E402

S = load_static()
POOL = "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅓㅗㅜㅡㅣㅐㅔ"
MARGINS = (0.0, 5.0, 8.0, 10.0, 12.0, 15.0)
TIER_OCR = {1: ["l", "1", "|", ""], 2: ["Il", "lI", "1I", "ll", "I1", "11", "I", ""],
            3: ["Ill", "IlI", "lII", "1II", "II", "IIII", ""]}
ROMAN = {1: "I", 2: "II", 3: "III"}


def accept(m: NameMatcher, r: NameMatch | None, mm: float) -> bool:
    if r is None:
        return False
    return (r.score >= m.min_score and r.margin >= mm) or (r.score >= m.min_score - 25 and r.margin >= m.relaxed_margin)


def jamo_variants(m: NameMatcher, only_ko: bool = True):
    for i, key in enumerate(m._keys):
        if only_ko and not any("가" <= c <= "힣" for c in m._names[i][1]):
            continue
        vs = set()
        for p in range(len(key)):
            vs.add(key[:p] + key[p + 1:])
            for c in POOL:
                if c != key[p]:
                    vs.add(key[:p] + c + key[p + 1:])
        for v in vs:
            if len(v) >= 2:
                yield m._ids[i], m.best_parts(v, m._sigs[i])


def tier_variants(m: NameMatcher):
    for i, (kind, name) in enumerate(m._names):
        sig = m._sigs[i]
        if not sig or not isinstance(sig[-1], int) or not name.rstrip().endswith(ROMAN.get(sig[-1], "#")):
            continue
        base = name.rstrip()[: -len(ROMAN[sig[-1]])]
        for tok in TIER_OCR.get(sig[-1], []):
            yield m._ids[i], m.best(base + tok), tok


def summarize(label, results, m):
    n = len(results)
    print(f"\n[{label}] {n} variants")
    print("  min_margin  wrong_accepted  right_accepted  abstain")
    for mm in MARGINS:
        w = sum(1 for tid, r in results if accept(m, r, mm) and r.api_name != tid)
        ok = sum(1 for tid, r in results if accept(m, r, mm) and r.api_name == tid)
        print(f"  {mm:>10.0f}  {w:>7d} ({w / n:.3%})  {ok / n:>13.1%}  {1 - (w + ok) / n:>7.1%}")


def main() -> int:
    shop = NameMatcher(S, ("champions", "shop_specials"), 85)
    aug = NameMatcher(S, ("augments",), 85)
    for m, lab in ((shop, "shop jamo"), (aug, "augments jamo")):
        summarize(lab, list(jamo_variants(m)), m)
    tv = list(tier_variants(aug))
    summarize("augments tier-token OCR", [(t, r) for t, r, _ in tv], aug)
    # 등급 토큰별 상세(min_margin=8)
    from collections import Counter
    c = Counter()
    for tid, r, tok in tv:
        res = "right" if accept(aug, r, 8) and r.api_name == tid else ("WRONG" if accept(aug, r, 8) else "abstain")
        c[(tok, res)] += 1
    print("\n  tier token → outcome (min_margin=8):", dict(sorted(c.items())))
    # 정확한 이름은 자기 자신으로
    miss = [(n, i) for m in (shop, aug) for (k, n), i in zip(m._names, m._ids)
            if (r := m.match(n)) is None or r.api_name != i]
    print(f"\nexact names not self-matched: {len(miss)}", miss[:10])
    return 0


if __name__ == "__main__":
    sys.exit(main())
