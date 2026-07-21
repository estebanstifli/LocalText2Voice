from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.utils.gpu_detection import detect_gpus, format_gpu_detection
from app.utils.paths import app_data_root

from .model_cache import huggingface_model_is_cached
from .python_runtime_manager import PythonRuntimeError, PythonRuntimeManager


class QwenError(RuntimeError):
    pass


class QwenCancelled(QwenError):
    pass


QwenProgress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class QwenModel:
    model_id: str
    display_name: str
    repo_id: str
    kind: str
    requires_gpu: bool = True


@dataclass(frozen=True)
class QwenVoice:
    voice_id: str
    display_name: str


@dataclass(frozen=True)
class QwenLanguage:
    language_id: str
    display_name: str


QWEN_PYTHON_CLI = r'''
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from pathlib import Path


def emit(message: dict) -> None:
    print(json.dumps(message, ensure_ascii=False), flush=True)


def emit_timing(label: str, started_at: float, request_id: str | None = None) -> None:
    payload = {"type": "timing", "label": label, "elapsed": time.perf_counter() - started_at}
    if request_id is not None:
        payload["id"] = request_id
    emit(payload)


def emit_info(message: str) -> None:
    emit({"type": "info", "message": message})


def emit_fatal(message: str) -> None:
    emit({"type": "fatal", "message": message})


def configure_environment(cache_dir: str, deps_dir: str) -> None:
    deps_path = Path(deps_dir)
    if str(deps_path) not in sys.path:
        sys.path.insert(0, str(deps_path))
    add_dll_search_paths(deps_path)
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_path)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache_path / "hub")
    os.environ["TRANSFORMERS_CACHE"] = str(cache_path / "hub")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    warnings.filterwarnings("ignore", category=FutureWarning)


def add_dll_search_paths(deps_path: Path) -> None:
    candidates = [
        deps_path,
        deps_path / "torch" / "lib",
        deps_path / "torchaudio" / "lib",
    ]
    for nvidia_dir in (deps_path / "nvidia").glob("*"):
        candidates.append(nvidia_dir / "bin")
        candidates.append(nvidia_dir / "lib")

    path_parts = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        path_parts.append(str(candidate))
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(candidate))
            except OSError:
                pass
    if path_parts:
        os.environ["PATH"] = os.pathsep.join(
            [*path_parts, os.environ.get("PATH", "")]
        )


def cuda_info() -> dict[str, object]:
    try:
        import torch
    except Exception as exc:
        return {
            "torch_available": False,
            "cuda_available": False,
            "device_count": 0,
            "error": str(exc),
        }
    devices = []
    for index in range(torch.cuda.device_count()):
        try:
            props = torch.cuda.get_device_properties(index)
            devices.append(
                {
                    "index": index,
                    "name": torch.cuda.get_device_name(index),
                    "total_memory_gb": props.total_memory / (1024**3),
                    "capability": f"{props.major}.{props.minor}",
                }
            )
        except Exception as exc:
            devices.append({"index": index, "error": str(exc)})
    return {
        "torch_available": True,
        "torch_version": getattr(torch, "__version__", ""),
        "torch_cuda_version": getattr(torch.version, "cuda", None),
        "cuda_available": bool(torch.cuda.is_available()),
        "device_count": int(torch.cuda.device_count()),
        "devices": devices,
        "error": "",
    }


def resolve_device(torch, requested: str) -> str:
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        emit_info("CUDA requested but PyTorch cannot see a CUDA GPU; using CPU.")
        return "cpu"
    return requested


def resolve_dtype(torch, selected_device: str, requested: str):
    value = (requested or "auto").lower()
    if value in {"bf16", "bfloat16"}:
        return torch.bfloat16, "bfloat16"
    if value in {"fp16", "float16", "half"}:
        return torch.float16, "float16"
    if value in {"fp32", "float32"}:
        return torch.float32, "float32"
    if selected_device == "cuda":
        if getattr(torch.cuda, "is_bf16_supported", lambda: False)():
            return torch.bfloat16, "bfloat16"
        return torch.float16, "float16"
    return torch.float32, "float32"


def configure_torch_runtime(torch, selected_device: str) -> None:
    if selected_device != "cuda":
        return
    try:
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
        if hasattr(torch, "set_float32_matmul_precision"):
            torch.set_float32_matmul_precision("high")
        emit_info("CUDA optimizations enabled: TF32 matmul and cuDNN benchmark.")
    except Exception as exc:
        emit_info(f"Could not enable CUDA optimizations: {exc}")


def load_model(model_repo: str, selected_device: str, dtype_name: str):
    import torch

    dtype, resolved_dtype = resolve_dtype(torch, selected_device, dtype_name)
    configure_torch_runtime(torch, selected_device)
    if selected_device == "cuda":
        from faster_qwen3_tts import FasterQwen3TTS

        emit_info(
            "Qwen load options: "
            f"backend=faster-qwen3-tts, device=cuda, "
            f"dtype={resolved_dtype}, attention=sdpa"
        )
        load_started = time.perf_counter()
        model = FasterQwen3TTS.from_pretrained(
            model_repo,
            device="cuda",
            dtype=dtype,
            attn_implementation="sdpa",
            max_seq_len=2048,
        )
        emit_timing("model load", load_started)
        return model, resolved_dtype, "faster-qwen3-tts/cuda"

    from qwen_tts import Qwen3TTSModel

    kwargs = {
        "device_map": "cpu",
        "dtype": dtype,
        "attn_implementation": "sdpa",
    }
    emit_info(
        "Qwen load options: "
        f"backend=official-qwen-tts, device={kwargs['device_map']}, "
        f"dtype={resolved_dtype}, "
        "attention=sdpa"
    )
    load_started = time.perf_counter()
    try:
        model = Qwen3TTSModel.from_pretrained(model_repo, **kwargs)
    except TypeError as exc:
        if "attn_implementation" not in str(exc):
            raise
        emit_info("Qwen loader does not accept attn_implementation; retrying default attention.")
        kwargs.pop("attn_implementation", None)
        model = Qwen3TTSModel.from_pretrained(model_repo, **kwargs)
    emit_timing("model load", load_started)
    return model, resolved_dtype, "official-qwen-tts/cpu"


def supported_values(model) -> tuple[list[str], list[str]]:
    languages: list[str] = []
    speakers: list[str] = []
    candidates = [
        model,
        getattr(model, "model", None),
        getattr(getattr(model, "model", None), "model", None),
    ]
    try:
        for candidate in candidates:
            getter = getattr(candidate, "get_supported_languages", None)
            if callable(getter):
                languages = [str(item) for item in (getter() or [])]
                if languages:
                    break
    except Exception as exc:
        emit_info(f"Could not read Qwen languages: {exc}")
    try:
        for candidate in candidates:
            getter = getattr(candidate, "get_supported_speakers", None)
            if callable(getter):
                speakers = [str(item) for item in (getter() or [])]
                if speakers:
                    break
    except Exception as exc:
        emit_info(f"Could not read Qwen speakers: {exc}")
    return languages, speakers


def main() -> int:
    total_started = time.perf_counter()
    parser = argparse.ArgumentParser(description="LocalText2Voice Qwen TTS worker")
    parser.add_argument("--model-repo", default="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--dtype", choices=("auto", "bfloat16", "bf16", "float16", "fp16", "float32", "fp32"), default="auto")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--deps-dir", required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--cuda-info", action="store_true")
    args = parser.parse_args()

    configure_environment(args.cache_dir, args.deps_dir)

    try:
        import_started = time.perf_counter()
        import torch
        import soundfile as sf
        import qwen_tts
        import faster_qwen3_tts
        emit_timing("dependency import", import_started)
    except Exception as exc:
        if args.cuda_info:
            print(json.dumps(cuda_info(), ensure_ascii=False), flush=True)
            return 0
        emit_fatal(f"Qwen dependencies are missing: {exc}")
        return 3

    if args.cuda_info:
        print(json.dumps(cuda_info(), ensure_ascii=False), flush=True)
        return 0

    selected_device = resolve_device(torch, args.device)
    emit_info(
        "PyTorch: "
        f"{getattr(torch, '__version__', 'unknown')}, "
        f"CUDA build: {getattr(torch.version, 'cuda', None) or 'CPU'}"
    )
    if selected_device == "cuda":
        try:
            index = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(index)
            emit_info(
                "CUDA device: "
                f"{torch.cuda.get_device_name(index)}, "
                f"{props.total_memory / (1024**3):.1f} GB VRAM"
            )
        except Exception as exc:
            emit_info(f"Could not read CUDA device details: {exc}")
    emit_info(f"Qwen device selected: {selected_device}")

    try:
        try:
            model, resolved_dtype, backend_used = load_model(args.model_repo, selected_device, args.dtype)
        except Exception as exc:
            if args.device == "auto" and selected_device != "cpu":
                emit_info(f"CUDA model load failed; retrying CPU: {exc}")
                selected_device = "cpu"
                model, resolved_dtype, backend_used = load_model(args.model_repo, selected_device, "float32")
            else:
                raise
    except Exception as exc:
        emit_fatal(f"Qwen model load failed: {exc}")
        return 5

    languages, speakers = supported_values(model)
    if languages:
        emit_info(f"Qwen languages: {', '.join(languages)}")
    if speakers:
        emit_info(f"Qwen speakers: {', '.join(speakers)}")
    emit_timing("worker startup", total_started)
    emit(
        {
            "type": "ready",
            "model": args.model_repo,
            "device": selected_device,
            "dtype": resolved_dtype,
            "backend": backend_used,
            "languages": languages,
            "speakers": speakers,
        }
    )

    if args.warmup and not args.worker:
        return 0

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            emit({"type": "error", "id": "", "message": f"Invalid JSON request: {exc}"})
            continue

        request_id = str(request.get("id", ""))
        request_type = str(request.get("type", "synthesize"))
        if request_type == "shutdown":
            emit({"type": "shutdown"})
            return 0
        if request_type != "synthesize":
            emit({"type": "error", "id": request_id, "message": f"Unknown request type: {request_type}"})
            continue

        request_started = time.perf_counter()
        try:
            text = str(request.get("text", "")).strip()
            if not text:
                raise ValueError("Input text is empty.")
            output_path = Path(str(request["output"]))
            language = str(request.get("language", "Spanish"))
            speaker = str(request.get("speaker", "Serena"))
            instruct = str(request.get("instruct", "")).strip() or None
            generation_kwargs = {}
            for key in ("temperature", "top_k", "top_p", "repetition_penalty", "max_new_tokens"):
                value = request.get(key)
                if value is not None:
                    generation_kwargs[key] = value

            synth_started = time.perf_counter()
            wavs, sample_rate = model.generate_custom_voice(
                text=text,
                language=language,
                speaker=speaker,
                instruct=instruct,
                **generation_kwargs,
            )
            emit_timing("synthesis", synth_started, request_id)

            write_started = time.perf_counter()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(output_path), wavs[0], int(sample_rate), subtype="PCM_16")
            emit_timing("write wav", write_started, request_id)
            emit_timing("request total", request_started, request_id)
            emit({"type": "result", "id": request_id, "output": str(output_path)})
        except Exception as exc:
            emit({"type": "error", "id": request_id, "message": f"Qwen synthesis failed: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.strip() + "\n"


class QwenManager:
    VERSION = "qwen3-tts-v1"
    RUNTIME_VERSION = "qwen3-tts-fast-deps-v1"
    INSTALL_FILENAME = "qwen-install.json"
    RUNTIME_INSTALL_FILENAME = "qwen-runtime-install.json"
    CLI_FILENAME = "qwen_worker.py"
    QWEN_PACKAGE = "faster-qwen3-tts==0.3.0"
    UPSTREAM_QWEN_PACKAGE = "qwen-tts==0.1.1"
    TORCH_VERSION = "2.6.0"
    GPU_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu126"
    MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    MODEL_REQUIRED_FILES = {
        "config.json": 1_000,
        "model.safetensors": 100 * 1024 * 1024,
        "speech_tokenizer/config.json": 1_000,
        "speech_tokenizer/model.safetensors": 100 * 1024 * 1024,
    }

    MODELS: tuple[QwenModel, ...] = (
        QwenModel(
            "custom_voice_0_6b",
            "Qwen3 TTS 0.6B CustomVoice Fast",
            MODEL_REPO,
            "custom_voice",
            True,
        ),
    )
    VOICES: tuple[QwenVoice, ...] = (
        QwenVoice("Serena", "Serena"),
        QwenVoice("Vivian", "Vivian"),
        QwenVoice("Uncle_fu", "Uncle Fu"),
        QwenVoice("Ryan", "Ryan"),
        QwenVoice("Aiden", "Aiden"),
        QwenVoice("Ono_anna", "Ono Anna"),
        QwenVoice("Sohee", "Sohee"),
        QwenVoice("Eric", "Eric"),
        QwenVoice("Dylan", "Dylan"),
    )
    LANGUAGES: tuple[QwenLanguage, ...] = (
        QwenLanguage("Chinese", "Chinese"),
        QwenLanguage("English", "English"),
        QwenLanguage("Japanese", "Japanese"),
        QwenLanguage("Korean", "Korean"),
        QwenLanguage("German", "German"),
        QwenLanguage("French", "French"),
        QwenLanguage("Russian", "Russian"),
        QwenLanguage("Portuguese", "Portuguese"),
        QwenLanguage("Spanish", "Spanish"),
        QwenLanguage("Italian", "Italian"),
    )
    SUPPORT_REQUIREMENTS = (
        "faster-qwen3-tts==0.3.0",
        "qwen-tts==0.1.1",
        "transformers==4.57.3",
        "accelerate==1.12.0",
        "librosa",
        "soundfile",
        "onnxruntime",
        "einops",
        "sox",
    )

    def __init__(
        self,
        install_dir: Path | None = None,
        python_runtime: PythonRuntimeManager | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.install_dir = install_dir or app_data_root() / "models" / "qwen"
        self.cache_dir = self.install_dir / "hf-cache"
        self.python_runtime = python_runtime or PythonRuntimeManager()
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def is_installed(self) -> bool:
        return self.has_model_files() and self.has_runtime()

    def has_model_files(self, model_id: str | None = None) -> bool:
        model_repo = self.model_repo(model_id or self.MODELS[0].model_id)
        return huggingface_model_is_cached(
            self.cache_dir,
            model_repo,
            self.MODEL_REQUIRED_FILES,
        )

    def has_runtime(self) -> bool:
        manifest = self.runtime_manifest()
        return (
            self.python_runtime.is_installed()
            and manifest.get("state") == "installed"
            and manifest.get("runtime_version") == self.RUNTIME_VERSION
            and self.cli_path.is_file()
            and (self.dependency_dir / "qwen_tts").is_dir()
            and (self.dependency_dir / "faster_qwen3_tts").is_dir()
        )

    def runtime_is_current(self) -> bool:
        return self.has_runtime()

    def install_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.manifest_path)

    def runtime_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.runtime_manifest_path)

    def list_models(self) -> list[QwenModel]:
        return list(self.MODELS)

    def list_voices(self) -> list[QwenVoice]:
        return list(self.VOICES)

    def list_languages(self) -> list[QwenLanguage]:
        return list(self.LANGUAGES)

    def model_repo(self, model_id: str) -> str:
        for model in self.MODELS:
            if model.model_id == model_id:
                return model.repo_id
        return self.MODEL_REPO

    def install(
        self,
        model: str = "custom_voice_0_6b",
        device: str = "auto",
        progress_callback: QwenProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        model_repo = self.model_repo(model)
        try:
            self._install_runtime_dependencies(progress, cancel_token)
            self._write_cli()
            self._write_manifest("installing", model, device)
            self._remove_path(self.cache_dir / "transformers")
            progress(80, 100, "Downloading and preparing Qwen model cache...")
            warmup_device = device
            try:
                self._run_runtime(
                    ["--warmup", "--model-repo", model_repo, "--device", device],
                    cancel_token,
                )
            except QwenError as exc:
                if device == "cpu":
                    raise
                progress(
                    88,
                    100,
                    "Qwen CUDA warmup failed; retrying with CPU: "
                    f"{exc}",
                )
                warmup_device = "auto"
                self._run_runtime(
                    ["--warmup", "--model-repo", model_repo, "--device", "cpu"],
                    cancel_token,
                )
            if warmup_device != device:
                device = warmup_device
            self._write_manifest("installed", model, device)
            progress(100, 100, "Qwen TTS is ready.")
            return self.install_dir
        except QwenCancelled:
            self._write_manifest("cancelled", model, device)
            raise
        except Exception:
            self._write_manifest("failed", model, device)
            raise

    def uninstall(self) -> None:
        self.cancel()
        self._remove_path(self.install_dir)

    def uninstall_runtime(self) -> None:
        self.cancel()
        self._remove_path(self.runtime_manifest_path)
        self._remove_path(self.dependency_dir)

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        from .qwen_engine import QwenTTSEngine

        engine = QwenTTSEngine(self)
        try:
            return engine.synthesize_to_wav(text, output_path, voice_config)
        finally:
            engine.close()

    def cancel(self) -> None:
        self._cancel_requested.set()
        self.python_runtime.cancel()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def runtime_command(self) -> list[str]:
        self._write_cli()
        return [str(self.python_runtime.python_exe), str(self.cli_path)]

    def runtime_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = str(self.dependency_dir)
        env["HF_HOME"] = str(self.cache_dir)
        env["HUGGINGFACE_HUB_CACHE"] = str(self.cache_dir / "hub")
        env["TRANSFORMERS_CACHE"] = str(self.cache_dir / "hub")
        dll_paths = [
            self.dependency_dir,
            self.dependency_dir / "torch" / "lib",
            self.dependency_dir / "torchaudio" / "lib",
        ]
        for nvidia_dir in (self.dependency_dir / "nvidia").glob("*"):
            dll_paths.append(nvidia_dir / "bin")
            dll_paths.append(nvidia_dir / "lib")
        existing_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(
            [str(path) for path in dll_paths if path.exists()]
            + ([existing_path] if existing_path else [])
        )
        return env

    def cuda_info(self) -> dict[str, Any]:
        if not self.has_runtime():
            return {}
        try:
            output = self._run_runtime(["--cuda-info"])
            lines = [line for line in output.splitlines() if line.strip()]
            data = json.loads(lines[-1] if lines else "{}")
        except (QwenError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def manifest_path(self) -> Path:
        return self.install_dir / self.INSTALL_FILENAME

    @property
    def runtime_manifest_path(self) -> Path:
        return self.python_runtime.runtime_dir / "engine-deps" / self.RUNTIME_INSTALL_FILENAME

    @property
    def dependency_dir(self) -> Path:
        return self.python_runtime.runtime_dir / "engine-deps" / "qwen" / "site-packages"

    @property
    def cli_path(self) -> Path:
        return self.install_dir / self.CLI_FILENAME

    @property
    def runtime_path(self) -> Path:
        return self.python_runtime.python_exe

    def _install_runtime_dependencies(
        self,
        progress: QwenProgress,
        cancel_token: threading.Event | None,
    ) -> None:
        if not self.python_runtime.is_installed():
            self.python_runtime.install(
                lambda current, total, message: progress(
                    int((current / total) * 25) if total else 0,
                    100,
                    message,
                ),
                cancel_token,
            )
        if self.has_runtime():
            progress(75, 100, "Qwen TTS Python dependencies already installed.")
            return

        gpu_detection = detect_gpus()
        gpu_summary = format_gpu_detection(gpu_detection)
        progress(30, 100, gpu_summary.splitlines()[0])

        self._remove_path(self.dependency_dir)
        self.dependency_dir.mkdir(parents=True, exist_ok=True)
        requirements: list[str] = []
        backend = "cpu"

        torch_requirements = [
            f"torch=={self.TORCH_VERSION}",
            f"torchaudio=={self.TORCH_VERSION}",
        ]
        if gpu_detection.has_nvidia_gpu:
            progress(38, 100, "Installing Qwen PyTorch CUDA runtime...")
            try:
                self._run_pip(
                    [
                        "install",
                        "--upgrade",
                        "--target",
                        str(self.dependency_dir),
                        "--no-warn-script-location",
                        "--index-url",
                        self.GPU_TORCH_INDEX_URL,
                        "--extra-index-url",
                        "https://pypi.org/simple",
                        *torch_requirements,
                    ],
                    cancel_token,
                )
                requirements.extend(
                    [
                        f"torch=={self.TORCH_VERSION} ({self.GPU_TORCH_INDEX_URL})",
                        f"torchaudio=={self.TORCH_VERSION} ({self.GPU_TORCH_INDEX_URL})",
                    ]
                )
                backend = "cuda"
            except PythonRuntimeError as exc:
                progress(
                    42,
                    100,
                    "Qwen CUDA PyTorch install failed; falling back to CPU: "
                    f"{exc}",
                )

        if backend != "cuda":
            progress(40, 100, "Installing Qwen PyTorch CPU runtime...")
            self._run_pip(
                [
                    "install",
                    "--upgrade",
                    "--target",
                    str(self.dependency_dir),
                    "--no-warn-script-location",
                    *torch_requirements,
                ],
                cancel_token,
            )
            requirements.extend(torch_requirements)

        progress(55, 100, "Installing Qwen TTS Python dependencies...")
        self._run_pip(
            [
                "install",
                "--upgrade",
                "--target",
                str(self.dependency_dir),
                "--no-warn-script-location",
                "--no-deps",
                self.QWEN_PACKAGE,
            ],
            cancel_token,
        )
        self._run_pip(
            [
                "install",
                "--upgrade",
                "--target",
                str(self.dependency_dir),
                "--no-warn-script-location",
                *self.SUPPORT_REQUIREMENTS[1:],
            ],
            cancel_token,
        )
        requirements.append(self.QWEN_PACKAGE)
        requirements.extend(self.SUPPORT_REQUIREMENTS[1:])

        # Some support packages declare broad torch dependencies. With
        # pip --target, resolver calls do not reliably treat the target folder
        # as an installed environment, so a later support install can overwrite
        # the CUDA PyTorch wheel. Put the selected torch backend back last.
        progress(67, 100, "Finalizing Qwen PyTorch backend...")
        self._remove_python_package_artifacts(
            "torch",
            "torchaudio",
            "functorch",
            "triton",
            "nvidia",
        )
        if backend == "cuda":
            self._run_pip(
                [
                    "install",
                    "--upgrade",
                    "--force-reinstall",
                    "--target",
                    str(self.dependency_dir),
                    "--no-warn-script-location",
                    "--index-url",
                    self.GPU_TORCH_INDEX_URL,
                    "--extra-index-url",
                    "https://pypi.org/simple",
                    *torch_requirements,
                ],
                cancel_token,
            )
        else:
            self._run_pip(
                [
                    "install",
                    "--upgrade",
                    "--force-reinstall",
                    "--target",
                    str(self.dependency_dir),
                    "--no-warn-script-location",
                    *torch_requirements,
                ],
                cancel_token,
            )

        progress(72, 100, "Validating Qwen TTS runtime...")
        runtime_info = self._validate_runtime(cancel_token)
        if runtime_info.get("cuda_available"):
            backend = "cuda"
        elif backend == "cuda":
            backend = "cpu"
        self._write_runtime_manifest(
            "installed",
            requirements,
            backend,
            gpu_summary,
            runtime_info,
        )

    def _run_pip(
        self,
        args: list[str],
        cancel_token: threading.Event | None,
    ) -> str:
        return self.python_runtime.run_python(["-m", "pip", *args], cancel_token)

    def _validate_runtime(
        self,
        cancel_token: threading.Event | None,
    ) -> dict[str, Any]:
        code = (
            "import json, os, sys; from pathlib import Path; "
            f"deps=Path({str(self.dependency_dir)!r}); "
            "sys.path.insert(0, str(deps)); "
            "dlls=[deps, deps/'torch'/'lib', deps/'torchaudio'/'lib']; "
            "nvidia=deps/'nvidia'; "
            "dlls += [child/'bin' for child in nvidia.glob('*')]; "
            "dlls += [child/'lib' for child in nvidia.glob('*')]; "
            "paths=[]; "
            "\nfor dll in dlls:\n"
            "    if dll.exists():\n"
            "        paths.append(str(dll))\n"
            "        add_dir=getattr(os, 'add_dll_directory', None)\n"
            "        if add_dir is not None:\n"
            "            try:\n"
            "                add_dir(str(dll))\n"
            "            except OSError:\n"
            "                pass\n"
            "os.environ['PATH']=os.pathsep.join(paths+[os.environ.get('PATH','')]); "
            "import torch, torchaudio, transformers, qwen_tts, faster_qwen3_tts, soundfile, onnxruntime, einops; "
            "print(json.dumps({"
            "'torch_version': torch.__version__, "
            "'torch_cuda_version': torch.version.cuda, "
            "'cuda_available': torch.cuda.is_available(), "
            "'device_count': torch.cuda.device_count(), "
            "'transformers_version': transformers.__version__"
            "}))"
        )
        output = self.python_runtime.run_python(["-c", code], cancel_token)
        lines = [line for line in output.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1] if lines else "{}")
        except json.JSONDecodeError as exc:
            raise PythonRuntimeError(f"Could not validate Qwen runtime: {output}") from exc
        return payload if isinstance(payload, dict) else {}

    def _run_runtime(
        self,
        args: list[str],
        cancel_token: threading.Event | None = None,
    ) -> str:
        if not self.python_runtime.is_installed():
            raise QwenError("Embedded Python runtime is not installed.")
        command = [
            *self.runtime_command(),
            *args,
            "--cache-dir",
            str(self.cache_dir),
            "--deps-dir",
            str(self.dependency_dir),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.runtime_environment(),
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except OSError as exc:
            raise QwenError(f"Could not start Qwen TTS: {exc}") from exc
        with self._lock:
            self._process = process
        stdout = b""
        stderr = b""
        try:
            while True:
                self._check_cancelled(cancel_token)
                try:
                    stdout, stderr = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            details = self._runtime_json_error(stdout_text)
            if not details:
                details = self._clean_runtime_stderr(stderr_text)
            if not details:
                details = stdout_text
            raise QwenError(
                f"Qwen TTS failed with exit code {process.returncode}: "
                f"{details or 'No error details were returned.'}"
            )
        if "--cuda-info" in args:
            return stdout_text
        for line in reversed([line for line in stdout_text.splitlines() if line.strip()]):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("type") == "fatal":
                raise QwenError(str(data.get("message", "Unknown error.")))
        return stdout_text

    def _write_cli(self) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cli_path.write_text(QWEN_PYTHON_CLI, encoding="utf-8")

    def _write_manifest(self, state: str, model: str, device: str) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "engine": "qwen",
            "version": self.VERSION,
            "state": state,
            "model": model,
            "model_repo": self.model_repo(model),
            "device": device,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cache_dir": str(self.cache_dir),
            "runtime": "python",
            "python_runtime": str(self.python_runtime.python_exe),
        }
        self._write_json_atomic(self.manifest_path, manifest)

    def _write_runtime_manifest(
        self,
        state: str,
        requirements: list[str],
        backend: str,
        gpu_summary: str,
        runtime_info: dict[str, Any],
    ) -> None:
        manifest = {
            "engine": "qwen",
            "runtime_version": self.RUNTIME_VERSION,
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "requirements": requirements,
            "backend": backend,
            "gpu_detection": gpu_summary,
            "runtime_info": runtime_info,
            "dependency_dir": str(self.dependency_dir),
            "python_runtime": str(self.python_runtime.python_exe),
        }
        self._write_json_atomic(self.runtime_manifest_path, manifest)

    def _check_cancelled(self, cancel_token: threading.Event | None = None) -> None:
        if self._cancel_requested.is_set() or (
            cancel_token is not None and cancel_token.is_set()
        ):
            raise QwenCancelled("Qwen TTS operation cancelled.")

    @staticmethod
    def _runtime_json_error(output: str) -> str:
        for line in reversed([line for line in output.splitlines() if line.strip()]):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("type") in {"fatal", "error"}:
                return str(data.get("message", "Unknown Qwen TTS error."))
        return ""

    @staticmethod
    def _clean_runtime_stderr(stderr_text: str) -> str:
        noisy_fragments = (
            "Fetching ",
            "FutureWarning:",
            "UserWarning:",
            "WARNING:",
            "HF Hub",
            "HF_TOKEN",
            "deprecated",
        )
        useful_lines = []
        for line in stderr_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if any(fragment in stripped for fragment in noisy_fragments):
                continue
            useful_lines.append(stripped)
        return "\n".join(useful_lines).strip()

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            import shutil

            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def _remove_python_package_artifacts(self, *package_names: str) -> None:
        normalized = {name.lower().replace("-", "_") for name in package_names}
        for child in self.dependency_dir.iterdir():
            child_name = child.name.lower().replace("-", "_")
            stem = child_name.split(".dist_info", 1)[0].split(".egg_info", 1)[0]
            if (
                child_name in normalized
                or stem in normalized
                or any(child_name.startswith(f"{name}_") for name in normalized)
            ):
                self._remove_path(child)
