"""Phase 2 QA: jev-strategist 설계 10절 weights/settings 추가 키를 현재 config.py 로더가 받는지 확인.

실행: .venv\\Scripts\\python _workspace/qa_scripts/config_proposal_check.py
현재 config/weights.toml + 제안 키를 합친 임시 toml을 load_weights()로 읽어 거부되는 키를 모두 출력한다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from tft_advisor.config import load_settings, load_weights  # noqa: E402

# 설계 10절 그대로(기존 섹션에 합칠 키는 섹션별로 분리해 기존 파일 뒤에 이어 붙이면 TOML 중복 테이블 오류가 나므로
# 기존 파일을 파싱해 dict 병합 후 다시 쓴다)
import tomllib  # noqa: E402

PROPOSED_WEIGHTS = {
    "comp": {"stat_avg_best": 4.0, "stat_avg_worst": 5.2, "show_ratio_undecided": 0.6, "tie_eps": 0.02},
    "prefilter": {"min_games": 1000, "dedupe_jaccard": 0.75, "w_item": 0.40, "w_aug": 0.20, "w_unit": 0.25,
                  "w_stat": 0.15, "item_saturation": 2.0, "unit_saturation": 4.0},
    "shop": {"jev_share_now": 0.7, "jev_share_path": 0.5, "two_star_bonus": 0.15, "three_star_bonus": 0.25,
             "hp_danger_shift": 0.15},
    "item": {"w_bis": 0.5, "w_jev": 0.35, "w_stat": 0.15, "place_change_span": 1.0, "hold_bis_max": 0.4,
             "hold_until_stage": 4},
    "augment": {"w_comp": 0.65, "unlisted_score": 0.5, "tie_eps": 0.03,
                "commit_by_stage": {"2": 0.3, "3": 0.6, "4": 0.9}},
}
PROPOSED_SETTINGS = {
    "advisor": {"jev_timeout_s": 1.2, "jev_retry_budget_s": 1.5, "jev_max_retries": 1,
                "circuit_fail_threshold": 3, "circuit_cooldown_s": 60},
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


if __name__ == "__main__":
    errs = run("weights", ROOT / "config/weights.toml", PROPOSED_WEIGHTS, load_weights)
    errs += run("settings", ROOT / "config/settings.toml", PROPOSED_SETTINGS, load_settings)
    print(f"rejected keys: {len(errs)}")
    for e in errs:
        print(" ", e)
