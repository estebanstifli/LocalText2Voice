import io
import json
import urllib.error
from copy import deepcopy
from unittest.mock import patch

import pytest
from PIL import Image, PngImagePlugin

from app.core import storyboard_temporary_storage as storage
from app.core import video_storyboard_runpod as rp
from app.core.storyboard_credentials import protect, reveal
from app.core.settings_manager import DEFAULT_SETTINGS, SettingsManager


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "jobs_directory", lambda: tmp_path / "jobs")
    monkeypatch.setattr(rp, "_asset_index", lambda: tmp_path / "assets")
    settings = deepcopy(DEFAULT_SETTINGS["video_storyboard"])
    settings["runpod"].update(api_key="runpod-test-key", reference_storage="managed",
        temporary_storage_url="https://storage.example", temporary_storage_token_encrypted=protect("a" * 64))
    source = tmp_path / "reference.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Private", "original filename and location")
    Image.new("RGB", (16, 16), "blue").save(source, pnginfo=metadata)
    asset = {"id": "12345678-1234-1234-1234-123456789abc", "url": "https://storage.example/media/reference?signature=secret",
        "expires": 9999999999, "service_url": "https://storage.example", "credential_id": storage.credential_id(settings["runpod"])}
    return settings, source, asset


def test_upload_normalization_preserves_pixels_and_removes_metadata(setup):
    _, source, _ = setup
    data = storage.normalized_png(source)
    assert b"Private" not in data and b"original filename" not in data
    with Image.open(source) as original, Image.open(io.BytesIO(data)) as normalized:
        assert original.tobytes() == normalized.tobytes()
        assert normalized.info == {}


def test_temporary_credential_never_redirects_or_sends_runpod_key(setup):
    settings, _, _ = setup
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *_): pass
    with patch.object(storage.urllib.request, "build_opener") as factory:
        factory.return_value.open.return_value = Response(b'{"available": true}')
        storage.request(settings["runpod"], "/v1/status")
    req = factory.return_value.open.call_args.args[0]
    assert req.get_header("Authorization") == "Bearer " + "a" * 64
    assert "runpod-test-key" not in str(req.headers)
    assert isinstance(factory.call_args.args[0], storage._NoRedirect)
    assert storage._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example") is None


@pytest.mark.parametrize("url", ["http://storage.example", "https://user:pass@storage.example", "https://storage.example/path", "https://storage.example?token=x"])
def test_invalid_service_addresses_are_rejected(url):
    with pytest.raises(storage.TemporaryStorageError):
        storage.service_url({"temporary_storage_url": url})


def test_other_profile_cannot_delete_asset(setup):
    settings, _, asset = setup
    settings["runpod"]["temporary_storage_token_encrypted"] = protect("b" * 64)
    with patch.object(storage, "request") as request:
        assert storage.delete(asset, settings["runpod"]) is False
        request.assert_not_called()


def test_managed_uploads_deduplicate_within_job_and_delete_after_completion(setup, tmp_path):
    settings, source, asset = setup
    def payload():
        return {"images": [rp.source_url(source, settings["runpod"]), rp.source_url(source, settings["runpod"])]}
    def submit(*args):
        assert args[3]["input"]["images"] == [asset["url"], asset["url"]]
        return {"id": "job", "status": "COMPLETED", "output": {"image_url": "https://image.runpod.ai/result.png"}}
    with patch.object(storage, "upload", return_value=asset.copy()) as upload, patch.object(storage, "delete", return_value=True) as delete, \
         patch.object(rp, "request", side_effect=submit), patch.object(rp, "download", side_effect=lambda u, p, c: Image.new("RGB", (16, 16)).save(p)):
        rp.execute(settings, "edit", payload, tmp_path / "result.png", identity={"prompt": "edit"})
    assert upload.call_count == delete.call_count == 1
    saved = json.loads(next((tmp_path / "jobs").glob("*.json")).read_text())
    assert saved["temporary_assets"][0]["deleted"] is True
    assert "signature=secret" not in json.dumps(saved)
    assert "a" * 64 not in json.dumps(saved)


@pytest.mark.parametrize("status_code,should_delete", [(401, True), (None, False)])
def test_known_rejection_cleans_but_unknown_submission_preserves_reference(setup, tmp_path, status_code, should_delete):
    settings, source, asset = setup
    error = rp.RunpodError("submission failed")
    error.status_code = status_code
    with patch.object(storage, "upload", return_value=asset.copy()), patch.object(storage, "delete", return_value=True) as delete, patch.object(rp, "request", side_effect=error):
        with pytest.raises(rp.RunpodError):
            rp.execute(settings, "video", lambda: {"image": rp.source_url(source, settings["runpod"])}, tmp_path / "result.mp4", identity={"scene": "one"})
    assert bool(delete.call_count) == should_delete


