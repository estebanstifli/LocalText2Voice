"""Bounded, cancellable retries of an individual analysis request."""
from contextvars import ContextVar
from functools import wraps
import json
import time

_active = ContextVar("storyboard_llm_retry", default=False)


def transient(error):
    details = getattr(error, "details", {}) or {}
    message = (type(error).__name__ + " " + str(error) + " " + json.dumps(details, default=str)).casefold()
    if any(word in message for word in ("cancelled", "canceled", "insufficient_quota", "billing", "credit balance", "authentication")):
        return False
    status = details.get("status_code", getattr(error, "status_code", getattr(error, "code", None)))
    if status is not None:
        try:
            return int(status) in {408, 429, 500, 502, 503, 504}
        except (ValueError, TypeError):
            pass
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if any(word in message for word in (
        "timeouterror", "apitimeout", "connectionerror", "apiconnectionerror",
        "serviceunavailableerror", "connection timeout", "timed out", "connection reset",
        "readtimeout", "connecttimeout", "pooltimeout", "writetimeout",
        "remoteprotocolerror", "readerror", "connecterror",
        "remote end closed connection", "incomplete read", "incompleteread")):
        return True
    cause = error.__cause__
    return transient(cause) if cause is not None and cause is not error else False


def wait_for_retry(seconds, cancelled):
    from app.core.video_storyboard_planner import VideoStoryboardPlanningError
    end = time.monotonic() + seconds
    while True:
        if cancelled and cancelled():
            raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(.1, remaining))


def retry_llm_request(function):
    @wraps(function)
    def wrapped(settings, *args, **kwargs):
        # _request_plan(schema=None) delegates to _request_free_text: one budget.
        if _active.get():
            return function(settings, *args, **kwargs)
        from app.core.video_storyboard_planner import VideoStoryboardPlanningError
        provider = str(settings.get("llm_provider") or "ollama")
        try:
            retries = max(0, min(20, int(settings.get(provider, {}).get("max_retries", 5))))
        except (TypeError, ValueError):
            retries = 5
        trace, cancelled = kwargs.get("trace"), kwargs.get("cancelled")
        token = _active.set(True)
        try:
            for attempt in range(retries + 1):
                if cancelled and cancelled():
                    raise VideoStoryboardPlanningError("Storyboard analysis was cancelled.")
                try:
                    return function(settings, *args, **kwargs)
                except Exception as exc:
                    if not transient(exc):
                        raise
                    if attempt == retries:
                        raise VideoStoryboardPlanningError(
                            f"LLM request failed after {attempt + 1} attempts ({retries} retries): {exc}",
                            details=getattr(exc, "details", {}) or {"type": type(exc).__name__, "message": str(exc)},
                        ) from exc
                    delay = min(30, 2 ** (attempt + 1))
                    if trace:
                        trace({"kind": "warning", "retry_type": "provider", "retry": attempt + 1,
                               "max_retries": retries, "delay_seconds": delay,
                               "label": kwargs.get("request_label", ""),
                               "message": f"Temporary LLM error: {exc}. Retry {attempt + 1}/{retries} in {delay}s; retrying this request only."})
                    wait_for_retry(delay, cancelled)
        finally:
            _active.reset(token)
    return wrapped
