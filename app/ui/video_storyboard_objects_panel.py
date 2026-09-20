from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from app.ui.video_storyboard_entity_dialog import VideoStoryboardEntityDialog
from app.ui.icons import ui_icon


class VideoStoryboardObjectsPanel(QWidget):
    """Project-owned reusable props and their visual references."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.tr_text = page.tr_text
        layout = QHBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([self.tr_text("name", "Name"), self.tr_text("video_storyboard_entity_identity", "Permanent visual identity")])
        self.tree.setColumnWidth(0, 220)
        layout.addWidget(self.tree, 1)
        actions = QVBoxLayout()
        self.new_button = QPushButton(self.tr_text("video_storyboard_new_object", "New object"))
        self.edit_button = QPushButton(self.tr_text("video_storyboard_edit_object", "Edit object"))
        self.delete_button = QPushButton(self.tr_text("video_storyboard_delete_object", "Delete object"))
        self.new_state_button = QPushButton(self.tr_text("video_storyboard_new_character_state", "New state"))
        self.edit_state_button = QPushButton(self.tr_text("video_storyboard_edit_object_state", "Edit state"))
        self.delete_state_button = QPushButton(self.tr_text("video_storyboard_delete_character_state", "Delete state"))
        for button in (self.new_button, self.new_state_button):
            button.setIcon(ui_icon("add"))
        for button in (self.edit_button, self.edit_state_button):
            button.setIcon(ui_icon("edit"))
        for button in (self.delete_button, self.delete_state_button):
            button.setIcon(ui_icon("delete", danger=True))
        for button in (self.new_button, self.edit_button, self.delete_button, self.new_state_button, self.edit_state_button, self.delete_state_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.new_button.clicked.connect(lambda: self.edit_record())
        self.edit_button.clicked.connect(lambda: self.edit_record(self.selected()))
        self.delete_button.clicked.connect(self.delete_record)
        self.new_state_button.clicked.connect(lambda: self.edit_state(creating=True))
        self.edit_state_button.clicked.connect(lambda: self.edit_state())
        self.delete_state_button.clicked.connect(self.delete_state)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit_record(self.selected()))
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.refresh()

    def records(self):
        return self.page._plan_metadata.get("continuity", {}).get("objects", [])

    def selected(self):
        item = self.tree.currentItem()
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return next((r for r in self.records() if r.get("id") == key), None)

    def _selection_changed(self):
        selected = self.selected() is not None
        self.edit_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)
        self.new_state_button.setEnabled(selected)
        has_state = self.selected_state() is not None
        self.edit_state_button.setEnabled(has_state)
        self.delete_state_button.setEnabled(has_state)

    def selected_state(self):
        item = self.tree.currentItem()
        record = self.selected()
        state_id = item.data(1, Qt.ItemDataRole.UserRole) if item else None
        return next((s for s in record.get("states", []) if s.get("id") == state_id), None) if record else None

    def refresh(self):
        selected = self.selected()
        self.tree.clear()
        for record in self.records():
            item = QTreeWidgetItem([str(record.get("name") or ""), str(record.get("identity_description") or "")])
            item.setData(0, Qt.ItemDataRole.UserRole, record.get("id"))
            if record.get("reference_image_path"):
                item.setIcon(0, QIcon(record["reference_image_path"]))
            self.tree.addTopLevelItem(item)
            for state in record.get("states", []):
                child = QTreeWidgetItem([f"{state.get('from_seconds', 0):g}–{state.get('to_seconds', 0):g} s", str(state.get("description") or "")])
                child.setData(0, Qt.ItemDataRole.UserRole, record.get("id"))
                child.setData(1, Qt.ItemDataRole.UserRole, state.get("id"))
                item.addChild(child)
            item.setExpanded(True)
            if selected and selected.get("id") == record.get("id"):
                self.tree.setCurrentItem(item)
        self._selection_changed()

    def edit_record(self, record=None):
        dialog = VideoStoryboardEntityDialog(self.tr_text, "object", self.page._entity_total_duration(), record, self, settings=self.page._configuration, storyboard_page=self.page)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        records = self.page._plan_metadata.setdefault("continuity", {}).setdefault("objects", [])
        creating = record is None
        updated = deepcopy(record) if record is not None else {"id": self.page._unique_continuity_id(values["name"], records, "object"), "user_authored": True}
        old_path, old_name = str(updated.get("reference_image_path") or ""), str(updated.get("name") or "")
        for key in ("name", "aliases", "identity_description"):
            updated[key] = values[key]
        updated["reference_image_prompt"] = str(values.get("reference_image_prompt") or "")
        if old_name and old_name != updated["name"] and old_name not in updated["aliases"]:
            updated["aliases"].append(old_name)
        updated["reference_image_path"] = self.page._import_entity_reference(values["reference_image_path"], "objects", updated["id"])
        states = updated.setdefault("states", [])
        if not states:
            states.append({"id": updated["id"] + "_state_1"})
        states[0].update(description=values["state_description"], from_seconds=values["from_seconds"], to_seconds=values["to_seconds"])
        if creating:
            records.append(updated)
        else:
            record.update(updated)
            self.page._replace_entity_reference_paths(old_path, updated["reference_image_path"], old_name, updated["name"])
        self.page._commit_continuity_changes()
        self.refresh()

    def delete_record(self):
        record = self.selected()
        if record is None:
            return
        if QMessageBox.question(self, self.tr_text("video_storyboard_delete_object", "Delete object"),
                                self.tr_text("video_storyboard_delete_object_confirmation", "Delete this object and its visual states?"),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self._remove_state_references({s.get("id") for s in record.get("states", [])})
        self.page._replace_entity_reference_paths(str(record.get("reference_image_path") or ""), "", str(record.get("name") or ""), "")
        self.page._plan_metadata["continuity"]["objects"].remove(record)
        self.page._commit_continuity_changes()
        self.refresh()

    def edit_state(self, creating=False):
        record = self.selected()
        state = None if creating else self.selected_state()
        if record is None or (not creating and state is None):
            return
        draft = deepcopy(record)
        draft["states"] = [deepcopy(state)] if state else [{"from_seconds": 0, "to_seconds": self.page._entity_total_duration(), "description": ""}]
        dialog = VideoStoryboardEntityDialog(self.tr_text, "object", self.page._entity_total_duration(), draft, self, settings=self.page._configuration, storyboard_page=self.page)
        dialog.tabs.setTabEnabled(1, False)
        dialog.generate_image_button.setEnabled(False)
        for field in (dialog.name_edit, dialog.aliases_edit, dialog.identity_edit, dialog.choose_reference_button, dialog.remove_reference_button):
            field.setEnabled(False)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if creating:
            states = record.setdefault("states", [])
            state = {"id": self.page._unique_continuity_id(record["id"] + "_state", states, "object_state")}
            states.append(state)
        state.update(description=values["state_description"], from_seconds=values["from_seconds"], to_seconds=values["to_seconds"])
        self.page._commit_continuity_changes()
        self.refresh()

    def _remove_state_references(self, ids):
        for scene in self.page._scenes:
            overrides = scene.generation_overrides
            if isinstance(overrides, dict) and "object_state_ids" in overrides:
                overrides["object_state_ids"] = [v for v in overrides["object_state_ids"] if v not in ids]

    def delete_state(self):
        state = self.selected_state()
        if state is None:
            return
        self._remove_state_references({state["id"]})
        self.selected()["states"].remove(state)
        self.page._commit_continuity_changes()
        self.refresh()
