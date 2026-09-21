from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QWidget

from app.core.video_storyboard_video_dashscope import DASHSCOPE_DEFAULTS, WAN_MODEL, WAN_MODELS


class DashScopeVideoSettings(QWidget):
    changed = Signal()

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.tr_text = tr
        self.model = QComboBox()
        for model, info in WAN_MODELS.items():
            self.model.addItem(info["label"], model)
        form.addRow(tr("model", "Model"), self.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.url = QLineEdit()
        self.resolution = QComboBox()
        self.resolution.addItems(["720P", "1080P"])
        self.audio = QCheckBox(tr("wan_generate_audio", "Generate audio"))
        self.audio.toggled.connect(self._audio_changed)
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 7200)
        self.timeout.setSuffix(" s")
        form.addRow(tr("api_key", "API key"), self.key)
        form.addRow("DashScope URL", self.url)
        form.addRow(tr("video_storyboard_resolution", "Resolution"), self.resolution)
        form.addRow(self.audio)
        form.addRow(tr("timeout", "Timeout"), self.timeout)
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setObjectName("helperLabel")
        form.addRow(self.help_label)
        self.model.currentIndexChanged.connect(self._model_changed)
        self._requested_audio = False
        self._sync_audio()
        self._update_help()
        for edit in (self.key, self.url):
            edit.textChanged.connect(self._changed)
        self.resolution.currentIndexChanged.connect(self._changed)
        self.timeout.valueChanged.connect(self._changed)

    def _update_help(self):
        model = self.model.currentData() or WAN_MODEL
        info = WAN_MODELS[model]
        price = ("0.05" if self.audio.isChecked() else "0.025") if model == WAN_MODEL else info["price_720"]
        self.help_label.setText(self.tr_text("wan_models_audio_help", "One starting image. Generates 2–{maximum} seconds, then fits the clip to the scene. Singapore 720P price: ${price}/second, before taxes and credits. Clip audio uses the storyboard audio controls.", maximum=info["max_seconds"], price=price))
        key, note = ("wan27_native_audio", "Wan 2.7 always generates audio. Mute it with the scene audio controls.") if model == "wan2.7-i2v" else ("wan_audio_price", "Disabling audio reduces the API price only for Wan 2.6 Flash.")
        self.help_label.setText(self.help_label.text() + " " + self.tr_text(key, note))

    def _sync_audio(self):
        self.audio.blockSignals(True)
        self.audio.setEnabled(self.model.currentData() != "wan2.7-i2v")
        self.audio.setChecked(self._requested_audio if self.audio.isEnabled() else True)
        self.audio.blockSignals(False)

    def _audio_changed(self, checked):
        self._requested_audio = checked
        self._update_help()
        self.changed.emit()

    def _model_changed(self, *_):
        self.resolution.setCurrentText("720P")
        self._sync_audio()
        self._update_help()
        self.changed.emit()

    def _changed(self, *_):
        self.changed.emit()

    def set_configuration(self, config):
        config = {**DASHSCOPE_DEFAULTS, **config}
        self._requested_audio = bool(config["audio"])
        index = self.model.findData(config["model"])
        self.model.setCurrentIndex(max(0, index))
        self.key.setText(config["api_key"])
        self.url.setText(config["base_url"])
        self.resolution.setCurrentText(config["resolution"])
        self.timeout.setValue(config["timeout_seconds"])
        self._sync_audio()
        self._update_help()

    def configuration(self):
        return {"model": self.model.currentData(), "audio": self._requested_audio, "api_key": self.key.text().strip(), "base_url": self.url.text().strip(),
                "resolution": self.resolution.currentText(), "timeout_seconds": self.timeout.value()}
