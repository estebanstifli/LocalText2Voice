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

    def test_f5_russian_reuses_the_two_remote_russian_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            reference = root / "reference.wav"
            reference.write_bytes(b"reference")
            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
            )
            manager._replace_catalog(
                [
                    {
                        "id": "omnivoice_ru_russian_man",
                        "name": "Russian Man",
                        "engine": "omnivoice",
                        "language": "ru",
                        "language_name": "Russian",
                        "type": "Reference voice",
                        "install_type": "reference_audio",
                        "ref_audio": str(reference),
                        "ref_text": "Точная русская расшифровка.",
                    },
                    {
                        "id": "omnivoice_ru_russian_woman",
                        "name": "Russian Woman",
                        "engine": "omnivoice",
                        "language": "ru",
                        "language_name": "Russian",
                        "type": "Reference voice",
                        "install_type": "reference_audio",
                        "ref_audio": str(reference),
                        "ref_text": "Точная русская расшифровка.",
                    },
                    {
                        "id": "omnivoice_en_other",
                        "name": "Other Voice",
                        "engine": "omnivoice",
                        "language": "en",
                        "language_name": "English",
                        "type": "Reference voice",
                        "install_type": "reference_audio",
                        "ref_audio": str(reference),
                        "ref_text": "An English transcript.",
                    },
                    {
                        "id": "f5_russian_imported",
                        "name": "Imported Russian Voice",
                        "engine": "f5_russian",
                        "language": "ru",
                        "language_name": "Russian",
                        "type": "Reference voice",
                        "install_type": "reference_audio",
                        "ref_audio": str(reference),
                        "ref_text": "Ещё одна точная расшифровка.",
                    },
                ]
            )

            voices = manager.list_voices("f5_russian")

            self.assertEqual(
                {voice.voice_id for voice in voices},
                {
                    "f5_russian_imported",
                    "omnivoice_ru_russian_man",
                    "omnivoice_ru_russian_woman",
                },
            )
            self.assertNotIn("Other Voice", {voice.name for voice in voices})

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

    def test_user_import_can_update_metadata_and_replace_its_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            original = root / "original.wav"
            replacement = root / "replacement.wav"
            write_reference_wav(original, seconds=3.2)
            write_reference_wav(replacement, seconds=4.5)
            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
            )
            imported = manager.import_reference_voice(
                "f5_russian",
                original,
                name="Original voice",
                language="ru",
                language_name="Russian",
                ref_text="Original transcript.",
            )
            original_destination = Path(imported.installed_path)

            updated = manager.update_reference_voice(
                imported.voice_id,
                source=replacement,
                name="Corrected voice",
                language="ru",
                language_name="Russian",
                ref_text="Corrected transcript.",
                short_description="Warm narrator",
                gender="female",
                age_style="mature",
                voice_style="podcast",
                tags=["imported", "russian", "podcast"],
            )

            self.assertTrue(updated.is_user_import)
            self.assertEqual(updated.voice_id, imported.voice_id)
            self.assertEqual(updated.name, "Corrected voice")
            self.assertEqual(updated.ref_text, "Corrected transcript.")
            self.assertEqual(updated.tags, ("imported", "russian", "podcast"))
            self.assertEqual(Path(updated.installed_path), original_destination)
            self.assertAlmostEqual(
                updated.metadata["duration_seconds"], 4.5, places=1
            )
            self.assertEqual(
                updated.metadata["original_file"], str(replacement)
            )

    def test_uninstalling_a_user_import_deletes_its_record_and_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            source = root / "reference.wav"
            write_reference_wav(source, seconds=3.2)
            manager = VoiceGalleryManager(
                db_path=root / "voice-gallery.sqlite3",
                files_root=root / "files",
            )
            imported = manager.import_reference_voice(
                "f5_russian",
                source,
                name="Temporary voice",
                language="ru",
                language_name="Russian",
                ref_text="Exact reference transcript.",
            )
            installed_path = Path(imported.installed_path)

            manager.uninstall(imported)

            self.assertFalse(installed_path.exists())
            self.assertIsNone(manager.get_voice(imported.voice_id))
            self.assertNotIn(
                imported.voice_id,
                {voice.voice_id for voice in manager.list_voices("f5_russian")},
            )


if __name__ == "__main__":
    unittest.main()
