import json
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout

from app.core.video_storyboard_runpod import TERMINAL, configuration, job_error, jobs_directory, manage_job


class _JobAction(QObject):
    finished = Signal(str)

    def __init__(self, settings, path, action):
        super().__init__()
        self.settings, self.path, self.action = settings, path, action

    @Slot()
    def run(self):
        try:
            result = manage_job(self.settings, self.path, self.action)
            if result.get("status") in TERMINAL:
                raise job_error(result, configuration(self.settings))
            self.finished.emit(str(result.get("status", "")))
        except Exception as exc:
            self.finished.emit(str(exc))


class RunpodJobsDialog(QDialog):
    def __init__(self, settings, tr, parent=None):
        super().__init__(parent)
        self.settings, self.tr = settings, tr
        self.thread = None
        self.setWindowTitle(tr("runpod_jobs", "Runpod jobs"))
        self.resize(760, 440)
        layout = QVBoxLayout(self)
        note = QLabel(tr("runpod_jobs_help", "To recover a job or download, generate again with the same prompt, seed and source images in the same project. The saved job is reused. Allow new generation only when you want another paid request; check unknown submissions in the Runpod console first."))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.items = QListWidget()
        layout.addWidget(self.items)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.buttons = []
        for label, action in ((tr("runpod_job_status", "Refresh job status"), "check"), (tr("runpod_cancel", "Cancel remote job"), "cancel"), (tr("runpod_job_reset", "Allow new generation…"), "reset")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, action=action: self._action(action))
            buttons.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(buttons)
        self._load()

    def _load(self):
        self.items.clear()
        directory = jobs_directory()
        for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            cost = record.get("output", {}).get("cost") if isinstance(record.get("output"), dict) else None
            item = QListWidgetItem(f"{record.get('endpoint')} · {record.get('status')} · {record.get('id', '?')}" + (f" · ${cost}" if cost is not None else ""))
            item.setToolTip(record.get("target", ""))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.items.addItem(item)

    def _action(self, action):
        item = self.items.currentItem()
        if self.thread or item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if action == "reset":
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") in {"IN_QUEUE", "IN_PROGRESS"}:
                self.status.setText(self.tr("runpod_cancel_first", "Cancel the active job before allowing another generation."))
                return
            answer = QMessageBox.question(self, self.tr("runpod_job_reset", "Allow new generation…"), self.tr("runpod_reset_warning", "Forget the recovery record? Generating again will create a new paid request. This does not cancel a remote job."), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                path.unlink()
                self._load()
            return
        self.thread = QThread(self)
        self.worker = _JobAction(self.settings, path, action)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.status.setText)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self._finished)
        self.thread.finished.connect(self.thread.deleteLater)
        for button in self.buttons:
            button.setEnabled(False)
        self.thread.start()

    def _finished(self):
        self.thread = None
        self.worker = None
        for button in self.buttons:
            button.setEnabled(True)
        self._load()

    def reject(self):
        if self.thread is None:
            super().reject()

    def closeEvent(self, event):
        if self.thread is not None:
            event.ignore()
        else:
            super().closeEvent(event)
