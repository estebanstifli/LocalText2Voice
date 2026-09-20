import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
from unittest.mock import patch
import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from app.ui.video_storyboard_analysis_dialog import VideoStoryboardAnalysisDialog
from app.ui.storyboard_analysis_sidebar import entity_scene_counts
from app.ui.storyboard_era_dialog import StoryboardEraDialog
from app.ui.storyboard_entity_scenes import EntityScenesPanel
from app.ui.video_storyboard_page import VideoStoryboardPage
from app.ui.storyboard_analysis_settings import StoryboardAnalysisSettingsWidget
from app.core.storyboard_analysis_settings import normalize
from app.core.video_storyboard_comfyui import compile_effective_scene_prompt
from app.core.video_storyboard_project import load_storyboard_state, save_storyboard_state
from app.ui.video_storyboard_regeneration_dialog import VideoStoryboardRegenerationDialog

APP = QApplication.instance() or QApplication([])


def tr(key, default, **values):
    return default.format(**values)


def example():
    return {"continuity": {"era_mode": "auto_multiple", "eras": [
        {"id": "roman", "name": "Roman Britain", "description": "Roman Britain", "material_culture": "Stone roads",
         "from_seconds": 0, "to_seconds": 36, "reason": "explicit", "occurrences": [
            {"era_id": "roman", "from_seconds": 0, "to_seconds": 12},
            {"era_id": "roman", "from_seconds": 24, "to_seconds": 36}]},
        {"id": "victorian", "name": "Victorian London", "description": "Victorian London", "material_culture": "Steam trains",
         "from_seconds": 12, "to_seconds": 24, "reason": "inferred"}],
        "era_assignments": [{"era_id": "roman", "from_seconds": 0, "to_seconds": 12},
            {"era_id": "victorian", "from_seconds": 12, "to_seconds": 24},
            {"era_id": "roman", "from_seconds": 24, "to_seconds": 36}], "characters": [], "locations": []},
        "narrative_context": {"era": "Multiple periods: Roman Britain; Victorian London", "era_source": "detected", "era_mode": "auto_multiple"},
        "source_duration_seconds": 36, "scenes": [
            {"id": str(i+1), "start_seconds": i*12, "duration": 12, "era_state_id": period,
             "era": "Roman Britain" if period == "roman" else "Victorian London", "prompt": "A road.", "narration": "A road."}
            for i, period in enumerate(["roman", "victorian", "roman"])]}


def test_mode_controls_and_fifth_card_never_show_zero_while_pending():
    dialog = VideoStoryboardAnalysisDialog(tr, audiobook_era="My manual era")
    assert not dialog.era_automatic.isChecked() and dialog.era_edit.isEnabled()
    assert not dialog.era_scope.isEnabled()
    dialog.era_automatic.setChecked(True)
    dialog.era_scope.setCurrentIndex(1)
    dialog._select_plan("scenes")
    assert dialog._era_mode() == "auto_multiple" and not dialog.era_edit.isEnabled()
    assert dialog.era_edit.text() == "My manual era"
    starts = []
    dialog.startRequested.connect(starts.append)
    dialog._start_analysis()
    assert starts[0]["analysis_choices"]["era_mode"] == "auto_multiple"
    assert len(dialog.sidebar.cards) == 5
    assert "0" not in dialog.sidebar.cards["eras"][1].text()
    dialog.sidebar.set_stage("conversation: eras and visual context")
    assert dialog.sidebar.active == "eras"
    assert dialog.sidebar.cards["eras"][0].styleSheet()
    dialog.set_finished(True, "Complete")
    dialog.close()


def test_period_counts_and_appearance_gallery():
    plan = example()
    assert entity_scene_counts(plan, "eras") == [("Roman Britain", 2), ("Victorian London", 1)]
    dialog = VideoStoryboardAnalysisDialog(tr, analysis_choices={"era_mode": "auto_multiple"})
    dialog.update_plan(plan, final=True)
    table = dialog.entity_summaries["eras"]
    assert table.rowCount() == 2 and dialog.tabs.indexOf(table) >= 0
    assert table.item(0, 2).text() == "Stone roads"
    editor = StoryboardEraDialog(tr, plan, "roman", dialog)
    gallery = editor.findChild(EntityScenesPanel)
    assert gallery.list.count() == 2
    assert gallery.list.item(1).text() == "Scene 3 · 00:00:24"
    editor.assignments.item(1).setCheckState(Qt.CheckState.Checked)
    assert all(s["era_state_id"] == "roman" for s in editor.revised_plan()["scenes"])
    editor.close()
    dialog.close()


