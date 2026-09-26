"""프레임 → GameState 조립. 공개 API: `Recognizer.recognize(image) -> GameState`, `recognize(image)`.

원칙
- 화면 픽셀만 쓴다. 입력 이미지는 BGR ndarray(실시간 캡처와 스크린샷 파일이 같은 경로).
- 불확실하면 None. 값이 있는 필드는 `GameState.confidence[field]`에 0~1 신뢰도를 기록한다.
- 신뢰도 = OCR 점수 × 파싱/매칭 점수 × 필드별 보정. ROI는 1080p·16:10 원본 캡처로 확인했다(07 보고). 표본이 두 판뿐이라 여전히 잠정.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from ..config import VisionCfg
from ..contracts import (
    ActiveTrait, AugmentRef, FieldSource, GameState, ItemRef, ItemState, ScreenMode, ShopSlot, ShopSlotKind,
)
from ..static_data import PROJECT_ROOT, StaticData, load_static, preference_key
from . import parse
from .augment_learn import AugmentLearner, OwnedRow, augment_visual_keys, load_alt_manifest
from .board import BoardRead, BoardReader, find_ally_bars
from .bench_memory import BenchMemory
from .unit_db import arena_signature
from .unit_track import UnitTracker
from .icons import AugmentIconMatcher, IconMatcher, find_icon_row, slot_is_empty
from .item_ids import ItemCatalog
from .matching import NameMatcher
from .ocr import OcrEngine, TextBox, create_ocr
from .regions import (
    FrameMapper, Profile, Rect, detect_content_box, profile_for_frame, screen_candidates, stage_bar_score,
)
from .units import TraitPanel, UnitNamer
from .screen_mode import (
    ModeSignals, classify, count_enemy_bars, frame_is_dark, hud_panel_pixels, parse_board_count,
)

log = logging.getLogger(__name__)
T = TypeVar("T")
# 부분 인식 묶음(QA04-V7). stage와 화면 상태 신호는 항상 읽는다(한 줄 인식만, 싸다).
# app 루프는 `change.ChangeDetector`가 알려 준 묶음만 넘기고, 결과를 직전 GameState에 합친다(FIELD_GROUP 참고).
GROUPS: tuple[str, ...] = ("hud", "shop", "items", "augment", "players", "traits", "owned", "board")
# 기본값에서 "traits"(특성 패널)는 뺀다: 검출+인식이 프레임 시간의 약 35%인데, active_traits는 신뢰도가 TRAITS_FACTOR로
# 임계 미만이라 advisor가 쓰지 않는다. 필요하면 groups=GROUPS 로 켠다(평가·디버그, app이 몇 초에 한 번).
DEFAULT_GROUPS: tuple[str, ...] = tuple(g for g in GROUPS if g != "traits")
VISION_ONLY_GROUPS: frozenset[str] = frozenset({"board"})
"""`GameState` 필드를 만들지 **않는** 묶음. 결과는 `Recognizer.last_board_read`로 나가고 app이
`app.unit_merge`에서 상점 구매 장부와 합쳐 `board`/`bench`를 만든다 → app의 `GROUP_READ_MODES`(필드 병합 표)에는 없다."""
FIELD_GROUP: dict[str, str] = {
    "stage": "stage", "screen_mode": "stage",
    "level": "hud", "xp": "hud", "gold": "hud", "shop_odds": "hud", "streak": "hud",
    "shop": "shop", "items": "items", "augment_offer": "augment", "hp": "players", "active_traits": "traits",
}
# "owned"(보유 증강 줄 → augments_owned)는 FIELD_GROUP에 넣지 않는다: app 병합에서 augments_owned는 "새 값이 있으면 쓰고
# 없으면 유지"(수동 입력·추적값을 vision의 '못 읽음'으로 지우지 않는다, app/session._CARRY_FIELDS).

_ALL = frozenset(ScreenMode)
READ_MODES: dict[str, frozenset[ScreenMode]] = {
    # 묶음 → 그 묶음을 실제로 읽는 화면. `recognize()`의 분기는 이 표만 본다(단일 출처).
    # app/session.GROUP_READ_MODES는 이것과 같아야 한다(tests/app/test_session.py가 같음을 고정).
    "stage": _ALL,
    "players": _ALL,
    # 전투 중에도 하단 HUD·상점·특성 패널은 그대로 보이고 살 수 있다(1080p 준비/전투 쌍으로 확인).
    "hud": frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT}),
    "shop": frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT}),
    "traits": frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT}),
    # 왼쪽 아이템 벤치는 캐러셀·모루/특수 선택·증강 선택 화면에서도 보인다.
    "items": frozenset({ScreenMode.PLANNING, ScreenMode.COMBAT, ScreenMode.AUGMENT_SELECT,
                        ScreenMode.ITEM_SELECT, ScreenMode.CAROUSEL}),
    "augment": frozenset({ScreenMode.AUGMENT_SELECT}),
    # 보유 증강 줄은 보드에 붙어 있다: 준비 단계(내 보드, 카메라 고정)에서만 읽는다. 원정 전투에서는 상대 줄도 보인다.
    "owned": frozenset({ScreenMode.PLANNING}),
    # 보드·벤치 유닛(자리·성급·장착 아이템). 카메라가 고정이고 유닛이 칸 위에 서 있는 화면에서만 읽는다:
    # 준비 단계 + 모루/전리품 선택(보드가 그대로 보인다). 전투 중에는 유닛이 칸을 떠나 자리가 뜻이 없다.
    "board": frozenset({ScreenMode.PLANNING, ScreenMode.ITEM_SELECT}),
}
AUGMENT_MATCH_MIN = 0.80      # 보유 증강 글리프 매칭 최소 점수(실측 정답 0.89~0.96, 오답 1위 <= 0.66)
AUGMENT_MATCH_MARGIN = 0.05   # 1위와 2위(다른 아이콘) 차 하한(초월 0.951 vs 불완전한 초월 0.878 = 0.073)
# 1위가 대체 출처 아이콘(글리프 정규화 경로)이면 더 큰 차를 요구한다: 정규화가 비슷한 모양(육각 특성 글리프)끼리의 점수도
# 올리기 때문(내면의 야수 정답 0.855 vs 종결자 협곡야수 0.725 = 0.130, 09 보고서 §3).
AUGMENT_GLYPH_MARGIN = 0.10
_HUD_WORDS = ("경험치", "새로고침", "구매", "buy", "refresh")
_TITLE_WORDS = ("선택", "choose")

# 필드별 보정 계수 — 검증되지 않은 해석 규칙이 들어간 필드는 낮춘다.
# 연승/연패 부호를 아이콘 색으로 판단: **주황·빨강 불꽃 = 연승, 파란 물방울 = 연패**. 2026-09-22 1080p 원본으로 검증:
# 파란 물방울 1·3·4·5(2-2 → 3-1, 내 HP 95 → 84 → 76 → 71로 계속 감소 = 연패), 빨강 불꽃 9(5-1 → 5-5 HP 28 유지 = 연승).
# 그래서 예전 0.5(advisor 입력에서 제외)를 요청서 계획대로 0.8로 올린다. QA04-V4: 임계(0.6)에서 0.05 이상 떨어져 있어야 한다.
# streak=0은 부호 무관이라 OCR 점수 그대로.
STREAK_SIGN_FACTOR = 0.8
HP_FACTOR = 0.7              # 내 HP = 플레이어 목록에서 가장 큰 글자 숫자(규칙 추정)
TRAITS_FACTOR = 0.55         # 특성 패널 OCR: 인원 1인 비활성 행을 자주 놓침(fixture), 스크롤/접힘 미확인 → 기본 임계 0.6 미만
LEVEL_FROM_XP_FACTOR = 0.8   # 레벨 숫자가 안 보여 XP 필요량으로 추정
HP_BOX_MAX_ASPECT = 1.8      # HP 숫자 박스 가로/세로 상한(관측 1.2~1.3, 이름 박스 1.7~3.5 → 일부 짧은 이름만 추가 인식)
FAST_LINE_MIN_SCORE = 0.85   # 한 줄 인식(rec만) 결과를 그대로 쓸 최소 점수. 미만이거나 파서가 거부하면 검출+인식으로 재시도
_ITEM_GROUP = {"component": "components", "completed": "completed", "emblem": "emblems"}


@dataclass
class _Out:
    """필드 값·신뢰도 누적."""

    values: dict[str, object] = field(default_factory=dict)
    conf: dict[str, float] = field(default_factory=dict)

    def put(self, name: str, value: object, conf: float) -> None:
        if value is None:
            return
        self.values[name] = value
        self.conf[name] = round(max(0.0, min(1.0, conf)), 3)


def _join(boxes: list[TextBox]) -> tuple[str, float]:
    """박스들을 왼→오 순으로 이어 붙인 문자열과 최소 점수."""
    if not boxes:
        return "", 0.0
    boxes = sorted(boxes, key=lambda b: b.box[0])
    return " ".join(b.text for b in boxes), min(b.score for b in boxes)


class Recognizer:
    """한 프로파일·정적 데이터·OCR 엔진을 묶은 인식기(스레드 하나에서 재사용)."""

    def __init__(
        self,
        static: StaticData | None = None,
        cfg: VisionCfg | None = None,
        ocr: OcrEngine | None = None,
        profile: Profile | None = None,
        item_template_dir: str | Path | Collection[str | Path] | None = None,
        augment_template_dir: str | Path | None = None,
        unit_template_dir: str | Path | None = None,
    ) -> None:
        self.static = static or load_static()
        self.cfg = cfg or VisionCfg()
        # profile을 직접 주면 프레임 크기와 무관하게 그것만 쓴다(테스트·디버그). 아니면 프레임마다 비율로 고른다.
        self.pinned_profile = profile
        self.profile_setting = "auto" if profile is not None else self.cfg.aspect_setting()
        self.ocr = ocr if ocr is not None else create_ocr("korean", backend=self.cfg.ocr_backend)
        margins = {"min_margin": self.cfg.name_fuzzy_min_margin, "relaxed_margin": self.cfg.name_fuzzy_relaxed_margin}
        self.shop_matcher = NameMatcher(self.static, ("champions", "shop_specials"), self.cfg.shop_fuzzy_min, **margins)
        self.augment_matcher = NameMatcher(self.static, ("augments",), self.cfg.shop_fuzzy_min, **margins)
        self.trait_matcher = NameMatcher(self.static, ("traits",), self.cfg.shop_fuzzy_min, **margins)
        self.items = ItemCatalog(self.static)
        # XP 표: 정적 데이터 meta.json `xp_to_next`(stats-researcher 관리), 없으면 parse.XP_TO_NEXT 대체값
        self.xp_table: dict[int, int] = self.static.xp_to_next() or dict(parse.XP_TO_NEXT)
        if item_template_dir is None:
            dirs = item_template_dirs(self.static.set_number)
        elif isinstance(item_template_dir, (str, Path)):
            dirs = [item_template_dir]
        else:
            dirs = list(item_template_dir)   # 여러 디렉터리 → ID별로 합친다
        self.item_matcher = IconMatcher.from_dirs(
            dirs, valid=lambda stem: self.static.get("items", stem) is not None, group=self.items.rep)
        # 대체 출처 아이콘(augments_alt, CDragon에 없는 세트 증강)은 기본 디렉터리를 쓸 때만 싣는다(테스트는 지정 디렉터리만).
        alt_dir = augment_alt_dir(self.static.set_number) if augment_template_dir is None else None
        self.augment_icons = AugmentIconMatcher.from_dirs(
            augment_template_dirs(self.static.set_number) if augment_template_dir is None else [augment_template_dir],
            valid=lambda stem: self.static.get("augments", stem) is not None,
            glyph_dirs=[alt_dir] if alt_dir is not None else [])
        self._augment_keys = augment_visual_keys(self.static, load_alt_manifest(alt_dir))
        self._icon_owners = _augment_icon_owners(self.static, self._augment_keys)
        # 선택 순간 자동 학습(app/session이 호출). 기본 디렉터리를 쓸 때만 실화면 템플릿을 저장한다.
        self.augment_learner = AugmentLearner(
            self.static, self.augment_icons, self._augment_keys,
            save_dir=augment_template_dirs(self.static.set_number)[1] if augment_template_dir is None else None)
        self.board_reader = BoardReader.from_recognizer(self.item_matcher, self.items)
        """보드·벤치 판독기. 아이템 벤치용 매처를 장착 아이콘 크기로 다시 정규화해 쓴다(디스크 재로딩 없음)."""
        self.unit_namer: UnitNamer | None = (
            UnitNamer.from_static(self.static, unit_template_dir, autolearn=self.cfg.unit_autolearn,
                                  # 25: 설정 키 제안(config.py는 app-integrator 담당) — 없으면 기본값
                                  pending_weight=float(getattr(self.cfg, "unit_pending_weight", 0.0) or 0.0),
                                  auto_approve_purchase=bool(getattr(self.cfg, "unit_purchase_autoapprove", False)),
                                  collect_until=int(getattr(self.cfg, "unit_collect_until", 3)))
            if self.cfg.unit_names else None)
        """보드·벤치 챔피언 이름(특성 패널 구속 + 모델 크롭 라이브러리). 설정 `[vision] unit_names=false`면 None."""
        self._last_shop_odds: list[int] | None = None
        """마지막으로 읽은 상점 확률(이번 프레임에 HUD를 안 읽었을 때 유닛 이름 풀이에 쓴다)."""
        self.bench_memory = BenchMemory()
        self.unit_tracker = UnitTracker()
        """판 안의 유닛 정체(구매 칸 · 옮기기 · 합성, `vision.unit_track`). 새 판이면 app이 `reset()`을 부른다."""
        """벤치 빈 칸 기준 그림 + 칸별 직전 유닛(`vision.bench_memory`). 새 판이면 app이 `reset()`을 부른다."""
        self._panel_cache: tuple[np.ndarray, tuple[list[ActiveTrait], float, bool]] | None = None
        self.last_board_read: BoardRead | None = None
        """직전 `recognize()`의 보드 판독(`board` 묶음을 읽었을 때만). app은 `app.unit_merge.board_obs_from`으로 받는다."""
        self.last_owned_row: OwnedRow | None = None
        """직전 `recognize()`가 읽은 보유 증강 줄(칸 그림 + 칸별 ID). 읽지 않았으면 None. app 학습 경로가 쓴다."""
        self._screen_cache: dict[tuple, tuple[int, int, int, int]] = {}

    # ------------------------------------------------------------------ 프로파일
    def profile_for(self, width: int, height: int) -> Profile:
        """이 프레임 크기에 쓸 ROI 프로파일. 설정 `[vision] profile/aspect/resolution` → `profile_for_frame`."""
        if self.pinned_profile is not None:
            return self.pinned_profile
        if self.profile_setting == "auto" and (width <= 0 or height <= 0):
            return profile_for_frame(16, 9)   # 크기를 모를 때의 기본(16:9)
        return profile_for_frame(width, height, self.profile_setting)

    @property
    def profile(self) -> Profile:
        """설정으로 고정된(또는 기본 16:9) 프로파일. **프레임마다 달라질 수 있으므로** 인식 경로는 `profile_for()`를
        쓴다. 이 속성은 `change.ChangeDetector`·`templates.debug-rois`처럼 프레임 크기를 모르는 호출자를 위한 것이다."""
        return self.profile_for(0, 0)

    def content_for(self, image: np.ndarray, content: tuple[int, int, int, int] | None,
                    ) -> tuple[int, int, int, int] | None:
        """이 프레임의 게임 화면 영역. 인자 > 설정 `content_box` > (content_box_auto면) 자동 탐지:
        ① 여러 모니터를 이어 붙인 캡처(Win+PrtSc)면 게임 화면이 있는 모니터(`pick_screen`), ② 레터박스 검은 띠."""
        if content is not None:
            return content
        box = self.cfg.content_px(image.shape[1], image.shape[0])
        if box is not None:
            return box
        if not self.cfg.content_box_auto:
            return None
        found = screen_candidates(image)
        if len(found) <= 1:
            return detect_content_box(image)
        # 세로 모니터·순수 검정 조각이 만든 좁은 조각은 게임 화면일 수 없다(프로파일 유도 불가) → 뺀다(QA08).
        # 쓸 수 있는 후보가 없으면(한 화면이 검정 조각으로 쪼개진 경우) 한 화면으로 본다.
        cands = [c for c in found if self._profile_ok(c[2], c[3])]
        if not cands:
            return detect_content_box(image)
        left, top, w, h = cands[0] if len(cands) == 1 else self.pick_screen(image, cands)
        inner = detect_content_box(image[top:top + h, left:left + w])
        if inner is not None:
            return left + inner[0], top + inner[1], inner[2], inner[3]
        return left, top, w, h

    def _profile_ok(self, width: int, height: int) -> bool:
        try:
            self.profile_for(width, height)
        except ValueError:
            return False
        return True

    def pick_screen(self, image: np.ndarray, cands: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
        """모니터 후보 중 게임 화면: 스테이지 글자("2-5")가 읽히는 후보 > 기억한 후보 > 스테이지 막대가 어두운 후보.

        스테이지로 고른 결과는 (프레임 크기, 후보) 단위로 기억한다 — 같은 배치의 다음 캡처(게임 종료 화면처럼 스테이지가
        없는 화면 포함)는 같은 모니터를 쓴다.
        """
        key = (image.shape[1], image.shape[0], tuple(cands))
        # 세로 모니터(1080x1920)나 좁은 조각은 16:9 유도가 불가능하다(derive_profile ValueError) → 후보에서 뺀다(QA08).
        usable = []
        for c in cands:
            try:
                usable.append((c, self.profile_for(c[2], c[3])))
            except ValueError:
                log.debug("게임 화면 후보 제외(비율 %.2f): %s", c[2] / max(c[3], 1), c)
        if not usable:
            return max(cands, key=lambda c: c[2] * c[3])
        cands = [c for c, _ in usable]
        crops = []
        for (left, top, w, h), prof in usable:
            m = FrameMapper.for_image(image, (left, top, w, h))
            crops.append(m.crop(image, prof.stage))
        for c, tb in zip(cands, self._lines(crops)):
            if tb is not None and parse.parse_stage(tb.text):
                self._screen_cache[key] = c
                return c
        if key in self._screen_cache:
            return self._screen_cache[key]
        scored = [(stage_bar_score(image, c, prof), c[2] * c[3], c) for c, prof in usable]
        return max(scored)[2]

    def screen_score(self, image: np.ndarray) -> float:
        """이 프레임(모니터 1대)이 TFT 게임 화면일 가능성 0~1. 스테이지 글자가 읽히면 1, 아니면 스테이지 막대 픽셀 점수 x 0.5.
        실시간 캡처의 모니터 자동 선택(`capture.MssSource(monitor="auto")`)이 쓴다."""
        h, w = image.shape[:2]
        P = self.profile_for(w, h)
        tb = self._lines([FrameMapper.for_image(image).crop(image, P.stage)])[0]
        if tb is not None and parse.parse_stage(tb.text):
            return 1.0
        return 0.5 * stage_bar_score(image, (0, 0, w, h), P)

    # ------------------------------------------------------------------ 공개 API
    def recognize(
        self,
        image: np.ndarray,
        *,
        content: tuple[int, int, int, int] | None = None,
        source_image: str | None = None,
        captured_at: datetime | None = None,
        groups: Collection[str] | None = None,
    ) -> GameState:
        """BGR 프레임 1장 → GameState. `content`는 프레임 안 게임 화면 영역(left, top, w, h px).
        None이면 설정 `[vision] content_box`(비율, 기본 = 프레임 전체)를 이 프레임 크기로 바꿔 쓴다.

        `groups`: 읽을 묶음(GROUPS의 부분집합, 기본 DEFAULT_GROUPS = traits 제외). 빠진 묶음의 필드는 None이다
        (=이번 프레임에서 읽지 않음).
        stage·screen_mode는 항상 읽는다. 필드 → 묶음은 FIELD_GROUP.
        """
        g = set(DEFAULT_GROUPS if groups is None else groups)
        self.last_owned_row = None
        self.last_board_read = None
        unknown_groups = g - set(GROUPS)
        if unknown_groups:
            raise ValueError(f"알 수 없는 묶음: {sorted(unknown_groups)} (가능: {GROUPS})")
        content = self.content_for(image, content)
        m = FrameMapper.for_image(image, content)
        out = _Out()
        # ROI 프로파일은 **게임 화면 영역의 비율**로 고른다(레터박스·창 테두리를 뺀 뒤). 설정으로 고정할 수도 있다.
        _, _, box_w, box_h = m.box
        P = self.profile_for(box_w, box_h)

        # 항상 읽는 칸(스테이지, HUD 버튼 2개, 증강 제목, 화면 상태 신호 5개)은 한 줄 인식 한 번(배치)으로 먼저 읽는다.
        signal_rois = (P.board_count, P.prep_banner, P.select_title, P.game_over_title, P.exit_button)
        pre = self._lines([m.crop(image, r) for r in
                           (P.stage, P.xp_button, P.refresh_button, P.augment_title, *signal_rois)])
        (stage, stage_conf), = self._read_parsed_many(image, m, [(P.stage, parse.parse_stage)], lines=pre[:1])
        out.put("stage", stage, stage_conf)

        # 화면 상태 신호
        hud_by_ocr = self._find_keyword(image, m, (P.xp_button, P.refresh_button), _HUD_WORDS, lines=pre[1:3])
        hud = hud_by_ocr or hud_panel_pixels(m.crop(image, P.xp_button))
        bc_line, banner_line, select_line, over_line, exit_line = pre[4:9]
        select_title = not hud_by_ocr and _has(select_line, _TITLE_WORDS)
        # 증강 화면에는 상점 HUD가 없다 → HUD 글자가 읽혔으면 제목은 한 줄 인식만(검출 생략).
        augment_title = self._find_keyword(image, m, (P.augment_title,), _TITLE_WORDS,
                                           detect=not hud_by_ocr and not select_title, lines=pre[3:4])
        offers = (self._read_augment_offer(image, m, P)
                  if ((augment_title or not hud) and not select_title and "augment" in g) else [])
        board_count = prep_banner = False
        enemy_bars = bench_bars = 0
        game_over_title = exit_button = False
        if hud:
            board_count = self._board_count(image, m, P, bc_line)
            prep_banner = _has(banner_line, ("준비",))
            if not (board_count or prep_banner):
                # 벤치 아군 체력바는 전투가 시작되면 사라진다 → 워터마크가 가려졌을 때 준비를 확정한다(vision 16 §5).
                bench_bars = len(find_ally_bars(image, m, P.bench_area))
                if bench_bars == 0:
                    enemy_bars = count_enemy_bars(m.crop(image, P.combat_area), m.box[3])
        elif stage is not None and not augment_title and not select_title:
            # 상점 HUD가 없는 인게임 화면: 원정 전투(상대 아레나)인지 캐러셀인지 적 체력바로 가른다.
            enemy_bars = count_enemy_bars(m.crop(image, P.combat_area), m.box[3])
        elif stage is None and not select_title:
            # 게임 종료: 스테이지 막대도 HUD도 없다. 제목은 두 줄("최종 순위" / "1위")이라 한 줄 인식이 아래 줄만 읽는다 → 검출.
            exit_button = _has(exit_line, ("나가기",))
            game_over_title = _has(over_line, ("순위",)) or any(
                "순위" in b.text for b in self._read(image, m, P.game_over_title))
        sig = ModeSignals(
            shop_hud=hud, shop_hud_by_ocr=hud_by_ocr, augment_title=augment_title,
            augment_names_matched=sum(1 for a in offers if a is not None), stage=stage,
            dark=frame_is_dark(image), board_count=board_count, prep_banner=prep_banner, enemy_bars=enemy_bars,
            bench_bars=bench_bars, select_title=select_title, game_over_title=game_over_title,
            exit_button=exit_button,
        )
        mode, mode_conf = classify(sig)
        if mode != ScreenMode.UNKNOWN:
            out.put("screen_mode", mode, mode_conf)

        def reads(group: str) -> bool:
            return group in g and mode in READ_MODES[group]

        if reads("players"):
            out_hp = self._read_hp(image, m, P)
            if out_hp:
                out.put("hp", *out_hp)
        if reads("augment") and offers and all(a is not None for a in offers):
            refs = [a for a, _ in offers]
            out.put("augment_offer", refs, min(c for _, c in offers))
        if reads("hud"):
            self._read_hud_numbers(image, m, P, out)
        if reads("shop"):
            self._read_shop(image, m, P, out)
        if reads("items"):
            self._read_items(image, m, P, out)
        panel = None
        if reads("traits"):
            panel = self._read_traits(image, m, P, out)
        if reads("owned"):
            self._read_augments_owned(image, m, P, stage, out)
        if reads("board"):
            read = self.board_reader.read(image, m, P)
            unit_ctx = None
            if self.unit_namer is not None and read.count:
                if panel is None and read.board:
                    panel = self.cached_trait_panel(image, m, P)
                tp = (TraitPanel({t.id: t.count for t in panel[0]}, complete=panel[2], confidence=panel[1])
                      if panel is not None and panel[0] else None)
                odds = out.values.get("shop_odds")
                if odds is not None:
                    self._last_shop_odds = list(odds)
                unit_ctx = self._unit_ctx(image, m, out, captured_at)
                read = self.unit_namer.name(image, m, read, tp, ctx=unit_ctx,
                                            shop_odds=odds if odds is not None else self._last_shop_odds)
            # 벤치 체력바가 사라진 프레임(준비 끝·전환): 벤치를 비우지 않고 칸 그림 + 직전 판독으로 채운다(30 보고)
            read = self.bench_memory.apply(image, m, P, read)
            if self.unit_namer is not None and read.count:
                # 판 안의 정체 이어 가기(구매 칸 · 옮기기 · 합성, 35 보고). 준비 단계에서만 갱신한다
                bd, nd = self.unit_namer.last_descs
                read = self.unit_tracker.update(
                    read, list(bd), list(nd),
                    captured_at.timestamp() if captured_at is not None else time.time(),
                    shop=self._shop_key(out), active=mode == ScreenMode.PLANNING,
                    arena=arena_signature(image, m.box))
                self._collect_unknown(read, unit_ctx, mode)
            self.last_board_read = read

        values = dict(out.values)
        return GameState(
            **values,
            confidence=dict(out.conf),
            field_source={k: FieldSource.VISION for k in values},
            set_number=self.static.set_number,
            captured_at=captured_at,
            source_image=source_image,
            frame_size=(image.shape[1], image.shape[0]),
        )

    def _collect_unknown(self, read: BoardRead, ctx: Any, mode: ScreenMode) -> None:
        """이름 미상 벤치 크롭을 검토 대기(`_pending/_unknown/`)로(사용자가 판 뒤에 이름을 준다). 준비 단계 · 체력바가 보이는
        프레임만(`read.bench`가 이름 판정의 크롭과 같은 순서일 때)."""
        namer = self.unit_namer
        col = getattr(namer, "collector", None) if namer is not None else None
        if col is None or ctx is None or mode != ScreenMode.PLANNING or read.bench_held or self.unit_tracker.frozen:
            return                                         # frozen = 내 맵이 아님(다른 플레이어 벤치일 수 있다)
        crops = list(getattr(namer, "last_bench_crops", []) or [])
        if len(crops) != len(read.bench):
            return
        try:
            col.observe_unknown(ctx, read, crops, owned=sorted(namer.hints), unplaced=read.unplaced)
        except Exception:                                  # 수집 실패가 인식을 멈추게 하지 않는다
            log.exception("이름 미상 크롭 수집 실패")

    @staticmethod
    def _shop_key(out: _Out) -> tuple | None:
        """상점 5칸: 챔피언 ID | None(빈 칸) | "*"(특수·식별 실패). 이번 프레임에 상점을 읽지 않았으면 None."""
        slots = out.values.get("shop")
        if slots is None:
            return None
        return tuple(s.id if s.kind == ShopSlotKind.CHAMPION else (None if s.kind == ShopSlotKind.EMPTY else "*")
                     for s in slots)

    def _unit_ctx(self, image: np.ndarray, m: FrameMapper, out: _Out, captured_at: datetime | None) -> Any:
        """유닛 사진 수집기(`vision.unit_db`)에 줄 같은 프레임 판독. 수집기가 없으면 None(계산하지 않는다)."""
        if self.unit_namer is None or self.unit_namer.collector is None:
            return None
        from .unit_db import FrameContext, arena_signature, frame_digest

        shop = None
        slots = out.values.get("shop")
        if slots is not None:
            shop = tuple(s.id if s.kind == ShopSlotKind.CHAMPION else (None if s.kind == ShopSlotKind.EMPTY else "*")
                         for s in slots)
        at = captured_at.timestamp() if captured_at is not None else time.time()
        from .bench_memory import tactician_cells

        try:        # 전략가가 벤치 위를 지나가는 칸은 사진을 모으지 않는다(30 보고)
            tact = frozenset(tactician_cells(image, m, self.profile_for(m.box[2], m.box[3])))
        except Exception:
            tact = frozenset()
        return FrameContext(at=at, stage=out.values.get("stage"), shop=shop, gold=out.values.get("gold"),
                            frame=frame_digest(image), arena=arena_signature(image, m.box), tactician=tact)

    # ------------------------------------------------------------------ OCR 헬퍼
    def _read(self, image: np.ndarray, m: FrameMapper, r: Rect) -> list[TextBox]:
        return self.ocr.read(m.crop(image, r))

    def _lines(self, crops: list[np.ndarray]) -> list[TextBox | None]:
        """한 줄 인식 여러 장(엔진이 배치를 지원하면 한 번에)."""
        fn = getattr(self.ocr, "read_lines", None)
        if fn is not None:
            return fn(crops)
        return [self.ocr.read_line(c) if c.size else None for c in crops]

    def _read_parsed(self, image: np.ndarray, m: FrameMapper, r: Rect, parse_fn: Callable[[str], T | None],
                     ) -> tuple[T | None, float]:
        """한 줄 ROI → (값, 점수). `_read_parsed_many`의 한 칸 버전."""
        return self._read_parsed_many(image, m, [(r, parse_fn)])[0]

    def _read_parsed_many(self, image: np.ndarray, m: FrameMapper,
                          items: list[tuple[Rect, Callable[[str], object]]],
                          lines: list[TextBox | None] | None = None) -> list[tuple[object, float]]:
        """한 줄 ROI들 → [(값, 점수)]. 빠른 한 줄 인식(rec만, 배치)을 먼저 쓰고, 점수가 FAST_LINE_MIN_SCORE 미만이거나
        파서가 거부한 칸만 검출+인식(칸당 약 70~160ms)으로 재시도한다(QA04-V7). 값이 없으면 (None, 0.0)."""
        crops = [m.crop(image, r) for r, _ in items]
        if lines is None:
            lines = self._lines(crops)
        out: list[tuple[object, float]] = []
        for (r, parse_fn), crop, tb in zip(items, crops, lines):
            if crop.size == 0:
                out.append((None, 0.0))
                continue
            if tb is not None and tb.score >= FAST_LINE_MIN_SCORE:
                v = parse_fn(tb.text)
                if v is not None:
                    out.append((v, tb.score))
                    continue
            text, score = _join(self.ocr.read(crop))
            v = parse_fn(text) if text else None
            out.append((v, score) if v is not None else (None, 0.0))
        return out

    def _read_boxes(self, image: np.ndarray, m: FrameMapper, r: Rect,
                    keep: Callable[[tuple[float, float, float, float]], bool]) -> list[TextBox]:
        """검출 후 keep인 박스만 인식(엔진이 read_boxes를 지원하지 않으면 read() 결과를 거른다)."""
        crop = m.crop(image, r)
        fn = getattr(self.ocr, "read_boxes", None)
        if fn is not None:
            return fn(crop, keep)
        return [b for b in self.ocr.read(crop) if keep(b.box)]

    def _find_keyword(self, image: np.ndarray, m: FrameMapper, rois: tuple[Rect, ...], words: tuple[str, ...],
                      detect: bool = True, lines: list[TextBox | None] | None = None) -> bool:
        """ROI들 중 하나에 키워드가 있는가. 한 줄 인식을 먼저(`lines`가 있으면 그것), 없으면(detect=True일 때) 검출+인식."""
        def has(t: str) -> bool:
            return any(w in t or w in t.lower() for w in words)

        crops = [m.crop(image, r) for r in rois]
        if lines is None:
            lines = self._lines(crops)
        if any(tb is not None and has(tb.text) for tb in lines):
            return True
        if detect:
            for c in crops:
                if has("".join(b.text for b in self.ocr.read(c))):
                    return True
        return False

    # ------------------------------------------------------------------ 화면 상태 신호
    def _board_count(self, image: np.ndarray, m: FrameMapper, P: Profile, line: TextBox | None) -> bool:
        """보드 인원 워터마크("3/3"). 한 줄 인식이 실패하면(유닛·효과에 일부 가림) 검출+인식으로 한 번 더."""
        if line is not None and line.score >= 0.5 and parse_board_count(line.text):
            return True
        return any(b.score >= 0.5 and parse_board_count(b.text) for b in self._read(image, m, P.board_count))

    # ------------------------------------------------------------------ 필드별
    def _read_augments_owned(self, image: np.ndarray, m: FrameMapper, P: Profile, stage: str | None,
                             out: _Out) -> None:
        """보드 왼쪽 위 보유 증강 줄 → augments_owned. 한 칸이라도 못 알아보면 None(부분 목록은 "이것뿐"으로 오해된다).

        줄이 없을 때: 스테이지 1(첫 증강 전)이면 [](확실히 없음), 그 밖에는 None(유닛·효과에 가렸을 수 있다).
        """
        crop = m.crop(image, P.augments_owned)
        cells = find_icon_row(crop, m.box[3])
        if not cells:
            if stage is not None and stage.startswith("1-"):
                out.put("augments_owned", [], 0.9)
                self.last_owned_row = OwnedRow()   # 확실히 0칸(첫 증강 전) — 학습 경로의 "선택 전 칸 수"
            return
        row = OwnedRow()
        refs: list[AugmentRef] = []
        for x1, y1, x2, y2 in cells:
            cell = crop[y1:y2, x1:x2].copy()
            match = self.augment_icons.match(cell)
            rec = None
            if match is not None and match.score >= AUGMENT_MATCH_MIN and match.margin >= (
                    AUGMENT_GLYPH_MARGIN if match.via_glyph else AUGMENT_MATCH_MARGIN):
                rec = self._resolve_augment_icon(match.api_name)
            row.cells.append(cell)
            row.ids.append(rec["apiName"] if rec is not None else None)
            row.scores.append(round(match.score, 3) if match is not None else 0.0)
            if rec is not None:
                refs.append(AugmentRef(id=rec["apiName"], name_ko=rec.get("name_ko"), rarity=rec.get("tier"),
                                       confidence=round(match.score, 3)))
        self.last_owned_row = row
        if len(refs) == len(cells):   # 한 칸이라도 모르면 필드 전체 None(칸별 결과는 last_owned_row로 app 학습에 넘긴다)
            out.put("augments_owned", refs, min(r.confidence or 0.0 for r in refs))

    def _resolve_augment_icon(self, api: str) -> dict | None:
        """템플릿 ID → 증강 레코드. 같은 그림(아이콘 경로 또는 대체 출처 그림 묶음, `augment_visual_keys`)을 쓰는
        **다른 이름의** 세트 증강이 있으면 모호 → None."""
        rec = self.static.get("augments", api)
        if rec is None:
            return None
        keys = getattr(self, "_augment_keys", None)
        if keys is None:   # __new__로 만든 부분 객체(테스트) 호환
            keys = self._augment_keys = augment_visual_keys(self.static)
        owners = self._icon_owners.get(keys.get(api, "api:" + api), [rec])
        native = [r for r in owners if r.get("set_native")] or owners
        if len({r.get("name_ko") or r["apiName"] for r in native}) > 1:
            log.debug("증강 아이콘 모호: %s → %s", api, [r["apiName"] for r in native])
            return None
        return min(native, key=preference_key)

    def _read_hud_numbers(self, image: np.ndarray, m: FrameMapper, P: Profile, out: _Out) -> None:
        (level, level_conf), (xp, xp_conf), (gold, gold_conf), (odds, odds_conf), streak_n = self._read_parsed_many(
            image, m, [(P.level, parse.parse_level), (P.xp, lambda t: parse.parse_xp(t, self.xp_table)),
                       (P.gold, lambda t: parse.parse_int(t, 0, 999)), (P.shop_odds, parse.parse_odds),
                       (P.streak_value, lambda t: parse.parse_int(t, 0, 30))])
        if level and xp:
            if self.xp_table.get(level) == xp[1]:
                level_conf = xp_conf = max(level_conf, xp_conf)   # 서로 확인됨
            else:
                level_conf *= 0.5
                xp_conf *= 0.5
        elif xp and not level:
            level = parse.level_from_xp(xp, self.xp_table)
            level_conf = xp_conf * LEVEL_FROM_XP_FACTOR * (1.0 if level in parse.XP_TO_NEXT_OBSERVED else 0.75)
        out.put("level", level, level_conf)
        out.put("xp", xp, xp_conf)

        out.put("gold", gold, gold_conf)

        s = odds_conf
        observed = self.static.observed_shop_odds().get(level) if (odds and level) else None
        if observed is not None and observed != odds:
            s *= 0.5   # 관측 확률표(meta.json)와 다르면 레벨 또는 확률 판독을 의심
        out.put("shop_odds", odds, s)

        streak = self._read_streak(image, m, P, *streak_n)
        if streak:
            out.put("streak", *streak)

    def _read_streak(self, image: np.ndarray, m: FrameMapper, P: Profile, n: int | None, s: float) -> tuple[int, float] | None:
        """n, s = 연승 숫자 칸 판독값과 점수. 부호는 아이콘 색으로."""
        import cv2

        if n is None:
            return None
        if n == 0:
            return 0, s
        icon = m.crop(image, P.streak_icon)
        hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
        vivid = (hsv[..., 1] > 120) & (hsv[..., 2] > 120)
        disc = _dark_disc(hsv)
        if disc is not None:
            # 불꽃은 **어두운 원** 안에 있다. 원 밖은 반투명 상자 너머 맵 바닥이다 — 라이브 2(모래 맵)에서 주황 모래가
            # 파란 불꽃(연패)보다 많이 잡혀 연패 2를 연승 +2로 읽었다(23 보고).
            vivid &= disc
        hues = hsv[..., 0][vivid]
        if hues.size < 5:
            return None
        warm = float(((hues < 30) | (hues > 160)).mean())
        cool = float(((hues >= 85) & (hues <= 130)).mean())
        if warm >= 0.6:
            return n, s * STREAK_SIGN_FACTOR
        if cool >= 0.6:
            return -n, s * STREAK_SIGN_FACTOR
        return None

    def _read_shop(self, image: np.ndarray, m: FrameMapper, P: Profile, out: _Out) -> None:
        slots: list[ShopSlot] = []
        confs: list[float] = []
        names = self._lines([m.crop(image, r) for r in P.shop_names])   # 5칸 이름을 한 번에(배치)
        for i in range(len(P.shop_cards)):
            slot, c = self._read_shop_slot(image, m, P, i, names[i])
            slots.append(slot)
            confs.append(c)
        known = [c for s, c in zip(slots, confs) if s.kind != ShopSlotKind.UNKNOWN]
        if not known:
            return
        # QA04-V3: 필드 신뢰도 = "상점 줄을 읽었는가"(식별된 칸 중 최고 신뢰도). 칸별 차단은 advisor가 ShopSlot.confidence로
        # 한다(scoring.py 칸 필터, 인덱스 유지). min(칸)으로 두면 약한 칸 하나가 맞게 읽은 나머지 칸까지 버리게 된다.
        # UNKNOWN 칸은 계약상 명시적 "모름"이라 필드 신뢰도를 깎지 않는다.
        out.put("shop", slots, max(known))

    def _read_shop_slot(self, image: np.ndarray, m: FrameMapper, P: Profile, i: int, tb: TextBox | None,
                        ) -> tuple[ShopSlot, float]:
        if tb is None or not tb.text.strip():
            card = m.crop(image, P.shop_cards[i])
            if _card_is_empty(card):
                return ShopSlot(kind=ShopSlotKind.EMPTY), 0.8
            return ShopSlot(kind=ShopSlotKind.UNKNOWN), 0.0
        match = self.shop_matcher.match(tb.text)
        if match is None:
            return ShopSlot(kind=ShopSlotKind.UNKNOWN), 0.0
        rec = match.record
        c = min(tb.score, match.score / 100.0)
        if match.kind == "champions":
            return ShopSlot(kind=ShopSlotKind.CHAMPION, id=rec["apiName"], name_ko=rec.get("name_ko"),
                            cost=rec.get("cost"), confidence=round(c, 3)), c
        cost, _ = self._read_parsed(image, m, P.shop_costs[i], lambda t: parse.parse_int(t, 0, 99))
        return ShopSlot(kind=ShopSlotKind.SPECIAL, id=rec["apiName"], name_ko=rec.get("name_ko"),
                        cost=cost, confidence=round(c, 3)), c

    def _read_augment_offer(self, image: np.ndarray, m: FrameMapper, P: Profile) -> list[tuple[AugmentRef, float] | None]:
        res: list[tuple[AugmentRef, float] | None] = []
        reads = self._read_parsed_many(image, m, [(r, self.augment_matcher.match) for r in P.augment_names])
        for match, score in reads:
            if match is None:
                res.append(None)
                continue
            rec = match.record
            c = min(score, match.score / 100.0)
            res.append((AugmentRef(id=rec["apiName"], name_ko=rec.get("name_ko"), rarity=rec.get("tier"),
                                   confidence=round(c, 3)), c))
        return res

    def _read_items(self, image: np.ndarray, m: FrameMapper, P: Profile, out: _Out) -> None:
        """아이템 벤치 10칸. 빈 칸이 아닌데 매칭 실패한 칸이 하나라도 있으면 items=None(부분 목록은 오해를 낳는다)."""
        if len(self.item_matcher) == 0:
            return
        state = ItemState()
        confs: list[float] = []
        for r in P.item_slots:
            crop = m.crop(image, r)
            if slot_is_empty(crop):
                continue
            match = self.item_matcher.match(crop)
            if match is None or match.score < self.cfg.icon_match_min or match.margin < self.cfg.item_match_margin:
                return
            api = self.items.rep(match.api_name)   # QA04-V6: 사용 횟수 변형 등은 묶음 대표 ID로
            rec = self.static.get("items", api) or {}
            cat = rec.get("category")
            ref = ItemRef(id=api, name_ko=self.items.display_name(api), category=cat, confidence=round(match.score, 3))
            getattr(state, _ITEM_GROUP.get(cat, "others")).append(ref)
            confs.append(match.score)
        out.put("items", state, min(confs) if confs else 0.9)

    def _read_traits(self, image: np.ndarray, m: FrameMapper, P: Profile, out: _Out,
                     ) -> tuple[list[ActiveTrait], float, bool]:
        """왼쪽 특성 패널 → `active_traits`(묶음 "traits"). 판독 결과는 보드 이름 식별에도 다시 쓴다."""
        panel = self.cached_trait_panel(image, m, P)
        if panel[0]:
            out.put("active_traits", panel[0], panel[1] * TRAITS_FACTOR)
        return panel

    def cached_trait_panel(self, image: np.ndarray, m: FrameMapper, P: Profile,
                           ) -> tuple[list[ActiveTrait], float, bool]:
        """`read_trait_panel` + 캐시: 패널의 **글자 영역**이 직전과 같으면 OCR을 다시 하지 않는다.
        특성 패널은 보드가 바뀔 때만 바뀐다 → 실시간 루프에서 보드 묶음을 다시 읽어도 대부분 OCR 0회.

        비교(`panel_unchanged`): 인원 숫자·이름·구간 글자가 있는 가로 20~80% 열을 **원래 해상도** 회색조로 두고,
        밝기 차 40 초과 픽셀이 `PANEL_CACHE_TOL`개 이하일 때만 같다고 본다. 인원 글자 하나 바뀜 = 약 90px,
        작은 구간 글자 하나 = 약 18px, 같은 패널의 다른 프레임 = 0px(원본 캡처 실측, QA 19 WARN 수정).
        """
        crop = m.crop(image, P.traits_panel)
        if crop.size == 0:
            return [], 0.0, False
        key = panel_text_region(crop)
        if self._panel_cache is not None and panel_unchanged(self._panel_cache[0], key):
            return self._panel_cache[1]
        result = self.read_trait_panel(image, m, P)
        self._panel_cache = (key, result)
        return result

    def read_trait_panel(self, image: np.ndarray, m: FrameMapper, P: Profile,
                         ) -> tuple[list[ActiveTrait], float, bool]:
        """특성 패널: 행마다 [큰 숫자=인원][이름][구간]. → (특성들, OCR 신뢰도, 패널이 완전한가).

        이름을 퍼지 매칭하고 같은 행 왼쪽 숫자를 인원으로 쓴다. OCR은 가는 "1"(I 모양) 글자를 자주 놓친다 →
        ① 이름 아래 "1/2" 사다리 글자(비활성 행), ② 인원 칸이 가는 세로 막대 하나인지(글자 모양)로 보충한다(19 보고 §3).
        "완전"하지 않은 경우: 패널 아래 "N+" 넘침 표시가 있다(특성이 더 있는데 가려졌다), 또는 인원을 끝내 못 읽은 행이 있다.
        보드 챔피언 식별(`vision.units`)은 완전한 패널만 구속으로 쓴다.
        """
        from .units import ladder_count, thin_one_glyph

        crop = m.crop(image, P.traits_panel)
        boxes = self.ocr.read(crop)
        if not boxes:
            return [], 0.0, False
        complete = not any(re.fullmatch(r"\s*\d\s*\+\s*", b.text) for b in boxes)
        numbers = [b for b in boxes if re.fullmatch(r"[0-9Il|]{1,2}", b.text.strip())]
        traits: list[ActiveTrait] = []
        confs: list[float] = []
        seen: set[str] = set()
        for b in boxes:
            match = self.trait_matcher.match(b.text)
            if match is None or match.api_name in seen:
                continue
            bps = [bp for bp in (match.record.get("breakpoints") or []) if bp is not None]
            # 인원수 숫자는 이름 **바로 왼쪽**에 붙어 있다. 거리 제한이 없으면 위/아래 행의 구간 사다리("2>4>6")
            # 숫자를 집어 "약탈자 8" 같은 확신에 찬 오답이 나온다(16:10 캡처에서 관측).
            row = [n for n in numbers
                   if n.box[2] <= b.box[0] + 2
                   and b.box[0] - n.box[2] <= max(b.height, n.height) * 1.5
                   and abs(n.cy - b.cy) <= max(b.height, n.height) * 1.2]
            count: int | None = None
            score = b.score
            if row:
                num = max(row, key=lambda n: n.box[2])
                count = parse.parse_int(num.text, 0, 15)
                score = min(score, num.score)
            if count is None:
                count = self._trait_count_fallback(crop, boxes, b, bps, ladder_count, thin_one_glyph)
                score = min(score, 0.8)
            if count is None or count == 0:
                complete = False            # 이름은 있는데 인원을 못 읽었다 → 합이 맞지 않을 수 있다
                continue
            if bps and count > max(bps) + 2:
                complete = False
                continue   # 최고 구간보다 크게 넘는 인원수는 숫자를 잘못 붙인 것이다
            active = max((bp for bp in bps if bp <= count), default=None)
            nxt = min((bp for bp in bps if bp > count), default=None)
            seen.add(match.api_name)
            traits.append(ActiveTrait(id=match.api_name, name_ko=match.record.get("name_ko"), count=count,
                                      active_breakpoint=active, next_breakpoint=nxt))
            confs.append(min(score, match.score / 100.0))
        return traits, (min(confs) if confs else 0.0), complete and bool(traits)

    @staticmethod
    def _trait_count_fallback(crop: np.ndarray, boxes: list[TextBox], name: TextBox, bps: list[int],
                              ladder_count, thin_one_glyph) -> int | None:
        """숫자 OCR이 인원을 놓친 행: 이름 아래 "인원/다음 구간" 글자 → 인원, 아니면 인원 칸 글자가 "1" 모양인가."""
        h = max(1.0, name.height)
        for lb in boxes:
            if lb is name or not (0.3 * h <= lb.cy - name.cy <= 1.6 * h) or abs(lb.box[0] - name.box[0]) > 1.2 * h:
                continue
            got = ladder_count(lb.text)
            if got is None:
                continue
            count, nxt = got
            if bps and (nxt in bps) and count <= nxt:
                return count
        x1, x2 = int(name.box[0] - 1.05 * h), int(name.box[0] - 0.2 * h)
        y1, y2 = int(name.cy - 0.35 * h), int(name.cy + 0.85 * h)
        cell = crop[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if thin_one_glyph(cell, h) and (not bps or 1 <= max(bps)):
            return 1
        return None

    def _read_hp(self, image: np.ndarray, m: FrameMapper, P: Profile) -> tuple[int, float] | None:
        """플레이어 목록에서 내 HP: 내 칸은 숫자 글자가 다른 칸보다 크다(fixture 관측, 약 1.8배).
        숫자 박스(가로/세로 ≤ HP_BOX_MAX_ASPECT)만 인식해 이름 박스 인식 비용을 뺀다."""
        boxes = self._read_boxes(image, m, P.player_list,
                                 lambda b: (b[2] - b[0]) <= HP_BOX_MAX_ASPECT * max(1e-6, b[3] - b[1]))
        nums = [(b, parse.parse_int(b.text, 0, 100)) for b in boxes if re.fullmatch(r"\s*\d{1,3}\s*", b.text)]
        nums = [(b, v) for b, v in nums if v is not None]
        if len(nums) < 3:
            return None
        heights = sorted(b.height for b, _ in nums)
        median = heights[len(heights) // 2]
        big = [(b, v) for b, v in nums if b.height >= median * 1.4]
        if len(big) != 1:
            return None
        b, v = big[0]
        return v, b.score * HP_FACTOR


PANEL_CACHE_TOL = 6           # 특성 패널 캐시: 글자 영역에서 이보다 많은 픽셀이 바뀌면 다시 읽는다
PANEL_CACHE_DIFF = 40         # 픽셀 밝기 차 임계
PANEL_TEXT_COLS = (0.20, 0.80)   # 패널 가로 비율: 인원 숫자(약 0.27~0.37) · 이름 · 구간 글자


def panel_text_region(crop: np.ndarray) -> np.ndarray:
    """특성 패널 크롭 → 캐시 비교용 글자 영역(원래 해상도 회색조, int16)."""
    import cv2

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    w = gray.shape[1]
    return gray[:, int(w * PANEL_TEXT_COLS[0]):int(w * PANEL_TEXT_COLS[1])].astype(np.int16)


def panel_unchanged(prev: np.ndarray, cur: np.ndarray) -> bool:
    """두 패널 글자 영역이 같은가(크기가 다르면 다르다)."""
    if prev.shape != cur.shape:
        return False
    return int((np.abs(prev - cur) > PANEL_CACHE_DIFF).sum()) <= PANEL_CACHE_TOL


def augment_template_dirs(set_number: int) -> list[Path]:
    """증강 글리프 템플릿: CDragon 원본(augments, `fetch-augments`) + 실화면(augments_screen, `harvest-augments`·선택 순간 학습)."""
    base = PROJECT_ROOT / "data" / "templates" / str(set_number)
    return [base / "augments", base / "augments_screen"]


def augment_alt_dir(set_number: int) -> Path:
    """CDragon에 없는 세트 증강의 대체 출처 아이콘(`fetch-augments`, tactics.tools). 글리프 정규화 경로로 비교한다."""
    return PROJECT_ROOT / "data" / "templates" / str(set_number) / "augments_alt"


def _augment_icon_owners(static: StaticData, keys: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """그림 키(`augment_visual_keys`, 생략하면 대체 출처 없이 계산) → 그 그림을 쓰는 증강 레코드들."""
    if keys is None:
        keys = augment_visual_keys(static)
    owners: dict[str, list[dict]] = {}
    for rec in static.tables.get("augments", []):
        k = keys.get(rec["apiName"])
        if k:
            owners.setdefault(k, []).append(rec)
    return owners


def _dark_disc(hsv: np.ndarray) -> np.ndarray | None:
    """연승 아이콘 칸(HSV) → 아이콘의 **어두운 원**(불꽃 포함) 마스크. 원을 못 찾으면 None(칸 전체를 쓴다).
    가장 큰 어두운 덩어리의 볼록 껍질 = 원(안의 불꽃 구멍까지 채워진다)."""
    import cv2

    dark = (hsv[..., 2] < 50).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    if n <= 1:
        return None
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[big, cv2.CC_STAT_AREA] < 0.15 * dark.size:
        return None
    pts = np.column_stack(np.nonzero(labels == big))[:, ::-1].astype(np.int32)
    hull = cv2.convexHull(pts)
    out = np.zeros(dark.shape, np.uint8)
    cv2.fillConvexPoly(out, hull, 1)
    return out.astype(bool)


def _has(tb: TextBox | None, words: tuple[str, ...]) -> bool:
    return tb is not None and any(w in tb.text or w in tb.text.lower() for w in words)


def item_template_dirs(set_number: int) -> list[Path]:
    """아이템 템플릿 디렉터리: CDragon 원본 아이콘(items) + 실화면 템플릿(items_screen, harvest 결과).
    ID별로 합쳐진다(실화면 템플릿은 추가일 뿐 원본 아이콘을 가리지 않는다, QA04-V1)."""
    base = PROJECT_ROOT / "data" / "templates" / str(set_number)
    return [base / "items", base / "items_screen"]


def _card_is_empty(card: np.ndarray) -> bool:
    """구매 완료된 빈 상점 칸: 어둡고 무늬가 거의 없다(가운데 흐린 헬멧 문양만)."""
    if card.size == 0:
        return False
    return float(card.mean()) < 45.0 and float(card.std()) < 25.0


_default: Recognizer | None = None


def recognize(image: np.ndarray, **kwargs) -> GameState:
    """기본 설정 인식기로 1장 인식(프로세스 내 싱글턴). 앱/테스트는 `Recognizer`를 직접 만들어 재사용해도 된다."""
    global _default
    if _default is None:
        _default = Recognizer()
    return _default.recognize(image, **kwargs)


def recognize_file(path: str | Path, recognizer: Recognizer | None = None) -> GameState:
    """스크린샷 파일 → GameState (`--screenshot` 모드와 fixture 평가 공용)."""
    from .capture import load_image

    rec = recognizer or Recognizer()
    return rec.recognize(load_image(path), source_image=str(path))
