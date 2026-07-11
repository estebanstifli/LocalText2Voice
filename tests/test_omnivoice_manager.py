from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.core.settings_manager import DEFAULT_SETTINGS
from app.tts.engine_registry import create_tts_engine, engine_ids
from app.tts.omnivoice_engine import OmniVoiceTTSEngine
from app.tts.omnivoice_manager import OMNIVOICE_PYTHON_CLI, OmniVoiceManager


class OmniVoiceManagerTests(unittest.TestCase):
    def test_registry_creates_omnivoice_engine(self) -> None:
        self.assertIn("omnivoice", engine_ids())
        self.assertIsInstance(
            create_tts_engine("omnivoice", Path("piper.exe")),
            OmniVoiceTTSEngine,
        )

    def test_models_and_modes_are_available(self) -> None:
        manager = OmniVoiceManager()
        self.assertIn("omnivoice", {model.model_id for model in manager.list_models()})
        self.assertEqual(
            {"auto", "design", "clone"},
            {mode.mode_id for mode in manager.list_modes()},
        )

    def test_default_settings_include_omnivoice(self) -> None:
        settings = DEFAULT_SETTINGS["omnivoice"]
        self.assertEqual(settings["model"], "omnivoice")
        self.assertEqual(settings["mode"], "clone")
        self.assertEqual(settings["language"], "auto")
        self.assertEqual(settings["device"], "auto")
        self.assertEqual(settings["dtype"], "auto")

    def test_runtime_detection_uses_isolated_dependency_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)

            class FakePythonRuntime:
                runtime_dir = root / "runtime"
                python_exe = Path(sys.executable)

                def is_installed(self) -> bool:
                    return True

                def cancel(self) -> None:
                    pass

            manager = OmniVoiceManager(
                install_dir=root / "models",
                python_runtime=FakePythonRuntime(),  # type: ignore[arg-type]
            )
            (manager.dependency_dir / "omnivoice").mkdir(parents=True)
            manager._write_cli()
            manager._write_runtime_manifest(
                "installed",
                ["omnivoice"],
                "cpu",
                "System GPU: no compatible GPU detected.",
                {"cuda_available": False},
            )

            self.assertTrue(manager.has_runtime())
            self.assertEqual(
                manager.runtime_command(),
                [sys.executable, str(manager.cli_path)],
            )

    def test_worker_uses_omnivoice_generate_and_writes_pcm_wav(self) -> None:
        self.assertIn("OmniVoice.from_pretrained", OMNIVOICE_PYTHON_CLI)
        self.assertIn("model.generate", OMNIVOICE_PYTHON_CLI)
        self.assertIn('subtype="PCM_16"', OMNIVOICE_PYTHON_CLI)

    def test_engine_reuses_worker_for_multiple_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_script = root / "fake_omnivoice_runtime.py"
            count_path = root / "starts.txt"
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
                    parser.add_argument("--model-repo", required=True)
                    parser.add_argument("--device", required=True)
                    parser.add_argument("--dtype", required=True)
                    parser.add_argument("--cache-dir", required=True)
                    parser.add_argument("--deps-dir", required=True)
                    parser.parse_args()

                    count = Path(os.environ["FAKE_OMNIVOICE_COUNT"])
                    count.write_text(
                        str(int(count.read_text() or "0") + 1)
                        if count.exists()
                        else "1",
                        encoding="utf-8",
                    )

                    def emit(payload):
                        print(json.dumps(payload), flush=True)

                    emit({"type": "timing", "label": "model load", "elapsed": 0.1})
                    emit({"type": "ready", "device": "cpu", "dtype": "float32"})
                    for raw_line in sys.stdin:
                        request = json.loads(raw_line)
                        if request.get("type") == "shutdown":
                            emit({"type": "shutdown"})
                            break
                        output = Path(request["output"])
                        output.write_bytes(b"fake wav")
                        emit({
                            "type": "timing",
                            "id": request["id"],
                            "label": "synthesis",
                            "elapsed": 0.2,
                        })
                        emit({
                            "type": "result",
                            "id": request["id"],
                            "output": str(output),
                        })
                    """
                ),
                encoding="utf-8",
            )

            class FakeOmniVoiceManager:
                cache_dir = root / "cache"
                dependency_dir = root / "deps"

                def is_installed(self) -> bool:
                    return True

                def model_repo(self, _model_id: str) -> str:
                    return "k2-fsa/OmniVoice"

                def runtime_command(self) -> list[str]:
                    return [sys.executable, str(runtime_script)]

                def runtime_environment(self) -> dict[str, str]:
                    env = dict(os.environ)
                    env["FAKE_OMNIVOICE_COUNT"] = str(count_path)
                    return env

            engine = OmniVoiceTTSEngine(FakeOmniVoiceManager())  # type: ignore[arg-type]
            logs: list[str] = []
            engine.set_log_callback(logs.append)
            config = {
                "engine": "omnivoice",
                "model": "omnivoice",
                "mode": "design",
                "device": "auto",
                "dtype": "auto",
                "instruct": "female, clear narrator",
            }

            try:
                engine.synthesize_to_wav("one", root / "one.wav", config)
                engine.synthesize_to_wav("two", root / "two.wav", config)
            finally:
                engine.close()

            self.assertEqual(count_path.read_text(encoding="utf-8"), "1")
            self.assertTrue((root / "one.wav").is_file())
            self.assertTrue((root / "two.wav").is_file())
            self.assertEqual(
                sum("OmniVoice worker ready" in item for item in logs),
                1,
            )


if __name__ == "__main__":
    unittest.main()
