from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QComboBox, QPushButton

from acquire_app.gui.widgets.card import Card
from acquire_app.gui.widgets.status_dot import StatusDot


@dataclass
class DeviceEntry:
    role: str            # "daheng" | "dummy"
    index: int
    display: str         # 下拉显示文案


class ConnectionPanel(Card):
    """相机连接面板。

    仅负责 UI + 发信号, 不持有相机实例 (由 MainWindow 管理)。
    """

    scan_requested = Signal()
    connect_requested = Signal(str, int)   # role, index
    disconnect_requested = Signal()

    _STATE_IDLE = "idle"         # 未连接, 未扫描
    _STATE_READY = "ready"       # 已扫描, 可连接
    _STATE_CONNECTING = "busy"   # 正在连接
    _STATE_CONNECTED = "ok"      # 已连接
    _STATE_ERROR = "error"       # 错误

    def __init__(self, parent=None):
        super().__init__("相机连接", subtitle="M5", parent=parent)

        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_row.addWidget(QLabel("设备"))
        self._device_combo = QComboBox()
        self._device_combo.addItem("(未扫描)")
        self._device_combo.setEnabled(False)
        device_row.addWidget(self._device_combo, 1)
        self.add_layout(device_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._scan_btn = QPushButton("扫描")
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setProperty("kind", "primary")
        self._connect_btn.setEnabled(False)
        btn_row.addWidget(self._scan_btn)
        btn_row.addWidget(self._connect_btn, 1)
        self.add_layout(btn_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._dot = StatusDot("idle")
        self._status_label = QLabel("未扫描")
        self._status_label.setProperty("muted", True)
        status_row.addWidget(self._dot)
        status_row.addWidget(self._status_label, 1)
        self.add_layout(status_row)

        self._connected = False
        self._locked = False
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        self._connect_btn.clicked.connect(self._on_connect_clicked)

    # ── 对外 API (供 MainWindow 调用) ──

    def set_devices(self, entries: list[DeviceEntry]) -> None:
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        if not entries:
            self._device_combo.addItem("(未发现设备)")
            self._device_combo.setEnabled(False)
            self._connect_btn.setEnabled(False)
            self._set_status(self._STATE_ERROR, "未发现任何设备")
        else:
            for e in entries:
                self._device_combo.addItem(e.display, userData=e)
            self._device_combo.setEnabled(True)
            self._connect_btn.setEnabled(True)
            self._set_status(self._STATE_READY, f"发现 {len(entries)} 台, 可连接")
        self._device_combo.blockSignals(False)

    def set_connecting(self, connecting: bool = True) -> None:
        self._connect_btn.setEnabled(not connecting)
        self._scan_btn.setEnabled(not connecting)
        self._device_combo.setEnabled(not connecting)
        if connecting:
            self._set_status(self._STATE_CONNECTING, "连接中…")

    def set_connected(self, info_text: str) -> None:
        self._connected = True
        self._connect_btn.setText("断开")
        self._connect_btn.setEnabled(True)
        self._connect_btn.setProperty("kind", "danger")
        self._device_combo.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._set_status(self._STATE_CONNECTED, info_text)
        self._restyle(self._connect_btn)

    def set_disconnected(self, message: str = "已断开") -> None:
        self._connected = False
        self._connect_btn.setText("连接")
        self._connect_btn.setProperty("kind", "primary")
        self._device_combo.setEnabled(self._device_combo.count() > 0 and self._device_combo.itemData(0) is not None)
        self._scan_btn.setEnabled(True)
        has_devices = self._device_combo.count() > 0 and self._device_combo.itemData(0) is not None
        self._connect_btn.setEnabled(has_devices)
        self._set_status(self._STATE_READY if has_devices else self._STATE_IDLE, message)
        self._restyle(self._connect_btn)

    def set_error(self, message: str) -> None:
        self._set_status(self._STATE_ERROR, message)
        self._connect_btn.setEnabled(self._device_combo.itemData(0) is not None)
        self._scan_btn.setEnabled(True)
        self._device_combo.setEnabled(self._device_combo.count() > 0 and self._device_combo.itemData(0) is not None)

    def set_locked(self, locked: bool) -> None:
        """采集期间锁定: 禁用扫描/连接/断开。"""
        self._locked = bool(locked)
        if locked:
            self._scan_btn.setEnabled(False)
            self._connect_btn.setEnabled(False)
            self._device_combo.setEnabled(False)
        else:
            # 根据当前状态恢复
            if self._connected:
                self._connect_btn.setEnabled(True)
                self._scan_btn.setEnabled(False)
            else:
                has_devices = (
                    self._device_combo.count() > 0
                    and self._device_combo.itemData(0) is not None
                )
                self._connect_btn.setEnabled(has_devices)
                self._scan_btn.setEnabled(True)
                self._device_combo.setEnabled(has_devices)

    # ── 内部 ──

    def _current_entry(self) -> DeviceEntry | None:
        data = self._device_combo.currentData()
        return data if isinstance(data, DeviceEntry) else None

    def _on_scan_clicked(self) -> None:
        self._set_status(self._STATE_CONNECTING, "扫描中…")
        self._scan_btn.setEnabled(False)
        self._connect_btn.setEnabled(False)
        self.scan_requested.emit()

    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.disconnect_requested.emit()
            return
        entry = self._current_entry()
        if entry is None:
            return
        self.connect_requested.emit(entry.role, entry.index)

    def _set_status(self, state: str, text: str) -> None:
        self._dot.set_state(state)
        self._status_label.setText(text)

    @staticmethod
    def _restyle(widget) -> None:
        """属性变了要重刷 QSS 才生效。"""
        widget.style().unpolish(widget)
        widget.style().polish(widget)
