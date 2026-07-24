from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.tts.kokoro_python_engine import KokoroPythonTTSEngine
from app.tts.kokoro_python_manager import KOKORO_PYTHON_CLI


class FakeKokoroPythonManager:
    def __init__(self, worker_path: Path, count_path: Path) -> None:
        self.worker_path = worker_path
        self.count_path = count_path
        self.model_path = Path("model.onnx")
        self.cpu_model_path = Path("model.onnx")
        self.gpu_model_path = Path("model-gpu.onnx")
        self.voices_path = Path("voices.bin")
        self.dependency_dir = Path("deps")

    def is_installed(self) -> bool:
        return True

    def runtime_command(self) -> list[str]:
        return [sys.executable, str(self.worker_path)]

    def runtime_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["FAKE_KOKORO_COUNT"] = str(self.count_path)
        return env


class KokoroPythonEngineTests(unittest.TestCase):
    def test_worker_configures_isolated_dependency_directory(self) -> None:
        self.assertIn(
            'parser.add_argument("--deps-dir", required=True)',
            KOKORO_PYTHON_CLI,
        )
        self.assertIn(
            "sys.path.insert(0, str(deps_path))",
            KOKORO_PYTHON_CLI,
        )

    def test_worker_is_reused_for_multiple_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            worker_path = root / "fake_kokoro_worker.py"
            count_path = root / "starts.txt"
            worker_path.write_text(
                textwrap.dedent(
                    """
                    import argparse
                    import json
                    import os
                    import sys
                    from pathlib import Path

                    parser = argparse.ArgumentParser()
                    parser.add_argument("--cpu-model", required=True)
                    parser.add_argument("--gpu-model", required=True)
                    parser.add_argument("--voices", required=True)
                    parser.add_argument("--deps-dir", required=True)
                    parser.add_argument("--provider", required=True)
                    parser.parse_args()

                    count_path = Path(os.environ["FAKE_KOKORO_COUNT"])
                    count_path.write_text(
                        str(int(count_path.read_text() or "0") + 1)
                        if count_path.exists()
                        else "1",
                        encoding="utf-8",
                    )

                    def emit(payload):
                        print(json.dumps(payload), flush=True)

                    emit({"type": "timing", "label": "model load", "elapsed": 0.1})
                    emit({"type": "ready"})
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
            manager = FakeKokoroPythonManager(worker_path, count_path)
            engine = KokoroPythonTTSEngine(manager)  # type: ignore[arg-type]
            logs: list[str] = []
            engine.set_log_callback(logs.append)
            config = {
                "engine": "kokoro",
                "voice": "em_alex",
                "lang": "es",
                "speed": 1.0,
                "provider": "auto",
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
                sum("Kokoro worker ready." in item for item in logs),
                1,
            )


if __name__ == "__main__":
    unittest.main()
