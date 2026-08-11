from __future__ import annotations

from PySide6.QtGui import QColor, QPalette


LIGHT_THEME = "light"
DARK_THEME = "dark"
SUPPORTED_THEMES = {LIGHT_THEME, DARK_THEME}


def normalize_theme(value: object) -> str:
    theme = str(value or LIGHT_THEME).strip().casefold()
    return theme if theme in SUPPORTED_THEMES else LIGHT_THEME


def theme_palette(theme: str) -> QPalette:
    """Return a complete palette so native and styled Qt controls agree."""
    dark = normalize_theme(theme) == DARK_THEME
    colors = (
        {
            "window": "#0b1220",
            "window_text": "#e5edf8",
            "base": "#111b2e",
            "alternate_base": "#162238",
            "tooltip_base": "#1d2a40",
            "tooltip_text": "#f8fafc",
            "text": "#e5edf8",
            "button": "#162238",
            "button_text": "#e5edf8",
            "bright_text": "#ffffff",
            "highlight": "#2f7dff",
            "highlighted_text": "#ffffff",
            "link": "#79a8ff",
            "placeholder": "#7f8da3",
            "disabled_text": "#64748b",
            "disabled_base": "#101827",
        }
        if dark
        else {
            "window": "#f8fafc",
            "window_text": "#111827",
            "base": "#ffffff",
            "alternate_base": "#f8fafc",
            "tooltip_base": "#111827",
            "tooltip_text": "#ffffff",
            "text": "#111827",
            "button": "#ffffff",
            "button_text": "#1f2937",
            "bright_text": "#ffffff",
            "highlight": "#1769ff",
            "highlighted_text": "#ffffff",
            "link": "#1769ff",
            "placeholder": "#7c8798",
            "disabled_text": "#98a2b3",
            "disabled_base": "#f1f5f9",
        }
    )
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "window",
        QPalette.ColorRole.WindowText: "window_text",
        QPalette.ColorRole.Base: "base",
        QPalette.ColorRole.AlternateBase: "alternate_base",
        QPalette.ColorRole.ToolTipBase: "tooltip_base",
        QPalette.ColorRole.ToolTipText: "tooltip_text",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "button",
        QPalette.ColorRole.ButtonText: "button_text",
        QPalette.ColorRole.BrightText: "bright_text",
        QPalette.ColorRole.Highlight: "highlight",
        QPalette.ColorRole.HighlightedText: "highlighted_text",
        QPalette.ColorRole.Link: "link",
        QPalette.ColorRole.PlaceholderText: "placeholder",
    }
    for role, key in roles.items():
        palette.setColor(role, QColor(colors[key]))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            role,
            QColor(colors["disabled_text"]),
        )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Base,
        QColor(colors["disabled_base"]),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Button,
        QColor(colors["disabled_base"]),
    )
    return palette


