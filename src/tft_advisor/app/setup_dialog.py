"""실행 전 설정 대화상자(PySide6) — 해상도·모니터 자동 감지, 테스트 캡처, `settings.toml` 저장.

감지·저장 로직은 전부 `app/setup.py`에 있다(Qt 없이 테스트된다). 이 파일은 **화면만** 만든다.
헤드리스 테스트: `QT_QPA_PLATFORM=offscreen`으로 `SetupDialog`를 만들고 버튼 슬롯을 직접 부른다.

흐름
  [자동 감지] → 요약 + 경고 표시 → (필요하면 직접 고치기) → [테스트 캡처]로 ROI 확인 → [저장 후 시작]

API 키 칸은 `credentials` 모듈(환경변수 → OS 키체인 → 폴백 파일)만 부른다. 이 파일은 키 값을 보관하지도,
화면에 다시 보여 주지도 않는다 — 저장한 뒤에는 가린 힌트(`sk-…abcd`)만 보인다.
테스트는 `SetupDialog(creds=가짜, key_verifier=가짜)`로 진짜 키체인·진짜 Jev 호출 없이 돈다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import credentials as _credentials
from ..config import Settings, load_settings
from . import setup as core

log = logging.getLogger(__name__)

ACCENT = "#1b6ec2"
WARN = "#b26a00"
BAD = "#c0392b"
OK = "#2b8a3e"
MUTED = "#6b7280"
PREVIEW_W = 560

ASPECT_LABELS = {
    "auto": "자동(권장)", "16:9": "16:9 (1920x1080 등) — 실측", "16:10": "16:10 (1920x1200, 1440x900) — 실측",
    "4:3": "4:3 — 유도(미검증)", "21:9": "21:9 울트라와이드 — 유도(미검증)", "32:9": "32:9 — 유도(미검증)",
}
PROFILE_LABELS = {"auto": "자동(비율로 고름, 권장)", "set18_16x9": "set18_16x9 (16:9 실측)",
                  "set18_16x10": "set18_16x10 (16:10 실측)"}
JEV_LIVE_LABEL = "Jev 실시간 판단 사용 (TypeSafe API 과금, 요청당 약 $0.0005)"
JEV_OFF_LABEL = "고급: 추천에서 Jev를 빼기 — 통계·규칙만 사용(off)"
JEV_LIVE_NOTE = ("live — 화면(상점·증강)이 바뀔 때마다 TypeSafe Jev에 묻는다. 키가 없거나 실패하면 "
                 "통계 추천으로 물러난다.")
JEV_OFF_NOTE = "off — Jev를 아예 부르지 않는다(fallback_reason = jev_disabled). 통계·규칙 추천만 나온다."
KEY_PLACEHOLDER = "여기에 자기 TypeSafe API 키를 붙여 넣는다"
KEY_TEST_TOOLTIP = ("TypeSafe에 가장 작은 요청을 **한 번** 보내 키가 되는지 확인한다"
                    " (요청 1회 분량만 과금된다).")
KEY_TESTING = "연결 테스트 중… (TypeSafe에 요청 1회)"


def to_pixmap(image) -> QPixmap:
    """BGR numpy 배열 → QPixmap(미리보기용)."""
    import numpy as np

    arr = np.ascontiguousarray(image[:, :, ::-1])   # BGR → RGB
    h, w = arr.shape[:2]
    qimg = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class SetupDialog(QDialog):
    """설정 대화상자. 결과는 `self.outcome`(`setup.SetupOutcome`)에 담긴다."""

    def __init__(self, settings: Settings | None = None, *, config_dir: Path | None = None,
                 state_dir: Path | None = None, grabber: core.MonitorGrabber | None = None,
                 creds: object | None = None, key_verifier: object | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 키 보관소와 연결 테스트 호출자는 갈아 끼울 수 있다(테스트: 가짜 보관소 + 가짜 호출자).
        self.creds = creds or _credentials
        self.key_verifier = key_verifier
        self._key_info = _credentials.KeyInfo()
        self.settings = settings or load_settings(config_dir)
        self.config_dir = config_dir
        self.state_dir = state_dir or core.resolve_state_dir(self.settings)
        self.grabber = grabber or core.MonitorGrabber()
        self.detection: core.SetupDetection | None = None
        self.outcome = core.SetupOutcome(action="cancelled", settings=self.settings)
        self._recognizer = None

        self.setWindowTitle("TFT Advisor 설정")
        self.setMinimumSize(640, 600)
        self.resize(700, 820)
        self._build()
        self.auto_detect(ocr=False)   # 창이 바로 뜨도록 빠른 예비 감지(픽셀). 확인은 start()에서

    def start(self) -> core.SetupDetection:
        """창을 띄운 뒤 부른다 — 인식기(스테이지 OCR)로 다시 감지해 TFT 화면을 **확인**한다.

        인식기 생성(OCR 모델 + 템플릿)이 1~3초 걸리므로 창을 먼저 보여 주고 여기서 치른다.
        """
        return self.auto_detect(ocr=True)

    # ------------------------------------------------------------------ 화면 구성
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "<b>게임 화면을 자동으로 찾습니다.</b><br>"
            "TFT를 <b>테두리 없는 창 모드</b>로 띄운 뒤 [자동 감지]를 누르세요. "
            "값이 맞으면 [저장 후 시작]만 누르면 됩니다."))

        top = QHBoxLayout()
        self.detect_btn = QPushButton("🔍  자동 감지")
        self.detect_btn.setMinimumHeight(40)
        self.detect_btn.setStyleSheet(f"font-size: 15px; font-weight: bold; color: white; background: {ACCENT};"
                                      " border-radius: 6px; padding: 6px 18px;")
        self.detect_btn.clicked.connect(self.auto_detect)
        top.addWidget(self.detect_btn)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        top.addWidget(self.status, 1)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        inner = QVBoxLayout(body)

        # --- 감지 결과 ---
        box = QGroupBox("감지 결과")
        lay = QVBoxLayout(box)
        self.summary = QLabel("아직 감지하지 않았다.")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.warn_label = QLabel("")
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        lay.addWidget(self.warn_label)
        self.perm_box = QGroupBox("화면 캡처 권한")
        perm_lay = QVBoxLayout(self.perm_box)
        self.perm_label = QLabel(core.permission_help())
        self.perm_label.setWordWrap(True)
        perm_lay.addWidget(self.perm_label)
        retry = QPushButton("권한을 켰다 — 다시 시도")
        retry.clicked.connect(self.auto_detect)
        perm_lay.addWidget(retry)
        self.perm_box.setVisible(False)
        lay.addWidget(self.perm_box)
        inner.addWidget(box)

        # --- 직접 고치기 ---
        box = QGroupBox("직접 고치기 (보통은 건드릴 필요 없음)")
        grid = QGridLayout(box)
        row = 0
        self.monitor_combo = QComboBox()
        grid.addWidget(QLabel("모니터"), row, 0)
        grid.addWidget(self.monitor_combo, row, 1, 1, 3)
        row += 1
        self.aspect_combo = QComboBox()
        for key in core.ASPECT_CHOICES:
            self.aspect_combo.addItem(ASPECT_LABELS[key], key)
        grid.addWidget(QLabel("화면 비율"), row, 0)
        grid.addWidget(self.aspect_combo, row, 1, 1, 3)
        row += 1
        self.res_combo = QComboBox()
        self.res_combo.setEditable(True)
        self.res_combo.addItem("자동(캡처 크기를 그대로 사용)", "auto")
        grid.addWidget(QLabel("게임 해상도"), row, 0)
        grid.addWidget(self.res_combo, row, 1, 1, 3)
        row += 1
        self.profile_combo = QComboBox()
        for key, label in PROFILE_LABELS.items():
            self.profile_combo.addItem(label, key)
        grid.addWidget(QLabel("ROI 프로파일"), row, 0)
        grid.addWidget(self.profile_combo, row, 1, 1, 3)
        row += 1
        self.box_auto = QCheckBox("게임 화면 영역을 자동으로 찾는다(레터박스·창 테두리 잘라내기)")
        self.box_auto.setChecked(True)
        self.box_auto.toggled.connect(self._sync_box_enabled)
        grid.addWidget(self.box_auto, row, 0, 1, 4)
        row += 1
        self.box_manual = QCheckBox("게임 화면 영역을 직접 지정한다 (프레임 대비 비율 0~1)")
        self.box_manual.toggled.connect(self._sync_box_enabled)
        grid.addWidget(self.box_manual, row, 0, 1, 4)
        row += 1
        self.box_spins: list[QDoubleSpinBox] = []
        for i, name in enumerate(("좌 x1", "상 y1", "우 x2", "하 y2")):
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 1.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.01)
            spin.setPrefix(f"{name} ")
            spin.setEnabled(False)
            self.box_spins.append(spin)
            grid.addWidget(spin, row, i)
        row += 1
        self.use_detected_box = QPushButton("감지한 영역 넣기")
        self.use_detected_box.clicked.connect(self._fill_detected_box)
        grid.addWidget(self.use_detected_box, row, 0, 1, 2)
        inner.addWidget(box)

        # --- TypeSafe API 키 ---
        # 키는 이 칸에서만 들어오고, 저장되는 곳은 OS 키체인(폴백: 홈의 0600 파일)이다.
        # settings.toml 에는 절대 쓰지 않는다 — 이 저장소는 공개다(CLAUDE.md 고정 제약).
        box = QGroupBox(core.KEY_GROUP_TITLE)
        lay = QVBoxLayout(box)
        self.key_help = QLabel(core.KEY_FRIEND_NOTE)
        self.key_help.setWordWrap(True)
        self.key_help.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(self.key_help)
        self.key_status = QLabel("")
        self.key_status.setTextFormat(Qt.TextFormat.RichText)
        self.key_status.setWordWrap(True)
        lay.addWidget(self.key_status)
        line = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)   # 어깨너머로 보이지 않게
        self.key_edit.setPlaceholderText(KEY_PLACEHOLDER)
        self.key_edit.textChanged.connect(self._sync_key_buttons)
        line.addWidget(self.key_edit, 1)
        self.key_show = QPushButton("표시")
        self.key_show.setCheckable(True)
        self.key_show.setToolTip("지금 입력한 글자를 눈으로 확인한다(저장한 키는 다시 보여 주지 않는다).")
        self.key_show.toggled.connect(self._toggle_key_echo)
        line.addWidget(self.key_show)
        lay.addLayout(line)
        line = QHBoxLayout()
        self.key_test_btn = QPushButton("연결 테스트")
        self.key_test_btn.setToolTip(KEY_TEST_TOOLTIP)
        self.key_test_btn.clicked.connect(self.test_key)
        line.addWidget(self.key_test_btn)
        self.key_save_btn = QPushButton("저장")
        self.key_save_btn.setToolTip("키를 이 컴퓨터의 OS 키체인에 넣는다. 저장 뒤 입력칸은 비워진다.")
        self.key_save_btn.clicked.connect(self.save_key)
        line.addWidget(self.key_save_btn)
        self.key_delete_btn = QPushButton("삭제")
        self.key_delete_btn.setToolTip("이 컴퓨터에 저장된 키를 지운다(환경변수는 건드리지 않는다).")
        self.key_delete_btn.clicked.connect(self.delete_key)
        line.addWidget(self.key_delete_btn)
        line.addStretch(1)
        lay.addLayout(line)
        self.key_result = QLabel("")
        self.key_result.setTextFormat(Qt.TextFormat.RichText)
        self.key_result.setWordWrap(True)
        lay.addWidget(self.key_result)
        inner.addWidget(box)

        # --- 그 밖 ---
        box = QGroupBox("추천·오버레이")
        grid = QGridLayout(box)
        # Jev는 체크박스다(사용자 요청). 켬 = live, 끔 = mock. "off"는 아래 고급 체크로만 고른다 —
        # 평소에 쓸 일이 없는 선택지가 기본 화면을 차지하지 않게 한다.
        self.jev_live = QCheckBox(JEV_LIVE_LABEL)
        self.jev_live.toggled.connect(self._sync_jev)
        grid.addWidget(self.jev_live, 0, 0, 1, 4)
        self.jev_note = QLabel("")
        self.jev_note.setWordWrap(True)
        self.jev_note.setStyleSheet(f"color: {MUTED};")
        grid.addWidget(self.jev_note, 1, 0, 1, 4)
        self.jev_off = QCheckBox(JEV_OFF_LABEL)
        self.jev_off.toggled.connect(self._sync_jev)
        grid.addWidget(self.jev_off, 2, 0, 1, 4)
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.2, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setDecimals(2)
        grid.addWidget(QLabel("오버레이 불투명도"), 3, 0)
        grid.addWidget(self.opacity_spin, 3, 1)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.5, 3.0)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.setDecimals(2)
        grid.addWidget(QLabel("오버레이 글꼴 배율"), 3, 2)
        grid.addWidget(self.scale_spin, 3, 3)
        inner.addWidget(box)

        # --- 테스트 캡처 ---
        box = QGroupBox("테스트 캡처 — 인식 위치가 맞는지 눈으로 확인")
        lay = QVBoxLayout(box)
        line = QHBoxLayout()
        self.test_btn = QPushButton("📷  테스트 캡처")
        self.test_btn.setMinimumHeight(34)
        self.test_btn.clicked.connect(self.test_capture)
        line.addWidget(self.test_btn)
        self.test_status = QLabel("게임이 준비 단계일 때 누르면 가장 정확하다.")
        self.test_status.setWordWrap(True)
        line.addWidget(self.test_status, 1)
        lay.addLayout(line)
        self.preview = QLabel("")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(80)
        lay.addWidget(self.preview)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["항목", "인식 결과", "신뢰도"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(240)
        lay.addWidget(self.table)
        inner.addWidget(box)
        inner.addStretch(1)

        # --- 버튼 ---
        buttons = QDialogButtonBox()
        self.start_btn = buttons.addButton("저장 후 시작", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_btn = buttons.addButton("저장만", QDialogButtonBox.ButtonRole.ApplyRole)
        self.cancel_btn = buttons.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        self.start_btn.clicked.connect(lambda: self.save_and_close("start"))
        self.save_btn.clicked.connect(lambda: self.save_and_close("saved"))
        self.cancel_btn.clicked.connect(self.reject)
        outer.addWidget(buttons)

        self._load_settings_into_widgets()

    def _load_settings_into_widgets(self) -> None:
        s = self.settings
        _select(self.aspect_combo, s.vision.aspect)
        _select(self.profile_combo, s.vision.profile, add_label=s.vision.profile)
        _select(self.res_combo, s.vision.resolution, add_label=s.vision.resolution)
        live, off = core.jev_checks(s.advisor.jev_backend)
        self.jev_live.setChecked(live)
        self.jev_off.setChecked(off)
        self.refresh_key_row()     # 키 상태 → 안내 + Jev 체크 활성화(_sync_jev를 부른다)
        self.opacity_spin.setValue(s.overlay_opacity())
        self.scale_spin.setValue(s.overlay.scale)
        self.box_auto.setChecked(s.vision.content_box_auto)
        if s.vision.content_box is not None:
            self.box_manual.setChecked(True)
            for spin, v in zip(self.box_spins, s.vision.content_box):
                spin.setValue(v)
        self._sync_box_enabled()

    def _sync_box_enabled(self) -> None:
        for spin in self.box_spins:
            spin.setEnabled(self.box_manual.isChecked())
        self.use_detected_box.setEnabled(self.box_manual.isChecked())

    def _sync_jev(self, *_: object) -> None:
        """체크 상태 → 안내문과 사용 가능 여부.

        `TYPESAFE_API_KEY`가 없으면 live를 **켤 수 없다**(이미 켜져 있으면 끄는 것은 언제나 된다 —
        설정에 live가 적혀 있는데 키만 빠진 경우 그 값을 말없이 바꾸지 않는다).
        """
        off = self.jev_off.isChecked()
        key = self.creds.key_present()
        self.jev_live.setEnabled(not off and (key or self.jev_live.isChecked()))
        if off:
            note = JEV_OFF_NOTE
        elif self.jev_live.isChecked():
            note = JEV_LIVE_NOTE
        else:
            note = core.JEV_MOCK_NOTE
        if not key:
            note += "  " + core.JEV_NO_KEY_NOTE
        self.jev_note.setText(note)

    # ------------------------------------------------------------------ API 키
    def _toggle_key_echo(self, shown: bool) -> None:
        """[표시]/[숨기기] — 지금 **입력 중인** 글자에만 해당한다. 저장된 키는 어느 쪽이든 보이지 않는다."""
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        self.key_show.setText("숨기기" if shown else "표시")

    def _sync_key_buttons(self, *_: object) -> None:
        typed = bool(self.key_edit.text().strip())
        self.key_save_btn.setEnabled(typed)
        self.key_test_btn.setEnabled(typed or self._key_info.present)
        self.key_delete_btn.setEnabled(bool(self.creds.stored_key_present()))

    def _set_key_result(self, text: str, color: str) -> None:
        self.key_result.setText(f"<span style='color: {color};'>{_esc(text)}</span>")

    def refresh_key_row(self) -> None:
        """키 보관소를 다시 읽어 상태 줄·버튼·Jev 체크박스를 맞춘다. **키 값은 읽어서 보여 주지 않는다.**"""
        info = self.creds.key_info()
        self._key_info = info
        color = OK if info.present else MUTED
        text = _esc(info.describe())
        if info.source == "env":
            color = WARN                      # 환경변수가 이긴다는 사실을 숨기지 않는다
            text += "<br>" + core.KEY_ENV_NOTE
        self.key_status.setText(f"<span style='color: {color};'>{text}</span>")
        self._sync_key_buttons()
        self._sync_jev()

    def save_key(self) -> object:
        """[저장] — 입력칸의 키를 OS 키체인(폴백: 홈의 0600 파일)에 넣는다. 어느 곳에 넣었는지 말해 준다."""
        result = self.creds.save_api_key(self.key_edit.text())
        if result.ok:
            self.key_edit.clear()             # 저장한 뒤로는 화면에 두지 않는다
            self.key_show.setChecked(False)
            message, color = result.message, OK
            if result.shadowed_by_env:
                message += f"  다만 지금은 환경변수 {core.JEV_KEY_ENV} 가 먼저 쓰인다."
                color = WARN
        else:
            message, color = result.message, BAD
        self._set_key_result(message, color)
        self.refresh_key_row()
        return result

    def delete_key(self) -> object:
        """[삭제] — 이 컴퓨터에 저장한 키를 지운다(환경변수는 셸/OS의 몫이라 건드리지 않는다)."""
        result = self.creds.delete_api_key()
        self._set_key_result(result.message, OK if result.ok else MUTED)
        self.refresh_key_row()
        return result

    def test_key(self) -> object:
        """[연결 테스트] — 입력칸의 키(비어 있으면 지금 저장된 키)로 **Jev를 한 번** 부른다."""
        self._set_key_result(KEY_TESTING, MUTED)
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        self.key_test_btn.setEnabled(False)
        try:
            result = self.creds.verify_key(self.key_edit.text().strip() or None, caller=self.key_verifier)
        finally:
            self._sync_key_buttons()
        self._set_key_result(result.message, OK if result.ok else BAD)
        return result

    # ------------------------------------------------------------------ 자동 감지
    def auto_detect(self, ocr: bool = True) -> core.SetupDetection:
        """[자동 감지] — 모니터를 모두 한 장씩 찍어 게임 화면을 찾고 위젯을 채운다.

        `ocr=True`면 인식기의 스테이지 글자 채점으로 **어느 모니터에 TFT가 떠 있는지 확인**한다(정확, 1~3초).
        `ocr=False`는 픽셀 채점만 하는 예비 감지다(즉시, 창을 처음 열 때).
        """
        self._busy("화면을 찾는 중…" if ocr else None)
        try:
            scorer = None
            if ocr:
                try:
                    scorer = self._get_recognizer(self.settings).screen_score
                except Exception:   # noqa: BLE001 — 인식기를 못 만들면 픽셀 채점으로 내려간다
                    log.warning("인식기를 만들지 못했다 → 픽셀 채점으로 감지한다", exc_info=True)
            det = core.safe_detect(lambda: core.detect_live(self.settings, scorer=scorer,
                                                            grabber=self.grabber))
        finally:
            self._busy(None)
        self.detection = det
        self._fill_from_detection(det)
        return det

    def _fill_from_detection(self, det: core.SetupDetection) -> None:
        self.monitor_combo.clear()
        self.monitor_combo.addItem("자동(실행할 때마다 TFT 화면을 찾는다)", "auto")
        confirmed = det.scorer_kind == "ocr"
        for i, probe in enumerate(det.probes):
            if probe.black:
                note = "검은 화면"
            elif confirmed and probe.score >= core.GAME_FOUND_SCORE:
                note = "TFT 화면 확인"
            else:
                note = f"점수 {probe.score:.2f}"
            self.monitor_combo.addItem(f"{probe.info.label()} — {note}", probe.info.number)
            if i == det.chosen:
                self.monitor_combo.setCurrentIndex(self.monitor_combo.count() - 1)

        choice = core.choice_from_detection(det, self.settings)
        if det.game_size[0]:
            label = f"{det.game_size[0]}x{det.game_size[1]} (감지값)"
            _select(self.res_combo, choice.resolution, add_label=label)
        _select(self.aspect_combo, choice.aspect)
        self.box_auto.setChecked(choice.content_box_auto)
        if det.content_box is not None:
            self._fill_detected_box()
        self._sync_box_enabled()

        self.summary.setText("<br>".join(_esc(line) for line in det.summary_lines()
                                         + ["· " + n for n in det.notes]))
        if det.warnings:
            color = BAD if det.permission_issue else WARN
            self.warn_label.setText(f"<span style='color:{color}'>"
                                    + "<br>".join("⚠ " + _esc(w) for w in det.warnings) + "</span>")
        self.warn_label.setVisible(bool(det.warnings))
        self.perm_box.setVisible(det.permission_issue)
        found = det.game_found and not det.permission_issue
        if found:
            color, text = OK, "TFT 화면을 찾았다."
        elif det.scorer_kind == "ocr":
            color, text = WARN, "TFT 화면을 확인하지 못했다 — 값은 추정이다."
        else:
            color, text = ACCENT, "화면 정보를 읽었다. [자동 감지]를 누르면 TFT 화면인지 확인한다."
        self.status.setText(f"<span style='color:{color}'>{text}</span>")

    def _fill_detected_box(self) -> None:
        det = self.detection
        if det is None or det.content_box is None:
            return
        self.box_manual.setChecked(True)
        for spin, v in zip(self.box_spins, det.content_box):
            spin.setValue(v)
        self._sync_box_enabled()

    # ------------------------------------------------------------------ 현재 선택값
    def current_choice(self) -> core.SetupChoice:
        """위젯 → `SetupChoice`."""
        box = None
        if self.box_manual.isChecked():
            x1, y1, x2, y2 = (s.value() for s in self.box_spins)
            if x1 < x2 and y1 < y2:
                box = (round(x1, 6), round(y1, 6), round(x2, 6), round(y2, 6))
        return core.SetupChoice(
            monitor=_data(self.monitor_combo),   # 0 = "모든 모니터를 합친 화면"이라 `or`로 처리하면 안 된다
            aspect=_data(self.aspect_combo),
            resolution=_combo_value(self.res_combo),
            profile=_data(self.profile_combo),
            content_box=box,
            content_box_auto=self.box_auto.isChecked(),
            jev_backend=core.jev_backend_from_checks(self.jev_live.isChecked(), self.jev_off.isChecked()),
            overlay_opacity=round(self.opacity_spin.value(), 3),
            overlay_scale=round(self.scale_spin.value(), 3),
        )

    def preview_settings(self) -> Settings:
        """지금 위젯 값을 얹은 Settings(검증 포함). 잘못된 조합이면 예외를 낸다."""
        return self.current_choice().apply(self.settings)

    # ------------------------------------------------------------------ 테스트 캡처
    def _get_recognizer(self, settings: Settings):
        from ..vision.recognizer import Recognizer

        if self._recognizer is None:
            self._recognizer = Recognizer(cfg=settings.vision)
        else:   # 설정만 갈아 끼운다(OCR 모델·템플릿을 다시 싣지 않는다)
            self._recognizer.cfg = settings.vision
            self._recognizer.profile_setting = settings.vision.aspect_setting()
        return self._recognizer

    def test_capture(self) -> core.TestCapture:
        """[테스트 캡처] — 지금 선택값으로 한 장 찍어 인식하고, ROI를 그린 미리보기와 결과표를 보여 준다."""
        self._busy("캡처하고 인식하는 중… (처음에는 몇 초 걸린다)")
        result = core.TestCapture()
        try:
            settings = self.preview_settings()
            image = self._grab_current(settings)
            result = core.run_test_capture(image, self._get_recognizer(settings))
        except Exception as e:   # noqa: BLE001
            log.warning("테스트 캡처 실패", exc_info=True)
            result.message = f"테스트 캡처 실패: {type(e).__name__}: {e}"
        finally:
            self._busy(None)
        self._show_test(result)
        return result

    def _grab_current(self, settings: Settings):
        """지금 고른 모니터를 한 장 찍는다("자동"이면 감지에서 고른 모니터)."""
        number = self.monitor_combo.currentData()
        monitors = self.grabber.monitors()
        info = next((m for m in monitors if m.number == number), None)
        if info is None:
            det = self.detection
            info = det.monitor if det is not None and det.monitor is not None else (monitors[0] if monitors else None)
        if info is None:
            raise RuntimeError("모니터를 찾지 못했다")
        return self.grabber.grab(info)

    def _show_test(self, result: core.TestCapture) -> None:
        color = OK if result.ok and "읽지 못했다" not in result.message else WARN
        self.test_status.setText(f"<span style='color:{color if result.ok else BAD}'>"
                                 f"{_esc(result.message)}</span>")
        self.table.setRowCount(len(result.rows))
        for r, (label, value, conf) in enumerate(result.rows):
            self.table.setItem(r, 0, QTableWidgetItem(label))
            self.table.setItem(r, 1, QTableWidgetItem(value))
            self.table.setItem(r, 2, QTableWidgetItem("—" if value == "—" else f"{conf:.2f}"))
        if result.image is not None:
            pix = to_pixmap(result.image)
            self.preview.setPixmap(pix.scaledToWidth(min(PREVIEW_W, pix.width()),
                                                     Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setText("미리보기 없음")

    # ------------------------------------------------------------------ 저장
    def save_and_close(self, action: str) -> core.SetupOutcome:
        """[저장 후 시작] / [저장만] — settings.toml에 쓰고 셋업 완료를 기록한다."""
        try:
            choice = self.current_choice()
            choice.apply(self.settings)       # 검증 먼저(잘못된 조합이면 저장하지 않는다)
            self.outcome = core.commit(choice, settings=self.settings, config_dir=self.config_dir,
                                       det=self.detection, action=action, state_dir=self.state_dir)
        except Exception as e:   # noqa: BLE001
            log.warning("설정 저장 실패", exc_info=True)
            self.warn_label.setText(f"<span style='color:{BAD}'>⚠ 저장하지 못했다: {_esc(str(e))}</span>")
            self.warn_label.setVisible(True)
            return self.outcome
        self.accept()
        return self.outcome

    def reject(self) -> None:
        self.outcome = core.SetupOutcome(action="cancelled", settings=self.settings)
        super().reject()

    def closeEvent(self, event) -> None:   # noqa: N802 — Qt 이름
        self.grabber.close()
        super().closeEvent(event)

    # ------------------------------------------------------------------ 보조
    def _busy(self, message: str | None) -> None:
        """긴 작업 동안 버튼을 잠그고 상태를 보여 준다(감지 0.1~1s, 테스트 캡처 1~3s)."""
        for w in (self.detect_btn, self.test_btn, self.start_btn, self.save_btn):
            w.setEnabled(message is None)
        if message is not None:
            self.status.setText(_esc(message))
        app = QApplication.instance()
        if app is not None:
            app.processEvents()


def _esc(text: str) -> str:
    import html

    return html.escape(text).replace("\n", "<br>")


def _select(combo: QComboBox, value: str, *, add_label: str | None = None) -> None:
    """콤보에서 데이터가 `value`인 항목을 고른다. 없으면(옛 설정값 등) 항목을 만들어 고른다."""
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return
    if value in (None, ""):
        return
    combo.addItem(add_label or value, value)
    combo.setCurrentIndex(combo.count() - 1)


def _data(combo: QComboBox, default: object = "auto") -> object:
    """고른 항목의 데이터. 없으면 기본값 — `or`를 쓰면 모니터 0번("전체 화면")이 사라진다."""
    value = combo.currentData()
    return default if value is None else value


def _combo_value(combo: QComboBox) -> str:
    """편집 가능한 콤보의 값 — 고른 항목의 데이터, 직접 친 글자면 그 글자."""
    text = combo.currentText().strip()
    data = combo.currentData()
    if data is not None and combo.itemText(combo.currentIndex()).strip() == text:
        return str(data)
    return text or "auto"


def run_setup_dialog(settings: Settings | None = None, *, config_dir: Path | None = None,
                     state_dir: Path | None = None) -> core.SetupOutcome:
    """대화상자를 모달로 띄우고 결과를 돌려준다. QApplication이 없으면 만든다(오버레이와 공유)."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    dialog = SetupDialog(settings, config_dir=config_dir, state_dir=state_dir)
    dialog.show()
    app.processEvents()      # 창을 먼저 보여 주고
    dialog.start()           # 인식기(OCR)로 확인 감지 — 1~3초
    dialog.exec()
    dialog.grabber.close()
    return dialog.outcome


__all__ = ["SetupDialog", "run_setup_dialog", "to_pixmap"]
