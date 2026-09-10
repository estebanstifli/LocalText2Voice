from copy import deepcopy

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

from app.core.storyboard_credentials import protect, reveal
from app.core.storyboard_profiles import RUNPOD_DEFAULTS
from app.core.video_storyboard_runpod import check_connection
from app.core.runpod_video_models import RUNPOD_SIGNUP_URL, VIDEO_MODELS, model_id
from app.core.runpod_image_models import IMAGE_MODELS, image_model_id


class _CheckWorker(QObject):
    finished = Signal(str)

    def __init__(self, config, storage=False, tr=None):
        super().__init__()
        self.config = config
        self.storage = storage
        self.tr = tr or (lambda key, fallback: fallback)

    @Slot()
    def run(self):
        try:
            if self.storage:
                from app.core.storyboard_temporary_storage import register, request
                register(self.config)
                result = request(self.config, "/v1/status")
                self.finished.emit("Temporary storage connected. " + ("New uploads are paused." if not result["available"] else
                    f"Images today: {result['usage']['uploads']}/{result['limits']['daily_uploads']}."))
                return
            check_connection({"runpod": self.config})
            self.finished.emit(self.tr("runpod_auth_verified", "Runpod authentication verified for all three endpoints. No generation was submitted; generation permissions, models and billing still need a generation test."))
        except Exception as exc:
            self.finished.emit(str(exc))


