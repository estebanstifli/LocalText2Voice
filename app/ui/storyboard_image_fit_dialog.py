from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageFilter, ImageOps
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from app.ui.icons import ui_icon


class _FrameCropCanvas(QWidget):
    changed = Signal()

    def __init__(self, image: QImage, width: int, height: int, parent=None):
        super().__init__(parent)
        self.image = image.copy()
        self.output_width, self.output_height = width, height
        self.cover = max(width / image.width(), height / image.height())
        self.contain = min(width / image.width(), height / image.height())
        self.scale = self.cover
        self.offset = QPointF()
        self.background = "solid"
        self.color = QColor("#000000")
        self._blurred = QImage()
        self._drag = None
        self.setMinimumSize(400, 260)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def frame_rect(self):
        scale = min((self.width() - 64) / self.output_width,
                    (self.height() - 64) / self.output_height)
        width, height = self.output_width * scale, self.output_height * scale
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2,
                      width, height)

    def image_rect(self):
        width, height = self.image.width() * self.scale, self.image.height() * self.scale
        return QRectF((self.output_width - width) / 2 + self.offset.x(),
                      (self.output_height - height) / 2 + self.offset.y(), width, height)

    def set_scale(self, scale):
        previous = self.scale
        self.scale = max(self.contain, min(self.cover * 4, scale))
        self.offset *= self.scale / previous
        self._clamp_offset()
        self.changed.emit()
        self.update()

    def _clamp_offset(self):
        x = abs(self.image.width() * self.scale - self.output_width) / 2
        y = abs(self.image.height() * self.scale - self.output_height) / 2
        self.offset = QPointF(max(-x, min(x, self.offset.x())),
                              max(-y, min(y, self.offset.y())))

    def fit(self, cover=True):
        self.offset = QPointF()
        self.set_scale(self.cover if cover else self.contain)

    def _background_image(self):
        if self._blurred.isNull():
            source = self.image.convertToFormat(QImage.Format.Format_RGBA8888)
            pil = Image.frombytes("RGBA", (source.width(), source.height()),
                                  bytes(source.constBits()), "raw", "RGBA",
                                  source.bytesPerLine()).convert("RGB")
            size = (max(1, self.output_width // 4), max(1, self.output_height // 4))
            pil = ImageOps.fit(pil, size, method=Image.Resampling.LANCZOS)
            pil = pil.filter(ImageFilter.GaussianBlur(max(size) / 30))
            buffer = BytesIO()
            pil.save(buffer, format="PNG")
            self._blurred = QImage.fromData(buffer.getvalue())
        return self._blurred

    def _paint_output(self, painter):
        output = QRectF(0, 0, self.output_width, self.output_height)
        painter.fillRect(output, self.color)
        if self.background == "blur":
            painter.drawImage(output, self._background_image())
        painter.drawImage(self.image_rect(), self.image)

    def result_image(self):
        result = QImage(self.output_width, self.output_height, QImage.Format.Format_RGB32)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._paint_output(painter)
        painter.end()
        return result

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#20242b"))
        frame = self.frame_rect()
        scale = frame.width() / self.output_width
        painter.save()
        painter.translate(frame.topLeft())
        painter.scale(scale, scale)
        painter.setOpacity(0.25)
        painter.drawImage(self.image_rect(), self.image)
        painter.setOpacity(1)
        painter.setClipRect(QRectF(0, 0, self.output_width, self.output_height))
        self._paint_output(painter)
        painter.restore()
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawRect(frame)
        painter.setPen(QPen(QColor(255, 255, 255, 65), 1))
        for fraction in (1 / 3, 2 / 3):
            x, y = frame.left() + frame.width() * fraction, frame.top() + frame.height() * fraction
            painter.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
            painter.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            scale = self.frame_rect().width() / self.output_width
            self.offset += (event.position() - self._drag) / scale
            self._drag = event.position()
            self._clamp_offset()
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event):
        self.set_scale(self.scale * 1.1 ** (event.angleDelta().y() / 120))
        event.accept()


class StoryboardImageFitDialog(QDialog):
    def __init__(self, tr, image: QImage, width: int, height: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("storyboard_fit_title", "Fit image to frame"))
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        dimensions = QLabel(tr(
            "storyboard_fit_dimensions", "Original: {source} → Result: {target}",
            source=f"{image.width()} × {image.height()}", target=f"{width} × {height}"))
        layout.addWidget(dimensions)
        help_label = QLabel(tr("storyboard_fit_help",
            "Drag to position the image. Use the mouse wheel or zoom controls. Only the area inside the frame is saved."))
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.canvas = _FrameCropCanvas(image, width, height)
        layout.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        for key, label, callback in (
            ("fill", "Fill", lambda: self.canvas.fit(True)),
            ("contain", "Fit whole image", lambda: self.canvas.fit(False)),
            ("reset", "Reset", self._reset),
        ):
            button = QPushButton(tr(f"storyboard_fit_{key}", label))
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()
        self.background = QComboBox()
        self.background.addItem(tr("storyboard_fit_solid", "Solid background"), "solid")
        self.background.addItem(tr("storyboard_fit_blur", "Blurred background"), "blur")
        self.background.currentIndexChanged.connect(self._background_changed)
        row.addWidget(self.background)
        self.color_button = QPushButton(tr("storyboard_fit_color", "Color…"))
        self.color_button.clicked.connect(self._choose_color)
        row.addWidget(self.color_button)
        layout.addLayout(row)
        zoom_row = QHBoxLayout()
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(0, 1000)
        self.zoom.valueChanged.connect(self._zoom_changed)
        for name, factor in (("zoom_out", 1 / 1.1), ("zoom_in", 1.1)):
            button = QPushButton()
            button.setIcon(ui_icon(name))
            label = tr("storyboard_fit_" + name, "Zoom out" if factor < 1 else "Zoom in")
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.clicked.connect(lambda checked=False, factor=factor: self.canvas.set_scale(self.canvas.scale * factor))
            zoom_row.addWidget(button)
            if factor < 1:
                zoom_row.addWidget(self.zoom, 1)
        self.zoom_label = QLabel()
        zoom_row.addWidget(self.zoom_label)
        layout.addLayout(zoom_row)
        self.warning = QLabel(tr("storyboard_fit_upscale",
            "This crop enlarges the original pixels and may look less sharp."))
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        buttons = QDialogButtonBox()
        buttons.addButton(tr("cancel", "Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.addButton(tr("storyboard_fit_apply", "Apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.canvas.changed.connect(self._sync_zoom)
        self._sync_zoom()

    def _sync_zoom(self):
        self.zoom.blockSignals(True)
        self.zoom.setValue(round(1000 * (self.canvas.scale - self.canvas.contain) /
                                 (self.canvas.cover * 4 - self.canvas.contain)))
        self.zoom.blockSignals(False)
        self.zoom_label.setText(f"{self.canvas.scale * 100:.0f}%")
        self.warning.setVisible(self.canvas.scale > 1.001)

    def _zoom_changed(self, value):
        self.canvas.set_scale(self.canvas.contain + value / 1000 *
                              (self.canvas.cover * 4 - self.canvas.contain))

    def _background_changed(self):
        self.canvas.background = self.background.currentData()
        self.color_button.setEnabled(self.canvas.background == "solid")
        self.canvas.update()

    def _choose_color(self):
        color = QColorDialog.getColor(self.canvas.color, self)
        if color.isValid():
            self.canvas.color = color
            self.canvas.update()

    def _reset(self):
        self.background.setCurrentIndex(0)
        self.canvas.color = QColor("#000000")
        self.canvas.fit(True)


def fit_pasted_image(tr, image: QImage, width: int, height: int, parent=None) -> QImage:
    if image.isNull() or (image.width() == width and image.height() == height):
        return image.copy()
    dialog = StoryboardImageFitDialog(tr, image, max(1, width), max(1, height), parent)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.canvas.result_image()
        return QImage()
    finally:
        dialog.deleteLater()
