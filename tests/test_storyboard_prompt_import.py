import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from app.core.storyboard_prompt_import import (
    PromptImportError, build_import_plan, export_prompts, format_timestamp,
    parse_prompts, source_transcript,
)
from app.core.video_storyboard_comfyui import compile_effective_scene_prompt, _effective_frame_plan
from app.core.video_storyboard_project import save_storyboard_state, load_storyboard_state
from app.ui.storyboard_prompt_import_dialog import StoryboardPromptImportDialog, StoryboardSourceDialog
from app.ui.video_storyboard_page import VideoStoryboardPage


def tr(key, default, **values):
    return default.format(**values)


SOURCE = {"title": "A small story", "text": "One. Two.", "duration_seconds": 20.125,
          "voice_start_offset_seconds": 2,
          "narration_cues": [{"start_seconds": 2, "end_seconds": 8, "duration_seconds": 6, "text": "One.", "timing_ready": True},
                             {"start_seconds": 8, "end_seconds": 20.125, "duration_seconds": 12.125, "text": "Two.", "timing_ready": True}]}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_multiline_milliseconds_fences_and_last_scene():
    rows = parse_prompts("```text\n[00:00] New York.\nA red car.\n\n[00:06,500] The driver.\n```", 20.125)
    assert [(r.start_ms, r.end_ms) for r in rows] == [(0, 6500), (6500, 20125)]
    assert rows[0].prompt == "New York.\nA red car."
    assert rows[0].line == 2


def test_hours_and_long_minute_notation():
    assert parse_prompts("[00:00] First\n[01:00:00.001] Last", 3601)[1].start_ms == 3600001
    assert parse_prompts("[00:00] First\n[60:00.001] Last", 3601)[1].start_ms == 3600001
    assert format_timestamp(3600001) == "01:00:00.001"


@pytest.mark.parametrize("text, key", [
    ("", "empty"), ("Here's your storyboard\n[00:00] Image", "bad_line"),
    ("[00:00] A\n[00:00] B", "order"), ("[00:00] A\n[00:10] B\n[00:06] C", "order"),
    ("[00:00]\n[00:06] B", "empty_prompt"), ("[00:00] A\n[00:20.125] B", "outside"),
    ("[00:61] A", "bad_time"), ("[01:61:00] A", "bad_time"), ("[-00:01] A", "bad_time"),
    ("[00:00] A\n[00:06 B", "bad_line"), ("[00:00] A [00:06] B", "new_line"),
    ("[00:00] A\n00:06 B", "new_line"), ("```text\n[00:00] A", "fence"),
    ("[00:01] A", "opening"), ("[00:00] A\n[00:06]", "empty_prompt"),
    ("[00:00.1234] A", "bad_time"),
])
def test_invalid_input_is_never_silently_repaired(text, key):
    with pytest.raises(PromptImportError) as exc:
        parse_prompts(text, SOURCE["duration_seconds"])
    assert exc.value.key == "prompt_import_" + key


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_no_valid_audio_duration(duration):
    with pytest.raises(PromptImportError, match="duration"):
        parse_prompts("[00:00] A", duration)


def test_opening_fill_is_explicit_and_does_not_shift_later_scenes():
    rows = parse_prompts("[00:01] A\n[00:06] B", 20, fill_opening=True)
    assert [(r.start_ms, r.end_ms) for r in rows] == [(0, 6000), (6000, 20000)]


@pytest.mark.parametrize("text,duration", [("[00:00] A\n[00:00.050] B", 20), ("[00:00] A\n[00:19.950] B", 20)])
def test_rejects_scenes_that_renderer_would_silently_extend(text, duration):
    with pytest.raises(PromptImportError, match="0.1 seconds"):
        parse_prompts(text, duration)


def test_scene_roundtrip_media_reuse_and_new_ids(tmp_path):
    source = deepcopy(SOURCE)
    entries = parse_prompts("[00:00] First\n[00:06.500] Second", source["duration_seconds"])
    original = build_import_plan(entries, source, {}, complete_prompts=True)
    original["scenes"][0]["image_path"] = str(tmp_path / "kept.png")
    original["scenes"][0]["status"] = "ready"
    state = {"plan": original, "scenes": original["scenes"], "source": source}
    text = export_prompts(state["scenes"])
    assert "[00:06.500]" in text
    changed = build_import_plan(parse_prompts(text.replace("Second", "Changed"), 20.125), source, state, complete_prompts=True)
    assert changed["scenes"][0]["image_path"] == original["scenes"][0]["image_path"]
    assert changed["scenes"][0]["scene_id"] == original["scenes"][0]["scene_id"]
    assert changed["scenes"][1]["scene_id"] != original["scenes"][1]["scene_id"]
    assert changed["scenes"][1]["image_path"] == ""
    assert original["scenes"][1]["prompt"] == "Second"
    save_storyboard_state(tmp_path, {"source": source, "plan": changed, "scenes": changed["scenes"]})
    restored = load_storyboard_state(tmp_path)
    assert restored["scenes"][1]["duration_seconds"] == 13.625
    assert restored["scenes"][0]["generation_overrides"]["prompt_mode"] == "complete"