def test_partial_reference_preparation_is_cleaned_without_submitting(setup, tmp_path):
    settings, source, asset = setup
    def payload():
        rp.source_url(source, settings["runpod"])
        raise ValueError("second reference invalid")
    with patch.object(storage, "upload", return_value=asset.copy()), patch.object(storage, "delete", return_value=True) as delete, patch.object(rp, "request") as submit:
        with pytest.raises(ValueError):
            rp.execute(settings, "edit", payload, tmp_path / "result.png", identity={"prompt": "edit"})
    delete.assert_called_once()
    submit.assert_not_called()


def test_cleanup_failure_does_not_lose_paid_result(setup, tmp_path):
    settings, source, asset = setup
    with patch.object(storage, "upload", return_value=asset.copy()), patch.object(storage, "delete", side_effect=storage.TemporaryStorageError("offline")), \
         patch.object(rp, "request", return_value={"id": "job", "status": "COMPLETED", "output": {"video_url": "https://video.runpod.ai/result.mp4"}}), patch.object(rp, "download"):
        result = rp.execute(settings, "video", lambda: {"image": rp.source_url(source, settings["runpod"])}, tmp_path / "result.mp4", identity={"scene": "one"})
    assert result["prompt_id"] == "job"
    saved = json.loads(next((tmp_path / "jobs").glob("*.json")).read_text())
    assert saved["temporary_assets"][0]["deleted"] is False


def test_beta_configuration_roundtrip(setup, tmp_path):
    settings, _, _ = setup
    manager = SettingsManager(tmp_path / "config.json")
    config = deepcopy(manager.settings)
    config["video_storyboard"] = settings
    manager.save(config)
    restored = SettingsManager(tmp_path / "config.json").get("video_storyboard")["runpod"]
    assert restored["reference_storage"] == "managed"
    assert restored["temporary_storage_url"] == "https://storage.example"
    assert reveal(restored["temporary_storage_token_encrypted"]) == "a" * 64


def test_wait_timeout_preserves_remote_references(setup, tmp_path):
    settings, source, asset = setup
    with patch.object(storage, "upload", return_value=asset.copy()), patch.object(storage, "delete") as delete, \
         patch.object(rp, "request", return_value={"id": "job", "status": "IN_QUEUE"}), patch.object(rp.time, "monotonic", side_effect=[0, 9999]):
        with pytest.raises(rp.RunpodError, match="may still be running"):
            rp.execute(settings, "video", lambda: {"image": rp.source_url(source, settings["runpod"])}, tmp_path / "result.mp4", identity={"scene": "one"})
    delete.assert_not_called()


@pytest.mark.parametrize("immediate_failure", [False, True])
def test_cancel_and_immediate_failure_release_references(setup, tmp_path, immediate_failure):
    settings, source, asset = setup
    calls = []
    def remote(config, endpoint, operation, payload=None):
        calls.append(operation)
        return {"id": "job", "status": "FAILED" if immediate_failure else "IN_QUEUE"}
    with patch.object(storage, "upload", return_value=asset.copy()), patch.object(storage, "delete", return_value=True) as delete, patch.object(rp, "request", side_effect=remote):
        with pytest.raises(rp.RunpodError):
            rp.execute(settings, "video", lambda: {"image": rp.source_url(source, settings["runpod"])}, tmp_path / "result.mp4",
                       identity={"scene": "one"}, cancelled=lambda: bool(calls) and not immediate_failure)
    delete.assert_called_once()
    assert calls == (["run"] if immediate_failure else ["run", "cancel/job"])


def test_ui_uses_automatic_access_and_preserves_legacy_s3():
    from PySide6.QtWidgets import QApplication
    from app.ui.storyboard_runpod_settings import RunpodSettingsWidget
    app = QApplication.instance() or QApplication([])
    widget = RunpodSettingsWidget(lambda key, fallback=None, **values: (fallback or key).format(**values))
    widget.set_configuration({"reference_storage": "managed", "temporary_storage_token_encrypted": protect("a" * 64)})
    assert widget.reference_storage.currentData() == "managed"
    assert not widget.managed_panel.isHidden()
    assert widget.configuration()["temporary_storage_token_encrypted"] == ""
    assert not hasattr(widget, "storage_token")
    widget.set_configuration({})
    assert widget.reference_storage.currentData() == "managed"
    widget.set_configuration({"s3_endpoint": "https://own.example"})
    assert widget.reference_storage.currentData() == "s3"
    assert widget.managed_panel.isHidden()
    widget.deleteLater()
