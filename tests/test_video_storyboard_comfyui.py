from __future__ import annotations

from pathlib import Path
import base64
from unittest.mock import patch

from app.core.video_storyboard_comfyui import (
    build_z_image_workflow,
    canonical_character_lines_for_prompt,
    compile_effective_scene_prompt,
    compile_scene_prompt,
    generate_storyboard_frame,
    prepare_image_runtime,
)


def test_effective_prompt_applies_all_temporary_frame_overrides() -> None:
    plan, scene = _plan_and_scene()
    scene["generation_overrides"] = {
        "style": {
            "medium": "charcoal illustration",
            "characters": ["Mara: older woman with silver hair and a red coat"],
        },
    }
    scene["shot"] = "profile close-up, 85mm viewpoint"
    prompt = compile_effective_scene_prompt(plan, scene)

    assert "STYLE: charcoal illustration" in prompt
    assert "Mara: older woman with silver hair" in prompt
    assert "SHOT AND COMPOSITION: profile close-up, 85mm viewpoint" in prompt


def test_effective_prompt_honors_exact_raw_override_without_recomposition() -> None:
    plan, scene = _plan_and_scene()
    scene["generation_overrides"] = {
        "raw_prompt": "Exact manually composed positive prompt"
    }

    assert compile_effective_scene_prompt(plan, scene) == (
        "Exact manually composed positive prompt"
    )


def test_litellm_image_provider_generates_and_saves_base64_image(tmp_path: Path) -> None:
    plan, scene = _plan_and_scene()
    settings = _settings()
    settings["image_provider"] = "litellm_image"
    settings["litellm_image"] = {
        "base_url": "",
        "model": "openai/gpt-image-1",
        "api_key": "secret",
        "timeout_seconds": 120,
    }
    png = b"\x89PNG\r\n\x1a\nmock-image"

    class Response:
        def model_dump(self):
            return {
                "id": "img-1",
                "data": [{"b64_json": base64.b64encode(png).decode("ascii")}],
            }

    target = tmp_path / "frame.png"
    stages: list[str] = []
    with patch("litellm.image_generation", return_value=Response()) as generation:
        runtime = prepare_image_runtime(settings)
        result = generate_storyboard_frame(
            scene, plan, settings, target, status=stages.append
        )

    assert runtime["provider"] == "litellm_image"
    assert target.read_bytes() == png
    assert result["prompt_id"] == "img-1"
    assert stages == ["generating", "downloading"]
    kwargs = generation.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-image-1"
    assert kwargs["n"] == 1
    assert kwargs["size"] == "1280x720"
    assert "seed" not in kwargs
    assert result["seed_applied"] is False


def test_litellm_image_retries_without_a_rejected_optional_parameter(
    tmp_path: Path,
) -> None:
    plan, scene = _plan_and_scene()
    settings = _settings()
    settings["image_provider"] = "litellm_image"
    settings["litellm_image"] = {
        "base_url": "",
        "model": "stability/stable-image-ultra",
        "api_key": "secret",
        "timeout_seconds": 120,
    }
    png = b"\x89PNG\r\n\x1a\nmock-image"

    class UnsupportedSeedError(RuntimeError):
        status_code = 400

    class Response:
        def model_dump(self):
            return {
                "id": "img-2",
                "data": [{"b64_json": base64.b64encode(png).decode("ascii")}],
            }

    target = tmp_path / "frame.png"
    with patch(
        "litellm.image_generation",
        side_effect=[
            UnsupportedSeedError("Unsupported parameter: seed"),
            Response(),
        ],
    ) as generation:
        result = generate_storyboard_frame(scene, plan, settings, target)

    assert generation.call_count == 2
    assert generation.call_args_list[0].kwargs["seed"] == plan["base_seed"]
    assert "seed" not in generation.call_args_list[1].kwargs
    assert result["seed_applied"] is False
    assert target.read_bytes() == png


def _settings() -> dict:
    return {
        "comfyui": {
            "base_url": "http://127.0.0.1:8188",
            "workflow_path": "",
            "diffusion_model": "z_image_turbo_bf16.safetensors",
            "text_encoder": "qwen_3_4b.safetensors",
            "vae_model": "ae.safetensors",
            "timeout_seconds": 900,
        },
        "ollama": {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3:8b",
        },
        "image": {
            "width": 1280,
            "height": 720,
            "steps": 8,
            "cfg": 1.0,
            "sampler": "res_multistep",
            "scheduler": "simple",
            "denoise": 1.0,
            "auraflow_shift": 3.0,
        },
    }


