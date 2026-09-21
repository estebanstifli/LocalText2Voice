import pytest
from PIL import Image

from app.core.storyboard_image_sizes import image_parameters, fit_frame


@pytest.mark.parametrize("model,size", [
    ("openai/gpt-image-2", "1280x720"),
    ("openai/gpt-image-2.5-flare", "1280x720"),
    ("openai/gpt-image-2.5-sunburst", "1280x720"),
    ("openai/gpt-image-1", "1536x1024"),
    ("openai/gpt-image-1-mini", "1536x1024"),
])
def test_provider_request_size(model, size):
    assert image_parameters(model, 1280, 720) == {"size": size}


def test_gemini_uses_aspect_ratio_and_resolution_tier():
    model = "gemini/gemini-3-pro-image-preview"
    assert image_parameters(model, 1280, 720) == {"imageConfig": {"aspectRatio": "16:9", "imageSize": "1K"}}
    assert image_parameters(model, 1920, 1080) == {"imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"}}
    from litellm.llms.gemini.image_generation.transformation import GoogleImageGenConfig
    mapped = GoogleImageGenConfig().map_openai_params(image_parameters(model, 1280, 720), {}, model.split("/", 1)[1], True)
    assert mapped["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "1K"}


def test_legacy_gpt_center_crop_removes_top_bottom_before_resize(tmp_path):
    path = tmp_path / "frame.png"
    image = Image.new("RGB", (1536, 1024), "red")
    image.paste("blue", (0, 80, 1536, 944))
    image.save(path)
    fit_frame(path, 1280, 720)
    with Image.open(path) as result:
        assert result.size == (1280, 720)
        assert result.getpixel((640, 20)) == (0, 0, 255)
        assert result.getpixel((640, 700)) == (0, 0, 255)


def test_irregular_gemini_output_is_fitted_and_native_output_is_untouched(tmp_path):
    path = tmp_path / "frame.png"
    Image.new("RGB", (1376, 768), "green").save(path)
    fit_frame(path, 1280, 720)
    with Image.open(path) as result:
        assert result.size == (1280, 720)
    original = path.read_bytes()
    fit_frame(path, 1280, 720)
    assert path.read_bytes() == original


@pytest.mark.parametrize("model", ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst"])
def test_installed_litellm_routes_new_models_to_gpt_image_adapter(model):
    from litellm.llms.openai.image_generation import get_openai_image_generation_config, GPTImageGenerationConfig
    assert isinstance(get_openai_image_generation_config(model), GPTImageGenerationConfig)
