from copy import deepcopy
import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QComboBox, QSpinBox,
    QPushButton, QDialog, QHBoxLayout, QPlainTextEdit, QDialogButtonBox, QMessageBox,
)
from app.core.storyboard_analysis_settings import normalize, prompt_catalog, instruction, contract_for


class StoryboardAnalysisSettingsWidget(QWidget):
    settingsChanged = Signal()

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr
        self._loading = False
        self._prompts = {}
        layout = QVBoxLayout(self)
        help_text = QLabel(tr("continuity_conversation_help_v2",
            "Conversational analysis discovers selected entities and historical periods, proposes scenes, and aligns them to the audiobook. Choose manual or automatic periods before each analysis."))
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        form = QFormLayout()
        self.block_size = QComboBox()
        for key, label in (("small", "Small · 4,000 characters"), ("medium", "Medium · 17,000 characters"), ("custom", "Custom")):
            self.block_size.addItem(tr("continuity_conversation_block_" + key, label), key)
        form.addRow(tr("continuity_block_size", "Text per block"), self.block_size)
        self.characters = QSpinBox()
        self.characters.setRange(1000, 100000)
        self.characters.setSingleStep(1000)
        form.addRow(tr("continuity_custom_size", "Custom character limit"), self.characters)
        layout.addLayout(form)
        self.flow = QLabel()
        self.flow.setWordWrap(True)
        layout.addWidget(self.flow)
        self.edit = QPushButton(tr("continuity_edit_prompts", "View / edit instructions…"))
        self.edit.clicked.connect(self._edit)
        layout.addWidget(self.edit)
        self.status = QLabel()
        layout.addWidget(self.status)
        note = QLabel(tr("continuity_settings_note",
            "Changes apply to the next analysis, not to existing scenes. The project records the instructions used. Editing instructions creates a customized version of the selected process; step dependencies and JSON contracts remain fixed."))
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        for control in (self.block_size,):
            control.currentIndexChanged.connect(self._changed)
        self.characters.valueChanged.connect(self._changed)
        self.set_configuration({})

    def set_configuration(self, config):
        self._loading = True
        config = normalize(config)
        self._prompts = deepcopy(config["prompts"])
        for widget, key in ((self.block_size, "block_size"),):
            widget.setCurrentIndex(max(0, widget.findData(config[key])))
        self.characters.setValue(config["custom_characters"])
        self._loading = False
        self._refresh()

    def configuration(self):
        return normalize({"block_size": self.block_size.currentData(),
                          "custom_characters": self.characters.value(),
                          "prompts": deepcopy(self._prompts)})

    def _refresh(self):
        self.characters.setEnabled(self.block_size.currentData() == "custom")
        self.flow.setText(self.tr("continuity_flow_conversational_v2",
            "Discover periods and entities → Propose scenes → Optional review → Structure profiles and periods → Align to audio → Image prompts. Scene duration follows your selected maximum."))
        self.status.setText(self.tr("continuity_custom_prompts", "Customized instruction stages: {count}", count=len(self._prompts)))

    def _changed(self, *_):
        if not self._loading:
            self._refresh()
            self.settingsChanged.emit()

    def _edit(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("continuity_instructions", "Continuity analysis instructions"))
        dialog.resize(980, 740)
        layout = QVBoxLayout(dialog)
        stages = QComboBox()
        keys = list(prompt_catalog())
        catalog = prompt_catalog()
        for key in keys:
            stages.addItem(catalog[key][0], key)
        layout.addWidget(stages)
        editor = QPlainTextEdit()
        editor.setObjectName("continuityInstructionEditor")
        layout.addWidget(editor, 3)
        layout.addWidget(QLabel(self.tr("continuity_contract", "Protected technical contract (not editable)")))
        contract = QPlainTextEdit(contract_for(stages.currentData()))
        contract.setReadOnly(True)
        contract.setMaximumHeight(105)
        layout.addWidget(contract)
        row = QHBoxLayout()
        restore = QPushButton(self.tr("restore_defaults", "Restore default"))
        example = QPushButton(self.tr("continuity_example", "Preview request structure"))
        row.addWidget(restore)
        row.addWidget(example)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        draft = deepcopy(self._prompts)
        current = [stages.currentData()]
        editor.setPlainText(instruction({"prompts": draft}, current[0]))

        def capture():
            text = editor.toPlainText().strip()
            if text and text != catalog[current[0]][1].strip():
                draft[current[0]] = text
            else:
                draft.pop(current[0], None)

        def switch(*_):
            capture()
            current[0] = stages.currentData()
            editor.setPlainText(instruction({"prompts": draft}, current[0]))
            contract.setPlainText(contract_for(current[0]))

        def reset():
            if QMessageBox.question(dialog, self.tr("restore_defaults", "Restore default"),
                    self.tr("continuity_restore_confirm", "Discard edits to this stage and restore its default instructions?")) == QMessageBox.StandardButton.Yes:
                editor.setPlainText(catalog[current[0]][1])

        def preview():
            preview_dialog = QDialog(dialog)
            preview_dialog.setWindowTitle(self.tr("continuity_example", "Preview request structure"))
            preview_dialog.resize(900, 600)
            preview_layout = QVBoxLayout(preview_dialog)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText(json.dumps({
                "example_only": True, "stage": current[0],
                "instructions": editor.toPlainText(), "protected_contract": contract_for(current[0]),
                "runtime_input": "Source passage or discovery report, with known entities/periods where applicable.",
                "response_schema": "Plain-text discovery; structured JSON conversion follows review. Full requests are recorded in the analysis log.",
            }, indent=2, ensure_ascii=False))
            preview_layout.addWidget(text)
            preview_dialog.exec()

        stages.currentIndexChanged.connect(switch)
        restore.clicked.connect(reset)
        example.clicked.connect(preview)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            capture()
            self._prompts = draft
            self._changed()
