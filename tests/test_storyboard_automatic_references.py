import os
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.core.storyboard_reference_images import scene_with_references
from app.core.video_storyboard_comfyui import compile_effective_scene_prompt, compile_reference_edit_prompt, generate_storyboard_frame
from app.ui.video_storyboard_page import VideoStoryboardPage
from app.ui.video_storyboard_regeneration_dialog import VideoStoryboardRegenerationDialog
from app.ui.video_storyboard_reference_picker_dialog import VideoStoryboardReferencePickerDialog
from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
from app.ui.video_storyboard_entity_dialog import VideoStoryboardEntityDialog
from app.workers.video_storyboard_frame_worker import VideoStoryboardFrameWorker

APP = QApplication.instance() or QApplication([])


def tr(key, default, **values):
    return default.format(**values)


def character(tmp_path, name="Marco"):
    path = tmp_path / (name + ".png")
    Image.new("RGB", (32, 32), "blue").save(path)
    return {"id": name.lower(), "name": name, "identity_description": "short black hair",
            "reference_image_path": str(path), "states": [{"id": name.lower() + "_1",
            "description": "cobalt-blue shirt", "from_seconds": 0, "to_seconds": 60}]}


def test_automatic_references_replace_compiled_appearance_and_do_not_mutate(tmp_path):
    record = character(tmp_path)
    plan = {"continuity": {"characters": [record]}, "style": {"characters": ["marco_1: black hair, cobalt-blue shirt"]}}
    scene = {"prompt": "@Marco walks", "characters": ["marco_1"]}
    before = deepcopy(scene)
    resolved = scene_with_references(scene, plan)
    refs = resolved["generation_overrides"]["reference_images"]
    prompt = compile_reference_edit_prompt(compile_effective_scene_prompt(plan, resolved), refs)
    assert "Image 1 is the visual reference for Marco." in prompt
    assert "Marco walks" in prompt
    assert "black hair" not in prompt and "cobalt-blue" not in prompt
    assert "@Marco" not in prompt
    assert scene == before
    disabled = scene_with_references(scene, plan, {"auto_character_references": False})
    assert not disabled["generation_overrides"]["reference_images"]
    assert "cobalt-blue shirt" in compile_effective_scene_prompt(plan, disabled)


def test_missing_reference_uses_description_and_matching_prefers_longest_name(tmp_path):
    marco, polo = character(tmp_path), character(tmp_path, "Marco Polo")
    plan = {"continuity": {"characters": [marco, polo]}}
    refs = scene_with_references({"prompt": "@Marco Polo rides"}, plan)["generation_overrides"]["reference_images"]
    assert [r["label"] for r in refs] == ["Marco Polo"]
    marco["reference_image_path"] = str(tmp_path / "missing.png")
    resolved = scene_with_references({"prompt": "@Marco walks", "characters": ["marco_1"]}, plan)
    assert not resolved["generation_overrides"]["reference_images"]
    assert "black hair" in compile_effective_scene_prompt(plan, resolved)


def test_manual_references_keep_order_and_are_used_with_auto_disabled(tmp_path):
    record = character(tmp_path)
    manual = {"path": record["reference_image_path"], "label": "Marco"}
    plan = {"continuity": {"characters": [record]}}
    scene = {"prompt": "@Marco walks", "generation_overrides": {"reference_images": [manual]}}
    for config in ({}, {"auto_character_references": False}):
        resolved = scene_with_references(scene, plan, config)
        assert resolved["generation_overrides"]["reference_images"] == [manual]
        assert "black hair" not in compile_effective_scene_prompt(plan, resolved)
    initial = compile_reference_edit_prompt("Scene", [manual])
    expanded = compile_reference_edit_prompt(initial, [manual, {"label": "Bike"}])
    assert expanded.count("Image 1") == 1 and "Image 2 is the visual reference for Bike" in expanded


def test_generation_routes_automatic_references_to_generation_provider(tmp_path):
    record = character(tmp_path)
    plan = {"continuity": {"characters": [record]}}
    settings = {"image_provider": "litellm_image", "image_edit_provider": "comfyui"}
    with patch("app.core.video_storyboard_comfyui._generate_litellm_storyboard_frame", return_value={}) as generate, patch(
        "app.core.video_storyboard_image_edit.generate_edited_storyboard_image"
    ) as edit:
        generate_storyboard_frame({"prompt": "@Marco walks"}, plan, settings, tmp_path / "frame.png")
        scene = generate.call_args.args[0]
        assert scene["generation_overrides"]["reference_images"][0]["label"] == "Marco"
        assert "black hair" not in scene["generation_overrides"]["raw_prompt"]
        edit.assert_not_called()
    with patch("app.workers.video_storyboard_frame_worker.prepare_image_runtime") as prepare, patch(
        "app.core.video_storyboard_image_edit.prepare_image_edit_runtime"
    ) as prepare_edit, patch("app.workers.video_storyboard_frame_worker.generate_storyboard_frame", return_value={}):
        worker = VideoStoryboardFrameWorker({"plan": plan, "scenes": [{"prompt": "Marco walks"}]}, settings, tmp_path)
        worker.run()
        prepare.assert_called_once()
        prepare_edit.assert_not_called()


