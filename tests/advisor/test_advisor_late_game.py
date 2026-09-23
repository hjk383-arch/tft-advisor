"""09 jev-strategist: 후반 방향 표시(J1), 히스테리시스 불변식(s09 검토), 보유 증강 반영, 최신 통계 파일 선택.

네트워크 없음(mock Jev). 실제 저장소가 필요한 테스트는 `real_stats` 마크 + 저장소 없으면 skip.
"""
from __future__ import annotations

import copy
import json

import pytest

from tft_advisor.advisor import Advisor, MockJevBackend
from tft_advisor.advisor.candidates import late_cfg
from tft_advisor.advisor.questions import UNDECIDED
from tft_advisor.advisor.stats_source import default_stats_path, patch_sort_key
from tft_advisor.contracts import GameState

from .conftest import MINI, load_fixture


def _open_real():
    from tft_advisor.stats.repository import StatsNotFound, open_repository

    try:
        return open_repository()
    except StatsNotFound as e:   # pragma: no cover - 통계 미수집 환경
        pytest.skip(f"실제 통계 없음: {e}")


def _open_inmem_mini():
    from tft_advisor.stats.repository import InMemoryStatsRepository

    return InMemoryStatsRepository.from_doc(json.loads(MINI.read_text(encoding="utf-8")))


@pytest.fixture(scope="module", params=["inmem_mini", "real"])
def any_stats(request):
    return _open_real() if request.param == "real" else _open_inmem_mini()


def _late_raw() -> dict:
    return copy.deepcopy(load_fixture("s14_late_blind")["state"])


def _advise(stats, settings, weights, raw: dict, **mock):
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=MockJevBackend(**mock))
    return adv, adv.advise(GameState.model_validate(raw))


# ---------------------------------------------------------------------------
# J1: 후반에는 '초반: 방향 미정'을 쓰지 않고 레벨 템포로 좁힌다
# ---------------------------------------------------------------------------

def test_late_blind_never_says_early(any_stats, settings, weights):
    adv, rec = _advise(any_stats, settings, weights, _late_raw())
    assert rec.debug["p_undecided"] == 0.0 and rec.debug["blind_late"] is True
    assert not any("방향 미정" in r for t in rec.target_comps for r in t.reasons)
    # 1위 덱은 이 스테이지에 레벨 9에 도달하는 덱(템포 1.0)이다
    cand = {c["comp_id"]: c for c in rec.debug["candidates"]}
    top = cand[rec.target_comps[0].comp_id]
    assert top["T"] == 1.0 and top["T_exp"] == 9 and top["terms"]["tempo"]["src"] == "code"
    assert "레벨 템포 일치" in rec.target_comps[0].reasons[0]
    # 오버레이는 근거 앞 3개만 보여 준다 → 안내 문구가 그 안에 있어야 한다
    assert any("레벨 템포·메타로 추정" in r for r in rec.target_comps[0].reasons[:3])
    # Jev comp_pick에 '너무 이르다' 선택지가 없다
    assert UNDECIDED not in adv.gateway.backend.last_questions["comp_pick"]["criteria"]


def test_early_blind_still_undecided(stats, settings, weights):
    raw = _late_raw()
    raw.update(stage="2-5", level=4, xp=[2, 10])
    adv, rec = _advise(stats, settings, weights, raw)
    assert rec.debug["blind_late"] is False
    assert rec.debug["p_undecided"] >= weights.comp.undecided_min_p
    assert any("초반: 방향 미정" in r for r in rec.target_comps[0].reasons)
    assert all("tempo" not in c["terms"] for c in rec.debug["candidates"])
    assert UNDECIDED in adv.gateway.backend.last_questions["comp_pick"]["criteria"]


def test_undecided_until_stage_boundary(stats, settings, weights):
    """3스테이지는 여전히 '초반'(자원 없으면 방향 미정), 4스테이지부터 후반."""
    until = late_cfg(weights).undecided_until_stage
    for stage, late in ((f"{until - 1}-5", False), (f"{until}-1", True)):
        raw = _late_raw()
        raw["stage"] = stage
        _, rec = _advise(stats, settings, weights, raw)
        assert rec.debug["blind_late"] is late, stage
        assert (rec.debug["p_undecided"] >= weights.comp.undecided_min_p) is (not late), stage


