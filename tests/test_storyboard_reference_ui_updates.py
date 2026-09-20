import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog

from app.core.storyboard_provider_label import active_provider_label, provider_label
from app.ui.video_storyboard_entity_dialog import VideoStoryboardEntityDialog
from app.ui.video_storyboard_page import VideoStoryboardPage
from app.ui.video_storyboard_regeneration_dialog import VideoStoryboardRegenerationDialog

APP = QApplication.instance() or QApplication([])


def tr(key, default, **values):
    return default.format(**values)


def test_provider_names_use_job_snapshot_and_exact_models(tmp_path):
    running = {"image_provider": "litellm_image", "litellm_image": {"model": "openai/gpt-image-2", "api_key": "secret"}}
    window = SimpleNamespace(settings={"video_storyboard": {"image_provider": "comfyui"}},
                             video_storyboard_frame_worker=SimpleNamespace(settings=running))
    assert active_provider_label(window) == "LiteLLM · openai/gpt-image-2"
    assert provider_label({"image_provider": "runpod", "runpod": {"image_endpoint": "https://api.runpod.ai/v2/qwen-image-t2i?token=secret"}}) == "Runpod · qwen-image-t2i"
    assert provider_label({"image_provider": "comfyui", "comfyui": {"diffusion_model": "exact-model.safetensors"}}) == "ComfyUI · exact-model.safetensors"
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({"1": {"inputs": {"unet_name": "custom-model.gguf"}}}))
    assert provider_label({"image_provider": "custom_comfyui", "comfyui": {"workflow_path": str(path)}}) == "ComfyUI · custom-model.gguf"


def test_frame_progress_and_errors_report_running_provider():
    from app.ui.main_window import MainWindow
    worker = SimpleNamespace(settings={"image_provider": "litellm_image", "litellm_image": {"model": "openai/gpt-image-2"}})
    holder = SimpleNamespace(tr=tr, video_storyboard_frame_worker=worker, settings={},
                             _frame_generation_matches_current_project=lambda: True,
                             video_storyboard_frame_mode="candidate", video_storyboard_frame_scene_id="1",
                             video_storyboard_page=MagicMock(), log_view=MagicMock())
    MainWindow._on_video_storyboard_frame_progress(holder, 1, 4, "1", "generating")
    message = holder.video_storyboard_page.set_regeneration_progress.call_args.args[1]
    assert "LiteLLM · openai/gpt-image-2" in message
    assert "ComfyUI" not in message
    MainWindow._on_video_storyboard_frames_failed(holder, "Test failure")
    assert "LiteLLM · openai/gpt-image-2" in holder.log_view.append_event.call_args.args[0]


def test_automatic_reference_summary_matches_prompt_and_deduplicates_manual(tmp_path):
    path = tmp_path / "marco.png"
    Image.new("RGB", (16, 16), "blue").save(path)
    record = {"id": "marco", "name": "Marco", "reference_image_path": str(path), "states": []}
    dialog = VideoStoryboardRegenerationDialog(tr, {"prompt": "@Marco walks"}, {"continuity": {"characters": [record]}})
    assert dialog.reference_summary.text() == "Marco (automatic)"
    assert dialog.reference_summary.toolTip() == str(path)
    assert dialog.request_payload()["overrides"]["reference_images"] == []
    dialog._reference_images = [{"path": str(path), "label": "Marco"}]
    dialog._refresh_composed_prompt()
    assert dialog.reference_summary.text() == "Marco"
    assert dialog.raw_prompt_edit.toPlainText().count("Image 1 is the visual reference for Marco") == 1
    dialog._reference_images = []
    dialog.settings["auto_character_references"] = False
    dialog._refresh_composed_prompt()
    assert dialog.reference_summary.text() == "No image references"
    dialog.close()


def test_raw_only_automatic_references_appear_in_summary(tmp_path):
    path = tmp_path / "bike.png"
    Image.new("RGB", (16, 16)).save(path)
    plan = {"continuity": {"objects": [{"id": "bike", "name": "Bike", "reference_image_path": str(path)}]}}
    dialog = VideoStoryboardRegenerationDialog(tr, {"prompt": "A road"}, plan)
    dialog.raw_prompt_edit.setPlainText("@Bike on a road")
    assert dialog.reference_summary.text() == "Bike (automatic)"
    dialog.raw_prompt_edit.setPlainText("An empty road")
    assert dialog.reference_summary.text() == "No image references"
    dialog.close()


@pytest.mark.parametrize("kind, detail", [("location", "architecture"), ("object", "components")])
def test_entity_image_tabs_use_type_specific_editable_prompts(kind, detail):
    dialog = VideoStoryboardEntityDialog(tr, kind, 60, {"name": "Reference", "identity_description": "Red stone",
                                                     "states": [{"description": "Pristine"}]})
    assert dialog.tabs.count() == 3
    prompt = dialog.prompt_edit.toPlainText()
    assert detail in prompt and "Reference" in prompt and "Red stone" in prompt and "Pristine" in prompt
    assert "face close-up" not in prompt
    dialog.prompt_edit.setPlainText("My editable prompt")
    dialog.identity_edit.setPlainText("Updated description")
    assert dialog.prompt_edit.toPlainText() == "My editable prompt"
    dialog.reset_prompt_button.click()
    assert "Updated description" in dialog.prompt_edit.toPlainText()
    dialog.generate_image_button.click()
    assert dialog.tabs.currentIndex() == 1
    dialog.close()


@pytest.mark.parametrize("kind", ["location", "object"])
def test_entity_creation_and_editing_preserve_image_prompt_and_generation_settings(tmp_path, kind):
    page = VideoStoryboardPage(tr)
    page._source_project_dir = str(tmp_path)
    page._configuration = {"image_provider": "litellm_image", "litellm_image": {"model": "openai/gpt-image-2"}}
    path = tmp_path / "reference.png"
    Image.new("RGB", (1280, 720), "green").save(path)
    prompts = iter(["Original editable prompt", "Updated editable prompt"])
    def accept(dialog):
        assert dialog.generation_settings["litellm_image"]["model"] == "openai/gpt-image-2"
        dialog.name_edit.setText("Example")
        dialog.prompt_edit.setPlainText(next(prompts))
        dialog.reference_image_path = str(path)
        return QDialog.DialogCode.Accepted
    with patch.object(VideoStoryboardEntityDialog, "exec", new=accept):
        if kind == "location":
            page._new_location()
            page._edit_location()
        else:
            page.objects_panel.edit_record()
            page.objects_panel.edit_record(page.objects_panel.records()[0])
    record = page.project_state()["plan"]["continuity"][kind + "s"][0]
    assert record["reference_image_prompt"] == "Updated editable prompt"
    assert record["reference_image_path"].endswith("example.png")
    page.deleteLater()