def _plan_and_scene() -> tuple[dict, dict]:
    plan = {
        "base_seed": 12345,
        "style": {
            "medium": "painted cinematic realism",
            "palette": "green and gold",
            "lighting": "soft morning light",
            "continuity": "Alpine railway",
            "characters": [
                "Mara: young woman with short dark hair and blue coat",
                "Conductor: older man in a navy uniform",
            ],
            "negative": "text, watermark, malformed anatomy",
        },
    }
    scene = {
        "scene_id": "001",
        "narration": "Mara waits beside the railway.",
        "era": "late nineteenth century",
        "characters": ["Mara"],
        "shot": "wide shot from a low angle",
        "prompt": "Mara stands beside a train in a green valley",
    }
    return plan, scene


def test_default_workflow_and_prompt_use_locked_style_and_models() -> None:
    plan, scene = _plan_and_scene()
    prompt = compile_scene_prompt(plan, scene)
    workflow = build_z_image_workflow(
        prompt,
        12345,
        _settings(),
        "test/scene-001",
    )

    assert "Mara: young woman" in prompt
    assert "Conductor: older man" not in prompt
    assert prompt == (
        "SCENE: Mara stands beside a train in a green valley. "
        "CHARACTERS: Mara: young woman with short dark hair and blue coat. "
        "STYLE: painted cinematic realism; palette: green and gold; lighting: soft morning light. "
        "SHOT AND COMPOSITION: wide shot from a low angle. "
        "ERA AND MATERIAL CULTURE: late nineteenth century."
    )
    assert workflow["1"]["inputs"]["unet_name"] == "z_image_turbo_bf16.safetensors"
    assert workflow["2"]["inputs"]["type"] == "lumina2"
    assert workflow["2"]["inputs"]["clip_name"] == "qwen_3_4b.safetensors"
    assert workflow["3"]["inputs"]["vae_name"] == "ae.safetensors"
    assert workflow["8"]["inputs"]["seed"] == 12345
    assert workflow["8"]["inputs"]["steps"] == 8
    assert workflow["6"]["inputs"]["width"] == 1280


def test_prompt_omits_empty_optional_style_and_era_labels() -> None:
    plan = {
        "style": {
            "medium": "minimalist ink illustration",
            "palette": "",
            "lighting": "",
            "continuity": (
                "Consistent character design, proportions, facial features, "
                "rendering technique and cinematic color grading across scenes"
            ),
            "characters": [],
        },
        "narrative_context": {"era": ""},
    }
    scene = {
        "prompt": "A lone bird crosses an empty sky",
        "shot": "wide profile shot",
        "era": "",
        "characters": [],
    }

    prompt = compile_scene_prompt(plan, scene)

    assert prompt == (
        "SCENE: A lone bird crosses an empty sky. "
        "STYLE: minimalist ink illustration. "
        "SHOT AND COMPOSITION: wide profile shot."
    )
    assert "Single cinematic storyboard frame" not in prompt
    assert "continuity:" not in prompt
    assert "palette:" not in prompt
    assert "lighting:" not in prompt
    assert "ERA AND MATERIAL CULTURE" not in prompt
    assert "CHARACTERS IN FRAME" not in prompt


def test_prompt_uses_only_character_states_selected_by_semantic_analysis() -> None:
    plan, scene = _plan_and_scene()
    scene["narration"] = "A train crosses an empty Alpine valley."
    scene["characters"] = ["Conductor"]

    prompt = compile_scene_prompt(plan, scene)

    assert "Mara: young woman" not in prompt
    assert "Conductor: older man" in prompt


def test_prompt_injects_exact_temporal_character_location_and_era_locks() -> None:
    plan, scene = _plan_and_scene()
    plan["continuity"] = {
        "characters": [
            {
                "id": "char_luis",
                "name": "Luis",
                "identity_description": "dark complexion, square face and brown eyes",
                "states": [
                    {
                        "id": "char_luis_state_1",
                        "description": "adult man with glasses, blue shirt and jeans",
                    },
                    {
                        "id": "char_luis_state_2",
                        "description": "older man without glasses, white shirt and shorts",
                    },
                ],
            }
        ],
        "locations": [
            {
                "id": "loc_maria_house",
                "name": "Maria's house",
                "identity_description": "small white masonry house with two front windows",
                "states": [
                    {
                        "id": "loc_maria_house_state_1",
                        "description": "green wooden door and flower pots beneath the windows",
                    }
                ],
            }
        ],
        "eras": [
            {
                "id": "era_roman_republic",
                "description": "Roman Republic, 1st century BCE",
                "material_culture": "wool tunics, stone streets and oil lamps",
            }
        ],
    }
    scene.update(
        {
            "prompt": "Luis arrives at Maria's house carrying a leather bag",
            "characters": ["char_luis_state_2"],
            "locations": ["loc_maria_house_state_1"],
            "era_state_id": "era_roman_republic",
            "era": "",
        }
    )

    prompt = compile_scene_prompt(plan, scene)

    assert "Luis (dark complexion, square face and brown eyes, older man without glasses" in prompt
    assert "adult man with glasses" not in prompt
    assert "Maria's house, small white masonry house with two front windows" in prompt
    assert "wool tunics, stone streets and oil lamps" in prompt
    assert prompt.index("SCENE:") < prompt.index("STYLE:")
    assert prompt.index("STYLE:") < prompt.index("SHOT AND COMPOSITION:")
    assert prompt.index("SHOT AND COMPOSITION:") < prompt.index("ERA AND MATERIAL CULTURE:")


