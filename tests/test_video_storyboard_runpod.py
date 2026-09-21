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


def test_explicit_video_regeneration_and_download_recovery(settings, tmp_path):
    calls = []
    def request(config, endpoint, operation, payload=None):
        calls.append(operation)
        return {'id': f'job-{len(calls)}', 'status': 'COMPLETED',
                'output': {'video_url': 'https://video.runpod.ai/result.mp4'}}
    target = tmp_path / 'candidate.mp4'
    with patch.object(rp, 'request', side_effect=request), patch.object(rp, 'download') as download:
        first = rp.execute(settings, 'video', {'prompt': 'same'}, target, regenerate=True)
        rp.consume_result(first)
        download.side_effect = rp.RunpodError('Interrupted download')
        with pytest.raises(rp.RunpodError, match='Interrupted'):
            rp.execute(settings, 'video', {'prompt': 'same'}, target, regenerate=True)
        assert calls == ['run', 'run']
        download.side_effect = None
        recovered = rp.execute(settings, 'video', {'prompt': 'same'}, target, regenerate=True)
        assert recovered['prompt_id'] == 'job-2'
        assert calls == ['run', 'run']
        rp.consume_result(recovered)
        third = rp.execute(settings, 'video', {'prompt': 'same'}, target, regenerate=True)
        assert third['prompt_id'] == 'job-3'


def test_legacy_accepted_video_starts_new_job(settings, tmp_path):
    with patch.object(rp, 'request', return_value={'id': 'old', 'status': 'COMPLETED', 'output': {'video_url': 'https://video.runpod.ai/old.mp4'}}), patch.object(rp, 'download'):
        old = rp.execute(settings, 'video', {'prompt': 'same'}, tmp_path / 'old.mp4')
    path = Path(old['job_record'])
    record = json.loads(path.read_text())
    record.pop('consumed')
    path.write_text(json.dumps(record))
    with patch.object(rp, 'request', return_value={'id': 'new', 'status': 'COMPLETED', 'output': {'video_url': 'https://video.runpod.ai/new.mp4'}}) as submit, patch.object(rp, 'download'):
        new = rp.execute(settings, 'video', {'prompt': 'same'}, tmp_path / 'new.mp4', regenerate=True, legacy_result_used=True)
    assert new['prompt_id'] == 'new'
    submit.assert_called_once()


def test_accepted_video_does_not_poll_legacy_queued_job(settings, tmp_path):
    from app.core import video_storyboard_video_comfyui as video
    from app.core.runpod_video_models import video_parameters
    image = tmp_path / 'image.png'
    png(image)
    identity = {**video_parameters(rp.configuration(settings), 'motion', 8, 5),
                'image': rp.file_digest(image), 'frame_role': 'start'}
    with patch.object(rp, 'request', side_effect=[{'id': 'old-queue', 'status': 'IN_QUEUE'}, rp.RunpodError('Connection lost')]):
        with pytest.raises(rp.RunpodError, match='Connection lost'):
            rp.execute(settings, 'video', {'prompt': 'motion'}, tmp_path / 'old.mp4', identity=identity)
    accepted = tmp_path / 'accepted.mp4'
    accepted.write_bytes(b'accepted video')
    scene = {'scene_id': '003', 'duration_seconds': 8, 'image_path': str(image), 'video_path': str(accepted)}
    def request(config, endpoint, operation, payload=None):
        assert operation == 'run', 'Regeneration must never query the old queued job'
        return {'id': 'new-job', 'status': 'COMPLETED', 'output': {'video_url': 'https://video.runpod.ai/new.mp4'}}
    with patch.object(rp, 'request', side_effect=request) as submit, patch.object(rp, 'source_url', return_value='https://image.runpod.ai/ref.png'), patch.object(rp, 'download', side_effect=lambda url, path, cancelled: path.write_bytes(b'video')), patch.object(video, '_probe_media_duration', return_value=10), patch.object(video, '_retime_video'):
        result = video.generate_storyboard_scene_video(scene, {'base_seed': 5}, settings, tmp_path / 'candidate.mp4', prompt='motion')
    submit.assert_called_once()
    record = json.loads(Path(result['job_record']).read_text())
    assert record['input_snapshot']['accepted_video'] == str(accepted)
    assert record['input_snapshot']['scene_id'] == '003'
    assert accepted.read_bytes() == b'accepted video'


@pytest.mark.parametrize('http_status,detail,restart', [(404, 'job not found', True), (404, 'endpoint not found', False), (401, 'unauthorized', False), (503, 'unavailable', False)])
def test_expired_pending_video_restarts_once_but_other_errors_do_not(settings, tmp_path, http_status, detail, restart):
    target = tmp_path / 'candidate.mp4'
    with patch.object(rp, 'request', side_effect=[{'id': 'pending', 'status': 'IN_QUEUE'}, rp.RunpodError('Connection lost')]):
        with pytest.raises(rp.RunpodError):
            rp.execute(settings, 'video', {'prompt': 'same'}, target, regenerate=True)
    error = rp.RunpodError(detail)
    error.status_code, error.response_detail = http_status, detail
    responses = [error, {'id': 'replacement', 'status': 'COMPLETED', 'output': {'video_url': 'https://video.runpod.ai/new.mp4'}}]
    with patch.object(rp, 'request', side_effect=responses) as request, patch.object(rp, 'download'):
        if restart:
            result = rp.execute(settings, 'video', {'prompt': 'same'}, target, regenerate=True)
            assert result['prompt_id'] == 'replacement'
            assert [call.args[2] for call in request.call_args_list] == ['status/pending', 'run']
        else:
            with pytest.raises(rp.RunpodError):
                rp.execute(settings, 'video', {'prompt': 'same'}, target, regenerate=True)
            assert [call.args[2] for call in request.call_args_list] == ['status/pending']