def test_tempo_reorders_away_from_reroll_at_level9(stats, settings, weights):
    """mini: 통계 1위(executioner-khazix, lvl 7 운영: 5-5에 보통 레벨 7)는 레벨 9 플레이어의 목표 1위가 아니다."""
    _, rec = _advise(stats, settings, weights, _late_raw())
    cand = {c["comp_id"]: c for c in rec.debug["candidates"]}
    assert max(cand.values(), key=lambda c: c["S"])["comp_id"] == "executioner-khazix"
    assert cand["executioner-khazix"]["T"] == 0.0
    assert rec.target_comps[0].comp_id != "executioner-khazix"


def test_tempo_kept_with_augment_but_not_with_board(any_stats, settings, weights):
    """템포 항은 '후반 + 보드 미인식'일 때 보드 항을 대신한다: 증강이 생겨도 유지, 보드를 알면 빠진다."""
    raw = _late_raw()
    raw["augments_owned"] = [{"id": "DA_ClutteredMind"}]
    _, rec = _advise(any_stats, settings, weights, raw)
    assert rec.debug["blind_late"] is False
    assert all("tempo" in c["terms"] for c in rec.debug["candidates"] if c["T"] is not None)
    assert not any("방향 미정" in r for t in rec.target_comps for r in t.reasons)

    raw = _late_raw()
    raw["board"] = [{"id": "DA_18_Zyra", "star": 2}, {"id": "DA_Amumu18", "star": 2}]
    raw["bench"] = []
    _, rec = _advise(any_stats, settings, weights, raw)
    assert all("tempo" not in c["terms"] for c in rec.debug["candidates"])


# ---------------------------------------------------------------------------
# 보유 증강 반영(09 §3)
# ---------------------------------------------------------------------------

@pytest.mark.real_stats
def test_owned_augment_moves_comp_scores(settings, weights):
    """실제 18.3: 초월(DA_Ascension)은 덱별 편집 등급이 자이라 S / 장로 드래곤 A다 → 자이라 점수가 더 오른다."""
    repo = _open_real()
    if repo.augment_tier("DA_Ascension", "juggernaut-zyra-amumu") is None:
        pytest.skip("이 패치 통계에 초월 덱별 등급 없음")

    def scores(augs):
        raw = _late_raw()
        raw["augments_owned"] = [{"id": a} for a in augs]
        _, rec = _advise(repo, settings, weights, raw)
        return {c["comp_id"]: c["score"] for c in rec.debug["candidates"]}, rec

    base, _ = scores([])
    asc, rec = scores(["DA_Ascension"])
    assert rec.target_comps[0].comp_id == "juggernaut-zyra-amumu"
    assert any("초월" in r for r in rec.target_comps[0].reasons)
    common = set(base) & set(asc)
    for cid in {"juggernaut-zyra-amumu", "juggernaut-elderdragon"} & common:
        assert asc[cid] > base[cid], cid
    if {"juggernaut-zyra-amumu", "juggernaut-elderdragon"} <= common:
        dz = asc["juggernaut-zyra-amumu"] - base["juggernaut-zyra-amumu"]
        de = asc["juggernaut-elderdragon"] - base["juggernaut-elderdragon"]
        assert dz > de


def test_unknown_owned_augment_lowers_jev_gate(any_stats, settings, weights):
    """정적 데이터에 없는 증강(18.3 stats 전용 ID 등)만 보유 → Jev 증강 판단은 낮은 gate, 크래시 없음."""
    raw = _late_raw()
    raw["augments_owned"] = [{"id": "DA_Lineup"}, {"id": "DA_NoSuchAugment"}]
    _, rec = _advise(any_stats, settings, weights, raw)
    st = rec.debug["jev_state"]["resources"]["augments"]
    assert all(a["description"] == "unknown (new augment)" for a in st)
    for c in rec.debug["candidates"]:
        assert c["terms"]["augment"]["src"] == "jev"
        assert c["terms"]["augment"]["gate"] == weights.jev.low_confidence_scale


