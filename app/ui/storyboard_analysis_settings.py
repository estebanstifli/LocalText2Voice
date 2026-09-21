from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QComboBox, QSpinBox,
    QPushButton, QDialog, QDialogButtonBox,
)
from app.core.storyboard_analysis_settings import normalize
from app.ui.storyboard_instruction_editor import StoryboardInstructionEditor


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
        self.edit.hide()
        self.status = QLabel()
        layout.addWidget(self.status)
        self.instruction_editor = StoryboardInstructionEditor(tr)
        self.instruction_editor.changed.connect(self._instructions_changed)
        layout.addWidget(self.instruction_editor, 1)
        note = QLabel(tr("continuity_settings_note",
            "Changes apply to the next analysis, not to existing scenes. The project records the instructions used. Editing instructions creates a customized version of the selected process; step dependencies and JSON contracts remain fixed."))
        note.setWordWrap(True)
        layout.addWidget(note)

        for control in (self.block_size,):
            control.currentIndexChanged.connect(self._changed)
        self.characters.valueChanged.connect(self._changed)
        self.set_configuration({})

    def set_configuration(self, config):
        self._loading = True
        config = normalize(config)
        self._prompts = deepcopy(config["prompts"])
        self.instruction_editor.set_prompts(self._prompts)
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

    def _instructions_changed(self):
        self._prompts = self.instruction_editor.prompts()
        self._changed()

    def _edit(self):
        # Reusable draft editor for callers that need explicit Save / Cancel.
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("continuity_instructions", "Continuity analysis instructions"))
        dialog.resize(1380, 900)
        layout = QVBoxLayout(dialog)
        editor = StoryboardInstructionEditor(self.tr)
        editor.set_prompts(self._prompts)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._prompts = editor.prompts()
            self.instruction_editor.set_prompts(self._prompts)
            self._changed()
