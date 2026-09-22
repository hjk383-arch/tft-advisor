"""프레임 → GameState 조립. 공개 API: `Recognizer.recognize(image) -> GameState`, `recognize(image)`.

원칙
- 화면 픽셀만 쓴다. 입력 이미지는 BGR ndarray(실시간 캡처와 스크린샷 파일이 같은 경로).
- 불확실하면 None. 값이 있는 필드는 `GameState.confidence[field]`에 0~1 신뢰도를 기록한다.
- 신뢰도 = OCR 점수 × 파싱/매칭 점수 × 필드별 보정. ROI가 원본 캡처로 검증되기 전이므로 값은 모두 PROVISIONAL.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TypeVar

import numpy as np

from ..config import VisionCfg
from ..contracts import (
    ActiveTrait, AugmentRef, FieldSource, GameState, ItemRef, ItemState, ScreenMode, ShopSlot, ShopSlotKind,
)
from ..static_data import PROJECT_ROOT, StaticData, load_static
from . import parse
from .icons import IconMatcher, slot_is_empty
from .item_ids import ItemCatalog
from .matching import NameMatcher
from .ocr import OcrEngine, TextBox, create_ocr
from .regions import FrameMapper, Profile, Rect, detect_content_box, profile_for_frame
from .screen_mode import ModeSignals, classify, frame_is_dark, hud_panel_pixels

log = logging.getLogger(__name__)
T = TypeVar("T")
# 부분 인식 묶음(QA04-V7). stage와 화면 상태 신호는 항상 읽는다(한 줄 인식만, 싸다).
# app 루프는 `change.ChangeDetector`가 알려 준 묶음만 넘기고, 결과를 직전 GameState에 합친다(FIELD_GROUP 참고).
GROUPS: tuple[str, ...] = ("hud", "shop", "items", "augment", "players", "traits")
# 기본값에서 "traits"(특성 패널)는 뺀다: 검출+인식이 프레임 시간의 약 35%인데, active_traits는 신뢰도가 TRAITS_FACTOR로
# 임계 미만이라 advisor가 쓰지 않는다. 필요하면 groups=GROUPS 로 켠다(평가·디버그, app이 몇 초에 한 번).
DEFAULT_GROUPS: tuple[str, ...] = tuple(g for g in GROUPS if g != "traits")
FIELD_GROUP: dict[str, str] = {
    "stage": "stage", "screen_mode": "stage",
    "level": "hud", "xp": "hud", "gold": "hud", "shop_odds": "hud", "streak": "hud",
    "shop": "shop", "items": "items", "augment_offer": "augment", "hp": "players", "active_traits": "traits",
}
_HUD_WORDS = ("경험치", "새로고침", "구매", "buy", "refresh")
_TITLE_WORDS = ("선택", "choose")

# 필드별 보정 계수 — 검증되지 않은 해석 규칙이 들어간 필드는 낮춘다.
# 연승/연패 부호를 아이콘 색으로 추정(연패 예시 fixture 없음). QA04-V4: 임계(0.6)에 딱 걸리면 포함 여부가 반올림으로
# 정해진다 → 임계에서 떨어뜨려 **부호가 검증될 때까지 advisor 입력에서 제외**(0.5). streak=0은 부호 무관이라 OCR 점수 그대로.
# 연패 캡처(요청서 #8)로 부호 규칙을 확인하면 0.8로 올린다.
STREAK_SIGN_FACTOR = 0.5
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
        """이 프레임의 게임 화면 영역. 인자 > 설정 `content_box` > (content_box_auto면) 레터박스 자동 탐지."""
        if content is not None:
            return content
        box = self.cfg.content_px(image.shape[1], image.shape[0])
        if box is not None:
            return box
        return detect_content_box(image) if self.cfg.content_box_auto else None

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
        unknown_groups = g - set(GROUPS)
        if unknown_groups:
            raise ValueError(f"알 수 없는 묶음: {sorted(unknown_groups)} (가능: {GROUPS})")
        content = self.content_for(image, content)
        m = FrameMapper.for_image(image, content)
        out = _Out()
        # ROI 프로파일은 **게임 화면 영역의 비율**로 고른다(레터박스·창 테두리를 뺀 뒤). 설정으로 고정할 수도 있다.
        _, _, box_w, box_h = m.box
        P = self.profile_for(box_w, box_h)

        # 항상 읽는 4칸(스테이지, HUD 버튼 2개, 증강 제목)은 한 줄 인식 한 번(배치)으로 먼저 읽는다.
        pre = self._lines([m.crop(image, r) for r in (P.stage, P.xp_button, P.refresh_button, P.augment_title)])
        (stage, stage_conf), = self._read_parsed_many(image, m, [(P.stage, parse.parse_stage)], lines=pre[:1])
        out.put("stage", stage, stage_conf)

        # 화면 상태 신호
        hud_by_ocr = self._find_keyword(image, m, (P.xp_button, P.refresh_button), _HUD_WORDS, lines=pre[1:3])
        hud = hud_by_ocr or hud_panel_pixels(m.crop(image, P.xp_button))
        # 증강 화면에는 상점 HUD가 없다 → HUD 글자가 읽혔으면 제목은 한 줄 인식만(검출 생략).
        augment_title = self._find_keyword(image, m, (P.augment_title,), _TITLE_WORDS, detect=not hud_by_ocr,
                                           lines=pre[3:])
        offers = self._read_augment_offer(image, m, P) if ((augment_title or not hud) and "augment" in g) else []
        sig = ModeSignals(
            shop_hud=hud, shop_hud_by_ocr=hud_by_ocr, augment_title=augment_title,
            augment_names_matched=sum(1 for a in offers if a is not None), stage=stage,
            dark=frame_is_dark(image),
        )
        mode, mode_conf = classify(sig)
        if mode != ScreenMode.UNKNOWN:
            out.put("screen_mode", mode, mode_conf)

        if "players" in g:
            out_hp = self._read_hp(image, m, P)
            if out_hp:
                out.put("hp", *out_hp)

        if mode == ScreenMode.AUGMENT_SELECT:
            if offers and all(a is not None for a in offers):
                refs = [a for a, _ in offers]
                out.put("augment_offer", refs, min(c for _, c in offers))
            if "items" in g:
                self._read_items(image, m, P, out)
        elif mode == ScreenMode.PLANNING:
            if "hud" in g:
                self._read_hud_numbers(image, m, P, out)
            if "shop" in g:
                self._read_shop(image, m, P, out)
            if "items" in g:
                self._read_items(image, m, P, out)
            if "traits" in g:
                self._read_traits(image, m, P, out)

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

    # ------------------------------------------------------------------ 필드별
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

    def _read_traits(self, image: np.ndarray, m: FrameMapper, P: Profile, out: _Out) -> None:
        """왼쪽 특성 패널: 행마다 [큰 숫자=인원][이름][구간]. 이름을 퍼지 매칭하고 같은 행 왼쪽 숫자를 인원으로."""
        boxes = self._read(image, m, P.traits_panel)
        if not boxes:
            return
        numbers = [b for b in boxes if re.fullmatch(r"[0-9Il|]{1,2}", b.text.strip())]
        traits: list[ActiveTrait] = []
        confs: list[float] = []
        seen: set[str] = set()
        for b in boxes:
            match = self.trait_matcher.match(b.text)
            if match is None or match.api_name in seen:
                continue
            # 인원수 숫자는 이름 **바로 왼쪽**에 붙어 있다. 거리 제한이 없으면 위/아래 행의 구간 사다리("2>4>6")
            # 숫자를 집어 "약탈자 8" 같은 확신에 찬 오답이 나온다(16:10 캡처에서 관측).
            row = [n for n in numbers
                   if n.box[2] <= b.box[0] + 2
                   and b.box[0] - n.box[2] <= max(b.height, n.height) * 1.5
                   and abs(n.cy - b.cy) <= max(b.height, n.height) * 1.2]
            if not row:
                continue
            num = max(row, key=lambda n: n.box[2])
            count = parse.parse_int(num.text, 0, 15)
            if count is None or count == 0:
                continue
            bps = [bp for bp in (match.record.get("breakpoints") or []) if bp is not None]
            if bps and count > max(bps) + 2:
                continue   # 최고 구간보다 크게 넘는 인원수는 숫자를 잘못 붙인 것이다
            active = max((bp for bp in bps if bp <= count), default=None)
            nxt = min((bp for bp in bps if bp > count), default=None)
            seen.add(match.api_name)
            traits.append(ActiveTrait(id=match.api_name, name_ko=match.record.get("name_ko"), count=count,
                                      active_breakpoint=active, next_breakpoint=nxt))
            confs.append(min(b.score, num.score, match.score / 100.0))
        if traits:
            out.put("active_traits", traits, min(confs) * TRAITS_FACTOR)

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
