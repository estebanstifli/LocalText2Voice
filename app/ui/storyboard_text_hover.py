from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea, QVBoxLayout


class StoryboardTextHover(QFrame):
    """Expanded narration card that stays open while the reader hovers it."""

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setObjectName("storyboardTextHover")
        self._dark = None
        self._surface = QColor("#ffffff")
        self._border = QColor("#cbd5e1")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.time_label = QLabel()
        self.time_label.setTextFormat(Qt.TextFormat.PlainText)
        self.time_label.setObjectName("storyboardHoverTime")
        layout.addWidget(self.time_label)
        self.text_label = QLabel()
        self.text_label.setObjectName("storyboardHoverText")
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.text_label)
        self.scroll.viewport().setAutoFillBackground(False)
        layout.addWidget(self.scroll, 1)
        self.anchor = QRect()
        self._key = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(220)
        self._hide_timer.timeout.connect(self._hide_if_outside)

    def _apply_theme(self):
        app = QApplication.instance()
        theme = app.property("uiTheme") if app else None
        dark = theme == "dark" if theme in ("light", "dark") else self.parentWidget().palette().color(self.palette().ColorRole.Base).lightness() < 128
        if dark == self._dark:
            return
        self._dark = dark
        self._surface = QColor("#162238" if dark else "#ffffff")
        self._border = QColor("#405575" if dark else "#cbd5e1")
        foreground = "#e5edf8" if dark else "#172033"
        heading = "#a9cfff" if dark else "#405675"
        handle = "#536984" if dark else "#b6c4d6"
        hover = "#7894b7" if dark else "#8da4bf"
        self.setStyleSheet(
            "QFrame#storyboardTextHover { background: transparent; border: none; }"
            f"QLabel#storyboardHoverText {{ background: transparent; border: none; color: {foreground}; padding: 0; }}"
            f"QLabel#storyboardHoverTime {{ background: transparent; border: none; color: {heading}; font-weight: 600; padding: 0; }}"
            "QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 10px; margin: 0; border: none; }"
            f"QScrollBar::handle:vertical {{ background: {handle}; min-height: 24px; border-radius: 5px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: {hover}; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; background: transparent; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        )
        self.update()

    def paintEvent(self, event):
        # Paint the surface once; translucent window corners avoid square halos.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._surface)
        painter.setPen(QPen(self._border, 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        painter.end()

    def show_text(self, text: str, time_range: str, anchor: QRect, pointer: QPoint):
        self._hide_timer.stop()
        self._apply_theme()
        self.anchor = anchor
        key = (text, time_range, anchor, self._dark)
        if key == self._key and self.isVisible():
            return
        self._key = key
        screen = QApplication.screenAt(pointer) or self.screen()
        available = screen.availableGeometry().adjusted(8, 8, -8, -8)
        width = min(520, available.width())
        self.text_label.setText(text)
        self.time_label.setText(time_range)
        body_height = self.text_label.heightForWidth(max(1, width - 42))
        height = min(max(90, body_height + 58), max(90, min(450, available.height() // 2)))
        y = anchor.bottom() + 8
        height = min(height, max(90, available.bottom() - y + 1))
        self.resize(width, height)
        x = max(available.left(), min(pointer.x() - width // 2, available.right() - width + 1))
        y = max(available.top(), min(y, available.bottom() - height + 1))
        self.move(x, y)
        self.scroll.verticalScrollBar().setValue(0)
        self.show()

    def schedule_hide(self):
        self._hide_timer.start()

    def _hide_if_outside(self):
        pointer = QCursor.pos()
        if not self.frameGeometry().contains(pointer) and not self.anchor.contains(pointer):
            self.hide()

    def enterEvent(self, event):
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.schedule_hide()
        super().leaveEvent(event)
