"""Visual analysis progress and scene participation, independent of LLM prose."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.utils.paths import resource_root


def entity_scene_counts(plan, category):
    continuity = plan.get("continuity") or {}
    records = continuity.get(category) or []
    scenes = plan.get("scenes") or []
    rows = []
    for record in records:
        identifiers = {str(record.get(k) or "").strip().casefold() for k in ("id", "name")}
        identifiers.update(str(s.get("id") or "").strip().casefold() for s in record.get("states", []))
        identifiers.update(str(a).strip().casefold() for a in record.get("aliases", []))
        identifiers.discard("")
        count = 0
        for scene in scenes:
            values = scene.get(category) or []
            if category == "eras":
                values = [scene.get("era_state_id", "")]
            if isinstance(values, str):
                values = [values]
            overrides = scene.get("generation_overrides") or {}
            bindings = overrides.get(category[:-1] + "_state_ids") or []
            if isinstance(bindings, str):
                bindings = [bindings]
            selected = {str(v).strip().casefold() for v in [*values, *bindings]}
            count += bool(identifiers.intersection(selected))
        rows.append((str(record.get("name") or record.get("description") or record.get("id") or "—"), count))
    return sorted(rows, key=lambda row: (-row[1], row[0].casefold()))


def character_scene_counts(plan):
    return entity_scene_counts(plan, "characters")


class StoryboardAnalysisSidebar(QWidget):
    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self.tr = tr
        self.cards = {}
        self.active = None
        self.visited = set()
        self.enabled_categories = {"characters", "locations", "scenes"}
        self.setMinimumWidth(270)
        self.setMaximumWidth(320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(12)
        title = QLabel(tr("analysis_overview", "Analysis overview"))
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        for key, title in (("characters", "Characters"), ("locations", "Locations"),
                           ("scenes", "Scenes"), ("objects", "Important objects"), ("eras", "Eras / periods")):
            card = QFrame()
            card.setFrameShape(QFrame.Shape.StyledPanel)
            row = QHBoxLayout(card)
            row.setContentsMargins(8, 12, 8, 12)
            picture = QLabel()
            picture.setFixedSize(82, 82)
            pixmap = QPixmap(str(resource_root() / "assets" / "storyboard_analysis" / f"{key}.png"))
            # Zoom the illustration inside its existing slot, removing background margins.
            crop_width, crop_height = round(pixmap.width() / 1.6), round(pixmap.height() / 1.6)
            pixmap = pixmap.copy((pixmap.width() - crop_width) // 2, (pixmap.height() - crop_height) // 2, crop_width, crop_height)
            picture.setPixmap(pixmap.scaled(82, 82, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
            picture.setAccessibleName(tr("analysis_category_" + key, title))
            row.addWidget(picture)
            details = QVBoxLayout()
            heading = QLabel(tr("analysis_category_" + key, title))
            heading.setWordWrap(True)
            heading.setObjectName("sectionTitle")
            state = QLabel(tr("analysis_waiting", "Waiting to start"))
            state.setWordWrap(True)
            counter = QLabel(tr("analysis_count_pending", "Count pending"))
            counter.setWordWrap(True)
            details.addWidget(heading)
            details.addWidget(state)
            details.addWidget(counter)
            row.addLayout(details, 1)
            layout.addWidget(card)
            self.cards[key] = (state, counter)
        layout.addStretch(1)
        self.cards["objects"][0].setText(tr("analysis_not_requested", "Not requested"))
        self.cards["objects"][1].clear()


    def set_stage(self, label):
        label = label.casefold()
        category = next((key for key, words in (
            ("eras", ("eras", "historical period")),
            ("objects", ("object",)), ("locations", ("location", "place summary")),
            ("characters", ("character", "appearance")),
            ("scenes", ("scene", "image prompt", "anchor", "binding")))
            if any(word in label for word in words)), None)
        if not category or category not in self.enabled_categories:
            return
        if self.active and self.active != category:
            self.cards[self.active][0].setText(self.tr("analysis_stage_saved", "Stage results received"))
        self.active = category
        self._highlight_active(category)
        self.visited.add(category)
        key, text = ("analysis_first", "First analysis running")
        if "json" in label or "convert" in label:
            key, text = "analysis_profiles", "Validating structured results"
        elif "image prompt" in label:
            key, text = "analysis_prompts", "Preparing image prompts"
        elif "anchor" in label or "binding" in label:
            key, text = "analysis_alignment", "Aligning with narration"
        self.cards[category][0].setText(self.tr(key, text))

    def update_plan(self, plan, final=False):
        continuity = plan.get("continuity") or {}
        if continuity.get("eras"):
            self.enabled_categories.add("eras")
        for key, (_, counter) in self.cards.items():
            values = plan.get("scenes", []) if key == "scenes" else continuity.get(key, [])
            if values or (final and key in self.enabled_categories):
                counter.setText(self.tr("analysis_created", "{count} created", count=len(values)) if key == "scenes"
                                else self.tr("analysis_found", "{count} found", count=len(values)))
            elif key in self.enabled_categories:
                counter.setText(self.tr("analysis_pending_profiles", "Pending structured results"))
            else:
                counter.clear()
            if final:
                state = "analysis_complete" if values else "analysis_none"
                label = "Analysis complete" if values else "None identified"
                if key == "objects" and key not in self.enabled_categories:
                    state, label = "analysis_not_requested", "Not requested"
                elif key not in self.enabled_categories:
                    state, label = "analysis_not_requested", "Not requested"
                self.cards[key][0].setText(self.tr(state, label))
            if key == "eras" and continuity.get("era_mode") == "manual" and values:
                self.cards[key][0].setText(self.tr("analysis_era_manual", "Manual period"))
            if key == "eras" and final and key in self.enabled_categories:
                unassigned = sum(not s.get("era_state_id") for s in plan.get("scenes", []))
                if unassigned:
                    counter.setText(counter.text() + "\n" + self.tr("analysis_era_unassigned", "{count} scenes without a period", count=unassigned))
        if final:
            self._highlight_active(None)
            self.active = None

    def configure(self, selected):
        self.enabled_categories = {"scenes"} | {k for k in ("characters", "locations", "objects") if selected.get(k, k != "objects")}
        if selected.get("era_mode", "manual") != "manual" or selected.get("audiobook_era"):
            self.enabled_categories.add("eras")
        for key in ("characters", "locations", "objects", "eras"):
            if key in self.enabled_categories:
                self.cards[key][0].setText(self.tr("analysis_waiting", "Waiting to start"))
                self.cards[key][1].setText(self.tr("analysis_pending_profiles", "Pending structured results"))
            if key not in self.enabled_categories:
                self.cards[key][0].setText(self.tr("analysis_not_requested", "Not requested"))
                self.cards[key][1].clear()
        if selected.get("era_mode", "manual") == "manual" and selected.get("audiobook_era"):
            self.cards["eras"][0].setText(self.tr("analysis_era_manual", "Manual period"))
            self.cards["eras"][1].setText(self.tr("analysis_found", "{count} found", count=1))

    def _highlight_active(self, category):
        dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        foreground, background = ("#7dd3fc", "#12364b") if dark else ("#075985", "#e0f2fe")
        for key, (state, _) in self.cards.items():
            state.setStyleSheet(
                f"color: {foreground}; background-color: {background}; font-weight: bold; padding: 5px; border-radius: 4px;"
                if key == category else ""
            )

    def stop(self):
        self._highlight_active(None)
        if self.active:
            self.cards[self.active][0].setText(self.tr("analysis_stopped", "Analysis stopped here"))
