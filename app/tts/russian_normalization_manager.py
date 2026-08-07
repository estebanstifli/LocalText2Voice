from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.tts.base import TTSEngineError
from app.utils.paths import engine_dependencies_root, models_root

from .install_logging import (
    detailed_pip_args,
    progress_output_callback,
    report_process_command,
    run_python_with_live_output,
)
from .python_runtime_manager import (
    PythonRuntimeCancelled,
    PythonRuntimeError,
    PythonRuntimeManager,
)


class RussianNormalizationError(TTSEngineError):
    pass


class RussianNormalizationCancelled(RussianNormalizationError):
    pass


RussianNormalizationProgress = Callable[[int, int, str], None]


_MARKUP_COMMAND = re.compile(r"(\{\{.*?\}\})", re.DOTALL)


_ACCENTOR_SCRIPT = r'''
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def configure_dependencies(path: Path) -> None:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    candidates = [path, path / "torch" / "lib"]
    for candidate in candidates:
        if not candidate.exists():
            continue
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(candidate))
            except OSError:
                pass
    paths = [str(candidate) for candidate in candidates if candidate.exists()]
    if paths:
        os.environ["PATH"] = os.pathsep.join(paths + [os.environ.get("PATH", "")])


dependencies = Path(sys.argv[1])
configure_dependencies(dependencies)
from silero_stress import load_accentor

request = json.loads(sys.stdin.read() or "{}")
texts = request.get("texts", [])
if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
    raise ValueError("The Russian normalizer expects a list of text strings.")

accentor = load_accentor()
print(json.dumps({"texts": [str(accentor(text)) for text in texts]}, ensure_ascii=False))
'''.strip()


