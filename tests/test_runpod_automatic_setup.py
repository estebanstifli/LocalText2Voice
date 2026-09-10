import json
from copy import deepcopy
from unittest.mock import patch

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core import installation_identity as identity
from app.core import storyboard_temporary_storage as storage
from app.core import video_storyboard_runpod as rp
from app.core.settings_manager import DEFAULT_SETTINGS
from app.core.runpod_video_models import VIDEO_MODELS, video_parameters, RUNPOD_SIGNUP_URL
from app.ui.storyboard_storage_consent import ensure_storage_consent


@pytest.fixture
def local_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "identity_path", lambda: tmp_path / "identity.json")
    return tmp_path / "identity.json"


def test_installation_token_is_random_persistent_and_encrypted(local_identity):
    first = identity.installation_token()
    assert len(first) == 64
    assert first == identity.installation_token()
    assert first not in local_identity.read_text()
    assert storage.access_code({}) == first


def test_consent_precedes_registration_and_is_scoped_to_service(local_identity):
    config = {"temporary_storage_url": "https://storage.example"}
    with patch.object(storage, "request") as request:
        with pytest.raises(storage.TemporaryStorageError, match="Accept temporary"):
            storage.register(config)
        request.assert_not_called()
        identity.accept_storage(storage.service_url(config))
        storage.register(config)
        assert request.call_args.args[1] == "/v1/register"
        assert json.loads(request.call_args.kwargs["data"]) == {"consent_version": 1}
    assert not identity.storage_consent("https://other.example")
    assert not identity.storage_consent("https://storage.example", version=2)


def test_consent_dialog_denial_and_one_time_acceptance(local_identity):
    app = QApplication.instance() or QApplication([])
    config = {"reference_storage": "auto"}
    tr = lambda key, fallback, **values: fallback.format(**values)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        assert not ensure_storage_consent(config, None, tr)
    assert not identity.storage_consent(storage.DEFAULT_SERVICE_URL)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question:
        assert ensure_storage_consent(config, None, tr)
        assert ensure_storage_consent(config, None, tr)
        question.assert_called_once()
    with patch.object(QMessageBox, "question") as question:
        assert ensure_storage_consent({"reference_storage": "s3"}, None, tr)
        assert ensure_storage_consent({"reference_storage": "disabled"}, None, tr)
        question.assert_not_called()


@pytest.mark.parametrize("endpoint", ["wan-2-2-i2v-720", "wan-2-1-i2v-720"])
def test_alternative_video_payload_duration_price_and_auth(endpoint):
    config = {"video_endpoint": endpoint, "video_size": "1280*720"}
    payload = video_parameters(config, "motion", 7.2, 12)
    assert payload["duration"] == (10 if "2-1" in endpoint else 8)
    assert payload["size"] == "1280*720"
    assert payload["num_inference_steps"] == 30
    assert "shot_type" not in payload and "enable_prompt_expansion" not in payload
    assert rp.estimate_cost({"runpod": config}, durations=[7.2]) == (0.60 if "2-1" in endpoint else 0.48)
    assert endpoint in rp.PUBLIC_ENDPOINTS
    with pytest.raises(ValueError, match="720p only"):
        video_parameters({**config, "video_size": "1920*1080"}, "motion", 5, 12)


def test_model_selector_prices_resolution_roundtrip_and_affiliate_link():
    from app.ui.storyboard_runpod_settings import RunpodSettingsWidget
    app = QApplication.instance() or QApplication([])
    widget = RunpodSettingsWidget(lambda key, fallback, **values: fallback.format(**values))
    widget.set_configuration({"video_size": "1920*1080"})
    assert widget.size.currentData() == "1920*1080"
    widget.video_model.setCurrentIndex(widget.video_model.findData("wan-2-2-i2v-720"))
    assert widget.configuration()["video_endpoint"] == "wan-2-2-i2v-720"
    assert widget.size.count() == 1 and widget.size.currentData() == "1280*720"
    assert "$0.30" in widget.cost_label.text()
    assert RUNPOD_SIGNUP_URL in widget.signup.text()
    assert widget.layout().itemAt(0).layout().getWidgetPosition(widget.signup)[0] == 0
    assert widget.preserve_size.isChecked()
    widget.set_configuration(widget.configuration())
    assert widget.video_model.currentData() == "wan-2-2-i2v-720"
    widget.deleteLater()


