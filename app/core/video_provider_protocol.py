"""Small internal contract for asynchronous video providers; no user JSON."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class VideoRequest:
    provider: str
    model: str
    root: str
    submit_path: str
    poll_path: str  # Contains {id}; the runner quotes the returned identifier.
    payload: dict = field(repr=False)
    headers: dict = field(default_factory=dict, repr=False)
    # Optional compatibility identity for jobs submitted before the shared runner.
    identity: dict | None = field(default=None, repr=False)
    frame_size: tuple[int, int] | None = None


@dataclass(frozen=True)
class VideoStatus:
    state: str  # pending, succeeded, failed
    video_url: str = ""
    detail: str = ""


class VideoAdapter(Protocol):
    def submitted_id(self, data: dict) -> str: ...
    def status(self, data: dict) -> VideoStatus: ...


def error_detail(data):
    error = data.get("error") or data
    if isinstance(error, dict):
        return " · ".join(str(error[key]) for key in ("type", "code", "message") if error.get(key))
    return str(error)
