from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, QTimer, QUrl, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.audio_capture import (
    AudioCaptureUnavailable,
    REFERENCE_MAX_SECONDS,
    REFERENCE_RECOMMENDED_SECONDS,
    list_audio_capture_devices,
)
from app.core.waveform_preview import probe_audio_duration
from app.ui.icons import ui_icon
from app.verification.faster_whisper_manager import FasterWhisperManager
from app.workers.audio_capture_worker import AudioCaptureWorker
from app.workers.voice_transcription_worker import VoiceTranscriptionWorker


class AudioLevelMeter(QWidget):
    """Compact live level history used instead of a decorative waveform."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._levels: list[float] = [0.05] * 54
        self.setMinimumHeight(76)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_level(self, level: float) -> None:
        self._levels.append(max(0.02, min(1.0, float(level))))
        self._levels = self._levels[-54:]
        self.update()

    def reset(self) -> None:
        self._levels = [0.05] * 54
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)
        background = self.palette().color(self.palette().ColorRole.Base)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 7, 7)
        if not self._levels or rect.width() <= 0:
            return
        accent = self.palette().color(self.palette().ColorRole.Highlight)
        bar_width = max(2.0, rect.width() / len(self._levels) - 2.0)
        center = rect.center().y()
        for index, level in enumerate(self._levels):
            height = max(3.0, level * (rect.height() - 14))
            x = rect.left() + index * rect.width() / len(self._levels)
            color = QColor(accent)
            color.setAlpha(110 + round(145 * index / max(1, len(self._levels) - 1)))
            painter.setBrush(color)
            painter.drawRoundedRect(x, center - height / 2, bar_width, height, 1.5, 1.5)


class CloneVoiceDialog(QDialog):
    SUPPORTED_AUDIO_EXTENSIONS = {
        ".wav",
        ".mp3",
        ".flac",
        ".m4a",
        ".ogg",
        ".opus",
        ".aac",
        ".webm",
    }
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        whisper_manager: FasterWhisperManager | None = None,
        whisper_device: str = "cpu",
        whisper_compute_type: str = "int8",
        ffmpeg_path: str | Path = "ffmpeg/ffmpeg.exe",
        recording_directory: Path | None = None,
        translate: Callable[..., str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._translate = translate or (lambda _key, default, **_values: default)
        self.whisper_manager = whisper_manager or FasterWhisperManager()
        self.whisper_device = whisper_device
        self.whisper_compute_type = whisper_compute_type
        self.ffmpeg_path = ffmpeg_path
        self.recording_directory = recording_directory or Path(tempfile.gettempdir())
        self.source_path: Path | None = None
        self.source_duration: float | None = None
        self.source_mode = "upload"
        self._capture_started_at = 0.0
        self._capture_thread: QThread | None = None
        self._capture_worker: AudioCaptureWorker | None = None
        self._transcription_thread: QThread | None = None
        self._transcription_worker: VoiceTranscriptionWorker | None = None
        self._shutting_down = False

        self.setWindowTitle(self.tr_text("clone_voice", "Clone Voice"))
        self.setMinimumSize(720, 760)
        self.resize(760, 880)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel(self.tr_text("clone_voice", "Clone Voice"))
        title.setObjectName("sectionLabel")
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 4)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setMinimumHeight(title.sizeHint().height())
        subtitle = QLabel(
            self.tr_text(
                "clone_voice_help",
                "Create a voice profile from an uploaded or freshly recorded audio sample.",
            )
        )
        subtitle.setObjectName("helperLabel")
        subtitle.setWordWrap(True)
        subtitle.setMinimumHeight(subtitle.sizeHint().height())
        root.addWidget(title)
        root.addWidget(subtitle)

        mode_frame = QFrame()
        mode_frame.setObjectName("card")
        mode_frame.setMinimumHeight(52)
        mode_layout = QHBoxLayout(mode_frame)
        mode_layout.setContentsMargins(5, 5, 5, 5)
        mode_layout.setSpacing(5)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for mode, label, icon_name in (
            ("upload", self.tr_text("clone_voice_upload", "Upload"), "folder"),
            ("record", self.tr_text("clone_voice_record", "Record"), "generate"),
            (
                "system",
                self.tr_text("clone_voice_system_audio", "System Audio"),
                "volume",
            ),
        ):
            button = QPushButton(label)
            button.setObjectName("cloneModeButton")
            button.setCheckable(True)
            button.setIcon(ui_icon(icon_name))
            button.setMinimumHeight(40)
            button.clicked.connect(lambda _checked=False, selected=mode: self._select_mode(selected))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            mode_layout.addWidget(button, 1)
        self.mode_buttons["upload"].setChecked(True)
        root.addWidget(mode_frame)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        content = QVBoxLayout(scroll_content)
        content.setContentsMargins(0, 0, 6, 0)
        content.setSpacing(12)

        self.capture_stack = QStackedWidget()
        self.capture_stack.addWidget(self._build_upload_page())
        self.capture_stack.addWidget(self._build_capture_page())
        content.addWidget(self.capture_stack)

        advice = QLabel(
            self.tr_text(
                "clone_voice_duration_help",
                "For the best clone, use about 12 seconds of clean, voice-only audio. "
                "Accepted length: 3-20 seconds.",
            )
        )
        advice.setObjectName("helperLabel")
        advice.setWordWrap(True)
        content.addWidget(advice)

        self.audio_ready_frame = QFrame()
        self.audio_ready_frame.setObjectName("card")
        ready_layout = QVBoxLayout(self.audio_ready_frame)
        self.audio_status_label = QLabel(
            self.tr_text("clone_voice_no_audio", "No audio selected yet.")
        )
        self.audio_status_label.setWordWrap(True)
        ready_layout.addWidget(self.audio_status_label)
        ready_actions = QHBoxLayout()
        self.play_button = QPushButton(self.tr_text("play", "Play"))
        self.play_button.setIcon(ui_icon("play"))
        self.play_button.clicked.connect(self._toggle_playback)
        self.transcribe_button = QPushButton(
            self.tr_text("clone_voice_transcribe", "Transcribe automatically")
        )
        self.transcribe_button.setIcon(ui_icon("language"))
        self.transcribe_button.clicked.connect(self._start_transcription)
        self.record_again_button = QPushButton(
            self.tr_text("clone_voice_record_again", "Record again")
        )
        self.record_again_button.setIcon(ui_icon("regenerate"))
        self.record_again_button.clicked.connect(self._start_recording)
        ready_actions.addWidget(self.play_button)
        ready_actions.addWidget(self.transcribe_button)
        ready_actions.addWidget(self.record_again_button)
        ready_actions.addStretch(1)
        ready_layout.addLayout(ready_actions)
        self.transcription_status_label = QLabel()
        self.transcription_status_label.setObjectName("helperLabel")
        self.transcription_status_label.setWordWrap(True)
        ready_layout.addWidget(self.transcription_status_label)
        self.audio_ready_frame.setVisible(False)
        content.addWidget(self.audio_ready_frame)

        reference_label = QLabel(
            self.tr_text("omnivoice_reference_text", "Reference transcript")
        )
        reference_label.setObjectName("sectionTitle")
        self.reference_text_edit = QTextEdit()
        self.reference_text_edit.setFixedHeight(82)
        self.reference_text_edit.setPlaceholderText(
            self.tr_text(
                "clone_voice_transcript_placeholder",
                "Enter the exact text spoken in the audio, or use automatic transcription...",
            )
        )
        content.addWidget(reference_label)
        content.addWidget(self.reference_text_edit)

        self._build_profile_form(content)
        permission = QLabel(
            self.tr_text(
                "clone_voice_permission_help",
                "Only clone a voice when you have the speaker's permission to use it.",
            )
        )
        permission.setObjectName("helperLabel")
        permission.setWordWrap(True)
        content.addWidget(permission)
        content.addStretch(1)
        scroll.setWidget(scroll_content)
        root.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton(self.tr_text("cancel", "Cancel"))
        self.clone_button = QPushButton(
            self.tr_text("clone_voice_save", "Save Voice")
        )
        self.clone_button.setObjectName("primaryButton")
        self.clone_button.setIcon(ui_icon("voice"))
        cancel_button.clicked.connect(self.reject)
        self.clone_button.clicked.connect(self._accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.clone_button)
        root.addLayout(buttons)

        self.capture_timer = QTimer(self)
        self.capture_timer.setInterval(100)
        self.capture_timer.timeout.connect(self._update_capture_time)
        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self._update_play_button)
        self._update_whisper_state()

    def tr_text(self, key: str, default: str, **values: object) -> str:
        return self._translate(key, default, **values)

    def _build_upload_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("card")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 22, 20, 22)
        layout.setSpacing(10)
        heading = QLabel(
            self.tr_text("clone_voice_upload_heading", "Upload an audio sample")
        )
        heading.setObjectName("sectionTitle")
        helper = QLabel(
            self.tr_text(
                "clone_voice_upload_help",
                "Choose a clean recording without music, effects, echo, or other speakers.",
            )
        )
        helper.setObjectName("helperLabel")
        helper.setWordWrap(True)
        helper.setAlignment(Qt.AlignmentFlag.AlignCenter)
        helper.setMinimumWidth(480)
        self.upload_button = QPushButton(
            self.tr_text("clone_voice_choose_audio", "Choose audio file")
        )
        self.upload_button.setObjectName("primaryButton")
        self.upload_button.setIcon(ui_icon("folder"))
        self.upload_button.setMinimumHeight(42)
        self.upload_button.clicked.connect(self._choose_upload)
        layout.addWidget(heading, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(helper, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.upload_button, 0, Qt.AlignmentFlag.AlignHCenter)
        return page

    def _build_capture_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("card")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        source_row = QHBoxLayout()
        self.device_label = QLabel(
            self.tr_text("clone_voice_microphone", "Microphone")
        )
        self.device_combo = QComboBox()
        self.refresh_devices_button = QPushButton()
        self.refresh_devices_button.setIcon(ui_icon("refresh"))
        self.refresh_devices_button.setToolTip(
            self.tr_text("clone_voice_refresh_sources", "Refresh audio sources")
        )
        self.refresh_devices_button.clicked.connect(self._refresh_devices)
        source_row.addWidget(self.device_label)
        source_row.addWidget(self.device_combo, 1)
        source_row.addWidget(self.refresh_devices_button)
        layout.addLayout(source_row)
        self.capture_error_label = QLabel()
        self.capture_error_label.setObjectName("helperLabel")
        self.capture_error_label.setWordWrap(True)
        layout.addWidget(self.capture_error_label)
        self.level_meter = AudioLevelMeter()
        layout.addWidget(self.level_meter)
        self.capture_time_label = QLabel("00:00.0 / 00:20.0")
        self.capture_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.capture_time_label)
        capture_actions = QHBoxLayout()
        capture_actions.addStretch(1)
        self.record_button = QPushButton(
            self.tr_text("clone_voice_start_recording", "Start recording")
        )
        self.record_button.setObjectName("primaryButton")
        self.record_button.setIcon(ui_icon("generate"))
        self.record_button.clicked.connect(self._start_recording)
        self.stop_button = QPushButton(self.tr_text("stop", "Stop"))
        self.stop_button.setIcon(ui_icon("stop"))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_recording)
        capture_actions.addWidget(self.record_button)
        capture_actions.addWidget(self.stop_button)
        capture_actions.addStretch(1)
        layout.addLayout(capture_actions)
        return page

    def _build_profile_form(self, content: QVBoxLayout) -> None:
        profile = QGroupBox(self.tr_text("clone_voice_profile", "Voice profile"))
        form = QFormLayout(profile)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            self.tr_text("clone_voice_name_placeholder", "My cloned voice")
        )
        self.language_combo = QComboBox()
        for label, code in (
            (self.tr_text("clone_voice_language_auto", "Reference / Auto"), "reference"),
            (self.tr_text("language_english", "English"), "en"),
            (self.tr_text("language_spanish", "Spanish"), "es"),
            (self.tr_text("language_french", "French"), "fr"),
            (self.tr_text("language_german", "German"), "de"),
            (self.tr_text("language_italian", "Italian"), "it"),
            (self.tr_text("language_portuguese", "Portuguese"), "pt"),
            (self.tr_text("clone_voice_language_japanese", "Japanese"), "ja"),
            (self.tr_text("clone_voice_language_chinese", "Chinese"), "zh"),
            (self.tr_text("clone_voice_language_hindi", "Hindi"), "hi"),
            (self.tr_text("clone_voice_language_russian", "Russian"), "ru"),
        ):
            self.language_combo.addItem(label, code)
        self.short_description_edit = QLineEdit()
        self.short_description_edit.setPlaceholderText(
            self.tr_text(
                "clone_voice_description_placeholder",
                "Warm narrator, energetic promo, calm teacher...",
            )
        )
        self.gender_combo = QComboBox()
        for label, value in (
            (self.tr_text("clone_voice_not_specified", "Not specified"), ""),
            (self.tr_text("omnivoice_gender_female", "Female"), "female"),
            (self.tr_text("omnivoice_gender_male", "Male"), "male"),
            (self.tr_text("clone_voice_gender_neutral", "Neutral"), "neutral"),
        ):
            self.gender_combo.addItem(label, value)
        self.age_style_combo = QComboBox()
        for label, value in (
            (self.tr_text("clone_voice_not_specified", "Not specified"), ""),
            (self.tr_text("omnivoice_age_child", "Child"), "child"),
            (self.tr_text("omnivoice_age_young_adult", "Young adult"), "young_adult"),
            (self.tr_text("omnivoice_age_middle_aged", "Middle-aged"), "middle_aged"),
            (self.tr_text("clone_voice_age_mature", "Mature"), "mature"),
            (self.tr_text("omnivoice_age_elderly", "Elderly"), "elderly"),
        ):
            self.age_style_combo.addItem(label, value)
        self.voice_style_edit = QLineEdit()
        self.voice_style_edit.setPlaceholderText(
            self.tr_text(
                "clone_voice_style_placeholder",
                "storyteller, documentary, character, podcast...",
            )
        )
        form.addRow(self.tr_text("voice_name", "Voice name"), self.name_edit)
        form.addRow(self.tr_text("language", "Language"), self.language_combo)
        form.addRow(self.tr_text("short_description", "Short description"), self.short_description_edit)
        form.addRow(self.tr_text("gender", "Gender"), self.gender_combo)
        form.addRow(self.tr_text("age_style", "Age style"), self.age_style_combo)
        form.addRow(self.tr_text("voice_style", "Voice style"), self.voice_style_edit)
        content.addWidget(profile)

    def _select_mode(self, mode: str) -> None:
        if self._capture_thread is not None:
            return
        self.source_mode = mode
        self.mode_buttons[mode].setChecked(True)
        self.capture_stack.setCurrentIndex(0 if mode == "upload" else 1)
        self.record_again_button.setVisible(mode != "upload")
        if mode != "upload":
            self.device_label.setText(
                self.tr_text("clone_voice_microphone", "Microphone")
                if mode == "record"
                else self.tr_text("clone_voice_system_source", "System source")
            )
            self._refresh_devices()

    def _choose_upload(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            self.tr_text("clone_voice_choose_reference", "Choose reference audio"),
            "",
            self.tr_text(
                "clone_voice_audio_filter",
                "Audio files (*.wav *.mp3 *.flac *.m4a *.ogg *.opus *.aac *.webm);;All files (*.*)",
            ),
        )
        if selected:
            path = Path(selected)
            if path.suffix.lower() not in self.SUPPORTED_AUDIO_EXTENSIONS:
                QMessageBox.warning(
                    self,
                    self.tr_text("clone_voice_unsupported_title", "Unsupported audio format"),
                    self.tr_text(
                        "clone_voice_unsupported_message",
                        "Choose a WAV, MP3, FLAC, M4A, OGG, OPUS, AAC, or WEBM file.",
                    ),
                )
                return
            duration = probe_audio_duration(path, self.ffmpeg_path)
            if duration and not 3.0 <= duration <= REFERENCE_MAX_SECONDS:
                QMessageBox.warning(
                    self,
                    self.tr_text("clone_voice_length_title", "Audio length is not suitable"),
                    self.tr_text(
                        "clone_voice_selected_length",
                        "Reference audio must be between 3 and 20 seconds. The selected file is {duration:.1f} seconds.",
                        duration=duration,
                    ),
                )
                return
            self._set_audio_source(path, "upload", duration or None)

    def _refresh_devices(self) -> None:
        kind = "microphone" if self.source_mode == "record" else "system"
        previous_id = str(self.device_combo.currentData() or "")
        self.device_combo.clear()
        self.capture_error_label.clear()
        try:
            devices = list_audio_capture_devices(kind)
        except AudioCaptureUnavailable as exc:
            devices = []
            self.capture_error_label.setText(
                self.tr_text(
                    "clone_voice_audio_service_error",
                    "The operating-system audio service could not be queried: {message}",
                    message=str(exc),
                )
            )
        for device in devices:
            label = (
                self.tr_text(
                    "clone_voice_default_device",
                    "{name} (Default)",
                    name=device.name,
                )
                if device.is_default
                else device.name
            )
            self.device_combo.addItem(label, device.identifier)
        previous_index = self.device_combo.findData(previous_id)
        if previous_index >= 0:
            self.device_combo.setCurrentIndex(previous_index)
        if not devices and not self.capture_error_label.text():
            message = (
                self.tr_text(
                    "clone_voice_no_microphones",
                    "No microphones were found. Check the operating-system privacy settings.",
                )
                if kind == "microphone"
                else self.tr_text(
                    "clone_voice_no_system_sources",
                    "No system audio sources were found. The selected OS/audio server may not expose speaker loopback capture.",
                )
            )
            self.capture_error_label.setText(message)
        self.record_button.setEnabled(bool(devices))

    def _start_recording(self) -> None:
        if self._capture_thread is not None or self._transcription_thread is not None:
            return
        device_id = str(self.device_combo.currentData() or "")
        if not device_id:
            QMessageBox.warning(
                self,
                self.tr_text("clone_voice_source_unavailable", "Audio source unavailable"),
                self.capture_error_label.text(),
            )
            return
        self.player.stop()
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output_path = self.recording_directory / f"clone-voice-{timestamp}-{time.time_ns() % 1_000_000}.wav"
        worker = AudioCaptureWorker(
            device_id,
            output_path,
            loopback=self.source_mode == "system",
            max_seconds=REFERENCE_MAX_SECONDS,
            translate=self.tr_text,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.level_changed.connect(self.level_meter.set_level)
        worker.finished.connect(self._on_recording_finished)
        worker.failed.connect(self._on_recording_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_capture_thread)
        self._capture_worker = worker
        self._capture_thread = thread
        self._capture_started_at = time.monotonic()
        self.level_meter.reset()
        self.capture_error_label.setText(
            self.tr_text(
                "clone_voice_recording_system",
                "Recording system audio... play the source you want to capture.",
            )
            if self.source_mode == "system"
            else self.tr_text(
                "clone_voice_recording_microphone",
                "Recording microphone... speak naturally and keep a steady distance.",
            )
        )
        self._set_recording_controls(True)
        self.capture_timer.start()
        thread.start()

    def _stop_recording(self) -> None:
        if self._capture_worker is not None:
            self.stop_button.setEnabled(False)
            self.capture_error_label.setText(
                self.tr_text("clone_voice_finishing_recording", "Finishing recording...")
            )
            self._capture_worker.request_stop()

    def _on_recording_finished(self, path: str, duration: float) -> None:
        self.capture_timer.stop()
        self._set_recording_controls(False)
        self.capture_time_label.setText(
            f"{self._format_seconds(duration)} / {self._format_seconds(REFERENCE_MAX_SECONDS)}"
        )
        self.capture_error_label.setText(
            self.tr_text("clone_voice_recording_complete", "Recording complete.")
        )
        self._set_audio_source(Path(path), self.source_mode, duration)

    def _on_recording_failed(self, message: str) -> None:
        self.capture_timer.stop()
        self._set_recording_controls(False)
        self.capture_error_label.setText(message)

    def _clear_capture_thread(self) -> None:
        self._capture_worker = None
        self._capture_thread = None
        self.clone_button.setEnabled(True)

    def _set_recording_controls(self, recording: bool) -> None:
        self.record_button.setEnabled(not recording and self.device_combo.count() > 0)
        self.stop_button.setEnabled(recording)
        self.device_combo.setEnabled(not recording)
        self.refresh_devices_button.setEnabled(not recording)
        for button in self.mode_buttons.values():
            button.setEnabled(not recording)
        self.clone_button.setEnabled(not recording)

    def _update_capture_time(self) -> None:
        elapsed = min(REFERENCE_MAX_SECONDS, time.monotonic() - self._capture_started_at)
        self.capture_time_label.setText(
            f"{self._format_seconds(elapsed)} / {self._format_seconds(REFERENCE_MAX_SECONDS)}"
        )

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        minutes, remainder = divmod(max(0.0, seconds), 60.0)
        return f"{int(minutes):02d}:{remainder:04.1f}"

    def _set_audio_source(
        self,
        path: Path,
        mode: str,
        duration: float | None = None,
    ) -> None:
        previous_default = self.source_path.stem if self.source_path else ""
        self.source_path = path
        self.source_duration = duration
        self.source_mode = mode
        mode_label = {
            "upload": self.tr_text("clone_voice_uploaded_audio", "Uploaded audio"),
            "record": self.tr_text("clone_voice_microphone_recording", "Microphone recording"),
            "system": self.tr_text("clone_voice_system_recording", "System audio recording"),
        }.get(mode, self.tr_text("clone_voice_audio", "Audio"))
        duration_text = f" - {duration:.1f}s" if duration is not None else ""
        self.audio_status_label.setText(
            self.tr_text(
                "clone_voice_audio_ready",
                "{source} ready - {filename}{duration}",
                source=mode_label,
                filename=path.name,
                duration=duration_text,
            )
        )
        self.audio_ready_frame.setVisible(True)
        self.record_again_button.setVisible(mode != "upload")
        if not self.name_edit.text().strip() or self.name_edit.text().strip() == previous_default:
            self.name_edit.setText(path.stem)
        self._update_whisper_state()

    def _update_whisper_state(self) -> None:
        installed = self.whisper_manager.is_installed()
        self.transcribe_button.setVisible(self.source_path is not None)
        self.transcribe_button.setEnabled(
            self.source_path is not None
            and installed
            and self._transcription_thread is None
        )
        if not installed:
            self.transcription_status_label.setText(
                self.tr_text(
                    "clone_voice_whisper_missing",
                    "Faster Whisper is not installed. You can type the transcript manually, or install it from Settings > Review for automatic transcription.",
                )
            )
        elif self.source_path is not None and self._transcription_thread is None:
            self.transcription_status_label.setText(
                self.tr_text(
                    "clone_voice_whisper_optional",
                    "Automatic transcription is optional and runs locally with Faster Whisper.",
                )
            )

    def _start_transcription(self) -> None:
        if self.source_path is None or self._transcription_thread is not None:
            return
        language = str(self.language_combo.currentData() or "reference")
        if language == "reference":
            language = "auto"
        worker = VoiceTranscriptionWorker(
            self.whisper_manager,
            self.source_path,
            language=language,
            device=self.whisper_device,
            compute_type=self.whisper_compute_type,
            translate=self.tr_text,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_transcription_finished)
        worker.failed.connect(self._on_transcription_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_transcription_thread)
        self._transcription_worker = worker
        self._transcription_thread = thread
        self.transcribe_button.setEnabled(False)
        self.clone_button.setEnabled(False)
        self.transcription_status_label.setText(
            self.tr_text(
                "clone_voice_transcribing",
                "Transcribing locally with Faster Whisper...",
            )
        )
        thread.start()

    def _on_transcription_finished(self, transcript: str) -> None:
        if transcript:
            self.reference_text_edit.setPlainText(transcript)
            self.transcription_status_label.setText(
                self.tr_text(
                    "clone_voice_transcription_complete",
                    "Transcription complete. Review the text and correct it if necessary.",
                )
            )
        else:
            self.transcription_status_label.setText(
                self.tr_text(
                    "clone_voice_no_speech",
                    "Faster Whisper did not detect speech. You can enter the transcript manually.",
                )
            )

    def _on_transcription_failed(self, message: str) -> None:
        self.transcription_status_label.setText(message)

    def _clear_transcription_thread(self) -> None:
        self._transcription_worker = None
        self._transcription_thread = None
        self.clone_button.setEnabled(True)
        self._update_whisper_state()

    def _toggle_playback(self) -> None:
        if self.source_path is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if self.player.source().toLocalFile() != str(self.source_path):
            self.player.setSource(QUrl.fromLocalFile(str(self.source_path)))
        self.player.play()

    def _update_play_button(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText(
            self.tr_text("pause", "Pause") if playing else self.tr_text("play", "Play")
        )
        self.play_button.setIcon(ui_icon("pause" if playing else "play"))

    def _accept(self) -> None:
        if self.source_path is None or not self.source_path.is_file():
            QMessageBox.warning(
                self,
                self.tr_text("clone_voice_sample_required", "Audio sample required"),
                self.tr_text(
                    "clone_voice_sample_required_message",
                    "Upload or record an audio sample before cloning the voice.",
                ),
            )
            return
        if (
            self.source_duration is not None
            and not 3.0 <= self.source_duration <= REFERENCE_MAX_SECONDS
        ):
            QMessageBox.warning(
                self,
                self.tr_text("clone_voice_length_title", "Audio length is not suitable"),
                self.tr_text(
                    "clone_voice_current_length",
                    "Reference audio must be between 3 and 20 seconds. The current recording is {duration:.1f} seconds.",
                    duration=self.source_duration,
                ),
            )
            return
        if self._capture_thread is not None or self._transcription_thread is not None:
            return
        self.accept()

    def values(self) -> dict[str, object]:
        if self.source_path is None:
            raise RuntimeError("No reference audio has been selected.")
        language_code = str(self.language_combo.currentData() or "reference")
        language_name = self.language_combo.currentText()
        if language_code == "reference":
            language_name = "Reference"
        gender = str(self.gender_combo.currentData() or "")
        age_style = str(self.age_style_combo.currentData() or "")
        voice_style = self.voice_style_edit.text().strip()
        tags = ["imported", "reference"]
        for value in (language_code, gender, age_style, voice_style):
            if value and value != "reference":
                tags.append(value)
        return {
            "source": self.source_path,
            "name": self.name_edit.text().strip() or self.source_path.stem,
            "language": language_code,
            "language_name": language_name,
            "ref_text": self.reference_text_edit.toPlainText().strip(),
            "short_description": self.short_description_edit.text().strip(),
            "gender": gender,
            "age_style": age_style,
            "voice_style": voice_style,
            "tags": tags,
        }

    def done(self, result: int) -> None:
        if self._shutting_down:
            super().done(result)
            return
        self._shutting_down = True
        self.capture_timer.stop()
        self.player.stop()
        if self._capture_worker is not None:
            self._capture_worker.request_stop()
        if self._capture_thread is not None and self._capture_thread.isRunning():
            self._capture_thread.quit()
            self._capture_thread.wait(5_000)
        if self._transcription_worker is not None:
            self._transcription_worker.request_cancel()
        if self._transcription_thread is not None and self._transcription_thread.isRunning():
            self._transcription_thread.quit()
            self._transcription_thread.wait(5_000)
        super().done(result)
