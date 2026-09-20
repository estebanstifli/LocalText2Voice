"""Illustrated, keyboard-accessible category tabs for storyboard settings."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy, QVBoxLayout


class _ElidedLabel(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


class StoryboardSettingsCard(QFrame):
    selected = Signal()
    stepRequested = Signal(int)

    def __init__(self, title, image_path, parent=None):
        super().__init__(parent)
        self.setObjectName("storyboardSettingsCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        self.setProperty("selected", False)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet('''
            QFrame#storyboardSettingsCard { border: 2px solid rgba(128, 144, 168, 110); border-radius: 10px; background: transparent; }
            QFrame#storyboardSettingsCard:hover { border-color: #5596ef; }
            QFrame#storyboardSettingsCard[selected="true"] { border: 3px solid #3b82f6; background: rgba(59, 130, 246, 20); }
            QFrame#storyboardSettingsCard:focus { border-color: #5596ef; }
            QFrame#storyboardSettingsCard QLabel { border: none; background: transparent; padding: 0; }
        ''')
        box = QVBoxLayout(self)
        box.setContentsMargins(9, 8, 9, 8)
        box.setSpacing(5)
        self.art = QLabel()
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setFixedHeight(106)
        pixmap = QPixmap(str(image_path))
        if not pixmap.isNull():
            self.art.setPixmap(pixmap.scaled(177, 138, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.heading = _ElidedLabel(title)
        self.heading.setToolTip(title)
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heading.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.model = _ElidedLabel()
        self.status = _ElidedLabel()
        for label in (self.model, self.status):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            label.setFixedHeight(19)
            label.setStyleSheet("font-size: 11px;")
        self.install = QPushButton()
        self.install.setStyleSheet("font-size: 11px; padding: 3px 5px;")
        for label in (self.art, self.heading, self.model, self.status):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            box.addWidget(label)
        box.addWidget(self.install)
        box.addStretch(1)
        self.setFixedHeight(229)

    def set_selected(self, selected):
        if self.property("selected") == selected:
            return
        self.setProperty("selected", selected)
        self.setAccessibleDescription("Selected" if selected else "")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.selected.emit()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.selected.emit()
            event.accept()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.stepRequested.emit(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
        else:
            super().keyPressEvent(event)
