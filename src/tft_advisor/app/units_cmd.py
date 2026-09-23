"""보유 유닛 수동 교정 — 문자열 명령 한 줄 → `SessionTracker` 호출.

구매 추적은 틀릴 수 있다(상점 칸을 놓치거나, 공동 선택·증강으로 받은 유닛은 상점 이벤트가 없다).
그래서 사용자가 바로잡을 길이 있어야 한다. 정식 UI(오버레이 패널)는 나중에 붙이고, 지금은

- **콘솔**: 앱을 띄운 터미널에 명령을 입력한다(`--live`·`--no-overlay` 모두). `app.live`가 표준 입력을 읽는다.
- **오버레이·트레이**: 같은 문자열을 `apply_command(tracker, line)`에 넘기면 된다(입력 상자 하나면 충분하다).

명령(한국어·영어 둘 다 받는다. 이름은 한국어 표시 이름 또는 canonical ID)

    유닛                     현재 장부를 보여 준다
    유닛 추가 자야           자야 1사본을 더한다(공동 선택·증강·모루로 받은 유닛)
    유닛 추가 자야 2         2사본
    유닛 제거 자야           1사본을 뺀다
    유닛 제거 자야 전부      그 챔피언을 장부에서 지운다
    유닛 성급 자야 2         자야를 2성으로 맞춘다(사본 3개)
    유닛 확인                "지금 장부가 맞습니다" — 쌓인 '설명되지 않은 거래'를 지운다
    유닛 초기화              장부를 비운다
    도움말                   사용법

이름 → ID 변환은 `static_data.champion_by_name`(정확 일치)을 쓰고, 실패하면 무엇을 입력해야 하는지 알려 준다.
추측해서 엉뚱한 챔피언을 넣지 않는다.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

PROMPT = "보유 유닛 수정: '유닛' 입력 시 목록, '도움말' 입력 시 사용법"
HELP = (
    "보유 유닛 수동 교정\n"
    "  유닛                  현재 장부 보기\n"
    "  유닛 추가 <이름> [수]  유닛을 더합니다(공동 선택·증강·모루 등)\n"
    "  유닛 제거 <이름> [수|전부]  유닛을 뺍니다\n"
    "  유닛 성급 <이름> <1~3>  성급을 맞춥니다(2성 = 사본 3개)\n"
    "  유닛 확인              장부가 맞다고 확인합니다(신뢰도 회복)\n"
    "  유닛 초기화            장부를 비웁니다"
)
_ADD = {"추가", "add", "+"}
_REMOVE = {"제거", "삭제", "remove", "del", "-"}
_STAR = {"성급", "성", "star"}
_CONFIRM = {"확인", "confirm", "ok"}
_CLEAR = {"초기화", "clear", "reset"}
_LIST = {"목록", "list", "show"}
_ALL = {"전부", "모두", "all"}
_HEADS = {"유닛", "보유", "unit", "units"}
_HELP = {"도움말", "help", "?"}


def _resolve(name: str) -> tuple[str | None, str]:
    """표시 이름 또는 ID → 챔피언 ID. 못 찾으면 (None, 안내 문구)."""
    from ..static_data import load_static

    static = load_static()
    if static.get("champions", name) is not None:
        return name, ""
    row = static.champion_by_name(name)
    if row is not None:
        return str(row["apiName"]), ""
    return None, f"'{name}'에 해당하는 챔피언을 찾지 못했습니다. 한국어 이름 또는 ID를 정확히 입력해 주세요."


def _rows_text(tracker: Any) -> str:
    from ..static_data import load_static
    from .ledger import SOURCE_LABELS, bodies_for

    rows = tracker.units_rows()
    if not rows:
        return "장부가 비어 있습니다(보유 유닛을 아직 추적하지 못했습니다)."
    static = load_static()
    out = []
    for cid, copies, star, source in rows:
        name = static.name_ko(cid) or cid
        stars = "/".join(f"{s}성" for s in bodies_for(copies))
        out.append(f"  {name}  사본 {copies} ({stars})  출처 {SOURCE_LABELS.get(source, source)}")
    ambiguous = tracker.data.units.ambiguous
    head = f"보유 유닛 {sum(len(bodies_for(r[1])) for r in rows)}기"
    if ambiguous:
        head += f" · 설명되지 않은 거래 {ambiguous}건(신뢰도가 내려갑니다. 맞으면 '유닛 확인')"
    return head + "\n" + "\n".join(out)


def apply_command(tracker: Any, line: str) -> str | None:
    """명령 한 줄을 적용하고 사용자에게 보여 줄 답을 돌려준다. 우리 명령이 아니면 None."""
    parts = (line or "").strip().split()
    if not parts:
        return None
    head = parts[0].lower()
    if head in _HELP:
        return HELP
    if head not in _HEADS:
        return None
    if len(parts) == 1 or parts[1].lower() in _LIST:
        return _rows_text(tracker)
    verb = parts[1].lower()
    args = parts[2:]
    try:
        if verb in _CONFIRM:
            tracker.confirm_units()
            return "장부를 확인했습니다(설명되지 않은 거래 수를 지웠습니다)."
        if verb in _CLEAR:
            tracker.clear_units()
            return "장부를 비웠습니다."
        if not args:
            return "챔피언 이름이 필요합니다. 예: 유닛 추가 자야"
        name = args[0]
        champion, why = _resolve(name)
        if champion is None:
            return why
        if verb in _ADD:
            n = _int(args[1], 1) if len(args) > 1 else 1
            tracker.add_unit(champion, max(1, n), source="manual")
            return f"{name} {max(1, n)}사본을 더했습니다.\n" + _rows_text(tracker)
        if verb in _REMOVE:
            if len(args) > 1 and args[1].lower() in _ALL:
                tracker.remove_unit(champion, 0)
                return f"{name}을(를) 장부에서 지웠습니다.\n" + _rows_text(tracker)
            n = _int(args[1], 1) if len(args) > 1 else 1
            tracker.remove_unit(champion, max(1, n))
            return f"{name} {max(1, n)}사본을 뺐습니다.\n" + _rows_text(tracker)
        if verb in _STAR:
            star = _int(args[1], 0) if len(args) > 1 else 0
            if star not in (1, 2, 3):
                return "성급은 1~3 중 하나여야 합니다. 예: 유닛 성급 자야 2"
            tracker.set_unit_star(champion, star)
            return f"{name}을(를) {star}성으로 맞췄습니다.\n" + _rows_text(tracker)
    except Exception as e:   # 잘못된 입력이 앱을 멈추지 않는다
        log.exception("보유 유닛 명령 실패: %s", line)
        return f"명령을 처리하지 못했습니다: {e}"
    return f"알 수 없는 명령입니다.\n{HELP}"


def _int(text: str, default: int) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


__all__ = ["HELP", "PROMPT", "apply_command"]
