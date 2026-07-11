from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from app.tts.voice_gallery_manager import VoiceGalleryManager


def write_reference_wav(path: Path, seconds: float = 3.2, sample_rate: int = 24000) -> None:
    frame_count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frame_count)


class VoiceGalleryManagerTest(unittest.TestCase):
    def test_sync_resolves_voice_relative_assets_and_installs_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            gallery = root / "gallery"
            voice_dir = gallery / "engines" / "chatterbox" / "en" / "sample"
            voice_dir.mkdir(parents=True)
            (voice_dir / "preview.wav").write_bytes(b"preview")
            (voice_dir / "reference.wav").write_bytes(b"reference")
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
                        "ref_audio": "reference.wav",
                        "ref_text": "Hello.",
                    }
                ),
                encoding="utf-8",
            )
            (gallery / "engines" / "chatterbox" / "index.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "engine": "chatterbox",
                        "voices": ["en/sample/voice.json"],
                    }
                ),
                encoding="utf-8",
            )
            (gallery / "catalog.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "indexes": ["engines/chatterbox/index.json"],
                        "voices": [],
                    }
                ),
                encoding="utf-8",
            )

            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
                local_catalog_path=gallery / "catalog.json",
            )
            self.assertEqual(manager.sync(), 1)
            voice = manager.list_voices("chatterbox")[0]
            self.assertTrue(manager.preview_source(voice).endswith("preview.wav"))

            installed = manager.install(voice)
            self.assertIsNotNone(installed)
            self.assertTrue(installed.is_file())
            refreshed = manager.get_voice("sample")
            self.assertIsNotNone(refreshed)
            self.assertTrue(Path(refreshed.installed_path).is_file())

    def test_builtin_voice_is_ready_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
            )
            manager._replace_catalog(  # focused test of DB mapping
                [
                    {
                        "id": "qwen_serena_spanish",
                        "name": "Serena - Spanish",
                        "engine": "qwen",
                        "language": "es",
                        "language_name": "Spanish",
                        "type": "Model speaker",
                        "install_type": "engine_builtin",
                        "speaker_id": "Serena",
                    }
                ]
            )
            voice = manager.list_voices("qwen")[0]
            self.assertTrue(voice.is_builtin)
            self.assertTrue(manager.is_installed(voice))

    def test_builtin_voice_preview_can_be_materialized_for_reference_use(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            preview = root / "preview.wav"
            preview.write_bytes(b"preview audio")
            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
            )
            manager._replace_catalog(
                [
                    {
                        "id": "omnivoice_harold",
                        "name": "Harold",
                        "engine": "omnivoice",
                        "language": "en",
                        "language_name": "English",
                        "type": "Designed voice",
                        "install_type": "engine_builtin",
                        "preview_audio": str(preview),
                        "ref_text": "Sit by the fire.",
                    }
                ]
            )
            voice = manager.get_voice("omnivoice_harold")
            self.assertIsNotNone(voice)
            materialized = manager.ensure_voice_audio(voice)
            self.assertIsNotNone(materialized)
            self.assertTrue(materialized.is_file())
            refreshed = manager.get_voice("omnivoice_harold")
            self.assertIsNotNone(refreshed)
            self.assertTrue(Path(refreshed.installed_path).is_file())

    def test_compatible_engine_index_expands_omnivoice_for_chatterbox(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            gallery = root / "gallery"
            voice_dir = gallery / "engines" / "omnivoice" / "en" / "harold"
            voice_dir.mkdir(parents=True)
            (voice_dir / "preview.wav").write_bytes(b"preview")
            (voice_dir / "voice.json").write_text(
                json.dumps(
                    {
                        "id": "omnivoice_harold",
                        "name": "Harold",
                        "engine": "omnivoice",
                        "language": "en",
                        "language_name": "English",
                        "type": "Designed voice",
                        "install_type": "engine_builtin",
                        "preview_audio": "preview.wav",
                        "ref_text": "The old narrator smiles.",
                    }
                ),
                encoding="utf-8",
            )
            (gallery / "engines" / "omnivoice" / "index.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "engine": "omnivoice",
                        "compatible_engines": ["chatterbox"],
                        "voices": ["en/harold/voice.json"],
                    }
                ),
                encoding="utf-8",
            )
            (gallery / "catalog.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "indexes": ["engines/omnivoice/index.json"],
                        "voices": [],
                    }
                ),
                encoding="utf-8",
            )

            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
                local_catalog_path=gallery / "catalog.json",
            )
            self.assertEqual(manager.sync(), 1)

            omnivoice = manager.list_voices("omnivoice")
            chatterbox = manager.list_voices("chatterbox")
            self.assertEqual(len(omnivoice), 1)
            self.assertEqual(len(chatterbox), 1)
            self.assertEqual(chatterbox[0].voice_id, "chatterbox_harold")
            self.assertTrue(chatterbox[0].is_reference_audio)
            self.assertEqual(chatterbox[0].metadata["compatible_source_id"], "omnivoice_harold")

            installed = manager.install(chatterbox[0])
            self.assertIsNotNone(installed)
            self.assertTrue(installed.is_file())

    def test_sync_removes_stale_remote_rows_but_preserves_imported_voices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            gallery = root / "gallery"
            (gallery / "engines" / "omnivoice").mkdir(parents=True)
            (gallery / "engines" / "omnivoice" / "index.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "engine": "omnivoice",
                        "voices": [],
                    }
                ),
                encoding="utf-8",
            )
            (gallery / "catalog.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "indexes": ["engines/omnivoice/index.json"],
                        "voices": [],
                    }
                ),
                encoding="utf-8",
            )

            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
                local_catalog_path=gallery / "catalog.json",
            )
            manager._replace_catalog(
                [
                    {
                        "id": "remote_old",
                        "name": "Remote Old",
                        "engine": "chatterbox",
                        "language": "en",
                        "language_name": "English",
                        "type": "Reference voice",
                        "install_type": "reference_audio",
                        "preview_audio": "",
                    }
                ]
            )
            imported = root / "imported.wav"
            write_reference_wav(imported)
            manager.import_reference_voice(
                "chatterbox",
                imported,
                name="Mine",
                ref_text="This is my reference transcript.",
            )

            self.assertEqual(manager.sync(), 0)
            self.assertIsNone(manager.get_voice("remote_old"))
            imported_rows = [
                voice
                for voice in manager.list_voices("chatterbox")
                if voice.name == "Mine"
            ]
            self.assertEqual(len(imported_rows), 1)
            self.assertEqual(imported_rows[0].ref_text, "This is my reference transcript.")
            self.assertTrue(imported_rows[0].installed_path.endswith(".wav"))

    def test_import_reference_voice_rejects_too_short_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            imported = root / "short.wav"
            write_reference_wav(imported, seconds=1.0)
            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
            )

            with self.assertRaisesRegex(Exception, "between 3 and 20 seconds"):
                manager.import_reference_voice("omnivoice", imported, name="Too Short")


if __name__ == "__main__":
    unittest.main()