class RussianNormalizationManager:
    """Manage the optional, shared Silero Russian text-normalization runtime.

    Silero is installed below the user-selected AI model folder (Settings >
    General), rather than beside Python-only engine dependencies. This keeps it
    together with TTS and Faster Whisper models when the user chooses another
    drive.
    """

    VERSION = "russian-normalization-v1"
    INSTALL_FILENAME = "russian-normalization-install.json"
    TORCH_VERSION = "2.8.0"
    SILERO_STRESS_VERSION = "1.4"
    CPU_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cpu"
    REQUIREMENTS = (
        f"torch=={TORCH_VERSION}",
        f"silero-stress=={SILERO_STRESS_VERSION}",
    )
    INSTALL_DIRECTORY_NAME = "silero-stress"
    LEGACY_INSTALL_DIRECTORY_NAME = "russian-normalizer"

    def __init__(
        self,
        python_runtime: PythonRuntimeManager | None = None,
        install_root: Path | None = None,
        dependencies_root: Path | None = None,
    ) -> None:
        if install_root is not None and dependencies_root is not None:
            raise ValueError(
                "Specify either install_root or dependencies_root, not both."
            )
        self.python_runtime = python_runtime or PythonRuntimeManager()
        # ``dependencies_root`` is retained as a test/backwards-compatible
        # constructor alias. New callers should use ``install_root``.
        explicit_root = install_root or dependencies_root
        self.install_root = (explicit_root or models_root()).resolve()
        self._legacy_install_root = (
            engine_dependencies_root().resolve()
            if explicit_root is None
            else None
        )
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    @property
    def dependency_dir(self) -> Path:
        return self.install_dir / "site-packages"

    @property
    def install_dir(self) -> Path:
        """Folder stored with the selected TTS and Whisper model folders."""
        return self.install_root / self.INSTALL_DIRECTORY_NAME

    @property
    def manifest_path(self) -> Path:
        return self.install_dir / self.INSTALL_FILENAME

    def is_installed(self) -> bool:
        manifest = self._read_manifest()
        return (
            self.python_runtime.is_installed()
            and manifest.get("state") == "installed"
            and manifest.get("version") == self.VERSION
            and (self.dependency_dir / "torch").is_dir()
            and (self.dependency_dir / "silero_stress").is_dir()
        )

    def install(
        self,
        progress_callback: RussianNormalizationProgress | None = None,
        cancel_token: threading.Event | None = None,
    ) -> Path:
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        if self.is_installed():
            progress(100, 100, "Russian normalization runtime already installed.")
            return self.dependency_dir

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
            self._check_cancelled(cancel_token)
            if self._migrate_legacy_install(progress, cancel_token):
                return self.dependency_dir
            self._reset_dependency_dir()
            self._write_manifest("installing")
            progress(35, 100, "Installing Silero Stress and its CPU dependencies...")
            pip_args = detailed_pip_args(
                [
                    "install",
                    "--upgrade",
                    "--target",
                    str(self.dependency_dir),
                    "--no-warn-script-location",
                    "--index-url",
                    self.CPU_TORCH_INDEX_URL,
                    "--extra-index-url",
                    "https://pypi.org/simple",
                    *self.REQUIREMENTS,
                ]
            )
            report_process_command(progress, 35, "pip", ["pip", *pip_args])
            run_python_with_live_output(
                self.python_runtime,
                ["-m", "pip", *pip_args],
                cancel_token,
                progress_output_callback(progress, 35, "pip"),
            )
            self._check_cancelled(cancel_token)
            progress(88, 100, "Validating Silero Stress...")
            self._validate_runtime(cancel_token)
            self._write_manifest("installed")
            progress(100, 100, "Russian Silero normalization is ready.")
            return self.dependency_dir
        except (PythonRuntimeCancelled, RussianNormalizationCancelled):
            self._write_manifest("cancelled")
            raise RussianNormalizationCancelled("Russian normalization installation cancelled.")
        except (PythonRuntimeError, OSError) as exc:
            self._write_manifest("failed")
            raise RussianNormalizationError(str(exc)) from exc
        except Exception:
            self._write_manifest("failed")
            raise

    def uninstall(self) -> None:
        self.cancel()
        self._remove_path(self.install_dir)
        self._remove_legacy_install()

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

    def accentuate(self, text: str, cancel_token: threading.Event | None = None) -> str:
        """Return Russian prose with stress marks and restored ``ё`` characters.

        LTV markup stays byte-for-byte unchanged and all text spans are processed
        in one worker request, so the accentor is loaded once per document.
        """
        if not text or not self.is_installed():
            if not text:
                return text
            raise RussianNormalizationError(
                "Russian Silero normalization is not installed. Open Text Normalization and install it first."
            )
        self._cancel_requested.clear()
        parts = _MARKUP_COMMAND.split(text)
        indices = [index for index in range(0, len(parts), 2) if parts[index]]
        if not indices:
            return text
        values = [parts[index] for index in indices]
        accented = self._accentuate_many(values, cancel_token)
        if len(accented) != len(indices):
            raise RussianNormalizationError(
                "Russian Silero normalization returned an unexpected number of text parts."
            )
        for index, value in zip(indices, accented, strict=True):
            parts[index] = value
        return "".join(parts)

    def _accentuate_many(
        self,
        texts: list[str],
        cancel_token: threading.Event | None,
    ) -> list[str]:
        self._check_cancelled(cancel_token)
        command = [str(self.python_runtime.python_exe), "-c", _ACCENTOR_SCRIPT, str(self.dependency_dir)]
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONPATH"] = str(self.dependency_dir)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
        except OSError as exc:
            raise RussianNormalizationError(
                f"Could not start Russian Silero normalization: {exc}"
            ) from exc
        with self._lock:
            self._process = process
        try:
            if process.stdin is not None:
                process.stdin.write(json.dumps({"texts": texts}, ensure_ascii=False).encode("utf-8"))
                process.stdin.close()
            while process.poll() is None:
                self._check_cancelled(cancel_token)
                time.sleep(0.05)
            stdout = process.stdout.read() if process.stdout is not None else b""
            stderr = process.stderr.read() if process.stderr is not None else b""
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
        if process.returncode != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            raise RussianNormalizationError(
                "Russian Silero normalization failed: " + (details or "no error details returned.")
            )
        try:
            payload = json.loads(stdout.decode("utf-8", errors="replace").strip())
        except json.JSONDecodeError as exc:
            raise RussianNormalizationError(
                "Russian Silero normalization returned an invalid response."
            ) from exc
        values = payload.get("texts") if isinstance(payload, dict) else None
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise RussianNormalizationError(
                "Russian Silero normalization returned an invalid text response."
            )
        return values

    def _validate_runtime(self, cancel_token: threading.Event | None) -> None:
        code = (
            "import os, sys; from pathlib import Path; "
            f"deps=Path({str(self.dependency_dir)!r}); sys.path.insert(0, str(deps)); "
            "dlls=[deps, deps/'torch'/'lib']; "
            "paths=[str(path) for path in dlls if path.exists()]; "
            "os.environ['PATH']=os.pathsep.join(paths+[os.environ.get('PATH','')]); "
            "import torch, silero_stress; print(torch.__version__)"
        )
        run_python_with_live_output(
            self.python_runtime,
            ["-c", code],
            cancel_token,
            None,
        )

    def _reset_dependency_dir(self) -> None:
        try:
            self._remove_path(self.dependency_dir)
        except OSError as exc:
            raise RussianNormalizationError(
                "Could not clean the previous Russian normalization installation. "
                "Close other LocalText2Voice processes and try again. "
                f"Windows reported: {exc}"
            ) from exc
        self.dependency_dir.mkdir(parents=True, exist_ok=True)

    def _migrate_legacy_install(
        self,
        progress: RussianNormalizationProgress,
        cancel_token: threading.Event | None,
    ) -> bool:
        """Move the v1 preview location into the selected models directory.

        The old location was ``data/engine-deps/russian-normalizer``. Migrate
        only during the user's explicit Install action, so no large files are
        moved silently at application startup.
        """
        legacy_dir = self._legacy_install_dir()
        if legacy_dir is None or not self._legacy_is_installed(legacy_dir):
            return False
        self._check_cancelled(cancel_token)
        progress(
            35,
            100,
            "Moving the existing Silero Stress installation to the selected AI models folder...",
        )
        try:
            self._remove_path(self.install_dir)
            self.install_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_dir), str(self.install_dir))
            self._validate_runtime(cancel_token)
            self._write_manifest("installed")
            legacy_manifest = self._legacy_manifest_path()
            if legacy_manifest is not None:
                self._remove_path(legacy_manifest)
        except (OSError, PythonRuntimeError) as exc:
            raise RussianNormalizationError(
                "Could not move the existing Silero Stress installation to the "
                f"selected AI models folder. Windows reported: {exc}"
            ) from exc
        progress(100, 100, "Russian Silero normalization is ready.")
        return True

    def _legacy_install_dir(self) -> Path | None:
        if self._legacy_install_root is None:
            return None
        return self._legacy_install_root / self.LEGACY_INSTALL_DIRECTORY_NAME

    def _legacy_manifest_path(self) -> Path | None:
        if self._legacy_install_root is None:
            return None
        return self._legacy_install_root / self.INSTALL_FILENAME

    def _legacy_is_installed(self, legacy_dir: Path) -> bool:
        manifest_path = self._legacy_manifest_path()
        if manifest_path is None:
            return False
        manifest = self._read_manifest_at(manifest_path)
        return (
            manifest.get("state") == "installed"
            and manifest.get("version") == self.VERSION
            and (legacy_dir / "site-packages" / "torch").is_dir()
            and (legacy_dir / "site-packages" / "silero_stress").is_dir()
        )

    def _remove_legacy_install(self) -> None:
        legacy_dir = self._legacy_install_dir()
        if legacy_dir is not None:
            self._remove_path(legacy_dir)
        legacy_manifest = self._legacy_manifest_path()
        if legacy_manifest is not None:
            self._remove_path(legacy_manifest)

    def _check_cancelled(self, cancel_token: threading.Event | None) -> None:
        if self._cancel_requested.is_set() or (
            cancel_token is not None and cancel_token.is_set()
        ):
            raise RussianNormalizationCancelled("Russian normalization cancelled.")

    def _read_manifest(self) -> dict[str, Any]:
        return self._read_manifest_at(self.manifest_path)

    @staticmethod
    def _read_manifest_at(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_manifest(self, state: str) -> None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "component": "russian_normalization",
            "version": self.VERSION,
            "state": state,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dependency_dir": str(self.dependency_dir),
            "python_runtime": str(self.python_runtime.python_exe),
            "requirements": list(self.REQUIREMENTS),
        }
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
