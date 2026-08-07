from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.tts.install_logging import install_detail_text, is_install_detail


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
    "f5_russian": EngineInstallRequirement(15, "15-40 min"),
    "russian_normalization": EngineInstallRequirement(4, "3-10 min"),
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
        *,
        install_path: Path | None = None,
        existing_model_detected: bool = False,
        license_notice: str = "",
        license_url: str = "",
    ) -> None:
        super().__init__(parent)
        self.engine_name = engine_name
        self.requirement = requirement
        self.available_free_gb = available_free_gb
        self.volume = volume
        self.install_path = (
            install_path.expanduser().resolve() if install_path is not None else None
        )
        self.tr = tr
        self.existing_model_detected = existing_model_detected
        self.license_notice = license_notice.strip()
        self.license_url = license_url.strip()
        self._installation_started = False
        self._installation_active = False
        self._installation_completed = False

        self.setWindowTitle(
            (
                self.tr(
                    "engine_repair_title",
                    "Repair / Update {engine}",
                    engine=self.engine_name,
                )
                if self.existing_model_detected
                else self.tr(
                    "engine_install_title",
                    "Install {engine}",
                    engine=self.engine_name,
                )
            )
        )
        self.setModal(True)
        self.setMinimumSize(760, 620)
        self.resize(820, 680)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        title = QLabel(
            (
                self.tr(
                    "engine_repair_heading",
                    "Reuse and repair {engine}",
                    engine=self.engine_name,
                )
                if self.existing_model_detected
                else self.tr(
                    "engine_install_heading",
                    "Prepare {engine}",
                    engine=self.engine_name,
                )
            )
        )
        title.setObjectName("engineInstallHeading")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        intro = QLabel(
            (
                self.tr(
                    "engine_repair_intro",
                    "Existing model files were detected. They will be reused, and "
                    "only missing or updated runtime/model files will be downloaded. "
                    "You can also run this again later to repair or update the engine.",
                )
                if self.existing_model_detected
                else self.tr(
                    "engine_install_intro",
                    "This engine downloads its own isolated Python dependencies and AI "
                    "model. The application may appear busy while large files are being "
                    "downloaded and prepared.",
                )
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if self.install_path is not None:
            self.install_path_label = QLabel(
                self.tr(
                    "engine_install_destination",
                    "Model download directory:\n{path}",
                    path=str(self.install_path),
                )
            )
            self.install_path_label.setObjectName("engineInstallDestination")
            self.install_path_label.setWordWrap(True)
            self.install_path_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(self.install_path_label)

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
        self.space_label.setProperty("spaceAvailable", enough_space)
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
            layout.addWidget(self.space_warning_label)

        self.license_checkbox: QCheckBox | None = None
        if self.license_notice:
            license_text = self.license_notice
            if self.license_url:
                license_text += f' <a href="{self.license_url}">CC BY-NC 4.0</a>'
            license_label = QLabel(license_text)
            license_label.setObjectName("engineInstallLicenseNotice")
            license_label.setWordWrap(True)
            license_label.setOpenExternalLinks(True)
            layout.addWidget(license_label)
            self.license_checkbox = QCheckBox(
                self.tr(
                    "engine_install_accept_noncommercial",
                    "I understand that this model and its generated output are for non-commercial use only.",
                )
            )
            layout.addWidget(self.license_checkbox)

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

        details_label = QLabel(
            self.tr("engine_install_details", "Installation details")
        )
        details_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(details_label)

        self.details_view = QPlainTextEdit()
        self.details_view.setObjectName("engineInstallDetails")
        self.details_view.setReadOnly(True)
        self.details_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.details_view.setMinimumHeight(180)
        self.details_view.document().setMaximumBlockCount(10000)
        self._last_detail = ""
        initial_details: list[str] = []
        if self.install_path is not None:
            initial_details.append(
                self.tr(
                    "engine_install_log_destination",
                    "Destination: {path}",
                    path=str(self.install_path),
                )
            )
        initial_details.append(
            self.tr(
                "engine_install_log_space",
                "Free space on {volume}: {free:.1f} GB",
                volume=self.volume,
                free=self.available_free_gb,
            )
        )
        self.details_view.setPlainText("\n".join(initial_details))
        layout.addWidget(self.details_view, 1)

        self.close_warning_label = QLabel(
            self.tr(
                "engine_install_do_not_close",
                "Important: do not close LocalText2Voice or turn off the computer "
                "while the installation is running.",
            )
        )
        self.close_warning_label.setObjectName("engineInstallCloseWarning")
        self.close_warning_label.setWordWrap(True)
        layout.addWidget(self.close_warning_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.later_button = QPushButton(
            self.tr("engine_install_later", "Another time")
        )
        self.install_button = QPushButton(
            (
                self.tr("engine_repair_now", "Repair / Update now")
                if self.existing_model_detected
                else self.tr("engine_install_now", "Install now")
            )
        )
        self.install_button.setObjectName("engineInstallNowButton")
        self.install_button.setDefault(True)
        self._enough_space = enough_space
        self.install_button.setEnabled(enough_space and self.license_checkbox is None)
        if self.license_checkbox is not None:
            self.license_checkbox.toggled.connect(
                lambda checked: self.install_button.setEnabled(
                    self._enough_space and checked and not self._installation_active
                )
            )
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
        self._append_detail(self.progress_label.text())
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
        if is_install_detail(message):
            self._append_detail(install_detail_text(message))
            return
        if total > 0:
            percentage = max(0, min(100, int((current / total) * 100)))
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percentage)
        else:
            self.progress_bar.setRange(0, 0)
        if message:
            self.progress_label.setText(message)
            prefix = f"[{self.progress_bar.value():3d}%] " if total > 0 else ""
            self._append_detail(f"{prefix}{message}")

    def finish(self, success: bool, message: str) -> None:
        self._installation_active = False
        self._installation_completed = True
        self.progress_bar.setRange(0, 100)
        if success:
            self.progress_bar.setValue(100)
        self.progress_label.setText(message)
        result = (
            self.tr("engine_install_log_success", "COMPLETED")
            if success
            else self.tr("engine_install_log_failed", "STOPPED / FAILED")
        )
        self._append_detail(f"{result}: {message}")
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

    def _append_detail(self, message: str) -> None:
        value = str(message).strip()
        if not value or value == self._last_detail:
            return
        self._last_detail = value
        self.details_view.appendPlainText(value)
        self.details_view.ensureCursorVisible()

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
