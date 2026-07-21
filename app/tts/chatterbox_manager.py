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


class ChatterboxError(RuntimeError):
    pass


class ChatterboxCancelled(ChatterboxError):
    pass


ChatterboxProgress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class ChatterboxModel:
    model_id: str
    display_name: str
    requires_reference: bool
    supports_language: bool


@dataclass(frozen=True)
class ChatterboxLanguage:
    language_id: str
    display_name: str


CHATTERBOX_PYTHON_CLI = r'''
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


def configure_cache(cache_dir: str) -> None:
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_path)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache_path / "hub")
    os.environ["TRANSFORMERS_CACHE"] = str(cache_path / "transformers")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings(
        "ignore",
        message="pkg_resources is deprecated as an API.*",
        category=UserWarning,
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

    info: dict[str, object] = {
        "torch_available": True,
        "torch_version": getattr(torch, "__version__", ""),
        "torch_cuda_version": getattr(torch.version, "cuda", None),
        "cuda_available": bool(torch.cuda.is_available()),
        "device_count": int(torch.cuda.device_count()),
        "devices": [],
        "error": "",
    }
    devices: list[dict[str, object]] = []
    for index in range(torch.cuda.device_count()):
        try:
            properties = torch.cuda.get_device_properties(index)
            devices.append(
                {
                    "index": index,
                    "name": torch.cuda.get_device_name(index),
                    "total_memory_gb": properties.total_memory / (1024**3),
                    "capability": f"{properties.major}.{properties.minor}",
                }
            )
        except Exception as exc:
            devices.append({"index": index, "error": str(exc)})
    info["devices"] = devices
    return info


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
    if requested == "mps" and not (
        hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
    ):
        emit_info("MPS requested but unavailable; using CPU.")
        return "cpu"
    return requested


def load_model(model_id: str, device: str):
    if model_id == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        return ChatterboxTurboTTS.from_pretrained(device=device)
    if model_id == "multilingual_v3":
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        return ChatterboxMultilingualTTS.from_pretrained(device=device)
    if model_id == "english":
        from chatterbox.tts import ChatterboxTTS

        return ChatterboxTTS.from_pretrained(device=device)
    raise ValueError(f"Unknown Chatterbox model: {model_id}")


def synthesize(model, model_id: str, request: dict):
    text = str(request.get("text", "")).strip()
    if not text:
        raise ValueError("Input text is empty.")
    reference = str(request.get("reference_audio_path", "")).strip() or None
    if model_id == "turbo" and not reference:
        raise ValueError("Chatterbox Turbo requires a reference audio file.")
    kwargs = {
        "audio_prompt_path": reference,
        "exaggeration": float(request.get("exaggeration", 0.5)),
        "cfg_weight": float(request.get("cfg_weight", 0.5)),
    }
    if model_id == "multilingual_v3":
        return model.generate(
            text,
            language_id=str(request.get("language", "en")),
            **kwargs,
        )
    return model.generate(text, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalText2Voice Chatterbox worker")
    parser.add_argument("--model", choices=("multilingual_v3", "english", "turbo"), default="multilingual_v3")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu", "mps"), default="auto")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--cuda-info", action="store_true")
    args = parser.parse_args()

    configure_cache(args.cache_dir)
    try:
        import_started = time.perf_counter()
        import torch
        import torchaudio as ta
        emit_timing("dependency import", import_started)
    except Exception as exc:
        if args.cuda_info:
            print(json.dumps(cuda_info(), ensure_ascii=False))
            return 0
        emit_fatal(f"Chatterbox dependencies are missing: {exc}")
        return 3

    if args.cuda_info:
        print(json.dumps(cuda_info(), ensure_ascii=False))
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
    emit_info(f"Chatterbox device selected: {selected_device}")

    try:
        load_started = time.perf_counter()
        try:
            model = load_model(args.model, selected_device)
        except Exception as exc:
            if args.device == "auto" and selected_device != "cpu":
                emit_info(f"{selected_device} model load failed; retrying CPU: {exc}")
                selected_device = "cpu"
                model = load_model(args.model, selected_device)
            else:
                raise
        emit_timing("model load", load_started)
    except Exception as exc:
        emit_fatal(f"Chatterbox model load failed: {exc}")
        return 5

    if args.warmup and not args.worker:
        emit({"type": "ready", "model": args.model, "device": selected_device})
        return 0

    emit({"type": "ready", "model": args.model, "device": selected_device})

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
            output_path = Path(str(request["output"]))
            synth_started = time.perf_counter()
            try:
                wav = synthesize(model, args.model, request)
            except Exception as exc:
                if args.device == "auto" and selected_device != "cpu":
                    emit_info(f"{selected_device} synthesis failed; retrying CPU: {exc}")
                    selected_device = "cpu"
                    load_started = time.perf_counter()
                    model = load_model(args.model, selected_device)
                    emit_timing("model load CPU fallback", load_started)
                    synth_started = time.perf_counter()
                    wav = synthesize(model, args.model, request)
                else:
                    raise
            emit_timing("synthesis", synth_started, request_id)

            write_started = time.perf_counter()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            ta.save(
                str(output_path),
                wav,
                model.sr,
                encoding="PCM_S",
                bits_per_sample=16,
            )
            emit_timing("write wav", write_started, request_id)
            emit_timing("request total", request_started, request_id)
            emit({"type": "result", "id": request_id, "output": str(output_path)})
        except Exception as exc:
            emit({"type": "error", "id": request_id, "message": f"Chatterbox synthesis failed: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.strip() + "\n"


class ChatterboxManager:
    VERSION = "v2-python-worker"
    RUNTIME_VERSION = "chatterbox-python-deps-v3"
    INSTALL_FILENAME = "chatterbox-install.json"
    RUNTIME_INSTALL_FILENAME = "chatterbox-runtime-install.json"
    CLI_FILENAME = "chatterbox_worker.py"
    CHATTERBOX_PACKAGE = "chatterbox-tts==0.1.7"
    SUPPORT_PACKAGES = ("setuptools==80.9.0",)
    TORCH_VERSION = "2.6.0"
    GPU_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu126"
    MODEL_REPOSITORIES: dict[str, tuple[str, dict[str, int]]] = {
        "multilingual_v3": (
            "ResembleAI/chatterbox",
            {
                "ve.pt": 1 * 1024 * 1024,
                "t3_mtl23ls_v2.safetensors": 100 * 1024 * 1024,
                "s3gen.pt": 100 * 1024 * 1024,
                "grapheme_mtl_merged_expanded_v1.json": 10 * 1024,
                "conds.pt": 10 * 1024,
            },
        ),
        "english": (
            "ResembleAI/chatterbox",
            {
                "ve.safetensors": 1 * 1024 * 1024,
                "t3_cfg.safetensors": 100 * 1024 * 1024,
                "s3gen.safetensors": 100 * 1024 * 1024,
                "tokenizer.json": 10 * 1024,
                "conds.pt": 10 * 1024,
            },
        ),
        "turbo": (
            "ResembleAI/chatterbox-turbo",
            {
                "ve.safetensors": 1 * 1024 * 1024,
                "t3_turbo_v1.safetensors": 100 * 1024 * 1024,
                "s3gen_meanflow.safetensors": 100 * 1024 * 1024,
            },
        ),
    }

    MODELS: tuple[ChatterboxModel, ...] = (
        ChatterboxModel("multilingual_v3", "Chatterbox Multilingual V3", False, True),
        ChatterboxModel("english", "Chatterbox English", False, False),
        ChatterboxModel("turbo", "Chatterbox Turbo (English, voice clone)", True, False),
    )

    LANGUAGES: tuple[ChatterboxLanguage, ...] = (
        ChatterboxLanguage("ar", "Arabic"),
        ChatterboxLanguage("da", "Danish"),
        ChatterboxLanguage("de", "German"),
        ChatterboxLanguage("el", "Greek"),
        ChatterboxLanguage("en", "English"),
        ChatterboxLanguage("es", "Spanish"),
        ChatterboxLanguage("fi", "Finnish"),
        ChatterboxLanguage("fr", "French"),
        ChatterboxLanguage("he", "Hebrew"),
        ChatterboxLanguage("hi", "Hindi"),
        ChatterboxLanguage("it", "Italian"),
        ChatterboxLanguage("ja", "Japanese"),
        ChatterboxLanguage("ko", "Korean"),
        ChatterboxLanguage("ms", "Malay"),
        ChatterboxLanguage("nl", "Dutch"),
        ChatterboxLanguage("no", "Norwegian"),
        ChatterboxLanguage("pl", "Polish"),
        ChatterboxLanguage("pt", "Portuguese"),
        ChatterboxLanguage("ru", "Russian"),
        ChatterboxLanguage("sv", "Swedish"),
        ChatterboxLanguage("sw", "Swahili"),
        ChatterboxLanguage("tr", "Turkish"),
        ChatterboxLanguage("zh", "Chinese"),
    )

    def __init__(
        self,
        install_dir: Path | None = None,
        python_runtime: PythonRuntimeManager | None = None,
        runtime_dir: Path | None = None,
        timeout_seconds: int = 60,
        **_legacy_kwargs: Any,
    ) -> None:
        self.install_dir = install_dir or app_data_root() / "models" / "chatterbox"
        self.cache_dir = self.install_dir / "hf-cache"
        self.python_runtime = python_runtime or PythonRuntimeManager()
        self.runtime_dir = runtime_dir or (
            self.python_runtime.runtime_dir / "engine-deps"
        )
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def is_installed(self) -> bool:
        return self.has_model_files() and self.has_runtime()

    def has_model_files(self, model_id: str | None = None) -> bool:
        """Discover complete supported model snapshots directly from the cache."""

        model_ids = (
            (model_id,)
            if model_id in self.MODEL_REPOSITORIES
            else tuple(self.MODEL_REPOSITORIES)
        )
        return any(
            huggingface_model_is_cached(
                self.cache_dir,
                self.MODEL_REPOSITORIES[candidate][0],
                self.MODEL_REPOSITORIES[candidate][1],
            )
            for candidate in model_ids
        )

    def isInstalled(self) -> bool:
        return self.is_installed()

    def has_runtime(self) -> bool:
        manifest = self.runtime_manifest()
        return (
            self.python_runtime.is_installed()
            and manifest.get("state") == "installed"
            and manifest.get("runtime_version") == self.RUNTIME_VERSION
            and self.cli_path.is_file()
        )

    def runtime_is_current(self) -> bool:
        return self.has_runtime()

    def install_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.manifest_path)

    def runtime_manifest(self) -> dict[str, Any]:
        return self._read_manifest(self.runtime_manifest_path)

    def install(
        self,
        model: str = "multilingual_v3",
        device: str = "auto",
        progress_callback: ChatterboxProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._install_runtime_dependencies(progress, cancel_token)
            self._write_cli()
            self._write_manifest("installing", model, device)
            progress(80, 100, "Preparing Chatterbox model cache...")
            resolved_device = device
            try:
                self._run_runtime(
                    ["--warmup", "--model", model, "--device", device],
                    cancel_token,
                )
            except ChatterboxError as exc:
                if device == "cuda" and self._is_cuda_unavailable_error(str(exc)):
                    resolved_device = "auto"
                    progress(
                        85,
                        100,
                        "CUDA GPU was not available. Retrying with Auto/CPU...",
                    )
                    self._run_runtime(
                        ["--warmup", "--model", model, "--device", resolved_device],
                        cancel_token,
                    )
                else:
                    raise
            self._write_manifest("installed", model, resolved_device)
            progress(100, 100, "Chatterbox is ready.")
            return self.install_dir
        except ChatterboxCancelled:
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

    def list_models(self) -> list[ChatterboxModel]:
        return list(self.MODELS)

    def list_languages(self) -> list[ChatterboxLanguage]:
        return list(self.LANGUAGES)

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        from .chatterbox_engine import ChatterboxTTSEngine

        engine = ChatterboxTTSEngine(self)
        try:
            return engine.synthesize_to_wav(text, output_path, voice_config)
        finally:
            engine.close()

    def cuda_info(self) -> dict[str, Any]:
        if not self.has_runtime():
            return {}
        try:
            output = self._run_runtime(["--cuda-info"])
            lines = [line for line in output.splitlines() if line.strip()]
            data = json.loads(lines[-1] if lines else "{}")
        except (ChatterboxError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

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

    @property
    def manifest_path(self) -> Path:
        return self.install_dir / self.INSTALL_FILENAME

    @property
    def runtime_manifest_path(self) -> Path:
        return self.runtime_dir / self.RUNTIME_INSTALL_FILENAME

    @property
    def cli_path(self) -> Path:
        return self.install_dir / self.CLI_FILENAME

    @property
    def runtime_path(self) -> Path:
        return self.python_runtime.python_exe

    def runtime_command(self) -> list[str]:
        self._write_cli()
        return [str(self.python_runtime.python_exe), str(self.cli_path)]

    def runtime_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["HF_HOME"] = str(self.cache_dir)
        env["HUGGINGFACE_HUB_CACHE"] = str(self.cache_dir / "hub")
        env["TRANSFORMERS_CACHE"] = str(self.cache_dir / "transformers")
        return env

    def _install_runtime_dependencies(
        self,
        progress: ChatterboxProgress,
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
            progress(75, 100, "Chatterbox Python dependencies already installed.")
            return

        gpu_detection = detect_gpus()
        gpu_summary = format_gpu_detection(gpu_detection)
        progress(30, 100, gpu_summary.splitlines()[0])

        requirements: list[str] = list(self.SUPPORT_PACKAGES)
        backend = "cpu"

        progress(35, 100, "Installing Chatterbox support packages...")
        self._run_pip(
            [
                "install",
                "--upgrade",
                "--no-warn-script-location",
                *self.SUPPORT_PACKAGES,
            ],
            cancel_token,
        )

        progress(50, 100, "Installing Chatterbox Python package...")
        self._run_pip(
            [
                "install",
                "--upgrade",
                "--no-warn-script-location",
                self.CHATTERBOX_PACKAGE,
            ],
            cancel_token,
        )
        requirements.append(self.CHATTERBOX_PACKAGE)

        if gpu_detection.has_nvidia_gpu:
            progress(62, 100, "Installing PyTorch CUDA runtime for Chatterbox...")
            try:
                self._run_pip(
                    [
                        "install",
                        "--upgrade",
                        "--force-reinstall",
                        "--no-deps",
                        "--no-warn-script-location",
                        "--index-url",
                        self.GPU_TORCH_INDEX_URL,
                        f"torch=={self.TORCH_VERSION}",
                        f"torchaudio=={self.TORCH_VERSION}",
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
                    66,
                    100,
                    "CUDA PyTorch install failed; falling back to CPU: "
                    f"{exc}",
                )

        progress(70, 100, "Validating Chatterbox Python runtime...")
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
        output = self.python_runtime.run_python(
            [
                "-c",
                (
                    "import json, torch, torchaudio, chatterbox; "
                    "from perth.perth_net.perth_net_implicit.perth_watermarker "
                    "import PerthImplicitWatermarker; "
                    "print(json.dumps({"
                    "'torch_version': torch.__version__, "
                    "'torch_cuda_version': torch.version.cuda, "
                    "'cuda_available': torch.cuda.is_available(), "
                    "'device_count': torch.cuda.device_count(), "
                    "'perth_watermarker_available': "
                    "PerthImplicitWatermarker is not None"
                    "}))"
                ),
            ],
            cancel_token,
        )
        lines = [line for line in output.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1] if lines else "{}")
        except json.JSONDecodeError as exc:
            raise PythonRuntimeError(
                f"Could not validate Chatterbox runtime: {output}"
            ) from exc
        return payload if isinstance(payload, dict) else {}

    def _run_runtime(
        self,
        args: list[str],
        cancel_token: threading.Event | None = None,
    ) -> str:
        if not self.python_runtime.is_installed():
            raise ChatterboxError("Embedded Python runtime is not installed.")
        command = [
            *self.runtime_command(),
            *args,
            "--cache-dir",
            str(self.cache_dir),
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
            raise ChatterboxError(f"Could not start Chatterbox: {exc}") from exc
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
        if process.returncode != 0:
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            details = self._runtime_json_error(stdout_text)
            if not details:
                details = self._clean_runtime_stderr(stderr_text)
            if not details:
                details = stdout_text
            raise ChatterboxError(
                f"Chatterbox failed with exit code {process.returncode}: "
                f"{details or 'No error details were returned.'}"
            )
        output = stdout.decode("utf-8", errors="replace").strip()
        if "--cuda-info" in args:
            return output
        lines = [line for line in output.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("type") == "fatal":
                raise ChatterboxError(str(data.get("message", "Unknown error.")))
        return output

    def _write_cli(self) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cli_path.write_text(CHATTERBOX_PYTHON_CLI, encoding="utf-8")

    def _write_manifest(self, state: str, model: str, device: str) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "engine": "chatterbox",
            "version": self.VERSION,
            "state": state,
            "model": model,
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
            "engine": "chatterbox",
            "runtime_version": self.RUNTIME_VERSION,
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "requirements": requirements,
            "backend": backend,
            "gpu_detection": gpu_summary,
            "runtime_info": runtime_info,
            "python_runtime": str(self.python_runtime.python_exe),
        }
        self._write_json_atomic(self.runtime_manifest_path, manifest)

    def _check_cancelled(self, cancel_token: threading.Event | None = None) -> None:
        if self._cancel_requested.is_set() or (
            cancel_token is not None and cancel_token.is_set()
        ):
            raise ChatterboxCancelled("Chatterbox operation cancelled.")

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

    @staticmethod
    def _is_cuda_unavailable_error(message: str) -> bool:
        lowered = message.lower()
        return "cuda" in lowered and (
            "cannot see a cuda gpu" in lowered
            or "not available" in lowered
            or "no cuda" in lowered
            or "unavailable" in lowered
        )

    @staticmethod
    def _runtime_json_error(output: str) -> str:
        for line in reversed([line for line in output.splitlines() if line.strip()]):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") in {"fatal", "error"}:
                return str(data.get("message", "Unknown Chatterbox error."))
        return ""

    @staticmethod
    def _clean_runtime_stderr(stderr_text: str) -> str:
        noisy_fragments = (
            "Fetching ",
            "FutureWarning:",
            "HF_TOKEN",
            "LoRACompatibleLinear",
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
