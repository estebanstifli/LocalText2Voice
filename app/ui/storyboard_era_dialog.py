"""Review visual period descriptions and explicitly assign them to scenes."""
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QPlainTextEdit,
    QTabWidget, QWidget, QLabel, QListWidget, QListWidgetItem, QComboBox, QDialogButtonBox)

from app.core.storyboard_eras import revise_period
from app.ui.storyboard_entity_scenes import EntityScenesPanel, timestamp


class _SceneSource(QObject):
    scenesChanged = Signal()
    projectChanged = Signal(object)

    def __init__(self, plan, project_dir, parent):
        super().__init__(parent)
        self.plan = plan
        self._source_project_dir = project_dir

    def scenes(self):
        return self.plan.get("scenes", [])


class StoryboardEraDialog(QDialog):
    def __init__(self, tr, plan, identifier, parent=None, *, page=None, read_only=False):
        super().__init__(parent)
        self.plan, self.identifier = plan, identifier
        self.record = next((r for r in plan.get("continuity", {}).get("eras", []) if r["id"] == identifier), {})
        self.setWindowTitle(tr("era_edit_title", "Review historical period"))
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        form = QFormLayout()
        detail_layout.addLayout(form)
        detail_layout.addStretch(1)
        self.description = QLineEdit(str(self.record.get("name") or self.record.get("description") or ""))
        self.context = QPlainTextEdit(str(self.record.get("material_culture") or ""))
        self.context.setMaximumHeight(180)
        self.description.setReadOnly(read_only)
        self.context.setReadOnly(read_only)
        form.addRow(tr("analysis_category_eras", "Eras / periods"), self.description)
        form.addRow(tr("analysis_era_context", "Visual context"), self.context)
        evidence = QLabel(str(self.record.get("evidence") or ""))
        evidence.setWordWrap(True)
        evidence.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(tr("era_evidence", "Source evidence"), evidence)
        basis = str(self.record.get("reason") or "unknown")
        form.addRow(tr("era_basis", "Origin"), QLabel(tr("era_basis_" + basis, basis)))
        self.merge = QComboBox()
        self.merge.addItem(tr("era_keep_separate", "Keep this period"), "")
        for record in plan.get("continuity", {}).get("eras", []):
            if record["id"] != identifier:
                self.merge.addItem(record.get("name") or record.get("description") or record["id"], record["id"])
        self.merge.setEnabled(not read_only)
        form.addRow(tr("era_merge", "Merge into"), self.merge)
        note = QLabel(tr("era_edit_note", "Changes affect future image prompts. Existing images and custom prompts are preserved. Merging uses the destination period's visual context."))
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(details, tr("era_details", "Period details"))
        self.source = page or _SceneSource(plan, "", self)
        gallery = EntityScenesPanel(tr, self.record, "era", self.source, self)
        tabs.addTab(gallery, tr("analysis_appearances_eras", "Era appearances"))
        self.assignments = QListWidget()
        for index, scene in enumerate(plan.get("scenes", []), 1):
            label = tr("video_storyboard_scene_number", "Scene {number}", number=index)
            label += " · " + timestamp(scene.get("start_seconds", 0)) + " · " + str(scene.get("semantic_title") or scene.get("narration") or "")[:130]
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(scene.get("id") or scene.get("scene_id") or ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if scene.get("era_state_id") == identifier else Qt.CheckState.Unchecked)
            if read_only:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.assignments.addItem(item)
        tabs.addTab(self.assignments, tr("era_assign_scenes", "Assign scenes"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close if read_only else
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if not read_only:
            save = buttons.button(QDialogButtonBox.StandardButton.Save)
            save.setText(tr("save", "Save"))
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("cancel", "Cancel"))
            self.description.textChanged.connect(lambda value: save.setEnabled(bool(value.strip())))
            save.setEnabled(bool(self.description.text().strip()))

    def revised_plan(self):
        selected = [self.assignments.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.assignments.count())
                    if self.assignments.item(i).checkState() == Qt.CheckState.Checked]
        return revise_period(self.plan, self.identifier, self.description.text(), self.context.toPlainText(),
                             selected, str(self.merge.currentData() or ""))