def test_augment_select_follows_resources(any_stats, settings, weights):
    """증강 선택: 알 수 없는 증강이 섞여도 추천이 나오고, 각 증강의 '최적 덱'은 현재 목표 덱(자원 기반) 중 하나다."""
    raw = {"screen_mode": "augment_select", "stage": "3-2", "level": 6, "gold": 30, "hp": 70,
           "items": {"completed": [{"id": "DA_ArchangelsStaff"}, {"id": "DA_JeweledGauntlet"}], "components": []},
           "augments_owned": [{"id": "DA_Ascension"}],
           "augment_offer": [{"id": "DA_ClutteredMind"}, {"id": "DA_PartialAscension"}, {"id": "DA_Lineup"}]}
    _, rec = _advise(any_stats, settings, weights, raw)
    assert rec.augment is not None and rec.augment.pick in {"DA_ClutteredMind", "DA_PartialAscension", "DA_Lineup"}
    rows = {r["id"]: r for r in rec.debug["augment"]}
    assert rows["DA_Lineup"]["score"] < rows[rec.augment.pick]["score"]   # 설명 없는 증강은 gate가 낮아 1위가 아니다
    cand_ids = {c["comp_id"] for c in rec.debug["candidates"]}
    assert all(r["best"] in cand_ids for r in rows.values())


# ---------------------------------------------------------------------------
# s09 검토: 히스테리시스는 '근소한 뒤집힘'만 막고 확실한 변화는 따라간다(데이터 독립)
# ---------------------------------------------------------------------------

def _hyst_steps(stats, settings, weights, delta: float):
    """s09 1스텝 → 시그니처가 같은 2스텝에서 1위 덱의 item 판단만 낮춰, 무보너스 점수로 2위가 delta만큼 앞서게 한다."""
    fx = load_fixture("s09_hysteresis")
    be = MockJevBackend()
    adv = Advisor(stats=stats, settings=settings, weights=weights, backend=be)
    r1 = adv.advise(GameState.model_validate(fx["steps"][0]["state"]))
    rows = sorted(r1.debug["candidates"], key=lambda c: -c["final"])
    t1, t2 = rows[0], rows[1]
    k1 = next(i for i, c in enumerate(r1.debug["candidates"]) if c["comp_id"] == t1["comp_id"])
    gap = t1["score"] - t2["score"]
    cw = weights.comp
    slope = (1 - cw.wt) * cw.wi * t1["terms"]["item"]["gate"]   # d score / d item_norm
    target = t1["terms"]["item"]["norm"] - (gap + delta) / slope
    assert 0.0 <= target <= 1.0, target
    be.overrides[f"comp_item_fit_{k1}"] = target
    r2 = adv.advise(GameState.model_validate(fx["steps"][1]["state"]))
    return t1["comp_id"], t2["comp_id"], r2


@pytest.mark.parametrize("kind", ["inmem_mini", "real"])
def test_hysteresis_holds_near_tie_and_follows_clear_change(kind, settings, weights):
    stats = _open_real() if kind == "real" else _open_inmem_mini()
    cw = weights.comp
    margin = cw.hysteresis_bonus * (1 - cw.hysteresis_other_share)   # 직전 1위가 직전 2위보다 받는 보호폭
    # 1) 무보너스로 2위가 tie_eps보다 크고 margin보다 작게 앞서도 1위 유지 + '직전 추천 유지' 표시.
    #    tie_eps 이하면 comp_pick 타이브레이커가 따로 막으므로, 히스테리시스만 검증하려고 그 위를 쓴다.
    if not cw.tie_eps < margin:
        pytest.skip("tie_eps >= 보호폭: 히스테리시스 단독 구간이 없다")
    a, b, r2 = _hyst_steps(stats, settings, weights, (cw.tie_eps + margin) / 2)
    assert r2.debug["sig_unchanged"] is True
    ids = [t.comp_id for t in r2.target_comps]
    assert ids[0] == a, (a, b, ids)
    assert any("직전 추천 유지" in r for r in r2.target_comps[0].reasons)
    # 2) margin + tie_eps 를 넘게 앞서면 새 1위를 따른다(고착 없음)
    a, b, r2 = _hyst_steps(stats, settings, weights, margin + cw.tie_eps + 0.02)
    assert r2.target_comps[0].comp_id == b, (a, b, [t.comp_id for t in r2.target_comps])


# ---------------------------------------------------------------------------
# 최신 통계 JSON: 패치 번호를 숫자로 비교
# ---------------------------------------------------------------------------

def test_patch_sort_key_numeric():
    order = ["18.2", "18.2b", "18.3", "18.9", "18.10", "19.1"]
    assert sorted(reversed(order), key=patch_sort_key) == order


def test_default_stats_path_two_digit_patch(tmp_path):
    d = tmp_path / "stats"
    d.mkdir()
    for p in ("18.2b", "18.9", "18.10", "18.3"):
        (d / f"metatft_{p}.json").write_text("{}", encoding="utf-8")
    assert default_stats_path(tmp_path).name == "metatft_18.10.json"
    assert default_stats_path(tmp_path / "none") is None
