from __future__ import annotations

import argparse
import json
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_VOICE_FIELDS = {
    "id",
    "name",
    "engine",
    "language",
    "language_name",
    "type",
    "install_type",
}
ALLOWED_INSTALL_TYPES = {"engine_builtin", "reference_audio", "piper_model"}
ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


@dataclass
class GalleryIssue:
    path: Path
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def resolve_relative(path: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip())
    if candidate.is_absolute():
        return candidate
    return path.parent / candidate


def validate_audio(path: Path, issues: list[GalleryIssue]) -> None:
    if not path.is_file():
        issues.append(GalleryIssue(path, "audio file does not exist"))
        return
    if path.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
        issues.append(GalleryIssue(path, "unsupported audio extension"))
        return
    if path.suffix.lower() != ".wav":
        return
    try:
        with wave.open(str(path), "rb") as wav_file:
            if wav_file.getnframes() <= 0:
                issues.append(GalleryIssue(path, "WAV has no frames"))
            if wav_file.getframerate() <= 0:
                issues.append(GalleryIssue(path, "WAV has invalid sample rate"))
    except wave.Error as exc:
        issues.append(GalleryIssue(path, f"invalid WAV: {exc}"))


def validate_voice(
    path: Path,
    expected_engine: str,
    seen_ids: set[str],
    issues: list[GalleryIssue],
) -> None:
    try:
        voice = load_json(path)
    except ValueError as exc:
        issues.append(GalleryIssue(path, str(exc)))
        return

    missing = sorted(field for field in REQUIRED_VOICE_FIELDS if not voice.get(field))
    if missing:
        issues.append(GalleryIssue(path, f"missing required fields: {', '.join(missing)}"))

    voice_id = str(voice.get("id", "")).strip()
    if voice_id in seen_ids:
        issues.append(GalleryIssue(path, f"duplicate voice id: {voice_id}"))
    seen_ids.add(voice_id)

    engine = str(voice.get("engine", "")).strip()
    if engine != expected_engine:
        issues.append(
            GalleryIssue(path, f"engine mismatch: expected {expected_engine}, got {engine}")
        )

    install_type = str(voice.get("install_type", "")).strip()
    if install_type not in ALLOWED_INSTALL_TYPES:
        issues.append(GalleryIssue(path, f"unsupported install_type: {install_type}"))

    tags = voice.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        issues.append(GalleryIssue(path, "tags must be an array of strings"))

    for audio_field in ("preview_audio", "ref_audio"):
        audio_path = resolve_relative(path, voice.get(audio_field))
        if audio_path is not None:
            validate_audio(audio_path, issues)

    if install_type == "reference_audio" and not (
        voice.get("ref_audio") or voice.get("preview_audio")
    ):
        issues.append(
            GalleryIssue(path, "reference_audio voices must define ref_audio or preview_audio")
        )


def validate_gallery(root: Path) -> list[GalleryIssue]:
    issues: list[GalleryIssue] = []
    catalog_path = root / "catalog.json"
    try:
        catalog = load_json(catalog_path)
    except ValueError as exc:
        return [GalleryIssue(catalog_path, str(exc))]

    indexes = catalog.get("indexes", [])
    if not isinstance(indexes, list):
        return [GalleryIssue(catalog_path, "indexes must be an array")]

    seen_ids: set[str] = set()
    for index_entry in indexes:
        if not isinstance(index_entry, str):
            issues.append(GalleryIssue(catalog_path, "index entries must be strings"))
            continue
        index_path = root / index_entry
        try:
            index = load_json(index_path)
        except ValueError as exc:
            issues.append(GalleryIssue(index_path, str(exc)))
            continue
        engine = str(index.get("engine", "")).strip()
        if not engine:
            issues.append(GalleryIssue(index_path, "index is missing engine"))
        voices = index.get("voices", [])
        if not isinstance(voices, list):
            issues.append(GalleryIssue(index_path, "voices must be an array"))
            continue
        for voice_entry in voices:
            if not isinstance(voice_entry, str):
                issues.append(GalleryIssue(index_path, "voice entries must be strings"))
                continue
            validate_voice(index_path.parent / voice_entry, engine, seen_ids, issues)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate LocalText2Voice voice gallery")
    parser.add_argument(
        "gallery_root",
        nargs="?",
        default=str(Path(__file__).resolve().parents[2] / "LocalText2Voice-VoiceGallery"),
        help="Path to the LocalText2Voice-VoiceGallery repository",
    )
    args = parser.parse_args()
    root = Path(args.gallery_root).resolve()
    issues = validate_gallery(root)
    if issues:
        print(f"Voice gallery validation failed: {len(issues)} issue(s)", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print(f"Voice gallery validation passed: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
