import base64
from copy import deepcopy
import json
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image

from app.core import runpod_h3 as h3
from app.core import video_storyboard_runpod as rp
from app.core import video_storyboard_video_comfyui as video
from app.core.runpod_video_models import video_parameters
from app.core.settings_manager import DEFAULT_SETTINGS, _sanitize_video_storyboard
from app.core.storyboard_video_references import resolve_runpod_video_references


@pytest.fixture
def config():
    settings = deepcopy(DEFAULT_SETTINGS["video_storyboard"])
    settings["video_provider"] = "runpod"
    settings["runpod"].update(api_key="test-secret", video_adapter="h3", video_endpoint="private-h3", h3_preset="fast", gpu_hourly_usd=4.79)
    return settings


@pytest.mark.parametrize("seconds", [0, 10.1, -1, float("nan"), float("inf")])
def test_h3_rejects_invalid_duration(config, seconds):
    with pytest.raises(ValueError, match="1–10"):
        video_parameters(config["runpod"], "move", seconds, 5)


def test_h3_exact_contract_and_no_per_second_cost(config):
    assert video_parameters(config["runpod"], "move", 6.25, 5) == {
        "preset": "fast", "seconds": 6.25, "seed": 5, "prompt": "move", "return_base64": True}
    assert rp.estimate_cost(config, durations=[6.25]) is None
    config["runpod"]["h3_preset"] = "voice"
    with pytest.raises(ValueError):
        video_parameters(config["runpod"], "move", 5, 5)


@pytest.mark.parametrize("role", ["end", "none"])
def test_h3_requires_start_frame(config, tmp_path, role):
    image = tmp_path / "input.png"
    Image.new("RGB", (8, 8)).save(image)
    with pytest.raises(ValueError, match="starting image"):
        resolve_runpod_video_references({"image_path": str(image)}, role, config["runpod"])


def test_h3_pipeline_uses_base64_not_public_image_host(config, tmp_path):
    image = tmp_path / "input.png"
    Image.new("RGB", (8, 8)).save(image)
    def execute(settings, role, payload, target, **kwargs):
        values = payload()
        assert base64.b64decode(values["images"][0]) == image.read_bytes()
        assert values["seconds"] == 7.25
        assert not {"duration", "image", "size", "shot_type"} & values.keys()
        assert kwargs["identity"]["video_adapter"] == "h3"
        assert "images" not in kwargs["identity"]
        target.write_bytes(b"video")
        return {"prompt_id": "h3-job"}
    with patch.object(rp, "execute", side_effect=execute), patch.object(rp, "source_url", side_effect=AssertionError("must not upload")), patch.object(video, "_probe_media_duration", return_value=7.5), patch.object(video, "_retime_video"):
        result = video.generate_storyboard_scene_video({"image_path": str(image), "duration_seconds": 7.25}, {"base_seed": 5}, config, tmp_path / "clip.mp4", prompt="move", frame_role="start")
    assert result["video_duration_seconds"] == 7.25


def test_inline_video_resumes_without_another_paid_request(config, tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "jobs_directory", lambda: tmp_path / "jobs")
    data = b"\x00\x00\x00\x18ftypisom0000"
    reply = {"id": "h3-job", "status": "COMPLETED", "delayTime": 1234, "executionTime": 60000, "workerId": "worker-1",
             "output": {"files": [{"filename": "../../unsafe.mp4", "bytes": len(data), "base64": base64.b64encode(data).decode()}]}}
    with patch.object(rp, "request", return_value=reply) as submit, patch.object(rp, "download", side_effect=AssertionError("no URL")):
        result = rp.execute(config, "video", {"prompt": "move"}, tmp_path / "one.mp4")
        rp.execute(config, "video", {"prompt": "move"}, tmp_path / "two.mp4")
    assert submit.call_count == 1
    assert (tmp_path / "one.mp4").read_bytes() == data
    assert (tmp_path / "two.mp4").read_bytes() == data
    assert not (tmp_path.parent / "unsafe.mp4").exists()
    assert result["cost_usd"] is None
    assert result["timing"]["execution_seconds"] == 60
    assert result["timing"]["queue_seconds"] == 1.234
    assert result["timing"]["execution_cost_estimate_usd"] == pytest.approx(4.79 / 60)
    record = next((tmp_path / "jobs").glob("*.json")).read_text()
    assert "test-secret" not in record