def test_complete_prompts_survive_style_context_and_single_scene_edits(app):
    page = VideoStoryboardPage(tr)
    source = deepcopy(SOURCE)
    source["storyboard_overrides"] = {"style": {"medium": "watercolor", "negative": "no text"}, "narrative": {"illustration_context": "Wrong context"}}
    plan = build_import_plan(parse_prompts("[00:00] Doodle with label", 20.125), source, {}, complete_prompts=True)
    page.apply_imported_plan(plan)
    scene = page._scenes[0]
    scene.generation_overrides["raw_prompt"] = "A manual raw prompt"
    assert page._editable_prompt_text(scene) == "A manual raw prompt"
    page._apply_editable_prompt(scene, "Doodle with a changed label")
    assert compile_effective_scene_prompt(plan, scene.as_dict()) == "Doodle with a changed label"
    assert _effective_frame_plan(plan, scene.as_dict())["style"] == {}
    page.deleteLater()


def test_regeneration_preserves_complete_prompt_mode(app):
    from app.ui.video_storyboard_regeneration_dialog import VideoStoryboardRegenerationDialog
    scene = {"scene_id": "import-1", "prompt": "Stickman with red lettering", "generation_overrides": {"prompt_mode": "complete"}}
    dialog = VideoStoryboardRegenerationDialog(tr, scene, {"style": {"medium": "photorealism"}})
    assert dialog.request_payload()["overrides"]["prompt_mode"] == "complete"
    assert dialog._compose_structured_prompt() == scene["prompt"]
    dialog.deleteLater()


def test_native_style_still_applies():
    plan = {"style": {"medium": "watercolor"}}
    assert "watercolor" in compile_effective_scene_prompt(plan, {"prompt": "City", "generation_overrides": {"prompt_mode": "project_style"}})


def test_transcript_uses_final_timeline_without_double_offset():
    assert source_transcript(SOURCE).startswith("[00:02] One.")
    source = deepcopy(SOURCE)
    source["narration_cues"][0]["timing_ready"] = False
    assert source_transcript(source) == ""


def test_dialog_validation_copy_and_apply(app):
    dialog = StoryboardPromptImportDialog(tr, SOURCE)
    assert not dialog.apply_button.isEnabled()
    dialog._copy_request()
    assert "[00:02] One." in QApplication.clipboard().text()
    assert "00:20.125" in QApplication.clipboard().text()
    dialog.template.setCurrentIndex(1)
    assert "stickman" in dialog.request_text()
    dialog.editor.setPlainText("[00:00] City\n[00:06.500] Driver")
    assert dialog.validate()
    assert dialog.table.item(1, 2).text() == "13.625 s"
    dialog.editor.setPlainText("[00:00] City\n[00:00] Driver")
    assert not dialog.apply_button.isEnabled()
    dialog.accept()  # Enter / stale validation cannot bypass validation.
    assert dialog.imported_plan is None
    assert dialog.table.rowCount() == 0
    dialog.editor.setPlainText("[00:01] City\n[00:06] Driver")
    dialog.fill_opening.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.imported_plan["scenes"][1]["start_seconds"] == 6
    assert sum(s["duration_seconds"] for s in dialog.imported_plan["scenes"]) == 20.125
    dialog.deleteLater()


def test_import_is_undoable_and_redoable_including_metadata(app):
    page = VideoStoryboardPage(tr)
    page.set_analysis_result({"style": {"medium": "watercolor"}, "scenes": [{"id": "old", "duration": 20.125, "prompt": "Old"}]})
    page.apply_imported_plan(build_import_plan(parse_prompts("[00:00] A\n[00:06] B", 20.125), SOURCE, page.project_state(), complete_prompts=True))
    assert [s["start_seconds"] for s in page.scenes()] == [0, 6]
    page._undo_scene_edit()
    assert page.scenes()[0]["scene_id"] == "old"
    assert "plan_origin" not in page.project_state()["plan"]
    page._redo_scene_edit()
    assert len(page.scenes()) == 2
    assert page.project_state()["plan"]["plan_origin"] == "external_prompts"
    page.deleteLater()


def test_selector_does_not_select_on_cancel(app):
    chooser = StoryboardSourceDialog(tr)
    chooser.reject()
    assert not chooser.choice
    chooser.external_button.click()
    assert chooser.choice == "external"
    chooser.deleteLater()


