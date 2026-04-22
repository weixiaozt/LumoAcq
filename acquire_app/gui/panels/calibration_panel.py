"""校准面板: 拍暗场 / 拍平场 + 启用复选框。"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
)

from acquire_app.core.calibration import load_dark, load_flat
from acquire_app.gui.widgets.card import Card


class CalibrationPanel(Card):
    """暗场 / 平场参考帧的采集和启用。"""

    capture_dark_requested = Signal(int)   # n_frames
    capture_flat_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__("校准", subtitle="M11 · 暗场/平场", parent=parent)

        # N 选择
        n_row = QHBoxLayout()
        n_row.setSpacing(6)
        n_row.addWidget(QLabel("N 帧平均"))
        self._n_combo = QComboBox()
        self._n_combo.addItems(["8", "16", "32", "64", "128"])
        self._n_combo.setCurrentText("16")
        self._n_combo.setEditable(False)
        self._n_combo.setFixedWidth(80)
        n_row.addWidget(self._n_combo)
        n_row.addStretch()
        self.add_layout(n_row)

        # 暗场行
        self._dark_btn = QPushButton("拍暗场")
        self._dark_btn.setEnabled(False)
        self._dark_btn.setToolTip("盖好镜头, 连续拍 N 帧平均存为暗场参考")
        self._dark_btn.clicked.connect(self._on_dark_clicked)
        self.add_widget(self._dark_btn)
        self._dark_status = QLabel("暗场: —")
        self._dark_status.setProperty("mono", True)
        self._dark_status.setProperty("muted", True)
        self.add_widget(self._dark_status)

        # 平场行
        self._flat_btn = QPushButton("拍平场")
        self._flat_btn.setEnabled(False)
        self._flat_btn.setToolTip("对准均匀白目标 (白墙/漫射板), 连续拍 N 帧平均存为平场参考")
        self._flat_btn.clicked.connect(self._on_flat_clicked)
        self.add_widget(self._flat_btn)
        self._flat_status = QLabel("平场: —")
        self._flat_status.setProperty("mono", True)
        self._flat_status.setProperty("muted", True)
        self.add_widget(self._flat_status)

        # 启用开关
        self._chk_apply_dark = QCheckBox("采集时扣暗场")
        self._chk_apply_dark.setEnabled(False)
        self.add_widget(self._chk_apply_dark)

        self._chk_apply_flat = QCheckBox("采集时除平场")
        self._chk_apply_flat.setEnabled(False)
        self.add_widget(self._chk_apply_flat)

        self._connected = False
        self._busy = False
        self.refresh_status()

    # ── 对外 ──

    def set_camera_connected(self, connected: bool) -> None:
        self._connected = connected
        self._update_buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def get_n_frames(self) -> int:
        try:
            return int(self._n_combo.currentText())
        except ValueError:
            return 16

    def apply_dark(self) -> bool:
        return self._chk_apply_dark.isChecked() and self._chk_apply_dark.isEnabled()

    def apply_flat(self) -> bool:
        return self._chk_apply_flat.isChecked() and self._chk_apply_flat.isEnabled()

    def refresh_status(self) -> None:
        """重新读磁盘上的参考帧状态, 更新文字。"""
        _, dm = load_dark()
        if dm is None:
            self._dark_status.setText("暗场: —")
            self._chk_apply_dark.setEnabled(False)
            self._chk_apply_dark.setChecked(False)
        else:
            self._dark_status.setText(
                f"暗场: ✓ exp={dm.exposure_us:.0f}us  gain={dm.gain_db:.1f}dB  "
                f"N={dm.n_frames}  {dm.captured_at[:10]}"
            )
            self._chk_apply_dark.setEnabled(True)

        _, fm = load_flat()
        if fm is None:
            self._flat_status.setText("平场: —")
            self._chk_apply_flat.setEnabled(False)
            self._chk_apply_flat.setChecked(False)
        else:
            self._flat_status.setText(
                f"平场: ✓ exp={fm.exposure_us:.0f}us  gain={fm.gain_db:.1f}dB  "
                f"N={fm.n_frames}  {fm.captured_at[:10]}"
            )
            self._chk_apply_flat.setEnabled(True)

    # ── 内部 ──

    def _on_dark_clicked(self) -> None:
        self.capture_dark_requested.emit(self.get_n_frames())

    def _on_flat_clicked(self) -> None:
        self.capture_flat_requested.emit(self.get_n_frames())

    def _update_buttons(self) -> None:
        can = self._connected and not self._busy
        self._dark_btn.setEnabled(can)
        self._flat_btn.setEnabled(can)
        self._n_combo.setEnabled(not self._busy)