DARK_THEME_STYLESHEET = """
QMainWindow,
QDialog,
QWidget,
QWidget#rootWidget,
QWidget#contentArea,
QWidget#appBody,
QScrollArea,
QScrollArea > QWidget > QWidget,
QStackedWidget {
    background: #0b1220;
    color: #e5edf8;
}
QMenuBar#appMenuBar {
    background: #0f192a;
    border-bottom: 1px solid #26344a;
    color: #cbd5e1;
}
QMenuBar#appMenuBar::item:selected {
    background: #1b2940;
    color: #ffffff;
}
QMenu {
    background: #111b2e;
    border: 1px solid #31415a;
    color: #e5edf8;
}
QMenu::separator {
    background: #31415a;
    height: 1px;
    margin: 5px 8px;
}
QMenu::item:selected {
    background: #19345e;
    color: #8bb4ff;
}
QFrame#sidebar {
    background: #0f192a;
    border-right: 1px solid #26344a;
}
QWidget#sidebarBrand,
QFrame#sidebar QLabel {
    background: transparent;
}
QFrame#sidebar QLabel#engineStatusIcon {
    background: #2f7dff;
}
QLabel#sidebarTitleLabel,
QLabel#pageTitleLabel,
QLabel#titleLabel,
QLabel#sectionLabel,
QLabel#sectionTitle,
QLabel#sidebarStatusTitle,
QLabel#engineInstallHeading {
    color: #f8fafc;
}
QLabel#sidebarSubtitleLabel,
QLabel#subtitleLabel,
QLabel#helperLabel,
QLabel#authorCreditLabel,
QLabel#sidebarStatusText {
    color: #91a0b7;
}
QLabel#authorCreditLabel a {
    color: #79a8ff;
}
QPushButton#navButton {
    background: transparent;
    border-color: transparent;
    color: #cbd5e1;
}
QPushButton#navButton:hover {
    background: #18253a;
}
QPushButton#navButton[active="true"] {
    background: #17325b;
    border-color: #285493;
    color: #79a8ff;
}
QFrame#sidebarStatusCard,
QFrame#card,
QTabWidget::pane {
    background: #111b2e;
    border: 1px solid #2a3951;
}
QFrame#inlineStatusFrame,
QFrame#eventDetailsPanel {
    background: #0e1829;
    border: 1px solid #2a3951;
}
QFrame#whisperMissingFrame {
    background: #362111;
    border: 1px solid #b86b24;
    border-radius: 10px;
}
QFrame#whisperMissingFrame QLabel {
    color: #fdba74;
}
QLabel#engineInstallDestination {
    background: #0e1829;
    border: 1px solid #3a4b65;
    border-radius: 6px;
    color: #d7e1ef;
    padding: 10px;
    font-family: monospace;
}
QLabel#engineInstallSpaceAvailable[spaceAvailable="true"] {
    color: #6ee7b7;
    font-weight: 600;
}
QLabel#engineInstallSpaceAvailable[spaceAvailable="false"] {
    color: #fca5a5;
    font-weight: 700;
}
QLabel#engineInstallSpaceWarning {
    background: #3a171c;
    border: 1px solid #a84a56;
    border-radius: 6px;
    color: #fecaca;
    padding: 10px;
}
QLabel#engineInstallCloseWarning {
    background: #342710;
    border: 1px solid #9a7226;
    border-radius: 6px;
    color: #fde68a;
    padding: 10px;
    font-weight: 600;
}
QTextEdit,
QPlainTextEdit,
QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QDateEdit,
QTimeEdit {
    background: #0e1829;
    border: 1px solid #34445d;
    color: #e5edf8;
    selection-background-color: #2f7dff;
    selection-color: #ffffff;
}
QTextEdit:focus,
QPlainTextEdit:focus,
QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus {
    border: 1px solid #4f8cff;
}
QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled {
    background: #101827;
    border-color: #26344a;
    color: #64748b;
}
QComboBox QAbstractItemView {
    background: #111b2e;
    border: 1px solid #34445d;
    color: #e5edf8;
    selection-background-color: #234e89;
    selection-color: #ffffff;
    outline: none;
}
QPlainTextEdit#timelineTextPanel {
    background: #0a1322;
    color: #91a0b7;
}
QPlainTextEdit#segmentTextPanel {
    background: #0e1829;
    color: #e5edf8;
}
QPushButton {
    background: #162238;
    border: 1px solid #3a4b65;
    color: #dbe6f5;
}
QPushButton:hover {
    background: #1d2d47;
    border-color: #536784;
}
QPushButton#cloneModeButton:checked {
    background: #17325b;
    border-color: #4f8cff;
    color: #8bb4ff;
    font-weight: 700;
}
QPushButton:pressed {
    background: #223754;
}
QPushButton#themeToggleButton {
    background: #162238;
    border: 1px solid #3a4b65;
    border-radius: 8px;
    padding: 8px;
}
QPushButton#themeToggleButton:hover {
    background: #223754;
    border-color: #5a7397;
}
QPushButton#inlineActionButton {
    background: transparent;
    border-color: #3a4b65;
    color: #79a8ff;
}
QPushButton#inlineActionButton:hover {
    background: #17325b;
    border-color: #285493;
}
QPushButton#primaryButton {
    background: #2f7dff;
    border-color: #2f7dff;
    color: #ffffff;
}
QPushButton#primaryButton:hover {
    background: #4b8fff;
}
QPushButton#dangerButton {
    background: #31171d;
    border-color: #8f3e4a;
    color: #fca5a5;
}
QPushButton:disabled {
    background: #101827;
    border-color: #26344a;
    color: #64748b;
}
QTableView,
QTableWidget,
QListView,
QListWidget,
QTreeView {
    background: #0e1829;
    alternate-background-color: #121f33;
    border: 1px solid #34445d;
    color: #dbe6f5;
    gridline-color: #34445d;
    selection-background-color: #234e89;
    selection-color: #ffffff;
    outline: none;
}
QTableView::item,
QTableWidget::item,
QListView::item,
QListWidget::item,
QTreeView::item {
    border-color: #26344a;
}
QTableView::item:hover,
QTableWidget::item:hover,
QListView::item:hover,
QListWidget::item:hover,
QTreeView::item:hover {
    background: #192a43;
}
QHeaderView::section {
    background: #162238;
    border: none;
    border-right: 1px solid #34445d;
    border-bottom: 1px solid #34445d;
    color: #cbd5e1;
    padding: 6px;
}
QTableCornerButton::section {
    background: #162238;
    border: 1px solid #34445d;
}
QTabBar::tab {
    background: #121f33;
    border: 1px solid #34445d;
    color: #aebbd0;
}
QTabBar::tab:hover {
    background: #192a43;
}
QTabBar::tab:selected {
    background: #111b2e;
    color: #79a8ff;
    border-bottom-color: #111b2e;
}
QGroupBox {
    background: transparent;
    border: 1px solid #34445d;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
    color: #dbe6f5;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #dbe6f5;
}
QCheckBox,
QRadioButton {
    background: transparent;
    color: #dbe6f5;
    spacing: 7px;
}
QProgressBar {
    background: #101827;
    border-color: #34445d;
    color: #dbe6f5;
}
QProgressBar::chunk {
    background: #2f7dff;
}
QSlider::groove:horizontal {
    background: #24344d;
}
QSlider::handle:horizontal {
    background: #dbe6f5;
    border-color: #7890b0;
}
QSlider::sub-page:horizontal {
    background: #2f7dff;
}
QScrollBar:vertical,
QScrollBar:horizontal {
    background: #0d1727;
    border: none;
}
QScrollBar:vertical {
    width: 12px;
}
QScrollBar:horizontal {
    height: 12px;
}
QScrollBar::handle:vertical,
QScrollBar::handle:horizontal {
    background: #4b5f7b;
    border-radius: 6px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {
    background: #647b9b;
}
QScrollBar::add-line,
QScrollBar::sub-line,
QScrollBar::add-page,
QScrollBar::sub-page {
    background: transparent;
    border: none;
}
QSplitter::handle {
    background: #26344a;
}
QStatusBar {
    background: #0f192a;
    border-top: 1px solid #26344a;
    color: #aebbd0;
}
QToolTip {
    background: #1d2a40;
    border: 1px solid #536784;
    color: #f8fafc;
    padding: 6px;
}
QListWidget#sfxEventPanel {
    background: #2f1d12;
    border-color: #7c4a24;
}
QListWidget#sfxEventPanel::item {
    color: #fdba74;
}
QListWidget#sfxEventPanel::item:selected {
    background: #613519;
    color: #ffedd5;
}
QListWidget#sfxEventPanel[audioTrack="music"] {
    background: #102442;
    border-color: #285493;
}
QListWidget#sfxEventPanel[audioTrack="music"]::item:selected {
    background: #193f70;
    color: #dbeafe;
}
QListWidget#sfxEventPanel[audioTrack="ambient"] {
    background: #0e2b28;
    border-color: #267267;
}
QListWidget#sfxEventPanel[audioTrack="ambient"]::item:selected {
    background: #155048;
    color: #ccfbf1;
}
"""
