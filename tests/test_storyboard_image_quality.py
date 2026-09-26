import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.storyboard_image_quality import image_quality_options, image_quality_parameters
from app.core.video_storyboard_comfyui import _litellm_image_direct, _litellm_image_proxy
from app.core.storyboard_generation_references import litellm_reference_generation


@pytest.mark.parametrize("model", ["gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5", "gpt-image-2"])
def test_standard_gpt_quality(model):
    assert image_quality_options("openai/" + model) == ("auto", "low", "medium", "high")
    assert image_quality_parameters(model, "max") == {"quality": "auto"}


@pytest.mark.parametrize("model", ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst"])
@pytest.mark.parametrize("quality", ["auto", "low", "medium", "high", "xhigh", "max"])
def test_direct_and_proxy_send_selected_quality(model, quality):
    args = dict(model="openai/" + model, prompt="A forest", size="1024x1024", seed=1,
                api_key="", timeout=30, quality=quality)
    with patch("litellm.image_generation", return_value={"data": []}) as generate:
        _litellm_image_direct(**args)
    assert generate.call_args.kwargs["quality"] == quality
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"data": []}'
    with patch("urllib.request.urlopen", return_value=response) as send:
        _litellm_image_proxy("https://example.invalid/v1", **args)
    assert json.loads(send.call_args.args[0].data)["quality"] == quality


def test_other_models_do_not_receive_gpt_quality():
    assert image_quality_parameters("gemini/gemini-3-pro-image-preview", "high") == {}
    with patch("litellm.image_generation", return_value={"data": []}) as generate:
        _litellm_image_direct(model="openai/dall-e-3", prompt="A forest", size="1024x1024",
                              seed=1, api_key="", timeout=30, quality="high")
    assert "quality" not in generate.call_args.kwargs


@pytest.mark.parametrize("proxy", [False, True])
def test_reference_generation_preserves_quality_on_retry(tmp_path, proxy):
    from app.core.video_storyboard_image_edit import VideoStoryboardImageEditError
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"reference")
    config = {"model": "openai/gpt-image-2.5-flare", "quality": "max"}
    if proxy:
        config["base_url"] = "https://example.invalid/v1"
        with patch("app.core.video_storyboard_image_edit._multipart_json", side_effect=[
            VideoStoryboardImageEditError("HTTP 400 unsupported response_format"), {"data": []}
        ]) as send:
            litellm_reference_generation([{"path": str(ref)}], config, "A forest", 1024, 1024)
        assert all(call.args[1]["quality"] == "max" for call in send.call_args_list)
    else:
        with patch("litellm.image_edit", side_effect=[
            ValueError("unsupported response_format"), {"data": []}
        ]) as send:
            litellm_reference_generation([{"path": str(ref)}], config, "A forest", 1024, 1024)
        assert all(call.kwargs["quality"] == "max" for call in send.call_args_list)


def test_ui_quality_model_changes_and_settings_roundtrip(tmp_path):
    from PySide6.QtWidgets import QApplication
    from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
    from app.core.settings_manager import SettingsManager
    application = QApplication.instance() or QApplication([])
    widget = VideoStoryboardSettingsWidget(lambda key, default, **kw: default.format(**kw))
    try:
        combo = widget.litellm_image_model_combo
        quality = widget.litellm_image_quality_combo
        combo.setCurrentIndex(combo.findData("openai/gpt-image-2.5-flare"))
        assert not quality.isHidden()
        quality.setCurrentIndex(quality.findData("max"))
        config = widget.configuration()
        manager = SettingsManager(tmp_path / "settings.json")
        manager.settings["video_storyboard"] = config
        manager.save()
        restored = SettingsManager(tmp_path / "settings.json").settings["video_storyboard"]
        widget.set_configuration(restored)
        assert quality.currentData() == "max"
        combo.setCurrentIndex(combo.findData("openai/gpt-image-2"))
        assert quality.count() == 4
        assert quality.currentData() == "auto"
        widget._set_litellm_model(combo, widget.litellm_image_custom_model_edit,
                                 widget.litellm_image_custom_model_label, "openai/gpt-image-1.5")
        assert quality.count() == 4
        widget.litellm_image_custom_model_edit.setText("openai/dall-e-3")
        assert quality.isHidden()
    finally:
        widget.deleteLater()
        application.processEvents()
