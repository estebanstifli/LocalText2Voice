"""Edit a segment's synthesis snapshot without touching global engine settings."""
from copy import deepcopy
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QFormLayout, QLabel, QPlainTextEdit,
    QLineEdit, QComboBox, QCheckBox, QDoubleSpinBox, QSpinBox, QScrollArea,
    QWidget, QTabWidget, QMessageBox,
)

from app.tts.indextts_config import DEFAULTS as INDEX_DEFAULTS, EMOTIONS, validated_config as validate_index
from app.tts.audiocpp_config import MODELS, model_config, validated_config as validate_audiocpp
from app.tts.indextts_emotions import EMOTION_PRESETS
from app.tts.chatterbox_tags import prepare_text
from .widgets import FilePicker


HIDDEN = {
    "engine", "model", "model_repo", "cache_dir", "dependency_dir", "ffmpeg_path",
    "api_key", "headers", "auth_token", "voices", "model_settings", "chatterbox_emotion",
    "emotion_prompt", "emotion_revision", "emotion_source", "emo_vector",
    "base_url", "url", "endpoint", "request_template", "response_audio_path",
    "license", "commercial_use", "russian_silero_preprocessed", "voice_id",
}
POST_SPEED_ENGINES = {"chatterbox", "audiocpp", "qwen", "indextts", "omnivoice", "gemini", "elevenlabs"}

LABELS = {
    "reference_audio_path": "Reference audio", "reference_text": "Reference transcript",
    "emo_text": "Emotion description", "emo_audio_prompt": "Emotion reference audio",
    "emo_alpha": "Emotion intensity", "emotion_mode": "Emotion mode",
    "instruct": "Voice instructions", "instructions": "Voice instructions",
    "_postprocess_speed": "Playback speed", "_postprocess_volume_db": "Volume (dB)",
    "_postprocess_normalize_lufs": "Normalize (LUFS)", "text_chunk_size": "Internal chunk size",
}


