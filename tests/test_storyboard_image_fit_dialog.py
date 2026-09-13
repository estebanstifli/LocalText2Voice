import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QDialog

from app.ui.storyboard_image_fit_dialog import (
    _FrameCropCanvas, StoryboardImageFitDialog, fit_pasted_image,
)


def tr(key, default, **values):
    return default.format(**values)


class ImageFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def source(self):
        image = QImage(300, 100, QImage.Format.Format_RGB32)
        image.fill(QColor("green"))
        painter = QPainter(image)
        painter.fillRect(0, 0, 100, 100, QColor("red"))
        painter.fillRect(200, 0, 100, 100, QColor("blue"))
        painter.end()
        return image

    def test_fill_center_pan_and_exact_output(self):
        canvas = _FrameCropCanvas(self.source(), 100, 100)
        result = canvas.result_image()
        self.assertEqual((result.width(), result.height()), (100, 100))
        self.assertEqual(result.pixelColor(50, 50), QColor("green"))
        canvas.offset = QPointF(100, 0)
        self.assertEqual(canvas.result_image().pixelColor(50, 50), QColor("red"))

    def test_fit_preserves_whole_image_and_background(self):
        canvas = _FrameCropCanvas(self.source(), 300, 300)
        canvas.fit(False)
        result = canvas.result_image()
        self.assertEqual(result.pixelColor(150, 0), QColor("black"))
        self.assertEqual(result.pixelColor(50, 150), QColor("red"))
        self.assertEqual(result.pixelColor(250, 150), QColor("blue"))
        canvas.background = "blur"
        self.assertNotEqual(canvas.result_image().pixelColor(150, 0), QColor("black"))

    def test_same_resolution_skips_dialog_and_cancel_returns_no_image(self):
        image = self.source()
        with patch.object(StoryboardImageFitDialog, "exec", return_value=QDialog.DialogCode.Rejected) as execute:
            self.assertEqual(fit_pasted_image(tr, image, 300, 100), image)
            execute.assert_not_called()
            self.assertTrue(fit_pasted_image(tr, image, 1280, 720).isNull())

    def test_zoom_warning_reset_and_accepted_dimensions(self):
        dialog = StoryboardImageFitDialog(tr, self.source(), 100, 100)
        self.addCleanup(dialog.deleteLater)
        dialog.canvas.set_scale(2)
        self.assertFalse(dialog.warning.isHidden())
        dialog._reset()
        self.assertEqual(dialog.canvas.scale, dialog.canvas.cover)
        self.assertTrue(dialog.warning.isHidden())
        with patch.object(StoryboardImageFitDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            result = fit_pasted_image(tr, self.source(), 1280, 720)
        self.assertEqual((result.width(), result.height()), (1280, 720))
