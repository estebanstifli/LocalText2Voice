import base64
import io
from PIL import Image

def generated_png():
    buf = io.BytesIO()
    Image.new("RGB", (1280, 720)).save(buf, format="PNG")
    return buf.getvalue()

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.video_storyboard_comfyui import VideoStoryboardImageError, generate_storyboard_frame


def request(tmp_path):
    reference = tmp_path / "marco.png"
    reference.write_bytes(b"reference-bytes")
    scene = {"prompt": "Marco rides", "generation_overrides": {"reference_images": [{"path": str(reference), "label": "Marco"}]}}
    settings = {"image_provider": "litellm_image", "image_edit_provider": "comfyui",
                "litellm_image": {"model": "openai/gpt-image-2", "api_key": "generation-key", "timeout_seconds": 77},
                "litellm_image_edit": {"model": "different-model", "api_key": "edit-key", "base_url": "https://unused.invalid"},
                "image": {"width": 1280, "height": 720}}
    return scene, settings


def test_direct_generation_with_files_uses_generation_model_and_credentials(tmp_path):
    scene, settings = request(tmp_path)
    observed = {}
    def send(**kwargs):
        observed.update(kwargs)
        observed["bytes"] = [f.read() for f in kwargs["image"]]
        return {"data": [{"b64_json": base64.b64encode(generated_png()).decode()}]}
    with patch("litellm.image_edit", side_effect=send), patch("litellm.image_generation") as text_only, patch(
        "app.core.video_storyboard_image_edit.generate_edited_storyboard_image"
    ) as edit_flow:
        result = generate_storyboard_frame(scene, {}, settings, tmp_path / "result.png")
    assert observed["model"] == "openai/gpt-image-2"
    assert observed["api_key"] == "generation-key"
    assert observed["timeout"] == 77
    assert observed["size"] == "1280x720"
    assert observed["bytes"] == [b"reference-bytes"]
    assert all(f.closed for f in observed["image"])
    assert "Image 1 is the visual reference for Marco" in observed["prompt"]
    assert result["compiled_prompt"] == observed["prompt"]
    assert (tmp_path / "result.png").read_bytes() == generated_png()
    text_only.assert_not_called()
    edit_flow.assert_not_called()


def test_proxy_generation_keeps_generation_url_and_uploads_files(tmp_path):
    scene, settings = request(tmp_path)
    settings["litellm_image"]["base_url"] = "https://generation.invalid/v1"
    response = {"data": [{"b64_json": base64.b64encode(generated_png()).decode()}]}
    with patch("app.core.video_storyboard_image_edit._multipart_json", return_value=response) as send:
        generate_storyboard_frame(scene, {}, settings, tmp_path / "result.png")
    url, fields, files, timeout, headers = send.call_args.args
    assert url == "https://generation.invalid/v1/images/edits"
    assert fields["model"] == "openai/gpt-image-2"
    assert files[0][1].read_bytes() == b"reference-bytes"
    assert headers["Authorization"] == "Bearer generation-key"
    assert timeout == 77


def test_multiple_proxy_references_are_uploaded_as_an_ordered_array(tmp_path):
    scene, settings = request(tmp_path)
    second = tmp_path / "bike.png"
    second.write_bytes(b"bike-reference")
    scene["generation_overrides"]["reference_images"].append({"path": str(second), "label": "Bike"})
    settings["litellm_image"]["base_url"] = "https://generation.invalid/v1"
    response = {"data": [{"b64_json": base64.b64encode(generated_png()).decode()}]}
    with patch("app.core.video_storyboard_image_edit._multipart_json", return_value=response) as send:
        generate_storyboard_frame(scene, {}, settings, tmp_path / "result.png")
    files = send.call_args.args[2]
    assert [field for field, path in files] == ["image[]", "image[]"]
    assert [path.read_bytes() for field, path in files] == [b"reference-bytes", b"bike-reference"]


