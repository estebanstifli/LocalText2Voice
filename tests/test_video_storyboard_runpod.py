from copy import deepcopy
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app.core import video_storyboard_runpod as rp
from app.core.settings_manager import DEFAULT_SETTINGS, SettingsManager
from app.core.storyboard_profiles import switch_profile


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "jobs_directory", lambda: tmp_path / "jobs")
    monkeypatch.setattr(rp, "_asset_index", lambda: tmp_path / "assets")
    value = deepcopy(DEFAULT_SETTINGS["video_storyboard"])
    value.update(active_profile="runpod", image_provider="runpod", image_edit_provider="runpod", video_provider="runpod")
    value["runpod"]["api_key"] = "test-key"
    return value


def png(path):
    Image.new("RGB", (32, 32), "blue").save(path)


def test_profiles_preserve_connections_and_creative_settings():
    defaults = DEFAULT_SETTINGS["video_storyboard"]
    local = deepcopy(defaults)
    local["image"]["style_mode"] = "custom"
    local["comfyui"]["base_url"] = "http://localhost:8989"
    custom = switch_profile(local, "custom_comfyui", defaults)
    custom["comfyui"]["workflow_path"] = "workflow.json"
    custom["comfyui"]["bindings"]["prompt"] = "45.text"
    remote = switch_profile(custom, "runpod", defaults)
    assert remote["video_provider"] == remote["image_edit_provider"] == "runpod"
    assert remote["image"]["style_mode"] == "custom"
    recovered = switch_profile(remote, "custom_comfyui", defaults)
    assert recovered["comfyui"]["workflow_path"] == "workflow.json"
    assert recovered["comfyui"]["bindings"]["prompt"] == "45.text"
    assert switch_profile(recovered, "local", defaults)["comfyui"]["base_url"] == "http://localhost:8989"


@pytest.mark.parametrize("url_field", ["image_url", "result"])
def test_remote_jobs_resume_download_without_submitting_again(settings, tmp_path, url_field):
    target = tmp_path / "result.png"
    calls = []
    def request(config, endpoint, operation, payload=None):
        calls.append(operation)
        if operation == "run":
            assert payload["input"] == {"prompt": "scene", "seed": 5}
            return {"id": "job-1", "status": "IN_QUEUE"}
        return {"id": "job-1", "status": "COMPLETED", "output": {url_field: "https://image.runpod.ai/image.png", "cost": 0.005}}
    with patch.object(rp, "request", side_effect=request), patch.object(rp, "download", side_effect=rp.RunpodError("download failed")):
        with pytest.raises(rp.RunpodError, match="download failed"):
            rp.execute(settings, "image", {"prompt": "scene", "seed": 5}, target)
    # Different candidate file name still resumes the same job in this project.
    with patch.object(rp, "request", side_effect=AssertionError("should not resubmit")), patch.object(rp, "download", side_effect=lambda url, path, _: png(path)) as download:
        result = rp.execute(settings, "image", {"prompt": "scene", "seed": 5}, tmp_path / "another-candidate.png")
    assert download.call_args.args[0] == "https://image.runpod.ai/image.png"
    assert result["prompt_id"] == "job-1"
    assert result["cost_usd"] == 0.005
    assert calls == ["run", "status/job-1"]
    assert "test-key" not in next((tmp_path / "jobs").glob("*.json")).read_text()


@pytest.mark.parametrize("role,field", [("image", "image_url"), ("edit", "image_url"), ("video", "video_url")])
def test_public_output_url_formats_and_priority(role, field):
    final = "https://media.runpod.ai/final"
    fallback = "https://media.runpod.ai/result"
    assert rp.output_media_url({field: final, "result": fallback}, role) == final
    assert rp.output_media_url({"result": fallback, "cost": 0.005}, role) == fallback
    assert rp.output_media_url({field: None, "result": fallback}, role) == fallback


@pytest.mark.parametrize("output", [
    {"result": "http://media.runpod.ai/result.png"},
    {"result": "https:///result.png"},
    {"result": {"preview_url": "https://media.runpod.ai/preview.png"}},
    {"video_url": "https://media.runpod.ai/video.mp4"},
    {"result": None, "cost": 0.005},
])
def test_unusable_output_reports_fields_without_exposing_media_urls(output):
    with pytest.raises(rp.RunpodError, match="saved job can be recovered") as error:
        rp.output_media_url(output, "image")
    assert "Output fields:" in str(error.value)
    assert "media.runpod.ai" not in str(error.value)


def test_unknown_submit_is_not_automatically_repeated(settings, tmp_path):
    with patch.object(rp, "request", side_effect=rp.RunpodError("connection lost")) as request:
        with pytest.raises(rp.RunpodError, match="connection lost"):
            rp.execute(settings, "image", {"prompt": "scene"}, tmp_path / "image.png")
        with pytest.raises(rp.RunpodError, match="outcome is unknown"):
            rp.execute(settings, "image", {"prompt": "scene"}, tmp_path / "image.png")
    assert request.call_count == 1