class RunpodSettingsWidget(QWidget):
    changed = Signal()

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr
        self.thread = None
        self._secret_cache = {}
        self._restoring = False
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        form = QFormLayout()
        layout.addLayout(form)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("RUNPOD_API_KEY")
        form.addRow("Runpod API key", self.key)
        self.model_note = QLabel("Z-Image-Turbo · Qwen Image Edit 2511 · Wan 2.6 I2V")
        self.model_note.setWordWrap(True)
        form.addRow(self.model_note)
        self.image_model = QComboBox()
        for endpoint, model in IMAGE_MODELS.items():
            self.image_model.addItem(model["name"], endpoint)
        self.image_model.addItem(tr("runpod_custom_endpoint", "Custom endpoint (Advanced)"), "custom")
        form.addRow(tr("runpod_image_model", "Image model"), self.image_model)
        self.image_model.setToolTip(tr("runpod_image_model_help", "P-Image uses the nearest supported aspect ratio, not an exact pixel resolution."))
        self.video_model = QComboBox()
        for endpoint, model in VIDEO_MODELS.items():
            self.video_model.addItem(model["name"], endpoint)
        self.video_model.addItem(tr("runpod_custom_endpoint", "Custom endpoint (Advanced)"), "custom")
        form.addRow(tr("runpod_video_model", "Video model"), self.video_model)
        self.size = QComboBox()
        self.size.addItem("720p", "1280*720")
        self.size.addItem("1080p", "1920*1080")
        form.addRow(tr("runpod_video_resolution", "Video resolution"), self.size)
        self.cost_label = QLabel(tr("runpod_rates", "Estimated rates: image $0.005 · edit $0.02 · video $0.10/s at 720p or $0.15/s at 1080p. Video is generated in 5, 10 or 15 seconds. Verify current Runpod prices; retries may add cost."))
        self.cost_label.setWordWrap(True)
        form.addRow(self.cost_label)
        note = QLabel(tr("runpod_reference_storage_help", "Runpod-generated images can be reused directly. Choose temporary storage below for local references. Analysis uses the LLM selected below; final video assembly runs on this computer."))
        note.setWordWrap(True)
        form.addRow(note)
        links = QLabel('<a href="https://console.runpod.io/user/settings">Runpod API keys</a> · <a href="https://docs.runpod.io/public-endpoints/reference">Models and prices</a>')
        links.setOpenExternalLinks(True)
        form.addRow(links)
        self.signup = QLabel(f'<a href="{RUNPOD_SIGNUP_URL}">' + tr("runpod_signup_affiliate", "Create a Runpod account · affiliate link supporting LocalText2Voice") + '</a>')
        self.signup.setOpenExternalLinks(True)
        self.signup.setWordWrap(True)
        self.signup.setStyleSheet("QLabel { background: #15345c; color: white; border: 1px solid #3885d6; border-radius: 6px; padding: 12px; font-weight: 600; }")
        self.signup.setText(self.signup.text().replace('<a href=', '<a style="color: #8dc9ff; text-decoration: underline;" href='))
        form.insertRow(0, self.signup)
        self.check = QPushButton(tr("runpod_check", "Check connection (no generation)"))
        self.check.clicked.connect(self._check)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow(self.check)
        form.addRow(self.status)
        self.jobs = QPushButton(tr("runpod_jobs", "Runpod jobs"))
        self.jobs.clicked.connect(self._jobs)
        form.addRow(self.jobs)
        storage_group = QGroupBox(tr("runpod_reference_storage", "Storage for local references"))
        storage_form = QFormLayout(storage_group)
        self.reference_storage = QComboBox()
        self.reference_storage.addItem(tr("runpod_storage_disabled", "Existing Runpod images only (no uploads)"), "disabled")
        self.reference_storage.addItem(tr("runpod_storage_managed", "Free temporary storage · Beta"), "managed")
        self.reference_storage.addItem(tr("runpod_storage_own", "My S3 / R2 storage"), "s3")
        storage_form.addRow(self.reference_storage)
        self.managed_panel = QWidget()
        managed_form = QFormLayout(self.managed_panel)
        self.storage_notice = QLabel(tr("runpod_storage_automatic", "Automatic setup for this installation. Before the first use, you will be asked to allow temporary image uploads to Cloudflare. Images expire after 24 hours. No account or beta code is needed."))
        self.storage_notice.setWordWrap(True)
        managed_form.addRow(self.storage_notice)
        self.storage_check = QPushButton(tr("runpod_storage_check", "Check temporary storage"))
        self.storage_check.clicked.connect(lambda: self._check(storage=True))
        managed_form.addRow(self.storage_check)
        storage_form.addRow(self.managed_panel)
        self.own_storage_note = QLabel(tr("runpod_storage_own_note", "Enter your bucket and credentials under Advanced. Use a service supporting signed URLs, such as Cloudflare R2 or AWS S3. Runpod network-volume S3 does not support signed URLs."))
        self.own_storage_note.setWordWrap(True)
        storage_form.addRow(self.own_storage_note)
        layout.addWidget(storage_group)
        self.advanced_toggle = QCheckBox(tr("advanced", "Advanced"))
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        self.fields = {}
        self.fields["temporary_storage_url"] = QLineEdit()
        from app.core.storyboard_temporary_storage import DEFAULT_SERVICE_URL
        self.fields["temporary_storage_url"].setPlaceholderText(DEFAULT_SERVICE_URL)
        advanced.addRow(tr("runpod_storage_service", "Temporary storage service URL"), self.fields["temporary_storage_url"])
        for name, label in (("image_endpoint", "Image endpoint · Z-Image contract"), ("edit_endpoint", "Edit endpoint · Qwen Edit contract"), ("video_endpoint", "Video endpoint · selected public model or custom Wan 2.6 contract")):
            self.fields[name] = QLineEdit()
            advanced.addRow(label, self.fields[name])
        self.edit_size = QComboBox()
        for size in ("1280*720", "720*1280", "1024*1024", "1024*1280", "1280*1024", "1280*1280", "1280*1536", "1536*1080"):
            self.edit_size.addItem(size.replace("*", " × "), size)
        advanced.addRow(tr("runpod_edit_size", "Edited image size"), self.edit_size)
        self.preserve_size = QCheckBox(tr("runpod_edit_preserve_size", "Keep the first reference image's dimensions"))
        advanced.addRow(self.preserve_size)
        self.preserve_size.toggled.connect(lambda checked: self.edit_size.setEnabled(not checked))
        self.preserve_size.toggled.connect(self._changed)
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 7200)
        self.timeout.setSuffix(" s")
        advanced.addRow(tr("timeout", "Timeout"), self.timeout)
        self.expansion = QCheckBox(tr("runpod_expand", "Expand the video prompt"))
        advanced.addRow(self.expansion)
        self.s3_panel = QWidget()
        s3_form = QFormLayout(self.s3_panel)
        storage = QLabel(tr("runpod_s3_help", "S3 storage for local references: private objects are uploaded under localtext2voice/references/ and read through temporary URLs. Configure a bucket lifecycle rule to delete old references. Storage charges may apply. S3 credentials are separate from your Runpod API key."))
        storage.setWordWrap(True)
        s3_form.addRow(storage)
        for name, label in (("s3_endpoint", "S3 endpoint URL"), ("s3_bucket", "Bucket"), ("s3_region", "Region"), ("s3_access_key", "S3 access key"), ("s3_secret_encrypted", "S3 secret key")):
            edit = QLineEdit()
            if name == "s3_secret_encrypted":
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.fields[name] = edit
            s3_form.addRow(label, edit)
        advanced.addRow(self.s3_panel)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        for edit in (self.key, *self.fields.values()):
            edit.textChanged.connect(self._changed)
        self.size.currentIndexChanged.connect(self._changed)
        self.edit_size.currentIndexChanged.connect(self._changed)
        self.timeout.valueChanged.connect(self._changed)
        self.expansion.toggled.connect(self._changed)
        self.reference_storage.currentIndexChanged.connect(self._storage_changed)
        self.video_model.currentIndexChanged.connect(self._video_model_changed)
        self.image_model.currentIndexChanged.connect(self._image_model_changed)
        self.fields["image_endpoint"].textChanged.connect(self._sync_image_model)
        self.fields["video_endpoint"].textChanged.connect(self._sync_video_model)
        self.size.currentIndexChanged.connect(self._update_cost)
        self.set_configuration({})

    def _image_model_changed(self, *_):
        endpoint = self.image_model.currentData()
        if endpoint != "custom":
            self.fields["image_endpoint"].setText(endpoint)
        else:
            self.advanced_toggle.setChecked(True)
        self._changed()

    def _sync_image_model(self, *_):
        endpoint = image_model_id({"image_endpoint": self.fields["image_endpoint"].text()})
        self.image_model.blockSignals(True)
        self.image_model.setCurrentIndex(self.image_model.findData(endpoint if endpoint in IMAGE_MODELS else "custom"))
        self.image_model.blockSignals(False)
        self._update_cost()

    def _video_model_changed(self, *_):
        endpoint = self.video_model.currentData()
        if endpoint != "custom":
            self.fields["video_endpoint"].setText(endpoint)
        else:
            self.advanced_toggle.setChecked(True)
        self._changed()

    def _sync_video_model(self, *_):
        endpoint = model_id({"video_endpoint": self.fields["video_endpoint"].text()})
        self.video_model.blockSignals(True)
        self.video_model.setCurrentIndex(self.video_model.findData(endpoint if endpoint in VIDEO_MODELS else "custom"))
        self.video_model.blockSignals(False)
        sizes = VIDEO_MODELS.get(endpoint, VIDEO_MODELS["wan-2-6-i2v"])["rates"]
        previous = self.size.currentData()
        self.size.blockSignals(True)
        self.size.clear()
        for size in sizes:
            self.size.addItem("720p" if size == "1280*720" else "1080p", size)
        self.size.setCurrentIndex(max(0, self.size.findData(previous)))
        self.size.blockSignals(False)
        self._update_cost()

    def _update_cost(self, *_):
        model = VIDEO_MODELS.get(model_id({"video_endpoint": self.fields["video_endpoint"].text()}))
        image = IMAGE_MODELS.get(image_model_id({"image_endpoint": self.fields["image_endpoint"].text()}), {})
        self.model_note.setText(image.get("name", "Custom image") + " · Qwen Image Edit 2511 · " + (model["name"] if model else self.tr("runpod_custom_endpoint", "Custom endpoint (Advanced)")))
        rate = model["rates"].get(self.size.currentData()) if model else None
        amount = f"${rate:.2f}/s · 5s ≈ ${rate * 5:.2f}" if rate else self.tr("runpod_cost_unknown", "Depends on the endpoint")
        image_price = f"${image['price']:.3f}" if image else self.tr("runpod_cost_unknown", "Depends on the endpoint")
        self.cost_label.setText(self.tr("runpod_selected_model_rates", "Image {image_price} · edit $0.02 · selected video: {price}. Indicative prices; verify current Runpod prices.", image_price=image_price, price=amount))

    def _storage_changed(self, *_):
        mode = self.reference_storage.currentData()
        self.managed_panel.setVisible(mode == "managed")
        self.own_storage_note.setVisible(mode == "s3")
        self.s3_panel.setVisible(mode == "s3")
        if mode == "s3" and not self._restoring:
            self.advanced_toggle.setChecked(True)
        self._changed()

    def _changed(self, *_):
        if not self._restoring:
            self.changed.emit()

    def _encrypted(self, key, plain):
        previous = self._secret_cache.get(key)
        if previous and previous[0] == plain:
            return previous[1]
        encrypted = protect(plain)
        self._secret_cache[key] = (plain, encrypted)
        return encrypted

    def configuration(self):
        config = deepcopy(RUNPOD_DEFAULTS)
        config.update({key: edit.text().strip() for key, edit in self.fields.items()})
        config["api_key_encrypted"] = self._encrypted("api_key", self.key.text().strip())
        config["s3_secret_encrypted"] = self._encrypted("s3_secret", self.fields["s3_secret_encrypted"].text().strip())
        config["temporary_storage_token_encrypted"] = ""
        config["edit_preserve_size"] = self.preserve_size.isChecked()
        config["reference_storage"] = self.reference_storage.currentData()
        config.update(video_size=self.size.currentData(), edit_size=self.edit_size.currentData(), timeout_seconds=self.timeout.value(), prompt_expansion=self.expansion.isChecked())
        return config

    def set_configuration(self, value):
        config = {**RUNPOD_DEFAULTS, **value}
        self._restoring = True
        try:
            for key, edit in self.fields.items():
                if key != "s3_secret_encrypted":
                    edit.setText(str(config[key]))
            for cache_key, field, key in (("api_key", self.key, "api_key_encrypted"), ("s3_secret", self.fields["s3_secret_encrypted"], "s3_secret_encrypted")):
                try:
                    plain = reveal(config[key])
                except (ValueError, RuntimeError):
                    plain = ""
                    self.status.setText("Saved credential belongs to another Windows account. Enter it again.")
                self._secret_cache[cache_key] = (plain, config[key])
                field.setText(plain)
            self.size.setCurrentIndex(max(0, self.size.findData(config["video_size"])))
            self.edit_size.setCurrentIndex(max(0, self.edit_size.findData(config["edit_size"])))
            self.preserve_size.setChecked(bool(config["edit_preserve_size"]))
            self.edit_size.setEnabled(not self.preserve_size.isChecked())
            self.timeout.setValue(int(config["timeout_seconds"]))
            self.expansion.setChecked(bool(config["prompt_expansion"]))
            mode = config["reference_storage"]
            if mode == "auto":
                mode = "s3" if config.get("s3_endpoint") else "managed"
            self.reference_storage.setCurrentIndex(max(0, self.reference_storage.findData(mode)))
            self._storage_changed()
            self._sync_video_model()
            self._sync_image_model()
        finally:
            self._restoring = False

    def _jobs(self):
        from app.ui.storyboard_runpod_jobs import RunpodJobsDialog
        RunpodJobsDialog({"runpod": self.configuration()}, self.tr, self).exec()

    def _check(self, _checked=False, *, storage=False):
        if self.thread:
            return
        if storage:
            from app.ui.storyboard_storage_consent import ensure_storage_consent
            if not ensure_storage_consent(self.configuration(), self, self.tr):
                return
        self.check.setEnabled(False)
        self.storage_check.setEnabled(False)
        self.status.setText(self.tr("video_storyboard_checking", "Checking connection..."))
        self.thread = QThread(self)
        self.worker = _CheckWorker(self.configuration(), storage=storage, tr=self.tr)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.status.setText)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _finished(self):
        self.thread = None
        self.worker = None
        self.check.setEnabled(True)
        self.storage_check.setEnabled(True)