class SegmentDetailDialog(QDialog):
    def __init__(self, text, config, voices, translate, parent=None):
        super().__init__(parent)
        self.tr_text = translate
        self.original_config = deepcopy(config)
        self.config = deepcopy(config)
        self.engine = str(config.get("engine", "piper"))
        if self.engine == "indextts":
            self.config = {**deepcopy(INDEX_DEFAULTS), **self.config}
            if self.config.get("emotion_prompt") and self.config.get("emotion_source") in {"custom", "text", "auto"}:
                self.config["emotion_mode"] = "auto" if self.config["emotion_source"] == "auto" else "text"
                self.config["emo_text"] = self.config["emotion_prompt"] if self.config["emotion_mode"] == "text" else ""
            if "instruct" in self.config:
                self.config["emo_text"] = self.config.pop("instruct")
                self.config["emotion_mode"] = "text" if self.config["emo_text"].strip() else "reference"
        elif self.engine == "audiocpp":
            self.config = model_config(self.config)
        if self.engine == "chatterbox" and config.get("model") == "turbo":
            text = prepare_text(text, config, lambda _: None)
            self.config.pop("chatterbox_emotion", None)
        if self.engine in POST_SPEED_ENGINES:
            self.config.setdefault("_postprocess_speed", self.config.get("speed", 1.0))
        self.config.setdefault("_postprocess_volume_db", 0.0)
        self.fields = {}
        self.setWindowTitle(translate("segment_detail", "Segment detail"))
        self.resize(720, 760)
        layout = QVBoxLayout(self)
        names = {"indextts": "IndexTTS-2.5", "chatterbox": "Chatterbox", "qwen": "Qwen3 TTS", "omnivoice": "OmniVoice", "f5_russian": "F5-TTS Russian"}
        title_text = names.get(self.engine, self.engine.capitalize())
        if self.engine == "audiocpp" and self.config.get("model") in MODELS:
            title_text = MODELS[self.config["model"]].name
        elif self.engine == "chatterbox":
            title_text += " · " + str(self.config.get("model", "")).replace("_", " ").title()
        title = QLabel(title_text)
        title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(title)
        help_label = QLabel(translate("segment_detail_help", "Changes apply only to this segment. Save, then regenerate and approve the new audio."))
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        general, general_form = self._tab(translate("segment_text_voice", "Text and voice"))
        self.text_edit = QPlainTextEdit(text)
        self.text_edit.setMinimumHeight(180)
        self.text_edit.setMaximumHeight(260)
        general_form.addRow(translate("segment_text", "Text"), self.text_edit)
        self.voice_combo = QComboBox()
        self.voice_combo.addItem(translate("custom", "Custom"), None)
        for name, value in voices:
            self.voice_combo.addItem(name, value)
        general_form.addRow(translate("voice", "Voice"), self.voice_combo)
        _, generation_form = self._tab(translate("segment_generation", "Generation"))
        emotion_form = None
        if self.engine == "indextts":
            _, emotion_form = self._tab(translate("higgs_category_emotion", "Emotions"))
        general_keys = {"voice", "speaker", "speaker_id", "language", "lang", "locale",
                        "reference_audio_path", "reference_text", "model_path", "config_path"}
        for key, value in self.config.items():
            if key in HIDDEN or (key.startswith('_') and not key.startswith('_postprocess_')):
                continue
            if self.engine in POST_SPEED_ENGINES and key == "speed":
                continue
            if not isinstance(value, (str, bool, int, float)):
                continue
            if any(secret in key.casefold() for secret in ("password", "token", "secret", "api_key")) and key not in {"max_tokens", "max_mel_tokens", "max_text_tokens_per_segment", "max_new_tokens"}:
                continue
            form = emotion_form if emotion_form and (key.startswith("emo_") or key in {"emotion_mode", "use_random"}) else general_form if key in general_keys else generation_form
            widget = self._field(key, value)
            form.addRow(translate("segment_param_" + key, LABELS.get(key, key.replace('_', ' ').capitalize())), widget)
            self.fields[key] = widget
        self.vector_fields = []
        if emotion_form:
            self.preset_combo = QComboBox()
            self.preset_combo.addItem(translate("custom", "Custom"), None)
            for name in EMOTION_PRESETS:
                self.preset_combo.addItem(translate("indextts_emotion_" + name, name.capitalize()), name)
            emotion_form.addRow(translate("preset", "Preset"), self.preset_combo)
            for name, value in zip(EMOTIONS, self.config["emo_vector"]):
                spin = QDoubleSpinBox()
                spin.setRange(0, 1.2)
                spin.setDecimals(3)
                spin.setSingleStep(.05)
                spin.setValue(value)
                self.vector_fields.append(spin)
                emotion_form.addRow(translate("indextts_emotion_" + name, name.capitalize()), spin)
            self.preset_combo.currentIndexChanged.connect(self._preset_changed)
            self.fields["emotion_mode"].currentIndexChanged.connect(self._emotion_mode_changed)
            self._emotion_mode_changed()
        self.voice_combo.currentIndexChanged.connect(self._voice_changed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(translate("segment_save", "Save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(translate("cancel", "Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _tab(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        return content, form

    def _field(self, key, value):
        if key == "emotion_mode":
            combo = QComboBox()
            for mode, label in (("reference", "Voice reference"), ("vector", "Emotion vector"), ("text", "Description"), ("auto", "Automatic from text"), ("audio", "Emotion reference audio")):
                combo.addItem(self.tr_text("segment_emotion_" + mode, label), mode)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            return combo
        if key in {"reference_audio_path", "emo_audio_prompt"}:
            picker = FilePicker(self.tr_text("browse", "Browse"), "Audio (*.wav *.mp3 *.flac *.ogg)")
            picker.set_path(value)
            return picker
        if isinstance(value, bool):
            widget = QCheckBox()
            widget.setChecked(value)
        elif isinstance(value, (int, float)):
            widget = QSpinBox() if isinstance(value, int) else QDoubleSpinBox()
            if isinstance(widget, QDoubleSpinBox):
                widget.setDecimals(5)
            widget.setRange(min(-1_000_000, value), max(1_000_000, value))
            widget.setValue(value)
        else:
            widget = QLineEdit(value)
        return widget

    def _voice_changed(self):
        values = self.voice_combo.currentData()
        if not values:
            return
        self.config.update(deepcopy(values))
        for key, value in values.items():
            widget = self.fields.get(key)
            if isinstance(widget, FilePicker):
                widget.set_path(value)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.setValue(value)

    def _preset_changed(self):
        name = self.preset_combo.currentData()
        if name:
            combo = self.fields["emotion_mode"]
            combo.setCurrentIndex(combo.findData("vector"))
            for widget, value in zip(self.vector_fields, EMOTION_PRESETS[name]):
                widget.setValue(value)

    def _emotion_mode_changed(self):
        mode = self.fields["emotion_mode"].currentData()
        self.fields["emo_text"].setEnabled(mode == "text")
        self.fields["emo_audio_prompt"].setEnabled(mode == "audio")
        self.preset_combo.setEnabled(mode == "vector")
        for widget in self.vector_fields:
            widget.setEnabled(mode == "vector")

    def values(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            raise ValueError(self.tr_text("review_empty_segment", "Segment text cannot be empty."))
        config = deepcopy(self.config)
        for key, widget in self.fields.items():
            if isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, FilePicker):
                value = str(widget.path() or "")
            elif isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QLineEdit):
                value = widget.text().strip()
            else:
                value = widget.value()
            config[key] = value
        if self.vector_fields:
            config["emo_vector"] = [w.value() for w in self.vector_fields]
            emotion_changed = any(config.get(k) != self.original_config.get(k) for k in ("emotion_mode", "emo_vector", "emo_text", "emo_audio_prompt", "emo_alpha"))
            if emotion_changed:
                for key in ("emotion_prompt", "emotion_revision", "emotion_source", "instruct", "instructions"):
                    config.pop(key, None)
            if config.get("emotion_mode") == "text" and not config.get("emo_text", "").strip():
                raise ValueError(self.tr_text("segment_emotion_required", "Enter an emotion description."))
            config = validate_index(config)
        elif self.engine == "audiocpp":
            config = validate_audiocpp(config)
        for key, value in config.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Invalid {key}")
        for key in ("speed", "_postprocess_speed"):
            if key in config and not .25 <= config[key] <= 4:
                raise ValueError(self.tr_text("segment_speed_range", "Speed must be between 0.25 and 4."))
        if config.get("emotion_mode") == "text" and not config.get("emo_text", "").strip():
            raise ValueError(self.tr_text("segment_emotion_required", "Enter an emotion description."))
        if self.engine in POST_SPEED_ENGINES:
            config["speed"] = config["_postprocess_speed"]
        return text, config

    def accept(self):
        try:
            self.values()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        super().accept()
