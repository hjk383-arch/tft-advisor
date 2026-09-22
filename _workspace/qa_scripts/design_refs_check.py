"""Phase 2 재검증: 설계 문서(02_jev-strategist_design.md)가 참조하는 설정 키·계약 필드·FallbackReason ↔ 코드.

실행: .venv/bin/python _workspace/qa_scripts/design_refs_check.py
- `section.key`(pf./comp./shop./item_fit./item./augment./jev./shrinkage./advisor./vision./ui.) 참조가 config에 존재하는지
- `Model.field` 참조가 contracts 모델에 존재하는지
- §8.1 fallback 표의 값 집합 == contracts.FallbackReason
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tft_advisor import contracts as C  # noqa: E402
from tft_advisor.config import Settings, Weights  # noqa: E402

text = (ROOT / "_workspace/02_jev-strategist_design.md").read_text(encoding="utf-8")
W, S = Weights(), Settings()
ALIAS = {"pf": "prefilter"}
WSEC = set(Weights.model_fields)
SSEC = set(Settings.model_fields)
out: dict = {}

bad_cfg, ok_cfg = [], set()
for m in re.finditer(r"(?<![\w.\[])(?:settings\.|weights\.)?([a-z_]+)\.([a-z_]+)(?:\(|\b)", text):
    sec, key = ALIAS.get(m.group(1), m.group(1)), m.group(2)
    if sec in WSEC:
        model = getattr(W, sec)
    elif sec in SSEC:
        model = getattr(S, sec)
    else:
        continue
    ref = f"{sec}.{key}"
    if key in type(model).model_fields or callable(getattr(model, key, None)):
        ok_cfg.add(ref)
    else:
        bad_cfg.append(ref)
out["config_refs_ok"] = len(ok_cfg)
out["config_refs_missing"] = sorted(set(bad_cfg))

MODELS = {n: getattr(C, n) for n in dir(C) if isinstance(getattr(C, n), type) and issubclass(getattr(C, n), C.ContractModel)}
bad_f, ok_f = [], set()
for m in re.finditer(r"\b([A-Z][A-Za-z]+)\.([a-z_]+)\b", text):
    mn, f = m.groups()
    if mn not in MODELS:
        continue
    cls = MODELS[mn]
    if f in cls.model_fields or hasattr(cls, f):
        ok_f.add(f"{mn}.{f}")
    else:
        bad_f.append(f"{mn}.{f}")
out["contract_refs_ok"] = sorted(ok_f)
out["contract_refs_missing"] = sorted(set(bad_f))

# GameState 필드를 `GameState` 없이 부르는 §4.3/§5.4 용어 확인
for f in ("hp", "board", "bench", "active_traits", "items", "level", "stage", "shop", "augments_owned"):
    assert f in C.GameState.model_fields, f
for f in ("completed", "emblems", "components", "others"):
    assert f in C.ItemState.model_fields, f

sec81 = text[text.index("### 8.1"):text.index("### 8.2")]
design_fr = set(re.findall(r"^\| `([a-z_]+)` \|", sec81, re.M))
enum_fr = {e.value for e in C.FallbackReason}
sec10 = text[text.index("## 10. contracts"):text.index("## 10a.")]
row7 = next(line for line in sec10.splitlines() if "fallback_reason" in line and "StrEnum" in line)
design10_fr = set(re.findall(r'="([a-z_]+)"', row7))
out["fallback_design_8_1"] = sorted(design_fr)
out["fallback_design_10"] = sorted(design10_fr)
out["fallback_enum"] = sorted(enum_fr)
out["fallback_equal"] = design_fr == enum_fr == design10_fr
print(json.dumps(out, ensure_ascii=False, indent=1))
