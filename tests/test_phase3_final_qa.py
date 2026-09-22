"""QA Phase 3 최종 게이트 회귀(_workspace/04_qa_phase3_final.md).

- 라이브러리 폴백 기본값 = 설정 기본값(`db.DEFAULT_KEEP` ↔ `[stats] keep_snapshots`)
- 삭제된 `[capture] poll_interval_ms` / `stable_frames`가 설정 모델·TOML에 다시 생기지 않음
- 커밋 위생: Riot 아이콘·SQLite 부속 파일·원시 수집 데이터가 .gitignore로 제외됨(git 없으면 skip)
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tft_advisor.config import CaptureCfg, StatsCfg

ROOT = Path(__file__).resolve().parents[1]


def test_db_default_keep_matches_settings_default():
    from tft_advisor.stats import db

    assert db.DEFAULT_KEEP == StatsCfg().keep_snapshots


def test_removed_capture_keys_stay_removed():
    assert set(CaptureCfg.model_fields) == {"monitor"}
    toml = (ROOT / "config" / "settings.toml").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*(poll_interval_ms|stable_frames)\s*=", toml)


@pytest.mark.parametrize("path", [
    "data/stats/stats.sqlite",
    "data/stats/stats.sqlite-wal",
    "data/stats/stats.sqlite-shm",
    "data/stats/stats.sqlite-journal",
    "data/raw/metatft/x.json",
    "data/templates/18/items/DA_Component_BFSword.png",
    "data/templates/18/items_screen/DA_Component_BFSword.png",
    ".env",
])
def test_gitignore_excludes_non_committable(path):
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git 저장소 아님")
    r = subprocess.run([git, "check-ignore", "-q", "--no-index", path], cwd=ROOT, check=False)
    assert r.returncode == 0, f"{path} 가 .gitignore로 제외되지 않음"
