"""tests/fixtures/stats/mini_18.json 생성기(advisor 테스트용 결정적 통계 부분집합).

실행: .venv/bin/python tests/fixtures/stats/build_mini.py [원본 metatft_*.json]
원본(기본: data/stats/ 최신)에서 아래 덱 11개와 해당 증강 등급(전체+덱별), 단일 아이템 UnitItemStats만 뽑는다.
원본 통계가 재수집돼도 테스트 기대값이 흔들리지 않게 하려는 파일이다. 재생성하면 테스트 기대값을 다시 확인할 것.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPS = [
    "juggernaut-zyra-amumu",      # AP(대천사) 캐리
    "lunar-aphelios-nidalee_ap",  # AD(죽음의 검/구인수/크라켄)
    "invoker-ahri",
    "executioner-khazix",         # BIS에 유물(리치베인), Elderwood 3
    "spellweaver-veigar",         # BIS에 유물(여명심장), Elderwood 3
    "executioner-draven",         # Elderwood 3, BIS 상징
    "juggernaut-caitlyn",         # BIS에 찬란한 구인수
    "inferno-ashe",               # BIS 상징(Inferno)
    "juggernaut-elderdragon",     # 표본 최대
    "elderwood-aphelios",         # Elderwood 상징 BIS
    "lunar-aphelios-kayle",       # games 604 < min_games → 1차 필터 제외 확인용
]


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else max((ROOT / "data/stats").glob("metatft_*.json"))
    raw = json.loads(src.read_text(encoding="utf-8"))
    comps = [c for c in raw["comps"] if c["comp_id"] in COMPS]
    assert len(comps) == len(COMPS), sorted(set(COMPS) - {c["comp_id"] for c in comps})
    keep = set(COMPS)
    out = {
        "report": {"patch": raw["report"]["patch"], "note": f"mini subset of {src.name} for advisor tests"},
        "comps": comps,
        "augment_tiers": [a for a in raw["augment_tiers"] if a.get("comp_id") in keep or a.get("comp_id") is None],
        "unit_item_stats": [u for u in raw["unit_item_stats"]
                            if u.get("comp_id") in keep and len(u.get("item_ids", [])) == 1],
        "unit_stats": raw.get("unit_stats", []),
    }
    dst = Path(__file__).with_name("mini_18.json")
    dst.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(dst, {k: len(v) for k, v in out.items() if isinstance(v, list)}, f"{dst.stat().st_size/1e6:.2f}MB")


if __name__ == "__main__":
    main()