def test_canonical_prompt_name_selects_only_the_active_character_state() -> None:
    plan = {
        "continuity": {
            "characters": [
                {
                    "id": "char_heidi",
                    "name": "Heidi",
                    "identity_description": "young girl with dark hair and freckles",
                    "states": [
                        {
                            "id": "char_heidi_state_1",
                            "description": "wearing a red dress",
                            "from_seconds": 2.0,
                            "to_seconds": 58.0,
                        },
                        {
                            "id": "char_heidi_state_2",
                            "description": "adult woman wearing a blue coat",
                            "from_seconds": 120.0,
                            "to_seconds": 180.0,
                        },
                    ],
                }
            ]
        }
    }
    scene = {"start_seconds": 40.0, "duration_seconds": 8.0}

    lines = canonical_character_lines_for_prompt(
        plan,
        scene,
        "Heidi kneels under tree branches near a stream.",
    )

    assert lines == [
        "char_heidi_state_1: Heidi, young girl with dark hair and freckles, wearing a red dress"
    ]


def test_canonical_prompt_name_is_not_selected_outside_its_time_range() -> None:
    plan = {
        "continuity": {
            "characters": [
                {
                    "name": "Heidi",
                    "states": [
                        {
                            "id": "char_heidi_state_1",
                            "description": "young girl",
                            "from_seconds": 2.0,
                            "to_seconds": 58.0,
                        }
                    ],
                }
            ]
        }
    }
    scene = {"start_seconds": 70.0, "duration_seconds": 6.0}

    assert canonical_character_lines_for_prompt(
        plan, scene, "Heidi walks through the square."
    ) == []


def test_legacy_coverage_prompt_is_cleaned_before_comfyui_generation() -> None:
    plan, scene = _plan_and_scene()
    scene.update(
        {
            "semantic_scene_id": "001",
            "coverage_index": 2,
            "coverage_count": 3,
            "shot": "medium three-quarter view centered on the main subject and the ongoing action",
            "prompt": (
                "Mara stands beside a train Cinematic coverage frame 2 of 3: "
                "medium three-quarter view centered on the main subject "
                "Preserve exactly the same location, characters and ongoing action."
            ),
        }
    )

    prompt = compile_scene_prompt(plan, scene)

    assert "Cinematic coverage frame" not in prompt
    assert "Preserve exactly" not in prompt
    assert "medium three-quarter view centered" not in prompt
    assert "Mara stands beside a train." in prompt
    assert any(
        framing in prompt
        for framing in (
            "Close-up of Mara.",
            "Profile view of Mara.",
            "Three-quarter view of Mara.",
            "Low-angle view of Mara.",
        )
    )


def test_generation_queues_waits_and_downloads_frame(tmp_path: Path) -> None:
    plan, scene = _plan_and_scene()
    scene["generation_overrides"] = {
        "seed": 777,
        "style": {"medium": "charcoal cinematic illustration"},
    }
    target = tmp_path / "scene.png"

    def fake_http(url, **kwargs):
        if url.endswith("/prompt"):
            workflow = kwargs["payload"]["prompt"]
            assert workflow["8"]["inputs"]["seed"] == 777
            return {"prompt_id": "prompt-1"}
        if url.endswith("/history/prompt-1"):
            return {
                "prompt-1": {
                    "status": {"completed": True, "status_str": "success"},
                    "outputs": {
                        "10": {
                            "images": [
                                {
                                    "filename": "scene.png",
                                    "subfolder": "ltv",
                                    "type": "output",
                                }
                            ]
                        }
                    },
                }
            }
        raise AssertionError(url)

    def fake_download(url, selected_target, timeout, headers=None):
        assert "filename=scene.png" in url
        assert timeout == 120
        selected_target.write_bytes(b"fake-png")

    stages: list[str] = []
    with (
        patch(
            "app.core.video_storyboard_comfyui._http_json",
            side_effect=fake_http,
        ),
        patch(
            "app.core.video_storyboard_comfyui._download",
            side_effect=fake_download,
        ),
    ):
        result = generate_storyboard_frame(
            scene,
            plan,
            _settings(),
            target,
            status=stages.append,
        )

    assert stages == ["queue", "generating", "downloading"]
    assert result["scene_id"] == "001"
    assert result["image_path"] == str(target)
    assert result["seed"] == 777
    assert "charcoal cinematic illustration" in result["compiled_prompt"]
    assert target.read_bytes() == b"fake-png"


