from copy import deepcopy
from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTabWidget, QPlainTextEdit, QHBoxLayout, QPushButton


class StoryboardReviewDialog(QDialog):
    submitted = Signal(object)
    draftChanged = Signal(object)

    def __init__(self, draft, tr, parent=None):
        super().__init__(parent)
        self.draft = deepcopy(draft)
        self._submitted = False
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(400)
        self._autosave.timeout.connect(self._save_edits)
        self.setWindowTitle(tr("storyboard_review_title", "Revisar análisis antes de continuar"))
        self.resize(1000, 730)
        layout = QVBoxLayout(self)
        hint = QLabel(tr("storyboard_review_instructions", "El análisis está pausado. Corrige identidades, apariencia y lugares. Tus cambios tendrán prioridad en las siguientes fases; el texto original seguirá determinando los tiempos."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        tabs = QTabWidget()
        self.editors = {}
        names = {"characters": "Personajes / resumen narrativo", "scenes": "Escenas y frases de inicio",
                 "story_summary": "Resumen global", "locations": "Lugares", "era": "Época manual (opcional)"}
        for key, value in draft.get("edited", {}).items():
            if key in {"appearance", "directions"}:
                continue
            editor = QPlainTextEdit()
            editor.setPlainText(str(value))
            editor.textChanged.connect(lambda: self._autosave.start())
            self.editors[key] = editor
            tabs.addTab(editor, tr("storyboard_review_" + key, names.get(key, key)))
        layout.addWidget(tabs)
        buttons = QHBoxLayout()
        for action, label in (("continue", "Continuar"), ("pause", "Guardar y cerrar"), ("cancel", "Cancelar análisis")):
            button = QPushButton(tr("storyboard_review_action_" + action, label))
            button.clicked.connect(lambda checked=False, action=action: self.submit(action))
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def _save_edits(self):
        if not self._submitted:
            self.draft.setdefault("edited", {}).update({k: e.toPlainText() for k, e in self.editors.items()})
            self.draft["status"] = "pending"
            self.draftChanged.emit(deepcopy(self.draft))

    def submit(self, action):
        if self._submitted:
            return
        self._submitted = True
        self._autosave.stop()
        self.draft.setdefault("edited", {}).update({k: e.toPlainText() for k, e in self.editors.items()})
        self.draft["status"] = "approved" if action == "continue" else "cancelled" if action == "cancel" else "pending"
        self.submitted.emit({"action": action, "draft": deepcopy(self.draft)})
        self.accept()

    def reject(self):
        self.submit("pause")

    def closeEvent(self, event):
        self.submit("pause")
        event.accept()
