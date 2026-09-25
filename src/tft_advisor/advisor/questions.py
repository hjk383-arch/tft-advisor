"""Jev 질문 템플릿과 모드별 묶음 (설계 §3, 결측 변형 §4.3).

- 질문 ID는 모델에 전달되지 않으므로 instructions에 대상 이름과 state 경로를 직접 넣는다.
- 문구 변형(S1-owned/-noboard, A2/I1 -hp/-nohp)은 **state 키 존재 여부만으로** 고른다(캐시 키와 일치).
- 질문은 SDK 객체가 아니라 wire dict(`{"type": "score", ...}`)로 만든다 — mock 모드는 SDK 없이 돈다.
- 각 질문에 `QMeta.hint`(0~1, 코드 프록시)를 붙인다. mock 백엔드가 이 값으로 결정적 답을 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

QUESTIONS_VERSION = "q3"
"""질문·레벨·구간 라벨 문장 버전. 문구를 바꾸면 올린다(캐시 키·state_hash에 포함)."""

HOLD = "hold_components"
UNDECIDED = "undecided"


@dataclass
class QMeta:
    kind: str                     # "score" | "choice"
    levels: int = 0               # score 레벨 수
    hint: float = 0.5             # score mock 힌트(0~1)
    hints: dict[str, float] = field(default_factory=dict)   # choice mock 힌트(라벨 → 가중)
    force_low_gate: bool = False  # 설명 없음/의미 손실 → gate = low_confidence_scale


@dataclass
class QuestionSet:
    questions: dict[str, dict[str, Any]] = field(default_factory=dict)
    meta: dict[str, QMeta] = field(default_factory=dict)

    def score(self, qid: str, instructions: str, levels: list[str], hint: float, force_low: bool = False) -> None:
        self.questions[qid] = {"type": "score", "instructions": instructions, "criteria": list(levels)}
        self.meta[qid] = QMeta("score", len(levels), max(0.0, min(1.0, hint)), force_low_gate=force_low)

    def choice(self, qid: str, instructions: str, criteria: dict[str, Any], hints: dict[str, float],
               force_low: bool = False) -> None:
        self.questions[qid] = {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}
        self.meta[qid] = QMeta("choice", hints=dict(hints), force_low_gate=force_low)

    def __len__(self) -> int:
        return len(self.questions)


# ---------------------------------------------------------------------------
# 템플릿
# ---------------------------------------------------------------------------

C1_LEVELS = [
    "None of the player's items or components is used by any unit of this comp; they would go to filler units or be wasted.",
    "Some of the player's items suit this comp's tanks or support units, but none is an item its main carry uses.",
    "One core item of this comp's main carry is already completed or appears in resources.craftable_items.",
    "Two or more core items of this comp's main carry are completed or craftable, or the player holds an emblem of this comp's key trait.",
]

C2_LEVELS = [
    "The augments work against this comp: they reward a trait, unit type or play pattern (for example rerolling low-cost units, or leveling fast) that this comp does not use.",
    "The augments are generic (gold, items, or plain stats) and help this comp about as much as any other comp.",
    "At least one augment directly boosts this comp's key trait, its main carry's damage type, or its leveling plan.",
    "An augment is made for this comp: it grants or boosts this comp's key trait or its main carry specifically.",
]

A1_LEVELS = [
    "This augment works against this comp: it rewards a trait, unit type or play pattern (for example rerolling low-cost units, or leveling fast) that this comp does not use.",
    "This augment is generic (gold, items, or plain stats) and helps this comp about as much as any other comp.",
    "This augment directly boosts this comp's key trait, its main carry's damage type, or its leveling plan.",
    "This augment is made for this comp: it grants or boosts this comp's key trait or its main carry specifically.",
]

C3_LEVELS = [
    "The player's units share no units and no traits with this comp; switching means selling almost everything.",
    "A few low-cost units or one trait overlap with this comp's buildup boards, but none of its core units is owned.",
    "Several units match this comp's buildup boards and at least one of its core units is owned.",
    "The player already owns this comp's main carry or several of its core units, some of them at 2 stars.",
]

S1_OWNED_LEVELS = [
    "It would not be fielded: it is weaker than the units the player already fields and shares no trait with them.",
    "A usable filler: it could replace a weak unit or hold a trait for a few rounds, but adds little strength.",
    "A clear upgrade now: it activates or raises an active trait, or shop[{i}].buy_makes_2star is true, or it is a strong unit for this stage.",
    "One of the best pickups possible at this stage: it makes the current team much stronger immediately.",
]

S1_NOBOARD_LEVELS = [
    "A weak unit for this stage that appears in none of the candidate comps' buildup boards for the player's level.",
    "A usable filler for this stage: it could hold a slot or a trait for a few rounds, but adds little strength.",
    "A clear upgrade for this stage: a strong unit at this level, or it shares a key trait with the candidate comps' buildup boards for the player's level.",
    "One of the best pickups possible at this stage: strong on its own and a core unit of the top candidate comps' buildup boards.",
]

S2_LEVELS = [
    "It appears in none of the candidate comps' final boards or buildup boards and shares no key trait with them.",
    "It only shares a key trait with a candidate comp, or appears only in an early buildup board.",
    "It is a supporting unit in a candidate comp's final_board, or appears in the buildup board for the player's next level.",
    "It is the main carry or a core unit of the first or second comp in candidate_comps.",
]

S3_LEVELS = [
    "Not useful for this player now.",
    "Some value: a modest boost the player can use.",
    "High value: it directly strengthens the current team or the top candidate comp.",
]

A2_LEVELS_HP = [
    "Little or no value for this player: its condition is unlikely to be met, or its reward arrives too late for the player's health.",
    "Modest generic value: a little gold, small stats, or a minor item.",
    "Strong generic value: significant gold, a completed item, or a combat effect that works in most comps.",
    "Game-changing for this player's situation: a large immediate resource or effect, such as helping a low-health player stabilize or a rich player level fast.",
]

A2_LEVELS_NOHP = [
    "Little or no value for this player: its condition is unlikely to be met, or its reward arrives too late in the game to matter.",
    A2_LEVELS_HP[1],
    A2_LEVELS_HP[2],
    "Game-changing for this player's situation: a large immediate resource or effect, such as a big combat boost right now or enough gold to level fast.",
]

HOLD_HP = ("Do not combine yet: none of the buildable items is a core item for the candidate comps, "
           "and the player's health is high enough to wait for better components.")
HOLD_NOHP = ("Do not combine yet: none of the buildable items is a core item for the candidate comps, "
             "and it is early enough to wait for better components.")


def c1(k: int, comp: str) -> str:
    return (f"How well do the player's items (resources.completed_items, resources.emblems, resources.item_components, "
            f"resources.craftable_items) fit the team comp `candidate_comps[{k}]` (\"{comp}\")? Compare them with that "
            f"comp's main_carry_items and the items listed on its final_board units.")


def c2(k: int, comp: str) -> str:
    return (f"How well do the player's augments (resources.augments, read their descriptions) support playing the team "
            f"comp `candidate_comps[{k}]` (\"{comp}\")?")


# q3(2026-09-24, 21 §10): 2~3스테이지 유닛은 지나가는 빌드업 — 1성은 약한 증거, 2성·아이템 보유자만 의미 있게
EARLY_UNITS_NOTE = ("In stages 2 and 3 most units are temporary buildup units the player will sell later: treat 1-star "
                    "units as weak evidence and count mainly 2-star or better units and units holding items.")


def c3(k: int, comp: str) -> str:
    return (f"How close are the player's current units (board and bench) to the team comp `candidate_comps[{k}]` "
            f"(\"{comp}\"), using that comp's final_board and buildup boards? {EARLY_UNITS_NOTE}")


C4 = ("Which team comp in candidate_comps should the player aim for as their final comp, given their items, augments "
      "and units? Items and augments decide the final comp; units matter more later in the game. " + EARLY_UNITS_NOTE)
C4_UNDECIDED = ("It is too early to tell: the player's items, augments and units do not point to any one of these "
                "comps.")


def s1(i: int, unit: str, owned: bool) -> str:
    if owned:
        return (f"How much would buying the unit in `shop[{i}]` (\"{unit}\") strengthen the player's team for the fights "
                f"of the current stage (game.stage_phase), right now?")
    return (f"How much would buying the unit in `shop[{i}]` (\"{unit}\") strengthen a typical team for the fights of "
            f"the current stage (game.stage_phase), given the player's level? The player's current units are not known.")


def s2(i: int, unit: str) -> str:
    return (f"Does the unit in `shop[{i}]` (\"{unit}\") belong to the player's plan toward the comps in candidate_comps "
            f"(their final_board or their buildup boards)? candidate_comps is ordered from most to least likely.")


def s3(i: int, name: str, desc: str | None) -> str:
    if desc:
        return (f"How valuable is buying the special shop offer `shop[{i}]` (\"{name}\": \"{desc}\") for this player "
                f"right now?")
    return f"How valuable is buying the special shop offer `shop[{i}]` (\"{name}\") for this player right now?"


def a1(a: int, aug: str, k: int, comp: str) -> str:
    return (f"If the player takes the augment `augment_offer[{a}]` (\"{aug}\"), how well would it support playing the "
            f"team comp `candidate_comps[{k}]` (\"{comp}\")?")


def a2(a: int, aug: str, has_hp: bool) -> str:
    tail = "given their stage, health, gold and items?" if has_hp else "given their stage, gold and items?"
    return (f"Regardless of which comp the player ends up playing, how much does the augment `augment_offer[{a}]` "
            f"(\"{aug}\") help this player, {tail}")


A3 = "Which offered augment in augment_offer should the player take?"


def i1(has_hp: bool, has_board: bool) -> str:
    head = ("The player can combine two item components now (resources.item_components). Which completed item should "
            "they build first, considering the carries of candidate_comps")
    if has_hp:
        mid = ", the current team, and the player's health (game.health_status)?" if has_board else \
            " and the player's health (game.health_status)?"
    else:
        mid = ", the current team, and the current stage (game.stage_phase)?" if has_board else \
            " and the current stage (game.stage_phase)?"
    return head + mid
