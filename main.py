from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.utils.logging_utils import configure_logging
from app.utils.paths import application_root


def main() -> int:
    configure_logging(application_root() / "logs" / "course_to_podcast.log")
    application = QApplication(sys.argv)
    application.setApplicationName("CourseToPodcast")
    application.setOrganizationName("CourseToPodcast")
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