@pytest.mark.parametrize("choice, accepted", [("external", True), ("external", False), ("llm", True), ("", False)])
def test_analyze_button_routes_to_chosen_workflow_without_an_llm_call(app, choice, accepted):
    from app.ui.main_window import MainWindow
    fake = SimpleNamespace(
        video_storyboard_analysis_dialog=None, video_storyboard_planner_thread=None,
        video_storyboard_video_thread=None, video_storyboard_frame_thread=None,
        video_storyboard_render_thread=None, current_audiobook_id=None,
        tr=tr, settings={}, video_storyboard_page=MagicMock(),
        _close_storyboard_review_window=MagicMock(), _persist_video_storyboard_state=MagicMock(),
    )
    fake.video_storyboard_page.project_state.return_value = {"plan": {}, "scenes": []}
    fake.video_storyboard_page.scenes.return_value = []
    plan = build_import_plan(parse_prompts("[00:00] City", 20.125), SOURCE, {}, complete_prompts=True)
    with patch("app.ui.storyboard_prompt_import_dialog.StoryboardSourceDialog") as chooser, patch(
        "app.ui.storyboard_prompt_import_dialog.StoryboardPromptImportDialog"
    ) as editor, patch("app.ui.main_window.VideoStoryboardAnalysisDialog") as native:
        chooser.return_value.choice = choice
        chooser.return_value.exec.return_value = QDialog.DialogCode.Accepted if choice else QDialog.DialogCode.Rejected
        editor.return_value.exec.return_value = QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
        editor.return_value.imported_plan = plan
        MainWindow._start_video_storyboard_analysis(fake, SOURCE)
        if choice == "external" and accepted:
            fake.video_storyboard_page.apply_imported_plan.assert_called_once_with(plan)
            fake._persist_video_storyboard_state.assert_called_once()
            assert fake.video_storyboard_analysis_status == "ready"
        else:
            fake.video_storyboard_page.apply_imported_plan.assert_not_called()
        if choice == "llm":
            native.return_value.open.assert_called_once()
        else:
            native.assert_not_called()


def test_complete_prompts_do_not_inject_automatic_references(tmp_path):
    from app.core.storyboard_reference_images import scene_with_references
    image = tmp_path / "Mara.png"
    image.write_bytes(b"image")
    plan = {"continuity": {"characters": [{"id": "mara", "name": "Mara", "reference_image_path": str(image)}]}}
    scene = {"prompt": "Mara in a doodle", "generation_overrides": {"prompt_mode": "complete"}}
    assert not scene_with_references(scene, plan)["generation_overrides"]["reference_images"]
    scene["generation_overrides"]["reference_images"] = [{"path": str(image), "label": "Mara"}]
    assert len(scene_with_references(scene, plan)["generation_overrides"]["reference_images"]) == 1


@pytest.mark.parametrize("route", ["direct", "proxy", "reference"])
def test_complete_prompts_reach_image_provider_without_conflicting_exclusions(tmp_path, route):
    from app.core.video_storyboard_comfyui import _generate_litellm_storyboard_frame
    scene = {"prompt": "Split-screen stickman diagram with a red label.", "generation_overrides": {"prompt_mode": "complete"}}
    if route == "reference":
        scene["generation_overrides"]["reference_images"] = [{"path": "reference.png"}]
    plan = {"style": {"negative": "no text, no diagrams"}, "narrative_context": {"illustration_context": "Unrelated story"}}
    config = {"litellm_image": {"model": "test-model", "base_url": "http://localhost/v1" if route == "proxy" else ""}}
    target = {"direct": "app.core.video_storyboard_comfyui._litellm_image_direct", "proxy": "app.core.video_storyboard_comfyui._litellm_image_proxy", "reference": "app.core.storyboard_generation_references.litellm_reference_generation"}[route]
    with patch(target, return_value={}) as request, patch("app.core.video_storyboard_comfyui._save_litellm_image"), patch("app.core.storyboard_image_sizes.fit_frame"):
        _generate_litellm_storyboard_frame(scene, plan, config, tmp_path / "frame.png", status=None, cancelled=None)
    prompt = request.call_args.args[2] if route == "reference" else request.call_args.kwargs["prompt"]
    assert prompt == scene["prompt"]


def test_load_utf16_and_save_utf8(app, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    path = tmp_path / "input.txt"
    path.write_text("[00:00] Una ilustración", encoding="utf-16")
    dialog = StoryboardPromptImportDialog(tr, SOURCE)
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        dialog._load_text(dialog.editor, "*.txt")
    assert dialog.validate()
    output = tmp_path / "output.txt"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(output), "")):
        dialog._save_text()
    assert output.read_text(encoding="utf-8") == "[00:00] Una ilustración"
    dialog.deleteLater()
