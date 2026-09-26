# 33 — 고정 크기 HUD · 목표 덱 유닛 아이콘 · app-integrator

사용자 요청
1. "HUD 크기를 좀 늘려줘 — 자꾸 라인이 늘어났다 줄었다 해서 헷갈려."
2. "최종덱을 보여줄 때 최종덱에 들어가는 유닛들도 표시해줘. 메타사이트처럼 캐릭터 아이콘으로 보여주는 것도 가능할까?"

## 1. 고정 크기 HUD
- 창 크기 고정: `[overlay] width = 460`, `height = 960`(새 키). 화면보다 크면 화면 크기로 줄이고, 대신 섹션 줄 수가 줄어든다.
  `render()`는 내용만 바꾸고 크기를 건드리지 않는다(예전 `_fit_height` 삭제). 설정 화면의 배율(scale)은 글꼴·여백·아이콘에 그대로 쓰인다.
- 본문은 QLabel 리치 텍스트 대신 직접 그리는 위젯(`app/hud_view.HudView`)이다. 표시 모델은 Qt 없이 `app/hud_model.py`.
- 배치(위에서 아래, 늘 전부 있음): 머리 3줄(제목 · 상태 · 알림) → [목표 덱] 덱 3자리(머리 줄 + 아이콘 줄 + `lines_comp`=3줄)
  → [보드 배치] `lines_board`=7 → [상점] `lines_shop`=5 → [증강 선택] `lines_augment`=4 → [아이템] `lines_item`=4(재료 줄 포함) → 상태줄 2줄.
  - 넘치면 마지막 줄이 "… 외 N줄"이 된다. 비면 자리 표시 한 줄("(보드 배치 추천 없음)", "(증강 선택 화면에서 표시합니다)" 등) + 빈 줄.
  - 긴 줄은 줄바꿈하지 않고 말줄임(…)한다.
  - 높이가 모자라면 증강 → 아이템 → 상점 → 보드 배치 → 목표 덱 순서로 한 섹션씩 1줄까지 줄이고(`hud_model.fit_budgets`),
    그래도 모자라면 아이콘 줄을 글자 줄로 바꾼다. 아래 섹션을 잘라내지 않는다. 줄 수는 설정·글꼴·창 높이로만 정해지므로 갱신 사이에 바뀌지 않는다.
- 목표 덱 띠(`DeckChooser`)는 `overlay.chooser_offset()`(= [목표 덱] 제목 y)에 붙고, ✕ 손잡이는 예전처럼 오른쪽 위에 붙는다.
- 테스트 호환: `window.body.text()`는 보이는 줄의 HTML 모양 원문(말줄임 전)을 돌려준다.
- 바뀐 동작: 보드 배치 추천이 없어도 [보드 배치] 섹션 자리가 남는다(`test_shop_refresh`를 자리 표시 확인으로 고쳤다).

## 2. 목표 덱 최종 유닛
- 계약: `TargetComp.final_board: list[CompUnit] = []`(선택, 최대 12). `advisor/scoring.py`의 `target_comp()`가 `CompStats.final_board`를 복사한다(한 줄).
- 오버레이: 덱마다 아이콘 줄. 코스트 색 테두리(1 회색 · 2 초록 · 3 파랑 · 4 보라 · 5 금색). 캐리는 금색 굵은 테두리 + "C" 배지,
  보유는 ✓ 배지, 부족은 50% 투명. 보유를 모르면 전부 보통으로 그린다. ★3 목표(리롤)만 "★3" 표시(★2는 거의 모든 유닛이라 뺐다).
  아이콘 파일이 없는 칸은 이름 앞 두 글자, 아이콘을 끄거나(`unit_icons=false`) 높이가 모자라면 "최종: 이름✓ · …" 글자 줄.
- 콘솔·`--screenshot`: `comp_lines`(자세히)에 "최종 덱: 마스터 이★3(캐리) · 렝가★3 · 세트 · …"(보유면 ✓) 줄. `report.final_units_text`.

