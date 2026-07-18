from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QCloseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.waveform_preview import generate_waveform_preview

from .audio_mix_preview_panel import WaveformGraph
from .icons import ui_icon


class AudioTailCutDialog(QDialog):
    """Waveform player that lets the user seek and choose an exact cut point."""

    def __init__(
        self,
        audio_path: Path,
        ffmpeg_path: str | Path,
        initial_cut_seconds: float,
        tr: Callable[..., str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.audio_path = Path(audio_path)
        self.tr = tr
        self.cut_seconds: float | None = None
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="ltv_tail_waveform_"
        )

        envelope = generate_waveform_preview(
            self.audio_path,
            ffmpeg_path,
            Path(self._temporary_directory.name),
            max_points=12000,
        )
        self.duration_seconds = max(
            envelope.source_duration_seconds,
            envelope.duration_seconds,
            0.01,
        )

        self.setWindowTitle(
            self.tr("review_tail_waveform_title", "Waveform tail editor")
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(820, 430)
        self.resize(940, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        help_label = QLabel(
            self.tr(
                "review_tail_waveform_help",
                "Click or drag the white line to seek. Listen around the end of "
                "the spoken text, place the line after the final valid sound, then "
                "choose Cut Here. Whisper will review the trimmed clip again.",
            )
        )
        help_label.setObjectName("helperLabel")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self.waveform = WaveformGraph(
            self.tr("review_tail_waveform", "Segment waveform"),
            self.tr("review_tail_waveform_empty", "No waveform data is available."),
            self,
        )
        self.waveform.setMinimumHeight(270)
        self.waveform.set_waveforms(
            [(envelope, QColor("#238cff"), 1.0, 0.0, False)],
            self.duration_seconds,
        )
        self.waveform.set_view(0.0, self.duration_seconds)
        self.waveform.cursorChanged.connect(self._seek_to)
        layout.addWidget(self.waveform, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.play_button = QPushButton(self.tr("play", "Play"))
        self.play_button.setIcon(ui_icon("play"))
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.play_button)

        self.position_label = QLabel()
        self.position_label.setMinimumWidth(150)
        controls.addWidget(self.position_label)
        controls.addStretch(1)

        cancel_button = QPushButton(self.tr("cancel", "Cancel"))
        cancel_button.clicked.connect(self.reject)
        controls.addWidget(cancel_button)
        self.cut_button = QPushButton(self.tr("review_tail_cut_here", "Cut Here"))
        self.cut_button.setIcon(ui_icon("edit"))
        self.cut_button.clicked.connect(self._accept_cut)
        controls.addWidget(self.cut_button)
        layout.addLayout(controls)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setSource(QUrl.fromLocalFile(str(self.audio_path)))
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)

        start = max(0.0, min(float(initial_cut_seconds), self.duration_seconds))
        self._set_cursor(start)

    def _toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.position() >= max(0, self.player.duration() - 20):
                self.player.setPosition(0)
            self.player.play()

    def _seek_to(self, seconds: float) -> None:
        self.player.setPosition(round(seconds * 1000))
        self._set_cursor(seconds)

    def _on_position_changed(self, milliseconds: int) -> None:
        self._set_cursor(milliseconds / 1000.0)

    def _set_cursor(self, seconds: float) -> None:
        position = max(0.0, min(float(seconds), self.duration_seconds))
        self.waveform.set_cursor(position)
        self.position_label.setText(
            self.tr(
                "review_tail_cut_position",
                "Cut position: {position:.2f} / {duration:.2f} s",
                position=position,
                duration=self.duration_seconds,
            )
        )
        self.cut_button.setEnabled(0.01 < position < self.duration_seconds - 0.01)

    def _on_playback_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText(self.tr("pause", "Pause"))
            self.play_button.setIcon(ui_icon("pause"))
        else:
            self.play_button.setText(self.tr("play", "Play"))
            self.play_button.setIcon(ui_icon("play"))

    def _accept_cut(self) -> None:
        position = self.waveform.cursor_seconds
        if not 0.01 < position < self.duration_seconds - 0.01:
            QMessageBox.warning(
                self,
                self.tr("review_tail_invalid_cut_title", "Invalid cut position"),
                self.tr(
                    "review_tail_invalid_cut",
                    "Choose a point inside the audio clip.",
                ),
            )
            return
        self.cut_seconds = position
        self.accept()

    def done(self, result: int) -> None:
        self.player.stop()
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.player.stop()
        super().closeEvent(event)

    def __del__(self) -> None:
        try:
            self._temporary_directory.cleanup()
        except (AttributeError, OSError):
            pass