def test_known_rejection_can_be_retried(settings, tmp_path):
    error = rp.RunpodError("invalid key")
    error.status_code = 401
    with patch.object(rp, "request", side_effect=error) as request:
        for _ in range(2):
            with pytest.raises(rp.RunpodError, match="invalid key"):
                rp.execute(settings, "image", {"prompt": "scene"}, tmp_path / "image.png")
    assert request.call_count == 2


def test_cancel_stops_the_remote_job(settings, tmp_path):
    calls = []
    def request(config, endpoint, operation, payload=None):
        calls.append(operation)
        return {"id": "job-cancel", "status": "IN_QUEUE"}
    with patch.object(rp, "request", side_effect=request):
        with pytest.raises(rp.RunpodError, match="cancelled"):
            rp.execute(settings, "image", {"prompt": "scene"}, tmp_path / "image.png", cancelled=lambda: bool(calls))
    assert calls == ["run", "cancel/job-cancel"]


def test_existing_runpod_asset_reused_by_content_hash(settings, tmp_path):
    original = tmp_path / "image.png"
    copied = tmp_path / "copy.png"
    png(original)
    with Image.open(original) as pixels:
        pixels.save(copied, compress_level=0)
    assert copied.read_bytes() != original.read_bytes()
    rp.remember_asset(original, "https://image.runpod.ai/image.png")
    assert rp.source_url(copied, settings["runpod"]) == "https://image.runpod.ai/image.png"
    png(tmp_path / "other.png")
    settings["runpod"]["reference_storage"] = "disabled"
    with patch.object(rp, "_read", return_value={}):
        with pytest.raises(rp.RunpodError, match="temporary URL"):
            rp.source_url(original, settings["runpod"])


def test_model_payloads_and_reference_urls(settings, tmp_path):
    from app.core.video_storyboard_comfyui import generate_storyboard_frame
    from app.core.video_storyboard_image_edit import generate_edited_storyboard_image
    image = tmp_path / "image.png"
    png(image)
    refs = [tmp_path / f"reference-{index}.png" for index in range(3)]
    for ref in refs:
        Image.new("RGB", (1280, 720), "blue").save(ref)
    def execute(settings, role, payload, target, **kwargs):
        payload = payload() if callable(payload) else payload
        if role == "image":
            assert payload["size"] == "1280*720"
            assert payload["seed"] == 5
        else:
            assert payload["images"] == ["https://image.runpod.ai/reference.png"] * 3
            assert payload["size"] == "1280*720"
        return {"prompt_id": "job", "provider": "runpod"}
    with patch.object(rp, "execute", side_effect=execute), patch.object(rp, "source_url", return_value="https://image.runpod.ai/reference.png"):
        generate_storyboard_frame({"scene_id": "1", "generation_overrides": {"raw_prompt": "scene"}}, {"base_seed": 5}, settings, image)
        result = generate_edited_storyboard_image([str(ref) for ref in refs], "edit it", settings, image, seed=5)
        assert result["seed_applied"] is True


@pytest.mark.parametrize("saved_size,wire_size,cost", [("1280*720", "720p", 1.0), ("1920*1080", "1080p", 1.5)])
def test_wan26_payload_and_existing_postprocessing(settings, tmp_path, saved_size, wire_size, cost):
    from app.core import video_storyboard_video_comfyui as video
    settings["runpod"]["video_size"] = saved_size
    image = tmp_path / "image.png"
    png(image)
    def execute(settings, role, payload, target, **kwargs):
        values = payload()
        assert values["duration"] == 10
        assert values["size"] == wire_size
        assert kwargs["identity"]["size"] == wire_size
        assert values["image"] == "https://image.runpod.ai/image.png"
        assert values["shot_type"] == "single"
        target.write_bytes(b"video")
        return {"prompt_id": "video-job"}
    with patch.object(rp, "execute", side_effect=execute), patch.object(rp, "source_url", return_value="https://image.runpod.ai/image.png"), patch.object(video, "_probe_media_duration", return_value=10), patch.object(video, "_retime_video") as retime:
        result = video.generate_storyboard_scene_video({"image_path": str(image), "duration_seconds": 8}, {"base_seed": 5}, settings, tmp_path / "video.mp4", prompt="motion")
    assert result["generated_duration_seconds"] == 10
    assert result["video_duration_seconds"] == 8
    assert retime.call_args.args[1] == 8
    assert rp.estimate_cost(settings, durations=[8]) == cost


@pytest.mark.parametrize("immediate", [True, False])
def test_failed_job_surfaces_server_validation_error_on_first_attempt_and_retry(settings, tmp_path, immediate, caplog):
    # The real WAN 2.6 response: HTTP submission succeeds, job validation fails.
    detail = 'Error submitting task: 400, {"code":400,"message":"Invalid request body: field resolution must be one of [720p, 1080p], got string 1280*720"}'
    failed = {"id": "failed-video", "status": "FAILED", "error": detail, "output": {"status": "error"}}
    responses = [failed] if immediate else [{"id": "failed-video", "status": "IN_QUEUE"}, failed]
    target = tmp_path / "video.mp4"
    with patch.object(rp, "request", side_effect=responses), patch.object(rp, "download") as download:
        with pytest.raises(rp.RunpodError, match="resolution must be one of") as error:
            rp.execute(settings, "video", {"prompt": "motion", "size": "1280*720"}, target)
    assert error.value.response_detail == detail
    assert detail in caplog.text
    download.assert_not_called()
    with patch.object(rp, "request", side_effect=AssertionError("Failed jobs must not resubmit automatically")):
        with pytest.raises(rp.RunpodError, match="resolution must be one of"):
            rp.execute(settings, "video", {"prompt": "motion", "size": "1280*720"}, target)


