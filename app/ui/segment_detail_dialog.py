"""Edit a segment's synthesis snapshot without touching global engine settings."""
from copy import deepcopy
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QFormLayout, QLabel, QPlainTextEdit,
    QLineEdit, QComboBox, QCheckBox, QDoubleSpinBox, QSpinBox, QScrollArea,
    QWidget, QTabWidget, QMessageBox, QSizePolicy, QHBoxLayout,
)

from app.tts.indextts_config import DEFAULTS as INDEX_DEFAULTS, EMOTIONS, validated_config as validate_index
from app.tts.audiocpp_config import MODELS, BREEZE_MODEL, BREEZE_FIELDS, VOXCPM_MODELS, model_field_visible, model_config, validated_config as validate_audiocpp
from app.tts.indextts_emotions import EMOTION_PRESETS
from app.tts.chatterbox_tags import prepare_text
from .widgets import FilePicker
from app.core.segment_engine import identity, migrate, validate_destination, POST_SPEED_ENGINES


HIDDEN = {
    "engine", "model", "model_repo", "cache_dir", "dependency_dir", "ffmpeg_path",
    "api_key", "headers", "auth_token", "voices", "model_settings", "chatterbox_emotion",
    "emotion_prompt", "emotion_revision", "emotion_source", "emo_vector",
    "base_url", "url", "endpoint", "request_template", "response_audio_path",
    "license", "commercial_use", "russian_silero_preprocessed", "voice_id", "generation_mode", "mode",
}

ENGINE_MESSAGES = {
    'segment_engine_reset': 'Generation parameters use the destination engine settings. This change affects only this segment.',
    'segment_engine_reference': 'The reference audio and transcript are retained. The voice may sound different with this engine.',
    'segment_engine_choose_reference': 'Select a reference audio to retain the character voice.',
    'segment_engine_choose_voice': 'Select a voice supported by this engine.',
    'segment_engine_emotion_kept': 'The emotion description is retained; its interpretation depends on the new engine.',
    'segment_engine_emotion_removed': 'The previous emotion controls cannot be transferred. Configure the available controls below.',
    'segment_engine_tags_removed': 'Native vocal/emotion tags from the previous engine were removed from this draft.',
    'segment_engine_language': 'This model does not support the selected language, or requires an explicit language. Select a compatible model or language.',
    'segment_engine_voice_language': 'Choose a voice in the original segment language.',
    'segment_engine_missing_reference': 'Select an existing reference audio file for this voice.',
    'segment_engine_missing_transcript': 'Enter the exact transcript of the reference audio.',
    'segment_engine_invalid_wav': 'This engine requires a non-empty, uncompressed PCM WAV reference.',
}

LABELS = {
    "reference_audio_path": "Reference audio", "reference_text": "Reference transcript",
    "emo_text": "Emotion description", "emo_audio_prompt": "Emotion reference audio",
    "emo_alpha": "Emotion intensity", "emotion_mode": "Emotion mode",
    "instruction": "Voice / emotion instruction",
    "num_inference_steps": "Diffusion steps", "guidance_scale": "Guidance scale", "depth_temperature": "Depth temperature",
    "instruct": "Voice instructions", "instructions": "Voice instructions",
    "_postprocess_speed": "Playback speed", "_postprocess_volume_db": "Volume (dB)",
    "_postprocess_normalize_lufs": "Normalize (LUFS)", "text_chunk_size": "Internal chunk size",
}


