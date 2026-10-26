"""Design a reusable Breeze reference through the shared engine host."""
from copy import deepcopy
from pathlib import Path
import secrets
import tempfile

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QMessageBox, QScrollArea, QWidget,
)

from app.tts.audiocpp_config import BREEZE_MODEL, model_config, validated_config


EXAMPLES = {
    "en": (
        "An adult narrator with a warm, clear voice, gentle pacing and natural expression.",
        "Every morning, I open the window and listen to the birds in the garden. Today, I have a new story to share with you. Take a comfortable seat, and let us begin.",
    ),
    "zh": (
        "一位成年叙述者，声音温暖清晰，语速平稳，表达自然亲切。",
        "每天早晨，我都会打开窗户，听花园里的鸟儿歌唱。今天，我想和你分享一个新的故事。请找一个舒服的位置坐下，让我们一起开始吧。",
    ),
}


class BreezeDesignPreviewWorker(QObject):
    completed = Signal(str, str)

    def __init__(self, client, text, config, output):
        super().__init__()
        self.client, self.text, self.config, self.output = client, text, deepcopy(config), output

    @Slot()
    def run(self):
        try:
            from .voice_design_client import request_preview
            self.result = request_preview(self.client, self.text, self.config)
            self.completed.emit(self.result["output_wav"], "")
        except Exception as exc:
            self.completed.emit("", str(exc))


