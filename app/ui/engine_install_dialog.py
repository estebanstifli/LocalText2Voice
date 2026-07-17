from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


Translate = Callable[..., str]


@dataclass(frozen=True)
class EngineInstallRequirement:
    recommended_free_gb: int
    duration_estimate: str


ENGINE_INSTALL_REQUIREMENTS: dict[str, EngineInstallRequirement] = {
    "kokoro": EngineInstallRequirement(8, "5-15 min"),
    "chatterbox": EngineInstallRequirement(20, "15-40 min"),
    "qwen": EngineInstallRequirement(20, "20-45 min"),
    "omnivoice": EngineInstallRequirement(30, "30-60+ min"),
}


def available_disk_space_gb(path: Path) -> tuple[float, str]:
    """Return free GiB and the volume label for the nearest existing path."""

    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    usage = shutil.disk_usage(candidate)
    volume = candidate.anchor or str(candidate)
    return usage.free / (1024**3), volume


class EngineInstallDialog(QDialog):
    """Modal confirmation and live progress view for a downloadable TTS engine."""

    install_requested = Signal()
    cancel_requested = Signal()

    def __init__(
        self,
        engine_name: str,
        requirement: EngineInstallRequirement,
        available_free_gb: float,
        volume: str,
        tr: Translate,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine_name = engine_name
        self.requirement = requirement
        self.available_free_gb = available_free_gb
        self.volume = volume
        self.tr = tr
        self._installation_started = False
        self._installation_active = False
        self._installation_completed = False

        self.setWindowTitle(
            self.tr(
                "engine_install_title",
                "Install {engine}",
                engine=self.engine_name,
            )
        )
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        title = QLabel(
            self.tr(
                "engine_install_heading",
                "Prepare {engine}",
                engine=self.engine_name,
            )
        )
        title.setObjectName("engineInstallHeading")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        intro = QLabel(
            self.tr(
                "engine_install_intro",
                "This engine downloads its own isolated Python dependencies and AI "
                "model. The application may appear busy while large files are being "
                "downloaded and prepared.",
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        required = QLabel(
            self.tr(
                "engine_install_space_required",
                "Recommended free space before installation: at least {size} GB.",
                size=self.requirement.recommended_free_gb,
            )
        )
        required.setStyleSheet("font-weight: 600;")
        layout.addWidget(required)

        enough_space = available_free_gb >= requirement.recommended_free_gb
        self.space_label = QLabel(
            self.tr(
                "engine_install_space_available",
                "Currently available on {volume}: {free:.1f} GB.",
                volume=self.volume,
                free=self.available_free_gb,
            )
        )
        self.space_label.setObjectName("engineInstallSpaceAvailable")
        self.space_label.setStyleSheet(
            "color: #18794e; font-weight: 600;"
            if enough_space
            else "color: #b42318; font-weight: 700;"
        )
        layout.addWidget(self.space_label)

        duration = QLabel(
            self.tr(
                "engine_install_duration",
                "Estimated time: {duration}. It may take longer depending on your "
                "internet connection, computer and storage speed.",
                duration=self.requirement.duration_estimate,
            )
        )
        duration.setWordWrap(True)
        layout.addWidget(duration)

        if not enough_space:
            self.space_warning_label = QLabel(
                self.tr(
                    "engine_install_not_enough_space",
                    "There is not enough free space to start safely. Free some disk "
                    "space and open this installer again.",
                )
            )
            self.space_warning_label.setObjectName("engineInstallSpaceWarning")
            self.space_warning_label.setWordWrap(True)
            self.space_warning_label.setStyleSheet(
                "background: #fff1f0; border: 1px solid #fda29b; "
                "border-radius: 6px; color: #912018; padding: 10px;"
            )
            layout.addWidget(self.space_warning_label)

        self.progress_label = QLabel(
            self.tr(
                "engine_install_waiting",
                "Waiting for confirmation.",
            )
        )
        self.progress_label.setObjectName("engineInstallProgressLabel")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("engineInstallProgressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.close_warning_label = QLabel(
            self.tr(
                "engine_install_do_not_close",
                "Important: do not close LocalText2Voice or turn off the computer "
                "while the installation is running.",
            )
        )
        self.close_warning_label.setObjectName("engineInstallCloseWarning")
        self.close_warning_label.setWordWrap(True)
        self.close_warning_label.setStyleSheet(
            "background: #fffaeb; border: 1px solid #fedf89; "
            "border-radius: 6px; color: #7a2e0e; padding: 10px; font-weight: 600;"
        )
        layout.addWidget(self.close_warning_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.later_button = QPushButton(
            self.tr("engine_install_later", "Another time")
        )
        self.install_button = QPushButton(
            self.tr("engine_install_now", "Install now")
        )
        self.install_button.setObjectName("engineInstallNowButton")
        self.install_button.setDefault(True)
        self.install_button.setEnabled(enough_space)
        self.later_button.clicked.connect(self._on_later_clicked)
        self.install_button.clicked.connect(self._on_install_clicked)
        buttons.addWidget(self.later_button)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

    @property
    def installation_active(self) -> bool:
        return self._installation_active

    @property
    def installation_started(self) -> bool:
        return self._installation_started

    def _on_install_clicked(self) -> None:
        if self._installation_completed:
            self.accept()
            return
        if self._installation_active or not self.install_button.isEnabled():
            return
        self._installation_started = True
        self._installation_active = True
        self.install_button.setEnabled(False)
        self.later_button.setText(
            self.tr("engine_install_cancel", "Cancel installation")
        )
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText(
            self.tr(
                "engine_install_starting",
                "Starting {engine} installation...",
                engine=self.engine_name,
            )
        )
        self.install_requested.emit()

    def _on_later_clicked(self) -> None:
        if not self._installation_active:
            self.reject()
            return
        self.later_button.setEnabled(False)
        self.progress_label.setText(
            self.tr("engine_install_cancelling", "Cancelling installation...")
        )
        self.cancel_requested.emit()

    def update_progress(self, current: int, total: int, message: str) -> None:
        if not self._installation_active:
            return
        if total > 0:
            percentage = max(0, min(100, int((current / total) * 100)))
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percentage)
        else:
            self.progress_bar.setRange(0, 0)
        if message:
            self.progress_label.setText(message)

    def finish(self, success: bool, message: str) -> None:
        self._installation_active = False
        self._installation_completed = True
        self.progress_bar.setRange(0, 100)
        if success:
            self.progress_bar.setValue(100)
        self.progress_label.setText(message)
        self.close_warning_label.setText(
            self.tr(
                "engine_install_finished_safe",
                "The installation process has stopped. You can close this window safely.",
            )
        )
        self.later_button.hide()
        self.install_button.setText(self.tr("close", "Close"))
        self.install_button.setEnabled(True)
        self.install_button.setDefault(True)

    def reject(self) -> None:
        if self._installation_active:
            self.progress_label.setText(
                self.tr(
                    "engine_install_close_blocked",
                    "Installation is still running. Use Cancel installation and wait "
                    "for it to stop safely.",
                )
            )
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._installation_active:
            self.progress_label.setText(
                self.tr(
                    "engine_install_close_blocked",
                    "Installation is still running. Use Cancel installation and wait "
                    "for it to stop safely.",
                )
            )
            event.ignore()
            return
        super().closeEvent(event)
