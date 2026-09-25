"""유닛 사진 수집기(`vision.unit_db.UnitCollector`)를 끄는 도우미 — 실시간(`--live`) 밖의 인식 실행용.

수집기는 실제 게임 스트림에서 증거가 강한 크롭을 사용자 DB의 검토 대기(`units_screen/_pending/`)에 모은다.
`--screenshot`(`--test-view` 포함)·설정 창 테스트 캡처 같은 한 장짜리 실행이 거기에 쌓이면 QA/테스트 이미지가
사용자 DB를 더럽힌다(QA 27 W5: test.png 2-6에서 `traits` 5장). 그래서 이런 실행은 수집기 없이 만든다.

`vision/units.py`·`vision/unit_db.py`에는 끄는 스위치가 따로 없으므로(공개 면: `VisionConfig.unit_autolearn`,
`UnitNamer.collector`/`autolearn`) 여기서 두 가지를 쓴다:
- `no_collect_vision(cfg)`: 만들기 전에 `unit_autolearn=False`로 복사 — 수집기를 아예 만들지 않는다
  (`UnitImageDB.migrate_legacy()`의 파일 이동도 일어나지 않는다).
- `disable_unit_collector(recognizer)`: 이미 만들어진 인식기(호출자가 넘긴 것)의 `unit_namer.collector`를 None으로,
  `autolearn` 표시도 False로. 이번 실행 **메모리** 표본 학습(`UnitNamer._learn`, 디스크에 쓰지 않음)은 그대로다.
승인 라이브러리 읽기는 바뀌지 않는다(`unit_autolearn`과 무관하게 같은 폴더의 승인 크롭을 읽는다).
"""
from __future__ import annotations

from typing import Any


def no_collect_vision(cfg: Any) -> Any:
    """`VisionConfig` 복사본(`unit_autolearn=False`). pydantic 모델이 아니면 그대로 돌려준다."""
    copy = getattr(cfg, "model_copy", None)
    if copy is None or not getattr(cfg, "unit_autolearn", False):
        return cfg
    return copy(update={"unit_autolearn": False})


def disable_unit_collector(recognizer: Any) -> bool:
    """인식기의 유닛 사진 수집을 끈다. 반환: 켜져 있던 수집기를 껐으면 True."""
    namer = getattr(recognizer, "unit_namer", None)
    if namer is None:
        return False
    had = getattr(namer, "collector", None) is not None
    if hasattr(namer, "collector"):
        namer.collector = None
    if hasattr(namer, "autolearn"):
        namer.autolearn = False
    return had


def make_recognizer_no_collect(vision_cfg: Any):
    """수집기 없는 `Recognizer`(스크린샷·테스트 캡처용)."""
    from ..vision.recognizer import Recognizer

    rec = Recognizer(cfg=no_collect_vision(vision_cfg))
    disable_unit_collector(rec)
    return rec
