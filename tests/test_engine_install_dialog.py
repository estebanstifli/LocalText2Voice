from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLabel

from app.ui.engine_install_dialog import (
    EngineInstallDialog,
    EngineInstallRequirement,
    available_disk_space_gb,
)


def translate(key: str, default: str | None = None, **values: object) -> str:
    message = default if default is not None else key
    return message.format(**values)


class EngineInstallDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _dialog(self, free_gb: float = 50.0) -> EngineInstallDialog:
        dialog = EngineInstallDialog(
            "OmniVoice",
            EngineInstallRequirement(30, "30-60+ min"),
            free_gb,
            "C:\\",
            translate,
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_confirmation_shows_space_time_progress_and_warning(self) -> None:
        dialog = self._dialog()

        label_texts = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertTrue(any("30 GB" in text for text in label_texts))
        self.assertIn("50.0 GB", dialog.space_label.text())
        self.assertEqual(dialog.progress_bar.value(), 0)
        self.assertTrue(dialog.install_button.isEnabled())
        self.assertEqual(dialog.install_button.text(), "Install now")
        self.assertEqual(dialog.later_button.text(), "Another time")
        self.assertIn("do not close", dialog.close_warning_label.text().lower())

    def test_install_stays_modal_and_reports_live_progress(self) -> None:
        dialog = self._dialog()
        installs: list[bool] = []
        cancellations: list[bool] = []
        dialog.install_requested.connect(lambda: installs.append(True))
        dialog.cancel_requested.connect(lambda: cancellations.append(True))

        dialog.install_button.click()

        self.assertEqual(installs, [True])
        self.assertTrue(dialog.installation_active)
        self.assertEqual(dialog.progress_bar.maximum(), 0)
        self.assertEqual(dialog.later_button.text(), "Cancel installation")

        dialog.update_progress(42, 100, "Downloading model...")
        self.assertEqual(dialog.progress_bar.value(), 42)
        self.assertEqual(dialog.progress_label.text(), "Downloading model...")

        close_event = QCloseEvent()
        dialog.closeEvent(close_event)
        self.assertFalse(close_event.isAccepted())
        self.assertTrue(dialog.installation_active)

        dialog.later_button.click()
        self.assertEqual(cancellations, [True])
        self.assertFalse(dialog.later_button.isEnabled())

        dialog.finish(False, "Installation cancelled.")
        self.assertFalse(dialog.installation_active)
        self.assertEqual(dialog.install_button.text(), "Close")
        self.assertTrue(dialog.install_button.isEnabled())

    def test_install_is_blocked_when_recommended_space_is_unavailable(self) -> None:
        dialog = self._dialog(free_gb=12.0)

        self.assertFalse(dialog.install_button.isEnabled())
        self.assertIn("not enough free space", dialog.space_warning_label.text().lower())
        self.assertIn("12.0 GB", dialog.space_label.text())

    def test_available_disk_space_uses_nearest_existing_parent(self) -> None:
        gib = 1024**3
        with patch(
            "app.ui.engine_install_dialog.shutil.disk_usage",
            return_value=SimpleNamespace(total=100 * gib, used=60 * gib, free=40 * gib),
        ) as disk_usage:
            free_gb, volume = available_disk_space_gb(
                Path.cwd() / "missing" / "engine" / "folder"
            )

        self.assertEqual(free_gb, 40.0)
        self.assertTrue(volume)
        self.assertTrue(disk_usage.called)


if __name__ == "__main__":
    unittest.main()
