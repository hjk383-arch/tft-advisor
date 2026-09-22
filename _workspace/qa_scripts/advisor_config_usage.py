"""QA: §10a 설정 키가 advisor 코드에서 실제로 읽히는지(이름으로) 전수 확인. 하드코딩 상수 후보도 출력."""
import re, pathlib, sys
sys.path.insert(0, "src")
from tft_advisor.config import Weights, Settings, AdvisorCfg
src = "\n".join(p.read_text() for p in pathlib.Path("src/tft_advisor/advisor").glob("*.py"))
missing = []
for sec, model in Weights.model_fields.items():
    sub = model.annotation
    for key in sub.model_fields:
        pat = rf"\.{key}\b"
        if not re.search(pat, src) and key != "stage_weights" and key != "commit_by_stage" and key != "editorial_tier_score":
            missing.append(f"weights.{sec}.{key}")
for key in AdvisorCfg.model_fields:
    if not re.search(rf"\.{key}\b", src):
        missing.append(f"settings.advisor.{key}")
for k in ("state_min_confidence", "max_target_comps"):
    if not re.search(rf"\.{k}\b", src):
        missing.append(k)
# indirect: for_stage / commit_for_stage / editorial_tier_score
for k in ("for_stage", "commit_for_stage", "editorial_tier_score", "adjust("):
    print(k, "used" if k in src else "NOT USED")
print("unused keys:", missing or "none")
