from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QWidget,
)

from acquire_app.gui import theme
from acquire_app.config import IMAGES_DIR
from acquire_app.gui.widgets.card import Card
from acquire_app.gui.widgets.last_capture_strip import LastCaptureStrip


class CapturePanel(Card):
    """采集面板: 单帧 / N 帧平均 / 锁相 三个独立按钮, 各自独立最近采集。"""

    single_capture_requested = Signal(dict)
    average_capture_requested = Signal(dict)
    grouped_average_capture_requested = Signal(dict)
    lockin_capture_requested = Signal(dict)
    software_trigger_requested = Signal()
    capture_stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("采集", subtitle="M8", parent=parent)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self._prefix = QLineEdit()
        self._prefix.setPlaceholderText("panel01")
        self._prefix.setText("panel01")

        self._label = QComboBox()
        self._label.setEditable(True)
        self._label.addItems(["OC", "SC", "FW", "RV"])

        self._out_dir = QLineEdit(str(IMAGES_DIR))
        self._out_dir.setReadOnly(True)
        self._out_dir.setToolTip(
            "根目录; 采集时自动分 single / average / lockin 子目录"
        )
        self._browse_btn = QPushButton("…")
        self._browse_btn.setFixedWidth(32)
        self._browse_btn.clicked.connect(self._on_browse_dir)

        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.setSpacing(4)
        dir_row.addWidget(self._out_dir, 1)
        dir_row.addWidget(self._browse_btn)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)

        form.addRow(QLabel("前缀"), self._prefix)
        form.addRow(QLabel("标签"), self._label)
        form.addRow(QLabel("根目录"), dir_widget)

        self.add_layout(form)

        # ── 单帧 ──
        self._single_btn = QPushButton("拍单帧")
        self._single_btn.setProperty("kind", "primary")
        self._single_btn.setEnabled(False)
        self._single_btn.clicked.connect(self._on_single_clicked)
        self.add_widget(self._single_btn)

        # 仅软触发模式下可用: 发一次触发, 预览会看到那一帧, 不落盘
        self._soft_trigger_btn = QPushButton("软触发 (预览一帧, 不保存)")
        self._soft_trigger_btn.setEnabled(False)
        self._soft_trigger_btn.setToolTip(
            "仅在「软触发」模式下可用。点一次让相机拍一张, 仅刷新预览, 不落盘"
        )
        self._soft_trigger_btn.clicked.connect(self.software_trigger_requested.emit)
        self.add_widget(self._soft_trigger_btn)

        self._strip_s = LastCaptureStrip("S")
        self.add_widget(self._strip_s)

        # ── N 帧平均 ──
        avg_row = QHBoxLayout()
        avg_row.setContentsMargins(0, 0, 0, 0)
        avg_row.setSpacing(6)
        avg_row.addWidget(QLabel("N"))
        self._n = QComboBox()
        self._n.setEditable(True)
        self._n.addItems(["1", "4", "8", "16", "32", "64", "128", "256"])
        self._n.setCurrentText("16")
        self._n.setFixedWidth(84)
        self._n.lineEdit().setValidator(QIntValidator(1, 1024, self))
        self._n.setToolTip("平均帧数, 预设 1/4/8/16/32/64/128/256; 可手填 1~1024")
        avg_row.addWidget(self._n)
        self._average_btn = QPushButton("拍 N 帧平均")
        self._average_btn.setProperty("kind", "primary")
        self._average_btn.setEnabled(False)
        self._average_btn.clicked.connect(self._on_average_clicked)
        avg_row.addWidget(self._average_btn, 1)
        self.add_layout(avg_row)

        # 分组平均 (EL): 按亮/暗分两张 float32 TIFF, Halcon 侧 sub_image 做差
        self._grouped_avg_btn = QPushButton("拍分组平均 (亮/暗双图)")
        self._grouped_avg_btn.setProperty("kind", "primary")
        self._grouped_avg_btn.setEnabled(False)
        self._grouped_avg_btn.setToolTip(
            "采 N 帧 (N≥4), 自动按帧均值分亮/暗两组并剔除过渡帧, "
            "各自平均成一张 float32 TIFF, 文件名带 _bright / _dark 后缀. "
            "用 Halcon sub_image(bright, dark) 可得锁相差分图."
        )
        self._grouped_avg_btn.clicked.connect(self._on_grouped_average_clicked)
        self.add_widget(self._grouped_avg_btn)

        self._strip_a = LastCaptureStrip("A")
        self.add_widget(self._strip_a)

        # ── 锁相 ──
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {theme.BORDER}; max-height: 1px;")
        self.add_widget(sep)

        lk_label = QLabel("锁相 (Lock-in)")
        lk_label.setObjectName("CardSubtitle")
        self.add_widget(lk_label)

        lk_row1 = QHBoxLayout()
        lk_row1.setContentsMargins(0, 0, 0, 0)
        lk_row1.setSpacing(6)
        lk_row1.addWidget(QLabel("N"))
        self._lk_n = QComboBox()
        self._lk_n.setEditable(True)
        self._lk_n.addItems(["16", "32", "64", "128", "256", "400"])
        self._lk_n.setCurrentText("64")
        self._lk_n.setFixedWidth(84)
        self._lk_n.lineEdit().setValidator(QIntValidator(4, 400, self))
        self._lk_n.setToolTip(
            "锁相原始采集帧数 (4~400). "
            "自由运行模式会自动剔除过渡帧后做差, N 越大过渡帧占比越小. "
            "400 帧 × 10 MB 上限约 4 GB 内存."
        )
        lk_row1.addWidget(self._lk_n)

        lk_row1.addWidget(QLabel("光源"))
        self._lk_freq = QDoubleSpinBox()
        self._lk_freq.setRange(1.0, 100.0)
        self._lk_freq.setDecimals(1)
        self._lk_freq.setSuffix(" Hz")
        self._lk_freq.setValue(5.0)
        self._lk_freq.setFixedWidth(96)
        self._lk_freq.setToolTip(
            "光源频闪频率。建议把相机帧率设为 2 × 本值 以获得最佳锁相效果"
        )
        lk_row1.addWidget(self._lk_freq)
        lk_row1.addStretch()
        self.add_layout(lk_row1)

        self._lockin_btn = QPushButton("拍锁相")
        self._lockin_btn.setProperty("kind", "primary")
        self._lockin_btn.setEnabled(False)
        self._lockin_btn.clicked.connect(self._on_lockin_clicked)
        self.add_widget(self._lockin_btn)
        self._strip_l = LastCaptureStrip("L")
        self.add_widget(self._strip_l)

        # ── 进度 + 停止 ──
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(6)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m")
        self._progress.setFixedHeight(20)
        progress_row.addWidget(self._progress, 1)

        self._stop_btn = QPushButton("停止")
        self._stop_btn.setProperty("kind", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setFixedWidth(72)
        self._stop_btn.clicked.connect(self.capture_stop_requested.emit)
        progress_row.addWidget(self._stop_btn)
        self.add_layout(progress_row)

        self._connected = False
        self._busy = False
        self._trigger_mode = "free"

    # ── 对外 ──

    def set_camera_connected(self, connected: bool) -> None:
        self._connected = connected
        self._update_buttons()

    def set_trigger_mode(self, mode: str) -> None:
        self._trigger_mode = mode
        self._update_buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for w in (
            self._prefix, self._label, self._browse_btn,
            self._n, self._lk_n, self._lk_freq,
        ):
            w.setEnabled(not busy)
        self._update_buttons()

    def set_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self._progress.setRange(0, 1)
            self._progress.setValue(0)
            return
        self._progress.setRange(0, total)
        self._progress.setValue(current)

    def reset_progress(self) -> None:
        self._progress.setRange(0, 100)
        self._progress.setValue(0)

    def set_last_capture(self, mode: str, tiff_path: Path, json_path: Path | None) -> None:
        """mode: 'S' | 'A' | 'L' — 只更新对应模式的最近采集条。"""
        strip = {
            "S": self._strip_s,
            "A": self._strip_a,
            "L": self._strip_l,
        }.get(mode)
        if strip is None:
            return
        strip.set_path(tiff_path, json_path)

    # ── 内部 ──

    def _on_browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择输出根目录", self._out_dir.text()
        )
        if path:
            self._out_dir.setText(path)

    def _clamp_lockin_n(self) -> None:
        """把 N 夹到 [4, 400] 范围. 自由运行模式不强制偶数."""
        text = self._lk_n.currentText().strip()
        if not text:
            return
        try:
            n = int(text)
        except ValueError:
            return
        n = max(4, min(400, n))
        if str(n) != text:
            self._lk_n.setEditText(str(n))

    def _build_payload(self, mode: str, n_frames: int) -> dict | None:
        prefix = self._prefix.text().strip()
        if not prefix:
            return None
        label = self._label.currentText().strip()
        if not label:
            return None
        return {
            "prefix": prefix,
            "label": label,
            "mode": mode,
            "n_frames": n_frames,
            "out_dir": Path(self._out_dir.text()),
        }

    def _on_single_clicked(self) -> None:
        payload = self._build_payload("S", 1)
        if payload:
            self.single_capture_requested.emit(payload)

    def _on_average_clicked(self) -> None:
        try:
            n = int(self._n.currentText().strip())
        except ValueError:
            n = 0
        if n < 1:
            return
        payload = self._build_payload("A", n)
        if payload:
            self.average_capture_requested.emit(payload)

    def _on_grouped_average_clicked(self) -> None:
        """分组平均: 复用 N 帧平均的 N 输入框, 最小 N=4."""
        try:
            n = int(self._n.currentText().strip())
        except ValueError:
            n = 0
        if n < 4:
            return
        payload = self._build_payload("A", n)
        if payload:
            self.grouped_average_capture_requested.emit(payload)

    def _on_lockin_clicked(self) -> None:
        self._clamp_lockin_n()
        try:
            n = int(self._lk_n.currentText().strip())
        except ValueError:
            return
        if n < 4:
            return
        payload = self._build_payload("L", n)
        if payload is None:
            return
        payload["light_freq_hz"] = float(self._lk_freq.value())
        self.lockin_capture_requested.emit(payload)

    def _update_buttons(self) -> None:
        can_capture = self._connected and not self._busy
        self._single_btn.setEnabled(can_capture)
        self._average_btn.setEnabled(can_capture)
        self._grouped_avg_btn.setEnabled(can_capture)
        self._lockin_btn.setEnabled(can_capture)
        self._stop_btn.setEnabled(self._busy)
        self._soft_trigger_btn.setEnabled(
            can_capture and self._trigger_mode == "software"
        )

    # ── 状态持久化 (QSettings) ──

    def get_state(self) -> dict:
        return {
            "prefix": self._prefix.text(),
            "label": self._label.currentText(),
            "out_dir": self._out_dir.text(),
            "n_avg": self._n.currentText(),
            "lk_n": self._lk_n.currentText(),
            "lk_freq": float(self._lk_freq.value()),
        }

    def apply_state(self, s: dict) -> None:
        if not s:
            return
        if "prefix" in s:
            self._prefix.setText(str(s["prefix"]))
        if "label" in s:
            self._label.setCurrentText(str(s["label"]))
        if "out_dir" in s and s["out_dir"]:
            self._out_dir.setText(str(s["out_dir"]))
        if "n_avg" in s:
            self._n.setCurrentText(str(s["n_avg"]))
        if "lk_n" in s:
            self._lk_n.setCurrentText(str(s["lk_n"]))
            self._clamp_lockin_n()
        if "lk_freq" in s:
            try:
                self._lk_freq.setValue(float(s["lk_freq"]))
            except (TypeError, ValueError):
                pass
