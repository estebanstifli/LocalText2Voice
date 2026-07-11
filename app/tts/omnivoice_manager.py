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

from .python_runtime_manager import PythonRuntimeError, PythonRuntimeManager


class OmniVoiceError(RuntimeError):
    pass


class OmniVoiceCancelled(OmniVoiceError):
    pass


OmniVoiceProgress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class OmniVoiceModel:
    model_id: str
    display_name: str
    repo_id: str
    requires_gpu: bool = True


@dataclass(frozen=True)
class OmniVoiceMode:
    mode_id: str
    display_name: str


OMNIVOICE_PYTHON_CLI = r'''
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
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        emit_info("CUDA requested but PyTorch cannot see a CUDA GPU; using CPU.")
        return "cpu"
    if requested == "mps":
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        emit_info("Apple MPS requested but PyTorch cannot use it; using CPU.")
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
    from omnivoice import OmniVoice

    dtype, resolved_dtype = resolve_dtype(torch, selected_device, dtype_name)
    configure_torch_runtime(torch, selected_device)
    device_map = "cuda:0" if selected_device == "cuda" else selected_device
    load_started = time.perf_counter()
    try:
        model = OmniVoice.from_pretrained(
            model_repo,
            device_map=device_map,
            dtype=dtype,
        )
    except TypeError as exc:
        if "dtype" not in str(exc):
            raise
        emit_info("OmniVoice loader does not accept dtype; retrying without dtype.")
        model = OmniVoice.from_pretrained(model_repo, device_map=device_map)
    emit_timing("model load", load_started)
    return model, resolved_dtype, device_map


def normalize_audio_payload(audio):
    if isinstance(audio, (list, tuple)):
        if not audio:
            raise ValueError("OmniVoice returned an empty audio list.")
        return audio[0]
    return audio


def main() -> int:
    total_started = time.perf_counter()
    parser = argparse.ArgumentParser(description="LocalText2Voice OmniVoice worker")
    parser.add_argument("--model-repo", default="k2-fsa/OmniVoice")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu", "mps"), default="auto")
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
        import omnivoice
        emit_timing("dependency import", import_started)
    except Exception as exc:
        if args.cuda_info:
            print(json.dumps(cuda_info(), ensure_ascii=False), flush=True)
            return 0
        emit_fatal(f"OmniVoice dependencies are missing: {exc}")
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
    emit_info(f"OmniVoice device selected: {selected_device}")

    try:
        try:
            model, resolved_dtype, backend_used = load_model(
                args.model_repo,
                selected_device,
                args.dtype,
            )
        except Exception as exc:
            if args.device == "auto" and selected_device != "cpu":
                emit_info(f"OmniVoice GPU model load failed; retrying CPU: {exc}")
                selected_device = "cpu"
                model, resolved_dtype, backend_used = load_model(
                    args.model_repo,
                    selected_device,
                    "float32",
                )
            else:
                raise
    except Exception as exc:
        emit_fatal(f"OmniVoice model load failed: {exc}")
        return 5

    emit_timing("worker startup", total_started)
    emit(
        {
            "type": "ready",
            "model": args.model_repo,
            "device": selected_device,
            "dtype": resolved_dtype,
            "backend": str(backend_used),
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
            mode = str(request.get("mode", "clone") or "clone").lower()
            generation_kwargs = {"text": text}
            language = str(request.get("language", "") or "").strip()
            if language and language.lower() not in {"auto", "default"}:
                generation_kwargs["language"] = language

            if mode == "clone":
                ref_audio = str(request.get("ref_audio", "")).strip()
                if not ref_audio:
                    raise ValueError("Voice cloning mode requires a reference audio file.")
                generation_kwargs["ref_audio"] = ref_audio
                ref_text = str(request.get("ref_text", "")).strip()
                if ref_text:
                    generation_kwargs["ref_text"] = ref_text
            elif mode == "design":
                instruct = str(request.get("instruct", "")).strip()
                if instruct:
                    generation_kwargs["instruct"] = instruct
            elif mode != "auto":
                raise ValueError(f"Unsupported OmniVoice mode: {mode}")

            for key in ("num_step", "speed", "duration"):
                value = request.get(key)
                if value is not None and value != "":
                    generation_kwargs[key] = value

            synth_started = time.perf_counter()
            audio = model.generate(**generation_kwargs)
            emit_timing("synthesis", synth_started, request_id)

            write_started = time.perf_counter()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(
                str(output_path),
                normalize_audio_payload(audio),
                24000,
                subtype="PCM_16",
            )
            emit_timing("write wav", write_started, request_id)
            emit_timing("request total", request_started, request_id)
            emit({"type": "result", "id": request_id, "output": str(output_path)})
        except Exception as exc:
            emit({"type": "error", "id": request_id, "message": f"OmniVoice synthesis failed: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.strip() + "\n"


class OmniVoiceManager:
    VERSION = "omnivoice-v1"
    RUNTIME_VERSION = "omnivoice-python-deps-v1"
    INSTALL_FILENAME = "omnivoice-install.json"
    RUNTIME_INSTALL_FILENAME = "omnivoice-runtime-install.json"
    CLI_FILENAME = "omnivoice_worker.py"
    OMNIVOICE_PACKAGE = "omnivoice"
    MODEL_REPO = "k2-fsa/OmniVoice"
    TORCH_VERSION = "2.8.0"
    GPU_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
    MODELS: tuple[OmniVoiceModel, ...] = (
        OmniVoiceModel(
            "omnivoice",
            "OmniVoice",
            MODEL_REPO,
            True,
        ),
    )
    MODES: tuple[OmniVoiceMode, ...] = (
        OmniVoiceMode("design", "Voice design"),
        OmniVoiceMode("clone", "Voice cloning"),
        OmniVoiceMode("auto", "Auto voice"),
    )
    SUPPORT_REQUIREMENTS = (
        "omnivoice",
        "soundfile",
        "huggingface-hub",
    )

    def __init__(
        self,
        install_dir: Path | None = None,
        python_runtime: PythonRuntimeManager | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.install_dir = install_dir or app_data_root() / "models" / "omnivoice"
        self.cache_dir = self.install_dir / "hf-cache"
        self.python_runtime = python_runtime or PythonRuntimeManager()
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def is_installed(self) -> bool:
        manifest = self.install_manifest()
        return (
            manifest.get("state") == "installed"
            and manifest.get("version") == self.VERSION
            and self.cache_dir.exists()
            and self.has_runtime()
        )

    def has_runtime(self) -> bool:
        manifest = self.runtime_manifest()
        return (
            self.python_runtime.is_installed()
            and manifest.get("state") == "installed"
            and manifest.get("runtime_version") == self.RUNTIME_VERSION
            and self.cli_path.is_file()
            and (self.dependency_dir / "omnivoice").is_dir()
        )

    def runtime_is_current(self) -> bool:
        return self.has_runtime()

    def install_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.manifest_path)

    def runtime_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.runtime_manifest_path)

    def list_models(self) -> list[OmniVoiceModel]:
        return list(self.MODELS)

    def list_modes(self) -> list[OmniVoiceMode]:
        return list(self.MODES)

    def model_repo(self, model_id: str) -> str:
        for model in self.MODELS:
            if model.model_id == model_id:
                return model.repo_id
        return self.MODEL_REPO

    def install(
        self,
        model: str = "omnivoice",
        device: str = "auto",
        progress_callback: OmniVoiceProgress | None = None,
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
            progress(82, 100, "Downloading and preparing OmniVoice model cache...")
            warmup_device = device
            try:
                self._run_runtime(
                    ["--warmup", "--model-repo", model_repo, "--device", device],
                    cancel_token,
                )
            except OmniVoiceError as exc:
                if device == "cpu":
                    raise
                progress(
                    90,
                    100,
                    "OmniVoice GPU warmup failed; retrying with CPU: "
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
            progress(100, 100, "OmniVoice is ready.")
            return self.install_dir
        except OmniVoiceCancelled:
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
        from .omnivoice_engine import OmniVoiceTTSEngine

        engine = OmniVoiceTTSEngine(self)
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
        except (OmniVoiceError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def manifest_path(self) -> Path:
        return self.install_dir / self.INSTALL_FILENAME

    @property
    def runtime_manifest_path(self) -> Path:
        return (
            self.python_runtime.runtime_dir
            / "engine-deps"
            / self.RUNTIME_INSTALL_FILENAME
        )

    @property
    def dependency_dir(self) -> Path:
        return (
            self.python_runtime.runtime_dir
            / "engine-deps"
            / "omnivoice"
            / "site-packages"
        )

    @property
    def cli_path(self) -> Path:
        return self.install_dir / self.CLI_FILENAME

    def _install_runtime_dependencies(
        self,
        progress: OmniVoiceProgress,
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
            progress(75, 100, "OmniVoice Python dependencies already installed.")
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
            progress(38, 100, "Installing OmniVoice PyTorch CUDA runtime...")
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
                    "OmniVoice CUDA PyTorch install failed; falling back to CPU: "
                    f"{exc}",
                )

        if backend != "cuda":
            progress(40, 100, "Installing OmniVoice PyTorch CPU runtime...")
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

        progress(56, 100, "Installing OmniVoice Python package...")
        self._run_pip(
            [
                "install",
                "--upgrade",
                "--target",
                str(self.dependency_dir),
                "--no-warn-script-location",
                *self.SUPPORT_REQUIREMENTS,
            ],
            cancel_token,
        )
        requirements.extend(self.SUPPORT_REQUIREMENTS)

        progress(68, 100, "Finalizing OmniVoice PyTorch backend...")
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

        progress(74, 100, "Validating OmniVoice runtime...")
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
            "import torch, torchaudio, soundfile, omnivoice; "
            "print(json.dumps({"
            "'torch_version': torch.__version__, "
            "'torch_cuda_version': torch.version.cuda, "
            "'cuda_available': torch.cuda.is_available(), "
            "'device_count': torch.cuda.device_count()"
            "}))"
        )
        output = self.python_runtime.run_python(["-c", code], cancel_token)
        lines = [line for line in output.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1] if lines else "{}")
        except json.JSONDecodeError as exc:
            raise PythonRuntimeError(
                f"Could not validate OmniVoice runtime: {output}"
            ) from exc
        return payload if isinstance(payload, dict) else {}

    def _run_runtime(
        self,
        args: list[str],
        cancel_token: threading.Event | None = None,
    ) -> str:
        if not self.python_runtime.is_installed():
            raise OmniVoiceError("Embedded Python runtime is not installed.")
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
            raise OmniVoiceError(f"Could not start OmniVoice: {exc}") from exc
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
            raise OmniVoiceError(
                f"OmniVoice failed with exit code {process.returncode}: "
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
                raise OmniVoiceError(str(data.get("message", "Unknown error.")))
        return stdout_text

    def _write_cli(self) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cli_path.write_text(OMNIVOICE_PYTHON_CLI, encoding="utf-8")

    def _write_manifest(self, state: str, model: str, device: str) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "engine": "omnivoice",
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
            "engine": "omnivoice",
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
            raise OmniVoiceCancelled("OmniVoice operation cancelled.")

    @staticmethod
    def _runtime_json_error(output: str) -> str:
        for line in reversed([line for line in output.splitlines() if line.strip()]):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("type") in {"fatal", "error"}:
                return str(data.get("message", "Unknown OmniVoice error."))
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
        if not self.dependency_dir.exists():
            return
        for child in self.dependency_dir.iterdir():
            child_name = child.name.lower().replace("-", "_")
            stem = child_name.split(".dist_info", 1)[0].split(".egg_info", 1)[0]
            if (
                child_name in normalized
                or stem in normalized
                or any(child_name.startswith(f"{name}_") for name in normalized)
            ):
                self._remove_path(child)
