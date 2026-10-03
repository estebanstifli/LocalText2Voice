from copy import deepcopy

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

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
        self._role = None
        self._video_adapter = "public"
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
        image_help = QLabel(tr("runpod_qwen_generation_help", "Qwen Image Edit 2511 uses 1–3 character, location or object references. Add references before generating; text-only scenes need a T2I model. Output uses the nearest supported size (1536 × 1080 for a 16:9 preset). Local images use the reference storage selected below."))
        image_help.setWordWrap(True)
        form.addRow(image_help)
        self.image_model.setToolTip(tr("runpod_image_model_help", "P-Image uses the nearest supported aspect ratio, not an exact pixel resolution."))
        self.video_model = QComboBox()
        for endpoint, model in VIDEO_MODELS.items():
            self.video_model.addItem(model["name"], endpoint)
        self.video_model.addItem(tr("runpod_custom_endpoint", "Custom endpoint (Advanced)"), "custom")
        self.video_model.addItem(tr("h3_private_endpoint", "MiniMax H3 · private ComfyUI endpoint"), "h3_private")
        form.addRow(tr("runpod_video_model", "Video model"), self.video_model)
        self.h3_panel = QGroupBox(tr("h3_private_endpoint", "MiniMax H3 · private ComfyUI endpoint"))
        h3_form = QFormLayout(self.h3_panel)
        self.h3_preset = QComboBox()
        self.h3_preset.addItem(tr("h3_fast", "Fast · Turbo 4 steps"), "fast")
        self.h3_preset.addItem(tr("h3_normal", "Normal · 20 steps"), "normal")
        h3_form.addRow(tr("h3_preset", "H3 preset"), self.h3_preset)
        self.gpu_hourly = QDoubleSpinBox()
        self.gpu_hourly.setRange(0, 100)
        self.gpu_hourly.setDecimals(4)
        self.gpu_hourly.setSuffix(" USD/h")
        self.gpu_hourly.setSpecialValueText(tr("era_basis_unknown", "Unknown"))
        h3_form.addRow(tr("h3_gpu_rate", "GPU rate (estimate only)"), self.gpu_hourly)
        h3_note = QLabel(tr("h3_help", "Requires a compatible private H3 endpoint, not the commercial MiniMax API. Enter its ID under Advanced. One starting image, 1–10 seconds, native resolution and audio. References go directly to your worker; mute unwanted audio in the storyboard. Set Max workers to at least 1 remotely. The app does not start or stop GPU workers."))
        h3_note.setWordWrap(True)
        h3_form.addRow(h3_note)
        form.addRow(self.h3_panel)
        self.size = QComboBox()
        self.size.addItem("720p", "1280*720")
        self.size.addItem("1080p", "1920*1080")
        form.addRow(tr("runpod_video_resolution", "Video resolution"), self.size)
        self.cost_label = QLabel(tr("runpod_rates", "Estimated rates: image $0.005 · edit $0.02 · video $0.10/s at 720p or $0.15/s at 1080p. Video is generated in 5, 10 or 15 seconds. Verify current Runpod prices; retries may add cost."))
        self.cost_label.setWordWrap(True)
        form.addRow(self.cost_label)
        note = QLabel(tr("runpod_reference_storage_category_help", "Runpod-generated images can be reused directly. Choose storage below for local references. Configure analysis in the LLM card; final video assembly runs on this computer."))
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
        for name, label in (("image_endpoint", "Image endpoint · Z-Image contract"), ("edit_endpoint", "Edit endpoint · Qwen Edit contract"), ("video_endpoint", "Video endpoint ID or Runpod URL · contract chosen above")):
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
        self.h3_preset.currentIndexChanged.connect(self._changed)
        self.gpu_hourly.valueChanged.connect(self._changed)
        self.gpu_hourly.valueChanged.connect(self._update_cost)
        self._role_forms = (
            (form, {"image": [self.image_model, image_help], "video": [self.video_model, self.size]}),
            (advanced, {"image": [self.fields["image_endpoint"]], "video": [self.fields["video_endpoint"], self.expansion],
                        "edit": [self.fields["edit_endpoint"], self.edit_size, self.preserve_size]}),
        )
        self.set_configuration({})

    def set_role(self, role):
        """Share credentials and storage, showing only the selected engine's fields."""
        self._role = role
        for form, roles in self._role_forms:
            for field_role, fields in roles.items():
                for field in fields:
                    form.setRowVisible(field, role is None or role == field_role)
        self._update_cost()

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
        self._video_adapter = "h3" if endpoint == "h3_private" else "public"
        if endpoint == "h3_private":
            if model_id({"video_endpoint": self.fields["video_endpoint"].text()}) in VIDEO_MODELS:
                self.fields["video_endpoint"].clear()
            self.advanced_toggle.setChecked(True)
        elif endpoint != "custom":
            self.fields["video_endpoint"].setText(endpoint)
        else:
            self.advanced_toggle.setChecked(True)
        self._sync_video_model()
        self._changed()

    def _sync_video_model(self, *_):
        endpoint = model_id({"video_endpoint": self.fields["video_endpoint"].text()})
        self.video_model.blockSignals(True)
        h3 = self._video_adapter == "h3"
        self.video_model.setCurrentIndex(self.video_model.findData("h3_private" if h3 else endpoint if endpoint in VIDEO_MODELS else "custom"))
        self.video_model.blockSignals(False)
        sizes = VIDEO_MODELS.get(endpoint, VIDEO_MODELS["wan-2-6-i2v"])["rates"]
        previous = self.size.currentData()
        self.size.blockSignals(True)
        self.size.clear()
        for size in sizes:
            self.size.addItem("720p" if size == "1280*720" else "1080p", size)
        if endpoint == "kling-video-o1-r2v":
            self.size.clear()
            self.size.addItem(self.tr("storyboard_video_model_resolution", "Model default (16:9)"), "1280*720")
        if h3:
            self.size.clear()
            self.size.addItem(self.tr("h3_native", "Native H3 workflow · 1344×768, 24 FPS"), "1280*720")
        self.size.setEnabled(not h3 and endpoint != "kling-video-o1-r2v")
        self.size.setCurrentIndex(max(0, self.size.findData(previous)))
        self.size.blockSignals(False)
        self._update_cost()

    def _update_cost(self, *_):
        h3 = self._video_adapter == "h3"
        self.h3_panel.setVisible(h3 and self._role in (None, "video"))
        model = VIDEO_MODELS.get(model_id({"video_endpoint": self.fields["video_endpoint"].text()}))
        image = IMAGE_MODELS.get(image_model_id({"image_endpoint": self.fields["image_endpoint"].text()}), {})
        self.model_note.setText(image.get("name", "Custom image") + " · Qwen Image Edit 2511 · " + (model["name"] if model else self.tr("runpod_custom_endpoint", "Custom endpoint (Advanced)")))
        rate = model["rates"].get(self.size.currentData()) if model else None
        amount = f"${rate:.2f}/s · 5s ≈ ${rate * 5:.2f}" if rate else self.tr("runpod_cost_unknown", "Depends on the endpoint")
        image_price = f"${image['price']:.3f}" if image else self.tr("runpod_cost_unknown", "Depends on the endpoint")
        self.cost_label.setText(self.tr("runpod_selected_model_rates", "Image {image_price} · edit $0.02 · selected video: {price}. Indicative prices; verify current Runpod prices.", image_price=image_price, price=amount))
        if self._role:
            label, price = {
                "image": (image.get("name", "Custom image"), image_price),
                "video": (model["name"] if model else "Custom video", amount),
                "edit": (self.fields["edit_endpoint"].text(), "$0.02"),
            }[self._role]
            self.model_note.setText(label)
            self.cost_label.setText(self.tr("runpod_role_rate", "Indicative price: {price}. Verify current Runpod prices.", price=price))
        if h3 and self._role in (None, "video"):
            self.model_note.setText(self.tr("h3_private_endpoint", "MiniMax H3 · private ComfyUI endpoint"))
            rate = self.gpu_hourly.value()
            self.cost_label.setText(self.tr("h3_cost_help", "Estimates use the GPU rate above; zero means unknown. Billing depends on active GPU time, not clip duration or queue time. Startup, idle and storage may add cost. Timings are saved in Runpod jobs."))

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
        config.update(video_adapter=self._video_adapter, h3_preset=self.h3_preset.currentData(), gpu_hourly_usd=self.gpu_hourly.value())
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
            self._video_adapter = config.get("video_adapter", "public")
            self.h3_preset.setCurrentIndex(max(0, self.h3_preset.findData(config.get("h3_preset", "fast"))))
            self.gpu_hourly.setValue(float(config.get("gpu_hourly_usd", 0)))
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