def test_references_are_not_limited(tmp_path):
    records = [character(tmp_path, str(i)) for i in range(4)]
    scene = scene_with_references({"prompt": "@0 @1 @2 @3"}, {"continuity": {"characters": records}})
    assert len(scene["generation_overrides"]["reference_images"]) == 4


def test_existing_frames_do_not_start_a_provider(tmp_path):
    record = character(tmp_path)
    scene = {"prompt": "@Marco walks", "image_path": record["reference_image_path"]}
    with patch("app.workers.video_storyboard_frame_worker.prepare_image_runtime") as prepare, patch(
        "app.core.video_storyboard_image_edit.prepare_image_edit_runtime"
    ) as prepare_edit:
        worker = VideoStoryboardFrameWorker({"plan": {"continuity": {"characters": [record]}}, "scenes": [scene]}, {}, tmp_path)
        worker.run()
        prepare.assert_not_called()
        prepare_edit.assert_not_called()


def test_object_state_selection_is_used_without_a_reference():
    record = {"id": "bike", "name": "Bike", "identity_description": "red motorcycle", "states": [
        {"id": "clean", "from_seconds": 0, "to_seconds": 30, "description": "polished bodywork"},
        {"id": "dirty", "from_seconds": 30, "to_seconds": 60, "description": "mud-covered wheels"}]}
    plan = {"continuity": {"objects": [record]}}
    scene = {"prompt": "@Bike is parked", "start_seconds": 0, "duration_seconds": 5}
    dialog = VideoStoryboardRegenerationDialog(tr, scene, plan)
    dialog._insert_entity(record, record["states"][1], "objects")
    assert dialog.request_payload()["overrides"]["object_state_ids"] == ["dirty"]
    prompt = dialog.raw_prompt_edit.toPlainText()
    assert "mud-covered wheels" in prompt
    assert "polished bodywork" not in prompt
    dialog.close()


def test_preview_and_object_reference_picker(tmp_path):
    record, bike = character(tmp_path), character(tmp_path, "Bike")
    plan = {"continuity": {"characters": [record], "objects": [bike]}}
    dialog = VideoStoryboardRegenerationDialog(tr, {"prompt": "@Marco rides @Bike"}, plan)
    prompt = dialog.raw_prompt_edit.toPlainText()
    assert "Image 1 is the visual reference for Marco" in prompt
    assert "Image 2 is the visual reference for Bike" in prompt
    assert "black hair" not in prompt
    assert dialog.insert_object_button.isEnabled()
    dialog.close()
    picker = VideoStoryboardReferencePickerDialog(tr, plan)
    assert picker.list_widget.count() == 2
    picker.close()


def test_setting_defaults_and_roundtrip():
    widget = VideoStoryboardSettingsWidget(tr)
    assert widget.configuration()["auto_character_references"] is True
    widget.auto_character_references_checkbox.setChecked(False)
    saved = widget.configuration()
    widget.set_configuration(saved)
    assert widget.configuration()["auto_character_references"] is False
    widget.deleteLater()


def test_object_crud_persistence_and_reanalysis(tmp_path):
    page = VideoStoryboardPage(tr)
    page._source_project_dir = str(tmp_path / "project")
    image = character(tmp_path, "Bike")["reference_image_path"]
    values = {"name": "Bike", "aliases": [], "identity_description": "Red motorcycle", "state_description": "Clean",
              "from_seconds": 0.0, "to_seconds": 60.0, "reference_image_path": image}
    with patch.object(VideoStoryboardEntityDialog, "exec", return_value=QDialog.DialogCode.Accepted), patch.object(
        VideoStoryboardEntityDialog, "values", return_value=values
    ):
        page.objects_panel.edit_record()
        record = page.objects_panel.records()[0]
        assert record["name"] == "Bike"
        assert "references" in record["reference_image_path"]
        values["name"] = "Motorcycle"
        page.objects_panel.edit_record(record)
        assert record["name"] == "Motorcycle"
        assert "Bike" in record["aliases"]
    state = page.project_state()
    restored = VideoStoryboardPage(tr)
    restored.restore_project_state(state)
    assert restored.objects_panel.records()[0]["name"] == "Motorcycle"
    restored.set_analysis_partial_result({"continuity": {}, "scenes": []})
    restored.set_analysis_result({"continuity": {}, "scenes": []})
    assert restored.objects_panel.records()[0]["name"] == "Motorcycle"
    restored.objects_panel.tree.setCurrentItem(restored.objects_panel.tree.topLevelItem(0))
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        restored.objects_panel.delete_record()
    assert not restored.objects_panel.records()
    page.deleteLater()
    restored.deleteLater()
