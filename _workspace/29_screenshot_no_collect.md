# 29 — 스크린샷 실행은 유닛 사진을 모으지 않음 (QA 27 W5) — app-integrator

## 문제
`--screenshot`은 `Recognizer(cfg=settings.vision)`로 인식기를 만들었다. settings.toml에서 `unit_autolearn = true`라서
`UnitCollector`가 붙었고, 한 장짜리 QA 실행이 사용자 DB 검토 대기(`data/templates/18/units_screen/_pending/`)에
크롭을 넣었다(2026-09-23 test.png 2-6에서 `traits` 5장).

## 수정 (app/ 안에서만. vision/units.py, vision/unit_db.py는 건드리지 않음)
`vision`에는 수집기를 끄는 스위치가 따로 없다. 그래서 공개된 부분만 쓴다: `VisionConfig.unit_autolearn`, `UnitNamer.collector`/`autolearn`.

- 새 파일 `src/tft_advisor/app/no_collect.py`
  - `no_collect_vision(cfg)`: `unit_autolearn=False`로 바꾼 복사본을 만든다. 이렇게 하면 수집기를 아예 만들지 않고, `migrate_legacy()`의 파일 이동도 일어나지 않는다.
  - `disable_unit_collector(recognizer)`: 이미 만들어진 인식기에서 `unit_namer.collector = None`, `autolearn = False`로 바꾼다.
  - `make_recognizer_no_collect(vision_cfg)`: 위 두 가지를 적용한 `Recognizer`를 만든다.
- `app/screenshot.py` `run_screenshot`: 인식기를 스스로 만들 때는 `make_recognizer_no_collect`를 쓴다. 호출자가 넘긴 인식기에도 `disable_unit_collector`를 적용한다. 그다음 `single_frame_names`(agree_frames=1)가 실행된다. `--test-view` 실행도 이 경로를 탄다.
- `app/setup.py`(감지용 screen_score, 콘솔 테스트 캡처)와 `app/setup_dialog.py._get_recognizer`(테스트 캡처·감지)도 `make_recognizer_no_collect`를 쓴다. 한 장짜리 확인용 실행이므로 스크린샷과 같은 규칙을 적용했다.
- 바뀌지 않는 것: `--live`(`app/live.py`)는 그대로 수집한다. 이번 실행의 **메모리** 표본 학습(`UnitNamer._learn`, 디스크에 쓰지 않음)과 승인 라이브러리 읽기도 그대로다.
- `vision/evaluate.py`는 `Recognizer()` 기본 설정을 쓰는데, 기본값이 `unit_autolearn=False`라 원래 수집하지 않는다. `templates harvest-units`는 라벨을 붙여 모으는 도구라 의도된 수집이므로 두었다.

## 정리
`UnitImageDB.delete()`(vision이 제공하는 휴지통 이동)로 대기 크롭 5장(png와 json)을 `units_screen/_trash/{챔피언}/`으로 옮겼다.
옮기기 전에 json을 모두 확인했다: game `20260923-221619-442b`, stage 2-6, evidence traits, 2026-09-23T22:16:20, frame bffadaab9bf3.
대상: Akali_AD `traits_8820393bfb15`, Camille `traits_ab640610de7d`, Cassiopeia `traits_b1dc4d91feb4`,
Elise `traits_219d7970d6c1`, KogMaw18_AD `traits_0294127fda45`. `_pending/`에는 이제 파일이 0개다. `_trash/`는 gitignore 대상이다.

## 검증
- 새 테스트 `tests/app/test_screenshot_no_collect.py`(5개 통과)
  - 도우미 단위 테스트, `run_screenshot`이 `unit_autolearn=False`로 인식기를 만드는지 확인
  - test.png로 대조 실험을 했다. 수집기를 붙이면 `observe`가 불린다. 수집기가 켜진 인식기를 `run_screenshot(..., test_view=True)`에 넘기면 `collector is None`이 되고, observe는 0회 불리며, tmp DB 파일도 0개다. test.png는 gitignore 대상이라 파일이 없으면 이 테스트는 skip된다.
- 실제 CLI: 사용자 설정(autolearn=true)으로 `python -m tft_advisor --screenshot tests/fixtures/screens/test/test.png --test-view --no-overlay`를 실행했다. 종료 코드 0이고 `units_screen/` 파일 목록은 실행 전과 같다(`_pending/` 0개).
- 전체 pytest(`PYTHONIOENCODING=utf-8`, `-o addopts=""`): **1322 passed, 3 skipped, 1 xfailed, 4 failed**. 실패 4건은 알려진 Windows 건(test_api_key 마스킹 힌트, test_setup 권한 상자, test_credentials 0600 두 건)이다. 전체 실행 후에도 `_pending/`은 0개다.

## 참고
- vision에 `UnitNamer.disable_collection()` 같은 공식 스위치가 생기면 `no_collect.disable_unit_collector`가 그것을 부르도록 바꾸면 된다.
- 커밋하지 않았다.
