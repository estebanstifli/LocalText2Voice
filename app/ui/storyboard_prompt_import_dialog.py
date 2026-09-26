"""Friendly entry points for native analysis and an external, timed storyboard."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSaveFile, QIODevice
from PySide6.QtGui import QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox,
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from app.core.storyboard_prompt_import import (
    MAX_TEXT_LENGTH, PromptImportError, build_import_plan, export_prompts,
    format_timestamp, parse_prompts, source_transcript,
)
from app.utils.paths import resource_root


def _art(name: str) -> QPixmap:
    return QPixmap(str(resource_root() / "assets" / "storyboard_import" / f"{name}.png"))


def _label(text, *, heading=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if heading:
        font = label.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        label.setFont(font)
        label.setStyleSheet("font-size: 18px; font-weight: 600; background: transparent;")
    return label


class StoryboardSourceDialog(QDialog):
    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle(tr("storyboard_source_title", "Create your storyboard"))
        self.resize(640, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(_label(tr("storyboard_source_heading", "How would you like to plan your scenes?"), heading=True))
        layout.addWidget(_label(tr("storyboard_source_intro", "Two ways to prepare the same editable timeline. Choose what works for you.")))
        self.llm_button = self._source_card(
            tr("storyboard_source_llm", "Analyze with the app's AI"),
            tr("storyboard_source_llm_help", "Use your configured LLM and the current analysis workflow."))
        self.external_button = self._source_card(
            tr("storyboard_source_external", "Import / edit scene prompts"),
            tr("storyboard_source_external_help", "Bring timed prompts from ChatGPT, Claude or your own writing."))
        for button, name, choice in ((self.llm_button, "assistant", "llm"), (self.external_button, "external", "external")):
            button.art_label.setPixmap(_art(name).scaled(76, 76, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            button.setMinimumHeight(96)
            button.clicked.connect(lambda _checked=False, value=choice: self._choose(value))
            layout.addWidget(button)
        cancel = QPushButton(tr("cancel", "Cancel"))
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel)
        layout.addLayout(row)

    @staticmethod
    def _source_card(title, description):
        button = QPushButton()
        button.setAccessibleName(title)
        button.setAccessibleDescription(description)
        button.setAutoDefault(False)
        row = QHBoxLayout(button)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(16)
        button.art_label = QLabel()
        button.art_label.setFixedSize(76, 76)
        row.addWidget(button.art_label)
        words = QVBoxLayout()
        heading = _label(title)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        detail = _label(description)
        words.addWidget(heading)
        words.addWidget(detail)
        row.addLayout(words, 1)
        for label in (button.art_label, heading, detail):
            label.setStyleSheet("background: transparent;")
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return button

    def _choose(self, choice):
        self.choice = choice
        self.accept()


class StoryboardPromptImportDialog(QDialog):
    def __init__(self, tr, source: dict, state: dict | None = None, parent=None):
        super().__init__(parent)
        self.tr, self.source, self.state = tr, deepcopy(source), deepcopy(state or {})
        self.imported_plan = None
        self.entries = []
        self.setWindowTitle(tr("prompt_import_title", "Import / edit scene prompts"))
        self.resize(1120, 820)
        screen = self.screen().availableGeometry()
        self.resize(min(1120, screen.width() - 48), min(820, screen.height() - 64))
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)
        header = QHBoxLayout()
        art = QLabel()
        art.setPixmap(_art("external").scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        art.setFixedSize(100, 100)
        header.addWidget(art)
        words = QVBoxLayout()
        words.addWidget(_label(tr("prompt_import_heading", "Your ideas. Every scene in place."), heading=True))
        words.addWidget(_label(tr("prompt_import_intro", "Prepare prompts with an external AI, paste them here, then check the timeline. No in-app LLM analysis is needed.")))
        duration = format_timestamp(round(float(source.get("duration_seconds") or 0) * 1000))
        words.addWidget(_label(tr("prompt_import_audio", "{title} · Timeline duration: {duration}", title=source.get("title") or tr("prompt_import_project", "Current project"), duration=duration)))
        header.addLayout(words, 1)
        root.addLayout(header)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._build_prepare_tab()
        self._build_editor_tab()
        footer = QHBoxLayout()
        footer.addWidget(_label(tr("prompt_import_footer", "Nothing is changed until you apply. Image generation starts separately.")), 1)
        cancel = QPushButton(tr("cancel", "Cancel"))
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.apply_button = QPushButton(tr("prompt_import_apply", "Apply scenes"))
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.setEnabled(False)
        self.apply_button.setAutoDefault(False)
        self.apply_button.clicked.connect(self.accept)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)
        self.validation_timer = QTimer(self)
        self.validation_timer.setSingleShot(True)
        self.validation_timer.setInterval(250)
        self.validation_timer.timeout.connect(self.validate)
        self.editor.textChanged.connect(self._schedule_validation)
        self.fill_opening.toggled.connect(self._schedule_validation)
        self.complete_prompts.toggled.connect(self._mode_changed)
        self.table.cellClicked.connect(self._select_line)
        self.template.currentIndexChanged.connect(self._refresh_request)
        self.transcript.textChanged.connect(self._refresh_request)
        scenes = self.state.get("scenes") or []
        mode = (self.state.get("plan") or {}).get("imported_prompt_mode")
        self.complete_prompts.setChecked(mode == "complete" if mode else not bool(scenes))
        self.transcript.setPlainText(source_transcript(source))
        self.editor.setPlainText(export_prompts(scenes))
        self._refresh_request()
        self.tabs.setCurrentIndex(1 if scenes else 0)
        self.validate()

    def _build_prepare_tab(self):
        tr = self.tr
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addWidget(_label(tr("prompt_import_prepare_help", "1. Choose an example. 2. Check or load the timed transcript. 3. Copy the full request into your AI chat and bring its answer to the next tab.")))
        split = QSplitter(Qt.Orientation.Horizontal)
        left, right = QWidget(), QWidget()
        a, b = QVBoxLayout(left), QVBoxLayout(right)
        a.setContentsMargins(0, 0, 8, 0)
        b.setContentsMargins(8, 0, 0, 0)
        self.template = QComboBox()
        for key, title in (("narrative", tr("prompt_import_narrative", "Narrative / cinematic")), ("educational", tr("prompt_import_educational", "Educational stickman / doodle")), ("faithful", tr("prompt_import_faithful", "Documentary / faithful illustration"))):
            self.template.addItem(title, key)
        a.addWidget(self.template)
        self.request_preview = QPlainTextEdit()
        self.request_preview.setReadOnly(True)
        self.request_preview.setAccessibleName(tr("prompt_import_request", "Example request for your AI"))
        a.addWidget(self.request_preview, 1)
        self.copy_request_button = QPushButton(tr("prompt_import_copy_request", "Copy request + transcript"))
        self.copy_request_button.clicked.connect(self._copy_request)
        a.addWidget(self.copy_request_button)
        b.addWidget(_label(tr("prompt_import_transcript", "Timed transcript of the final audio")))
        self.transcript = QPlainTextEdit()
        self.transcript.setAccessibleName(tr("prompt_import_transcript", "Timed transcript of the final audio"))
        self.transcript.setPlaceholderText(tr("prompt_import_transcript_placeholder", "Paste a timed transcript, or load an SRT / VTT / TXT file. If no reliable times are available, generate or transcribe the final audio first."))
        b.addWidget(self.transcript, 1)
        b.addWidget(_label(tr("prompt_import_timing_note", "App timings are segment boundaries, not word-level alignment. All imported scene times refer to the final storyboard timeline, including any opening delay. Use a transcript from the same audio version.")))
        load = QPushButton(tr("prompt_import_load_transcript", "Load transcript…"))
        load.clicked.connect(lambda: self._load_text(self.transcript, "*.srt *.vtt *.txt"))
        b.addWidget(load)
        split.addWidget(left)
        split.addWidget(right)
        layout.addWidget(split, 1)
        self.copy_feedback = _label("")
        layout.addWidget(self.copy_feedback)
        next_button = QPushButton(tr("prompt_import_have_prompts", "I have my prompts → Paste & review"))
        next_button.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        layout.addWidget(next_button)
        self.tabs.addTab(page, tr("prompt_import_tab_prepare", "1 · Prepare with your AI"))

    def _build_editor_tab(self):
        tr = self.tr
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.addWidget(_label(tr("prompt_import_format_help", "One scene per timestamp: [00:00] image description. Each scene lasts until the next; the last lasts until the end of the audio. Multiline descriptions and milliseconds are supported.")))
        bar = QHBoxLayout()
        for title, callback in ((tr("prompt_import_paste", "Paste"), self._paste), (tr("prompt_import_load", "Load TXT…"), lambda: self._load_text(self.editor, "*.txt")), (tr("prompt_import_save", "Save TXT…"), self._save_text), (tr("prompt_import_example", "Insert example"), self._insert_example)):
            button = QPushButton(title)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            bar.addWidget(button)
        bar.addStretch()
        layout.addLayout(bar)
        split = QSplitter(Qt.Orientation.Vertical)
        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName(tr("prompt_import_editor", "All scene prompts with timestamps"))
        self.editor.setPlaceholderText("[00:00] A red car drives through New York.\n\n[00:06] Side view of the same car outside a tall building.\n\n[00:12.500] Close-up of the driver looking through the window.")
        split.addWidget(self.editor)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("prompt_import_start", "Start"), tr("prompt_import_end", "End"), tr("prompt_import_duration", "Duration"), tr("prompt_import_prompt", "Image prompt")])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(100)
        split.addWidget(self.table)
        split.setSizes([270, 180])
        layout.addWidget(split, 1)
        self.complete_prompts = QCheckBox(tr("prompt_import_complete", "Complete prompts: do not add the project's visual style or narrative context"))
        self.complete_prompts.setToolTip(tr("prompt_import_complete_tip", "Enable for prompts that already include the visual style. Disable for scene descriptions that should use your project style. Image size and manually selected visual references still apply."))
        layout.addWidget(self.complete_prompts)
        self.fill_opening = QCheckBox(tr("prompt_import_fill", "Show the first image from 00:00 if its timestamp starts later"))
        layout.addWidget(self.fill_opening)
        self.validation = _label("")
        self.validation.setMinimumHeight(38)
        layout.addWidget(self.validation)
        if self.state.get("scenes"):
            layout.addWidget(_label(tr("prompt_import_replace_note", "Applying updates replaces the timeline. Unchanged scenes keep their media; changed scenes need new images. Existing media files are not deleted. You can undo the import in the storyboard.")))
        self.tabs.addTab(page, tr("prompt_import_tab_editor", "2 · Paste & review scenes"))

    def request_text(self, *, with_transcript=True):
        tr = self.tr
        brief = {
            "narrative": tr("prompt_import_brief_narrative", "Plan clear narrative images with varied framing and consistent characters, objects and locations. Show only the events currently being narrated; do not anticipate later actions."),
            "educational": tr("prompt_import_brief_educational", "Create an educational stickman explainer. Use simple expressive figures, flat colors, thick outlines and uncluttered backgrounds. Explain ideas with visual comparisons, diagrams and clearly illustrative metaphors. Keep a stable character and palette; do not invent scientific claims."),
            "faithful": tr("prompt_import_brief_faithful", "Illustrate the narration faithfully. Preserve historical and factual context. Do not invent events, quotations, evidence or named people. Prefer a neutral diagram when the narration does not support a literal depiction."),
        }[self.template.currentData() or "narrative"]
        mode = tr("prompt_import_request_complete", "Write self-contained image prompts including the intended visual style in each scene.") if self.complete_prompts.isChecked() else tr("prompt_import_request_style", "Describe the visible scene only; the application will add the project's visual style.")
        request = tr("prompt_import_request_body", "Turn the timed transcript below into visual scene prompts.\n\n{brief}\n\n{mode}\n\nRules:\n- Return only plain-text blocks: [MM:SS.mmm] image prompt. Start every timestamp on a new line. Separate scenes with a blank line. No tables or explanations.\n- Use times from this transcript. Do not estimate new times or change the narration. The first scene starts at [00:00]; all starts must increase and stay before {end}. The app ends the last scene at {end}; do not add an END marker.\n- Choose changes by meaning, not one image per subtitle. Keep a scene while the same idea continues.\n- Describe one visible moment per image. Restate essential identity details; each image is generated independently.\n- If timestamps are missing or inconsistent, ask for a corrected transcript instead of inventing them.\n\nOutput example (format only):\n[00:00] Description of the opening image.\n\n[00:06.500] Description of the next image.", brief=brief, mode=mode, end=format_timestamp(round(float(self.source.get("duration_seconds") or 0) * 1000)))
        if with_transcript:
            request += "\n\n" + tr("prompt_import_transcript", "Timed transcript of the final audio") + ":\n" + self.transcript.toPlainText().strip()
        return request

    def _refresh_request(self):
        self.request_preview.setPlainText(self.request_text(with_transcript=False))
        self.copy_request_button.setEnabled(bool(self.transcript.toPlainText().strip()))

    def _copy_request(self):
        QApplication.clipboard().setText(self.request_text())
        self.copy_feedback.setText(self.tr("prompt_import_copied", "Copied. Paste into your AI chat, then bring its answer to tab 2."))

    def _mode_changed(self):
        self._refresh_request()
        self._schedule_validation()

    def _schedule_validation(self):
        self.apply_button.setEnabled(False)
        self.entries = []
        self.table.setRowCount(0)
        self.validation.setText(self.tr("prompt_import_checking", "Checking timestamps and scene descriptions…"))
        self.validation_timer.start()

    def validate(self):
        self.validation_timer.stop()
        try:
            entries = parse_prompts(self.editor.toPlainText(), float(self.source.get("duration_seconds") or 0), fill_opening=self.fill_opening.isChecked())
        except PromptImportError as exc:
            self.entries = []
            self.table.setRowCount(0)
            self.apply_button.setEnabled(False)
            self.validation.setText(self.tr(exc.key, exc.default, **exc.values))
            return False
        self.entries = entries
        self.table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            for j, value in enumerate((format_timestamp(entry.start_ms), format_timestamp(entry.end_ms), f"{(entry.end_ms - entry.start_ms) / 1000:g} s", entry.prompt.replace("\n", " "))):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(i, j, item)
        self.validation.setText(self.tr("prompt_import_valid", "Ready: {count} scenes cover 00:00 → {end}. Click a row to edit its prompt. This checks format and timing; review visual meaning before generating images.", count=len(entries), end=format_timestamp(entries[-1].end_ms)))
        self.apply_button.setText(self.tr("prompt_import_apply_count", "Apply {count} scenes", count=len(entries)))
        self.apply_button.setEnabled(True)
        return True

    def _select_line(self, row, _column):
        if row >= len(self.entries):
            return
        block = self.editor.document().findBlockByNumber(self.entries[row].line - 1)
        self.editor.setTextCursor(QTextCursor(block))
        self.editor.setFocus()
        self.editor.centerCursor()

    def _replace_editor(self, text):
        if self.editor.toPlainText().strip() and text != self.editor.toPlainText():
            if QMessageBox.question(self, self.tr("prompt_import_replace_draft", "Replace the text in this editor?"), self.tr("prompt_import_replace_draft_help", "The current text will be replaced. The storyboard itself will not change until you apply.")) != QMessageBox.StandardButton.Yes:
                return
        self.editor.setPlainText(text)

    def _paste(self):
        self._replace_editor(QApplication.clipboard().text())

    def _insert_example(self):
        total = round(float(self.source.get("duration_seconds") or 0) * 1000)
        if total < 3:
            return
        descriptions = [self.tr("prompt_import_demo_one", "A red car drives along a New York avenue, wide view."), self.tr("prompt_import_demo_two", "Side view of the same red car stopped outside a tall building."), self.tr("prompt_import_demo_three", "Close-up of the driver looking through the car window.")]
        self._replace_editor("\n\n".join(f"[{format_timestamp(total * i // 3)}] {description}" for i, description in enumerate(descriptions)))

    def _load_text(self, target, extensions):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("prompt_import_load", "Load TXT…"), "", f"Text ({extensions})")
        if not path:
            return
        try:
            if Path(path).stat().st_size > MAX_TEXT_LENGTH * 4:
                raise ValueError(self.tr("prompt_import_too_large", "This text is too large. The limit is 1,000,000 characters."))
            data = Path(path).read_bytes()
            text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
            if len(text) > MAX_TEXT_LENGTH:
                raise ValueError(self.tr("prompt_import_too_large", "This text is too large. The limit is 1,000,000 characters."))
            if target is self.editor:
                self._replace_editor(text)
            else:
                target.setPlainText(text)
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, self.tr("prompt_import_file_error", "Could not read the text file"), str(exc))

    def _save_text(self):
        path, _ = QFileDialog.getSaveFileName(self, self.tr("prompt_import_save", "Save TXT…"), "scene_prompts.txt", "Text (*.txt)")
        if not path:
            return
        file = QSaveFile(path)
        data = self.editor.toPlainText().encode("utf-8")
        if not file.open(QIODevice.OpenModeFlag.WriteOnly) or file.write(data) != len(data) or not file.commit():
            QMessageBox.warning(self, self.tr("prompt_import_save_error", "Could not save the text file"), file.errorString())

    def accept(self):
        if not self.validate():
            self.tabs.setCurrentIndex(1)
            return
        self.imported_plan = build_import_plan(self.entries, self.source, self.state, complete_prompts=self.complete_prompts.isChecked())
        super().accept()
