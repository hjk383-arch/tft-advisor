"""고정 크기 HUD · 섹션 줄 수 · 목표 덱 유닛 아이콘(33 보고)."""
from __future__ import annotations

import json
import threading

from tft_advisor.app.hud_model import (
    SHRINK_ORDER, Budgets, IconCell, Row, build_model, fit_budgets, fit_rows, layout_rows, model_height, model_html,
)
from tft_advisor.app.hud_view import IconBook
from tft_advisor.app.names import NameBook
from tft_advisor.app.overlay import OverlayWindow
from tft_advisor.app.report import comp_lines, final_units_text
from tft_advisor.contracts import (
    BoardPlan, BoardPlanEntry, CompUnit, Recommendation, ShopAdvice, ShopSlotKind, TargetComp,
)

from .conftest import planning_state, sample_recommendation

UNITS = ["DA_18_Xayah", "DA_Cinderling18", "DA_Murkwolf18", "DA_18_Hecarim", "DA_Sentinel18"]


def big_rec() -> Recommendation:
    comps = [TargetComp(comp_id=f"c{i}", name=f"아주 긴 이름의 덱 {i} " * 3, score=0.5, carry=UNITS[0],
                        owned_units=UNITS[:2], missing_units=UNITS[2:],
                        final_board=[CompUnit(id=u, star=3 if j == 0 else 2) for j, u in enumerate(UNITS)])
             for i in range(3)]
    plan = BoardPlan(slots=5, lineup=[BoardPlanEntry(unit_id=u, on_board=True, action="keep", reason="이유 " * 20)
                                      for u in UNITS], notes=[f"참고 {i}" for i in range(12)])
    shop = [ShopAdvice(slot=i, kind=ShopSlotKind.CHAMPION, offer_id=UNITS[i], buy=i % 2 == 0, score=0.5,
                       reason="아주 긴 근거 " * 10) for i in range(5)]
    return Recommendation(jev_used=True, target_comps=comps, board_plan=plan, shop=shop,
                          component_priority=["DA_Component_BFSword"])


def small_rec() -> Recommendation:
    return Recommendation(jev_used=True, target_comps=[TargetComp(comp_id="c1", name="덱", score=0.4)])


def test_window_size_and_section_positions_do_not_change_between_updates(qapp, settings, tmp_path):
    w = OverlayWindow(settings, state_dir=tmp_path)
    size0 = w.size()
    seen = []
    for rec in (None, small_rec(), big_rec(), sample_recommendation(), None):
        w.set_data(planning_state(), rec)
        w.body.grab()                     # 그려야 섹션 위치가 정해진다
        seen.append((w.size().width(), w.size().height(), dict(w.body.section_y)))
    assert all(s == seen[0] for s in seen)
    # 설정 크기(offscreen 화면이 더 작으면 화면 높이로 줄어든다 — 그래도 고정)
    assert (size0.width(), size0.height()) == seen[0][:2] == w.fixed_size()
    assert w.fixed_size()[0] == settings.overlay.width
    assert set(seen[0][2]) == {"comps", "board", "shop", "augment", "item"}
    w.deleteLater()


def test_budgets_shrink_lowest_priority_first():
    base = Budgets(comps=3, board=7, shop=5, augment=4, item=4)
    full = model_height(base, 3, 18, 30)
    b = fit_budgets(base, 3, full - 18 * 5, 18, 30)       # 5줄 모자람: 증강 3줄 → 아이템 2줄
    assert (b.augment, b.item, b.shop, b.board, b.comps) == (1, 2, 5, 7, 3)
    b = fit_budgets(base, 3, 200, 18, 30)                  # 아주 작으면 전부 1줄까지, 그래도 넘치면 아이콘 뺌
    assert (b.augment, b.item, b.shop, b.board, b.comps) == (1, 1, 1, 1, 1) and b.icons is False
    assert SHRINK_ORDER == ("augment", "item", "shop", "board", "comps")
    assert fit_budgets(base, 3, full, 18, 30) == base      # 맞으면 그대로


def test_rows_are_elided_or_padded_to_the_budget():
    rows = [Row("text", f"줄 {i}") for i in range(6)]
    out = fit_rows(rows, 4, "(없음)")
    assert [r.text for r in out] == ["줄 0", "줄 1", "줄 2", "… 외 3줄"]
    out = fit_rows([], 3, "(없음)")
    assert out[0].text == "(없음)" and [r.kind for r in out[1:]] == ["blank", "blank"]


def test_layout_row_count_is_independent_of_content(settings):
    names = NameBook()
    b = Budgets.from_cfg(settings.overlay)
    counts = {len(layout_rows(build_model(planning_state(), r, names), b)) for r in (None, small_rec(), big_rec())}
    assert len(counts) == 1


def test_icons_fall_back_to_names_without_files(qapp, settings, tmp_path):
    book = IconBook(tmp_path / "none")
    assert book.get("DA_18_Xayah", 28) is None and not book.available()
    names = NameBook()
    model = build_model(planning_state(), big_rec(), names)
    html = model_html(model, Budgets.from_cfg(settings.overlay))
    assert "최종: " in html and names.name(UNITS[0]) in html
    cfg = settings.overlay.model_copy(update={"unit_icons": False})
    s2 = settings.model_copy(update={"overlay": cfg})
    w = OverlayWindow(s2, state_dir=tmp_path)
    w.set_data(planning_state(), big_rec())
    w.body.grab()                                           # 아이콘 없이도 그린다
    assert names.name(UNITS[2]) in w.body.text()
    w.deleteLater()


