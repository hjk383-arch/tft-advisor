"""실시간 루프·오버레이 UI (소유: app-integrator). 상태 변화 기반 호출, 추천은 백그라운드 실행.

| 모듈 | 역할 |
|---|---|
| `session.py` | 부분 인식 결과 병합(`merge_state`), 보유 증강·구매 추적, `_state/session.json` 영속 |
| `loop.py` | `LiveLoop` — 캡처 → 변화 감지 → 부분 인식 → 병합 → 추천(별도 스레드) |
| `report.py` | 한국어 표시 문구(콘솔·오버레이 공용). `Recommendation` → 사람이 읽는 줄 |
| `names.py` | ID → 한국어 표시 이름 |
| `overlay.py` | PySide6 오버레이 창(프레임 없음·항상 위·반투명·클릭 통과) |
| `platform_window.py` | 플랫폼별 창 동작(항상 위/클릭 통과)과 실패 시 대체 |
| `screenshot.py` | `--screenshot` 모드 |
| `live.py` | `--live` 배선(오버레이 또는 콘솔) |
| `recog_view.py` | 인식 확인 표시 모델(보드·벤치·장착/미사용 아이템, 순수 함수) — 창·콘솔 공용 |
| `recog_window.py` | 인식 확인 창(PySide6, 항상 위·포커스 안 가져감) + 트레이 토글 컨트롤러 |

UI 표시 계약: `Recommendation.target_comps`는 **advisor가 준 순서 그대로** 보여 준다(점수로 재정렬 금지).
"""
from .report import format_report
from .session import SessionTracker, merge_state

__all__ = ["SessionTracker", "format_report", "merge_state"]
