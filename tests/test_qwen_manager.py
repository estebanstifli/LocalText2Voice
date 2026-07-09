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
from app.tts.qwen_engine import QwenTTSEngine
from app.tts.qwen_manager import QWEN_PYTHON_CLI, QwenManager


class QwenManagerTests(unittest.TestCase):
    def test_registry_creates_qwen_engine(self) -> None:
        self.assertIn("qwen", engine_ids())
        self.assertIsInstance(
            create_tts_engine("qwen", Path("piper.exe")),
            QwenTTSEngine,
        )

    def test_models_languages_and_speakers_are_available(self) -> None:
        manager = QwenManager()
        self.assertIn(
            "custom_voice_0_6b",
            {model.model_id for model in manager.list_models()},
        )
        self.assertIn(
            "Spanish",
            {language.language_id for language in manager.list_languages()},
        )
        self.assertIn("Serena", {voice.voice_id for voice in manager.list_voices()})

    def test_default_settings_include_qwen(self) -> None:
        settings = DEFAULT_SETTINGS["qwen"]
        self.assertEqual(settings["model"], "custom_voice_0_6b")
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

            manager = QwenManager(
                install_dir=root / "models",
                python_runtime=FakePythonRuntime(),  # type: ignore[arg-type]
            )
            (manager.dependency_dir / "qwen_tts").mkdir(parents=True)
            (manager.dependency_dir / "faster_qwen3_tts").mkdir(parents=True)
            manager._write_cli()
            manager._write_runtime_manifest(
                "installed",
                ["faster-qwen3-tts==0.3.0", "qwen-tts==0.1.1"],
                "cpu",
                "System GPU: no compatible GPU detected.",
                {"cuda_available": False},
            )

            self.assertTrue(manager.has_runtime())
            self.assertEqual(manager.runtime_command(), [sys.executable, str(manager.cli_path)])

    def test_runtime_error_prefers_structured_fatal_over_stderr_noise(self) -> None:
        stdout = "\n".join(
            [
                '{"type":"timing","label":"dependency import","elapsed":1.2}',
                '{"type":"fatal","message":"Qwen model load failed: missing dependency"}',
            ]
        )
        stderr = "\n".join(
            [
                "Fetching 6 files: 100%|##########| 6/6",
                "FutureWarning: deprecated",
                "WARNING: You are using unauthenticated requests to the HF Hub.",
            ]
        )

        self.assertEqual(
            QwenManager._runtime_json_error(stdout),
            "Qwen model load failed: missing dependency",
        )
        self.assertEqual(QwenManager._clean_runtime_stderr(stderr), "")

    def test_worker_writes_pcm_wav_for_pipeline_compatibility(self) -> None:
        self.assertIn("FasterQwen3TTS", QWEN_PYTHON_CLI)
        self.assertIn("generate_custom_voice", QWEN_PYTHON_CLI)
        self.assertIn('subtype="PCM_16"', QWEN_PYTHON_CLI)

    def test_engine_reuses_worker_for_multiple_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            runtime_script = root / "fake_qwen_runtime.py"
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

                    count = Path(os.environ["FAKE_QWEN_COUNT"])
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

            class FakeQwenManager:
                cache_dir = root / "cache"
                dependency_dir = root / "deps"

                def is_installed(self) -> bool:
                    return True

                def model_repo(self, _model_id: str) -> str:
                    return "Qwen/fake"

                def runtime_command(self) -> list[str]:
                    return [sys.executable, str(runtime_script)]

                def runtime_environment(self) -> dict[str, str]:
                    env = dict(os.environ)
                    env["FAKE_QWEN_COUNT"] = str(count_path)
                    return env

            engine = QwenTTSEngine(FakeQwenManager())  # type: ignore[arg-type]
            logs: list[str] = []
            engine.set_log_callback(logs.append)
            config = {
                "engine": "qwen",
                "model": "custom_voice_0_6b",
                "language": "Spanish",
                "speaker": "Serena",
                "device": "auto",
                "dtype": "auto",
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
                sum("Qwen3 TTS worker ready" in item for item in logs),
                1,
            )


if __name__ == "__main__":
    unittest.main()
