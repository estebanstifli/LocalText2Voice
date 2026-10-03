from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QGroupBox,
)
from app.tts.indextts_config import (
    DEFAULTS,
    LANGUAGES,
    LICENSE_URL,
    LICENSE_NOTICE,
    language_code,
)
from app.workers.indextts_worker import IndexTTSWorker
from .widgets import FilePicker


class IndexTTSSettingsMixin:
    def _configure_indextts_install_dialog(self, dialog):
        # Uninstalled engines cannot be selected yet, so expose precision here too.
        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("indextts_device", "Compute device")))
        runtime = QComboBox()
        for label, value in (
            ("CUDA / NVIDIA — BF16", ("cuda", "bfloat16")),
            ("CUDA / NVIDIA — FP32", ("cuda", "float32")),
            ("CPU — FP32", ("cpu", "float32")),
        ):
            runtime.addItem(label, value)
        current = self._indextts_settings()
        runtime.setCurrentIndex(
            max(0, runtime.findData((current["device"], current["dtype"])))
        )

        def apply_runtime():
            device, dtype = runtime.currentData()
            for key, value in (("device", device), ("dtype", dtype)):
                widget = self.indextts_fields[key]
                widget.setCurrentIndex(widget.findData(value))

        runtime.currentIndexChanged.connect(apply_runtime)
        apply_runtime()
        dialog.install_requested.connect(lambda: runtime.setEnabled(False))
        row.addWidget(runtime)
        dialog.layout().insertLayout(2, row)
        dialog.indextts_runtime_combo = runtime

    def _build_indextts_engine_panel(self):
        panel = QWidget()
        self.indextts_panel = panel
        layout = QVBoxLayout(panel)
        self.indextts_status = QLabel()
        layout.addWidget(self.indextts_status)
        help_label = QLabel(
            self.tr(
                "indextts_editor_help",
                "Clone a voice from a reference clip. Add emotions with the Emotion menu in the text editor. BF16 requires a compatible NVIDIA GPU; CPU uses FP32.",
            )
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        form = QFormLayout()
        layout.addLayout(form)
        self.indextts_fields = {}
        for key, values in (
            (
                "language",
                [
                    (self.tr("language_" + name.lower(), name), code)
                    for code, name in LANGUAGES.items()
                ],
            ),
            ("device", [("CUDA / NVIDIA", "cuda"), ("CPU", "cpu")]),
            ("dtype", [("BF16", "bfloat16"), ("FP32", "float32")]),
        ):
            widget = QComboBox()
            for label, value in values:
                widget.addItem(label, value)
            self.indextts_fields[key] = widget
            form.addRow(
                self.tr("indextts_" + key, key.replace("_", " ").title()), widget
            )
        picker = FilePicker(
            self.tr("browse", "Browse"), "Audio (*.wav *.mp3 *.flac *.ogg *.m4a)"
        )
        self.indextts_fields["reference_audio_path"] = picker
        form.addRow(
            self.tr("indextts_reference_audio_path", "Voice reference audio"), picker
        )
        advanced = QGroupBox(self.tr("advanced_settings", "Advanced settings"))
        advanced_form = QFormLayout(advanced)
        parameter_labels = {
            "temperature": "Temperature", "top_p": "Top-p", "top_k": "Top-k",
            "num_beams": "Beam count", "repetition_penalty": "Repetition penalty",
            "max_mel_tokens": "Maximum audio tokens",
            "max_text_tokens_per_segment": "Text tokens per segment",
            "interval_silence": "Internal silence (ms)",
        }
        for key, low, high in (
            ("duration_factor", 0.5, 2),
            ("temperature", 0.05, 2),
            ("top_p", 0.01, 1),
            ("top_k", 0, 100),
            ("num_beams", 1, 10),
            ("repetition_penalty", 0.1, 20),
            ("max_mel_tokens", 50, 3000),
            ("max_text_tokens_per_segment", 20, 200),
            ("interval_silence", 0, 2000),
        ):
            spin = QSpinBox() if isinstance(DEFAULTS[key], int) else QDoubleSpinBox()
            spin.setRange(low, high)
            if isinstance(spin, QDoubleSpinBox):
                spin.setSingleStep(0.05)
            self.indextts_fields[key] = spin
            label = (
                self.tr("indextts_duration_factor", "Duration factor (>1 = slower)")
                if key == "duration_factor"
                else self.tr("indextts_" + key, parameter_labels[key])
            )
            advanced_form.addRow(label, spin)
        advanced_toggle = QPushButton(self.tr("advanced_settings", "Advanced settings"))
        advanced_toggle.setCheckable(True)
        advanced_toggle.toggled.connect(advanced.setVisible)
        layout.addWidget(advanced_toggle)
        advanced.setVisible(False)
        layout.addWidget(advanced)
        license_label = QLabel(
            f'<a href="{LICENSE_URL}">bilibili Model Use License Agreement</a>'
        )
        license_label.setOpenExternalLinks(True)
        layout.addWidget(license_label)
        notice = QLabel(self.tr("indextts_license", LICENSE_NOTICE))
        notice.setWordWrap(True)
        layout.addWidget(notice)
        row = QHBoxLayout()
        self.indextts_buttons = {}
        for key, label, callback in (
            (
                "install",
                self.tr("install", "Install"),
                lambda: self._show_tts_engine_install_dialog("indextts"),
            ),
            (
                "remove",
                self.tr("uninstall", "Uninstall"),
                lambda: self._start_indextts_operation("remove"),
            ),
            (
                "load",
                self.tr("load_into_memory", "Load into memory"),
                lambda: self._toggle_preloaded_tts_engine("indextts"),
            ),
            ("cancel", self.tr("cancel", "Cancel"), self._cancel_indextts_operation),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            self.indextts_buttons[key] = button
            row.addWidget(button)
        layout.addLayout(row)
        self._load_indextts_settings()
        layout.addStretch(1)
        return panel

    def _load_indextts_settings(self):
        config = {**DEFAULTS, **self.settings.get("indextts", {})}
        for key, widget in self.indextts_fields.items():
            value = config[key]
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(value)))
            elif isinstance(widget, FilePicker):
                widget.set_path(value)
            else:
                widget.setValue(value)

    def _indextts_settings(self):
        # Global emotions are intentionally neutral; the editor supplies them per segment.
        config = deepcopy(DEFAULTS)
        for key, widget in self.indextts_fields.items():
            if isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, FilePicker):
                value = str(widget.path() or "")
            else:
                value = widget.value()
            config[key] = value
        return config

    def _indextts_voice_config_for_ui(self):
        return {
            **self._indextts_settings(),
            "engine": "indextts",
            "speed": self.speed_spin.value(),
        }

    def _refresh_indextts_status(self):
        if not hasattr(self, "indextts_status"):
            return
        ready = self.indextts_manager.is_installed()
        busy = self.indextts_thread is not None
        self.indextts_status.setText(
            self.tr("installed", "Installed")
            if ready
            else self.tr("not_installed", "Not installed")
        )
        for key, button in self.indextts_buttons.items():
            button.setEnabled(
                busy
                if key == "cancel"
                else not busy
                and (
                    ready
                    or key == "install"
                    or (key == "remove" and self.indextts_manager.install_dir.exists())
                )
            )
        self._configure_preload_button(
            self.indextts_buttons["load"], "indextts", ready and not busy
        )

    def _start_indextts_operation(self, operation):
        if (
            self.indextts_thread is not None
            or self.worker_thread is not None
            or self.preload_thread is not None
        ):
            return
        if (
            "indextts" in self.host_loaded_tts_engine_ids
            or self.preloaded_tts_engine_id == "indextts"
        ):
            self._show_error(
                "IndexTTS",
                self.tr(
                    "indextts_unload_before_changes",
                    "Unload IndexTTS from memory before repairing or removing it.",
                ),
            )
            self._finish_tts_engine_install_dialog(
                "indextts",
                False,
                self.tr("indextts_unload_first", "Unload IndexTTS from memory first."),
            )
            return
        config = self._indextts_voice_config_for_ui()
        self._save_settings()
        self.indextts_operation = operation
        thread = QThread(self)
        worker = IndexTTSWorker(self.indextts_manager, operation, config)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_indextts_progress)
        worker.finished.connect(self._on_indextts_finished)
        worker.failed.connect(self._on_indextts_failed)
        worker.cancelled.connect(self._on_indextts_cancelled)
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_indextts_worker)
        self.indextts_thread, self.indextts_worker = thread, worker
        self._refresh_indextts_status()
        thread.start()

    def _cancel_indextts_operation(self):
        if self.indextts_worker is not None:
            self.indextts_worker.request_cancel()

    def _on_indextts_progress(self, current, total, message):
        self._update_tts_engine_install_dialog("indextts", current, total, message)
        self.log_view.append_event(message)

    def _on_indextts_finished(self, path):
        self._finish_tts_engine_install_dialog("indextts", True, path)
        self.log_view.append_event(f"IndexTTS {self.indextts_operation}: {path}")

    def _on_indextts_failed(self, message):
        self._finish_tts_engine_install_dialog("indextts", False, message)
        self._show_error("IndexTTS", message)

    def _on_indextts_cancelled(self):
        self._finish_tts_engine_install_dialog(
            "indextts",
            False,
            self.tr("engine_install_cancelled", "Installation cancelled."),
        )

    def _clear_indextts_worker(self):
        self.indextts_worker = self.indextts_thread = None
        self._refresh_indextts_status()
        self._refresh_tts_engine_table()
        self._refresh_voices_page()

    def _apply_indextts_gallery_reference(self, voice):
        from pathlib import Path

        path = Path(voice.installed_path or voice.ref_audio_path or "")
        if not path.is_file():
            return False
        self.indextts_fields["reference_audio_path"].set_path(path)
        code = language_code(voice.language)
        if code in LANGUAGES:
            self._select_combo_data(self.indextts_fields["language"], code)
        return True
