from __future__ import annotations

import json
import tempfile
import wave
from pathlib import Path

from tools.validate_voice_gallery import validate_gallery


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\0\0" * 240)


def test_validate_voice_gallery_accepts_minimal_reference_catalog() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        root = Path(temporary_name)
        voice_dir = root / "engines" / "chatterbox" / "en" / "sample"
        _write_wav(voice_dir / "preview.wav")
        (voice_dir / "voice.json").write_text(
            json.dumps(
                {
                    "id": "sample",
                    "name": "Sample",
                    "engine": "chatterbox",
                    "language": "en",
                    "language_name": "English",
                    "type": "Reference voice",
                    "install_type": "reference_audio",
                    "preview_audio": "preview.wav",
                    "tags": ["test"],
                }
            ),
            encoding="utf-8",
        )
        (root / "engines" / "chatterbox" / "index.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "engine": "chatterbox",
                    "voices": ["en/sample/voice.json"],
                }
            ),
            encoding="utf-8",
        )
        (root / "catalog.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "indexes": ["engines/chatterbox/index.json"],
                    "voices": [],
                }
            ),
            encoding="utf-8",
        )

        assert validate_gallery(root) == []


def test_validate_voice_gallery_reports_invalid_audio() -> None:
    with tempfile.TemporaryDirectory() as temporary_name:
        root = Path(temporary_name)
        voice_dir = root / "engines" / "kokoro" / "es" / "sample"
        voice_dir.mkdir(parents=True)
        (voice_dir / "preview.wav").write_bytes(b"not a wav")
        (voice_dir / "voice.json").write_text(
            json.dumps(
                {
                    "id": "sample",
                    "name": "Sample",
                    "engine": "kokoro",
                    "language": "es",
                    "language_name": "Spanish",
                    "type": "Model speaker",
                    "install_type": "engine_builtin",
                    "preview_audio": "preview.wav",
                    "tags": [],
                }
            ),
            encoding="utf-8",
        )
        (root / "engines" / "kokoro" / "index.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "engine": "kokoro",
                    "voices": ["es/sample/voice.json"],
                }
            ),
            encoding="utf-8",
        )
        (root / "catalog.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "indexes": ["engines/kokoro/index.json"],
                    "voices": [],
                }
            ),
            encoding="utf-8",
        )

        issues = validate_gallery(root)
        assert len(issues) == 1
        assert "invalid WAV" in issues[0].message