def test_new_video_job_404_does_not_loop_submissions(settings, tmp_path):
    error = rp.RunpodError('job not found')
    error.status_code, error.response_detail = 404, 'job not found'
    with patch.object(rp, 'request', side_effect=[{'id': 'just-submitted', 'status': 'IN_QUEUE'}, error]) as request:
        with pytest.raises(rp.RunpodError):
            rp.execute(settings, 'video', {'prompt': 'same'}, tmp_path / 'candidate.mp4', regenerate=True)
    assert [call.args[2] for call in request.call_args_list] == ['run', 'status/just-submitted']


def test_retry_of_current_regeneration_keeps_pending_job(settings, tmp_path):
    from app.core import video_storyboard_video_comfyui as video
    image = tmp_path / 'frame.png'
    png(image)
    scene = {'scene_id': '003', 'duration_seconds': 8, 'image_path': str(image),
             'video_path': str(tmp_path / 'accepted.mp4')}
    with patch.object(rp, 'source_url', return_value='https://image.runpod.ai/ref.png'), patch.object(rp, 'request', side_effect=[{'id': 'current-regeneration', 'status': 'IN_QUEUE'}, rp.RunpodError('Connection lost')]):
        with pytest.raises(video.VideoStoryboardVideoError, match='Connection lost'):
            video.generate_storyboard_scene_video(scene, {}, settings, tmp_path / 'attempt-1.mp4', prompt='storm')
    with patch.object(rp, 'request', return_value={'id': 'current-regeneration', 'status': 'COMPLETED', 'output': {'video_url': 'https://video.runpod.ai/new.mp4'}}) as request, patch.object(rp, 'download', side_effect=lambda url, path, cancelled: path.write_bytes(b'video')), patch.object(video, '_probe_media_duration', return_value=10), patch.object(video, '_retime_video'):
        result = video.generate_storyboard_scene_video(scene, {}, settings, tmp_path / 'attempt-2.mp4', prompt='storm')
    assert result['prompt_id'] == 'current-regeneration'
    assert [call.args[2] for call in request.call_args_list] == ['status/current-regeneration']


@pytest.mark.parametrize('endpoint,role,reference_count', [('kling-video-o1-r2v','none',3)])
def test_text_only_and_multi_reference_video_payloads(settings, tmp_path, endpoint, role, reference_count):
    from app.core import video_storyboard_video_comfyui as video
    settings['runpod']['video_endpoint'] = endpoint
    refs = []
    for i in range(reference_count):
        path = tmp_path / f'ref-{i}.png'
        png(path)
        refs.append({'path': str(path), 'label': f'Ref {i}'})
    scene = {'duration_seconds': 8, 'generation_overrides': {'video_reference_images': refs}}
    def execute(config, kind, payload, target, **kwargs):
        values = payload()
        assert kwargs['regenerate'] is True
        if reference_count:
            assert config['runpod']['video_endpoint'] == 'kling-video-o1-r2v'
            assert len(values['images']) == 3
            assert 'image' not in values and 'size' not in values
            assert values['aspect_ratio'] == '16:9'
        else:
            assert config['runpod']['video_endpoint'] == 'wan-2-6-t2v'
            assert 'image' not in values and 'images' not in values
        target.write_bytes(b'video')
        return {'prompt_id': 'job'}
    with patch.object(rp, 'execute', side_effect=execute), patch.object(rp, 'source_url', return_value='https://image.runpod.ai/ref.png') as upload, patch.object(video, '_probe_media_duration', return_value=8), patch.object(video, '_retime_video'):
        result = video.generate_storyboard_scene_video(scene, {}, settings, tmp_path / 'out.mp4', prompt='storm', frame_role=role)
    assert upload.call_count == reference_count
    assert result['video_frame_role'] == 'none'


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


def test_removed_runpod_text_only_model_does_not_submit(settings, tmp_path):
    from app.core import video_storyboard_video_comfyui as video
    from app.core.runpod_video_models import VIDEO_MODELS
    assert 'wan-2-6-t2v' not in VIDEO_MODELS
    with patch.object(rp, 'execute') as execute:
        with pytest.raises(video.VideoStoryboardVideoError, match='Text-to-video is no longer'):
            video.generate_storyboard_scene_video({'duration_seconds': 8}, {}, settings, tmp_path / 'out.mp4', prompt='storm', frame_role='none')
        execute.assert_not_called()