@pytest.mark.parametrize("error_fields", [
    {"error": 'invalid input test-key rpa_other_secret Bearer another-secret https://media.example/image?signature=private-signature'},
    {"error": {"message": "invalid input test-key", "input": "PRIVATE_INPUT"}},
    {"output": {"error": "invalid input test-key", "input": "PRIVATE_INPUT"}},
])
def test_job_failure_details_are_redacted(settings, error_fields, caplog):
    record = {"id": "failed-job", "endpoint": "wan-2-6-i2v", "status": "FAILED", "input": "PRIVATE_INPUT", **error_fields}
    error = rp.job_error(record, settings["runpod"])
    visible = str(error) + caplog.text
    assert "invalid input" in visible
    for secret in ("test-key", "rpa_other_secret", "another-secret", "private-signature", "PRIVATE_INPUT"):
        assert secret not in visible


def test_jobs_dialog_status_refresh_includes_failure_detail(settings):
    from app.ui.storyboard_runpod_jobs import _JobAction
    action = _JobAction(settings, Path("job.json"), "check")
    messages = []
    action.finished.connect(messages.append)
    with patch("app.ui.storyboard_runpod_jobs.manage_job", return_value={"id": "failed-job", "status": "FAILED", "error": "Invalid resolution: use 720p"}):
        action.run()
    assert len(messages) == 1
    assert "Invalid resolution: use 720p" in messages[0]


def test_media_download_does_not_send_api_key(settings, tmp_path):
    contents = io.BytesIO()
    Image.new("RGB", (8, 8)).save(contents, format="PNG")
    def urlopen(req, **kwargs):
        assert req.get_header("Authorization") is None
        return io.BytesIO(contents.getvalue())
    with patch("urllib.request.urlopen", side_effect=urlopen):
        rp.download("https://image.runpod.ai/result.png", tmp_path / "download.png")


def test_s3_upload_uses_separate_credentials_and_signed_private_url(settings, tmp_path):
    from unittest.mock import Mock
    from app.core.storyboard_credentials import protect
    config = settings["runpod"]
    config.update(s3_endpoint="https://s3.example.com", s3_bucket="references", s3_region="region-1", s3_access_key="s3-access", s3_secret_encrypted=protect("s3-secret"))
    reference = tmp_path / "local.png"
    png(reference)
    client = Mock()
    client.generate_presigned_url.return_value = "https://s3.example.com/reference?signature=temporary"
    with patch("boto3.client", return_value=client) as factory:
        url = rp.source_url(reference, config)
    assert factory.call_args.kwargs["aws_secret_access_key"] == "s3-secret"
    assert "test-key" not in str(factory.call_args)
    assert client.upload_file.call_args.kwargs["ExtraArgs"] == {"ContentType": "image/png"}
    assert client.generate_presigned_url.call_args.args[0] == "get_object"
    assert client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 86400
    assert rp.source_url(reference, config) == url


def test_selected_workflow_output_is_used():
    from app.core.video_storyboard_comfyui import _find_output_image, VideoStoryboardImageError
    from app.core.video_storyboard_video_comfyui import _find_output_video
    history = {"outputs": {"preview": {"images": [{"filename": "preview.png"}]}, "final": {"images": [{"filename": "final.png"}]}}}
    assert _find_output_image(history, "final")["filename"] == "final.png"
    with pytest.raises(VideoStoryboardImageError):
        _find_output_image(history, "missing")
    assert _find_output_video({"outputs": {"1": {"filename": "preview.mp4"}, "9": {"filename": "final.mp4"}}}, "9")["filename"] == "final.mp4"


def test_credentials_are_encrypted_and_survive_settings_roundtrip(tmp_path):
    from app.core.storyboard_credentials import protect, reveal
    import os
    if os.name != "nt":
        pytest.skip("Windows DPAPI")
    encrypted = protect("example-secret")
    assert "example-secret" not in encrypted
    assert reveal(encrypted) == "example-secret"
    manager = SettingsManager(tmp_path / "config.json")
    config = deepcopy(manager.settings)
    config["video_storyboard"].update(active_profile="runpod", video_provider="runpod", image_provider="runpod", image_edit_provider="runpod")
    config["video_storyboard"]["runpod"]["api_key_encrypted"] = encrypted
    manager.save(config)
    stored = SettingsManager(tmp_path / "config.json").get("video_storyboard")
    assert stored["image_provider"] == "runpod"
    assert stored["runpod"]["video_endpoint"] == "wan-2-6-i2v"
    assert reveal(stored["runpod"]["api_key_encrypted"]) == "example-secret"
    assert "example-secret" not in (tmp_path / "config.json").read_text(encoding="utf-8")
