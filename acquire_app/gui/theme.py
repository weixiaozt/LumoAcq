"""深色 Fluent 风主题：色板 + QSS。"""

# --- 色板 ---
BG = "#1a1b1e"
CARD = "#26282c"
CARD_HOVER = "#2b2e33"
INPUT = "#2f3237"
INPUT_HOVER = "#353941"
BORDER = "#3a3d42"
BORDER_STRONG = "#4a4e55"
TEXT = "#e4e4e7"
TEXT_MUTED = "#9ca3af"
TEXT_DIM = "#6b7280"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
ACCENT_PRESSED = "#1d4ed8"
SUCCESS = "#10b981"
WARNING = "#f59e0b"
DANGER = "#ef4444"

FONT_UI = "'Segoe UI', 'Microsoft YaHei UI', sans-serif"
FONT_MONO = "'Cascadia Mono', 'Consolas', monospace"


QSS = f"""
/* ========== 基础 ========== */
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: {FONT_UI};
    font-size: 9pt;
}}

QMainWindow, QDialog {{
    background-color: {BG};
}}

/* ========== Card 卡片 ========== */
QFrame#Card {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}

QLabel#CardTitle {{
    color: {TEXT};
    font-size: 10pt;
    font-weight: 600;
    padding: 0 0 4px 0;
    background: transparent;
}}

QLabel#CardSubtitle {{
    color: {TEXT_MUTED};
    font-size: 8.5pt;
    background: transparent;
}}

QFrame#CardSeparator {{
    background-color: {BORDER};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* ========== 标签 ========== */
QLabel {{
    background: transparent;
    color: {TEXT};
}}

QLabel[muted="true"] {{
    color: {TEXT_MUTED};
}}

QLabel[mono="true"] {{
    font-family: {FONT_MONO};
}}

/* ========== 输入控件 ========== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 20px;
    selection-background-color: {ACCENT};
}}

QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover,
QPlainTextEdit:hover, QTextEdit:hover {{
    border-color: {BORDER_STRONG};
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
}}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QPlainTextEdit:disabled, QTextEdit:disabled {{
    color: {TEXT_DIM};
    background-color: {CARD};
}}

QComboBox::drop-down {{
    border: none;
    width: 18px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_MUTED};
    margin-right: 6px;
}}

QComboBox QAbstractItemView {{
    background-color: {INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: {ACCENT};
    outline: none;
    padding: 2px;
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 14px;
}}

/* ========== 按钮 ========== */
QPushButton {{
    background-color: {INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 14px;
    min-height: 18px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {INPUT_HOVER};
    border-color: {BORDER_STRONG};
}}

QPushButton:pressed {{
    background-color: {CARD};
}}

QPushButton:disabled {{
    color: {TEXT_DIM};
    background-color: {CARD};
    border-color: {BORDER};
}}

QPushButton[kind="primary"] {{
    background-color: {ACCENT};
    color: white;
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton[kind="primary"]:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton[kind="primary"]:pressed {{
    background-color: {ACCENT_PRESSED};
}}

QPushButton[kind="danger"] {{
    color: {DANGER};
    border-color: {BORDER};
}}
QPushButton[kind="danger"]:hover {{
    background-color: rgba(239, 68, 68, 0.12);
    border-color: {DANGER};
}}

/* ========== 分组/表单 ========== */
QGroupBox {{
    background: transparent;
    border: none;
}}

/* ========== 复选 / 单选 ========== */
QCheckBox, QRadioButton {{
    background: transparent;
    spacing: 6px;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 3px;
    background-color: {INPUT};
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {ACCENT};
}}

QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

QRadioButton::indicator {{
    border-radius: 7px;
}}

QRadioButton::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

/* ========== 状态栏 ========== */
QStatusBar {{
    background-color: {CARD};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
    padding: 2px 8px;
}}

QStatusBar::item {{
    border: none;
}}

QStatusBar QLabel {{
    background: transparent;
    padding: 0 6px;
}}

/* ========== 分隔条 ========== */
QSplitter::handle {{
    background-color: transparent;
}}

QSplitter::handle:horizontal {{
    width: 4px;
}}

QSplitter::handle:vertical {{
    height: 4px;
}}

QSplitter::handle:hover {{
    background-color: {BORDER};
}}

/* ========== 滚动条 ========== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    min-height: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background: {BORDER_STRONG};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    min-width: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {BORDER_STRONG};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ========== 菜单 ========== */
QMenuBar {{
    background-color: {BG};
    color: {TEXT};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item:selected {{
    background-color: {CARD};
}}
QMenu {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 20px;
    border-radius: 3px;
}}
QMenu::item:selected {{
    background-color: {ACCENT};
    color: white;
}}

/* ========== 工具提示 ========== */
QToolTip {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 6px;
}}
"""
