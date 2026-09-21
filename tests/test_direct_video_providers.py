import json
import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import httpx
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.core import video_storyboard_video_comfyui as router
from app.core import video_provider_jobs as jobs
from app.core.direct_video_models import DEFAULTS, MODELS, generation_seconds
from app.core.video_storyboard_video_direct import ADAPTERS


def replies(provider, *, state="success"):
    if provider == "ltx":
        return {"id": "job-1"}, {"status": "completed" if state == "success" else "failed",
                                "result": {"video_url": "https://result.example/clip.mp4"},
                                **({"error": {"type": "content_filtered_error", "message": "fake-secret"}} if state != "success" else {})}
    if provider == "minimax":
        return {"task_id": "job-1"}, {"task": {"status": "succeeded" if state == "success" else "failed",
                                              "content": {"url": "https://result.example/clip.mp4"},
                                              "error": {"message": "fake-secret"}}}
    return {"id": "job-1"}, {"status": "succeeded" if state == "success" else "failed",
                            "content": {"video_url": "https://result.example/clip.mp4"},
                            "error": None if state == "success" else {"code": "ContentRejected", "message": "fake-secret"}}


@pytest.fixture(params=list(DEFAULTS))
def setup(request, tmp_path, monkeypatch):
    provider = request.param
    image = tmp_path / "frame.png"
    Image.new("RGB", (640, 360), "blue").save(image)
    settings = {"video_provider": provider, provider + "_video": {**DEFAULTS[provider], "api_key": "fake-secret"}}
    scene = {"scene_id": "001", "image_path": str(image), "duration_seconds": 7.5}
    post, poll = replies(provider)
    state = {"post": post, "poll": poll, "post_status": 202, "poll_status": 200}
    calls, retimes = [], []
    def transport(request):
        calls.append(request)
        if request.url.host == "result.example":
            return httpx.Response(state.get("download_status", 200), content=b"video-and-audio")
        if request.method == "POST":
            return httpx.Response(state["post_status"], json=state["post"])
        return httpx.Response(state["poll_status"], json=state["poll"])
    original = httpx.Client
    monkeypatch.setattr(jobs.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    monkeypatch.setattr(router, "_probe_media_duration", lambda *a: 8)
    monkeypatch.setattr(router, "_retime_video", lambda *a, **kw: retimes.append(kw))
    return provider, settings, scene, calls, state, retimes


def run(tmp_path, setup, **kwargs):
    provider, settings, scene, *_ = setup
    return router.generate_storyboard_scene_video(scene, {}, settings, tmp_path / "clip.mp4",
                                                  prompt="A bird opens its wings", frame_role=kwargs.pop("frame_role", "start"), **kwargs)


def test_real_provider_shapes_download_and_audio_fit(tmp_path, setup):
    provider, settings, scene, calls, _, retimes = setup
    assert router.prepare_video_runtime(settings)["provider"] == provider
    result = run(tmp_path, setup)
    payload = json.loads(calls[0].content)
    assert payload["model"] == settings[provider + "_video"]["model"]
    assert payload["duration"] == 8
    assert result["video_duration_seconds"] == 7.5
    assert Path(result["video_path"]).read_bytes() == b"video-and-audio"
    assert retimes == [{"source_duration_seconds": 8, "preserve_audio": True, "frame_size": (1280, 720)}]
    assert calls[0].headers["Authorization"] == calls[1].headers["Authorization"] == "Bearer fake-secret"
    assert "authorization" not in calls[2].headers
    if provider == "ltx":
        assert calls[0].url.path == "/v2/image-to-video"
        assert calls[1].url.path == "/v2/image-to-video/job-1"
        assert payload["image_uri"].startswith("data:image/png;base64,")
        assert payload["resolution"] == "1280x720" and payload["fps"] == 24
        assert payload["generate_audio"] is False
    else:
        assert payload["content"][1]["role"] == "first_frame"
        assert payload["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        if provider == "minimax":
            assert calls[0].url.path == "/v2/video_generation"
            assert calls[1].url.path == "/v2/query/video_generation/job-1"
            assert payload["resolution"] == "768P"
            assert "generate_audio" not in payload
        else:
            assert calls[0].url.path == "/api/v1/contents/generations/tasks"
            assert calls[1].url.path.endswith("/tasks/job-1")
            assert payload["resolution"] == "720p" and payload["generate_audio"] is False
    for path in (tmp_path / (provider + "_jobs")).glob("*.json"):
        content = path.read_text()
        assert "fake-secret" not in content and "base64" not in content
    run(tmp_path, setup)
    assert sum(call.method == "POST" for call in calls) == 2


def test_poll_failure_resumes_without_another_charge(tmp_path, setup):
    _, _, _, calls, state, _ = setup
    state["poll_status"] = 503
    with pytest.raises(router.VideoStoryboardVideoError):
        run(tmp_path, setup)
    state["poll_status"] = 200
    run(tmp_path, setup)
    assert sum(call.method == "POST" for call in calls) == 1


def test_terminal_failure_can_be_regenerated_and_redacts_errors(tmp_path, setup):
    provider, _, _, calls, state, _ = setup
    state["poll"] = replies(provider, state="failed")[1]
    progress = []
    with pytest.raises(router.VideoStoryboardVideoError) as exc:
        run(tmp_path, setup, status=progress.append)
    assert "fake-secret" not in str(exc.value)
    assert all("fake-secret" not in message for message in progress)
    state["poll"] = replies(provider)[1]
    run(tmp_path, setup)
    assert sum(call.method == "POST" for call in calls) == 2


@pytest.mark.parametrize("ambiguous", [True, False])
def test_submission_failure_distinguishes_rejection_and_ambiguity(tmp_path, setup, ambiguous):
    _, _, _, calls, state, _ = setup
    state["post_status"] = 504 if ambiguous else 422
    with pytest.raises(router.VideoStoryboardVideoError):
        run(tmp_path, setup)
    state["post_status"] = 202
    if ambiguous:
        with pytest.raises(router.VideoStoryboardVideoError, match="no confirmed task ID"):
            run(tmp_path, setup)
        assert sum(call.method == "POST" for call in calls) == 1
    else:
        run(tmp_path, setup)
        assert sum(call.method == "POST" for call in calls) == 2


def test_download_failure_resumes_and_cancellation_prevents_submit(tmp_path, setup):
    _, _, _, calls, state, _ = setup
    with pytest.raises(router.VideoStoryboardVideoError, match="cancelled"):
        run(tmp_path, setup, cancelled=lambda: True)
    assert not calls
    state["download_status"] = 503
    with pytest.raises(router.VideoStoryboardVideoError):
        run(tmp_path, setup)
    assert not list(tmp_path.rglob("*.part"))
    state["download_status"] = 200
    run(tmp_path, setup)
    assert sum(call.method == "POST" for call in calls) == 1


def test_expired_confirmed_job_does_not_automatically_resubmit(tmp_path, setup):
    _, _, _, calls, state, _ = setup
    state["poll_status"] = 404
    for _ in range(2):
        with pytest.raises(router.VideoStoryboardVideoError, match="missing or expired"):
            run(tmp_path, setup)
    assert sum(call.method == "POST" for call in calls) == 1


def test_text_only_and_reference_modes(tmp_path, setup):
    provider, settings, scene, calls, _, _ = setup
    run(tmp_path, setup, frame_role="none")
    payload = json.loads(calls[0].content)
    if provider == "ltx":
        assert calls[0].url.path == "/v2/text-to-video" and "image_uri" not in payload
    else:
        assert payload["content"] == [{"type": "text", "text": "A bird opens its wings"}]
    refs = [{"path": scene["image_path"]}]
    scene["generation_overrides"] = {"video_reference_images": refs}
    request = ADAPTERS[provider].build(scene, settings[provider + "_video"], "Move", "none")
    if provider == "ltx":
        assert "image_uri" in request.payload
    else:
        assert request.payload["content"][1]["role"] == "reference_image"


def test_pending_task_can_be_cancelled_and_resumed(tmp_path, setup):
    provider, _, _, calls, state, _ = setup
    state["poll"] = {"status": "processing"} if provider == "ltx" else {"task": {"status": "running"}} if provider == "minimax" else {"status": "queued"}
    with pytest.raises(router.VideoStoryboardVideoError, match="cancelled"):
        run(tmp_path, setup, cancelled=lambda: len(calls) >= 2)
    state["poll"] = replies(provider)[1]
    run(tmp_path, setup)
    assert sum(call.method == "POST" for call in calls) == 1


def test_reference_constraints_reject_without_network(tmp_path, setup):
    provider, settings, scene, calls, _, _ = setup
    extra = tmp_path / "other.png"
    Image.new("RGB", (640, 360), "green").save(extra)
    scene["generation_overrides"] = {"video_reference_images": [{"path": str(extra)}]}
    with pytest.raises(router.VideoStoryboardVideoError, match="at most 1"):
        run(tmp_path, setup)
    assert not calls
    scene.pop("generation_overrides")
    if provider != "minimax":
        with pytest.raises(router.VideoStoryboardVideoError, match="ending frame"):
            run(tmp_path, setup, frame_role="end")
        assert not calls
    else:
        run(tmp_path, setup, frame_role="end")
        assert json.loads(calls[0].content)["content"][1]["role"] == "last_frame"


@pytest.mark.parametrize("model", ["ltx-2-3-fast", "ltx-2-5-fast"])
def test_ltx_models_support_1080_and_audio(tmp_path, model):
    config = {**DEFAULTS["ltx"], "model": model, "resolution": "1080p", "audio": True}
    frame = tmp_path / "frame.png"
    Image.new("RGB", (1280, 720)).save(frame)
    request = ADAPTERS["ltx"].build({"image_path": str(frame), "duration_seconds": 9}, config, "Move", "start")
    assert request.payload["model"] == model
    assert request.payload["generate_audio"] is True
    assert request.payload["resolution"] == "1920x1080"
    assert request.payload["duration"] == 10
    assert request.frame_size == (1920, 1080)


def test_byteplus_audio_switch_and_minimax_native_audio():
    for provider in ("byteplus", "minimax"):
        for enabled in (False, True):
            request = ADAPTERS[provider].build({}, {**DEFAULTS[provider], "audio": enabled}, "Move", "none")
            if provider == "byteplus":
                assert request.payload["generate_audio"] is enabled
            else:
                assert "generate_audio" not in request.payload and "audio" not in request.payload


@pytest.mark.parametrize("provider", list(DEFAULTS))
def test_page_passes_selected_model_without_credentials(tmp_path, provider):
    from app.ui.video_storyboard_page import VideoStoryboardPage
    app = QApplication.instance() or QApplication([])
    page = VideoStoryboardPage(lambda key, default, **kw: default.format(**kw))
    model = list(MODELS[provider])[-1]
    page.set_configuration({"video_provider": provider, provider + "_video": {**DEFAULTS[provider], "model": model, "api_key": "private-key"}})
    frame = tmp_path / "frame.png"
    Image.new("RGB", (640, 360)).save(frame)
    page.set_scenes([{"scene_id": "1", "duration_seconds": 8, "image_path": str(frame), "prompt": "Move"}])
    page._select_scene("1")
    page._request_video_generation()
    assert page._video_dialog.scene["_direct_video_config"]["model"] == model
    assert "private-key" not in str(page._video_dialog.scene)
    requests = []
    page.generateAllVideosRequested.connect(requests.append)
    page._start_video_batch(False, ["1"], "Move naturally")
    assert len(requests[0]) == 1 and requests[0][0]["frame_role"] == "start"
    page._video_dialog.close()
    page.close()


@pytest.mark.parametrize("provider,model,seconds,expected", [
    ("ltx", "ltx-2-3-fast", 1, 6), ("ltx", "ltx-2-5-fast", 9, 10),
    ("ltx", "ltx-2-5-fast", 40, 20), ("minimax", "MiniMax-H3", 1, 4),
    ("minimax", "MiniMax-H3", 40, 15), ("byteplus", "dreamina-seedance-2-5-260628", 40, 30),
])
def test_model_duration_constraints(provider, model, seconds, expected):
    assert generation_seconds({"duration_seconds": seconds}, provider, {"model": model}) == expected


@pytest.mark.parametrize("provider", list(DEFAULTS))
def test_settings_profile_roundtrip_and_dialog_reference_capabilities(tmp_path, provider):
    from app.core.settings_manager import DEFAULT_SETTINGS, _sanitize_video_storyboard
    from app.core.storyboard_profiles import switch_profile
    from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
    from app.ui.video_storyboard_video_dialog import VideoStoryboardVideoDialog
    from app.core.storyboard_provider_label import provider_label
    app = QApplication.instance() or QApplication([])
    tr = lambda key, default, **kw: default.format(**kw)
    config = deepcopy(DEFAULT_SETTINGS["video_storyboard"])
    config["video_provider"] = provider
    config[provider + "_video"].update(api_key="saved-secret", audio=True)
    _sanitize_video_storyboard(config)
    panel = VideoStoryboardSettingsWidget(tr)
    panel.set_configuration(config)
    saved = panel.configuration()
    assert saved[provider + "_video"] == config[provider + "_video"]
    assert not panel.direct_video_settings[provider].isHidden()
    assert panel.dashscope_video_settings.isHidden()
    restored = switch_profile(switch_profile(saved, "runpod", DEFAULT_SETTINGS["video_storyboard"]), "local", DEFAULT_SETTINGS["video_storyboard"])
    assert restored["video_provider"] == provider and restored[provider + "_video"]["api_key"] == "saved-secret"
    assert config[provider + "_video"]["model"] in provider_label(config, "video")
    frame = tmp_path / "frame.png"
    Image.new("RGB", (640, 360)).save(frame)
    dialog = VideoStoryboardVideoDialog(tr, {"scene_id": "1", "image_path": str(frame), "duration_seconds": 8,
        "_video_provider": provider, "_direct_video_config": config[provider + "_video"]}, {})
    assert config[provider + "_video"]["model"] in dialog.model_info.text()
    assert dialog.frame_role_combo.model().item(dialog.frame_role_combo.findData("end")).isEnabled() == (provider == "minimax")
    if provider == "minimax":
        assert panel.direct_video_settings[provider].audio.isChecked()
        assert not panel.direct_video_settings[provider].audio.isEnabled()
    dialog.close()
    panel.close()
