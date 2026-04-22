from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QHBoxLayout, QWidget


class Card(QFrame):
    """卡片容器：标题栏 + 内容区，带圆角边框。用 QSS objectName=Card 命中样式。"""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("CardTitle")
        header.addWidget(self._title_label)
        header.addStretch()

        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("CardSubtitle")
            header.addWidget(sub)

        outer.addLayout(header)

        sep = QFrame()
        sep.setObjectName("CardSeparator")
        sep.setFrameShape(QFrame.NoFrame)
        outer.addWidget(sep)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 4, 0, 0)
        self._body_layout.setSpacing(8)
        outer.addWidget(self._body)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def add_widget(self, widget) -> None:
        self._body_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._body_layout.addLayout(layout)
