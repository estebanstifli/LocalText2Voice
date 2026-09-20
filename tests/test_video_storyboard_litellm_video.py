import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.core import video_storyboard_video_litellm as veo
from app.core import video_storyboard_video_comfyui as router
from app.core.litellm_video_models import LITELLM_VIDEO_DEFAULTS, reference_mode


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = {"video_provider": "litellm", "litellm_video": {**LITELLM_VIDEO_DEFAULTS, "api_key": "fake-key"}}
    scene = {"scene_id": "3", "duration_seconds": 12, "video_path": "accepted-old.mp4"}
    calls = []
    responses = {"operation": {"name": "models/veo/operations/test", "done": True,
        "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://generativelanguage.googleapis.com/v1beta/files/test:download?alt=media"}}]}}}}

    def transport(request):
        calls.append(request)
        if request.method == "POST":
            if responses.get("post_error"):
                return httpx.Response(responses["post_error"], json={"error": {"message": "quota fake-key", "code": responses["post_error"]}})
            return httpx.Response(200, json={"name": "models/veo/operations/test", "done": False})
        if "download" in str(request.url):
            return httpx.Response(200, content=b"video-with-audio")
        if responses.get("poll_error"):
            return httpx.Response(responses["poll_error"], json={"error": {"message": "busy", "code": responses["poll_error"]}})
        return httpx.Response(200, json=responses["operation"])

    original = veo._client

    def client(instance, parameters):
        handler = original(instance, parameters)
        handler.close()
        handler._client = httpx.Client(transport=httpx.MockTransport(transport))
        return handler

    monkeypatch.setattr(veo, "_client", client)
    monkeypatch.setattr(router, "_probe_media_duration", lambda *args: 8)
    monkeypatch.setattr(router, "_retime_video", lambda *args, **kwargs: None)
    return settings, scene, calls, responses


def run(tmp_path, settings, scene, role="none"):
    return router.generate_storyboard_scene_video(scene, {}, settings, tmp_path / "candidate.mp4", prompt="A storm", frame_role=role)


def test_real_sdk_payload_all_references_audio_and_regeneration(tmp_path, setup):
    settings, scene, calls, _ = setup
    refs = []
    for index in range(4):
        path = tmp_path / f"ref-{index}.png"
        Image.new("RGB", (16, 16), (index, 20, 30)).save(path)
        refs.append({"path": str(path)})
    scene["generation_overrides"] = {"video_reference_images": refs}
    result = run(tmp_path, settings, scene)
    payload = json.loads(calls[0].content)
    assert len(payload["instances"][0]["referenceImages"]) == 4
    assert "referenceImages" not in payload["parameters"]
    assert payload["parameters"] == {"durationSeconds": 8, "resolution": "720p", "aspectRatio": "16:9"}
    assert Path(result["video_path"]).read_bytes() == b"video-with-audio"
    assert result["video_duration_seconds"] == 12
    run(tmp_path, settings, scene)
    assert sum(r.method == "POST" for r in calls) == 2
    assert len(list((tmp_path / "litellm_jobs").glob("*-history-*.json"))) == 1


def test_start_image_and_proxy_all_requests_stay_on_proxy(tmp_path, setup):
    settings, scene, calls, _ = setup
    settings["litellm_video"].update(base_url="https://proxy.example/v1", resolution="1080p", aspect_ratio="9:16")
    path = tmp_path / "frame.png"
    Image.new("RGB", (16, 16)).save(path)
    scene["image_path"] = str(path)
    run(tmp_path, settings, scene, "start")
    payload = json.loads(calls[0].content)
    assert "image" in payload["instances"][0]
    assert "referenceImages" not in payload["instances"][0]
    assert payload["parameters"]["aspectRatio"] == "9:16"
    assert all(r.url.host == "proxy.example" and r.url.path.startswith("/gemini/v1beta/") for r in calls)
    assert all(r.headers["x-goog-api-key"] == "fake-key" for r in calls)


def test_failed_poll_resumes_without_new_paid_submission(tmp_path, setup):
    settings, scene, calls, responses = setup
    responses["poll_error"] = 503
    with pytest.raises(router.VideoStoryboardVideoError):
        run(tmp_path, settings, scene)
    responses.pop("poll_error")
    run(tmp_path, settings, scene)
    assert sum(r.method == "POST" for r in calls) == 1


def test_new_accepted_clip_never_resumes_previous_pending_job(tmp_path, setup):
    settings, scene, calls, responses = setup
    responses["poll_error"] = 503
    with pytest.raises(router.VideoStoryboardVideoError):
        run(tmp_path, settings, scene)
    scene["video_path"] = "new-accepted.mp4"
    responses.pop("poll_error")
    run(tmp_path, settings, scene)
    assert sum(r.method == "POST" for r in calls) == 2


def test_saved_settings_keep_video_provider_and_normalize_fields(tmp_path):
    from app.core.settings_manager import SettingsManager
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"video_storyboard": {"video_provider": "litellm", "litellm_video": {
        "api_key": "test-key", "resolution": "bogus", "timeout_seconds": -3,
    }}}), encoding="utf-8")
    config = SettingsManager(path).settings["video_storyboard"]
    assert config["video_provider"] == "litellm"
    assert config["litellm_video"]["resolution"] == "720p"
    assert config["litellm_video"]["timeout_seconds"] >= 60
    assert config["litellm_video"]["api_key"] == "test-key"


