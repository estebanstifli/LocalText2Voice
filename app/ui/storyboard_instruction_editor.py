"""Entity-oriented editor for the actual audiobook analysis phases."""
from copy import deepcopy
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QComboBox, QPlainTextEdit, QPushButton, QSplitter,
)
from app.utils.paths import resource_root
from app.core.storyboard_analysis_settings import prompt_catalog, instruction, contract_for

GROUPS = [
    ("characters", "Characters", ["conversation_report", "conversation_additions", "characters_profiles", "character_changes", "character_portraits"]),
    ("locations", "Locations", ["conversation_locations", "location_additions", "locations_profiles"]),
    ("scenes", "Scenes", ["conversation_scenes", "scene_structure", "scene_visuals"]),
    ("objects", "Important objects", ["conversation_objects", "object_additions", "objects_profiles"]),
    ("eras", "Eras / periods", ["conversation_eras", "era_structure"]),
]

PHASE_CONTEXT = {
    "conversation_report": "Audiobook passage → characters, appearance changes and story summary. Runs only when character analysis is selected.",
    "conversation_additions": "Previous character report + next report → only newly discovered characters. Keeps identities consistent across blocks.",
    "characters_profiles": "Reviewed character report → reusable visual portraits. These text descriptions are later included in image prompts.",
    "character_changes": "Original passage + reports → explicit changes in age, clothes, hair or appearance. Runs in the full continuity plan.",
    "character_portraits": "Previous portrait + verified changes → updated portrait. Runs only when a supported change was found.",
    "conversation_locations": "Proposed scene summary → physical locations and their stated appearance. Runs only when locations are selected.",
    "location_additions": "Previous location report + next report → new places, without duplicating the same physical location.",
    "locations_profiles": "Reviewed location report → concise visual profiles. Lighting changes do not create a different location.",
    "conversation_objects": "Original audiobook passage → recurring or important physical objects. Runs only when objects are selected.",
    "object_additions": "Previous object report + next report → new objects. Ownership or condition changes do not create another object.",
    "objects_profiles": "Reviewed object report → canonical names and visual descriptions, without incidental props.",
    "conversation_eras": "Audiobook passage → historical periods, evidence and source quotes. Automatic-period modes only; manual periods skip this phase.",
    "era_structure": "Reviewed period reports → structured periods and source anchors. The app validates quotes and assigns narration times.",
    "conversation_scenes": "Short audiobook excerpt → proposed illustrations and literal opening sentences. Scene timing is assigned later.",
    "scene_structure": "Reviewed scene proposals → ordered titles and source quotes. The app aligns quotes to narration and splits long scenes at your maximum duration.",
    "scene_visuals": "Aligned narration intervals + entity names + story context → image descriptions. Entity appearances and the chosen visual style are added afterward.",
}


class StoryboardInstructionEditor(QWidget):
    changed = Signal()

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr
        self._draft = {}
        self._current = None
        self._loading = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter()
        layout.addWidget(splitter)
        self.entities = QListWidget()
        self.entities.setObjectName("continuityEntities")
        self.entities.setIconSize(QSize(78, 78))
        self.entities.setMinimumWidth(215)
        self.entities.setMaximumWidth(290)
        for key, title, _ in GROUPS:
            pixmap = QPixmap(str(resource_root() / "assets" / "storyboard_analysis" / f"{key}.png"))
            if not pixmap.isNull():
                w, h = round(pixmap.width() / 1.6), round(pixmap.height() / 1.6)
                pixmap = pixmap.copy((pixmap.width() - w) // 2, (pixmap.height() - h) // 2, w, h)
            item = QListWidgetItem(QIcon(pixmap), tr("analysis_category_" + key, title))
            item.setSizeHint(QSize(220, 94))
            self.entities.addItem(item)
        splitter.addWidget(self.entities)
        right = QWidget()
        pane = QVBoxLayout(right)
        pane.setContentsMargins(16, 0, 0, 0)
        self.phases = QComboBox()
        self.phases.setObjectName("continuityPhases")
        pane.addWidget(self.phases)
        self.context = QLabel()
        self.context.setWordWrap(True)
        self.context.setObjectName("helperLabel")
        pane.addWidget(self.context)
        self.state = QLabel()
        pane.addWidget(self.state)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("continuityInstructionEditor")
        self.editor.setMinimumHeight(180)
        pane.addWidget(self.editor, 1)
        row = QHBoxLayout()
        restore = QPushButton(tr("analysis_restore_instruction", "Restore default"))
        restore.clicked.connect(self._restore)
        self.technical = QPushButton(tr("continuity_show_contract", "Show protected technical contract"))
        self.technical.setCheckable(True)
        row.addWidget(restore)
        row.addWidget(self.technical)
        row.addStretch()
        pane.addLayout(row)
        self.contract = QPlainTextEdit()
        self.contract.setReadOnly(True)
        self.contract.setMaximumHeight(95)
        self.contract.hide()
        self.technical.toggled.connect(self.contract.setVisible)
        pane.addWidget(self.contract)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self.entities.currentRowChanged.connect(self._entity_changed)
        self.phases.currentIndexChanged.connect(self._phase_changed)
        self.editor.textChanged.connect(self._edited)
        self.entities.setCurrentRow(0)

    def _capture(self):
        if self._current:
            text = self.editor.toPlainText().strip()
            if text and text != prompt_catalog()[self._current][1].strip():
                self._draft[self._current] = text
            else:
                self._draft.pop(self._current, None)

    def _entity_changed(self, index):
        self._capture()
        self.phases.blockSignals(True)
        self.phases.clear()
        catalog = prompt_catalog()
        for number, key in enumerate(GROUPS[max(0, index)][2], 1):
            self.phases.addItem(f"{number}. " + self.tr("analysis_phase_" + key, catalog[key][0]), key)
        self.phases.blockSignals(False)
        self._current = None
        self._phase_changed()

    def _phase_changed(self, *_):
        self._capture()
        self._current = self.phases.currentData()
        if not self._current:
            return
        self._loading = True
        self.editor.setPlainText(instruction({"prompts": self._draft}, self._current))
        self.context.setText(self.tr("analysis_context_" + self._current, PHASE_CONTEXT[self._current]))
        self.contract.setPlainText(contract_for(self._current))
        self._loading = False
        self._refresh_state()

    def _refresh_state(self):
        custom = self._current in self._draft
        self.state.setText(self.tr("analysis_instruction_custom" if custom else "analysis_instruction_default",
                                  "Customized instructions" if custom else "Default instructions"))

    def _edited(self):
        if not self._loading:
            self._capture()
            self._refresh_state()
            self.changed.emit()

    def _restore(self):
        self.editor.setPlainText(prompt_catalog()[self._current][1])

    def set_prompts(self, prompts):
        self._draft = deepcopy(prompts)
        self._current = None
        self._phase_changed()

    def prompts(self):
        self._capture()
        return deepcopy(self._draft)