@pytest.mark.parametrize("preserve,expected", [(True, "1280*720"), (False, "1536*1080")])
def test_qwen_preserves_reference_dimensions_unless_explicitly_overridden(tmp_path, preserve, expected):
    from app.core.video_storyboard_image_edit import generate_edited_storyboard_image
    source = tmp_path / "reference.png"
    Image.new("RGB", (1280, 720)).save(source)
    settings = deepcopy(DEFAULT_SETTINGS["video_storyboard"])
    settings["image_edit_provider"] = "runpod"
    settings["runpod"]["edit_preserve_size"] = preserve
    def execute(settings, role, payload, target, **kwargs):
        assert payload()["size"] == expected
        assert kwargs["identity"]["size"] == expected
        return {}
    with patch.object(rp, "execute", side_effect=execute), patch.object(rp, "source_url", return_value="https://image.runpod.ai/reference.png"):
        generate_edited_storyboard_image([str(source)], "edit", settings, tmp_path / "edited.png")


@pytest.mark.parametrize("endpoint,seconds,expected", [
    ("wan-2-2-i2v-720", 6, 8), ("wan-2-2-i2v-720", 8, 8),
    ("wan-2-2-i2v-720", 8.01, 10), ("wan-2-2-i2v-720", 11, 15),
    ("wan-2-1-i2v-720", 7, 10), ("wan-2-1-i2v-720", 15, 10),
    ("wan-2-6-i2v", 6, 10), ("wan-2-6-i2v", 16, 15),
])
def test_duration_steps_follow_each_live_model(endpoint, seconds, expected):
    assert video_parameters({"video_endpoint": endpoint}, "motion", seconds, 0)["duration"] == expected


def test_runpod_camera_selects_lora_without_mutating_normal_edit(tmp_path, monkeypatch):
    from app.core.video_storyboard_image_edit import generate_edited_storyboard_image, prepare_image_edit_runtime
    from app.core.video_storyboard_camera import RUNPOD_CAMERA_ENDPOINT, RUNPOD_CAMERA_LORA
    source = tmp_path / "source.png"
    Image.new("RGB", (1280, 720)).save(source)
    settings = {"image_edit_provider": "runpod", "runpod": {"edit_endpoint": "qwen-image-edit-2511"}}
    camera = {"enabled": True, "rotate_deg": 45, "move_forward": 5}
    monkeypatch.setattr(rp, "prepare", lambda settings, role: settings["runpod"]["edit_endpoint"])
    assert prepare_image_edit_runtime(settings, camera=camera) == RUNPOD_CAMERA_ENDPOINT
    calls = []
    def execute(settings, role, payload, target, **kwargs):
        calls.append((settings["runpod"]["edit_endpoint"], payload(), kwargs["identity"]))
        return {}
    monkeypatch.setattr(rp, "execute", execute)
    monkeypatch.setattr(rp, "source_url", lambda *args: "https://example.test/image.png")
    generate_edited_storyboard_image([str(source)], "", settings, tmp_path / "edit.png", camera=camera)
    endpoint, payload, identity = calls[-1]
    assert endpoint == RUNPOD_CAMERA_ENDPOINT
    assert payload["prompt"] == "<sks> front-left quarter view eye-level shot medium shot"
    assert payload["size"] == "1280*720"
    assert payload["loras"] == identity["loras"] == [{"path": RUNPOD_CAMERA_LORA, "scale": 0.9}]
    generate_edited_storyboard_image([str(source)], payload["prompt"], settings, tmp_path / "edit.png", camera=camera)
    assert calls[-1][1]["prompt"].count("<sks>") == 1
    generate_edited_storyboard_image([str(source)], "Sunny", settings, tmp_path / "ordinary.png")
    assert calls[-1][0] == settings["runpod"]["edit_endpoint"] == "qwen-image-edit-2511"
    assert "loras" not in calls[-1][1] and "loras" not in calls[-1][2]


def test_runpod_camera_ui_is_enabled_and_displays_price(tmp_path):
    from app.ui.video_storyboard_image_edit_dialog import VideoStoryboardImageEditDialog
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.png"
    Image.new("RGB", (1280, 720)).save(source)
    dialog = VideoStoryboardImageEditDialog(lambda key, fallback, **v: fallback.format(**v), "001", str(source), configuration={"image_edit_provider": "runpod"})
    assert dialog.camera_controls.isEnabled()
    dialog.camera_controls.enable_check.setChecked(True)
    assert "0.025" in dialog.camera_cost_label.text()
    assert "<sks> front view eye-level shot medium shot" in dialog._compiled_prompt()
    assert dialog.generate_ai_button.isEnabled()
    dialog._set_edit_busy(True)
    assert not dialog.camera_controls.isEnabled()
    dialog._set_edit_busy(False)
    assert dialog.camera_controls.isEnabled()
    dialog.close()
