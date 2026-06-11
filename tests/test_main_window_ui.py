from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from app.ui.main_window import MainWindow


class MainWindowUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_generation_and_settings_views_are_separate(self) -> None:
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        self.assertEqual(window.page_stack.count(), 2)
        self.assertEqual(window.page_stack.currentIndex(), 0)
        self.assertTrue(hasattr(window, "import_button"))
        self.assertFalse(hasattr(window, "refresh_voices_button"))
        self.assertFalse(window.import_button.icon().isNull())
        author_credit = window.findChild(QLabel, "authorCreditLabel")
        self.assertIsNotNone(author_credit)
        self.assertTrue(author_credit.openExternalLinks())
        self.assertIn("https://andromedanova.com", author_credit.text())

        window.settings_button.click()
        self.assertEqual(window.page_stack.currentIndex(), 1)
        self.assertEqual(window.settings_tabs.count(), 2)

        window.back_button.click()
        self.assertEqual(window.page_stack.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()
