import io
import json
import logging
import urllib.error
from copy import deepcopy
from unittest.mock import patch

import pytest

from app.core import video_storyboard_runpod as rp
from app.core.settings_manager import DEFAULT_SETTINGS
from app.core.storyboard_credentials import protect


@pytest.fixture
def settings():
    settings = deepcopy(DEFAULT_SETTINGS["video_storyboard"])
    settings["runpod"]["api_key_encrypted"] = protect("rpa_test_secret_value")
    return settings


def http_error(req, status, detail):
    return urllib.error.HTTPError(req.full_url, status, "Error", {}, io.BytesIO(json.dumps({"status": status, "detail": detail}).encode()))


def test_public_auth_check_uses_saved_bearer_and_never_health_or_generation(settings, caplog):
    requests = []
    def respond(req, **kwargs):
        requests.append(req)
        assert req.get_method() == "GET"
        assert req.data is None
        assert "/status/" in req.full_url and req.full_url.endswith("-u1")
        assert req.get_header("Authorization") == "Bearer rpa_test_secret_value"
        raise http_error(req, 404, "job not found")
    with caplog.at_level(logging.INFO), patch.object(rp.urllib.request, "urlopen", side_effect=respond):
        result = rp.check_connection(settings)
    assert len(requests) == 3
    assert set(result) == {"image", "edit", "video"}
    assert all(value["authenticated"] and not value["generation_verified"] for value in result.values())
    assert "rpa_test_secret_value" not in caplog.text
    assert "WARNING" not in caplog.text


@pytest.mark.parametrize("status,detail", [(401, "invalid api key"), (401, "unauthorized access to endpoint"), (404, "endpoint not found"), (404, "Not Found"), (403, "Forbidden"), (429, "Too Many Requests")])
def test_failed_auth_or_generic_404_is_not_reported_as_success(settings, status, detail):
    def respond(req, **kwargs):
        raise http_error(req, status, detail)
    with patch.object(rp.urllib.request, "urlopen", side_effect=respond):
        with pytest.raises(rp.RunpodError) as error:
            rp.check_connection(settings)
    assert error.value.status_code == status
    assert error.value.response_detail == detail
    assert f"HTTP {status}" in str(error.value)
    assert "z-image-turbo/status/" in str(error.value)


def test_errors_retain_diagnostics_but_redact_credentials_and_signed_urls(settings, caplog):
    detail = 'rejected rpa_test_secret_value; Authorization: Bearer other-secret; https://media.example/image?signature=signed-secret\nsecond line'
    def respond(req, **kwargs):
        raise http_error(req, 401, detail)
    with caplog.at_level(logging.WARNING), patch.object(rp.urllib.request, "urlopen", side_effect=respond):
        with pytest.raises(rp.RunpodError) as error:
            rp.request(settings["runpod"], "z-image-turbo", "health")
    output = caplog.text + str(error.value)
    assert "rpa_test_secret_value" not in output
    assert "other-secret" not in output
    assert "signed-secret" not in output
    assert "https://api.runpod.ai/v2/z-image-turbo/health" in caplog.text
    assert "HTTP 401" in caplog.text
    assert "[REDACTED]" in output
    assert "\n" not in error.value.response_detail


def test_custom_endpoints_keep_their_health_probe(settings):
    settings["runpod"].update(image_endpoint="my-images", edit_endpoint="my-edits", video_endpoint="my-videos")
    def respond(req, **kwargs):
        assert req.full_url.endswith("/health")
        return io.BytesIO(b'{"workers": {"ready": 1}}')
    with patch.object(rp.urllib.request, "urlopen", side_effect=respond) as call:
        results = rp.check_connection(settings)
    assert call.call_count == 3
    assert results["video"]["workers"]["ready"] == 1


def test_error_does_not_log_echoed_input_objects(settings, caplog):
    def respond(req, **kwargs):
        body = {"detail": "invalid input", "input": {"image": "PRIVATE_IMAGE_CONTENT", "prompt": "PRIVATE_PROMPT"}, "headers": {"Authorization": "other-credential"}}
        raise urllib.error.HTTPError(req.full_url, 422, "Error", {}, io.BytesIO(json.dumps(body).encode()))
    with caplog.at_level(logging.WARNING), patch.object(rp.urllib.request, "urlopen", side_effect=respond):
        with pytest.raises(rp.RunpodError):
            rp.request(settings["runpod"], "z-image-turbo", "run", {"input": {"prompt": "test"}})
    assert "invalid input" in caplog.text
    assert "PRIVATE" not in caplog.text and "other-credential" not in caplog.text
