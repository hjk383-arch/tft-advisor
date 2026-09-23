"""추천 엔진 (소유: jev-strategist). 통계 + TypeSafe Jev 합성.
API 키: 환경변수 TYPESAFE_API_KEY → OS 키체인 → ~/.config/tft-advisor/ (tft_advisor.credentials).

입력: contracts.GameState + 통계(AdvisorStats) / 출력: contracts.Recommendation

    from tft_advisor.advisor import create_advisor
    adv = create_advisor()            # 설정 [advisor] jev_backend(기본 "mock"). 명시: "mock" | "live" | "off"
    rec = adv.advise(game_state)      # 동기. 비동기는 `await adv.recommend(game_state)`
"""
from .engine import Advisor, advise, create_advisor, resource_signature
from .jev_client import JevGateway, LiveJevBackend, MockJevBackend
from .questions import QUESTIONS_VERSION
from .stats_source import AdvisorStats, JsonStatsAdapter, load_stats

__all__ = [
    "Advisor", "AdvisorStats", "JevGateway", "JsonStatsAdapter", "LiveJevBackend", "MockJevBackend",
    "QUESTIONS_VERSION", "advise", "create_advisor", "load_stats", "resource_signature",
]
