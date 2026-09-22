"""Phase 2 QA: jev-strategist 설계 §10a "최종 설정 키 표"(2026-09-22)의 키를 현재 config.py 로더가 받는지 확인.

실행: .venv/bin/python _workspace/qa_scripts/config_proposal_check.py
현재 config/weights.toml, settings.toml + §10a 키(기존·신규 전부, 기본값)를 합친 임시 toml을 로더로 읽어
거부되는 키를 모두 출력한다. 이어서 로드된 값이 §10a 기본값과 같은지도 비교한다(값 불일치는 별도 줄로 출력).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from tft_advisor.config import load_settings, load_weights  # noqa: E402

# 기존 섹션에 합칠 키를 파일 뒤에 이어 붙이면 TOML 중복 테이블 오류가 나므로 기존 파일을 파싱해 dict 병합 후 다시 쓴다
import tomllib  # noqa: E402

# §10a weights.toml 표 전체(기존 + 신규). 신규 47개: comp 5, prefilter 15, item_fit 7, shop 10, augment 4, item 6
PROPOSED_WEIGHTS = {
    "comp": {"wi": 0.45, "wa": 0.25, "wb": 0.30, "wt": 0.2, "show_ratio": 0.75, "max_shown": 3,
             "hysteresis_bonus": 0.05,
             "stat_avg_best": 4.0, "stat_avg_worst": 5.2, "show_ratio_undecided": 0.6, "undecided_min_p": 0.5,
             "tie_eps": 0.02},
    "prefilter": {"min_games": 1000, "dedupe_jaccard": 0.75, "w_item": 0.40, "w_aug": 0.20, "w_unit": 0.25,
                  "w_stat": 0.15, "item_saturation": 2.0, "unit_saturation": 4.0, "craftable_factor": 0.5,
                  "aug_neutral": 0.5, "unit_w_core": 1.0, "unit_w_final": 0.6, "unit_w_buildup": 0.3,
                  "unit_star_mult": 1.5, "stat_quota": 2},
    "item_fit": {"carry_bis": 1.0, "core_unit": 0.7, "usage": 0.4, "emblem_key_trait": 1.0, "emblem_other": 0.2,
                 "usage_min_pcnt": 0.3, "used_by_min": 0.7},
    "shop": {"buy_threshold": 0.5,
             "jev_share_now": 0.7, "jev_share_path": 0.5, "two_star_bonus": 0.15, "three_star_bonus": 0.25,
             "hp_danger_shift": 0.15, "mu_core": 1.0, "mu_final": 0.7, "mu_next_buildup": 0.5,
             "mu_cur_buildup": 0.25, "special_fallback_score": 0.3},
    "shrinkage": {"k": 200, "prior_avg_place": 4.5},
    "jev": {"min_confidence": 0.5, "low_confidence_scale": 0.5},
    "augment": {"w_jev": 0.7, "w_editorial": 0.3,
                "w_comp": 0.65, "unlisted_score": 0.5, "tie_eps": 0.03,
                "commit_by_stage": {"2": 0.3, "3": 0.6, "4": 0.9}},
    "item": {"w_bis": 0.5, "w_jev": 0.35, "w_stat": 0.15, "place_change_span": 1.0, "hold_bis_max": 0.4,
             "hold_until_stage": 4},
}
# §10a settings.toml 표 전체. 신규 6개: advisor.jev_model/jev_timeout_s/jev_retry_budget_s/jev_max_retries/
# circuit_fail_threshold/circuit_cooldown_s
PROPOSED_SETTINGS = {
    "advisor": {"jev_enabled": True, "timeout_s": 2.0, "max_candidate_comps": 8, "cache_size": 64,
                "jev_model": "jev-latest", "jev_timeout_s": 1.2, "jev_retry_budget_s": 1.5, "jev_max_retries": 1,
                "circuit_fail_threshold": 3, "circuit_cooldown_s": 60},
    "vision": {"state_min_confidence": 0.6},
    "ui": {"max_target_comps": 3},
}

def dump_toml(d: dict, prefix: str = "") -> str:
    lines, subs = [], []
    for k, v in d.items():
        if isinstance(v, dict):
            subs.append((k, v))
        else:
            lines.append(f"{k} = {v!r}".replace("'", '"').replace("True", "true").replace("False", "false"))
    out = ""
    if prefix:
        out += f"[{prefix}]\n"
    out += "\n".join(lines) + ("\n" if lines else "")
    for k, v in subs:
        key = f'{prefix}."{k}"' if k.isdigit() else (f"{prefix}.{k}" if prefix else k)
        out += dump_toml(v, key)
    return out


def merged(base_file: Path, extra: dict) -> dict:
    base = tomllib.loads(base_file.read_text(encoding="utf-8"))
    for sec, kv in extra.items():
        base.setdefault(sec, {})
        for k, v in kv.items():
            base[sec][k] = v
    return base


def run(kind: str, base_file: Path, extra: dict, loader) -> list[str]:
    data = merged(base_file, extra)
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / base_file.name).write_text(dump_toml(data), encoding="utf-8")
        try:
            loader(Path(td))
            return []
        except ValidationError as e:
            return [f"{kind}: {'.'.join(map(str, err['loc']))} ({err['type']})" for err in e.errors()]


def value_mismatches(kind: str, loaded, proposed: dict) -> list[str]:
    out = []
    for sec, kv in proposed.items():
        model = getattr(loaded, sec)
        for k, v in kv.items():
            got = getattr(model, k)
            if isinstance(v, dict):
                got = {str(kk): vv for kk, vv in got.items()}
            if got != v:
                out.append(f"{kind}: {sec}.{k} = {got!r} (§10a 기본값 {v!r})")
    return out


if __name__ == "__main__":
    n_keys = sum(len(v) for v in PROPOSED_WEIGHTS.values()) + sum(len(v) for v in PROPOSED_SETTINGS.values())
    errs = run("weights", ROOT / "config/weights.toml", PROPOSED_WEIGHTS, load_weights)
    errs += run("settings", ROOT / "config/settings.toml", PROPOSED_SETTINGS, load_settings)
    print(f"§10a keys checked: {n_keys}")
    print(f"rejected keys: {len(errs)}")
    for e in errs:
        print(" ", e)
    if not errs:
        diffs = value_mismatches("weights", load_weights(), PROPOSED_WEIGHTS)
        diffs += value_mismatches("settings", load_settings(), PROPOSED_SETTINGS)
        print(f"config files differ from §10a defaults: {len(diffs)}")
        for d in diffs:
            print(" ", d)
