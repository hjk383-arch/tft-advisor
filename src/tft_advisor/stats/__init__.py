"""통계 수집·정규화·조회 (소유: stats-researcher). 수집은 실시간 루프 밖 배치, 루프는 로컬 DB만 읽는다.

- 런타임 조회: `tft_advisor.stats.repository` (`open_repository()` → `StatsRepository`)
- 배치 갱신:   `python -m tft_advisor.stats refresh` (수집 → 변환 → data/stats/metatft_{patch}.json + SQLite → diff)
- 구성: collectors/metatft.py(원본 수집), metatft_convert.py(원본 → 계약 모델), db.py(SQLite 스냅샷),
        diff.py(스냅샷 변경 목록), refresh.py(배치 파이프라인), static_extract.py(CDragon 정적 데이터)

출력 계약: contracts.CompStats / AugmentTier / UnitStats / UnitItemStats / PlacementStats
"""
