from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


CAPTURE_SAMPLE_RATE = 48_000
CAPTURE_BLOCK_FRAMES = 1_024
REFERENCE_RECOMMENDED_SECONDS = 12.0
REFERENCE_MAX_SECONDS = 20.0


class AudioCaptureUnavailable(RuntimeError):
    """Raised when the operating-system audio backend cannot be queried."""


@dataclass(frozen=True, slots=True)
class AudioCaptureDevice:
    identifier: str
    name: str
    kind: Literal["microphone", "system"]
    is_default: bool = False


def list_audio_capture_devices(
    kind: Literal["microphone", "system"],
    *,
    soundcard_module: Any | None = None,
) -> list[AudioCaptureDevice]:
    """Return microphone or speaker-loopback sources exposed by SoundCard.

    SoundCard maps this API to WASAPI on Windows, CoreAudio on macOS, and
    PulseAudio/PipeWire-compatible sources on Linux. Imports stay lazy so a
    missing or unavailable native audio service does not prevent app startup.
    """

    try:
        if soundcard_module is None:
            import soundcard as soundcard_module

        wants_loopback = kind == "system"
        sources = soundcard_module.all_microphones(include_loopback=True)
        default_source = (
            soundcard_module.default_speaker()
            if wants_loopback
            else soundcard_module.default_microphone()
        )
        default_id = str(getattr(default_source, "id", "") or "")
    except Exception as exc:
        raise AudioCaptureUnavailable(
            f"The operating-system audio service could not be queried: {exc}"
        ) from exc

    devices: list[AudioCaptureDevice] = []
    seen: set[str] = set()
    for source in sources:
        is_loopback = bool(getattr(source, "isloopback", False))
        if is_loopback != wants_loopback:
            continue
        identifier = str(getattr(source, "id", "") or "")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        name = str(getattr(source, "name", "") or identifier).strip()
        devices.append(
            AudioCaptureDevice(
                identifier=identifier,
                name=name,
                kind=kind,
                is_default=identifier == default_id,
            )
        )
    devices.sort(key=lambda device: (not device.is_default, device.name.casefold()))
    return devices
