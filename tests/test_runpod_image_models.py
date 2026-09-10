import pytest
from app.core.runpod_image_models import IMAGE_MODELS, image_parameters, image_model_id
from app.core import video_storyboard_runpod as rp


@pytest.mark.parametrize("endpoint", IMAGE_MODELS)
def test_model_contracts_and_prices(endpoint):
    config = {"image_endpoint": endpoint}
    width, height = (1440, 1024) if endpoint == "wan-2-6-t2i" else (1280, 720)
    values = image_parameters(config, "Scene", 42, width, height)
    assert values["prompt"] == "Scene" and values["seed"] == 42
    assert endpoint in rp.PUBLIC_ENDPOINTS
    if endpoint == "p-image-t2i":
        assert values == {"prompt": "Scene", "seed": 42, "aspect_ratio": "16:9"}
    else:
        assert values["size"] == f"{width}*{height}"
        assert ("output_format" in values) == (endpoint == "z-image-turbo")
    assert rp.estimate_cost({"runpod": config}, images=2) == IMAGE_MODELS[endpoint]["price"] * 2
    assert image_model_id({"image_endpoint": f"https://api.runpod.ai/v2/{endpoint}/runsync"}) == endpoint


def test_p_image_portrait_and_nearest_supported_ratio():
    assert image_parameters({"image_endpoint": "p-image-t2i"}, "Scene", 1, 720, 1280)["aspect_ratio"] == "9:16"
    assert image_parameters({"image_endpoint": "p-image-t2i"}, "Scene", 1, 1000, 1001)["aspect_ratio"] == "1:1"


@pytest.mark.parametrize("endpoint", ["qwen-image-t2i", "seedream-v4-t2i"])
def test_z_image_restrictions_do_not_apply_to_other_models(endpoint):
    assert image_parameters({"image_endpoint": endpoint}, "Scene", 1, 2048, 2048)["size"] == "2048*2048"
    with pytest.raises(ValueError):
        image_parameters({"image_endpoint": "z-image-turbo"}, "Scene", 1, 2048, 2048)


@pytest.mark.parametrize("endpoint", IMAGE_MODELS)
def test_frame_generation_sends_selected_model_contract(endpoint, tmp_path, monkeypatch):
    from app.core.video_storyboard_comfyui import generate_storyboard_frame
    calls = []
    def execute(settings, role, values, target, **kwargs):
        calls.append((settings["runpod"]["image_endpoint"], role, values))
        return {}
    monkeypatch.setattr(rp, "execute", execute)
    width, height = (1440, 1024) if endpoint == "wan-2-6-t2i" else (1280, 720)
    result = generate_storyboard_frame({"id": "1", "generation_overrides": {"raw_prompt": "Exact prompt"}},
                                      {"base_seed": 42}, {"image_provider": "runpod", "runpod": {"image_endpoint": endpoint}, "image": {"width": width, "height": height}},
                                      tmp_path / "frame.png")
    assert calls == [(endpoint, "image", image_parameters({"image_endpoint": endpoint}, "Exact prompt", 42, width, height))]
    assert result["compiled_prompt"] == "Exact prompt"


@pytest.mark.parametrize("endpoint", ["seedream-v4-t2i", "https://api.runpod.ai/v2/seedream-v4-t2i/runsync"])
def test_seedream_live_size_format_regression(endpoint):
    import json
    payload = image_parameters({"image_endpoint": endpoint}, "Scene", 129748608251047, 1024, 1024)
    wire = json.loads(json.dumps({"input": payload}))
    assert wire["input"]["size"] == "1024*1024"
    assert wire["input"]["seed"] == 129748608251047 % (2 ** 32)
    assert set(wire["input"]) == {"prompt", "seed", "size"}


def test_seedream_invalid_resolution_does_not_submit(tmp_path, monkeypatch):
    from app.core.video_storyboard_comfyui import generate_storyboard_frame, VideoStoryboardImageError
    monkeypatch.setattr(rp, "execute", lambda *a, **k: pytest.fail("Must not submit a paid request"))
    with pytest.raises(VideoStoryboardImageError, match="supports only"):
        generate_storyboard_frame({"id": "1", "prompt": "Cat"}, {},
                                  {"image_provider": "runpod", "runpod": {"image_endpoint": "seedream-v4-t2i"}},
                                  tmp_path / "frame.png")


@pytest.mark.parametrize("size", [(1024, 1024), (1024, 768), (1440, 1024)])
def test_wan_image_sizes_and_seed(size):
    result = image_parameters({"image_endpoint": "wan-2-6-t2i"}, "Scene", 129748608251047, *size)
    assert result == {"prompt": "Scene", "seed": 129748608251047 % 2**32, "size": f"{size[0]}*{size[1]}"}
    assert "seedream-v4-t2i" not in IMAGE_MODELS


def test_wan_invalid_size():
    with pytest.raises(ValueError, match="WAN 2.6 T2I"):
        image_parameters({"image_endpoint": "wan-2-6-t2i"}, "Scene", 42, 1280, 720)