class BreezeVoiceDesignDialog(QDialog):
    """Shared preview/save workflow; subclasses provide a native model's controls."""
    MODEL = BREEZE_MODEL
    TAG = "breeze"
    EXAMPLES = EXAMPLES
    LANGUAGES = (("en", "language_english", "English"), ("zh", "language_chinese", "Chinese"))
    GUIDANCE = 4  # Official Breeze voice-design examples.
    TITLE = ("breeze_design_title", "Breeze Voice Designer")
    HELP = ("breeze_design_help", "Describe a voice and generate a sample without reference audio. Supported languages: English and Chinese. Write the description and sample in the same language. Listen before saving the WAV and its transcript as a reusable voice.")
    GENERATING = ("breeze_design_generating", "Generating a voice sample with Breeze. Please wait before closing.")
    GENERATION_FIELDS = (
        ("guidance_scale", 0, 20, False), ("temperature", .01, 1.99, False),
        ("depth_temperature", .01, 1.99, False), ("top_p", .01, 1, False),
        ("top_k", 0, 1000, True), ("seed", -1, 2147483647, True),
        ("max_tokens", 1, 16384, True), ("text_chunk_size", 20, 2048, True),
    )
    voice_saved = Signal(object)

    def __init__(self, client, gallery, config, ffmpeg_path, translate, parent=None):
        super().__init__(parent)
        self.client, self.gallery, self.ffmpeg_path, self.tr_text = client, gallery, ffmpeg_path, translate
        self.base_config = model_config(deepcopy(config), self.MODEL)
        self.thread = self.worker = None
        self.preview_path = None
        self.generated = self.pending = None
        self.saved = False
        self._temporary = tempfile.TemporaryDirectory(prefix="ltv-" + self.TAG + "-design-", ignore_cleanup_errors=True)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(.8)
        self.setWindowTitle(translate(*self.TITLE))
        self.resize(920, 700)
        layout = QVBoxLayout(self)
        help_text = QLabel(translate(*self.HELP))
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        columns = QHBoxLayout()
        layout.addLayout(columns, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.controls = QWidget()
        left = QVBoxLayout(self.controls)
        scroll.setWidget(self.controls)
        columns.addWidget(scroll, 1)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.name_edit = QLineEdit(translate("omnivoice_design_default_name", "New designed voice"))
        self.language_combo = QComboBox()
        for code, key, label in self.LANGUAGES:
            self.language_combo.addItem(translate(key, label), code)
        form.addRow(translate("name", "Name"), self.name_edit)
        form.addRow(translate("language", "Language"), self.language_combo)
        left.addLayout(form)
        left.addWidget(QLabel(translate("breeze_design_description", "Voice description (free text)")))
        self.instruction_edit = QPlainTextEdit(self.EXAMPLES["en"][0])
        self.instruction_edit.setMaximumHeight(115)
        left.addWidget(self.instruction_edit)
        advanced = QGroupBox(translate("segment_generation", "Generation"))
        advanced_form = QFormLayout(advanced)
        self.fields = {}
        for key, low, high, integer in self.GENERATION_FIELDS:
            spin = QSpinBox() if integer else QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setValue(self.GUIDANCE if key == "guidance_scale" else self.base_config[key])
            label_key = "breeze_" + key if key in {"guidance_scale", "depth_temperature"} else "audiocpp_" + key
            advanced_form.addRow(translate(label_key, key.replace("_", " ").capitalize()), spin)
            self.fields[key] = spin
        left.addWidget(advanced)
        left.addStretch()
        right = QVBoxLayout()
        columns.addLayout(right, 1)
        right.addWidget(QLabel(translate("breeze_design_sample", "Custom sample text")))
        self.sample_text_edit = QPlainTextEdit(self.EXAMPLES["en"][1])
        right.addWidget(self.sample_text_edit)
        sample_help = QLabel(translate("breeze_design_sample_help", "Use a short passage (up to 600 characters). The complete generated audio must be 1–60 seconds to save as a reference; it will not be trimmed."))
        sample_help.setWordWrap(True)
        right.addWidget(sample_help)
        self.status_label = QLabel(translate("omnivoice_design_status_ready", "Choose parameters and generate a preview."))
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        self.generate_button = QPushButton(translate("generate_preview", "Generate Preview"))
        self.play_button = QPushButton(translate("play_preview", "Play Preview"))
        self.save_button = QPushButton(translate("save_as_voice", "Save as Voice"))
        self.close_button = QPushButton(translate("close", "Close"))
        for button in (self.generate_button, self.play_button, self.save_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.play_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.generate_button.clicked.connect(self.generate_preview)
        self.play_button.clicked.connect(self.play_preview)
        self.save_button.clicked.connect(self.save_voice)
        self.close_button.clicked.connect(self.reject)
        self._language = "en"
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        for widget in (self.instruction_edit, self.sample_text_edit):
            widget.textChanged.connect(self._edited)
        for widget in self.fields.values():
            widget.valueChanged.connect(self._edited)

    @property
    def busy(self):
        return self.thread is not None

    def values(self):
        text = self.sample_text_edit.toPlainText().strip()
        instruction = self.instruction_edit.toPlainText().strip()
        if not instruction or not text or len(text) > 600 or "{{" in text:
            raise ValueError(self.tr_text("breeze_design_invalid", "Enter a voice description and 1–600 characters of sample text, without LocalText2Voice markup."))
        # Never inherit the selected catalog reference or playback processing.
        config = {key: self.base_config[key] for key in ("backend", "device", "timeout_seconds")}
        config.update(engine="audiocpp", model=self.MODEL, reference_audio_path="", reference_text="",
                      instruction=instruction, language=self.language_combo.currentData())
        config.update({key: widget.value() for key, widget in self.fields.items()})
        return {"text": text, "config": validated_config(config)}

    def _language_changed(self):
        target = self.language_combo.currentData()
        # Preserve custom text when changing the catalog language label.
        for widget, index in ((self.instruction_edit, 0), (self.sample_text_edit, 1)):
            if widget.toPlainText() == self.EXAMPLES.get(self._language, (self.EXAMPLES["en"][0], ""))[index]:
                widget.setPlainText(self.EXAMPLES.get(target, (self.EXAMPLES["en"][0], ""))[index])
        self._language = target
        self._edited()

    def _matches_preview(self):
        try:
            return self.generated is not None and self.values() == self.generated
        except ValueError:
            return False

    def _edited(self):
        valid = self._matches_preview()
        self.save_button.setEnabled(valid and not self.busy and not self.saved)
        if self.generated is not None and not valid:
            self.status_label.setText(self.tr_text("breeze_design_changed", "The design has changed. Generate a new preview before saving."))

    def generate_preview(self):
        if self.busy:
            return
        try:
            pending = self.values()
        except ValueError as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self.pending = deepcopy(pending)
        self.preview_path = self.generated = None
        self.saved = False
        self.play_button.setEnabled(False)
        self.save_button.setEnabled(False)
        # Unique filenames prevent media-player locks from replacing an old preview.
        output = Path(self._temporary.name) / (secrets.token_hex(8) + ".wav")
        self.thread = QThread(self)
        self.worker = BreezeDesignPreviewWorker(self.client, pending["text"], pending["config"], output)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.completed.connect(self._preview_ready)
        self.worker.completed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._worker_finished)
        self.controls.setEnabled(False)
        self.sample_text_edit.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status_label.setText(self.tr_text(*self.GENERATING))
        self.thread.start()

    @Slot(str, str)
    def _preview_ready(self, path, error):
        if error:
            self.status_label.setText(self.tr_text("omnivoice_design_preview_failed", "Preview failed: {message}").format(message=error))
            return
        self.draft_id = self.worker.result["draft_id"]
        self.preview_path = Path(path)
        self.generated = deepcopy(self.pending)
        self.play_button.setEnabled(True)
        self.status_label.setText(self.tr_text("omnivoice_design_preview_ready", "Preview ready. Listen, then save it as a reusable voice."))
        self.play_preview()

    @Slot()
    def _worker_finished(self):
        self.worker = self.thread = None
        self.controls.setEnabled(True)
        self.sample_text_edit.setEnabled(True)
        self.generate_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self._edited()

    def play_preview(self):
        if self.preview_path is not None and self.preview_path.is_file():
            self.player.setSource(QUrl.fromLocalFile(str(self.preview_path)))
            self.player.play()

    def save_voice(self):
        if self.busy or self.saved or not self._matches_preview() or self.preview_path is None:
            return
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, self.windowTitle(), self.tr_text("breeze_design_name_required", "Enter a name for the voice."))
            return
        snapshot = self.generated
        config = snapshot["config"]
        try:
            from .save_reference_voice import save_designed_voice
            from .voice_design_client import ServerDesignGallery
            gallery = ServerDesignGallery(self.client, self.gallery, self.draft_id)
            voice = save_designed_voice(
                self, gallery, "audiocpp", self.preview_path, name=name, translate=self.tr_text,
                language=config["language"], language_name=self.language_combo.currentText(),
                ref_text=snapshot["text"], short_description=config["instruction"],
                voice_style=config["instruction"], tags=["designed", self.TAG, "audiocpp"],
                ffmpeg_path=self.ffmpeg_path, min_duration_seconds=1, max_duration_seconds=60,
            )
        except Exception as exc:
            QMessageBox.warning(self, self.tr_text("import_failed", "Import failed"), str(exc))
            return
        if voice is None:
            return
        self.name_edit.setText(voice.name)
        self.saved = True
        self.save_button.setEnabled(False)
        self.status_label.setText(self.tr_text("omnivoice_design_saved_voice", "Saved voice: {name}").format(name=voice.name))
        self.voice_saved.emit(voice)

    def done(self, result):
        # The shared synthesis request must finish before its worker/UI are destroyed.
        if self.busy:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self._temporary.cleanup()
        super().done(result)
