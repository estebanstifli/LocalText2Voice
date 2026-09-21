"""Provider-specific forms backed by the internal model capability catalog."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox, QLineEdit, QCheckBox, QSpinBox, QLabel
from app.core.direct_video_models import MODELS, PROVIDERS, DEFAULTS, normalize


class DirectVideoSettings(QWidget):
    changed = Signal()

    def __init__(self, provider, tr, parent=None):
        super().__init__(parent)
        self.provider, self.tr = provider, tr
        self._loading = True
        form = QFormLayout(self)
        self.model = QComboBox()
        for key, spec in MODELS[provider].items():
            self.model.addItem(spec.label, key)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.url = QLineEdit()
        self.resolution = QComboBox()
        self.audio = QCheckBox(tr("wan_generate_audio", "Generate audio"))
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 7200)
        self.timeout.setSuffix(" s")
        for label, field in ((tr("model", "Model"), self.model), (tr("api_key", "API key"), self.key),
                             ("API URL", self.url), (tr("video_storyboard_resolution", "Resolution"), self.resolution)):
            form.addRow(label, field)
        form.addRow(self.audio)
        form.addRow(tr("timeout", "Timeout"), self.timeout)
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setObjectName("helperLabel")
        form.addRow(self.help_label)
        self.model.currentIndexChanged.connect(self._model_changed)
        self.resolution.currentIndexChanged.connect(self._changed)
        self.audio.toggled.connect(self._changed)
        self.timeout.valueChanged.connect(self._changed)
        for field in (self.key, self.url):
            field.textChanged.connect(self._changed)
        self.set_configuration(DEFAULTS[provider])

    def _model_changed(self, *_):
        spec = MODELS[self.provider][self.model.currentData()]
        previous = self.resolution.currentData()
        self.resolution.blockSignals(True)
        self.resolution.clear()
        for resolution in spec.resolutions:
            self.resolution.addItem(resolution.upper(), resolution)
        self.resolution.setCurrentIndex(max(0, self.resolution.findData(previous)))
        self.resolution.blockSignals(False)
        self.audio.setEnabled(spec.audio_optional)
        if not spec.audio_optional:
            self.audio.setChecked(True)
        self._changed()

    def _changed(self, *_):
        spec = MODELS[self.provider][self.model.currentData()]
        index = max(0, self.resolution.currentIndex())
        rate = f"{spec.rates[index]:.4f}".rstrip("0").rstrip(".")
        text = self.tr("direct_video_price_help", "{provider} direct API. {resolution}: approximately ${rate}/generated second, before taxes and credits. Clips are fitted to the scene duration; audio uses the storyboard controls.",
                       provider=PROVIDERS[self.provider][0], resolution=self.resolution.currentText(), rate=rate)
        if self.provider == "ltx":
            text += " " + self.tr("ltx_video_limits", "Generates 6–20 seconds in 2-second steps at 24 fps. Start + scene image, None + one image, or None without images for text-to-video.")
        elif self.provider == "minimax":
            text += " " + self.tr("minimax_video_limits", "Generates 4–15 seconds at 768P, fitted to 720P. Audio is always generated. Start/end accepts one scene image; None accepts up to 9 references or text only.")
        else:
            text += " " + self.tr("byteplus_video_limits", "Requires a BytePlus LAS API key for Johor. Generates 4–30 seconds at 720P. Start accepts one scene image; None accepts up to 30 references or text only. LAS pricing differs from other Seedance services.")
        self.help_label.setText(text)
        if not self._loading:
            self.changed.emit()

    def set_configuration(self, value):
        self._loading = True
        config = normalize(value, self.provider)
        self.model.setCurrentIndex(self.model.findData(config["model"]))
        self._model_changed()
        self.key.setText(config["api_key"])
        self.url.setText(config["base_url"])
        self.resolution.setCurrentIndex(self.resolution.findData(config["resolution"]))
        self.audio.setChecked(config["audio"])
        self.timeout.setValue(config["timeout_seconds"])
        self._changed()
        self._loading = False

    def configuration(self):
        return {"model": self.model.currentData(), "api_key": self.key.text().strip(),
                "base_url": self.url.text().strip(), "resolution": self.resolution.currentData(),
                "audio": self.audio.isChecked(), "timeout_seconds": self.timeout.value()}