def test_unrelated_project_controls_preserve_periods_and_manual_override_replaces_them():
    page = VideoStoryboardPage(tr)
    page.set_analysis_result(example())
    before = deepcopy(page.project_state()["plan"]["continuity"])
    page.video_zoom_spin.setValue(25)
    page.style_lighting_edit.setText("Warm light")
    assert page.project_state()["plan"]["continuity"] == before
    assert page.project_state()["plan"]["narrative_context"]["era_mode"] == "auto_multiple"
    page.story_era_edit.setText("Ancient Rome")
    ledger = page.project_state()["plan"]["continuity"]
    assert ledger["era_mode"] == "manual" and "era_assignments" not in ledger
    assert len(ledger["eras"]) == 1
    assert all(s["era"] == "Ancient Rome" for s in page.scenes())
    page.deleteLater()


def test_period_editor_applies_and_emits_updated_scenes():
    page = VideoStoryboardPage(tr)
    page.set_analysis_result(example())
    updated = []
    page.scenesChanged.connect(updated.append)

    def accept(dialog):
        dialog.description.setText("Reviewed Rome")
        return QDialog.DialogCode.Accepted

    with patch.object(StoryboardEraDialog, "exec", accept):
        page.edit_storyboard_era("roman")
    assert updated and updated[-1][0]["era"] == "Reviewed Rome"
    assert page.scenes()[1]["era"] == "Victorian London"
    page.style_lighting_edit.setText("Cool light")
    assert len(page.project_state()["plan"]["continuity"]["eras"]) == 2
    page.deleteLater()


def test_only_conversational_settings_remain_and_old_settings_migrate():
    config = normalize({"process": "simple", "era_mode": "detect", "block_size": "custom", "custom_characters": 9000,
                        "prompts": {"era": "obsolete", "conversation_report": "My report prompt"}})
    assert config["process"] == "conversational" and "era_mode" not in config
    assert config["prompts"] == {"conversation_report": "My report prompt"}
    widget = StoryboardAnalysisSettingsWidget(tr)
    widget.set_configuration(config)
    assert widget.configuration() == config
    assert not hasattr(widget, "era") and not hasattr(widget, "process")
    widget.deleteLater()


@pytest.mark.parametrize("assigned", [False, True])
def test_regeneration_preserves_period_assignment_through_accept_and_reload(tmp_path, assigned):
    plan = example()
    if not assigned:
        plan["scenes"][0].update(era="", era_state_id="")
    page = VideoStoryboardPage(tr)
    page.set_analysis_result(plan)
    scene = page.scenes()[0]
    dialog = VideoStoryboardRegenerationDialog(tr, scene, plan)
    assert dialog.era_edit.text() == ("Roman Britain" if assigned else "")
    expected = compile_effective_scene_prompt(plan, scene)
    assert dialog.raw_prompt_edit.toPlainText() == expected
    dialog.lighting_edit.setText("Warm light")
    request = dialog.request_payload()
    assert "era" not in request["overrides"]["narrative"]
    payload = page.regeneration_payload(scene["scene_id"], request["prompt"], request["overrides"], request["shot"])
    regenerated = payload["scenes"][0]
    assert regenerated["era_state_id"] == scene["era_state_id"]
    prompt = compile_effective_scene_prompt(payload["plan"], regenerated)
    assert "Multiple periods" not in prompt and "Victorian" not in prompt
    assert ("ERA: Roman Britain." in prompt) == assigned
    assert ("ERA:" in prompt) == assigned
    assert dialog.raw_prompt_edit.toPlainText() == prompt
    page._accept_dialog_candidate({**request, "image_path": str(tmp_path / "candidate.png")})
    save_storyboard_state(tmp_path, page.project_state(), analysis_status="ready")
    restored = load_storyboard_state(tmp_path)
    assert restored["scenes"][0]["era_state_id"] == scene["era_state_id"]
    assert compile_effective_scene_prompt(restored["plan"], restored["scenes"][0]) == prompt
    reopened = VideoStoryboardRegenerationDialog(tr, restored["scenes"][0], restored["plan"])
    assert reopened.raw_prompt_edit.toPlainText() == prompt
    reopened.deleteLater()
    dialog.deleteLater()
    page.deleteLater()


