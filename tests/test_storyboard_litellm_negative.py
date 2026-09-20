from unittest.mock import patch

import pytest

from app.core.video_storyboard_comfyui import _generate_litellm_storyboard_frame


@pytest.mark.parametrize("route", ["direct", "proxy", "reference"])
@pytest.mark.parametrize("override", [False, True])
def test_litellm_sends_effective_negative_in_required_prompt(tmp_path, route, override):
    scene = {"scene_id": "007", "prompt": "A piper in the street.",
             "generation_overrides": {"raw_prompt": "One piper playing a pipe.",
                                      "style": {"negative": "collage, comic panels, lettering"}}}
    if route == "reference":
        scene["generation_overrides"]["reference_images"] = [{"path": "reference.png"}]
    plan = {"style": {"negative": "text, split screen, collage"}, "base_seed": 12}
    if not override:
        scene["generation_overrides"].pop("style")
    settings = {"litellm_image": {"model": "openai/gpt-image-2",
                "base_url": "http://localhost:4000/v1" if route == "proxy" else ""}}
    targets = {
        "direct": "app.core.video_storyboard_comfyui._litellm_image_direct",
        "proxy": "app.core.video_storyboard_comfyui._litellm_image_proxy",
        "reference": "app.core.storyboard_generation_references.litellm_reference_generation",
    }
    with patch(targets[route], return_value={}) as request, patch(
        "app.core.video_storyboard_comfyui._save_litellm_image"
    ):
        result = _generate_litellm_storyboard_frame(
            scene, plan, settings, tmp_path / "frame.png", status=None, cancelled=None
        )
    prompt = request.call_args.args[2] if route == "reference" else request.call_args.kwargs["prompt"]
    assert "One piper playing a pipe." in prompt
    assert "one full-frame image" in prompt
    expected = "collage, comic panels, lettering" if override else "text, split screen, collage"
    assert "EXCLUDE FROM THE IMAGE (negative prompt): " + expected in prompt
    assert result["compiled_prompt"] == prompt