## 3. 챔피언 아이콘
- `python -m tft_advisor.vision.templates fetch-champions [--overwrite] [--delay 0.5]` → `data/templates/18/champions/{apiName}.png`.
  정적 데이터의 `tileIcon`(`.../tft18_x_square.tex`, 128px 네모 얼굴)을 쓰고, 없으면 `squareIcon`(팀 플래너 그림 256px)을 쓴다. 상점에 나오는 챔피언 74개. 기존 `_fetch_icons` 사용(요청 간격·이미 받은 파일 건너뜀).
- `.gitignore`에 `data/templates/*/champions/` 추가(Riot 아트).
- 첫 실행: `live.ensure_champion_icons`가 폴더가 비었으면 백그라운드 스레드에서 받고, 끝나면 `overlay.icons_ready` → 캐시를 비우고 다시 그린다. 실패해도 이름 글자로 그린다.
- `IconBook`: (ID, 크기)별로 줄인 QPixmap 캐시. 그릴 때 파일을 다시 읽지 않는다.
- 이 PC에는 이미 받아 두었다(74개, 실패 0).

## 4. 설정(`config/settings.toml [overlay]`, `config.OverlayCfg`)
`width 460 · height 960 · lines_comp 3 · lines_board 7 · lines_shop 5 · lines_item 4 · lines_augment 4 · unit_icons true · icon_size 28`

## 5. 미리보기
실제 Windows 플랫폼(글꼴 확인), test.png 인식 + mock 추천. 스크립트는 세션 scratchpad의 `hud_preview.py`.
- `hud_preview.png`: 추천이 있을 때
- `hud_preview_empty.png`: 추천이 없을 때 — 섹션 자리가 같다

## 6. 테스트
`tests/app/test_hud_fixed.py` 12개: 내용이 바뀌어도 크기·섹션 위치 불변, 줄 수 줄이는 순서, 말줄임·자리 표시, 줄 수가 내용과 무관, 아이콘 없으면 글자,
pixmap 캐시, 보유/부족/캐리/★3 표시, 콘솔 "최종 덱", CDragon 경로 매핑·내려받기(기록한 레코드, `_download` 대체 — 네트워크 없음), 첫 실행 백그라운드 받기.
전체 `PYTHONIOENCODING=utf-8`: 알려진 Windows 4건 + `tests/test_vision_units.py` 2건(vision-engineer가 지금 고치는 `vision/unit_db.py`·`units.py` 쪽, 이번 변경과 무관).
`test_stats_refresh`/`test_vision_qa04`가 한 번 실패했다가 다시 돌리니 통과했다(다른 에이전트가 파일을 고치던 중으로 보인다).

## 7. 참고
- `--screenshot` 콘솔 확인을 한 번 CLI 기본 백엔드로 돌려 Jev live 호출이 1회(약 1만 4천 토큰) 나갔다. 이후 미리보기는 mock으로만 돌렸다.

---

## 8. 후속 — [보드 배치] 라인업도 아이콘으로 (사용자: "보드에 올린 추천 유닛 보여줄 때도 아이콘으로 보여줘")
- 아이콘 모드에서 [보드 배치]는 **라인업 아이콘 줄 1개(고정 높이) + 글자 줄 `lines_board - 1`개**다. "보드: …" 글자 줄 자리를 아이콘 줄이 대신하고,
  첫 줄 "기준 … · 칸 N"(직전이면 "(직전) …")은 섹션 제목 옆으로 올라간다. 교체·벤치·판매·참고 줄은 글자 그대로(교체·판매는 노랑).
- 아이콘 칸(`hud_model.lineup_cells`, 점수 순): 코스트 색 테두리, ★2/★3, 1위 덱 캐리 = 금색 테두리 + "C",
  **벤치에서 올릴 유닛(action=field) = 오른쪽 위 하늘색 "↑"**, 든 아이템 = 오른쪽 아래 주황 점(최대 3, 상태의 같은 챔피언·성급 유닛에서),
  이름 미상 보드 칸(`unknown_on_board`) = "?" 칸. 직전 계획(stale)은 아이콘 전체 45% 투명.