@pytest.mark.parametrize("entry,match", [
    ({"filename": "video.mp4", "volume_path": "/remote/video.mp4"}, "7 MB"),
    ({"filename": "video.mp4", "base64": "invalid==="}, "invalid base64"),
    ({"filename": "video.mp4", "base64": "aGVsbG8="}, "MP4 header"),
])
def test_h3_refuses_missing_or_invalid_video(tmp_path, entry, match):
    with pytest.raises(ValueError, match=match):
        h3.save_output({"files": [entry]}, tmp_path / "clip.mp4")
    assert not (tmp_path / "clip.mp4").exists()


def test_h3_does_not_submit_to_public_wan(config, tmp_path):
    config["runpod"]["video_endpoint"] = "wan-2-6-i2v"
    with patch.object(rp, "request") as submit, pytest.raises(rp.RunpodError, match="private"):
        rp.execute(config, "video", {}, tmp_path / "clip.mp4")
    submit.assert_not_called()


def test_h3_settings_and_ui_roundtrip(config):
    from PySide6.QtWidgets import QApplication
    from app.ui.storyboard_runpod_settings import RunpodSettingsWidget
    application = QApplication.instance() or QApplication([])
    _sanitize_video_storyboard(config)
    widget = RunpodSettingsWidget(lambda key, fallback, **values: fallback.format(**values))
    widget.set_role("video")
    widget.set_configuration(config["runpod"])
    assert widget.video_model.currentData() == "h3_private"
    assert "In development" in widget.video_model.currentText()
    assert "In development" in widget.h3_panel.title()
    assert "In development" in widget.model_note.text()
    from PySide6.QtWidgets import QLabel
    assert any("not ready for public use" in label.text()
               for label in widget.h3_panel.findChildren(QLabel))
    assert not widget.size.isEnabled()
    assert widget.configuration()["video_adapter"] == "h3"
    assert widget.configuration()["video_endpoint"] == "private-h3"
    assert widget.configuration()["gpu_hourly_usd"] == 4.79
    widget.video_model.setCurrentIndex(widget.video_model.findData("wan-2-6-i2v"))
    assert widget.configuration()["video_adapter"] == "public"
    assert widget.configuration()["video_endpoint"] == "wan-2-6-i2v"
    widget.video_model.setCurrentIndex(widget.video_model.findData("h3_private"))
    assert widget.configuration()["video_endpoint"] == ""
    assert widget.advanced_toggle.isChecked()
    widget.deleteLater()
    application.processEvents()


def test_h3_video_bypasses_temporary_hosting_consent_but_images_do_not(config):
    from types import SimpleNamespace
    from app.ui.main_window import MainWindow
    window = SimpleNamespace(tr=lambda key, fallback: fallback)
    with patch('app.ui.storyboard_storage_consent.ensure_storage_consent', return_value=True) as consent:
        assert MainWindow._confirm_runpod_storage(window, config, role='video')
        consent.assert_not_called()
        assert MainWindow._confirm_runpod_storage(window, config, role='image')
        consent.assert_called_once()
        config['runpod']['video_adapter'] = 'public'
        assert MainWindow._confirm_runpod_storage(window, config, role='video')
        assert consent.call_count == 2


def test_public_endpoint_does_not_inherit_private_gpu_cost_rate():
    timing = rp.job_metrics({'executionTime': 60000}, {'video_adapter': 'public', 'gpu_hourly_usd': 4.79})
    assert timing['execution_seconds'] == 60
    assert timing['execution_cost_estimate_usd'] is None
