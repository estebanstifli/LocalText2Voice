from __future__ import annotations

from copy import deepcopy
import html
import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel,
                              QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPushButton)

from app.tts.audiocpp_config import DEFAULTS, MODELS, MODEL_SETTING_KEYS, BREEZE_MODEL, BREEZE_FIELDS, model_config, model_field_visible
from app.tts.audiocpp_manager import runtime_key
from app.tts.base import TTSEngineError
from app.utils.paths import resolve_large_asset_path
from .engine_install_dialog import EngineInstallDialog, EngineInstallRequirement, available_disk_space_gb
from .widgets import FilePicker


class AudioCppInstallWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(bool, str)

    def __init__(self, manager, config):
        super().__init__()
        self.manager, self.config = manager, config

    @Slot()
    def run(self):
        try:
            self.manager.install(self.config["model"], self.config["backend"], self._progress)
            self.finished.emit(True, "audio.cpp model and runtime installed.")
        except Exception as exc:
            self.finished.emit(False, str(exc))

    def request_cancel(self):
        self.manager.cancel()

    def _progress(self, current, total, message):
        # Qt Signal(int) is 32-bit; both GGUFs exceed that range.
        self.progress.emit(int(current * 1000 / total) if total else 0, 1000 if total else 0, message)


class AudioCppInstallCompletion(QObject):
    """Keep results and GUI updates on the UI thread, after the worker stops."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.result = (False, "audio.cpp installation stopped without a result.")

    @Slot(bool, str)
    def receive(self, ok, message):
        self.result = (ok, message)
        logging.getLogger(__name__).info("audio.cpp install worker result: %s %s", ok, message)

    @Slot()
    def stopped(self):
        window = self.window
        logging.getLogger(__name__).info("audio.cpp install thread stopped; updating interface")
        window._clear_audiocpp_worker()
        window._audiocpp_install_finished(*self.result)
        window._audiocpp_completion = None
        self.deleteLater()


class AudioCppSettingsMixin:
    def _apply_audiocpp_gallery_reference(self, voice):
        path = resolve_large_asset_path(voice.installed_path or voice.ref_audio_path or "")
        if not path.is_file():
            return False
        self.audiocpp_voices[voice.voice_id] = {
            "name": voice.name, "reference_audio_path": str(path), "reference_text": voice.ref_text,
        }
        if self.audiocpp_voice_combo.findData(voice.voice_id) < 0:
            self.audiocpp_voice_combo.addItem(voice.name, voice.voice_id)
        self.audiocpp_voice_combo.setCurrentIndex(self.audiocpp_voice_combo.findData(voice.voice_id))
        self._audiocpp_choose_voice()
        return True

    def _build_audiocpp_engine_panel(self):
        self.audiocpp_thread = self.audiocpp_worker = None
        self.audiocpp_voices = deepcopy(self.settings.get("audiocpp", {}).get("voices", {}))
        self.audiocpp_fields = {}
        panel = QWidget()
        self.audiocpp_panel = panel
        layout = QVBoxLayout(panel)
        info = QLabel(self.tr("audiocpp_help", "Download a native runtime once, then install Higgs, Fish, Breeze or VoxCPM 2 GGUF. Generation runs locally. For voice cloning, choose a PCM WAV and its transcript. CPU can be slow for these models."))
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        layout.addLayout(form)
        self.audiocpp_form = form
        backend = QComboBox()
        for key, label in (("cpu", "CPU portable"), ("cuda", "NVIDIA / CUDA 12.4"), ("vulkan", "GPU / Vulkan portable"), ("metal", "Apple / Metal")):
            try:
                runtime_key(key)
            except TTSEngineError:
                continue
            backend.addItem(label, key)
        self.audiocpp_fields["backend"] = backend
        form.addRow(self.tr("compute_device", "Backend"), backend)
        picker = FilePicker(self.tr("browse", "Browse"), "PCM WAV (*.wav)")
        self.audiocpp_fields["reference_audio_path"] = picker
        form.addRow(self.tr("reference_audio", "Reference audio"), picker)
        reference = QLineEdit()
        self.audiocpp_fields["reference_text"] = reference
        form.addRow(self.tr("reference_text", "Reference transcript"), reference)
        self.audiocpp_voice_combo = QComboBox()
        self.audiocpp_voice_combo.addItem(self.tr("custom", "Custom"), "")
        for name in self.audiocpp_voices:
            self.audiocpp_voice_combo.addItem(self.audiocpp_voices[name].get("name", name), name)
        self.audiocpp_voice_combo.currentIndexChanged.connect(self._audiocpp_choose_voice)
        form.addRow(self.tr("voice", "Voice"), self.audiocpp_voice_combo)
        voice_row = QHBoxLayout()
        self.audiocpp_voice_name = QLineEdit()
        self.audiocpp_voice_name.setPlaceholderText(self.tr("audiocpp_voice_name", "Name for this reference voice"))
        save_voice = QPushButton(self.tr("save_voice", "Save voice"))
        save_voice.clicked.connect(self._audiocpp_save_voice)
        voice_row.addWidget(self.audiocpp_voice_name)
        voice_row.addWidget(save_voice)
        form.addRow(voice_row)
        for key, label, low, high, integer in (
            ("device", "GPU index", 0, 64, True),
            ("temperature", "Temperature", 0.01, 1.99, False),
            ("top_p", "Top-p", 0.01, 1.0, False), ("top_k", "Top-k", 1, 1000, True),
            ("seed", "Seed (-1: random)", -1, 2147483647, True),
            ("max_tokens", "Maximum tokens", 1, 16384, True),
            ("text_chunk_size", "Internal chunk size", 20, 2048, True),
            ("num_inference_steps", "Diffusion steps", 1, 100, True),
        ):
            widget = QSpinBox() if integer else QDoubleSpinBox()
            widget.setRange(low, high)
            self.audiocpp_fields[key] = widget
            form.addRow(self.tr("audiocpp_" + key, label), widget)
        instruction = QLineEdit()
        instruction.setPlaceholderText("Speak calmly and gently.")
        self.audiocpp_fields["instruction"] = instruction
        form.addRow(self.tr("breeze_instruction", "Voice / emotion instruction"), instruction)
        for key, label, low, high in (("guidance_scale", "Guidance scale", 0, 20),
                                      ("depth_temperature", "Depth temperature", .01, 1.99)):
            widget = QDoubleSpinBox()
            widget.setRange(low, high)
            self.audiocpp_fields[key] = widget
            form.addRow(self.tr("breeze_" + key, label), widget)
        self.audiocpp_notice = QLabel()
        self.audiocpp_notice.setWordWrap(True)
        self.audiocpp_notice.setOpenExternalLinks(True)
        layout.addWidget(self.audiocpp_notice)
        self.audiocpp_status = QLabel()
        self.audiocpp_status.setWordWrap(True)
        layout.addWidget(self.audiocpp_status)
        row = QHBoxLayout()
        self.audiocpp_buttons = {}
        for key, label, callback in (
            ("install", self.tr("audiocpp_install", "Install selected model + runtime"), self._install_audiocpp),
            ("remove", self.tr("audiocpp_remove", "Remove selected weights"), self._remove_audiocpp),
            ("load", self.tr("load_into_memory", "Load into memory"), lambda: self._audiocpp_row_action(self._audiocpp_settings()["model"], "load")),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
            self.audiocpp_buttons[key] = button
        layout.addLayout(row)
        self._load_audiocpp_settings()
        for widget in self.audiocpp_fields.values():
            signal = (widget.currentIndexChanged if isinstance(widget, QComboBox) else
                      widget.path_changed if isinstance(widget, FilePicker) else
                      widget.textChanged if isinstance(widget, QLineEdit) else widget.valueChanged)
            signal.connect(self._audiocpp_changed)
        layout.addStretch(1)
        return panel

    def _load_audiocpp_settings(self):
        config = model_config(self.settings.get("audiocpp", {}))
        self._audiocpp_displayed_model = config["model"]
        self.audiocpp_voices = deepcopy(config["voices"])
        previous = self.audiocpp_voice_combo.blockSignals(True)
        self.audiocpp_voice_combo.clear()
        self.audiocpp_voice_combo.addItem(self.tr("custom", "Custom"), "")
        for name in self.audiocpp_voices:
            self.audiocpp_voice_combo.addItem(self.audiocpp_voices[name].get("name", name), name)
        self.audiocpp_voice_combo.blockSignals(previous)
        top_k = self.audiocpp_fields["top_k"]
        blocked = top_k.blockSignals(True)
        top_k.setMinimum(0 if config["model"] == BREEZE_MODEL else 1)
        top_k.blockSignals(blocked)
        for key, widget in self.audiocpp_fields.items():
            previous = widget.blockSignals(True)
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(config[key])))
            elif isinstance(widget, FilePicker):
                widget.set_path(config[key])
            elif isinstance(widget, QLineEdit):
                widget.setText(str(config[key]))
            else:
                widget.setValue(config[key])
            widget.blockSignals(previous)
        self._refresh_audiocpp_status()

    def _audiocpp_settings(self):
        config = {**DEFAULTS, **self.settings.get("audiocpp", {})}
        for key, widget in getattr(self, "audiocpp_fields", {}).items():
            config[key] = (widget.currentData() if isinstance(widget, QComboBox) else
                           str(widget.path() or "") if isinstance(widget, FilePicker) else
                           widget.text() if isinstance(widget, QLineEdit) else widget.value())
        config["voices"] = deepcopy(getattr(self, "audiocpp_voices", config["voices"]))
        profiles = deepcopy(config.get("model_settings", {}))
        profiles[config["model"]] = {key: config[key] for key in MODEL_SETTING_KEYS}
        config["model_settings"] = profiles
        return config

    def _select_audiocpp_model(self, target):
        if target not in MODELS:
            raise ValueError(f"Unknown audio.cpp model: {target}")
        config = self._audiocpp_settings()
        if config["model"] == target:
            return
        # Persist the outgoing row's controls before restoring the selected row.
        self.settings["audiocpp"] = model_config(config, target)
        self._load_audiocpp_settings()
        self._audiocpp_changed()

    def _audiocpp_row_action(self, model, action):
        if self.audiocpp_thread or self.preload_thread or self.worker_thread:
            return
        if not self._select_tts_engine("audiocpp:" + model):
            return
        if action == "install":
            self._install_audiocpp()
        elif action == "remove":
            self._remove_audiocpp()
        elif self._audiocpp_model_loaded(model):
            self._start_engine_host_memory_action("audiocpp", load=False)
        else:
            self._start_preload_tts_engine("audiocpp")

    def _audiocpp_model_loaded(self, model):
        return ("audiocpp" in self.host_loaded_tts_engine_ids
                and getattr(self, "_audiocpp_loaded_model", None) == model)

    def _audiocpp_voice_config_for_ui(self):
        return {**self._audiocpp_settings(), "engine": "audiocpp", "speed": self.speed_spin.value()}

    def _audiocpp_changed(self, *_):
        if self._restoring_settings:
            return
        self._save_settings()
        self._refresh_audiocpp_status()
        self._refresh_tts_engine_table()
        self._update_header_engine_label()

    def _audiocpp_choose_voice(self, *_):
        name = self.audiocpp_voice_combo.currentData()
        if name in self.audiocpp_voices:
            value = self.audiocpp_voices[name]
            self.audiocpp_fields["reference_audio_path"].set_path(value["reference_audio_path"])
            self.audiocpp_fields["reference_text"].setText(value.get("reference_text", ""))

    def _audiocpp_save_voice(self):
        name = self.audiocpp_voice_name.text().strip()
        config = self._audiocpp_settings()
        if not name or not resolve_large_asset_path(config["reference_audio_path"]).is_file():
            self._show_error("audio.cpp", self.tr("audiocpp_voice_required", "Enter a name and choose an existing reference WAV."))
            return
        self.audiocpp_voices[name] = {key: config[key] for key in ("reference_audio_path", "reference_text")}
        if self.audiocpp_voice_combo.findData(name) < 0:
            self.audiocpp_voice_combo.addItem(name, name)
        self.audiocpp_voice_combo.setCurrentIndex(self.audiocpp_voice_combo.findData(name))
        self._save_settings()
        self._refresh_generation_voice_combo()
        self._refresh_voices_page()

    def _refresh_audiocpp_status(self):
        if not hasattr(self, "audiocpp_status"):
            return
        config = self._audiocpp_settings()
        spec = MODELS[config["model"]]
        ready = self.audiocpp_manager.is_installed(config["model"], config["backend"])
        busy = self.audiocpp_thread is not None
        license_label = html.escape(self.tr("audiocpp_license_link", "License"))
        self.audiocpp_notice.setText(html.escape(spec.notice) + f' <a href="{spec.license_url}">{license_label}</a>')
        self.audiocpp_notice.setText(
            f"<b>{html.escape(spec.name)}</b> · audio.cpp {spec.size / 1e9:.2f} GB · GGUF {html.escape(spec.precision)}<br>"
            + self.audiocpp_notice.text())
        self.audiocpp_status.setText(self.tr("installed", "Installed") if ready else self.tr("not_installed", "Not installed"))
        for widget in self.audiocpp_fields.values():
            widget.setEnabled(not busy)
        for key, widget in self.audiocpp_fields.items():
            self.audiocpp_form.setRowVisible(widget, model_field_visible(spec.id, key))
        for key, button in self.audiocpp_buttons.items():
            button.setEnabled(not busy and (key == "install" or ready))
        self._configure_preload_button(self.audiocpp_buttons["load"], "audiocpp:" + spec.id, ready and not busy)

    def _audiocpp_can_change_files(self):
        if self.audiocpp_thread or self.worker_thread or self.preload_thread:
            return False
        if "audiocpp" in self.host_loaded_tts_engine_ids:
            self._show_error("audio.cpp", self.tr("audiocpp_unload_first", "Unload audio.cpp before installing or removing files."))
            return False
        return True

    def _install_audiocpp(self):
        if not self._audiocpp_can_change_files():
            return
        if "audiocpp" in self.engine_install_dialogs:
            self.engine_install_dialogs["audiocpp"].raise_()
            return
        config = self._audiocpp_settings()
        spec = MODELS[config["model"]]
        free, volume = available_disk_space_gb(self.audiocpp_manager.root)
        dialog = EngineInstallDialog(
            spec.name, EngineInstallRequirement(int(spec.size / 1e9) + 4, "5–30+ min"),
            free, volume, self.tr, self, install_path=self.audiocpp_manager.root,
            license_notice=spec.notice, license_url=spec.license_url,
            license_accept_text=self.tr("audiocpp_license_accept", "I have read the model license and will use it only under its permitted terms."),
            intro_text=self.tr("audiocpp_install_intro", "Downloads the native audio.cpp runtime and the selected GGUF weights. No additional Python environment is installed. Existing verified files are reused."),
        )
        self.engine_install_dialogs["audiocpp"] = dialog
        dialog.install_requested.connect(lambda: self._start_audiocpp_install(config, dialog))
        dialog.cancel_requested.connect(lambda: self.audiocpp_worker.request_cancel() if self.audiocpp_worker else None)
        dialog.finished.connect(lambda _: self._forget_tts_engine_install_dialog("audiocpp", dialog))
        dialog.open()

    def _start_audiocpp_install(self, config, dialog):
        if self.audiocpp_thread is not None:
            return
        logging.getLogger(__name__).info("audio.cpp install starting: %s / %s", config["model"], config["backend"])
        self.audiocpp_manager.reset_cancel()
        thread = QThread(self)
        worker = AudioCppInstallWorker(self.audiocpp_manager, config)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(dialog.update_progress)
        completion = AudioCppInstallCompletion(self)
        self._audiocpp_completion = completion
        worker.finished.connect(completion.receive, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(completion.stopped, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        self.audiocpp_thread, self.audiocpp_worker = thread, worker
        self._refresh_audiocpp_status()
        thread.start()

    @Slot(bool, str)
    def _audiocpp_install_finished(self, ok, message):
        dialog = self.engine_install_dialogs.get("audiocpp")
        if dialog is not None:
            dialog.finish(ok, self.tr("audiocpp_installed_message",
                                     "audio.cpp model and runtime installed.") if ok else message)

    def _clear_audiocpp_worker(self):
        self.audiocpp_thread = self.audiocpp_worker = None
        self._refresh_audiocpp_status()
        self._refresh_tts_engine_table()
        self._refresh_voices_page()

    def _remove_audiocpp(self):
        if self._audiocpp_can_change_files():
            self.audiocpp_manager.remove_model(self._audiocpp_settings()["model"])
            self._refresh_audiocpp_status()
            self._refresh_tts_engine_table()
