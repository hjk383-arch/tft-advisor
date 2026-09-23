"""패치 번호 비교 — stats와 advisor가 같이 쓴다(모듈 간 의존을 만들지 않으려고 최상위에 둔다).

파일명 사전순(`"18.10" < "18.9"`)이나 수정 시각(git checkout·복사 뒤 뒤섞임)으로 최신 패치를 고르면 틀린다.
"""
from __future__ import annotations

import re
from pathlib import Path

_TOKEN = re.compile(r"\d+|[A-Za-z]+")


def patch_sort_key(patch: str) -> tuple:
    """패치 문자열 → 정렬 키. 숫자 부분은 정수로 비교한다("18.10" > "18.9", "18.3" > "18.2b" > "18.2").

    숫자·문자 조각을 (0, int, "") / (1, 0, str)로 만든다.
    """
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in _TOKEN.findall(patch))


def snapshot_sort_key(path: Path, prefix: str) -> tuple:
    """`{prefix}{패치}.json` 파일 정렬 키: (패치 번호 숫자 비교, 수정 시각, 파일명). 수정 시각은 같은 패치끼리만 가른다."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return patch_sort_key(path.stem.removeprefix(prefix)), mtime, path.name


def latest_snapshot(directory: Path, prefix: str, suffix: str = ".json") -> Path | None:
    """`directory/{prefix}*{suffix}` 중 패치 번호가 가장 큰 파일. 없으면 None."""
    return max(directory.glob(f"{prefix}*{suffix}"), key=lambda p: snapshot_sort_key(p, prefix), default=None)
