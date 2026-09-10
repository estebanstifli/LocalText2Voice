from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.core.video_storyboard_install import (
    default_comfy_root, endpoint_for, export_package, find_comfy_python,
    is_local_url, load_manifest,
)
from app.tts.model_cache import format_file_size
from app.ui.icons import ui_icon


class StoryboardInstallDialog(QDialog):
    installRequested = Signal(object)
    cancelRequested = Signal()

    def __init__(self, tr, role: str, configuration: dict, parent=None):
        super().__init__(parent)
        self.tr_text = tr
        self.role = role
        self.manifest = load_manifest(role)
        self.active = False
        self._last_log = ""
        self._last_log_time = 0.0
        self.setWindowTitle(tr("storyboard_install_title", "Install default engine") + " · " + self.manifest["name"])
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(900, 750)
        layout = QVBoxLayout(self)
        title = QLabel(self.manifest["name"])
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        help_label = QLabel(tr("storyboard_install_help", "Installs the default engine, workflow, required nodes and models. Existing models are reused and interrupted downloads can resume. Nothing starts until you press Install."))
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        sizes = sum(m["size_bytes"] for m in self.manifest["models"] if not m.get("optional")) or self.manifest.get("estimated_size_bytes", 0)
        size_label = QLabel(tr("storyboard_install_size", "Full model package: {size} on disk. Python/runtime dependencies need additional space. Shared files reduce the download.", size=format_file_size(sizes)))
        size_label.setObjectName("helperLabel")
        size_label.setWordWrap(True)
        layout.addWidget(size_label)
        self.controls = QWidget()
        form = QFormLayout(self.controls)
        self.url_edit = QLineEdit()
        url, self.headers = endpoint_for(role, configuration)
        self.original_url = url
        self.url_edit.setText(url)
        form.addRow("URL", self.url_edit)
        local_button = QPushButton(tr("storyboard_install_local", "Use localhost"))
        local_button.clicked.connect(lambda: self.url_edit.setText("http://127.0.0.1:11434" if role == "llm" else "http://127.0.0.1:8188"))
        form.addRow(local_button)
        preferences = configuration.get("installation", {})
        self.root_edit = QLineEdit(str(preferences.get("comfy_root") or default_comfy_root()))
        self.python_edit = QLineEdit(str(preferences.get("comfy_python") or ""))
        self.python_edit.setPlaceholderText(tr("storyboard_install_python_auto", "Auto-detect; created automatically for a new installation"))
        if role != "llm":
            for label, edit, browse in (
                (tr("storyboard_install_folder", "ComfyUI folder (contains main.py)"), self.root_edit, self._browse_root),
                (tr("storyboard_install_python", "ComfyUI Python executable"), self.python_edit, self._browse_python),
            ):
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(edit, 1)
                button = QPushButton(tr("browse", "Browse"))
                button.clicked.connect(browse)
                row_layout.addWidget(button)
                form.addRow(label, row)
        self.camera_check = QCheckBox(tr("storyboard_install_camera", "Include the optional Multiple-angles camera LoRA"))
        self.camera_check.setChecked(True)
        self.camera_check.setVisible(role == "edit")
        form.addRow(self.camera_check)
        self.use_defaults_check = QCheckBox(tr("storyboard_install_use", "Use this default engine in Video Storyboard after installation"))
        self.use_defaults_check.setChecked(True)
        form.addRow(self.use_defaults_check)
        layout.addWidget(self.controls)
        self.destination_label = QLabel()
        self.destination_label.setWordWrap(True)
        self.destination_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.destination_label)
        self.tasks = QLabel(" → ".join(["Ollama", "Qwen3:8b", tr("storyboard_install_validate", "Validation")]) if role == "llm" else " → ".join(["ComfyUI", "Workflow", "cm-cli", tr("models", "Models"), tr("storyboard_install_validate", "Validation")]))
        self.tasks.setWordWrap(True)
        self.tasks.setObjectName("helperLabel")
        layout.addWidget(self.tasks)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status_label)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(12000)
        layout.addWidget(self.log, 1)
        buttons = QHBoxLayout()
        self.export_button = QPushButton(tr("storyboard_install_export", "Export installation package"))
        self.export_button.setVisible(role != "llm")
        self.export_button.clicked.connect(self._export)
        buttons.addWidget(self.export_button)
        buttons.addStretch(1)
        self.install_button = QPushButton(tr("install", "Install"))
        self.install_button.setIcon(ui_icon("download"))
        self.install_button.setObjectName("primaryButton")
        self.install_button.clicked.connect(self._start)
        self.cancel_button = QPushButton(tr("storyboard_install_cancel", "Cancel installation"))
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        self.close_button = QPushButton(tr("close", "Close"))
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.install_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.url_edit.textChanged.connect(self._refresh_destination)
        self.root_edit.textChanged.connect(self._refresh_destination)
        self._refresh_destination()

    def _browse_root(self):
        selected = QFileDialog.getExistingDirectory(self, self.tr_text("storyboard_install_folder", "ComfyUI folder"), self.root_edit.text())
        if selected:
            self.root_edit.setText(selected)
            self.python_edit.setText(find_comfy_python(Path(selected)))

    def _browse_python(self):
        selected, _ = QFileDialog.getOpenFileName(self, self.tr_text("storyboard_install_python", "ComfyUI Python executable"))
        if selected:
            self.python_edit.setText(selected)

    def _refresh_destination(self):
        local = is_local_url(self.url_edit.text())
        self.install_button.setEnabled(local and not self.active)
        if not local:
            text = self.tr_text("storyboard_install_remote", "Remote service: HTTP access cannot install files or Python dependencies on that host. Export the package for installation there, or choose localhost to install on this computer.")
        elif self.role == "llm":
            text = self.tr_text("storyboard_install_ollama_storage", "An existing Ollama service keeps its own model storage. A service started by this installer uses the app's AI models storage.")
        else:
            text = self.tr_text("storyboard_install_destination", "Models will be installed in: {path}. All three ComfyUI engines share this directory.", path=str(Path(self.root_edit.text()) / "models"))
        self.destination_label.setText(text)

    def _start(self):
        if self.active or not is_local_url(self.url_edit.text()):
            return
        self.active = True
        self.controls.setEnabled(False)
        self.install_button.setEnabled(False)
        self.cancel_button.show()
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        self.close_button.setText(self.tr_text("storyboard_install_background", "Close · continue in background"))
        self.installRequested.emit({
            "url": self.url_edit.text().strip().rstrip("/"),
            "headers": self.headers if self.url_edit.text().strip().rstrip("/") == self.original_url else {},
            "comfy_root": self.root_edit.text().strip(), "python": self.python_edit.text().strip(),
            "include_camera": self.camera_check.isChecked(), "use_defaults": self.use_defaults_check.isChecked(),
        })

    def _cancel(self):
        self.cancel_button.setEnabled(False)
        self.cancelRequested.emit()
        self.status_label.setText(self.tr_text("storyboard_install_cancelling", "Cancelling… waiting for the current operation to stop."))

    @Slot(object)
    def set_progress(self, event):
        import time
        message = str(event.get("message", ""))
        current, total = int(event.get("current", 0)), int(event.get("total", 0))
        if total:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(100, round(current * 100 / total)))
            if total > 100:
                message += f" · {format_file_size(current)} / {format_file_size(total)}"
        else:
            self.progress_bar.setRange(0, 0)
        if event.get("speed"):
            message += f" · {format_file_size(int(event['speed']))}/s · ETA {int(event.get('eta', 0))}s"
        self.status_label.setText(message)
        # Keep smooth progress while limiting the log to one download line/sec.
        now = time.monotonic()
        key = str(event.get("message", ""))
        if key != self._last_log or now - self._last_log_time > 1:
            self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")
            self._last_log, self._last_log_time = key, now

    def finish_install(self, message: str, success: bool):
        self.active = False
        self.controls.setEnabled(True)
        self.cancel_button.hide()
        self.close_button.setText(self.tr_text("close", "Close"))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if success else 0)
        self.install_button.setEnabled(not success)
        self.status_label.setText(message)
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, self.tr_text("storyboard_install_export", "Export installation package"), f"{self.role}-installation.zip", "ZIP (*.zip)")
        if path:
            try:
                export_package(self.role, Path(path))
                self.log.appendPlainText(self.tr_text("storyboard_install_exported", "Package exported: {path}", path=path))
            except Exception as exc:
                self.log.appendPlainText(str(exc))

    def reject(self):
        # Keep the QObject alive while its worker is running; closing is a hide.
        if self.active:
            self.hide()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.active:
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)
