from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from app.utils.gpu_detection import gpu_runtime_environment
from app.utils.paths import engine_dependencies_root, models_root
from .indextts_cli import INDEXTTS_CLI
from .indextts_config import validated_config
from .install_logging import communicate_with_live_output, progress_output_callback
from .python_runtime_manager import PythonRuntimeManager


class IndexTTSError(RuntimeError):
    pass


class IndexTTSCancelled(IndexTTSError):
    pass


class IndexTTSManager:
    SOURCE_REVISION = "d9e41aac89fd00b3d71497fddb287b7f24613712"
    MODEL_REVISION = "c39ce5ba981572cb187443877ff559dfb246ce63"
    MODEL_REPO = "IndexTeam/IndexTTS-2.5"
    VERSION = "indextts-2.5-bf16-v1"
    UV_VERSION = "0.8.22"
    REQUIRED_FILES = {
        "config.yaml": 100,
        "gpt.pth": 100_000_000,
        "s2mel.pth": 100_000_000,
        "codec.pth": 100_000_000,
        "feat1.pt": 1000,
        "feat2.pt": 1000,
        "wav2vec2bert_stats.pt": 1000,
        "multilingual_zh_ja_yue_char_del.tiktoken": 1000,
        "qwen0.6bemo4-merge/config.json": 100,
        "qwen0.6bemo4-merge/model.safetensors": 100_000_000,
        "qwen0.6bemo4-merge/tokenizer.json": 1000,
        "hf_cache/w2v-bert-2.0/config.json": 100,
        "hf_cache/w2v-bert-2.0/preprocessor_config.json": 100,
        "hf_cache/w2v-bert-2.0/model.safetensors": 100_000_000,
        "hf_cache/campplus_cn_common.bin": 100_000,
        "hf_cache/bigvgan/config.json": 100,
        "hf_cache/bigvgan/bigvgan_generator.pt": 100_000_000,
    }

    def __init__(
        self,
        install_dir: Path | None = None,
        dependencies_root: Path | None = None,
        python_runtime: PythonRuntimeManager | None = None,
    ):
        self.install_dir = (install_dir or models_root() / "indextts").resolve()
        self.dependency_dir = (
            (dependencies_root or engine_dependencies_root()) / "indextts"
        ).resolve()
        self.source_dir = self.dependency_dir / f"index-tts-{self.SOURCE_REVISION}"
        self.cache_dir = self.install_dir / "hf-cache"
        self.model_dir = self.install_dir / "checkpoints"
        self.python_runtime = python_runtime or PythonRuntimeManager()
        self._cancel_requested = threading.Event()
        self._process = None
        self._lock = threading.Lock()

    @property
    def python_exe(self):
        return (
            self.source_dir
            / ".venv"
            / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )

    @property
    def cli_path(self):
        return self.dependency_dir / "indextts_worker.py"

    @property
    def manifest_path(self):
        return self.install_dir / "indextts-install.json"

    def install_manifest(self):
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def has_model_files(self):
        return all(
            (self.model_dir / name).is_file()
            and (self.model_dir / name).stat().st_size >= size
            for name, size in self.REQUIRED_FILES.items()
        )

    def has_runtime(self):
        manifest = self.install_manifest()
        return (
            self.python_exe.is_file()
            and self.cli_path.is_file()
            and manifest.get("source_revision") == self.SOURCE_REVISION
            and manifest.get("model_revision") == self.MODEL_REVISION
            and manifest.get("version") == self.VERSION
            and manifest.get("runtime_dir") == str(self.dependency_dir)
            and manifest.get("state") == "installed"
        )

    def is_installed(self):
        return self.has_runtime() and self.has_model_files()

    def runtime_is_current(self):
        return self.has_runtime()

    def runtime_environment(self):
        env = gpu_runtime_environment()
        for key in (
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
        ):
            env.pop(key, None)
        env.update(
            PYTHONUTF8="1",
            PYTHONUNBUFFERED="1",
            PYTHONNOUSERSITE="1",
            HF_HOME=str(self.cache_dir),
            HF_HUB_CACHE=str(self.cache_dir / "hub"),
            HUGGINGFACE_HUB_CACHE=str(self.cache_dir / "hub"),
            TRANSFORMERS_CACHE=str(self.cache_dir / "hub"),
            UV_CACHE_DIR=str(self.dependency_dir / "uv-cache"),
            UV_PYTHON_INSTALL_DIR=str(self.dependency_dir / "python"),
        )
        return env

    def runtime_command(self):
        self.dependency_dir.mkdir(parents=True, exist_ok=True)
        self.cli_path.write_text(INDEXTTS_CLI, encoding="utf-8")
        return [
            str(self.python_exe),
            str(self.cli_path),
            "--source-dir",
            str(self.source_dir),
        ]

    def _check_cancelled(self):
        if self._cancel_requested.is_set():
            raise IndexTTSCancelled("IndexTTS operation cancelled.")

    def _run(self, command, progress, percent):
        self._check_cancelled()
        with self._lock:
            self._process = subprocess.Popen(
                command,
                cwd=self.source_dir,
                env=self.runtime_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process = self._process
        try:
            stdout, stderr = communicate_with_live_output(
                process,
                check_cancelled=self._check_cancelled,
                output_callback=progress_output_callback(progress, percent, "IndexTTS"),
            )
            self._check_cancelled()
            if process.returncode:
                raise IndexTTSError(
                    (stderr or stdout).decode("utf-8", errors="replace")[-6000:]
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            with self._lock:
                self._process = None

    def _download_source(self, progress):
        if (self.source_dir / "uv.lock").is_file():
            return
        archive = self.dependency_dir / "source.zip"
        url = f"https://codeload.github.com/index-tts/index-tts/zip/{self.SOURCE_REVISION}"
        progress(5, 100, "Downloading pinned IndexTTS source and license...")
        with (
            urllib.request.urlopen(url, timeout=60) as response,
            archive.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                self._check_cancelled()
                output.write(chunk)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (self.dependency_dir / member.filename).resolve()
                if not target.is_relative_to(self.source_dir.resolve()):
                    raise IndexTTSError("Invalid IndexTTS source archive path.")
            bundle.extractall(self.dependency_dir)
        archive.unlink()

    def _write_manifest(self, state, device, dtype):
        self.install_dir.mkdir(parents=True, exist_ok=True)
        data = dict(
            state=state,
            version=self.VERSION,
            source_revision=self.SOURCE_REVISION,
            model_revision=self.MODEL_REVISION,
            device=device,
            dtype=dtype,
            runtime_dir=str(self.dependency_dir),
            license="bilibili Model Use License Agreement",
        )
        temp = self.manifest_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp.replace(self.manifest_path)

    def install(
        self, device="cuda", dtype="bfloat16", progress_callback: Callable | None = None
    ):
        validated_config({"device": device, "dtype": dtype})
        progress = progress_callback or (lambda *args: None)
        self._cancel_requested.clear()
        self.dependency_dir.mkdir(parents=True, exist_ok=True)
        previous = self.install_manifest()
        self._write_manifest("installing", device, dtype)
        try:
            self._download_source(progress)
            if previous.get("runtime_dir") and previous["runtime_dir"] != str(
                self.dependency_dir
            ):
                # Virtual environments contain absolute paths; repair after moving AI storage.
                venv = (self.source_dir / ".venv").resolve()
                if not venv.is_relative_to(self.source_dir.resolve()):
                    raise IndexTTSError("Invalid IndexTTS virtual environment path.")
                if venv.exists():
                    shutil.rmtree(venv)
            self.python_runtime.install(progress, self._cancel_requested)
            bootstrap = self.dependency_dir / "bootstrap"
            uv_exe = bootstrap / ("bin/uv.exe" if os.name == "nt" else "bin/uv")
            if not uv_exe.is_file():
                progress(10, 100, "Installing isolated uv bootstrap...")
                self.python_runtime.run_python(
                    [
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "--target",
                        str(bootstrap),
                        f"uv=={self.UV_VERSION}",
                    ],
                    self._cancel_requested,
                    output_callback=progress_output_callback(
                        progress, 10, "IndexTTS uv"
                    ),
                )
            if not uv_exe.is_file():
                raise IndexTTSError(f"uv executable missing: {uv_exe}")
            progress(
                15,
                100,
                "Preparing Python 3.11 and pinned CUDA dependencies (uv.lock)...",
            )
            self._run(
                [
                    str(uv_exe),
                    "sync",
                    "--frozen",
                    "--no-default-groups",
                    "--python",
                    "3.11",
                    "--managed-python",
                ],
                progress,
                15,
            )
            progress(
                75,
                100,
                "Downloading model, emotional interpreter and auxiliary weights; validating runtime...",
            )
            self._run(
                [
                    *self.runtime_command(),
                    "--prepare",
                    "--model-dir",
                    str(self.model_dir),
                    "--model-revision",
                    self.MODEL_REVISION,
                    "--cache-dir",
                    str(self.cache_dir),
                    "--device",
                    device,
                    "--dtype",
                    dtype,
                    "--use-qwen-emo",
                    "1",
                ],
                progress,
                75,
            )
            if not self.has_model_files():
                raise IndexTTSError(
                    "IndexTTS preparation finished but required model files are missing or incomplete."
                )
            self._write_manifest("installed", device, dtype)
            progress(100, 100, "IndexTTS-2.5 is ready.")
            return self.install_dir
        except BaseException:
            self._write_manifest(
                "cancelled" if self._cancel_requested.is_set() else "failed",
                device,
                dtype,
            )
            raise

    def cancel(self):
        self._cancel_requested.set()
        self.python_runtime.cancel()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                process.terminate()

    def uninstall(self):
        self.cancel()
        # Only this manager's resolved, explicitly scoped storage roots are removed.
        for path in (self.install_dir, self.dependency_dir):
            resolved = path.resolve()
            if resolved == resolved.parent or resolved == Path.home().resolve():
                raise IndexTTSError(f"Refusing unsafe uninstall path: {resolved}")
            if resolved.exists():
                shutil.rmtree(resolved)

    def synthesize(self, text, output_path, voice_config):
        from .indextts_engine import IndexTTSTTSEngine

        engine = IndexTTSTTSEngine(self)
        try:
            return engine.synthesize_to_wav(text, output_path, voice_config)
        finally:
            engine.close()
