import csv
import json
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout

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
        self.export_button = QPushButton(tr("runpod_export_metrics", "Export timing report (CSV)…"))
        self.export_button.clicked.connect(self._export_metrics)
        layout.addWidget(self.export_button)
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
            timing = record.get("timing") or {}
            if timing.get("execution_seconds") is not None:
                queue = timing.get("queue_seconds")
                queue_label = "?" if queue is None else f"{queue:.1f}s"
                item.setText(item.text() + " · " + self.tr("runpod_job_timing", "GPU {gpu}s · Queue {queue}", gpu=f"{timing['execution_seconds']:.1f}", queue=queue_label))
            item.setToolTip(record.get("target", ""))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.items.addItem(item)

    def _export_metrics(self):
        filename, _ = QFileDialog.getSaveFileName(self, self.tr("runpod_export_metrics", "Export timing report (CSV)…"), "runpod-timings.csv", "CSV (*.csv)")
        if not filename:
            return
        columns = ["endpoint", "id", "status", "queue_seconds", "execution_seconds", "media_save_seconds",
                   "postprocess_seconds", "total_to_ready_seconds", "worker_id", "execution_cost_estimate_usd", "reported_cost_usd", "cost_note"]
        try:
            with open(filename, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                for index in range(self.items.count()):
                    path = Path(self.items.item(index).data(Qt.ItemDataRole.UserRole))
                    record = json.loads(path.read_text(encoding="utf-8"))
                    row = {**{key: record.get(key, "") for key in ("endpoint", "id", "status")}, **(record.get("timing") or {})}
                    output = record.get("output")
                    row["reported_cost_usd"] = output.get("cost", "") if isinstance(output, dict) else ""
                    # Do not export prompts, reference images, credentials or media URLs.
                    writer.writerow({key: row.get(key, "") for key in columns})
            self.status.setText(self.tr("runpod_metrics_saved", "Timing report saved. Estimates exclude startup, idle and storage; check Runpod billing for actual charges."))
        except (OSError, ValueError, TypeError) as exc:
            self.status.setText(self.tr("runpod_metrics_error", "Cannot export timing report: {error}", error=str(exc)))

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
