"""Timeline navigation controls, independent of rendering and thumbnail caches."""
import re
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QScrollBar, QLineEdit


class TimelineScrollBar(QScrollBar):
    """A quarter-width grip with the normal scrollbar's full logical range."""
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._drag_offset = None
        self.setMinimumHeight(16)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def handle_rect(self):
        width = self.width() if self.maximum() == self.minimum() else max(1, round(self.width() * .25))
        span = self.width() - width
        ratio = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            ratio = 1 - ratio
        return QRect(round(span * ratio), 2, width, max(1, self.height() - 4))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.AlternateBase))
        color = self.palette().color(QPalette.ColorRole.Highlight if self.isSliderDown() else QPalette.ColorRole.Mid)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(self.handle_rect(), 5, 5)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        handle = self.handle_rect()
        self._drag_offset = event.position().x() - handle.x() if handle.contains(event.position().toPoint()) else handle.width() / 2
        self.setSliderDown(True)
        self._drag_to(event.position().x())
        event.accept()

    def _drag_to(self, x):
        span = self.width() - self.handle_rect().width()
        ratio = max(0., min(1., (x - self._drag_offset) / max(1, span)))
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            ratio = 1 - ratio
        self.setSliderPosition(round(self.minimum() + ratio * (self.maximum() - self.minimum())))

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self._drag_to(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_to(event.position().x())
            self._drag_offset = None
            self.setSliderDown(False)
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


def parse_time(text):
    text = text.strip().replace(",", ".")
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?", text):
        raise ValueError("Use seconds, MM:SS or HH:MM:SS")
    parts = [float(p) for p in text.split(":")]
    if any(p >= 60 for p in parts[1:]):
        raise ValueError("Minutes and seconds must be below 60")
    result = 0.
    for part in parts:
        result = result * 60 + part
    return result


class TimelineTimeEdit(QLineEdit):
    seekRequested = Signal(float)

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self._position = self._duration = 0.
        self._editing = False
        self.setReadOnly(True)
        self.setFrame(False)
        self.setFixedWidth(200)
        self.setToolTip(tr("storyboard_time_edit_help", "Hover and enter seconds, MM:SS or HH:MM:SS. Enter: go to time. Escape: cancel."))
        self.editingFinished.connect(self._commit)
        self.set_time(0, 0)

    @staticmethod
    def _format(value):
        seconds = max(0, int(value))
        return (f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600
                else f"{seconds // 60:02d}:{seconds % 60:02d}")

    def set_time(self, position, duration):
        self._position, self._duration = position, duration
        if not self._editing:
            self.setText(f"{self._format(position)} / {self._format(duration)}")

    def _begin(self):
        if not self._editing:
            self._editing = True
            self.setReadOnly(False)
            self.setFrame(True)
            self.setText(self._format(self._position))
            self.selectAll()

    def _restore(self):
        self._editing = False
        self.setReadOnly(True)
        self.setFrame(False)
        self.set_time(self._position, self._duration)

    def enterEvent(self, event):
        self._begin()
        super().enterEvent(event)

    def focusInEvent(self, event):
        self._begin()
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        self._begin()
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        if not self.hasFocus():
            self._restore()
        super().leaveEvent(event)

    def _commit(self):
        if not self._editing:
            return
        if not self.isModified():
            self._restore()
            return
        try:
            seconds = min(self._duration, parse_time(self.text()))
        except ValueError:
            self._restore()
            return
        self._restore()
        self.seekRequested.emit(seconds)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._restore()
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)