class SegmentDetailDialog(QDialog):
    def __init__(self, text, config, voices, translate, parent=None, engine_options=None):
        super().__init__(parent)
        self.tr_text = translate
        self.original_config = deepcopy(config)
        self.engine_options = engine_options or []
        self._drafts = {}
        self._active_option = 0
        self._configure(text, config)
        self.setWindowTitle(translate("segment_detail", "Segment detail"))
        self.resize(864, 760)
        layout = QVBoxLayout(self)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem(self._engine_label(self.config), {"config": deepcopy(config), "voices": voices})
        for option in self.engine_options:
            if identity(option["config"]) != identity(config):
                self.engine_combo.addItem(option["label"], option)
        header = QHBoxLayout()
        header.addWidget(QLabel(translate("segment_engine", "TTS engine")))
        header.addWidget(self.engine_combo, 1)
        layout.addLayout(header)
        self.compatibility_label = QLabel()
        self.compatibility_label.setWordWrap(True)
        self.compatibility_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.compatibility_label)
        help_label = QLabel(translate("segment_detail_help", "Changes apply only to this segment. Save, then regenerate and approve the new audio."))
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._build_fields(text, voices)
        self.engine_combo.currentIndexChanged.connect(self._engine_changed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(translate("segment_save", "Save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(translate("cancel", "Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _configure(self, text, config):
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
        self._prepared_text = text

    @staticmethod
    def _engine_label(config):
        engine = str(config.get("engine", "piper"))
        if engine == "audiocpp" and config.get("model") in MODELS:
            return MODELS[config["model"]].name
        return engine.capitalize() + (" · " + str(config["model"]) if config.get("model") else "")

    def _build_fields(self, text, voices):
        translate = self.tr_text
        text = self._prepared_text
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
        breeze = self.engine == "audiocpp" and self.config.get("model") == BREEZE_MODEL
        voxcpm = self.engine == "audiocpp" and self.config.get("model") in VOXCPM_MODELS
        if self.engine == "indextts" or breeze or voxcpm:
            _, emotion_form = self._tab(translate("higgs_category_emotion", "Emotions"))
        general_keys = {"voice", "speaker", "speaker_id", "language", "lang", "locale",
                        "reference_audio_path", "reference_text", "model_path", "config_path"}
        for key, value in self.config.items():
            if self.engine == 'qwen' and self.config.get('generation_mode') != 'voice_clone' and key in {'reference_audio_path', 'reference_text'}:
                continue
            if self.engine == 'qwen' and self.config.get('generation_mode') == 'voice_clone' and key == 'speaker':
                continue
            if self.engine == 'omnivoice' and self.config.get('mode', 'clone') == 'clone' and key == 'instruct':
                continue
            if self.engine == "audiocpp" and not model_field_visible(self.config["model"], key):
                continue
            if key in HIDDEN or (key.startswith('_') and not key.startswith('_postprocess_')):
                continue
            if self.engine in POST_SPEED_ENGINES and key == "speed":
                continue
            if not isinstance(value, (str, bool, int, float)):
                continue
            if any(secret in key.casefold() for secret in ("password", "token", "secret", "api_key")) and key not in {"max_tokens", "max_mel_tokens", "max_text_tokens_per_segment", "max_new_tokens"}:
                continue
            form = emotion_form if emotion_form and (key.startswith("emo_") or key in {"emotion_mode", "use_random", "instruction"}) else general_form if key in general_keys else generation_form
            widget = self._field(key, value)
            form.addRow(translate("segment_param_" + key, LABELS.get(key, key.replace('_', ' ').capitalize())), widget)
            if isinstance(widget, FilePicker):
                form.labelForField(widget).setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.fields[key] = widget
        self.vector_fields = []
        if self.engine == "indextts":
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
        if breeze or voxcpm:
            from app.tts.breeze_tags import EMOTION_INSTRUCTIONS, STYLE_INSTRUCTIONS
            if voxcpm:
                from app.tts.voxcpm_tags import EMOTION_INSTRUCTIONS
                STYLE_INSTRUCTIONS = {}
            self.breeze_preset_combo = QComboBox()
            self.breeze_preset_combo.addItem(translate("custom", "Custom"), None)
            self.breeze_preset_combo.addItem(translate("indextts_emotion_off", "Off"), "" if voxcpm else "Speak clearly and naturally.")
            for name, instruction in {**EMOTION_INSTRUCTIONS, **STYLE_INSTRUCTIONS}.items():
                label_key = "breeze_style_" + name.lower() if name in STYLE_INSTRUCTIONS else "indextts_emotion_" + name
                self.breeze_preset_combo.addItem(translate(label_key, name.capitalize()), instruction)
            self.breeze_preset_combo.currentIndexChanged.connect(
                lambda: self.fields["instruction"].setPlainText(self.breeze_preset_combo.currentData())
                if self.breeze_preset_combo.currentData() is not None else None)
            emotion_form.addRow(translate("preset", "Preset"), self.breeze_preset_combo)
            help_label = QLabel(translate("breeze_instruction_help", "Describe emotion, pace or voice in the same language as the text (English or Chinese). Instructions are sent separately; vocal events such as (laugh) belong in the text."))
            if voxcpm:
                help_label.setText(translate("voxcpm_instruction_help", "Describe the emotion or delivery without parentheses. The instruction is prefixed to every chunk automatically. Off leaves the instruction empty."))
            help_label.setWordWrap(True)
            emotion_form.addRow(help_label)
        self.voice_combo.currentIndexChanged.connect(self._voice_changed)

    def _engine_changed(self, index):
        # Read raw fields so an unfinished reference/emotion does not trap the user.
        text, config = self._read_fields()
        self._drafts[self._active_option] = (text, config, self.compatibility_label.text())
        if index in self._drafts:
            text, config, notice = self._drafts[index]
        else:
            option = self.engine_combo.itemData(index)
            text, config, notices = migrate(text, config, option["config"])
            notice = "\n".join(self.tr_text(key, ENGINE_MESSAGES[key]) for key in notices)
        self._active_option = index
        while self.tabs.count():
            page = self.tabs.widget(0)
            self.tabs.removeTab(0)
            page.deleteLater()
        self._configure(text, config)
        self._build_fields(text, self.engine_combo.itemData(index)["voices"])
        self.compatibility_label.setText(notice)

    def _tab(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        form = QFormLayout()
        content_layout.addLayout(form)
        content_layout.addStretch(1)
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
            picker.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            return picker
        if key == "instruction":
            widget = QPlainTextEdit(value)
            widget.setFixedHeight(widget.fontMetrics().lineSpacing() * 3 + 18)
            return widget
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
        values = {key: value for key, value in values.items() if key not in {'engine', 'model', 'speed'}}
        self.config.update(deepcopy(values))
        for key, value in values.items():
            widget = self.fields.get(key)
            if isinstance(widget, FilePicker):
                widget.set_path(value)
            elif isinstance(widget, QPlainTextEdit):
                widget.setPlainText(str(value))
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

    def _read_fields(self):
        text = self.text_edit.toPlainText().strip()
        config = deepcopy(self.config)
        for key, widget in self.fields.items():
            if isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, FilePicker):
                value = str(widget.path() or "")
            elif isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QPlainTextEdit):
                value = widget.toPlainText().strip()
            elif isinstance(widget, QLineEdit):
                value = widget.text().strip()
            else:
                value = widget.value()
            config[key] = value
        if self.vector_fields:
            config["emo_vector"] = [w.value() for w in self.vector_fields]
        return text, config

    def values(self):
        text, config = self._read_fields()
        if not text:
            raise ValueError(self.tr_text("review_empty_segment", "Segment text cannot be empty."))
        if self.vector_fields:
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
        if identity(config) != identity(self.original_config):
            try:
                validate_destination(config, self.original_config.get('language') or self.original_config.get('lang'))
            except ValueError as exc:
                raise ValueError(self.tr_text(str(exc), ENGINE_MESSAGES.get(str(exc), str(exc)))) from exc
        return text, config

    def accept(self):
        try:
            self.values()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        super().accept()
