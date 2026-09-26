import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
import pytest

from app.core import storyboard_llm_retry as retry
from app.core import video_storyboard_planner as p


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setattr(retry, "wait_for_retry", lambda seconds, cancelled: None)


def unavailable():
    return p.VideoStoryboardPlanningError("upstream connect error: connection timeout", details={
        "type": "ServiceUnavailableError", "status_code": 503})


@pytest.mark.parametrize("structured", [False, True])
def test_real_request_entrypoints_retry_503_without_changing_payload(monkeypatch, structured):
    payloads, events = [], []
    def transport(payload, **kwargs):
        payloads.append(deepcopy(payload))
        if len(payloads) < 3:
            raise unavailable()
        return {"choices": [{"message": {"content": '{"ok": true}' if structured else "Analysis complete"}}]}
    monkeypatch.setattr(p, "_litellm_direct_responses", transport)
    settings = {"llm_provider": "litellm", "litellm": {"model": "openai/test", "max_retries": 5}}
    if structured:
        result = p._request_plan(settings, {"type": "object", "properties": {"ok": {"type": "boolean"}}}, "Instruction", "Text", trace=events.append)
        assert result == {"ok": True}
    else:
        result = p._request_free_text(settings, "Instruction", "Text", trace=events.append)
        assert result == "Analysis complete"
    assert len(payloads) == 3 and payloads[0] == payloads[1] == payloads[2]
    assert [e["retry"] for e in events if e.get("retry_type") == "provider"] == [1, 2]


@pytest.mark.parametrize("count", [0, 2, 5])
def test_nested_plain_text_delegation_has_one_retry_budget(monkeypatch, count):
    calls = []
    def transport(*args, **kwargs):
        calls.append(1)
        raise unavailable()
    monkeypatch.setattr(p, "_litellm_direct_responses", transport)
    with pytest.raises(p.VideoStoryboardPlanningError, match=f"after {count+1} attempts") as error:
        p._request_plan({"llm_provider": "litellm", "litellm": {"model": "openai/test", "max_retries": count}}, None, "", "Text")
    assert len(calls) == count+1
    assert error.value.details["status_code"] == 503


@pytest.mark.parametrize("error", [
    p.VideoStoryboardPlanningError("bad API key", details={"status_code": 401}),
    p.VideoStoryboardPlanningError("invalid request", details={"status_code": 400}),
    p.VideoStoryboardPlanningError("insufficient_quota", details={"status_code": 429}),
    p.VideoStoryboardPlanningError("invalid structured JSON"),
    p.VideoStoryboardPlanningError("Storyboard analysis was cancelled."),
])
def test_permanent_errors_are_not_retried(error):
    calls = []
    @retry.retry_llm_request
    def request(settings):
        calls.append(1)
        raise error
    with pytest.raises(p.VideoStoryboardPlanningError):
        request({})
    assert len(calls) == 1


def test_timeout_and_stream_reset_backoff_and_cancellation(monkeypatch):
    delays, calls = [], []
    def wait(seconds, cancelled):
        delays.append(seconds)
    monkeypatch.setattr(retry, "wait_for_retry", wait)
    @retry.retry_llm_request
    def request(settings, **kwargs):
        calls.append(1)
        raise TimeoutError("read timed out")
    with pytest.raises(p.VideoStoryboardPlanningError, match="cancelled"):
        request({}, cancelled=lambda: len(calls) == 2)
    assert len(calls) == 2 and delays == [2, 4]
    calls.clear()
    delays.clear()
    with pytest.raises(p.VideoStoryboardPlanningError, match="6 attempts"):
        request({})
    assert delays == [2, 4, 8, 16, 30]


def test_wait_can_be_cancelled_immediately(monkeypatch):
    # Exercise the actual waiting function, without waiting in real time.
    import importlib
    actual = importlib.reload(retry).wait_for_retry
    with pytest.raises(p.VideoStoryboardPlanningError, match="cancelled"):
        actual(30, lambda: True)


def test_stream_timeout_discards_partial_response_before_retry(monkeypatch):
    calls, events = [], []
    def transport(payload, **kwargs):
        calls.append(1)
        def stream():
            yield {"type": "response.output_text.delta", "delta": "Discard this partial reply"}
            raise TimeoutError("stream read timed out")
        if len(calls) == 1:
            return p._collect_litellm_responses_stream(stream(), kwargs["trace"], None)
        return {"choices": [{"message": {"content": "Complete replacement"}}]}
    monkeypatch.setattr(p, "_litellm_direct_responses", transport)
    result = p._request_free_text({"llm_provider": "litellm", "litellm": {"model": "openai/test"}}, "", "Text", trace=events.append)
    assert result == "Complete replacement" and len(calls) == 2
    assert any(e.get("retry_type") == "provider" for e in events)


def test_ollama_retries_connection_timeout(monkeypatch):
    calls = []
    def transport(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("socket timed out")
        return {"message": {"content": "Complete"}, "done_reason": "stop"}
    monkeypatch.setattr(p, "_http_json", transport)
    assert p._request_free_text({"ollama": {"model": "test", "max_retries": 1}}, "", "Text") == "Complete"
    assert len(calls) == 2


def test_settings_round_trip_and_zero_disables_retries():
    from PySide6.QtWidgets import QApplication
    from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget
    app = QApplication.instance() or QApplication([])
    widget = VideoStoryboardSettingsWidget(lambda key, default, **values: default.format(**values))
    assert widget.configuration()["litellm"]["max_retries"] == 5
    widget.litellm_retries_spin.setValue(0)
    widget.ollama_retries_spin.setValue(8)
    settings = widget.configuration()
    widget.set_configuration(settings)
    assert widget.configuration()["litellm"]["max_retries"] == 0
    assert widget.configuration()["ollama"]["max_retries"] == 8
    widget.close()
