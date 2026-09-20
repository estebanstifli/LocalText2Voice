from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QWidget

from app.core.litellm_video_models import LITELLM_VIDEO_DEFAULTS, VEO_MODELS


class LiteLLMVideoSettings(QWidget):
    changed = Signal()

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.model = QComboBox()
        for label, value in VEO_MODELS:
            self.model.addItem(label, value)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.url = QLineEdit()
        self.url.setPlaceholderText(tr("veo_proxy_hint", "Optional LiteLLM proxy URL; leave empty for Gemini via LiteLLM SDK"))
        self.resolution = QComboBox()
        self.resolution.addItems(["720p", "1080p"])
        self.aspect = QComboBox()
        self.aspect.addItems(["16:9", "9:16"])
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 7200)
        self.timeout.setSuffix(" s")
        form.addRow(tr("model", "Model"), self.model)
        form.addRow(tr("api_key", "API key"), self.key)
        form.addRow("URL", self.url)
        form.addRow(tr("video_storyboard_resolution", "Resolution"), self.resolution)
        form.addRow(tr("aspect_ratio", "Aspect ratio"), self.aspect)
        form.addRow(tr("timeout", "Timeout"), self.timeout)
        help_label = QLabel(tr("veo_video_help", "Veo 3.1 and Fast support asset references with None + Img Ref (8 seconds; Google documents up to 3 images). Lite supports a starting image. Clips retain generated audio and are fitted to the scene duration. A proxy must enable Gemini pass-through (/gemini)."))
        help_label.setWordWrap(True)
        help_label.setObjectName("helperLabel")
        form.addRow(help_label)
        for combo in (self.model, self.resolution, self.aspect):
            combo.currentIndexChanged.connect(self._notify_changed)
        for edit in (self.key, self.url):
            edit.textChanged.connect(self._notify_changed)
        self.timeout.valueChanged.connect(self._notify_changed)

    def _notify_changed(self, *_args):
        # Qt fields emit their new value; our settings signal has no arguments.
        self.changed.emit()

    def set_configuration(self, config):
        value = {**LITELLM_VIDEO_DEFAULTS, **config}
        index = self.model.findData(value["model"])
        if index < 0:
            self.model.addItem(value["model"], value["model"])
            index = self.model.count() - 1
        self.model.setCurrentIndex(index)
        self.key.setText(value["api_key"])
        self.url.setText(value["base_url"])
        self.resolution.setCurrentText(value["resolution"])
        self.aspect.setCurrentText(value["aspect_ratio"])
        self.timeout.setValue(int(value["timeout_seconds"]))

    def configuration(self):
        return {"model": self.model.currentData(), "api_key": self.key.text().strip(),
                "base_url": self.url.text().strip(), "resolution": self.resolution.currentText(),
                "aspect_ratio": self.aspect.currentText(), "timeout_seconds": self.timeout.value()}
