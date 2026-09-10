from __future__ import annotations

import os
import pytest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.core import video_storyboard_image_edit as image_edit
from app.core.video_storyboard_camera import camera_prompt, compose_image_edit_prompt
from app.ui.video_storyboard_image_edit_dialog import VideoStoryboardImageEditDialog
from app.core.video_storyboard_comfyui import compile_reference_edit_prompt
from app.ui.video_storyboard_entity_dialog import VideoStoryboardEntityDialog
from app.ui.video_storyboard_page import VideoStoryboardPage
from app.ui.video_storyboard_reference_picker_dialog import (
    VideoStoryboardReferencePickerDialog,
)
from app.ui.video_storyboard_regeneration_dialog import (
    VideoStoryboardRegenerationDialog,
)

_APPLICATION = QApplication.instance() or QApplication([])


def test_camera_lora_resolves_portable_names_without_guessing():
    from app.core.video_storyboard_camera import CAMERA_LORA_MODEL, resolve_camera_lora
    renamed = "Qwen-Edit-2509-Multiple-Angles.safetensors"
    assert resolve_camera_lora(CAMERA_LORA_MODEL, [renamed]) == renamed
    assert resolve_camera_lora(renamed, [CAMERA_LORA_MODEL]) == CAMERA_LORA_MODEL
    assert resolve_camera_lora(CAMERA_LORA_MODEL, ["camera/" + renamed]) == "camera/" + renamed
    assert resolve_camera_lora("custom.safetensors", ["custom.safetensors", renamed]) == "custom.safetensors"
    with pytest.raises(ValueError, match="not installed"):
        resolve_camera_lora("custom.safetensors", [renamed])
    with pytest.raises(ValueError, match="Multiple"):
        resolve_camera_lora(CAMERA_LORA_MODEL, ["a/" + renamed, "b/" + renamed])


def test_edit_worker_uses_resolved_server_lora_without_changing_settings(monkeypatch, tmp_path):
    from app.workers import video_storyboard_image_edit_worker as worker_module
    settings = {"comfyui_image_edit": {"camera_lora_model": "original"}}
    monkeypatch.setattr(worker_module, "prepare_image_edit_runtime", lambda *a, **k: {"camera_lora_model": "server/renamed"})
    seen = []
    def generate(refs, prompt, config, target, **kwargs):
        seen.append(config["comfyui_image_edit"]["camera_lora_model"])
        return {}
    monkeypatch.setattr(worker_module, "generate_edited_storyboard_image", generate)
    worker_module.VideoStoryboardImageEditWorker({}, settings, tmp_path / "candidate.png").run()
    assert seen == ["server/renamed"]
    assert settings["comfyui_image_edit"]["camera_lora_model"] == "original"


def test_camera_is_opt_in_and_keeps_lightning_in_the_model_chain():
    camera = {"enabled": True, "rotate_deg": 45, "vertical_tilt": -1, "move_forward": 5}
    assert camera_prompt({**camera, "enabled": False}) == ""
    assert camera_prompt({"enabled": True}) == ""
    assert "45 degrees to the left" in camera_prompt(camera)
    assert "bird's-eye" in camera_prompt(camera)
    for count in (1, 2, 3):
        for enabled in (False, True):
            workflow = image_edit._builtin_qwen_workflow(count)
            image_edit._configure_qwen_workflow(
                workflow, [f"ref-{i}.png" for i in range(count)], "Remove the hat.",
                {"camera_lora_model": "camera/angles.safetensors", "camera_lora_strength": 1.25},
                seed=19, width=1280, height=720, prefix="test",
                camera={**camera, "enabled": enabled},
            )
            assert workflow["117"]["inputs"]["model"] == ["115", 0]
            if enabled:
                adapter = workflow["ltv_camera_angles"]["inputs"]
                assert adapter["model"] == ["117", 0]
                assert adapter["lora_name"] == "camera/angles.safetensors"
                assert adapter["strength_model"] == 1.25
                assert workflow["66"]["inputs"]["model"] == ["ltv_camera_angles", 0]
            else:
                assert "ltv_camera_angles" not in workflow
                assert workflow["66"]["inputs"]["model"] == ["117", 0]


def test_missing_camera_lora_fails_before_memory_release_or_upload(monkeypatch):
    calls = []

    def http(url, **kwargs):
        calls.append(url)
        if url.endswith("system_stats"):
            return {}
        return {name: {} for name in (
            "UnetLoaderGGUF", "LoraLoaderModelOnly", "CLIPLoader", "VAELoader",
            "TextEncodeQwenImageEditPlus", "ImageScaleToTotalPixels", "KSampler",
        )}

    monkeypatch.setattr(image_edit, "_http_json", http)
    with pytest.raises(image_edit.VideoStoryboardImageEditError, match="not installed"):
        image_edit.prepare_image_edit_runtime(_settings(), camera={"enabled": True, "rotate_deg": 45})
    assert not any(url.endswith("/free") for url in calls)
    with pytest.raises(image_edit.VideoStoryboardImageEditError, match="require"):
        image_edit.prepare_image_edit_runtime({"image_edit_provider": "litellm_image"}, camera={"enabled": True, "move_forward": 10})