@pytest.mark.parametrize("edit_provider", ["comfyui", "runpod"])
def test_main_window_does_not_start_editing_resources_for_generation(tmp_path, edit_provider):
    from app.ui.main_window import MainWindow
    scene, settings = request(tmp_path)
    settings["image_edit_provider"] = edit_provider
    holder = MagicMock()
    holder.settings = {"video_storyboard": settings}
    holder.current_audiobook_id = None
    holder.video_storyboard_page.project_state.return_value = {"scenes": [scene]}
    with patch("app.ui.main_window.QThread"), patch("app.ui.main_window.VideoStoryboardFrameWorker") as worker:
        MainWindow._start_video_storyboard_frame_worker(holder, {"scenes": [scene]}, tmp_path, candidate=False)
    holder._release_storyboard_local_vram.assert_not_called()
    holder._confirm_runpod_storage.assert_not_called()
    assert worker.call_args.args[1]["image_provider"] == "litellm_image"
    assert worker.call_args.args[1] is not settings


def test_edit_provider_does_not_trigger_runpod_batch_cost_confirmation(tmp_path):
    from app.ui.main_window import MainWindow
    holder = MagicMock()
    for name in ("video_storyboard_frame_thread", "video_storyboard_video_thread", "video_storyboard_planner_thread", "video_storyboard_render_thread", "video_storyboard_image_edit_thread"):
        setattr(holder, name, None)
    holder.settings = {"video_storyboard": {"image_provider": "litellm_image", "image_edit_provider": "runpod"}}
    MainWindow._start_video_storyboard_frame_generation(holder, {"scenes": [{"scene_id": "1"}]})
    holder._confirm_runpod_batch_cost.assert_not_called()
    holder._start_video_storyboard_frame_worker.assert_called_once()


@pytest.mark.parametrize("provider", ["comfyui", "runpod"])
def test_text_only_generation_rejects_references_without_switching_provider(tmp_path, provider):
    scene, settings = request(tmp_path)
    settings["image_provider"] = provider
    with patch("app.core.video_storyboard_image_edit.generate_edited_storyboard_image") as edit:
        with pytest.raises(VideoStoryboardImageError, match="does not accept reference images"):
            generate_storyboard_frame(scene, {}, settings, tmp_path / "result.png")
        edit.assert_not_called()


def test_custom_generation_workflow_receives_uploads_at_generation_server(tmp_path):
    scene, settings = request(tmp_path)
    path = tmp_path / "workflow.json"
    workflow = {"1": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
                "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}}}
    path.write_text(json.dumps(workflow), encoding="utf-8")
    settings.update(image_provider="custom_comfyui", comfyui={"workflow_path": str(path), "base_url": "http://generation.invalid:8188"})
    with patch("app.core.video_storyboard_comfyui.build_custom_image_workflow", return_value=workflow), patch(
        "app.core.video_storyboard_image_edit._upload_image", return_value="uploads/marco.png"
    ) as upload, patch("app.core.video_storyboard_comfyui._http_json", return_value={"prompt_id": "job"}) as submit, patch(
        "app.core.video_storyboard_comfyui._wait_for_history", return_value={}
    ), patch("app.core.video_storyboard_comfyui._find_output_image", return_value={"filename": "out.png", "subfolder": "", "type": "output"}), patch(
        "app.core.video_storyboard_comfyui._download"
    ):
        generate_storyboard_frame(scene, {}, settings, tmp_path / "result.png")
    assert upload.call_args.args[0] == "http://generation.invalid:8188"
    assert submit.call_args.kwargs["payload"]["prompt"]["1"]["inputs"]["image"] == "uploads/marco.png"


def test_provider_failure_never_retries_without_references_or_changes_model(tmp_path):
    scene, settings = request(tmp_path)
    with patch("litellm.image_edit", side_effect=RuntimeError("model does not support image inputs")) as send, patch(
        "litellm.image_generation"
    ) as no_files:
        with pytest.raises(VideoStoryboardImageError, match="selected generation model"):
            generate_storyboard_frame(scene, {}, settings, tmp_path / "result.png")
        assert send.call_count == 1
        no_files.assert_not_called()
