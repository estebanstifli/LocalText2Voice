from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

from app.core.settings_manager import DEFAULT_SETTINGS
from app.core.settings_manager import SettingsManager
from app.tts.chatterbox_engine import ChatterboxTTSEngine
from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.engine_registry import create_tts_engine, engine_ids


class ChatterboxManagerTests(unittest.TestCase):
    def test_registry_creates_chatterbox_engine(self) -> None:
        self.assertIn("chatterbox", engine_ids())
        self.assertIsInstance(
            create_tts_engine("chatterbox", Path("piper.exe")),
            ChatterboxTTSEngine,
        )

    def test_models_and_languages_are_available(self) -> None:
        manager = ChatterboxManager()
        self.assertIn(
            "multilingual_v3",
            {model.model_id for model in manager.list_models()},
        )
        self.assertIn(
            "es",
            {language.language_id for language in manager.list_languages()},
        )

    def test_runtime_detection_supports_onedir_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            runtime = (
                Path(temporary_name)
                / "engines"
                / "chatterbox"
                / "chatterbox_engine"
                / "chatterbox_engine.exe"
            )
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")

            manager = ChatterboxManager(runtime_path=runtime)

            self.assertTrue(manager.has_runtime())
            self.assertEqual(manager.runtime_command(), [str(runtime)])

    def test_runtime_pack_is_downloaded_and_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            archive_path = root / "runtime.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "chatterbox_engine/chatterbox_engine.exe",
                    b"runtime",
                )

            manager = ChatterboxManager(
                install_dir=root / "models",
                runtime_dir=root / "runtime",
                runtime_pack_url=archive_path.as_uri(),
            )
            manager.RUNTIME_PACK_MIN_SIZE = 1

            destination = manager.install_runtime()

            self.assertEqual(destination, manager.runtime_dir)
            self.assertTrue(manager.has_runtime())
            self.assertEqual(
                manager.runtime_manifest()["state"],
                "installed",
            )

    def test_runtime_pack_can_be_downloaded_in_parts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            archive_path = root / "runtime.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "chatterbox_engine/chatterbox_engine.exe",
                    b"runtime",
                )
            data = archive_path.read_bytes()
            midpoint = max(1, len(data) // 2)
            part_1 = root / "runtime.zip.part01"
            part_2 = root / "runtime.zip.part02"
            part_1.write_bytes(data[:midpoint])
            part_2.write_bytes(data[midpoint:])

            manager = ChatterboxManager(
                install_dir=root / "models",
                runtime_dir=root / "runtime",
                runtime_pack_urls=[part_1.as_uri(), part_2.as_uri()],
            )
            manager.RUNTIME_PACK_MIN_SIZE = 1

            destination = manager.install_runtime()

            self.assertEqual(destination, manager.runtime_dir)
            self.assertTrue(manager.has_runtime())
            self.assertEqual(
                manager.runtime_manifest()["urls"],
                [part_1.as_uri(), part_2.as_uri()],
            )

    def test_default_settings_include_chatterbox(self) -> None:
        settings = DEFAULT_SETTINGS["chatterbox"]
        self.assertEqual(settings["model"], "multilingual_v3")
        self.assertEqual(settings["device"], "auto")

    def test_legacy_cuda_setting_is_migrated_to_auto(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            config_path = Path(temporary_name) / "config.json"
            config_path.write_text(
                json.dumps({"chatterbox": {"device": "cuda"}}),
                encoding="utf-8",
            )

            settings = SettingsManager(config_path).settings

            self.assertEqual(settings["settings_schema_version"], 2)
            self.assertEqual(settings["chatterbox"]["device"], "auto")

    def test_install_retries_with_auto_when_cuda_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_script = root / "fake_chatterbox_runtime.py"
            runtime_script.write_text(
                textwrap.dedent(
                    """
                    import sys

                    device = sys.argv[sys.argv.index("--device") + 1]
                    if device == "cuda":
                        print(
                            "Chatterbox synthesis failed: CUDA was requested, "
                            "but PyTorch cannot see a CUDA GPU.",
                            file=sys.stderr,
                        )
                        raise SystemExit(5)
                    print(f"READY model=multilingual_v3 device={device}")
                    """
                ).strip(),
                encoding="utf-8",
            )

            class FakeRuntimeManager(ChatterboxManager):
                def has_runtime(self) -> bool:
                    return True

                def runtime_command(self) -> list[str]:
                    import sys

                    return [sys.executable, str(runtime_script)]

            manager = FakeRuntimeManager(
                install_dir=root / "models",
                runtime_dir=root / "runtime",
            )

            manager.install(device="cuda")

            self.assertEqual(manager.install_manifest()["state"], "installed")
            self.assertEqual(manager.install_manifest()["device"], "auto")

    def test_engine_retries_with_auto_when_cuda_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_script = root / "fake_chatterbox_runtime.py"
            runtime_script.write_text(
                textwrap.dedent(
                    """
                    import sys
                    from pathlib import Path

                    device = sys.argv[sys.argv.index("--device") + 1]
                    if device == "cuda":
                        print(
                            "Chatterbox synthesis failed: CUDA was requested, "
                            "but PyTorch cannot see a CUDA GPU.",
                            file=sys.stderr,
                        )
                        raise SystemExit(5)
                    output = Path(sys.argv[sys.argv.index("--output") + 1])
                    output.write_bytes(b"fallback wav")
                    """
                ).strip(),
                encoding="utf-8",
            )

            class FakeRuntimeManager(ChatterboxManager):
                def has_runtime(self) -> bool:
                    return True

                def runtime_command(self) -> list[str]:
                    import sys

                    return [sys.executable, str(runtime_script)]

            manager = FakeRuntimeManager(
                install_dir=root / "models",
                runtime_dir=root / "runtime",
            )
            engine = ChatterboxTTSEngine(manager)
            output = root / "out.wav"

            engine.synthesize_to_wav(
                "hello",
                output,
                {
                    "model": "multilingual_v3",
                    "language": "en",
                    "device": "cuda",
                    "reference_audio_path": "",
                },
            )

            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