@pytest.mark.parametrize("operation", [
    {"error": {"code": 400, "message": "Safety blocked fake-key"}},
    {"response": {"generateVideoResponse": {"raiMediaFilteredCount": 1, "raiMediaFilteredReasons": ["Safety blocked fake-key"]}}},
])
def test_provider_errors_are_visible_and_redacted(tmp_path, setup, operation):
    settings, scene, calls, responses = setup
    responses["operation"] = {"name": "models/veo/operations/test", "done": True, **operation}
    with pytest.raises(router.VideoStoryboardVideoError) as error:
        run(tmp_path, settings, scene)
    assert "fake-key" not in str(error.value)
    assert "Safety blocked" in str(error.value)
    assert not (tmp_path / "candidate.mp4").exists()
    record = next((tmp_path / "litellm_jobs").glob("*.json"))
    assert json.loads(record.read_text())["failed"]


def test_rejected_submission_can_retry_and_secrets_not_persisted(tmp_path, setup):
    settings, scene, calls, responses = setup
    responses["post_error"] = 429
    with pytest.raises(router.VideoStoryboardVideoError) as error:
        run(tmp_path, settings, scene)
    assert "fake-key" not in str(error.value)
    responses.pop("post_error")
    run(tmp_path, settings, scene)
    assert sum(r.method == "POST" for r in calls) == 2
    assert "fake-key" not in "".join(p.read_text() for p in (tmp_path / "litellm_jobs").glob("*.json"))


def test_lite_and_combined_modes_do_not_drop_references(tmp_path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"png")
    scene = {"image_path": str(image), "generation_overrides": {"video_reference_images": [{"path": str(image)}]}}
    config = deepcopy(LITELLM_VIDEO_DEFAULTS)
    config["model"] = "gemini/veo-3.1-lite-generate-preview"
    with pytest.raises(ValueError, match="require Veo 3.1"):
        reference_mode(scene, "none", config)
    with pytest.raises(ValueError, match="starting frame"):
        reference_mode(scene, "end", config)


def test_ambiguous_submission_does_not_retry_automatically(tmp_path, setup):
    settings, scene, calls, responses = setup
    responses["post_error"] = 503
    with pytest.raises(router.VideoStoryboardVideoError):
        run(tmp_path, settings, scene)
    responses.pop("post_error")
    with pytest.raises(router.VideoStoryboardVideoError, match="no confirmed operation ID"):
        run(tmp_path, settings, scene)
    assert sum(r.method == "POST" for r in calls) == 1


def test_download_redirect_does_not_forward_api_key(tmp_path):
    calls = []
    def transport(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "https://storage.googleapis.com/signed-video"})
        return httpx.Response(200, content=b"video")
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        veo._download(client, "https://generativelanguage.googleapis.com/v1beta/files/test", "fake-key", tmp_path / "video.mp4", lambda: None)
    assert calls[0].headers["x-goog-api-key"] == "fake-key"
    assert "x-goog-api-key" not in calls[1].headers


def test_settings_and_dialog_roundtrip(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
    from app.ui.video_storyboard_video_dialog import VideoStoryboardVideoDialog
    app = QApplication.instance() or QApplication([])
    import sys
    slot_errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: slot_errors.append(str(value)))
    tr = lambda key, default, **values: default.format(**values)
    widget = VideoStoryboardSettingsWidget(tr)
    try:
        widget._select_category("video")
        widget.video_provider_combo.setCurrentIndex(widget.video_provider_combo.findData("litellm"))
        changes = []
        widget.settingsChanged.connect(lambda: changes.append(widget.configuration()["litellm_video"].copy()))
        widget.litellm_video_settings.key.setText("private-key")
        assert changes[-1]["api_key"] == "private-key"
        widget.litellm_video_settings.resolution.setCurrentText("1080p")
        assert changes[-1]["resolution"] == "1080p"
        widget.litellm_video_settings.model.setCurrentIndex(1)
        assert "fast" not in changes[-1]["model"]
        widget.litellm_video_settings.url.setText("https://proxy.example")
        assert changes[-1]["base_url"] == "https://proxy.example"
        widget.litellm_video_settings.aspect.setCurrentText("9:16")
        assert changes[-1]["aspect_ratio"] == "9:16"
        widget.litellm_video_settings.timeout.setValue(1200)
        assert changes[-1]["timeout_seconds"] == 1200
        config = widget.configuration()
        widget.set_configuration(config)
        assert widget.configuration() == config
        assert not widget.litellm_video_settings.isHidden()
        assert widget.comfyui_video_fields.isHidden()
        assert widget.comfyui_video_profile_stack.isHidden()
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData("runpod"))
        assert not widget.engine_sections["video"].isHidden()
        widget.profile_combo.setCurrentIndex(widget.profile_combo.findData("local"))
        assert widget.configuration()["litellm_video"]["api_key"] == "private-key"
        dialog = VideoStoryboardVideoDialog(tr, {"scene_id": "1", "duration_seconds": 5,
            "_video_provider": "litellm", "_litellm_video_config": {"resolution": "1080p"}}, {})
        try:
            assert dialog.frame_role_combo.currentData() == "none"
            assert "LiteLLM" in dialog.model_info.text()
            assert "1080p" in dialog.model_info.text()
            assert not dialog.frame_role_combo.model().item(dialog.frame_role_combo.findData("end")).isEnabled()
        finally:
            dialog.deleteLater()
    finally:
        widget.deleteLater()
    assert slot_errors == []
