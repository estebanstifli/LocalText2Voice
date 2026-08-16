from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.settings_manager import DEFAULT_SETTINGS
from app.tts.base import TTSEngineError
from app.tts.engine_registry import create_tts_engine, engine_ids
from app.tts.qwen_engine import QwenTTSEngine
from app.tts.python_runtime_manager import PythonRuntimeError
from app.tts.qwen_manager import (
    QWEN_CUDA_BLACKWELL_PROFILE,
    QWEN_CUDA_LEGACY_PROFILE,
    QWEN_CPU_PROFILE,
    QWEN_PYTHON_CLI,
    QwenManager,
)
from app.utils.gpu_detection import GPUDetectionResult, GPUInfo


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
            "base_1_7b",
            {model.model_id for model in manager.list_models()},
        )
        self.assertEqual(manager.model_kind("custom_voice_0_6b"), "custom_voice")
        self.assertEqual(manager.model_kind("base_1_7b"), "voice_clone")
        self.assertEqual(
            manager.model_repo("base_1_7b"),
            "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        )
        self.assertIn(
            "Spanish",
            {language.language_id for language in manager.list_languages()},
        )
        self.assertIn("Serena", {voice.voice_id for voice in manager.list_voices()})

    def test_model_cache_requires_complete_nested_speech_tokenizer(self) -> None:
        required = QwenManager.MODEL_REQUIRED_FILES

        self.assertIn("preprocessor_config.json", required)
        self.assertIn("speech_tokenizer/configuration.json", required)
        self.assertIn("speech_tokenizer/preprocessor_config.json", required)
        self.assertIn("speech_tokenizer/model.safetensors", required)

    def test_default_settings_include_qwen(self) -> None:
        settings = DEFAULT_SETTINGS["qwen"]
        self.assertEqual(settings["model"], "custom_voice_0_6b")
        self.assertEqual(settings["device"], "auto")
        self.assertEqual(settings["dtype"], "auto")
        self.assertEqual(settings["reference_audio_path"], "")
        self.assertEqual(settings["reference_text"], "")

    def test_runtime_profiles_select_cpu_legacy_and_blackwell(self) -> None:
        cpu = GPUDetectionResult()
        legacy = GPUDetectionResult(
            gpus=[
                GPUInfo(
                    name="NVIDIA GeForce RTX 3070",
                    compute_capability="8.6",
                )
            ]
        )
        blackwell = GPUDetectionResult(
            gpus=[
                GPUInfo(
                    name="NVIDIA GeForce RTX 5060 Ti",
                    compute_capability="12.0",
                )
            ]
        )
        blackwell_name_fallback = GPUDetectionResult(
            gpus=[GPUInfo(name="NVIDIA GeForce RTX 5090")]
        )
        unknown_future_gpu = GPUDetectionResult(
            gpus=[
                GPUInfo(
                    name="NVIDIA GeForce RTX 6090",
                    compute_capability="13.0",
                )
            ]
        )

        self.assertEqual(QwenManager.select_runtime_profile(cpu), QWEN_CPU_PROFILE)
        self.assertEqual(
            QwenManager.select_runtime_profile(legacy),
            QWEN_CUDA_LEGACY_PROFILE,
        )
        self.assertEqual(
            QwenManager.select_runtime_profile(blackwell),
            QWEN_CUDA_BLACKWELL_PROFILE,
        )
        self.assertEqual(
            QwenManager.select_runtime_profile(blackwell_name_fallback),
            QWEN_CUDA_BLACKWELL_PROFILE,
        )
        self.assertEqual(
            QwenManager.select_runtime_profile(unknown_future_gpu),
            QWEN_CPU_PROFILE,
        )

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
            dependency_dir = manager.profile_dependency_dir(QWEN_CPU_PROFILE)
            (dependency_dir / "qwen_tts").mkdir(parents=True)
            (dependency_dir / "faster_qwen3_tts").mkdir(parents=True)
            manager._write_cli()
            manager._write_runtime_manifest(
                "installed",
                ["faster-qwen3-tts==0.3.0", "qwen-tts==0.1.1"],
                QWEN_CPU_PROFILE,
                QWEN_CPU_PROFILE,
                "System GPU: no compatible GPU detected.",
                {"cuda_available": False},
            )

            self.assertTrue(
                manager.has_runtime_for_detection(GPUDetectionResult())
            )
            self.assertEqual(manager.runtime_command(), [sys.executable, str(manager.cli_path)])

    def test_runtime_manifest_changes_when_hardware_needs_another_profile(
        self,
    ) -> None:
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
            dependency_dir = manager.profile_dependency_dir(
                QWEN_CUDA_LEGACY_PROFILE
            )
            (dependency_dir / "qwen_tts").mkdir(parents=True)
            (dependency_dir / "faster_qwen3_tts").mkdir(parents=True)
            manager._write_cli()
            manager._write_runtime_manifest(
                "installed",
                ["torch==2.6.0", "faster-qwen3-tts==0.3.0"],
                QWEN_CUDA_LEGACY_PROFILE,
                QWEN_CUDA_LEGACY_PROFILE,
                "NVIDIA GeForce RTX 3070",
                {"cuda_available": True, "cuda_kernel_test": True},
            )
            legacy = GPUDetectionResult(
                gpus=[
                    GPUInfo(
                        name="NVIDIA GeForce RTX 3070",
                        compute_capability="8.6",
                    )
                ]
            )
            blackwell = GPUDetectionResult(
                gpus=[
                    GPUInfo(
                        name="NVIDIA GeForce RTX 5060 Ti",
                        compute_capability="12.0",
                    )
                ]
            )

            self.assertTrue(manager.has_runtime_for_detection(legacy))
            self.assertFalse(manager.has_runtime_for_detection(blackwell))

    def test_cpu_fallback_is_current_for_requested_cuda_profile(self) -> None:
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
            dependency_dir = manager.profile_dependency_dir(QWEN_CPU_PROFILE)
            (dependency_dir / "qwen_tts").mkdir(parents=True)
            (dependency_dir / "faster_qwen3_tts").mkdir(parents=True)
            manager._write_cli()
            manager._write_runtime_manifest(
                "installed",
                ["torch==2.6.0", "faster-qwen3-tts==0.3.0"],
                QWEN_CPU_PROFILE,
                QWEN_CUDA_BLACKWELL_PROFILE,
                "NVIDIA GeForce RTX 5060 Ti",
                {"cuda_available": False},
                "CUDA validation failed",
            )
            blackwell = GPUDetectionResult(
                gpus=[
                    GPUInfo(
                        name="NVIDIA GeForce RTX 5060 Ti",
                        compute_capability="12.0",
                    )
                ]
            )

            self.assertTrue(manager.has_runtime_for_detection(blackwell))
            self.assertEqual(
                manager.runtime_manifest()["requested_profile_id"],
                QWEN_CUDA_BLACKWELL_PROFILE.profile_id,
            )

    def test_reinstall_retries_cuda_when_a_cpu_fallback_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            attempted_profiles = []

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
            dependency_dir = manager.profile_dependency_dir(QWEN_CPU_PROFILE)
            (dependency_dir / "qwen_tts").mkdir(parents=True)
            (dependency_dir / "faster_qwen3_tts").mkdir(parents=True)
            manager._write_cli()
            manager._write_runtime_manifest(
                "installed",
                ["torch==2.6.0"],
                QWEN_CPU_PROFILE,
                QWEN_CUDA_BLACKWELL_PROFILE,
                "NVIDIA GeForce RTX 5060 Ti",
                {
                    "torch_version": "2.6.0+cpu",
                    "torch_cuda_version": None,
                    "cuda_available": False,
                },
                "CUDA validation failed",
            )
            blackwell = GPUDetectionResult(
                gpus=[
                    GPUInfo(
                        name="NVIDIA GeForce RTX 5060 Ti",
                        compute_capability="12.0",
                    )
                ]
            )

            def install_profile(profile, _progress, _cancel_token):
                attempted_profiles.append(profile)
                if profile.backend == "cuda":
                    raise PythonRuntimeError("CUDA validation still fails")
                return (
                    ["torch==2.6.0"],
                    {
                        "torch_version": "2.6.0+cpu",
                        "torch_cuda_version": None,
                        "cuda_available": False,
                    },
                )

            with (
                patch(
                    "app.tts.qwen_manager.detect_gpus",
                    return_value=blackwell,
                ),
                patch.object(
                    manager,
                    "_install_profile_environment",
                    side_effect=install_profile,
                ),
            ):
                manager._install_runtime_dependencies(
                    lambda _current, _total, _message: None,
                    None,
                )

            self.assertEqual(
                attempted_profiles,
                [QWEN_CUDA_BLACKWELL_PROFILE, QWEN_CPU_PROFILE],
            )

    def test_blackwell_install_uses_cu130_profile_in_versioned_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            commands: list[list[str]] = []

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

            def fake_run_pip(
                args,
                _cancel_token,
                _progress=None,
                _current=0,
            ):
                command = list(args)
                commands.append(command)
                target = Path(command[command.index("--target") + 1])
                target.mkdir(parents=True, exist_ok=True)
                if (
                    manager.QWEN_PACKAGE in command
                    or manager.UPSTREAM_QWEN_PACKAGE in command
                ):
                    (target / "qwen_tts").mkdir(exist_ok=True)
                    (target / "faster_qwen3_tts").mkdir(exist_ok=True)
                return ""

            blackwell = GPUDetectionResult(
                gpus=[
                    GPUInfo(
                        name="NVIDIA GeForce RTX 5060 Ti",
                        compute_capability="12.0",
                    )
                ]
            )
            runtime_info = {
                "torch_version": "2.11.0+cu130",
                "torch_cuda_version": "13.0",
                "cuda_available": True,
                "cuda_kernel_test": True,
            }
            with (
                patch(
                    "app.tts.qwen_manager.detect_gpus",
                    return_value=blackwell,
                ),
                patch.object(manager, "_run_pip", side_effect=fake_run_pip),
                patch.object(
                    manager,
                    "_validate_runtime",
                    return_value=runtime_info,
                ),
            ):
                manager._install_runtime_dependencies(
                    lambda _current, _total, _message: None,
                    None,
                )
            manager._write_cli()

            torch_commands = [
                command
                for command in commands
                if f"torch=={QWEN_CUDA_BLACKWELL_PROFILE.torch_version}" in command
            ]
            self.assertEqual(len(torch_commands), 1)
            self.assertTrue(
                all(
                    QWEN_CUDA_BLACKWELL_PROFILE.torch_index_url in command
                    for command in torch_commands
                )
            )
            support_command = next(
                command
                for command in commands
                if manager.UPSTREAM_QWEN_PACKAGE in command
            )
            self.assertIn(
                f"torchaudio=={QWEN_CUDA_BLACKWELL_PROFILE.torch_version}",
                support_command,
            )
            self.assertEqual(
                manager.runtime_manifest()["profile_id"],
                QWEN_CUDA_BLACKWELL_PROFILE.profile_id,
            )
            self.assertEqual(
                manager.dependency_dir,
                manager.profile_dependency_dir(QWEN_CUDA_BLACKWELL_PROFILE),
            )
            self.assertTrue(manager.has_runtime_for_detection(blackwell))

    def test_legacy_cuda_runtime_migrates_without_pip_downloads(self) -> None:
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
            (manager.legacy_dependency_dir / "qwen_tts").mkdir(parents=True)
            (manager.legacy_dependency_dir / "faster_qwen3_tts").mkdir()
            manager._write_json_atomic(
                manager.runtime_manifest_path,
                {
                    "engine": "qwen",
                    "state": "installed",
                    "runtime_version": "qwen3-tts-fast-deps-v1",
                    "backend": "cuda",
                    "requirements": [
                        "torch==2.6.0 (https://download.pytorch.org/whl/cu126)",
                        "faster-qwen3-tts==0.3.0",
                    ],
                },
            )
            legacy = GPUDetectionResult(
                gpus=[
                    GPUInfo(
                        name="NVIDIA GeForce RTX 3070",
                        compute_capability="8.6",
                    )
                ]
            )
            runtime_info = {
                "torch_version": "2.6.0+cu126",
                "torch_cuda_version": "12.6",
                "cuda_available": True,
                "cuda_kernel_test": True,
            }
            with (
                patch(
                    "app.tts.qwen_manager.detect_gpus",
                    return_value=legacy,
                ),
                patch.object(
                    manager,
                    "_validate_runtime",
                    return_value=runtime_info,
                ),
                patch.object(manager, "_run_pip") as run_pip,
            ):
                manager._install_runtime_dependencies(
                    lambda _current, _total, _message: None,
                    None,
                )
            manager._write_cli()

            run_pip.assert_not_called()
            self.assertFalse(manager.legacy_dependency_dir.exists())
            self.assertTrue(
                (
                    manager.profile_dependency_dir(QWEN_CUDA_LEGACY_PROFILE)
                    / "qwen_tts"
                ).is_dir()
            )
            self.assertEqual(
                manager.runtime_manifest()["profile_id"],
                QWEN_CUDA_LEGACY_PROFILE.profile_id,
            )

    def test_cached_profile_can_be_reactivated_after_hardware_change(self) -> None:
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
            legacy_dependency_dir = manager.profile_dependency_dir(
                QWEN_CUDA_LEGACY_PROFILE
            )
            (legacy_dependency_dir / "qwen_tts").mkdir(parents=True)
            (legacy_dependency_dir / "faster_qwen3_tts").mkdir()
            manager._write_runtime_manifest(
                "installed",
                ["torch==2.6.0"],
                QWEN_CUDA_LEGACY_PROFILE,
                QWEN_CUDA_LEGACY_PROFILE,
                "NVIDIA GeForce RTX 3070",
                {
                    "torch_version": "2.6.0+cu126",
                    "torch_cuda_version": "12.6",
                    "cuda_available": True,
                    "cuda_kernel_test": True,
                },
            )
            blackwell_root = manager.profile_root(
                QWEN_CUDA_BLACKWELL_PROFILE
            )
            blackwell_dependency_dir = manager.profile_dependency_dir(
                QWEN_CUDA_BLACKWELL_PROFILE
            )
            (blackwell_dependency_dir / "qwen_tts").mkdir(parents=True)
            (blackwell_dependency_dir / "faster_qwen3_tts").mkdir()
            blackwell_runtime_info = {
                "torch_version": "2.11.0+cu130",
                "torch_cuda_version": "13.0",
                "cuda_available": True,
                "cuda_kernel_test": True,
            }
            manager._write_profile_manifest(
                blackwell_root,
                QWEN_CUDA_BLACKWELL_PROFILE,
                ["torch==2.11.0", "faster-qwen3-tts==0.3.0"],
                blackwell_runtime_info,
            )
            blackwell = GPUDetectionResult(
                gpus=[
                    GPUInfo(
                        name="NVIDIA GeForce RTX 5060 Ti",
                        compute_capability="12.0",
                    )
                ]
            )
            with (
                patch(
                    "app.tts.qwen_manager.detect_gpus",
                    return_value=blackwell,
                ),
                patch.object(
                    manager,
                    "_validate_runtime",
                    return_value=blackwell_runtime_info,
                ),
                patch.object(manager, "_run_pip") as run_pip,
            ):
                manager._install_runtime_dependencies(
                    lambda _current, _total, _message: None,
                    None,
                )
            manager._write_cli()

            run_pip.assert_not_called()
            self.assertEqual(
                manager.runtime_manifest()["profile_id"],
                QWEN_CUDA_BLACKWELL_PROFILE.profile_id,
            )
            self.assertTrue(manager.has_runtime_for_detection(blackwell))

    def test_runtime_validation_executes_a_real_cuda_kernel_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            captured_code = ""

            class FakePythonRuntime:
                runtime_dir = root / "runtime"
                python_exe = Path(sys.executable)

                def is_installed(self) -> bool:
                    return True

                def run_python(self, args, _cancel_token):
                    nonlocal captured_code
                    captured_code = str(args[1])
                    return json.dumps(
                        {
                            "torch_version": "2.11.0+cu130",
                            "torch_cuda_version": "13.0",
                            "cuda_available": True,
                            "device_count": 1,
                            "cuda_kernel_test": True,
                            "cuda_kernel_errors": [],
                            "transformers_version": "4.57.3",
                        }
                    )

                def cancel(self) -> None:
                    pass

            manager = QwenManager(
                install_dir=root / "models",
                python_runtime=FakePythonRuntime(),  # type: ignore[arg-type]
            )

            result = manager._validate_runtime(None, root / "dependencies")

            self.assertTrue(result["cuda_kernel_test"])
            self.assertIn("torch.ones(1", captured_code)
            self.assertIn("torch.cuda.synchronize(index)", captured_code)

    def test_cuda_profile_rejects_a_wheel_without_working_kernels(self) -> None:
        with self.assertRaises(PythonRuntimeError):
            QwenManager._validate_profile_runtime(
                QWEN_CUDA_BLACKWELL_PROFILE,
                {
                    "torch_version": "2.11.0+cu130",
                    "torch_cuda_version": "13.0",
                    "cuda_available": True,
                    "cuda_kernel_test": False,
                    "cuda_kernel_errors": ["no kernel image is available"],
                },
            )

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
        self.assertIn("from huggingface_hub import snapshot_download", QWEN_PYTHON_CLI)
        self.assertIn("local_files_only=not allow_download", QWEN_PYTHON_CLI)
        self.assertIn("HF_HUB_OFFLINE", QWEN_PYTHON_CLI)
        self.assertIn("TRANSFORMERS_OFFLINE", QWEN_PYTHON_CLI)
        self.assertIn(
            "configure_model_network_access(allow_download=bool(args.warmup))",
            QWEN_PYTHON_CLI,
        )
        self.assertIn("load_model(model_source", QWEN_PYTHON_CLI)
        self.assertIn("FasterQwen3TTS", QWEN_PYTHON_CLI)
        self.assertIn("generate_custom_voice", QWEN_PYTHON_CLI)
        self.assertIn("generate_voice_clone", QWEN_PYTHON_CLI)
        self.assertIn('clone_kwargs["xvec_only"] = False', QWEN_PYTHON_CLI)
        self.assertIn(
            'clone_kwargs["x_vector_only_mode"] = False',
            QWEN_PYTHON_CLI,
        )
        self.assertIn('subtype="PCM_16"', QWEN_PYTHON_CLI)

    def test_base_model_requires_reference_audio_and_transcript(self) -> None:
        class FakeQwenManager:
            def is_installed(self, _model_id: str | None = None) -> bool:
                return True

            def model_kind(self, _model_id: str) -> str:
                return "voice_clone"

        engine = QwenTTSEngine(FakeQwenManager())  # type: ignore[arg-type]
        config = {
            "model": "base_1_7b",
            "language": "Spanish",
            "device": "auto",
        }

        with self.assertRaisesRegex(
            TTSEngineError,
            "reference audio",
        ):
            engine.validate(config)

        with tempfile.TemporaryDirectory() as temporary_name:
            reference = Path(temporary_name) / "reference.wav"
            reference.write_bytes(b"RIFF")
            config["reference_audio_path"] = str(reference)
            with self.assertRaisesRegex(TTSEngineError, "transcript"):
                engine.validate(config)

            config["reference_text"] = "Texto exacto de la referencia."
            engine.validate(config)

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
