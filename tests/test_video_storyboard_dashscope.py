import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import httpx
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.core import video_storyboard_video_dashscope as wan
from app.core import video_storyboard_video_comfyui as router
from app.core import video_provider_jobs as jobs


@pytest.fixture
def setup(tmp_path, monkeypatch):
    image = tmp_path / "frame.png"
    Image.new("RGB", (256, 256)).save(image)
    settings = {"video_provider": "dashscope", "dashscope_video": {**wan.DASHSCOPE_DEFAULTS, "api_key": "fake-secret"}}
    scene = {"scene_id": "001", "image_path": str(image), "duration_seconds": 9.5}
    calls, replies = [], {}
    def transport(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(replies.get("post_status", 200), json=replies.get("post", {"output": {"task_id": "job-123"}}))
        if request.url.host == "result.example":
            return httpx.Response(200, content=b"silent-video")
        return httpx.Response(replies.get("poll_status", 200), json=replies.get("poll", {"output": {"task_status": "SUCCEEDED", "video_url": "https://result.example/clip.mp4"}}))
    original = httpx.Client
    monkeypatch.setattr(jobs.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    monkeypatch.setattr(router, "_probe_media_duration", lambda *a: 10)
    monkeypatch.setattr(router, "_retime_video", lambda *a, **kw: None)
    return settings, scene, calls, replies


def run(tmp_path, setup, **kwargs):
    settings, scene, *_ = setup
    return router.generate_storyboard_scene_video(scene, {}, settings, tmp_path / "clip.mp4", prompt="A moving camera", frame_role="start", **kwargs)


def test_silent_payload_download_and_fresh_regeneration(tmp_path, setup):
    settings, scene, calls, replies = setup
    settings["dashscope_video"]["resolution"] = "1080P"
    assert router.prepare_video_runtime(settings)["provider"] == "dashscope"
    result = run(tmp_path, setup)
    payload = json.loads(calls[0].content)
    assert payload["model"] == wan.WAN_MODEL
    assert payload["parameters"]["audio"] is False
    assert payload["parameters"]["duration"] == 10
    assert payload["parameters"]["resolution"] == "1080P"
    assert payload["input"]["img_url"].startswith("data:image/png;base64,")
    assert calls[0].url.path == "/api/v1/services/aigc/video-generation/video-synthesis"
    assert calls[0].headers["X-DashScope-Async"] == "enable"
    assert calls[1].headers["Authorization"] == "Bearer fake-secret"
    assert "authorization" not in calls[2].headers
    assert Path(result["video_path"]).read_bytes() == b"silent-video"
    assert result["video_duration_seconds"] == 9.5
    assert all("fake-secret" not in p.read_text() for p in (tmp_path / "dashscope_jobs").glob("*.json"))
    run(tmp_path, setup)
    assert sum(r.method == "POST" for r in calls) == 2


def test_failed_poll_resumes_same_job(tmp_path, setup):
    _, _, calls, replies = setup
    replies.update(poll_status=503, poll={"code": "Unavailable", "message": "fake-secret"})
    with pytest.raises(router.VideoStoryboardVideoError) as error:
        run(tmp_path, setup)
    assert "fake-secret" not in str(error.value)
    replies.clear()
    run(tmp_path, setup)
    assert sum(r.method == "POST" for r in calls) == 1


def test_shared_runner_resumes_legacy_wan_recovery_record(tmp_path, setup):
    import hashlib
    settings, scene, calls, _ = setup
    identity = {"root": settings["dashscope_video"]["base_url"], "model": wan.WAN_MODEL,
                "parameters": {"resolution": "720P", "duration": 10, "prompt_extend": False,
                               "watermark": False, "audio": False, "shot_type": "single"},
                "prompt": "A moving camera", "scene": "001",
                "image": hashlib.sha256(Path(scene["image_path"]).read_bytes()).hexdigest(), "accepted_video": ""}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = tmp_path / "dashscope_jobs"
    directory.mkdir()
    (directory / (fingerprint + ".json")).write_text(json.dumps({"id": "legacy-task-123", "submitting": False}))
    run(tmp_path, setup)
    assert not any(request.method == "POST" for request in calls)
    assert calls[0].url.path.endswith("/tasks/legacy-task-123")


@pytest.mark.parametrize("model", list(wan.WAN_MODELS))
def test_audio_enabled_payload_preserves_downloaded_audio(tmp_path, setup, monkeypatch, model):
    settings, _, calls, _ = setup
    settings["dashscope_video"].update(model=model, audio=True)
    retimes = []
    monkeypatch.setattr(router, "_retime_video", lambda *a, **kw: retimes.append(kw))
    run(tmp_path, setup)
    parameters = json.loads(calls[0].content)["parameters"]
    if model == "wan2.7-i2v":
        assert "audio" not in parameters
    else:
        assert parameters["audio"] is True
    assert retimes[0]["preserve_audio"] is True


def test_audio_preference_survives_forced_audio_model():
    from app.ui.storyboard_dashscope_video_settings import DashScopeVideoSettings
    app = QApplication.instance() or QApplication([])
    panel = DashScopeVideoSettings(lambda key, default, **kw: default.format(**kw))
    panel.set_configuration({"audio": False})
    panel.model.setCurrentIndex(panel.model.findData("wan2.7-i2v"))
    assert panel.audio.isChecked() and not panel.audio.isEnabled()
    panel.model.setCurrentIndex(panel.model.findData(wan.WAN_MODEL))
    assert not panel.audio.isChecked() and panel.audio.isEnabled()
    panel.audio.setChecked(True)
    assert panel.configuration()["audio"] is True
    assert "$0.05" in panel.help_label.text()
    panel.close()


def test_failed_task_can_retry(tmp_path, setup):
    _, _, calls, replies = setup
    replies["poll"] = {"output": {"task_status": "FAILED", "code": "ContentRejected", "message": "fake-secret"}}
    with pytest.raises(router.VideoStoryboardVideoError, match="ContentRejected") as error:
        run(tmp_path, setup)
    assert "fake-secret" not in str(error.value)
    replies.clear()
    run(tmp_path, setup)
    assert sum(r.method == "POST" for r in calls) == 2


def test_ambiguous_submission_does_not_duplicate_payment(tmp_path, setup):
    _, _, calls, replies = setup
    replies.update(post_status=504, post={"message": "Timed out"})
    with pytest.raises(router.VideoStoryboardVideoError):
        run(tmp_path, setup)
    replies.clear()
    with pytest.raises(router.VideoStoryboardVideoError, match="no confirmed task ID"):
        run(tmp_path, setup)
    assert sum(r.method == "POST" for r in calls) == 1


def test_cancel_before_submission(tmp_path, setup):
    with pytest.raises(router.VideoStoryboardVideoError, match="cancelled"):
        run(tmp_path, setup, cancelled=lambda: True)
    assert not setup[2]


@pytest.mark.parametrize("seconds, expected", [(1, 2), (2, 2), (5.1, 6), (15, 15), (30, 15)])
def test_duration_bounds(seconds, expected):
    assert wan.generation_seconds({"duration_seconds": seconds}) == expected


def test_reference_validation(tmp_path, setup):
    scene = setup[1]
    with pytest.raises(ValueError, match="ending"):
        wan.reference_image(scene, "end")
    with pytest.raises(ValueError, match="requires"):
        wan.reference_image(scene, "none")
    scene["generation_overrides"] = {"video_reference_images": [{"path": scene["image_path"]}]}
    assert wan.reference_image(scene, "none").is_file()
    assert wan.api_root({"base_url": "https://workspace.example/compatible-mode/v1"}) == "https://workspace.example/api/v1"


@pytest.mark.parametrize("model", list(wan.WAN_MODELS))
def test_settings_and_profile_roundtrip(model):
    from copy import deepcopy
    from app.core.settings_manager import DEFAULT_SETTINGS, _sanitize_video_storyboard
    from app.core.storyboard_profiles import switch_profile
    from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
    app = QApplication.instance() or QApplication([])
    config = deepcopy(DEFAULT_SETTINGS["video_storyboard"])
    config.update(video_provider="dashscope", dashscope_video={**wan.DASHSCOPE_DEFAULTS, "resolution": "1080P", "api_key": "local-key", "model": model})
    _sanitize_video_storyboard(config)
    assert config["video_provider"] == "dashscope"
    panel = VideoStoryboardSettingsWidget(lambda key, default, **kw: default.format(**kw))
    panel.set_configuration(config)
    assert panel.video_provider_combo.currentData() == "dashscope"
    assert not panel.dashscope_video_settings.isHidden()
    assert panel.litellm_video_settings.isHidden()
    saved = panel.configuration()
    assert saved["dashscope_video"] == config["dashscope_video"]
    switched = switch_profile(saved, "runpod", DEFAULT_SETTINGS["video_storyboard"])
    restored = switch_profile(switched, "local", DEFAULT_SETTINGS["video_storyboard"])
    assert restored["video_provider"] == "dashscope"
    assert restored["dashscope_video"]["api_key"] == "local-key"
    panel.close()

@pytest.mark.parametrize("model", list(wan.WAN_MODELS))
def test_dialog_shows_wan_and_disables_end_frame(tmp_path, setup, model):
    from PySide6.QtWidgets import QApplication
    from app.ui.video_storyboard_video_dialog import VideoStoryboardVideoDialog
    app = QApplication.instance() or QApplication([])
    scene = {**setup[1], "_video_provider": "dashscope", "_dashscope_video_config": {"resolution": "1080P", "model": model}, "video_frame_role": "end"}
    dialog = VideoStoryboardVideoDialog(lambda key, default, **kw: default.format(**kw), scene, {})
    assert dialog.frame_role_combo.currentData() == "start"
    assert not dialog.frame_role_combo.model().item(dialog.frame_role_combo.findData("end")).isEnabled()
    assert model in dialog.model_info.text()
    assert "1080P" in dialog.model_info.text()
    assert "audio controls" in dialog.reference_help.text()
    dialog.close()



@pytest.mark.parametrize("model, maximum", [("wan3.0-video", 30), ("wan3.0-video-prime", 30), ("wan2.7-i2v", 15)])
def test_new_models_use_media_api(tmp_path, setup, monkeypatch, model, maximum):
    settings, scene, calls, replies = setup
    settings["dashscope_video"]["model"] = model
    scene["duration_seconds"] = 40
    retimes = []
    monkeypatch.setattr(router, "_retime_video", lambda *a, **kw: retimes.append(kw))
    assert router.prepare_video_runtime(settings)["model"] == model
    result = run(tmp_path, setup)
    payload = json.loads(calls[0].content)
    assert payload["model"] == result["model"] == result["workflow_profile"] == model
    assert payload["parameters"]["resolution"] == "720P"
    assert payload["parameters"]["duration"] == maximum
    assert "shot_type" not in payload["parameters"]
    assert "img_url" not in payload["input"]
    assert payload["input"]["media"][0]["type"] == "first_frame"
    assert payload["input"]["media"][0]["url"].startswith("data:image/png;base64,")
    if model == "wan2.7-i2v":
        assert "audio" not in payload["parameters"]
    else:
        assert payload["parameters"]["audio"] is False
    assert retimes[0]["preserve_audio"] is True


def test_switching_model_does_not_resume_another_models_task(tmp_path, setup):
    settings, _, calls, replies = setup
    replies.update(poll_status=503, poll={"code": "Unavailable"})
    with pytest.raises(router.VideoStoryboardVideoError):
        run(tmp_path, setup)
    settings["dashscope_video"]["model"] = "wan3.0-video-prime"
    replies.clear()
    run(tmp_path, setup)
    posts = [json.loads(r.content)["model"] for r in calls if r.method == "POST"]
    assert posts == [wan.WAN_MODEL, "wan3.0-video-prime"]


def test_model_selection_defaults_to_720_and_updates_price():
    from app.ui.storyboard_dashscope_video_settings import DashScopeVideoSettings
    app = QApplication.instance() or QApplication([])
    panel = DashScopeVideoSettings(lambda key, default, **kw: default.format(**kw))
    panel.set_configuration({"resolution": "1080P"})
    panel.model.setCurrentIndex(panel.model.findData("wan3.0-video-prime"))
    assert panel.configuration()["resolution"] == "720P"
    assert "$0.14" in panel.help_label.text()
    panel.model.setCurrentIndex(panel.model.findData("wan2.7-i2v"))
    assert "$0.10" in panel.help_label.text()
    assert "always generates audio" in panel.help_label.text()
    assert panel.audio.isChecked() and not panel.audio.isEnabled()
    panel.close()


def test_retime_can_remove_audio(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    commands = []
    class Runner:
        def __init__(self, *args):
            pass
        def run(self, args):
            commands.append(args)
            Path(args[-1]).write_bytes(b"retimed")
    monkeypatch.setattr(router, "FFmpegRunner", Runner)
    monkeypatch.setattr(router, "find_ffmpeg", lambda *args: "ffmpeg")
    router._retime_video(source, 5, {}, source_duration_seconds=5, preserve_audio=False)
    assert "-an" in commands[0]
    assert "0:a:0?" not in commands[0]
    assert "-af" not in commands[0]
    assert source.read_bytes() == b"retimed"
