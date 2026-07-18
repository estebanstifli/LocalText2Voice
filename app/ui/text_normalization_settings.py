from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.text_normalization import (
    NORMALIZATION_RULE_KEYS,
    TextNormalizationStore,
    normalization_rule_settings,
    number_rules_available,
)
from app.ui.icons import ui_icon


Translate = Callable[..., str]


class TextNormalizationSettingsWidget(QWidget):
    settingsChanged = Signal()
    dictionaryChanged = Signal()

    def __init__(
        self,
        translate: Translate,
        store: TextNormalizationStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr = translate
        self.store = store or TextNormalizationStore()
        self._rules = normalization_rule_settings()
        self._loading = False
        self._build_ui()
        self._reload_dictionaries(preferred_editor="en")
        self._reload_categories()
        self.refresh_entries()

    def configuration(self) -> dict[str, object]:
        self._rules["enabled"] = self.rules_enabled_checkbox.isChecked()
        return {
            "enabled": self.enabled_checkbox.isChecked(),
            "language": str(self.language_combo.currentData() or "auto"),
            "rules": dict(self._rules),
        }

    def set_configuration(self, values: object) -> None:
        config = values if isinstance(values, dict) else {}
        self.enabled_checkbox.blockSignals(True)
        self.language_combo.blockSignals(True)
        self.rules_enabled_checkbox.blockSignals(True)
        self.enabled_checkbox.setChecked(bool(config.get("enabled", False)))
        self._rules = normalization_rule_settings(config.get("rules"))
        self.rules_enabled_checkbox.setChecked(self._rules["enabled"])
        wanted = str(config.get("language", "auto"))
        index = self.language_combo.findData(wanted)
        self.language_combo.setCurrentIndex(index if index >= 0 else 0)
        self.language_combo.blockSignals(False)
        self.rules_enabled_checkbox.blockSignals(False)
        self.enabled_checkbox.blockSignals(False)
        self._update_enabled_state()
        self._update_rules_summary()
        if wanted != "auto":
            editor_index = self.editor_language_combo.findData(wanted)
            if editor_index >= 0:
                self.editor_language_combo.setCurrentIndex(editor_index)
        self._reload_categories()
        self.refresh_entries()

    def refresh_entries(self) -> None:
        language = self._editor_language()
        category = str(self.category_combo.currentData() or "")
        search = self.search_edit.text().strip()
        entries = self.store.list_entries(
            language,
            category=category,
            search=search,
        )
        self._loading = True
        self.entries_table.setSortingEnabled(False)
        self.entries_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            enabled_item = QTableWidgetItem("")
            enabled_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            enabled_item.setCheckState(
                Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
            )
            enabled_item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self.entries_table.setItem(row, 0, enabled_item)
            self.entries_table.setItem(row, 1, QTableWidgetItem(entry.category))
            self.entries_table.setItem(row, 2, QTableWidgetItem(entry.source))
            self.entries_table.setItem(row, 3, QTableWidgetItem(entry.replacement))
        self.entries_table.setSortingEnabled(True)
        self._loading = False
        self.count_label.setText(
            self.tr(
                "normalization_entry_count",
                "{count} entries",
                count=len(entries),
            )
        )
        self._update_delete_button()
        self._update_dictionary_actions()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(
            self.tr(
                "text_normalization_description",
                "Expand abbreviations, numbers, quantities, and language-specific forms before text is sent to the TTS engine. The source text in the editor is not changed.",
            )
        )
        intro.setObjectName("helperLabel")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        controls = QFrame()
        controls.setObjectName("card")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(14, 12, 14, 12)
        self.enabled_checkbox = QCheckBox(
            self.tr("enable_text_normalization", "Enable text normalization")
        )
        self.enabled_checkbox.toggled.connect(self._on_configuration_changed)
        self.rules_enabled_checkbox = QCheckBox(
            self.tr("normalization_enable_rules", "Automatic rules")
        )
        self.rules_enabled_checkbox.setChecked(True)
        self.rules_enabled_checkbox.setToolTip(
            self.tr(
                "normalization_enable_rules_help",
                "Turn all number, date, quantity, and Roman numeral rules on or off. Dictionary replacements remain active.",
            )
        )
        self.rules_enabled_checkbox.toggled.connect(self._on_rules_enabled_changed)
        self.rules_details_button = QPushButton()
        self.rules_details_button.clicked.connect(self._open_rules_dialog)
        self.language_label = QLabel(
            self.tr("normalization_dictionary", "Active dictionary")
        )
        self.language_combo = QComboBox()
        self.language_combo.addItem(
            self.tr("normalization_language_auto", "Auto (voice language)"),
            "auto",
        )
        self.language_combo.currentIndexChanged.connect(
            self._on_configuration_changed
        )
        controls_layout.addWidget(self.enabled_checkbox)
        controls_layout.addWidget(self.rules_enabled_checkbox)
        controls_layout.addWidget(self.rules_details_button)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.language_label)
        controls_layout.addWidget(self.language_combo)
        layout.addWidget(controls)

        auto_help = QLabel(
            self.tr(
                "normalization_auto_help",
                "Auto uses the language reported by the selected voice. You can edit every starter dictionary or create and import one for another language.",
            )
        )
        auto_help.setObjectName("helperLabel")
        auto_help.setWordWrap(True)
        layout.addWidget(auto_help)

        dictionary_tools = QFrame()
        dictionary_tools.setObjectName("card")
        dictionary_tools_layout = QHBoxLayout(dictionary_tools)
        dictionary_tools_layout.setContentsMargins(14, 10, 14, 10)
        dictionary_tools_layout.addWidget(
            QLabel(self.tr("normalization_edit_dictionary", "Edit dictionary"))
        )
        self.editor_language_combo = QComboBox()
        self.editor_language_combo.currentIndexChanged.connect(
            self._on_editor_dictionary_changed
        )
        dictionary_tools_layout.addWidget(self.editor_language_combo, 1)
        self.create_dictionary_button = QPushButton(
            self.tr("normalization_create_dictionary", "Create dictionary")
        )
        self.create_dictionary_button.setIcon(ui_icon("add"))
        self.create_dictionary_button.clicked.connect(self._create_dictionary)
        self.import_button = QPushButton(
            self.tr("normalization_import_dictionary", "Import JSON")
        )
        self.import_button.clicked.connect(self._import_dictionary)
        self.export_button = QPushButton(
            self.tr("normalization_export_dictionary", "Export JSON")
        )
        self.export_button.clicked.connect(self._export_dictionary)
        self.delete_dictionary_button = QPushButton(
            self.tr("normalization_delete_dictionary", "Delete dictionary")
        )
        self.delete_dictionary_button.setIcon(ui_icon("delete"))
        self.delete_dictionary_button.clicked.connect(self._delete_dictionary)
        dictionary_tools_layout.addWidget(self.create_dictionary_button)
        dictionary_tools_layout.addWidget(self.import_button)
        dictionary_tools_layout.addWidget(self.export_button)
        dictionary_tools_layout.addWidget(self.delete_dictionary_button)
        layout.addWidget(dictionary_tools)

        json_help = QLabel(
            self.tr(
                "normalization_json_help",
                "JSON workflow: export any dictionary as a template, edit its entries in an external editor (or with an AI assistant), then import it and choose Merge or Replace.",
            )
        )
        json_help.setObjectName("helperLabel")
        json_help.setWordWrap(True)
        layout.addWidget(json_help)

        self.number_rules_label = QLabel("")
        self.number_rules_label.setObjectName("helperLabel")
        self.number_rules_label.setWordWrap(True)
        layout.addWidget(self.number_rules_label)

        filter_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText(
            self.tr(
                "normalization_search_placeholder",
                "Search source or replacement...",
            )
        )
        self.search_edit.textChanged.connect(lambda _text: self.refresh_entries())
        self.category_combo = QComboBox()
        self.category_combo.currentIndexChanged.connect(
            lambda _index: self.refresh_entries()
        )
        filter_layout.addWidget(self.search_edit, 1)
        filter_layout.addWidget(self.category_combo)
        layout.addLayout(filter_layout)

        self.entries_table = QTableWidget(0, 4)
        self.entries_table.setHorizontalHeaderLabels(
            [
                self.tr("normalization_enabled_column", "On"),
                self.tr("normalization_category_column", "Category"),
                self.tr("normalization_source_column", "Find"),
                self.tr("normalization_replacement_column", "Replace with"),
            ]
        )
        self.entries_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.entries_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.entries_table.setAlternatingRowColors(True)
        self.entries_table.verticalHeader().setVisible(False)
        self.entries_table.setSortingEnabled(True)
        header = self.entries_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.entries_table.itemChanged.connect(self._on_entry_changed)
        layout.addWidget(self.entries_table, 1)

        footer = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setObjectName("helperLabel")
        self.add_button = QPushButton(
            self.tr("normalization_add_entry", "Add entry")
        )
        self.add_button.setIcon(ui_icon("add"))
        self.add_button.clicked.connect(self._add_entry)
        self.delete_button = QPushButton(
            self.tr("normalization_delete_entries", "Delete selected")
        )
        self.delete_button.setIcon(ui_icon("delete"))
        self.delete_button.clicked.connect(self._delete_selected_entries)
        self.entries_table.itemSelectionChanged.connect(
            self._update_delete_button
        )
        self.reset_button = QPushButton(
            self.tr(
                "normalization_reset_dictionary",
                "Reset dictionary",
            )
        )
        self.reset_button.setIcon(ui_icon("refresh"))
        self.reset_button.clicked.connect(self._reset_dictionary)
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        footer.addWidget(self.add_button)
        footer.addWidget(self.delete_button)
        footer.addWidget(self.reset_button)
        layout.addLayout(footer)
        self._update_enabled_state()
        self._update_rules_summary()

    def _reload_dictionaries(self, *, preferred_editor: str = "") -> None:
        active = str(self.language_combo.currentData() or "auto")
        editor = preferred_editor or str(
            self.editor_language_combo.currentData() or "en"
        )
        dictionaries = self.store.list_dictionaries()
        self.language_combo.blockSignals(True)
        self.editor_language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem(
            self.tr("normalization_language_auto", "Auto (voice language)"),
            "auto",
        )
        self.editor_language_combo.clear()
        for dictionary in dictionaries:
            label = f"{dictionary.name} ({dictionary.language})"
            self.language_combo.addItem(label, dictionary.language)
            self.editor_language_combo.addItem(label, dictionary.language)
        active_index = self.language_combo.findData(active)
        self.language_combo.setCurrentIndex(active_index if active_index >= 0 else 0)
        editor_index = self.editor_language_combo.findData(editor)
        self.editor_language_combo.setCurrentIndex(editor_index if editor_index >= 0 else 0)
        self.editor_language_combo.blockSignals(False)
        self.language_combo.blockSignals(False)
        self._update_dictionary_actions()

    def _reload_categories(self) -> None:
        selected = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem(
            self.tr("normalization_all_categories", "All categories"),
            "",
        )
        for category in self.store.categories(self._editor_language()):
            self.category_combo.addItem(category.replace("_", " ").title(), category)
        index = self.category_combo.findData(selected)
        self.category_combo.setCurrentIndex(index if index >= 0 else 0)
        self.category_combo.blockSignals(False)

    def _on_editor_dictionary_changed(self, _index: int) -> None:
        self.search_edit.clear()
        self._reload_categories()
        self.refresh_entries()
        self._update_dictionary_actions()

    def _on_configuration_changed(self, _value: object = None) -> None:
        self._update_enabled_state()
        self._reload_categories()
        self.refresh_entries()
        self.settingsChanged.emit()

    def _on_rules_enabled_changed(self, enabled: bool) -> None:
        self._rules["enabled"] = bool(enabled)
        self._update_rules_summary()
        self.settingsChanged.emit()

    def _open_rules_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(
            self.tr(
                "normalization_rules_dialog_title",
                "Automatic normalization rules",
            )
        )
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        helper = QLabel(
            self.tr(
                "normalization_rules_dialog_help",
                "Choose which structured values are converted before TTS. Turning a rule off preserves that value exactly, even when the general number rule remains on.",
            )
        )
        helper.setObjectName("helperLabel")
        helper.setWordWrap(True)
        layout.addWidget(helper)
        labels = {
            "numbers": self.tr(
                "normalization_rule_numbers", "Numbers and decimals"
            ),
            "ordinals": self.tr("normalization_rule_ordinals", "Ordinals"),
            "dates": self.tr("normalization_rule_dates", "Dates"),
            "currencies": self.tr(
                "normalization_rule_currencies", "Currencies"
            ),
            "percentages": self.tr(
                "normalization_rule_percentages", "Percentages"
            ),
            "measurements": self.tr(
                "normalization_rule_measurements", "Measurements and units"
            ),
            "roman_numerals": self.tr(
                "normalization_rule_roman_numerals", "Roman numerals"
            ),
        }
        checkboxes: dict[str, QCheckBox] = {}
        for key in NORMALIZATION_RULE_KEYS:
            checkbox = QCheckBox(labels[key])
            checkbox.setChecked(bool(self._rules.get(key, True)))
            checkboxes[key] = checkbox
            layout.addWidget(checkbox)
        bulk_buttons = QHBoxLayout()
        enable_all = QPushButton(
            self.tr("normalization_rules_enable_all", "Enable all")
        )
        disable_all = QPushButton(
            self.tr("normalization_rules_disable_all", "Disable all")
        )
        enable_all.clicked.connect(
            lambda: [checkbox.setChecked(True) for checkbox in checkboxes.values()]
        )
        disable_all.clicked.connect(
            lambda: [checkbox.setChecked(False) for checkbox in checkboxes.values()]
        )
        bulk_buttons.addWidget(enable_all)
        bulk_buttons.addWidget(disable_all)
        bulk_buttons.addStretch(1)
        layout.addLayout(bulk_buttons)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(self.tr("cancel", "Cancel"))
        save = QPushButton(self.tr("save", "Save"))
        cancel.clicked.connect(dialog.reject)
        save.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for key, checkbox in checkboxes.items():
            self._rules[key] = checkbox.isChecked()
        self._update_rules_summary()
        self.settingsChanged.emit()

    def _update_rules_summary(self) -> None:
        enabled_count = sum(
            bool(self._rules.get(key, True)) for key in NORMALIZATION_RULE_KEYS
        )
        if not self.rules_enabled_checkbox.isChecked():
            text = self.tr(
                "normalization_rules_details_off",
                "Rule details (all off)",
            )
        else:
            text = self.tr(
                "normalization_rules_details_count",
                "Rule details ({enabled}/{total})",
                enabled=enabled_count,
                total=len(NORMALIZATION_RULE_KEYS),
            )
        self.rules_details_button.setText(text)

    def _update_enabled_state(self) -> None:
        enabled = self.enabled_checkbox.isChecked()
        self.language_combo.setEnabled(enabled)
        self.rules_enabled_checkbox.setEnabled(enabled)
        self.rules_details_button.setEnabled(enabled)

    def _editor_language(self) -> str:
        return str(self.editor_language_combo.currentData() or "en")

    def _update_dictionary_actions(self) -> None:
        dictionary = self.store.dictionary(self._editor_language())
        if dictionary is None:
            self.delete_dictionary_button.setEnabled(False)
            self.reset_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.number_rules_label.clear()
            return
        self.delete_dictionary_button.setEnabled(not dictionary.is_builtin)
        self.reset_button.setEnabled(dictionary.is_builtin)
        self.export_button.setEnabled(True)
        self.reset_button.setText(
            self.tr(
                "normalization_reset_named_dictionary",
                "Reset {name} dictionary",
                name=dictionary.name,
            )
        )
        if number_rules_available(dictionary.language):
            status = self.tr(
                "normalization_number_rules_available",
                "Automatic rules are available for numbers, decimals, percentages, currencies, measurements, dates, Roman numerals, and common ordinal forms. These rules are separate from the editable dictionary.",
            )
        else:
            status = self.tr(
                "normalization_number_rules_unavailable",
                "Dictionary replacements and measurement units are available. Automatic number and ordinal expansion is not yet available for this language.",
            )
        self.number_rules_label.setText(status)

    def _entry_id_for_row(self, row: int) -> int | None:
        item = self.entries_table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def _create_dictionary(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(
            self.tr("normalization_create_dictionary", "Create dictionary")
        )
        dialog.setMinimumWidth(430)
        layout = QVBoxLayout(dialog)
        help_label = QLabel(
            self.tr(
                "normalization_create_help",
                "Use a language or locale code such as nl, ko, pt-br, or en-gb. The new dictionary starts empty and can be edited here or populated by importing JSON.",
            )
        )
        help_label.setObjectName("helperLabel")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        form = QFormLayout()
        name_edit = QLineEdit()
        code_edit = QLineEdit()
        code_edit.setPlaceholderText("nl, ko, pt-br...")
        form.addRow(self.tr("normalization_dictionary_name", "Name"), name_edit)
        form.addRow(
            self.tr("normalization_dictionary_code", "Language code"), code_edit
        )
        layout.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(self.tr("cancel", "Cancel"))
        create = QPushButton(
            self.tr("normalization_create_dictionary", "Create dictionary")
        )
        create.setIcon(ui_icon("add"))
        cancel.clicked.connect(dialog.reject)
        create.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(create)
        layout.addLayout(buttons)
        name_edit.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.store.create_dictionary(code_edit.text(), name_edit.text())
            language = code_edit.text().strip().casefold().replace("_", "-")
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(
                self,
                self.tr("normalization_dictionary_error", "Dictionary error"),
                str(exc),
            )
            return
        self._reload_dictionaries(preferred_editor=language)
        self._reload_categories()
        self.refresh_entries()
        self.dictionaryChanged.emit()

    def _export_dictionary(self) -> None:
        language = self._editor_language()
        suggested = f"{language}-normalization.json"
        file_name, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("normalization_export_dictionary", "Export JSON"),
            suggested,
            self.tr("json_files_filter", "JSON files (*.json);;All files (*.*)"),
        )
        if not file_name:
            return
        try:
            payload = self.store.export_dictionary(language)
            Path(file_name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                self.tr("normalization_export_error", "Could not export dictionary"),
                str(exc),
            )

    def _import_dictionary(self) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr("normalization_import_dictionary", "Import JSON"),
            "",
            self.tr("json_files_filter", "JSON files (*.json);;All files (*.*)"),
        )
        if not file_name:
            return
        try:
            path = Path(file_name)
            if path.stat().st_size > 5 * 1024 * 1024:
                raise ValueError("The JSON file is larger than the 5 MB safety limit.")
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("The JSON root must be an object.")
            language, name, count = self.store.inspect_import(payload)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            QMessageBox.warning(
                self,
                self.tr("normalization_import_error", "Could not import dictionary"),
                str(exc),
            )
            return

        mode = "merge"
        if self.store.dictionary(language) is not None:
            prompt = QMessageBox(self)
            prompt.setIcon(QMessageBox.Icon.Question)
            prompt.setWindowTitle(
                self.tr("normalization_import_mode_title", "Import dictionary")
            )
            prompt.setText(
                self.tr(
                    "normalization_import_mode_message",
                    "{name} ({language}) already exists. Merge updates matching entries and keeps the rest. Replace removes all current entries first.",
                    name=name,
                    language=language,
                )
            )
            merge_button = prompt.addButton(
                self.tr("normalization_import_merge", "Merge"),
                QMessageBox.ButtonRole.AcceptRole,
            )
            replace_button = prompt.addButton(
                self.tr("normalization_import_replace", "Replace all"),
                QMessageBox.ButtonRole.DestructiveRole,
            )
            prompt.addButton(QMessageBox.StandardButton.Cancel)
            prompt.exec()
            if prompt.clickedButton() is replace_button:
                mode = "replace"
            elif prompt.clickedButton() is not merge_button:
                return
        try:
            imported_language, imported_count = self.store.import_dictionary(
                payload, mode=mode
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(
                self,
                self.tr("normalization_import_error", "Could not import dictionary"),
                str(exc),
            )
            return
        self._reload_dictionaries(preferred_editor=imported_language)
        self._reload_categories()
        self.refresh_entries()
        self.dictionaryChanged.emit()
        QMessageBox.information(
            self,
            self.tr("normalization_import_done_title", "Dictionary imported"),
            self.tr(
                "normalization_import_done_message",
                "Imported {count} entries into {language}.",
                count=imported_count if imported_count != count else count,
                language=imported_language,
            ),
        )

    def _delete_dictionary(self) -> None:
        dictionary = self.store.dictionary(self._editor_language())
        if dictionary is None or dictionary.is_builtin:
            return
        choice = QMessageBox.question(
            self,
            self.tr("normalization_delete_dictionary", "Delete dictionary"),
            self.tr(
                "normalization_delete_dictionary_confirm",
                "Delete {name} ({language}) and all of its entries? This cannot be undone.",
                name=dictionary.name,
                language=dictionary.language,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_dictionary(dictionary.language)
        self._reload_dictionaries(preferred_editor="en")
        self._reload_categories()
        self.refresh_entries()
        self.settingsChanged.emit()
        self.dictionaryChanged.emit()

    def _on_entry_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row = item.row()
        entry_id = self._entry_id_for_row(row)
        if entry_id is None:
            return
        try:
            self.store.update_entry(
                entry_id,
                category=self.entries_table.item(row, 1).text(),
                source=self.entries_table.item(row, 2).text(),
                replacement=self.entries_table.item(row, 3).text(),
                enabled=(
                    self.entries_table.item(row, 0).checkState()
                    == Qt.CheckState.Checked
                ),
            )
        except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(
                self,
                self.tr("normalization_entry_error", "Could not save entry"),
                str(exc),
            )
        self._reload_categories()
        self.refresh_entries()
        self.dictionaryChanged.emit()

    def _add_entry(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(
            self.tr("normalization_add_entry", "Add entry")
        )
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        category = QComboBox()
        category.setEditable(True)
        for value in self.store.categories(self._editor_language()):
            category.addItem(value.replace("_", " ").title(), value)
        source = QLineEdit()
        replacement = QLineEdit()
        enabled = QCheckBox(
            self.tr("normalization_enabled_column", "On")
        )
        enabled.setChecked(True)
        form.addRow(
            self.tr("normalization_category_column", "Category"), category
        )
        form.addRow(self.tr("normalization_source_column", "Find"), source)
        form.addRow(
            self.tr("normalization_replacement_column", "Replace with"),
            replacement,
        )
        form.addRow("", enabled)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(self.tr("cancel", "Cancel"))
        save = QPushButton(self.tr("normalization_add_entry", "Add entry"))
        save.setIcon(ui_icon("add"))
        cancel.clicked.connect(dialog.reject)
        save.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        source.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        category_value = (
            category.currentText().strip().casefold().replace(" ", "_")
        )
        try:
            self.store.add_entry(
                self._editor_language(),
                category_value,
                source.text(),
                replacement.text(),
                enabled=enabled.isChecked(),
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(
                self,
                self.tr("normalization_entry_error", "Could not save entry"),
                str(exc),
            )
            return
        self._reload_categories()
        self.refresh_entries()
        self.dictionaryChanged.emit()

    def _delete_selected_entries(self) -> None:
        entry_ids = [
            entry_id
            for row in {index.row() for index in self.entries_table.selectedIndexes()}
            if (entry_id := self._entry_id_for_row(row)) is not None
        ]
        if not entry_ids:
            return
        choice = QMessageBox.question(
            self,
            self.tr("normalization_delete_title", "Delete dictionary entries"),
            self.tr(
                "normalization_delete_confirm",
                "Delete the selected {count} entries?",
                count=len(entry_ids),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_entries(entry_ids)
        self._reload_categories()
        self.refresh_entries()
        self.dictionaryChanged.emit()

    def _reset_dictionary(self) -> None:
        dictionary = self.store.dictionary(self._editor_language())
        if dictionary is None or not dictionary.is_builtin:
            return
        choice = QMessageBox.question(
            self,
            self.tr(
                "normalization_reset_confirm_title",
                "Reset dictionary",
            ),
            self.tr(
                "normalization_reset_confirm_message",
                "All edits, disabled entries, and custom entries in {name} ({language}) will be replaced with the starter defaults. Continue?",
                name=dictionary.name,
                language=dictionary.language,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self.store.reset_language(dictionary.language)
        self._reload_categories()
        self.refresh_entries()
        self.dictionaryChanged.emit()

    def _update_delete_button(self) -> None:
        self.delete_button.setEnabled(
            bool(self.entries_table.selectionModel().selectedRows())
        )
