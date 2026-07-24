from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.core.settings_manager import CURRENT_SETTINGS_SCHEMA_VERSION, DEFAULT_SETTINGS
from app.core.settings_manager import SettingsManager
from app.tts.base import TTSEngineError
from app.tts.chatterbox_engine import ChatterboxTTSEngine
from app.tts.chatterbox_manager import CHATTERBOX_PYTHON_CLI, ChatterboxManager
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

    def test_runtime_detection_uses_embedded_python_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)

            class FakePythonRuntime:
                runtime_dir = root / "runtime"
                python_exe = Path(sys.executable)

                def is_installed(self) -> bool:
                    return True

                def cancel(self) -> None:
                    pass

            manager = ChatterboxManager(
                install_dir=root / "models",
                python_runtime=FakePythonRuntime(),  # type: ignore[arg-type]
            )
            manager._write_cli()
            manager._write_runtime_manifest(
                "installed",
                ["chatterbox-tts==0.1.7"],
                "cpu",
                "System GPU: no compatible GPU detected.",
                {"cuda_available": False},
            )

            self.assertTrue(manager.has_runtime())
            self.assertEqual(manager.runtime_command(), [sys.executable, str(manager.cli_path)])

    def test_old_runtime_manifest_is_not_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_dir = root / "runtime"
            manifest = runtime_dir / "engine-deps" / ChatterboxManager.RUNTIME_INSTALL_FILENAME
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "state": "installed",
                        "runtime_version": "chatterbox-python-deps-old",
                    }
                ),
                encoding="utf-8",
            )

            class FakePythonRuntime:
                runtime_dir = root / "runtime"
                python_exe = Path(sys.executable)

                def is_installed(self) -> bool:
                    return True

                def cancel(self) -> None:
                    pass

            manager = ChatterboxManager(
                install_dir=root / "models",
                python_runtime=FakePythonRuntime(),  # type: ignore[arg-type]
            )
            manager._write_cli()

            self.assertFalse(manager.runtime_is_current())

    def test_default_settings_include_chatterbox(self) -> None:
        settings = DEFAULT_SETTINGS["chatterbox"]
        self.assertEqual(settings["model"], "multilingual_v3")
        self.assertEqual(settings["device"], "auto")

    def test_runtime_error_prefers_structured_fatal_over_stderr_noise(self) -> None:
        stdout = "\n".join(
            [
                '{"type":"timing","label":"dependency import","elapsed":1.2}',
                '{"type":"fatal","message":"Chatterbox model load failed: missing dependency"}',
            ]
        )
        stderr = "\n".join(
            [
                "Fetching 6 files: 100%|##########| 6/6",
                "FutureWarning: LoRACompatibleLinear is deprecated",
            ]
        )

        self.assertEqual(
            ChatterboxManager._runtime_json_error(stdout),
            "Chatterbox model load failed: missing dependency",
        )
        self.assertEqual(ChatterboxManager._clean_runtime_stderr(stderr), "")

    def test_worker_writes_pcm_wav_for_pipeline_compatibility(self) -> None:
        self.assertIn('encoding="PCM_S"', CHATTERBOX_PYTHON_CLI)
        self.assertIn("bits_per_sample=16", CHATTERBOX_PYTHON_CLI)
        self.assertIn('parser.add_argument("--deps-dir", required=True)', CHATTERBOX_PYTHON_CLI)
        self.assertIn("sys.path.insert(0, str(deps_path))", CHATTERBOX_PYTHON_CLI)

    def test_legacy_cuda_setting_is_migrated_to_auto(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            config_path = Path(temporary_name) / "config.json"
            config_path.write_text(
                json.dumps({"chatterbox": {"device": "cuda"}}),
                encoding="utf-8",
            )

            settings = SettingsManager(config_path).settings

            self.assertEqual(
                settings["settings_schema_version"],
                CURRENT_SETTINGS_SCHEMA_VERSION,
            )
            self.assertEqual(settings["chatterbox"]["device"], "auto")

    def test_install_retries_with_auto_when_cuda_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_script = root / "fake_chatterbox_runtime.py"
            runtime_script.write_text(
                textwrap.dedent(
                    """
                    import argparse
                    import sys

                    parser = argparse.ArgumentParser()
                    parser.add_argument("--warmup", action="store_true")
                    parser.add_argument("--model")
                    parser.add_argument("--device")
                    parser.add_argument("--cache-dir")
                    parser.add_argument("--deps-dir")
                    args = parser.parse_args()
                    device = args.device
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

                def runtime_is_current(self) -> bool:
                    return True

                def runtime_command(self) -> list[str]:
                    return [sys.executable, str(runtime_script)]

                def _install_runtime_dependencies(self, progress, cancel_token):
                    self._write_cli()

            class FakePythonRuntime:
                runtime_dir = root / "runtime"
                python_exe = Path(sys.executable)

                def is_installed(self) -> bool:
                    return True

                def cancel(self) -> None:
                    pass

            manager = FakeRuntimeManager(
                install_dir=root / "models",
                runtime_dir=root / "runtime",
                python_runtime=FakePythonRuntime(),  # type: ignore[arg-type]
            )

            manager.install(device="cuda")

            self.assertEqual(manager.install_manifest()["state"], "installed")
            self.assertEqual(manager.install_manifest()["device"], "auto")

    def test_engine_reuses_worker_for_multiple_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_script = root / "fake_chatterbox_runtime.py"
            runtime_script.write_text(
                textwrap.dedent(
                    """
                    import argparse
                    import json
                    import os
                    import sys
                    from pathlib import Path

                    parser = argparse.ArgumentParser()
                    parser.add_argument("--worker", action="store_true")
                    parser.add_argument("--model", required=True)
                    parser.add_argument("--device", required=True)
                    parser.add_argument("--cache-dir", required=True)
                    parser.add_argument("--deps-dir", required=True)
                    parser.parse_args()

                    count = Path(os.environ["FAKE_CHATTERBOX_COUNT"])
                    count.write_text(
                        str(int(count.read_text() or "0") + 1)
                        if count.exists()
                        else "1",
                        encoding="utf-8",
                    )

                    def emit(payload):
                        print(json.dumps(payload), flush=True)

                    emit({"type": "ready", "model": "multilingual_v3", "device": "cpu"})
                    for raw_line in sys.stdin:
                        request = json.loads(raw_line)
                        if request.get("type") == "shutdown":
                            emit({"type": "shutdown"})
                            break
                        output = Path(request["output"])
                        output.write_bytes(b"fake wav")
                        emit({"type": "timing", "id": request["id"], "label": "synthesis", "elapsed": 0.1})
                        emit({"type": "result", "id": request["id"], "output": str(output)})
                    """
                ).strip(),
                encoding="utf-8",
            )
            count_path = root / "starts.txt"

            class FakeRuntimeManager(ChatterboxManager):
                def has_runtime(self) -> bool:
                    return True

                def runtime_is_current(self) -> bool:
                    return True

                def runtime_command(self) -> list[str]:
                    return [sys.executable, str(runtime_script)]

                def runtime_environment(self) -> dict[str, str]:
                    env = dict(os.environ)
                    env["FAKE_CHATTERBOX_COUNT"] = str(count_path)
                    return env

            manager = FakeRuntimeManager(
                install_dir=root / "models",
                runtime_dir=root / "runtime",
            )
            engine = ChatterboxTTSEngine(manager)
            output = root / "out.wav"
            output_2 = root / "out2.wav"

            config = {
                "model": "multilingual_v3",
                "language": "en",
                "device": "auto",
                "reference_audio_path": "",
            }
            try:
                engine.synthesize_to_wav("hello", output, config)
                engine.synthesize_to_wav("again", output_2, config)
            finally:
                engine.close()

            self.assertTrue(output.is_file())
            self.assertTrue(output_2.is_file())
            self.assertEqual(
                count_path.read_text(encoding="utf-8"),
                "1",
            )


if __name__ == "__main__":
    unittest.main()
