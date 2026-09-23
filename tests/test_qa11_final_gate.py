"""QA 11 최종 게이트: 08 QA 이후 변경(보유 증강 병합·선택 순간 학습·직전 추천 표시·새 판 확인·patch_version)의 경계면 회귀.

- 보유 증강: vision 전체 목록 판독(_track_augments) ↔ 선택 순간 학습(_learn_owned) ↔ advisor 입력(_apply_augments).
- 직전 추천: 표시용 사본만 거르고 원본 Recommendation은 불변.
- patch_version: stats(latest_json)와 advisor(default_stats_path)가 같은 파일을 고른다.
- git 위생: Riot 아트·개인 캡처·실행 상태·DB가 gitignore에 걸린다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tft_advisor.app.report import kept_view
from tft_advisor.app.session import SessionTracker
from tft_advisor.contracts import (
    AugmentRef, FieldSource, GameState, Recommendation, ScreenMode, ShopAdvice, ShopSlot, ShopSlotKind,
)
from tft_advisor.vision.augment_learn import OwnedRow

ROOT = Path(__file__).resolve().parents[1]
CM, VERT, ASC, FOUR = "DA_ClutteredMind", "DA_18_Verticality", "DA_Ascension", "DA_18_FOURcing"


def _planning(stage: str, ids: list[str | None]) -> GameState:
    """실제 인식기와 같은 규칙: 칸이 전부 인식됐을 때만 augments_owned가 채워진다."""
    full = all(i is not None for i in ids)
    return GameState(
        screen_mode=ScreenMode.PLANNING, stage=stage,
        augments_owned=[AugmentRef(id=i, confidence=0.9) for i in ids] if full else None,
        field_source={"augments_owned": FieldSource.VISION} if full else {},
        confidence={"augments_owned": 0.9} if full else {})


def _row(ids: list[str | None]) -> OwnedRow:
    return OwnedRow(cells=[np.zeros((36, 36, 3), np.uint8) for _ in ids], ids=list(ids), scores=[0.9] * len(ids))


def _offer(stage: str, ids: list[str]) -> GameState:
    return GameState(screen_mode=ScreenMode.AUGMENT_SELECT, stage=stage,
                     augment_offer=[AugmentRef(id=i, confidence=0.9) for i in ids])


def _observe(t: SessionTracker, stage: str, ids: list[str | None]) -> GameState:
    return t.observe(_planning(stage, ids), {"owned"}, owned_row=_row(ids))


# --------------------------------------------------------------------------- 보유 증강 경계


def test_advisor_augments_always_equal_session_list():
    """advisor에 가는 augments_owned는 언제나 세션 목록과 같다(버린 vision 값이 새지 않는다)."""
    t = SessionTracker()
    frames = [("2-2", [CM]), ("2-5", [CM, VERT]), ("3-1", ["DA_Other"]), ("3-2", [CM, VERT]), ("3-3", [CM])]
    for stage, ids in frames:
        m = _observe(t, stage, ids)
        got = [a.id for a in m.augments_owned] if m.augments_owned else []
        assert got == t.data.augments_owned, (stage, ids)
    assert t.data.augments_owned == [CM, VERT] and t.augments_rejected == 3


def test_manual_list_survives_vision_and_learning_rows():
    t = SessionTracker()
    t.set_augments_owned([ASC])
    t.observe(_offer("3-2", [VERT, FOUR, CM]), {"augment"})
    m = _observe(t, "3-2", [CM, VERT])          # 첫 칸이 수동과 다름 → vision·학습 모두 거부
    assert [a.id for a in m.augments_owned] == [ASC]
    assert m.field_source["augments_owned"] == FieldSource.MANUAL and not t.data.learned


def test_reset_clears_learning_state_and_owned_augments(tmp_path):
    t = SessionTracker(tmp_path / "session.json")
    _observe(t, "3-1", [CM])
    t.observe(_offer("3-2", [VERT, FOUR, ASC]), {"augment"})
    assert t.data.offer_pool and t.data.augments_owned
    t.reset("game_over")
    d = t.data
    assert (d.augments_owned, d.offer_pool, d.offer_base, d.owned_count, d.learned) == ([], [], None, None, [])
    assert t.last_archive is not None and t.last_archive.is_file()


@pytest.mark.xfail(strict=True, reason="QA11 W1: vision 전체 목록 판독은 증강 선택 화면에서 제시되지 않은 증강으로 "
                                       "칸을 늘려도 받는다(학습 경로는 같은 판독을 '제시되지 않음'으로 거부). "
                                       "app-integrator가 규칙을 정하면 이 xfail을 푼다.")
def test_vision_growth_must_be_among_offered_augments():
    t = SessionTracker()
    _observe(t, "3-1", [CM])
    t.observe(_offer("3-2", [ASC, VERT, FOUR]), {"augment"})
    m = _observe(t, "3-2", [CM, "DA_BonusGift"])
    assert [a.id for a in m.augments_owned] == [CM]


# --------------------------------------------------------------------------- 직전 추천 표시


def _rec() -> Recommendation:
    return Recommendation(
        jev_used=True,
        shop=[ShopAdvice(slot=0, kind=ShopSlotKind.CHAMPION, offer_id="DA_18_Xayah", buy=True, score=0.8),
              ShopAdvice(slot=1, kind=ShopSlotKind.CHAMPION, offer_id="DA_18_Yorick", buy=False, score=0.3)])


def test_kept_view_never_mutates_original_and_is_idempotent():
    rec = _rec()
    before = rec.model_dump()
    shop = [ShopSlot(kind=ShopSlotKind.EMPTY), ShopSlot(kind=ShopSlotKind.CHAMPION, id="DA_18_Yorick"),
            ShopSlot(kind=ShopSlotKind.UNKNOWN), ShopSlot(kind=ShopSlotKind.EMPTY), ShopSlot(kind=ShopSlotKind.EMPTY)]
    state = GameState(screen_mode=ScreenMode.COMBAT, shop=shop)
    shown, kept = kept_view(rec, state)
    again, _ = kept_view(shown, state)
    assert rec.model_dump() == before
    assert [a.offer_id for a in shown.shop] == ["DA_18_Yorick"] and kept.bought == 1
    assert again.shop == shown.shop


# --------------------------------------------------------------------------- patch_version 일관성


def test_stats_and_advisor_pick_same_snapshot(tmp_path):
    from tft_advisor.advisor.stats_source import default_stats_path
    from tft_advisor.stats.repository import latest_json

    stats = tmp_path / "stats"
    stats.mkdir()
    for i, n in enumerate(["18.10", "18.9", "18.2b", "18.3"]):
        p = stats / f"metatft_{n}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (5000 - i * 100, 5000 - i * 100) if n != "18.10" else (100, 100))
    assert latest_json(stats) == default_stats_path(tmp_path) == stats / "metatft_18.10.json"


# --------------------------------------------------------------------------- git 위생


PRIVATE_PATHS = [
    "data/templates/18/augments/DA_Ascension.png",
    "data/templates/18/augments_alt/sources.json",
    "data/templates/18/augments_alt/DA_Verticality.png",
    "data/templates/18/augments_screen/DA_Ascension.png",
    "data/templates/18/items/x.png",
    "data/templates/18/items_screen/x.png",
    "tests/fixtures/screens/raw/2-2 전투 전.png",
    "tests/fixtures/screens/raw/2-2 전투 전.expected.json",
    "_state/session.json",
    "_state/sessions/session_20260922_000000_000000.json",
    "data/raw/metatft/2026-09-22/comps_data.json",
    "data/stats/stats.sqlite",
    "data/stats/stats.sqlite-wal",
    "data/stats/stats.sqlite-shm",
    ".env",
]


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="git 저장소 아님")
def test_private_assets_are_gitignored():
    out = subprocess.run(["git", "-c", "core.quotepath=off", "check-ignore", "--no-index", *PRIVATE_PATHS],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    ignored = {line.strip() for line in out.stdout.splitlines() if line.strip()}
    assert ignored == set(PRIVATE_PATHS), sorted(set(PRIVATE_PATHS) - ignored)