def test_runtime_check_finds_configured_nodes_and_models() -> None:
    calls: list[str] = []

    def fake_http(url, **_kwargs):
        calls.append(url)
        if url.endswith("/api/generate"):
            return {}
        if url.endswith("/api/ps"):
            return {"models": []}
        if url.endswith("/system_stats"):
            return {
                "system": {"comfyui_version": "0.34.1"},
                "devices": [{"name": "RTX", "vram_total": 8, "vram_free": 7}],
            }
        if url.endswith("/object_info"):
            names = {
                "UNETLoader": ("unet_name", "z_image_turbo_bf16.safetensors"),
                "CLIPLoader": ("clip_name", "qwen_3_4b.safetensors"),
                "VAELoader": ("vae_name", "ae.safetensors"),
            }
            value = {
                node: {"input": {"required": {field: [[model]]}}}
                for node, (field, model) in names.items()
            }
            for node in (
                "CLIPTextEncode", "ConditioningZeroOut", "EmptySD3LatentImage",
                "ModelSamplingAuraFlow", "KSampler", "VAEDecode", "SaveImage",
            ):
                value[node] = {}
            return value
        if url.endswith("/free"):
            return {}
        raise AssertionError(url)

    with patch(
        "app.core.video_storyboard_comfyui._http_json",
        side_effect=fake_http,
    ):
        runtime = prepare_image_runtime(_settings())

    assert runtime["version"] == "0.34.1"
    assert any(url.endswith("/api/generate") for url in calls)
    assert any(url.endswith("/free") for url in calls)


def test_custom_image_mapping_preserves_unmapped_inputs():
    from app.core.video_storyboard_comfyui import build_custom_image_workflow, VideoStoryboardImageError
    import pytest
    template = {"3": {"class_type": "CustomNode", "inputs": {"text": "original", "seed": 12, "steps": 30, "width": "{{WIDTH}}"}}}
    values = dict(prompt="new prompt", negative_prompt="", seed=42, width=640, height=360, steps=8, cfg=1.0, output_prefix="test")
    result = build_custom_image_workflow(template, values, {"prompt": "3.text", "seed": "3.seed"})
    assert result["3"]["inputs"] == dict(text="new prompt", seed=42, steps=30, width=640)
    assert template["3"]["inputs"]["text"] == "original"
    with pytest.raises(VideoStoryboardImageError, match="Invalid image workflow binding"):
        build_custom_image_workflow(template, values, {"prompt": "3.missing"})
    with pytest.raises(VideoStoryboardImageError, match="Map the prompt"):
        build_custom_image_workflow(template, values, {})


def test_custom_image_remote_runtime_and_generation(tmp_path):
    import json
    settings = _settings()
    settings["image_provider"] = "custom_comfyui"
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"1": {"class_type": "MyImageNode", "inputs": {"text": "{{PROMPT}}", "seed": "{{SEED}}"}}}))
    settings["comfyui"].update(workflow_path=str(workflow), auth_token="remote-secret")
    def http(url, **kwargs):
        if "/api/" in url:
            return {}
        assert kwargs["headers"] == {"Authorization": "Bearer remote-secret"}
        if url.endswith("/system_stats"):
            return {"system": {}, "devices": []}
        if url.endswith("/object_info"):
            return {"MyImageNode": {}}
        if url.endswith("/prompt"):
            assert isinstance(kwargs["payload"]["prompt"]["1"]["inputs"]["seed"], int)
            return {"prompt_id": "job"}
        if url.endswith("/history/job"):
            return {"job": {"status": {"completed": True}, "outputs": {"1": {"images": [{"filename": "frame.png"}]}}}}
        return {}
    with patch("app.core.video_storyboard_comfyui._http_json", side_effect=http), patch("app.core.video_storyboard_comfyui._download") as download:
        prepare_image_runtime(settings)
        plan, scene = _plan_and_scene()
        generate_storyboard_frame(scene, plan, settings, tmp_path / "frame.png")
        assert download.call_args.kwargs["headers"] == {"Authorization": "Bearer remote-secret"}
