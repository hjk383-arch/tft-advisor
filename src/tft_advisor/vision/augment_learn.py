"""보유 증강 칸 판별 보조 — 그림 동일성(visual key)과 "선택 순간 자동 학습".

보유 증강 줄의 칸은 전체 증강 목록(CDragon 317 + 대체 출처 49)과 비교해 식별한다(`Recognizer._read_augments_owned`).
전체 목록으로 식별이 안 되는 칸(템플릿 없음·아이콘 공유·점수 부족)도, **방금 증강 선택 화면에서 제시된 3개(리롤 포함)**
안에서만 고르면 대부분 확정할 수 있다. 확정한 칸 그림은 실화면 템플릿(`augments_screen/`)으로 저장해 다음 판부터
전체 목록 매칭에서도 인식되게 한다.

이 모듈은 **판별(무상태)** 만 한다. "언제 학습하나"(제시 목록 기억, 새 칸 탐지, 세션 영속·초기화)는
`app/session.SessionTracker`가 정한다(`_workspace/09_vision_augment_icons.md` §4).

임계값 근거(1080p 실캡처 17칸 + 사용자 크롭 3칸, 09 보고서 §4.3)
- 정답 점수: CDragon 경로 0.863~0.951, 글리프 정규화 경로(대체 출처) 0.806~0.861.
- 오답 1위: CDragon 경로 317개 중 0.644~0.674, 글리프 경로 49개 중 0.725~0.764(육각 특성 글리프끼리).
- 후보가 제시된 3개뿐이면 "모르는 증강이 엉뚱한 템플릿과 0.7대로 맞는" 위험(전체 목록 임계 0.80의 이유)이 사라진다:
  정답이 반드시 후보 안에 있고 후보 모두 템플릿이 있으면 LEARN_MIN 0.70 + 후보 사이 차이로 가른다.
  후보 중 템플릿이 없는 것이 있으면 전체 목록 임계(0.80)를 그대로 쓴다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..static_data import StaticData
from .icons import AugmentIconMatcher, augment_cell_template

log = logging.getLogger(__name__)

LEARN_MIN = 0.70            # 후보 전부 템플릿이 있을 때 최소 점수(정답 최저 0.806 > 0.70 > 전체 목록 오답 1위 대부분)
LEARN_FULL_MIN = 0.80       # 후보 중 템플릿이 없는 것이 있을 때 = 전체 목록 임계(recognizer.AUGMENT_MATCH_MIN)
LEARN_MARGIN = 0.05         # CDragon/실화면 경로 1·2위 차(초월 0.951 vs 불완전한 초월 0.878 = 0.073)
LEARN_GLYPH_MARGIN = 0.10   # 글리프 경로 1·2위 차(내면의 야수 0.855 vs 종결자 협곡야수 0.725 = 0.130)
ELIMINATION_MAX = 0.60      # 소거법: 템플릿 있는 후보가 모두 이 미만이면 템플릿 없는 유일한 후보로 확정
ELIMINATION_CONF = 0.60     # 소거법으로 정한 칸의 신뢰도


def _icon_key(icon: str) -> str:
    """아이콘 경로 → 비교 키(확장자·대소문자 무시). OP.GG 전체 URL은 같은 파일명의 CDragon hexcore 경로로 본다."""
    p = (icon or "").lower()
    if p.startswith(("http://", "https://")):
        p = "assets/maps/tft/icons/augments/hexcore/" + p.rsplit("/", 1)[-1]
    return p.rsplit(".", 1)[0] if "." in p.rsplit("/", 1)[-1] else p


def load_alt_manifest(alt_dir: str | Path | None) -> dict[str, dict]:
    """`augments_alt/sources.json`의 entries(없으면 {})."""
    if alt_dir is None:
        return {}
    p = Path(alt_dir) / "sources.json"
    if not p.is_file():
        return {}
    try:
        return dict(json.loads(p.read_text(encoding="utf-8")).get("entries") or {})
    except (OSError, ValueError) as e:
        log.warning("대체 아이콘 목록을 읽지 못했습니다(%s): %s", p, e)
        return {}


def augment_visual_keys(static: StaticData, alt_manifest: dict[str, dict] | None = None) -> dict[str, str]:
    """apiName → 그림 키. **같은 키 = 화면에서 구별할 수 없는 증강**.

    - CDragon 아이콘이 있는 증강: 아이콘 경로.
    - 대체 출처 아이콘을 받은 증강(`augments_alt/sources.json`): 같은 그림 묶음(`group`).
    - 둘 다 없는 `missing-*` 증강: 자기 자신(그림을 모르므로 다른 증강과 묶지 않는다 — 템플릿이 없어 전체 목록에서는 어차피
      인식되지 않고, 선택 순간 학습으로만 채워진다).
    """
    alt = alt_manifest or {}
    keys: dict[str, str] = {}
    for rec in static.tables.get("augments", []):
        api = rec["apiName"]
        icon = rec.get("icon") or ""
        if api in alt:
            keys[api] = "alt:" + str(alt[api].get("group") or api)
        elif not icon or "/missing" in icon.lower():
            keys[api] = "api:" + api
        else:
            keys[api] = _icon_key(icon)
    return keys


@dataclass
class OwnedRow:
    """한 프레임의 보유 증강 줄(왼쪽부터). `ids[i]`는 전체 목록 매칭으로 확정한 ID(모르면 None)."""

    cells: list[np.ndarray] = field(default_factory=list)
    ids: list[str | None] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cells)


@dataclass(frozen=True)
class LearnDecision:
    api: str
    score: float
    margin: float
    reason: str             # "match"(후보 중 최고점) | "elimination"(템플릿 없는 유일한 후보)
    via_glyph: bool = False


@dataclass
class _Option:
    key: str
    ids: list[str]          # 이 그림에 해당하는 후보 ID(제시 순서)
    names: set[str]
    score: float | None = None
    via_glyph: bool = False


class AugmentLearner:
    """제시된 후보 안에서 새 칸을 판별하고(`decide`), 확정되면 칸 그림을 템플릿으로 저장한다(`commit`)."""

    def __init__(self, static: StaticData, matcher: AugmentIconMatcher, keys: dict[str, str],
                 save_dir: str | Path | None = None) -> None:
        self.static = static
        self.matcher = matcher
        self.keys = keys
        self.save_dir = Path(save_dir) if save_dir is not None else None

    def _name(self, api: str) -> str:
        rec = self.static.get("augments", api)
        return (rec or {}).get("name_ko") or api

    def key(self, api: str) -> str:
        return self.keys.get(api, "api:" + api)

    def same_picture(self, a: str, b: str) -> bool:
        return self.key(a) == self.key(b)

    def options(self, candidates: Iterable[str]) -> list[_Option]:
        """후보 ID들 → 그림별 선택지(같은 그림의 후보는 하나로 묶는다). 정적 데이터에 없는 ID는 뺀다."""
        opts: dict[str, _Option] = {}
        for api in candidates:
            if self.static.get("augments", api) is None:
                continue
            k = self.key(api)
            o = opts.setdefault(k, _Option(key=k, ids=[], names=set()))
            if api not in o.ids:
                o.ids.append(api)
            o.names.add(self._name(api))
        return list(opts.values())

    def decide(self, cell: np.ndarray, candidates: Sequence[str]) -> LearnDecision | None:
        """새 칸 그림 + 제시됐던 후보들 → 확정 ID 또는 None(학습하지 않음). 규칙은 모듈 docstring."""
        opts = self.options(candidates)
        if not opts or cell.size == 0:
            return None
        by_key = {o.key: o for o in opts}
        for api, (sc, via) in self.matcher.scores(cell).items():
            o = by_key.get(self.key(api))
            if o is not None and (o.score is None or sc > o.score):
                o.score, o.via_glyph = sc, via
        templated = sorted((o for o in opts if o.score is not None), key=lambda o: o.score, reverse=True)
        untemplated = [o for o in opts if o.score is None]
        if templated:
            best = templated[0]
            second = templated[1].score if len(templated) > 1 else None
            margin = best.score - second if second is not None else 1.0
            need_min = LEARN_FULL_MIN if untemplated else LEARN_MIN
            need_margin = LEARN_GLYPH_MARGIN if best.via_glyph else LEARN_MARGIN
            if best.score >= need_min and margin >= need_margin:
                if len(best.names) != 1:
                    log.info("증강 학습 보류: 같은 그림의 서로 다른 증강이 함께 제시됨 %s", sorted(best.names))
                    return None
                return LearnDecision(best.ids[0], round(best.score, 3), round(margin, 3), "match", best.via_glyph)
        if len(untemplated) == 1 and len(untemplated[0].names) == 1 and \
                all(o.score < ELIMINATION_MAX for o in templated):
            o = untemplated[0]
            top = templated[0].score if templated else 0.0
            return LearnDecision(o.ids[0], ELIMINATION_CONF, round(ELIMINATION_MAX - top, 3), "elimination")
        return None

    def commit(self, cell: np.ndarray, api: str) -> Path | None:
        """확정한 칸 → 실화면 템플릿. 이 판에서 바로 쓰도록 매처에 추가하고, `save_dir`가 있으면 `{api}.png`로 저장한다
        (이미 있으면 덮어쓰지 않는다 — 처음 확정한 그림을 기준으로 둔다). 저장한 경로(또는 None)."""
        from .capture import save_image

        tpl = augment_cell_template(cell)
        self.matcher.add(api, tpl)
        if self.save_dir is None:
            return None
        dest = self.save_dir / f"{api}.png"
        if dest.exists():
            return dest
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("증강 템플릿 폴더를 만들지 못했습니다(%s): %s", self.save_dir, e)
            return None
        return dest if save_image(dest, tpl) else None
