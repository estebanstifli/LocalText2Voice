from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from app.core.settings_manager import DEFAULT_SETTINGS
from app.tts.model_cache import huggingface_repo_cache_name
from app.verification.faster_whisper_manager import (
    FASTER_WHISPER_CLI,
    FasterWhisperError,
    FasterWhisperManager,
    FasterWhisperVerifier,
)


class FasterWhisperManagerTests(unittest.TestCase):
    def test_default_settings_include_review(self) -> None:
        review = DEFAULT_SETTINGS["review"]

        self.assertFalse(review["enabled"])
        self.assertEqual(review["model"], "small")
        self.assertEqual(review["compute_type"], "int8")

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

            manager = FasterWhisperManager(
                install_dir=root / "whisper",
                python_runtime=FakePythonRuntime(),  # type: ignore[arg-type]
            )
            (manager.dependency_dir / "faster_whisper").mkdir(parents=True)
            (manager.dependency_dir / "ctranslate2").mkdir(parents=True)
            manager._write_cli()
            manager._write_runtime_manifest("installed", ["faster-whisper==1.2.1"])
            manager._write_manifest("installed")
            manager.MODEL_REQUIRED_FILES = {"config.json": 1, "model.bin": 1}
            snapshot = (
                manager.cache_dir
                / "models"
                / huggingface_repo_cache_name(manager.MODEL_REPO)
                / "snapshots"
                / "revision"
            )
            snapshot.mkdir(parents=True)
            (snapshot / "config.json").write_text("{}", encoding="utf-8")
            (snapshot / "model.bin").write_bytes(b"weights")

            self.assertTrue(manager.has_runtime())
            self.assertTrue(manager.is_installed())
            self.assertEqual(
                manager.runtime_command(),
                [sys.executable, str(manager.cli_path)],
            )

    def test_worker_cli_uses_persistent_json_protocol(self) -> None:
        self.assertIn("WhisperModel", FASTER_WHISPER_CLI)
        self.assertIn('"word_timestamps": True', FASTER_WHISPER_CLI)
        self.assertIn('"words": words', FASTER_WHISPER_CLI)
        self.assertIn('"type": "ready"', FASTER_WHISPER_CLI)
        self.assertIn('"type": "result"', FASTER_WHISPER_CLI)

    def test_transcription_falls_back_to_cpu_when_cuda_dll_is_missing(self) -> None:
        manager = Mock()
        manager.is_installed.return_value = True
        verifier = FasterWhisperVerifier(manager)
        verifier.close = Mock()  # type: ignore[method-assign]
        verifier.set_log_callback(Mock())
        process = Mock()

        with (
            patch.object(verifier, "_ensure_worker", return_value=process) as ensure,
            patch.object(verifier, "_send_request"),
            patch.object(
                verifier,
                "_wait_for_response",
                side_effect=[
                    FasterWhisperError(
                        "Transcription failed: Library cublas64_12.dll is not "
                        "found or cannot be loaded"
                    ),
                    {"text": "ready", "words": []},
                ],
            ),
        ):
            result = verifier.transcribe(
                Path("segment.wav"),
                device="cuda",
                compute_type="float16",
            )

        self.assertEqual(result["text"], "ready")
        self.assertEqual(
            ensure.call_args_list,
            [call("cuda", "float16"), call("cpu", "int8")],
        )
        verifier.close.assert_called_once_with(force=True)
        verifier.log_callback.assert_called_once_with(
            "Faster Whisper CUDA libraries are unavailable; "
            "falling back to CPU (int8)."
        )

    def test_cuda_fallback_is_reused_for_later_transcriptions(self) -> None:
        manager = Mock()
        manager.is_installed.return_value = True
        verifier = FasterWhisperVerifier(manager)
        verifier._cuda_fallback_reason = "missing cublas"
        process = Mock()

        with (
            patch.object(verifier, "_ensure_worker", return_value=process) as ensure,
            patch.object(verifier, "_send_request"),
            patch.object(
                verifier,
                "_wait_for_response",
                return_value={"text": "ready", "words": []},
            ),
        ):
            verifier.transcribe(
                Path("segment.wav"),
                device="cuda",
                compute_type="float16",
            )

        ensure.assert_called_once_with("cpu", "int8")

    def test_cpu_library_error_is_not_retried(self) -> None:
        manager = Mock()
        manager.is_installed.return_value = True
        verifier = FasterWhisperVerifier(manager)

        with patch.object(
            verifier,
            "_ensure_worker",
            side_effect=FasterWhisperError(
                "Library cublas64_12.dll is not found or cannot be loaded"
            ),
        ):
            with self.assertRaises(FasterWhisperError):
                verifier.transcribe(Path("segment.wav"), device="cpu")


if __name__ == "__main__":
    unittest.main()