def test_icon_book_caches_scaled_pixmaps(qapp, tmp_path):
    from PySide6.QtGui import QColor, QPixmap

    pix = QPixmap(128, 128)
    pix.fill(QColor(200, 10, 10))
    pix.save(str(tmp_path / "DA_18_Xayah.png"))
    book = IconBook(tmp_path)
    a = book.get("DA_18_Xayah", 28)
    b = book.get("DA_18_Xayah", 28)
    assert a is b and a.width() == 28 and book.loads == 1
    book.refresh()
    book.get("DA_18_Xayah", 28)
    assert book.loads == 2


def test_deck_cells_mark_owned_missing_carry_and_star3():
    from tft_advisor.app.hud_model import deck_cells

    cells = deck_cells(big_rec().target_comps[0], NameBook())
    assert [c.unit_id for c in cells] == UNITS
    assert cells[0].carry and cells[0].star == 3 and cells[0].owned is True
    assert cells[1].star is None and cells[2].owned is False
    unknown = TargetComp(comp_id="x", name="x", score=0.1, final_board=[CompUnit(id=UNITS[0])])
    assert deck_cells(unknown, NameBook())[0].owned is None     # 보유를 모르면 흐리게 하지 않는다
    assert IconCell(unit_id="a", name="자야", owned=True, carry=True, star=3).text() == "자야★3✓(캐리)"


def test_console_shows_final_units_with_owned_marks():
    names = NameBook()
    comp = big_rec().target_comps[0]
    text = final_units_text(comp, names)
    assert text.startswith(f"{names.name(UNITS[0])}★3(캐리)✓") and f"{names.name(UNITS[2])}✓" not in text
    assert any(ln.strip().startswith("최종 덱:") for ln in comp_lines(comp, 1, names))
    assert not any("최종 덱" in ln for ln in comp_lines(comp, 1, names, compact=True))


# ---------------------------------------------------------------- 아이콘 받기(네트워크 없음)
RECORDED = [   # data/static/18/champions.json 에서 옮겨 적은 레코드(2026-09-25)
    {"apiName": "DA_Murkwolf18", "cost": 2, "shop_pool": True,
     "icon": "assets/ux/tft/championsplashes/x.tex",
     "squareIcon": "assets/characters/tft18_murkwolf/skins/base/images/t_18_murkwolf_teamplannersplash.tex",
     "tileIcon": "assets/characters/tft18_murkwolf/tft18_murkwolf_square.tex"},
    {"apiName": "DA_Only_Square", "cost": 1, "shop_pool": True,
     "squareIcon": "assets/characters/x/skins/base/images/t_x_teamplannersplash.tex", "tileIcon": None},
    {"apiName": "TFT_BlueGolem", "cost": 1, "shop_pool": False,
     "tileIcon": "assets/characters/tft_bluegolem/hud/tft_bluegolem_spell.tex"},
]


def test_champion_icon_mapping_and_fetch_without_network(monkeypatch, tmp_path):
    from tft_advisor.vision import templates as T

    assert T.champion_icon_path(RECORDED[0]) == RECORDED[0]["tileIcon"]
    assert T.champion_icon_path(RECORDED[1]) == RECORDED[1]["squareIcon"]
    assert T.cdragon_png_url(RECORDED[0]["tileIcon"]) == (
        "https://raw.communitydragon.org/latest/game/assets/characters/tft18_murkwolf/tft18_murkwolf_square.png")

    class Static:
        tables = {"champions": RECORDED}

    recs = T.champion_records(Static())
    assert [r["apiName"] for r in recs] == ["DA_Murkwolf18", "DA_Only_Square"]   # 상점 밖 유닛 제외
    urls = []
    monkeypatch.setattr(T, "load_static", lambda n: Static())
    monkeypatch.setattr(T, "template_dir", lambda n, kind: tmp_path / kind)
    monkeypatch.setattr(T, "_download", lambda url, timeout=15: urls.append(url) or b"png")
    ok, failed = T.fetch_champions(18, delay=0)
    assert ok == 2 and not failed and sorted(p.name for p in (tmp_path / "champions").glob("*.png")) == [
        "DA_Murkwolf18.png", "DA_Only_Square.png"]
    assert urls[0].endswith("tft18_murkwolf_square.png")
    assert not T.champion_icons_missing(18)
    ok, _ = T.fetch_champions(18, delay=0)                 # 이미 있으면 다시 받지 않는다
    assert ok == 2 and len(urls) == 2


def test_first_run_fetch_runs_in_background_and_reports(monkeypatch, settings, tmp_path):
    from tft_advisor.app import live
    from tft_advisor.vision import templates as T

    monkeypatch.setattr(T, "template_dir", lambda n, kind: tmp_path / kind)
    done = []
    called = threading.Event()

    def fake(n):
        called.set()
        return 3, []

    th = live.ensure_champion_icons(settings, on_done=done.append, fetch=fake)
    assert th is not None
    th.join(3)
    assert called.is_set() and done == [3]
    (tmp_path / "champions").mkdir()
    (tmp_path / "champions" / "a.png").write_bytes(b"x")
    assert live.ensure_champion_icons(settings, fetch=fake) is None     # 이미 있으면 받지 않는다
    off = settings.model_copy(update={"overlay": settings.overlay.model_copy(update={"unit_icons": False})})
    assert live.ensure_champion_icons(off, fetch=fake) is None


def test_config_defaults_are_larger(settings):
    assert settings.overlay.width >= 440 and settings.overlay.height >= 880
    assert json.dumps(Budgets.from_cfg(settings.overlay).__dict__)
