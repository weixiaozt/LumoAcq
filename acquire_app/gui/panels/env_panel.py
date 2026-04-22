from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPlainTextEdit,
    QHBoxLayout,
    QPushButton,
)

from acquire_app.core.metadata import EnvironmentInfo
from acquire_app.gui.widgets.card import Card


class EnvPanel(Card):
    """环境信息面板：采集时自动附加到元数据。字段均可选，值保留到手动清空或程序退出。"""

    def __init__(self, parent=None):
        super().__init__("环境信息", subtitle="M9 · 附加到元数据", parent=parent)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(-40.0, 80.0)
        self._temperature.setDecimals(1)
        self._temperature.setSuffix(" °C")
        self._temperature.setSpecialValueText("—")
        self._temperature.setValue(self._temperature.minimum())

        self._humidity = QSpinBox()
        self._humidity.setRange(0, 100)
        self._humidity.setSuffix(" %")
        self._humidity.setSpecialValueText("—")
        self._humidity.setValue(self._humidity.minimum())

        self._irradiance = QSpinBox()
        self._irradiance.setRange(0, 2000)
        self._irradiance.setSuffix(" W/m²")
        self._irradiance.setSpecialValueText("—")
        self._irradiance.setValue(self._irradiance.minimum())

        self._weather = QComboBox()
        self._weather.setEditable(True)
        self._weather.addItems(["", "晴", "多云", "阴", "雨"])
        self._weather.lineEdit().setPlaceholderText("晴 / 多云 / 阴 / 雨 / 自定义")

        self._note = QPlainTextEdit()
        self._note.setPlaceholderText("备注（可选）")
        self._note.setFixedHeight(72)

        form.addRow(QLabel("环境温度"), self._temperature)
        form.addRow(QLabel("相对湿度"), self._humidity)
        form.addRow(QLabel("辐照度"), self._irradiance)
        form.addRow(QLabel("天气"), self._weather)
        form.addRow(QLabel("备注"), self._note)

        self.add_layout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._clear_btn = QPushButton("清空")
        btn_row.addWidget(self._clear_btn)
        self.add_layout(btn_row)

        self._clear_btn.clicked.connect(self._clear_all)

    def _clear_all(self) -> None:
        self._temperature.setValue(self._temperature.minimum())
        self._humidity.setValue(self._humidity.minimum())
        self._irradiance.setValue(self._irradiance.minimum())
        self._weather.setCurrentIndex(0)
        self._weather.setEditText("")
        self._note.clear()

    def get_environment(self) -> EnvironmentInfo:
        """导出为 EnvironmentInfo (specialValueText 代表未填 → None / 空串)。"""
        env = EnvironmentInfo()
        if self._temperature.value() > self._temperature.minimum():
            env.temperature_c = float(self._temperature.value())
        if self._humidity.value() > self._humidity.minimum():
            env.humidity_pct = int(self._humidity.value())
        if self._irradiance.value() > self._irradiance.minimum():
            env.irradiance_wm2 = int(self._irradiance.value())
        env.weather = self._weather.currentText().strip()
        env.note = self._note.toPlainText().strip()
        return env
