from __future__ import annotations

import json
import os
import time
import threading
from pathlib import Path
from typing import Any

from app.utils.gpu_detection import detect_gpus, format_gpu_detection
from app.utils.paths import engine_dependencies_root, models_root

from .install_logging import (
    detailed_pip_args,
    progress_output_callback,
    report_process_command,
    run_python_with_live_output,
)
from .kokoro_manager import KokoroManager, KokoroProgress
from .python_runtime_manager import PythonRuntimeError, PythonRuntimeManager


KOKORO_PYTHON_CLI = r'''
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def emit(message: dict) -> None:
    print(json.dumps(message, ensure_ascii=False), flush=True)


def emit_timing(label: str, started_at: float, request_id: str | None = None) -> None:
    elapsed = time.perf_counter() - started_at
    payload = {"type": "timing", "label": label, "elapsed": elapsed}
    if request_id is not None:
        payload["id"] = request_id
    emit(payload)


def emit_fatal(message: str) -> None:
    emit({"type": "fatal", "message": message})


def emit_info(message: str) -> None:
    emit({"type": "info", "message": message})


def configure_environment(deps_dir: str) -> None:
    deps_path = Path(deps_dir)
    if str(deps_path) not in sys.path:
        sys.path.insert(0, str(deps_path))

    candidates = [deps_path]
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
        os.environ["PATH"] = os.pathsep.join(
            [*path_parts, os.environ.get("PATH", "")]
        )


def configure_backend(rt, requested_provider: str) -> str:
    preload_started = time.perf_counter()
    if requested_provider in {"auto", "cuda"} and hasattr(rt, "preload_dlls"):
        try:
            rt.preload_dlls(directory="")
            emit_timing("CUDA DLL preload", preload_started)
        except Exception as exc:
            emit_info(f"CUDA DLL preload skipped: {exc}")

    providers = list(rt.get_available_providers())
    providers_text = ", ".join(providers) if providers else "none"
    emit_info(f"ONNX Runtime providers: {providers_text}")

    selected = "CPUExecutionProvider"
    if requested_provider == "cuda":
        if "CUDAExecutionProvider" in providers:
            selected = "CUDAExecutionProvider"
        else:
            emit_info("CUDA provider requested but unavailable; falling back to CPU.")
    elif requested_provider == "directml":
        if "DmlExecutionProvider" in providers:
            selected = "DmlExecutionProvider"
        else:
            emit_info("DirectML provider requested but unavailable; falling back to CPU.")
    elif requested_provider == "auto" and "CUDAExecutionProvider" in providers:
        selected = "CUDAExecutionProvider"

    os.environ["ONNX_PROVIDER"] = selected
    emit_info(f"Kokoro backend selected: {selected}")
    return selected


def provider_chain(selected_provider: str) -> list:
    if selected_provider == "CUDAExecutionProvider":
        return [
            (
                "CUDAExecutionProvider",
                {"cudnn_conv_algo_search": "DEFAULT"},
            ),
            "CPUExecutionProvider",
        ]
    if selected_provider == "DmlExecutionProvider":
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def model_for_provider(
    selected_provider: str,
    cpu_model_path: Path,
    gpu_model_path: Path,
) -> Path:
    if selected_provider == "CUDAExecutionProvider":
        if gpu_model_path.is_file():
            return gpu_model_path
        emit_info("CUDA selected but Kokoro GPU model is missing; using CPU model.")
    return cpu_model_path


def load_kokoro(rt, Kokoro, model_path: Path, voices_path: Path, provider: str):
    load_started = time.perf_counter()
    options = rt.SessionOptions()
    options.log_severity_level = 3
    session = rt.InferenceSession(
        str(model_path),
        sess_options=options,
        providers=provider_chain(provider),
    )
    if hasattr(Kokoro, "from_session"):
        kokoro = Kokoro.from_session(session, str(voices_path))
    else:
        os.environ["ONNX_PROVIDER"] = provider
        kokoro = Kokoro(str(model_path), str(voices_path))
    emit_info(f"Kokoro model loaded: {model_path.name}")
    emit_info(f"Kokoro active providers: {', '.join(session.get_providers())}")
    emit_timing("model load", load_started)
    return kokoro


def main() -> int:
    total_started = time.perf_counter()
    parser = argparse.ArgumentParser(description="LocalText2Voice Kokoro engine")
    parser.add_argument("--model")
    parser.add_argument("--cpu-model")
    parser.add_argument("--gpu-model")
    parser.add_argument("--voices", required=True)
    parser.add_argument("--deps-dir", required=True)
    parser.add_argument(
        "--provider",
        choices=("auto", "cpu", "cuda", "directml"),
        default="auto",
    )
    args = parser.parse_args()

    configure_environment(args.deps_dir)
    try:
        import_started = time.perf_counter()
        import soundfile as sf
        import onnxruntime as rt
        from kokoro_onnx import Kokoro
        emit_timing("dependency import", import_started)
    except Exception as exc:
        emit_fatal(f"Kokoro dependencies are missing: {exc}")
        return 3

    backend_started = time.perf_counter()
    selected_provider = configure_backend(rt, args.provider)
    emit_timing("backend selection", backend_started)

    cpu_model_path = Path(args.cpu_model or args.model or "")
    gpu_model_path = Path(args.gpu_model or args.model or "")
    voices_path = Path(args.voices)
    if not cpu_model_path.is_file():
        emit_fatal(f"CPU model file not found: {cpu_model_path}")
        return 4
    if not voices_path.is_file():
        emit_fatal(f"Voices file not found: {voices_path}")
        return 4

    model_path = model_for_provider(
        selected_provider,
        cpu_model_path,
        gpu_model_path,
    )
    try:
        kokoro = load_kokoro(rt, Kokoro, model_path, voices_path, selected_provider)
    except Exception as exc:
        if args.provider == "auto" and selected_provider != "CPUExecutionProvider":
            emit_info(
                f"{selected_provider} model load failed; retrying CPU: {exc}"
            )
            try:
                selected_provider = "CPUExecutionProvider"
                model_path = cpu_model_path
                kokoro = load_kokoro(
                    rt,
                    Kokoro,
                    model_path,
                    voices_path,
                    selected_provider,
                )
            except Exception as fallback_exc:
                emit_fatal(f"Kokoro model load failed: {fallback_exc}")
                return 5
        else:
            emit_fatal(f"Kokoro model load failed: {exc}")
            return 5

    emit_timing("worker startup", total_started)
    emit(
        {
            "type": "ready",
            "provider": selected_provider,
            "model": str(model_path),
        }
    )

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            emit({"type": "error", "id": "", "message": f"Invalid JSON request: {exc}"})
            continue

        request_type = request.get("type", "synthesize")
        request_id = str(request.get("id", ""))
        if request_type == "shutdown":
            emit({"type": "shutdown"})
            return 0
        if request_type != "synthesize":
            emit({"type": "error", "id": request_id, "message": f"Unknown request type: {request_type}"})
            continue

        request_started = time.perf_counter()
        try:
            text = str(request.get("text", ""))
            output_path = Path(str(request["output"]))
            voice = str(request["voice"])
            lang = str(request["lang"])
            speed = float(request.get("speed", 1.0))

            synth_started = time.perf_counter()
            try:
                samples, sample_rate = kokoro.create(
                    text,
                    voice=voice,
                    speed=speed,
                    lang=lang,
                )
            except Exception as exc:
                if (
                    args.provider == "auto"
                    and selected_provider != "CPUExecutionProvider"
                ):
                    emit_info(
                        f"{selected_provider} synthesis failed; "
                        f"retrying CPU: {exc}"
                    )
                    selected_provider = "CPUExecutionProvider"
                    model_path = cpu_model_path
                    kokoro = load_kokoro(
                        rt,
                        Kokoro,
                        model_path,
                        voices_path,
                        selected_provider,
                    )
                    synth_started = time.perf_counter()
                    samples, sample_rate = kokoro.create(
                        text,
                        voice=voice,
                        speed=speed,
                        lang=lang,
                    )
                else:
                    raise
            emit_timing("synthesis", synth_started, request_id)

            write_started = time.perf_counter()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(output_path), samples, sample_rate)
            emit_timing("write wav", write_started, request_id)
            emit_timing("request total", request_started, request_id)
            emit({"type": "result", "id": request_id, "output": str(output_path)})
        except Exception as exc:
            emit({"type": "error", "id": request_id, "message": f"Kokoro synthesis failed: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.strip() + "\n"


class KokoroPythonManager(KokoroManager):
    VERSION = "v1.0-auto-cpu-gpu-python"
    DEPENDENCY_VERSION = "kokoro-deps-v3"
    INSTALL_FILENAME = "kokoro-install.json"
    DEPENDENCY_INSTALL_FILENAME = "kokoro-runtime-install.json"
    CLI_FILENAME = "kokoro_worker.py"
    CPU_REQUIREMENTS = (
        "kokoro-onnx>=0.4,<1",
        "soundfile>=0.12,<1",
        "onnxruntime==1.25.1",
    )
    GPU_REQUIREMENTS = (
        "onnxruntime-gpu[cuda,cudnn]==1.25.1",
        "nvidia-cudnn-cu12==9.10.2.21",
    )
    CPU_FALLBACK_REQUIREMENTS = (
        "onnxruntime==1.25.1",
    )

    def __init__(
        self,
        install_dir: Path | None = None,
        python_runtime: PythonRuntimeManager | None = None,
        dependencies_root: Path | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.python_runtime = python_runtime or PythonRuntimeManager()
        self.dependencies_root = dependencies_root or (
            self.python_runtime.runtime_dir / "engine-deps"
            if python_runtime is not None
            else engine_dependencies_root()
        )
        super().__init__(
            install_dir=install_dir or models_root() / "kokoro",
            timeout_seconds=timeout_seconds,
        )

    def is_installed(self) -> bool:
        return super().is_installed() and self.has_runtime()

    def has_runtime(self) -> bool:
        manifest = self.runtime_dependency_manifest()
        return (
            self.python_runtime.is_installed()
            and manifest.get("state") == "installed"
            and manifest.get("dependency_version") == self.DEPENDENCY_VERSION
            and self.cli_path.is_file()
        )

    def install(
        self,
        progress_callback: KokoroProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        self._external_cancel_token = cancel_token
        self.install_dir.mkdir(parents=True, exist_ok=True)
        try:
            if not self.python_runtime.is_installed():
                self.python_runtime.install(
                    lambda current, total, message: progress(
                        int((current / total) * 30) if total else 0,
                        100,
                        message,
                    ),
                    cancel_token,
                )
            self._install_python_dependencies(progress, cancel_token)
            self._write_cli()

            def asset_progress(current: int, total: int, message: str) -> None:
                percent = 50 + int((current / total) * 50) if total else 50
                progress(max(50, min(100, percent)), 100, message)

            destination = super().install(asset_progress, cancel_token)
            progress(100, 100, "Kokoro is ready.")
            return destination
        finally:
            self._external_cancel_token = None

    def synthesize(
        self,
        text: str,
        voice: str,
        lang: str,
        speed: float,
        output_path: Path,
        provider: str = "auto",
    ) -> Path:
        from .kokoro_python_engine import KokoroPythonTTSEngine

        engine = KokoroPythonTTSEngine(self)
        try:
            return engine.synthesize_to_wav(
                text,
                output_path,
                {
                    "engine": "kokoro",
                    "voice": voice,
                    "lang": lang,
                    "speed": speed,
                    "provider": provider,
                },
            )
        finally:
            engine.close()

    def runtime_command(self) -> list[str]:
        self._write_cli()
        return [str(self.python_runtime.python_exe), str(self.cli_path)]

    def runtime_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(self.dependency_dir), env.get("PYTHONPATH", "")]
        )
        runtime_paths = [self.dependency_dir]
        for nvidia_dir in (self.dependency_dir / "nvidia").glob("*"):
            runtime_paths.extend((nvidia_dir / "bin", nvidia_dir / "lib"))
        env["PATH"] = os.pathsep.join(
            [str(path) for path in runtime_paths if path.exists()]
            + [env.get("PATH", "")]
        )
        return env

    @property
    def dependency_dir(self) -> Path:
        return self.dependencies_root / "kokoro" / "site-packages"

    @property
    def dependency_manifest_path(self) -> Path:
        return self.install_dir / self.DEPENDENCY_INSTALL_FILENAME

    @property
    def runtime_dependency_manifest_path(self) -> Path:
        return self.dependencies_root / self.DEPENDENCY_INSTALL_FILENAME

    @property
    def cli_path(self) -> Path:
        return self.install_dir / self.CLI_FILENAME

    def dependency_manifest(self) -> dict[str, Any]:
        path = self.dependency_manifest_path
        return self._read_json_manifest(path)

    def runtime_dependency_manifest(self) -> dict[str, Any]:
        return self._read_json_manifest(self.runtime_dependency_manifest_path)

    def _read_json_manifest(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _install_python_dependencies(
        self,
        progress: KokoroProgress,
        cancel_token: threading.Event | None,
    ) -> None:
        if self.has_runtime():
            progress(50, 100, "Kokoro dependencies already installed.")
            return
        gpu_detection = detect_gpus()
        gpu_summary = format_gpu_detection(gpu_detection)
        progress(32, 100, gpu_summary.splitlines()[0])
        self._remove_path(self.dependency_dir)
        self.dependency_dir.mkdir(parents=True, exist_ok=True)

        backend = "CPUExecutionProvider"
        providers: list[str] = []
        requirements = list(self.CPU_REQUIREMENTS)
        self._install_requirements(
            requirements,
            cancel_token,
            progress,
            34,
        )

        if gpu_detection.has_nvidia_gpu:
            progress(
                40,
                100,
                "NVIDIA GPU detected. Installing Kokoro CUDA dependencies...",
            )
            try:
                self._uninstall_packages(("onnxruntime",), cancel_token)
                self._install_requirements(
                    self.GPU_REQUIREMENTS,
                    cancel_token,
                    progress,
                    40,
                )
                requirements = [
                    requirement
                    for requirement in requirements
                    if not requirement.startswith("onnxruntime")
                ]
                requirements.extend(self.GPU_REQUIREMENTS)
            except PythonRuntimeError as exc:
                progress(
                    43,
                    100,
                    "Kokoro CUDA dependencies failed; falling back to CPU: "
                    f"{exc}",
                )
                self._install_requirements(
                    self.CPU_FALLBACK_REQUIREMENTS,
                    cancel_token,
                    progress,
                    43,
                )
                requirements.extend(self.CPU_FALLBACK_REQUIREMENTS)
        else:
            progress(40, 100, "No NVIDIA CUDA GPU detected. Kokoro will use CPU.")

        progress(45, 100, "Validating Kokoro dependencies...")
        try:
            providers = self._validate_runtime_providers(cancel_token)
        except PythonRuntimeError as exc:
            progress(
                46,
                100,
                "Kokoro runtime validation failed; reinstalling CPU backend: "
                f"{exc}",
            )
            self._install_requirements(
                self.CPU_FALLBACK_REQUIREMENTS,
                cancel_token,
                progress,
                46,
            )
            requirements.extend(self.CPU_FALLBACK_REQUIREMENTS)
            providers = self._validate_runtime_providers(cancel_token)
        if "CUDAExecutionProvider" in providers:
            backend = "CUDAExecutionProvider"
            progress(47, 100, "Kokoro CUDA backend is available.")
        else:
            backend = "CPUExecutionProvider"
            progress(47, 100, "Kokoro CPU backend is available.")
        self._write_dependency_manifest(
            "installed",
            requirements,
            backend,
            providers,
            gpu_summary,
        )

    def _install_requirements(
        self,
        requirements: tuple[str, ...] | list[str],
        cancel_token: threading.Event | None,
        progress: KokoroProgress | None = None,
        current: int = 0,
    ) -> None:
        pip_args = detailed_pip_args(
            [
                "install",
                "--upgrade",
                "--target",
                str(self.dependency_dir),
                "--no-warn-script-location",
                *requirements,
            ]
        )
        if progress is not None:
            report_process_command(progress, current, "pip", ["pip", *pip_args])
        run_python_with_live_output(
            self.python_runtime,
            [
                "-m",
                "pip",
                *pip_args,
            ],
            cancel_token,
            (
                progress_output_callback(progress, current, "pip")
                if progress is not None
                else None
            ),
        )

    def _uninstall_packages(
        self,
        packages: tuple[str, ...],
        cancel_token: threading.Event | None,
    ) -> None:
        self._check_cancelled()
        for package in packages:
            normalized = package.casefold().replace("-", "_")
            for child in self.dependency_dir.iterdir():
                child_name = child.name.casefold().replace("-", "_")
                if child_name == normalized or child_name.startswith(normalized + "_"):
                    self._remove_path(child)

    def _validate_runtime_providers(
        self,
        cancel_token: threading.Event | None,
    ) -> list[str]:
        output = self.python_runtime.run_python(
            [
                "-c",
                (
                    "import json, os, sys; from pathlib import Path; "
                    f"deps=Path({str(self.dependency_dir)!r}); "
                    "sys.path.insert(0, str(deps)); "
                    "paths=[deps]; nvidia=deps/'nvidia'; "
                    "paths += [child/'bin' for child in nvidia.glob('*')]; "
                    "paths += [child/'lib' for child in nvidia.glob('*')]; "
                    "os.environ['PATH']=os.pathsep.join([str(p) for p in paths if p.exists()]+[os.environ.get('PATH','')]); "
                    "import kokoro_onnx, soundfile, onnxruntime as ort; "
                    "getattr(ort, 'preload_dlls', lambda **kwargs: None)"
                    "(directory=''); "
                    "print(json.dumps({'providers': ort.get_available_providers()}))"
                ),
            ],
            cancel_token,
        )
        lines = [line for line in output.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1] if lines else "{}")
        except json.JSONDecodeError as exc:
            raise PythonRuntimeError(
                f"Could not validate Kokoro ONNX Runtime providers: {output}"
            ) from exc
        providers = payload.get("providers", [])
        if not isinstance(providers, list):
            return []
        return [str(provider) for provider in providers]

    def _write_cli(self) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.cli_path.write_text(KOKORO_PYTHON_CLI, encoding="utf-8")

    def _write_manifest(self, state: str, files: list[dict[str, Any]]) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "engine": "kokoro",
            "version": self.VERSION,
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "files": files,
            "runtime": "python",
            "python_runtime": str(self.python_runtime.python_exe),
            "dependency_version": self.DEPENDENCY_VERSION,
        }
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    def _write_dependency_manifest(
        self,
        state: str,
        requirements: list[str],
        backend: str,
        providers: list[str],
        gpu_summary: str,
    ) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "engine": "kokoro",
            "dependency_version": self.DEPENDENCY_VERSION,
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "requirements": requirements,
            "backend": backend,
            "onnx_providers": providers,
            "gpu_detection": gpu_summary,
            "python_runtime": str(self.python_runtime.python_exe),
        }
        self._write_dependency_manifest_file(self.dependency_manifest_path, manifest)
        self._write_dependency_manifest_file(
            self.runtime_dependency_manifest_path,
            manifest,
        )

    @staticmethod
    def _write_dependency_manifest_file(path: Path, manifest: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