- 글자로 돌아가는 경우: 아이콘을 끔(`unit_icons=false`)·높이 부족(`fit_budgets`가 아이콘 줄을 뺌) → 예전 "보드: …" 줄 그대로.
  라인업이 비었으면 아이콘 자리에 "보드: -" 글자, 추천이 없으면 자리 표시 "(보드 배치 추천 없음)" — 어느 경우든 높이가 같다(`model_height`).
- 판매 후보 아이콘 줄은 넣지 않았다(줄 수를 하나 더 먹는다) — 판매는 노란 글자 줄로 충분히 보인다.
- 테스트(`tests/app/test_hud_fixed.py` +3): 라인업 칸(★·아이템·캐리·↑·?), 아이콘 줄이 "보드:" 줄을 대신하고 줄 수 고정, 글자 모드·직전 흐림·추천 없음 자리 표시,
  여러 추천 사이 창 높이·섹션 위치 불변.
- 미리보기(실제 Windows, test.png + mock): scratchpad `hud_preview_board.png`(`hud_preview_board_empty.png`).
- 전체 테스트: 알려진 Windows 4건 외에 advisor 쪽 실패가 보인다 — 다른 에이전트가 `advisor/engine.py`·`candidates.py`를 고치는 중이었다
  (한때 engine.py 136행 SyntaxError). 이번 변경(app/hud_*·테스트)과 무관.

---

## 9. QA 36 후속 (W2 성급 미상 · W4 빌드업 줄 잘림)
- **W2**: 성급을 못 읽은 유닛(None)이 ★1처럼 보였다.
  - 상태 줄(`report._unit_label`): 이름을 아는 유닛이면 " ★?"(예 "아칼리 ★? (자리 미상)"). 이름 미상 칸에는 붙이지 않는다.
    `tests/test_vision_units.py::test_report_lists_named_units` 기대값을 새 문구로 고쳤다.
  - HUD 라인업 아이콘: `IconCell.star_unknown` → 아이콘 왼쪽 아래 흐린 "★?"(글자 모드 "이름★?"). 목표 덱 최종 유닛 줄은 목표 성급이라 해당 없음.
- **W4**: HUD에서는 빌드업 기준 줄을 `hud_model.hud_reference_line`으로 바꿔 덱 이름(제목 옆에 이미 있음)을 빼고 보유 수를 앞에 둔다:
  "레벨 5 빌드업 보유 2/5: 알리스타 · 오른 · 쉔 · 바루스 · 자야"(아래 레벨 폴백이면 끝에 "(레벨 N 통계 없음)"). 콘솔(`report.board_plan_lines`)은 긴 줄 그대로.
  `lines_board` 기본값 7 유지(테스트로 고정).
- **선택 항목(넣음)**: 라인업 아이콘 뒤에 `BoardPlan.missing`(기준 보드에 있는데 없는 유닛)을 흐린 아이콘 + 초록 "+" 배지로 붙였다(글자 "(구하기)").
  `BoardPlanEntry.in_reference=True`인 라인업 유닛은 아이콘 아래 하늘색 막대(빌드업 기준 유닛), False(임시로 채운 유닛)는 막대 없음. 줄 수·높이는 그대로다.
- 테스트(`tests/app/test_hud_fixed.py` +2): ★? 칸·상태 줄, 기준 줄 덱 이름 없음·"보유 n/m" 앞·콘솔 긴 줄 유지·lines_board 7, missing 칸 흐림·"+".
- 미리보기(실제 Windows, mock 추천만): scratchpad `hud_qa36_test.png`, `hud_qa36_live4.png`(스크립트 `hud_preview2.py`).
- 전체 `PYTHONIOENCODING=utf-8`: 알려진 Windows 4건만 실패.
