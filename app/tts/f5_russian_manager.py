from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.utils.gpu_detection import detect_gpus, format_gpu_detection
from app.utils.paths import engine_dependencies_root, models_root

from .model_cache import (
    format_file_size,
    huggingface_cached_files,
    huggingface_repo_cache_name,
)
from .omnivoice_manager import OmniVoiceCancelled, OmniVoiceError, OmniVoiceManager
from .python_runtime_manager import PythonRuntimeError, PythonRuntimeManager


class F5RussianError(OmniVoiceError):
    pass


class F5RussianCancelled(OmniVoiceCancelled):
    pass


F5RussianProgress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class F5RussianModel:
    model_id: str
    display_name: str
    repo_id: str
    checkpoint_file: str
    revision: str
    requires_gpu: bool = False


F5_RUSSIAN_PYTHON_CLI = r'''
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import warnings
from pathlib import Path


def emit(message: dict) -> None:
    print(json.dumps(message, ensure_ascii=False), flush=True)


def emit_info(message: str) -> None:
    emit({"type": "info", "message": message})


def emit_timing(label: str, started_at: float, request_id: str | None = None) -> None:
    payload = {"type": "timing", "label": label, "elapsed": time.perf_counter() - started_at}
    if request_id is not None:
        payload["id"] = request_id
    emit(payload)


def add_dll_search_paths(deps_path: Path) -> None:
    candidates = [deps_path, deps_path / "torch" / "lib", deps_path / "torchaudio" / "lib"]
    for nvidia_dir in (deps_path / "nvidia").glob("*"):
        candidates.extend((nvidia_dir / "bin", nvidia_dir / "lib"))
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
        os.environ["PATH"] = os.pathsep.join([*path_parts, os.environ.get("PATH", "")])


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
        return {"torch_available": False, "cuda_available": False, "device_count": 0, "error": str(exc)}
    devices = []
    for index in range(torch.cuda.device_count()):
        try:
            props = torch.cuda.get_device_properties(index)
            devices.append({
                "index": index,
                "name": torch.cuda.get_device_name(index),
                "total_memory_gb": props.total_memory / (1024**3),
                "capability": f"{props.major}.{props.minor}",
            })
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
    return requested


MANUAL_STRESS_RE = re.compile(r"[А-Яа-яЁё-]*\+[АЕЁИОУЫЭЮЯаеёиоуыэюя][А-Яа-яЁё-]*")


def accent_preserving_manual_marks(accentor, text: str) -> str:
    protected: list[str] = []

    def replace(match) -> str:
        protected.append(match.group(0))
        return f"LTVMANUALSTRESS{len(protected) - 1}TOKEN"

    masked = MANUAL_STRESS_RE.sub(replace, text)
    accented = str(accentor(masked))
    for index, original in enumerate(protected):
        accented = accented.replace(f"LTVMANUALSTRESS{index}TOKEN", original)
    return accented


def main() -> int:
    total_started = time.perf_counter()
    parser = argparse.ArgumentParser(description="LocalText2Voice F5-TTS Russian worker")
    parser.add_argument("--model-repo", default="Misha24-10/F5-TTS_RUSSIAN")
    parser.add_argument("--checkpoint-file", default="F5TTS_v1_Base_v2/model_last_inference.safetensors")
    parser.add_argument("--revision", default="ea166adeae4c80ec5ee423a671e2bdb83906cf84")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu", "mps"), default="auto")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--deps-dir", required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--cuda-info", action="store_true")
    args = parser.parse_args()

    configure_environment(args.cache_dir, args.deps_dir)
    try:
        import torch
        from f5_tts.api import F5TTS
        from huggingface_hub import hf_hub_download
        from silero_stress import load_accentor
    except Exception as exc:
        if args.cuda_info:
            print(json.dumps(cuda_info(), ensure_ascii=False), flush=True)
            return 0
        emit({"type": "fatal", "message": f"F5-TTS Russian dependencies are missing: {exc}"})
        return 3

    if args.cuda_info:
        print(json.dumps(cuda_info(), ensure_ascii=False), flush=True)
        return 0

    selected_device = resolve_device(torch, args.device)
    try:
        download_started = time.perf_counter()
        checkpoint_path = hf_hub_download(
            repo_id=args.model_repo,
            filename=args.checkpoint_file,
            revision=args.revision,
            cache_dir=args.cache_dir,
        )
        emit_timing("checkpoint download/check", download_started)
        load_started = time.perf_counter()
        try:
            model = F5TTS(
                model="F5TTS_v1_Base",
                ckpt_file=checkpoint_path,
                device=selected_device,
                hf_cache_dir=args.cache_dir,
            )
        except Exception as gpu_exc:
            if args.device != "auto" or selected_device == "cpu":
                raise
            emit_info(f"F5-TTS Russian GPU load failed; retrying on CPU: {gpu_exc}")
            selected_device = "cpu"
            model = F5TTS(
                model="F5TTS_v1_Base",
                ckpt_file=checkpoint_path,
                device=selected_device,
                hf_cache_dir=args.cache_dir,
            )
        emit_timing("model load", load_started)
    except Exception as exc:
        emit({"type": "fatal", "message": f"F5-TTS Russian model load failed: {exc}"})
        return 5

    accentor = None

    def get_accentor():
        nonlocal accentor
        if accentor is None:
            started = time.perf_counter()
            accentor = load_accentor()
            emit_timing("Silero Stress load", started)
        return accentor

    emit_timing("worker startup", total_started)
    emit({"type": "ready", "model": args.checkpoint_file, "device": selected_device, "stress": "Silero Stress (lazy)"})
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
        if str(request.get("type", "synthesize")) == "shutdown":
            emit({"type": "shutdown"})
            return 0
        try:
            text = str(request.get("text", "")).strip()
            ref_audio = str(request.get("ref_audio", "")).strip()
            ref_text = str(request.get("ref_text", "")).strip()
            if not text:
                raise ValueError("Input text is empty.")
            if not ref_audio or not Path(ref_audio).is_file():
                raise ValueError("A valid reference audio file is required.")
            if not ref_text:
                raise ValueError("The exact transcript of the reference audio is required.")
            if bool(request.get("use_stress", True)):
                stress_model = get_accentor()
                if not bool(request.get("stress_text_preprocessed", False)):
                    text = accent_preserving_manual_marks(stress_model, text)
                ref_text = accent_preserving_manual_marks(stress_model, ref_text)
            output_path = Path(str(request["output"]))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            model.infer(
                ref_file=ref_audio,
                ref_text=ref_text,
                gen_text=text,
                nfe_step=int(request.get("nfe_step", 32) or 32),
                speed=float(request.get("speed", 1.0) or 1.0),
                remove_silence=bool(request.get("remove_silence", True)),
                file_wave=str(output_path),
                show_info=lambda message: emit_info(str(message)),
            )
            emit_timing("synthesis", started, request_id)
            emit({"type": "result", "id": request_id, "output": str(output_path)})
        except Exception as exc:
            emit({"type": "error", "id": request_id, "message": f"F5-TTS Russian synthesis failed: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.strip() + "\n"


class F5RussianManager(OmniVoiceManager):
    VERSION = "f5-russian-v1"
    RUNTIME_VERSION = "f5-russian-python-deps-v1"
    INSTALL_FILENAME = "f5-russian-install.json"
    RUNTIME_INSTALL_FILENAME = "f5-russian-runtime-install.json"
    CLI_FILENAME = "f5_russian_worker.py"
    F5_TTS_VERSION = "1.1.22"
    SILERO_STRESS_VERSION = "1.4"
    MODEL_REPO = "Misha24-10/F5-TTS_RUSSIAN"
    MODEL_REVISION = "ea166adeae4c80ec5ee423a671e2bdb83906cf84"
    CHECKPOINT_FILE = "F5TTS_v1_Base_v2/model_last_inference.safetensors"
    MODEL_REQUIRED_FILES = {CHECKPOINT_FILE: 100 * 1024 * 1024}
    MODELS = (
        F5RussianModel(
            "f5tts_v1_base_v2",
            "F5TTS_v1_Base_v2 (Russian)",
            MODEL_REPO,
            CHECKPOINT_FILE,
            MODEL_REVISION,
        ),
    )
    SUPPORT_REQUIREMENTS = (
        f"f5-tts=={F5_TTS_VERSION}",
        f"silero-stress=={SILERO_STRESS_VERSION}",
        "soundfile",
        "huggingface-hub",
    )

    def __init__(
        self,
        install_dir: Path | None = None,
        python_runtime: PythonRuntimeManager | None = None,
        dependencies_root: Path | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        super().__init__(
            install_dir=install_dir or models_root() / "f5-tts-russian",
            python_runtime=python_runtime,
            dependencies_root=dependencies_root or (
                python_runtime.runtime_dir / "engine-deps" if python_runtime is not None else engine_dependencies_root()
            ),
            timeout_seconds=timeout_seconds,
        )

    def list_models(self) -> list[F5RussianModel]:
        return list(self.MODELS)

    def model_repo(self, model_id: str) -> str:
        return self.MODEL_REPO

    def model(self, model_id: str | None = None) -> F5RussianModel:
        selected = str(model_id or self.MODELS[0].model_id)
        return next((model for model in self.MODELS if model.model_id == selected), self.MODELS[0])

    def has_model_files(self, model_id: str | None = None) -> bool:
        repository = huggingface_repo_cache_name(self.MODEL_REPO)
        for cache_root in (
            self.cache_dir / "hub",
            self.cache_dir / "models",
            self.cache_dir,
        ):
            checkpoint = (
                cache_root
                / repository
                / "snapshots"
                / self.MODEL_REVISION
                / self.CHECKPOINT_FILE
            )
            try:
                if checkpoint.is_file() and checkpoint.stat().st_size >= 100 * 1024 * 1024:
                    return True
            except OSError:
                continue
        return False

    def has_runtime(self) -> bool:
        manifest = self.runtime_manifest()
        return (
            self.python_runtime.is_installed()
            and manifest.get("state") == "installed"
            and manifest.get("runtime_version") == self.RUNTIME_VERSION
            and self.cli_path.is_file()
            and (self.dependency_dir / "f5_tts").is_dir()
            and (self.dependency_dir / "silero_stress").is_dir()
        )

    @property
    def dependency_dir(self) -> Path:
        return self.dependencies_root / "f5-russian" / "site-packages"

    def install(
        self,
        model: str = "f5tts_v1_base_v2",
        device: str = "auto",
        progress_callback: F5RussianProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        selected = self.model(model)
        try:
            self._install_runtime_dependencies(progress, cancel_token)
            self._write_cli()
            self._write_manifest("installing", selected.model_id, device)
            progress(82, 100, "Downloading and preparing F5TTS_v1_Base_v2...")
            self._run_runtime([
                "--warmup", "--model-repo", selected.repo_id,
                "--checkpoint-file", selected.checkpoint_file,
                "--revision", selected.revision, "--device", device,
            ], cancel_token)
            for filename, size in huggingface_cached_files(self.cache_dir, selected.repo_id):
                progress(98, 100, f"OK: {filename} ({format_file_size(size)})")
            self._write_manifest("installed", selected.model_id, device)
            progress(100, 100, "F5-TTS Russian and Silero Stress are ready.")
            return self.install_dir
        except OmniVoiceCancelled as exc:
            self._write_manifest("cancelled", selected.model_id, device)
            raise F5RussianCancelled(str(exc)) from exc
        except Exception:
            self._write_manifest("failed", selected.model_id, device)
            raise

    def synthesize(self, text: str, output_path: Path, voice_config: dict[str, Any]) -> Path:
        from .f5_russian_engine import F5RussianTTSEngine
        engine = F5RussianTTSEngine(self)
        try:
            return engine.synthesize_to_wav(text, output_path, voice_config)
        finally:
            engine.close()

    def _run_runtime(self, *args: Any, **kwargs: Any) -> str:
        try:
            return super()._run_runtime(*args, **kwargs)
        except OmniVoiceCancelled as exc:
            raise F5RussianCancelled(str(exc)) from exc
        except OmniVoiceError as exc:
            raise F5RussianError(str(exc).replace("OmniVoice", "F5-TTS Russian")) from exc

    def _install_runtime_dependencies(self, progress: F5RussianProgress, cancel_token: threading.Event | None) -> None:
        if not self.python_runtime.is_installed():
            self.python_runtime.install(
                lambda current, total, message: progress(int((current / total) * 25) if total else 0, 100, message),
                cancel_token,
            )
        if self.has_runtime():
            progress(75, 100, "F5-TTS Russian dependencies already installed.")
            return
        gpu_detection = detect_gpus()
        gpu_summary = format_gpu_detection(gpu_detection)
        progress(30, 100, gpu_summary.splitlines()[0])
        self._reset_dependency_dir()
        torch_requirements = [f"torch=={self.TORCH_VERSION}", f"torchaudio=={self.TORCH_VERSION}"]
        install_requirements = [*torch_requirements, *self.SUPPORT_REQUIREMENTS]
        requirements: list[str] = []
        backend = "cpu"
        if gpu_detection.has_nvidia_gpu:
            progress(38, 100, "Installing the isolated F5/Silero CUDA runtime...")
            try:
                self._install_pinned_environment(install_requirements, self.GPU_TORCH_INDEX_URL, cancel_token, progress, 38)
                requirements = [
                    f"torch=={self.TORCH_VERSION} ({self.GPU_TORCH_INDEX_URL})",
                    f"torchaudio=={self.TORCH_VERSION} ({self.GPU_TORCH_INDEX_URL})",
                    *self.SUPPORT_REQUIREMENTS,
                ]
                backend = "cuda"
            except PythonRuntimeError as exc:
                progress(42, 100, f"CUDA install failed; falling back to CPU: {exc}")
                self._reset_dependency_dir()
        if backend != "cuda":
            progress(40, 100, "Installing the isolated F5/Silero CPU runtime...")
            self._install_pinned_environment(install_requirements, self.CPU_TORCH_INDEX_URL, cancel_token, progress, 40)
            requirements = [
                f"torch=={self.TORCH_VERSION} ({self.CPU_TORCH_INDEX_URL})",
                f"torchaudio=={self.TORCH_VERSION} ({self.CPU_TORCH_INDEX_URL})",
                *self.SUPPORT_REQUIREMENTS,
            ]
        progress(74, 100, "Validating F5-TTS Russian and Silero Stress...")
        runtime_info = self._validate_runtime(cancel_token)
        if runtime_info.get("cuda_available"):
            backend = "cuda"
        self._write_runtime_manifest("installed", requirements, backend, gpu_summary, runtime_info)

    def _validate_runtime(self, cancel_token: threading.Event | None) -> dict[str, Any]:
        code = (
            "import json, os, sys; from pathlib import Path; "
            f"deps=Path({str(self.dependency_dir)!r}); sys.path.insert(0, str(deps)); "
            "dlls=[deps, deps/'torch'/'lib', deps/'torchaudio'/'lib']; nvidia=deps/'nvidia'; "
            "dlls += [x/'bin' for x in nvidia.glob('*')]; dlls += [x/'lib' for x in nvidia.glob('*')]; "
            "paths=[str(x) for x in dlls if x.exists()]; os.environ['PATH']=os.pathsep.join(paths+[os.environ.get('PATH','')]); "
            "import torch, torchaudio, soundfile, f5_tts, silero_stress; "
            "print(json.dumps({'torch_version': torch.__version__, 'torch_cuda_version': torch.version.cuda, "
            "'cuda_available': torch.cuda.is_available(), 'device_count': torch.cuda.device_count()}))"
        )
        output = self.python_runtime.run_python(["-c", code], cancel_token)
        lines = [line for line in output.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1] if lines else "{}")
        except json.JSONDecodeError as exc:
            raise PythonRuntimeError(f"Could not validate F5-TTS Russian runtime: {output}") from exc
        return payload if isinstance(payload, dict) else {}

    def _write_cli(self) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cli_path.write_text(F5_RUSSIAN_PYTHON_CLI, encoding="utf-8")

    def _write_manifest(self, state: str, model: str, device: str) -> None:
        selected = self.model(model)
        self._write_json_atomic(self.manifest_path, {
            "engine": "f5_russian", "version": self.VERSION, "state": state,
            "model": selected.model_id, "model_repo": selected.repo_id,
            "checkpoint_file": selected.checkpoint_file, "revision": selected.revision,
            "license": "CC BY-NC 4.0", "commercial_use": False, "device": device,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cache_dir": str(self.cache_dir),
        })

    def _write_runtime_manifest(
        self, state: str, requirements: list[str], backend: str,
        gpu_summary: str, runtime_info: dict[str, Any],
    ) -> None:
        self._write_json_atomic(self.runtime_manifest_path, {
            "engine": "f5_russian", "runtime_version": self.RUNTIME_VERSION, "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "requirements": requirements, "backend": backend, "gpu_detection": gpu_summary,
            "runtime_info": runtime_info, "dependency_dir": str(self.dependency_dir),
            "silero_stress_scope": "optional-f5-runtime-only",
        })