def test_edit_dialog_sends_visible_composed_prompt_and_keeps_reference_order(tmp_path):
    source = tmp_path / "source.png"
    reference = tmp_path / "cat.png"
    image = QImage(64, 36, QImage.Format.Format_RGB32)
    image.fill(0x8844CC)
    assert image.save(str(source))
    assert image.save(str(reference))
    dialog = VideoStoryboardImageEditDialog(_translate, "003", str(source), configuration=_settings())
    requests = []
    dialog.generateEditRequested.connect(requests.append)
    dialog.camera_controls.enable_check.setChecked(True)
    dialog.camera_controls.set_camera({"rotate_deg": -45})
    dialog._reference_images = [{"path": str(reference), "label": "Cat"}]
    dialog._refresh_references()
    dialog.ai_prompt_edit.setPlainText("Add the cat from Image 2.")
    prompt = dialog.prompt_preview.toPlainText()
    dialog.generate_ai_button.click()
    assert requests[0]["prompt"] == prompt
    assert "45 degrees to the right" in prompt
    assert prompt.startswith("Image 1 is the frame to edit. Image 2 is the visual reference for Cat.")
    assert requests[0]["camera"]["enabled"] is True
    assert requests[0]["reference_images"][1]["path"] == str(reference)
    assert len(requests[0]["reference_images"]) == 2
    assert not dialog.accept_button.isEnabled()
    assert not dialog.camera_controls.isEnabled()
    dialog.set_edit_failed("test")
    dialog.camera_controls.enable_check.setChecked(False)
    dialog._reference_images.clear()
    dialog.ai_prompt_edit.setPlainText("Remove the hat.")
    assert dialog._compiled_prompt() == "Remove the hat."
    dialog.reject()


def test_discard_ai_edit_restores_the_image_before_generation(tmp_path):
    source = tmp_path / "original.png"
    candidate = tmp_path / "candidate.png"
    image = QImage(32, 18, QImage.Format.Format_RGB32)
    image.fill(0xCC3344)
    assert image.save(str(source))
    dialog = VideoStoryboardImageEditDialog(_translate, "001", str(source), configuration=_settings())
    image.fill(0x33CC44)
    dialog._set_working(image)  # Manual edit made before requesting an AI candidate.
    dialog.ai_prompt_edit.setPlainText("Remove the hat.")
    dialog._request_ai_edit()
    image.fill(0x3344CC)
    assert image.save(str(candidate))
    dialog.set_edit_candidate(str(candidate))
    dialog._discard_ai_candidate()
    assert dialog._working.pixelColor(0, 0).name() == "#33cc44"
    dialog.reject()


def _translate(_key: str, default: str, **values: object) -> str:
    return default.format(**values)


def _settings() -> dict:
    return {
        "image_edit_provider": "comfyui",
        "comfyui_image_edit": {
            "base_url": "http://127.0.0.1:8188",
            "auth_token": "",
            "unet_model": "Qwen-Image-Edit-2509-Q3_K_S.gguf",
            "lora_model": "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors",
            "text_encoder": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
            "vae_model": "qwen_image_vae.safetensors",
            "width": 1280,
            "height": 720,
            "steps": 4,
            "cfg": 1.0,
            "timeout_seconds": 60,
        },
    }


def test_builtin_qwen_workflows_bind_one_to_three_references() -> None:
    for count in (1, 2, 3):
        workflow = image_edit._builtin_qwen_workflow(count)
        uploads = [f"ltv/reference-{index}.png" for index in range(1, count + 1)]
        image_edit._configure_qwen_workflow(
            workflow,
            uploads,
            "Keep the girl's face and change the coat to blue.",
            _settings()["comfyui_image_edit"],
            seed=8123,
            width=1280,
            height=720,
            prefix="LocalText2Voice/image-edit/test",
        )
        assert workflow["111"]["inputs"]["prompt"].startswith("Keep the girl's face")
        assert workflow["3"]["inputs"]["seed"] == 8123
        assert workflow["3"]["inputs"]["steps"] == 4
        assert workflow["112"]["inputs"]["width"] == 1280
        assert workflow["112"]["inputs"]["height"] == 720
        assert workflow["60"]["inputs"]["filename_prefix"].endswith("test")
        assert workflow["78"]["inputs"]["image"] == uploads[0]
        assert {
            key for key in workflow["111"]["inputs"] if key.startswith("image")
        } == {f"image{index}" for index in range(1, count + 1)}


