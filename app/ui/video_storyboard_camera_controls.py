from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from app.core.video_storyboard_camera import runpod_camera_prompt, camera_prompt, normalize_camera


class _CameraDiagram(QWidget):
    """Small projected camera rig. Only paint here; never change child widgets."""

    axisDragged = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(155)
        self.setMaximumHeight(190)
        self.setMouseTracking(True)
        self.camera = normalize_camera({})
        self.image = QImage()
        self._drag = ""
        self.setAccessibleName("Camera orbit, elevation and distance diagram")

    def _point(self, x, y, z):
        scale = min(self.width() / 6, self.height() / 3.7)
        return QPointF(self.width() * 0.46 + (x + z * 0.4) * scale,
                       self.height() * 0.47 + (z * 0.38 - y) * scale)

    def _position(self):
        angle = math.radians(-self.camera["rotate_deg"])
        distance = 2.1 - self.camera["move_forward"] * 0.09
        return self._point(math.sin(angle) * distance,
                           -self.camera["vertical_tilt"] * 0.75,
                           math.cos(angle) * distance)

    def _handles(self):
        return {
            "rotate_deg": self._position(),
            "vertical_tilt": QPointF(self.width() - 24, self.height() * (0.5 + self.camera["vertical_tilt"] * 0.3)),
            "move_forward": QPointF(25 + self.camera["move_forward"] * (self.width() - 90) / 10, self.height() - 15),
        }

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.palette().alternateBase())
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
            grid = QColor(self.palette().mid().color())
            grid.setAlpha(70)
            painter.setPen(QPen(grid, 1))
            for step in range(-2, 3):
                painter.drawLine(self._point(step, -0.65, -1), self._point(step, -0.65, 2))
                painter.drawLine(self._point(-2, -0.65, step), self._point(2, -0.65, step))
            arc = QPolygonF([self._point(math.sin(math.radians(a)) * 2.1, 0, math.cos(math.radians(a)) * 2.1)
                             for a in range(-90, 91, 5)])
            painter.setPen(QPen(QColor("#35b993"), 2))
            painter.drawPolyline(arc)
            center = self._point(0, 0, 0)
            if not self.image.isNull():
                size = self.image.size().scaled(78, 50, Qt.AspectRatioMode.KeepAspectRatio)
                painter.drawImage(QRectF(center.x() - size.width() / 2, center.y() - size.height() / 2,
                                         size.width(), size.height()), self.image)
            painter.setPen(QPen(QColor("#35b993"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(center, self._position())
            handles = self._handles()
            painter.setPen(QPen(QColor("#c778c9"), 2))
            painter.drawLine(QPointF(self.width() - 24, self.height() * 0.2),
                             QPointF(self.width() - 24, self.height() * 0.8))
            painter.setPen(QPen(QColor("#d59642"), 2))
            painter.drawLine(QPointF(25, self.height() - 15), QPointF(self.width() - 65, self.height() - 15))
            for key, color in (("rotate_deg", "#35b993"), ("vertical_tilt", "#c778c9"), ("move_forward", "#d59642")):
                painter.setPen(QPen(self.palette().base().color(), 2))
                painter.setBrush(QColor(color))
                painter.drawEllipse(handles[key], 8, 8)
            painter.setPen(self.palette().text().color())
            painter.drawText(QRectF(12, 6, self.width() - 24, 22), Qt.AlignmentFlag.AlignLeft, "↔  /  ↕  /  +")
        finally:
            painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position()
            self._drag = next((key for key, handle in self._handles().items()
                               if (handle - point).manhattanLength() < 26), "")
            if self._drag:
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag:
            point = event.position()
            if self._drag == "rotate_deg":
                scale = min(self.width() / 6, self.height() / 3.7)
                # Recover the orbit position in the ground plane.
                z = (point.y() - self.height() * 0.47) / (scale * 0.38)
                x = (point.x() - self.width() * 0.46) / scale - z * 0.4
                value = max(-90, min(90, -math.degrees(math.atan2(x, z))))
            elif self._drag == "vertical_tilt":
                value = (point.y() / self.height() - 0.5) / 0.3
            else:
                value = (point.x() - 25) * 10 / max(1, self.width() - 90)
            camera = normalize_camera({**self.camera, self._drag: value})
            self.axisDragged.emit(self._drag, camera[self._drag])
            event.accept()
            return
        hovering = any((handle - event.position()).manhattanLength() < 26 for handle in self._handles().values())
        self.setCursor(Qt.CursorShape.OpenHandCursor if hovering else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = ""
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)


class StoryboardCameraControls(QWidget):
    changed = Signal()

    def __init__(self, tr: Callable[..., str], parent=None):
        super().__init__(parent)
        self.tr_text = tr
        self.runpod_mode = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.enable_check = QCheckBox(tr("storyboard_camera_enable", "Change camera · Multiple-angles LoRA"))
        layout.addWidget(self.enable_check)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        self.diagram = _CameraDiagram()
        self.diagram.setToolTip(tr("storyboard_camera_drag", "Drag the green orbit, purple elevation or amber distance handle. This is a schematic, not an AI preview."))
        body.addWidget(self.diagram)
        self.sliders: dict[str, QSlider] = {}
        self.values: dict[str, QLabel] = {}
        for key, label, low, high in (
            ("rotate_deg", tr("storyboard_camera_orbit", "Orbit"), -2, 2),
            ("vertical_tilt", tr("storyboard_camera_elevation", "Elevation"), -1, 1),
            ("move_forward", tr("storyboard_camera_distance", "Distance"), 0, 2),
        ):
            row = QHBoxLayout()
            caption = QLabel(label)
            caption.setMinimumWidth(65)
            row.addWidget(caption)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(low, high)
            slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            slider.setTickInterval(1)
            slider.setAccessibleName(label)
            row.addWidget(slider, 1)
            value = QLabel()
            value.setMinimumWidth(85)
            row.addWidget(value)
            body.addLayout(row)
            self.sliders[key] = slider
            self.values[key] = value
            slider.valueChanged.connect(self._refresh)
        self.wide_check = QCheckBox(tr("storyboard_camera_wide", "Wide-angle lens"))
        body.addWidget(self.wide_check)
        presets = QGridLayout()
        for index, (label, data) in enumerate((
            (tr("storyboard_camera_left", "Left 45°"), {"rotate_deg": 45}),
            (tr("storyboard_camera_right", "Right 45°"), {"rotate_deg": -45}),
            (tr("storyboard_camera_top", "Bird's-eye"), {"vertical_tilt": -1}),
            (tr("storyboard_camera_low", "Low angle"), {"vertical_tilt": 1}),
            (tr("storyboard_camera_close", "Close-up"), {"move_forward": 10}),
            (tr("storyboard_camera_reset", "Reset camera"), {}),
        )):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, selected=data: self.set_camera(selected))
            presets.addWidget(button, index // 3, index % 3)
        body.addLayout(presets)
        layout.addWidget(self.body)
        self.prompt_label = QLabel()
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setTextFormat(Qt.TextFormat.PlainText)
        self.prompt_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.prompt_label.setObjectName("helperLabel")
        layout.addWidget(self.prompt_label)
        self.enable_check.toggled.connect(self._refresh)
        self.wide_check.toggled.connect(self._refresh)
        self.diagram.axisDragged.connect(self._axis_dragged)
        self._refresh()

    def set_runpod_mode(self):
        self.runpod_mode = True
        self.wide_check.hide()
        self.sliders["move_forward"].setValue(1)
        self._refresh()

    def camera(self):
        return {
            "enabled": self.enable_check.isChecked(),
            "rotate_deg": self.sliders["rotate_deg"].value() * 45,
            "vertical_tilt": self.sliders["vertical_tilt"].value(),
            "move_forward": self.sliders["move_forward"].value() * 5,
            "wideangle": self.wide_check.isChecked(),
        }

    def set_camera(self, values):
        camera = normalize_camera(({"move_forward": 5, **values}) if self.runpod_mode else values)
        for key, factor in (("rotate_deg", 45), ("vertical_tilt", 1), ("move_forward", 5)):
            self.sliders[key].setValue(camera[key] // factor)
        self.wide_check.setChecked(camera["wideangle"])
        self._refresh()

    def set_image(self, image):
        self.diagram.image = image.scaled(160, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.diagram.update()

    def _axis_dragged(self, key, value):
        self.sliders[key].setValue(value // {"rotate_deg": 45, "move_forward": 5}.get(key, 1))

    def _refresh(self, *_args):
        tr = self.tr_text
        camera = self.camera()
        rotation = camera["rotate_deg"]
        self.values["rotate_deg"].setText(
            tr("storyboard_camera_front", "Front") if not rotation else
            tr("storyboard_camera_left_value", "{angle}° left", angle=rotation) if rotation > 0 else
            tr("storyboard_camera_right_value", "{angle}° right", angle=abs(rotation)))
        self.values["vertical_tilt"].setText({
            -1: tr("storyboard_camera_top", "Bird's-eye"), 0: tr("storyboard_camera_eye", "Eye level"),
            1: tr("storyboard_camera_low", "Low angle"),
        }[camera["vertical_tilt"]])
        self.values["move_forward"].setText({
            0: tr("storyboard_camera_original", "Original"), 5: tr("storyboard_camera_nearer", "Nearer"),
            10: tr("storyboard_camera_close", "Close-up"),
        }[camera["move_forward"]])
        if self.runpod_mode:
            self.values["move_forward"].setText({0: tr("storyboard_camera_wide_shot", "Wide shot"), 5: tr("storyboard_camera_medium_shot", "Medium shot"), 10: tr("storyboard_camera_close", "Close-up")}[camera["move_forward"]])
        self.body.setVisible(camera["enabled"])
        self.diagram.camera = camera
        self.diagram.update()
        self.prompt_label.setText((runpod_camera_prompt(camera) if self.runpod_mode else camera_prompt(camera)) or tr("storyboard_camera_off", "No camera change. The camera LoRA will not be loaded."))
        self.changed.emit()
