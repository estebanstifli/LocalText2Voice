from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioQualityPreset:
    """A user-facing quality preset and its FFmpeg encoder options."""

    id: str
    label: str
    bitrate: str | None
    ffmpeg_options: tuple[str, ...]


@dataclass(frozen=True)
class AudioFormatSpec:
    """Everything the application needs to produce one audio format."""

    id: str
    label: str
    extension: str
    encoder: str
    container: str
    mime_type: str
    supports_chapters: bool
    qualities: tuple[AudioQualityPreset, ...]
    default_quality: str = "standard"

    def quality(self, quality_id: object) -> AudioQualityPreset:
        normalized = str(quality_id or "").strip().casefold()
        for preset in self.qualities:
            if preset.id == normalized:
                return preset
        for preset in self.qualities:
            if preset.id == self.default_quality:
                return preset
        return self.qualities[0]


AUDIO_FORMATS: dict[str, AudioFormatSpec] = {
    "mp3": AudioFormatSpec(
        id="mp3",
        label="MP3",
        extension=".mp3",
        encoder="libmp3lame",
        container="mp3",
        mime_type="audio/mpeg",
        supports_chapters=False,
        qualities=(
            AudioQualityPreset("compact", "Compact · 64 kbps", "64k", ("-b:a", "64k")),
            AudioQualityPreset("standard", "Standard · 128 kbps", "128k", ("-b:a", "128k")),
            AudioQualityPreset("high", "High · 192 kbps", "192k", ("-b:a", "192k")),
        ),
    ),
    "m4a": AudioFormatSpec(
        id="m4a",
        label="M4A (AAC)",
        extension=".m4a",
        encoder="aac",
        container="ipod",
        mime_type="audio/mp4",
        supports_chapters=False,
        qualities=(
            AudioQualityPreset("compact", "Compact · 64 kbps", "64k", ("-b:a", "64k")),
            AudioQualityPreset("standard", "Standard · 96 kbps", "96k", ("-b:a", "96k")),
            AudioQualityPreset("high", "High · 128 kbps", "128k", ("-b:a", "128k")),
        ),
    ),
    "m4b": AudioFormatSpec(
        id="m4b",
        label="M4B (Audiobook)",
        extension=".m4b",
        encoder="aac",
        container="ipod",
        mime_type="audio/mp4",
        supports_chapters=True,
        qualities=(
            AudioQualityPreset("compact", "Compact · 64 kbps", "64k", ("-b:a", "64k")),
            AudioQualityPreset("standard", "Standard · 96 kbps", "96k", ("-b:a", "96k")),
            AudioQualityPreset("high", "High · 128 kbps", "128k", ("-b:a", "128k")),
        ),
    ),
    "opus": AudioFormatSpec(
        id="opus",
        label="Opus",
        extension=".opus",
        encoder="libopus",
        container="opus",
        mime_type="audio/ogg",
        supports_chapters=False,
        qualities=(
            AudioQualityPreset("compact", "Compact · 24 kbps", "24k", ("-b:a", "24k")),
            AudioQualityPreset("standard", "Standard · 32 kbps", "32k", ("-b:a", "32k")),
            AudioQualityPreset("high", "High · 64 kbps", "64k", ("-b:a", "64k")),
        ),
    ),
    "flac": AudioFormatSpec(
        id="flac",
        label="FLAC (lossless)",
        extension=".flac",
        encoder="flac",
        container="flac",
        mime_type="audio/flac",
        supports_chapters=False,
        qualities=(
            AudioQualityPreset("fast", "Fast lossless", None, ("-compression_level", "0")),
            AudioQualityPreset("standard", "Balanced lossless", None, ("-compression_level", "5")),
            AudioQualityPreset("small", "Smallest lossless", None, ("-compression_level", "8")),
        ),
    ),
    "ogg": AudioFormatSpec(
        id="ogg",
        label="OGG Vorbis",
        extension=".ogg",
        encoder="libvorbis",
        container="ogg",
        mime_type="audio/ogg",
        supports_chapters=False,
        qualities=(
            AudioQualityPreset("compact", "Compact · ~96 kbps", "~96k", ("-q:a", "2")),
            AudioQualityPreset("standard", "Standard · ~128 kbps", "~128k", ("-q:a", "4")),
            AudioQualityPreset("high", "High · ~192 kbps", "~192k", ("-q:a", "6")),
        ),
    ),
}

DEFAULT_AUDIO_FORMAT = "mp3"


def normalize_audio_format(value: object) -> str:
    normalized = str(value or "").strip().casefold().lstrip(".")
    return normalized if normalized in AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT


def audio_format_spec(value: object) -> AudioFormatSpec:
    return AUDIO_FORMATS[normalize_audio_format(value)]


def normalize_audio_quality(format_id: object, quality_id: object) -> str:
    spec = audio_format_spec(format_id)
    normalized = str(quality_id or "").strip().casefold()
    if any(preset.id == normalized for preset in spec.qualities):
        return normalized
    return spec.default_quality


def audio_format_from_path(path: str | Path) -> AudioFormatSpec | None:
    suffix = Path(path).suffix.casefold()
    return next(
        (spec for spec in AUDIO_FORMATS.values() if spec.extension == suffix),
        None,
    )


def supported_audio_extensions() -> frozenset[str]:
    return frozenset(spec.extension for spec in AUDIO_FORMATS.values())


def encoding_arguments(format_id: object, quality_id: object) -> list[str]:
    """Return FFmpeg output arguments, excluding metadata and output path."""

    spec = audio_format_spec(format_id)
    quality = spec.quality(quality_id)
    arguments = ["-codec:a", spec.encoder, *quality.ffmpeg_options]
    if spec.id == "opus":
        arguments.extend(["-vbr", "on", "-application", "audio"])
    if spec.id in {"m4a", "m4b"}:
        arguments.extend(["-movflags", "+faststart"])
    if spec.id == "m4b":
        arguments.extend(["-brand", "M4B "])
    arguments.extend(["-f", spec.container])
    return arguments


def legacy_mp3_bitrate_quality(value: object) -> str:
    bitrate = str(value or "").strip().casefold()
    if bitrate in {"32k", "48k", "64k", "80k"}:
        return "compact"
    if bitrate in {"160k", "192k", "224k", "256k", "320k"}:
        return "high"
    return "standard"
