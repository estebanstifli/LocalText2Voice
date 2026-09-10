from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, PngImagePlugin

from scripts import generate_style_images_1024 as generator


def png_bytes(size=(1024, 1024)):
    stream = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("prompt", "Original server workflow metadata")
    Image.new("RGB", size, "gold").save(stream, format="PNG", pnginfo=metadata)
    return stream.getvalue()


def args(tmp_path, *extra):
    return generator.arguments(["--output", str(tmp_path), "--config",
                                str(generator.ROOT / "config.example.json"), *extra])


def test_dry_run_builds_all_50_real_workflows_without_network(tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Dry run must not contact ComfyUI")

    monkeypatch.setattr(generator, "prepare_image_runtime", forbidden)
    monkeypatch.setattr(generator, "generate_storyboard_frame", forbidden)
    assert generator.run(args(tmp_path, "--dry-run")) == 0
    workflows = list((tmp_path / "workflows").glob("*.json"))
    assert len(workflows) == 50
    plan = json.loads((tmp_path / "generation_plan.json").read_text())
    assert len({item["id"] for item in plan["styles"]}) == 50
    for path in workflows:
        workflow = json.loads(path.read_text())
        assert workflow["6"]["inputs"] == {"width": 1024, "height": 1024, "batch_size": 1}
        assert workflow["10"]["inputs"]["images"] == ["9", 0]
        assert "z_image_turbo" in workflow["1"]["inputs"]["unet_name"]
    assert not list(tmp_path.glob("*.png"))


def test_original_bytes_preserved_and_resume_does_not_generate_again(tmp_path, monkeypatch):
    original = png_bytes()
    calls = []
    monkeypatch.setattr(generator, "prepare_image_runtime", lambda *a, **k: {})

    def generate(scene, plan, settings, target, **kwargs):
        calls.append(scene["scene_id"])
        Path(target).write_bytes(original)
        return {"prompt_id": "test-prompt"}

    monkeypatch.setattr(generator, "generate_storyboard_frame", generate)
    selected = args(tmp_path, "--style", "watercolor")
    assert generator.run(selected) == 0
    target = tmp_path / "watercolor.png"
    assert target.read_bytes() == original
    assert generator.run(selected) == 0
    assert calls == ["style-watercolor"]
    assert generator.run(args(tmp_path, "--style", "watercolor", "--seed", "42")) == 1
    assert target.read_bytes() == original
    assert len(calls) == 1


def test_wrong_resolution_cannot_overwrite_previous_good_image(tmp_path, monkeypatch):
    original = png_bytes()
    target = tmp_path / "watercolor.png"
    target.write_bytes(original)
    monkeypatch.setattr(generator, "prepare_image_runtime", lambda *a, **k: {})

    def generate(scene, plan, settings, temporary, **kwargs):
        Path(temporary).write_bytes(png_bytes((512, 512)))
        return {}

    monkeypatch.setattr(generator, "generate_storyboard_frame", generate)
    assert generator.run(args(tmp_path, "--style", "watercolor", "--force")) == 1
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.pending.png"))
    assert "1024x1024" in json.loads((tmp_path / "manifest.json").read_text())["failures"][0]["error"]


def test_partial_failure_saves_successes_and_can_resume(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(generator, "prepare_image_runtime", lambda *a, **k: {})

    def generate(scene, plan, settings, target, **kwargs):
        calls.append(scene["scene_id"])
        if len(calls) == 1:
            raise generator.VideoStoryboardImageError("Temporary server failure")
        Path(target).write_bytes(png_bytes())
        return {"prompt_id": "ok"}

    monkeypatch.setattr(generator, "generate_storyboard_frame", generate)
    selected = args(tmp_path, "--style", "watercolor", "--style", "oil_painting")
    assert generator.run(selected) == 1
    assert (tmp_path / "oil_painting.png").is_file()
    assert generator.run(selected) == 0
    assert calls == ["style-watercolor", "style-oil_painting", "style-watercolor"]
    assert len(json.loads((tmp_path / "manifest.json").read_text())["samples"]) == 2