@pytest.mark.parametrize("override", ["Medieval England", ""])
def test_explicit_regeneration_period_edit_can_replace_or_clear_assignment(override):
    plan = example()
    page = VideoStoryboardPage(tr)
    page.set_analysis_result(plan)
    dialog = VideoStoryboardRegenerationDialog(tr, page.scenes()[0], plan)
    dialog.era_edit.setText(override)
    request = dialog.request_payload()
    assert request["overrides"]["narrative"]["era"] == override
    payload = page.regeneration_payload("1", request["prompt"], request["overrides"])
    scene = payload["scenes"][0]
    assert scene["era_state_id"] == "" and scene["era"] == override
    prompt = compile_effective_scene_prompt(payload["plan"], scene)
    assert "Roman Britain" not in prompt and "Multiple periods" not in prompt
    assert ("ERA:" in prompt) == bool(override)
    page._accept_dialog_candidate({**request, "image_path": "candidate.png"})
    assert page.scenes()[0]["era_state_id"] == ""
    assert page.scenes()[0]["era"] == override
    reopened = VideoStoryboardRegenerationDialog(tr, page.scenes()[0], plan)
    assert reopened.era_edit.text() == override
    assert reopened.raw_prompt_edit.toPlainText() == prompt
    reopened.deleteLater()
    dialog.deleteLater()
    page.deleteLater()


@pytest.mark.parametrize("has_periods", [False, True])
def test_fresh_analysis_replaces_old_periods_assignments_and_overrides(tmp_path, has_periods):
    page = VideoStoryboardPage(tr)
    old = example()
    old["scenes"][0].update(generation_overrides={"raw_prompt": "OLD PERIODS"}, image_path="old.png")
    page.set_analysis_result(old)
    new = {"continuity": {"era_mode": "auto_multiple", "eras": [], "era_assignments": []},
           "narrative_context": {"era": "", "era_source": "detected", "era_mode": "auto_multiple"},
           "scenes": [{"id": "1", "duration": 12, "prompt": "A factory.", "era": "", "era_state_id": ""}]}
    if has_periods:
        new["continuity"]["eras"] = [{"id": "industrial", "description": "Industrial Britain", "material_culture": "Factories"}]
        new["continuity"]["era_assignments"] = [{"era_id": "industrial", "from_seconds": 0, "to_seconds": 12}]
        new["narrative_context"]["era"] = "Industrial Britain"
        new["scenes"][0].update(era="Industrial Britain", era_state_id="industrial")
    page.set_analysis_partial_result(deepcopy(new))
    page.set_analysis_result(deepcopy(new))
    save_storyboard_state(tmp_path, page.project_state(), analysis_status="ready")
    restored = load_storyboard_state(tmp_path)
    assert restored["plan"]["continuity"]["eras"] == new["continuity"]["eras"]
    assert restored["plan"]["continuity"]["era_assignments"] == new["continuity"]["era_assignments"]
    assert page.continuity_eras_tree.topLevelItemCount() == int(has_periods)
    assert page.story_era_edit.text() == new["narrative_context"]["era"]
    scene = restored["scenes"][0]
    assert scene["era_state_id"] == new["scenes"][0]["era_state_id"]
    assert not scene["generation_overrides"] and not scene["image_path"]
    prompt = compile_effective_scene_prompt(restored["plan"], scene)
    assert "Roman" not in prompt and "Victorian" not in prompt and "OLD" not in prompt
    page.deleteLater()


def test_visual_context_checkbox_defaults_off_persists_and_preserves_assignments(tmp_path):
    page = VideoStoryboardPage(tr)
    plan = example()
    plan["continuity"]["eras"][0]["description"] = "A long description of the surroundings"
    page.set_analysis_result(plan)
    assert not page.era_visual_context_check.isChecked()
    assert page.continuity_eras_tree.topLevelItem(0).text(0) == "Roman Britain"
    editor = StoryboardEraDialog(tr, plan, "roman")
    assert editor.description.text() == "Roman Britain"
    editor.deleteLater()
    before = deepcopy(page.project_state()["plan"]["continuity"])
    updates = []
    page.projectChanged.connect(updates.append)
    for enabled in (True, False, True):
        page.era_visual_context_check.setChecked(enabled)
        assert updates[-1]["plan"]["narrative_context"]["era_visual_context"] == enabled
        state = page.project_state()
        assert state["plan"]["continuity"] == before
        scene = state["scenes"][0]
        prompt = compile_effective_scene_prompt(state["plan"], scene)
        assert "ERA: Roman Britain." in prompt
        assert ("ERA VISUAL CONTEXT: Stone roads." in prompt) == enabled
        dialog = VideoStoryboardRegenerationDialog(tr, scene, state["plan"])
        assert dialog.raw_prompt_edit.toPlainText() == prompt
        dialog.deleteLater()
    save_storyboard_state(tmp_path, page.project_state(), analysis_status="ready")
    page.restore_project_state(load_storyboard_state(tmp_path))
    assert page.era_visual_context_check.isChecked()
    page.set_analysis_partial_result(example())
    page.set_analysis_result(example())
    assert page.era_visual_context_check.isChecked()
    assert page.project_overrides()["narrative"]["era_visual_context"]
    page.deleteLater()