def test_comfyui_edit_uploads_references_and_downloads_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "ana.png"
    second = tmp_path / "plaza.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        image_edit,
        "_upload_image",
        lambda _root, path, _timeout, _headers: f"uploads/{path.name}",
    )

    def fake_http(url, *, method="GET", payload=None, timeout, headers=None):
        if url.endswith("/prompt"):
            captured["workflow"] = payload["prompt"]
            return {"prompt_id": "edit-123"}
        return {
            "edit-123": {
                "status": {"completed": True},
                "outputs": {"60": {"images": [{"filename": "edited.png", "type": "output"}]}},
            }
        }

    monkeypatch.setattr(image_edit, "_http_json", fake_http)
    monkeypatch.setattr(
        image_edit,
        "_download_with_headers",
        lambda _url, target, **_kwargs: target.write_bytes(b"edited"),
    )
    target = tmp_path / "candidate.png"
    result = image_edit.generate_edited_storyboard_image(
        [
            {"path": str(first), "label": "Ana"},
            {"path": str(second), "label": "Town square"},
        ],
        "Place Ana in the square while preserving her face.",
        _settings(),
        target,
        seed=77,
        width=1024,
        height=576,
        scene_id="004",
    )
    workflow = captured["workflow"]
    assert workflow["78"]["inputs"]["image"] == "uploads/ana.png"
    assert workflow["106"]["inputs"]["image"] == "uploads/plaza.png"
    assert workflow["112"]["inputs"]["width"] == 1024
    assert result["scene_id"] == "004"
    assert target.read_bytes() == b"edited"


def test_reference_validation_is_bounded_to_three(tmp_path: Path) -> None:
    paths = []
    for index in range(4):
        path = tmp_path / f"{index}.png"
        path.write_bytes(b"x")
        paths.append({"path": str(path), "label": str(index)})
    # Normalization deliberately truncates the picker/API contract to three.
    assert len(image_edit._normalize_references(paths)) == 3


def test_reference_prompt_roles_are_visible_and_not_duplicated() -> None:
    references = [
        {"path": "ana.png", "label": "Ana"},
        {"path": "square.png", "label": "Town square"},
    ]
    prompt = compile_reference_edit_prompt("Ana walks toward the fountain.", references)
    assert prompt.startswith(
        "Image 1 is the visual reference for Ana. "
        "Image 2 is the visual reference for Town square."
    )
    assert compile_reference_edit_prompt(prompt, references) == prompt


def test_entity_dialog_and_reference_picker_show_saved_thumbnails(tmp_path: Path) -> None:
    application = _APPLICATION
    source = tmp_path / "ana.png"
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(0x8844CC)
    assert image.save(str(source), "PNG")
    record = {
        "id": "ana",
        "name": "Ana",
        "identity_description": "Young woman with short dark hair",
        "reference_image_path": str(source),
        "states": [{
            "id": "ana_state_1",
            "description": "Blue coat",
            "from_seconds": 0,
            "to_seconds": 30,
        }],
    }
    entity_dialog = VideoStoryboardEntityDialog(
        _translate, "character", 30, record
    )
    assert not entity_dialog.reference_preview.pixmap().isNull()
    assert entity_dialog.values()["reference_image_path"] == str(source)
    entity_dialog.deleteLater()

    picker = VideoStoryboardReferencePickerDialog(
        _translate,
        {"continuity": {"characters": [record], "locations": []}},
        [{"path": str(source), "label": "Ana"}],
    )
    application.processEvents()
    selected = picker.selected_references()
    assert len(selected) == 1
    assert selected[0]["label"] == "Ana"
    picker.deleteLater()


def test_reference_is_copied_into_project_with_normalized_entity_name(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(0x33AA66)
    assert image.save(str(source), "JPG")
    page = VideoStoryboardPage(_translate)
    page._source_project_dir = str(tmp_path / "project")
    stored = Path(page._import_entity_reference(str(source), "characters", "Ána María"))
    assert stored.name == "ana_maria.png"
    assert stored.parent.name == "characters"
    assert stored.is_file()
    page.deleteLater()


def test_regeneration_payload_persists_selected_reference_images(tmp_path: Path) -> None:
    reference = tmp_path / "ana.png"
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(0x3344AA)
    assert image.save(str(reference), "PNG")
    scene = {
        "scene_id": "001",
        "prompt": "Ana walks through the square",
        "shot": "wide shot",
        "duration_seconds": 6,
    }
    plan = {
        "base_seed": 12,
        "style": {},
        "continuity": {"characters": [], "locations": [], "eras": []},
    }
    dialog = VideoStoryboardRegenerationDialog(_translate, scene, plan)
    dialog._reference_images = [{"path": str(reference), "label": "Ana"}]
    payload = dialog.request_payload()
    assert payload["overrides"]["reference_images"] == [
        {"path": str(reference), "label": "Ana"}
    ]
    dialog.deleteLater()
