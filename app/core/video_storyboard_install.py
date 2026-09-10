"""Manifest-driven, opt-in installation of the default Storyboard engines."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from app.core.comfyui_http import comfyui_request_headers
from app.tts.install_logging import communicate_with_live_output, install_detail_text, is_install_detail
from app.tts.model_cache import format_file_size
from app.tts.python_runtime_manager import PythonRuntimeManager
from app.utils.paths import large_assets_root, resource_root

ROLES = ("llm", "image", "video", "edit")
Progress = Callable[[dict[str, Any]], None]


class StoryboardInstallError(RuntimeError):
    pass


class StoryboardInstallCancelled(StoryboardInstallError):
    pass


def manifest_directory(role: str) -> Path:
    if role not in ROLES:
        raise StoryboardInstallError("Unknown Storyboard engine.")
    return resource_root() / "assets" / "storyboard_engines" / role


def load_manifest(role: str) -> dict[str, Any]:
    value = json.loads((manifest_directory(role) / "manifest.json").read_text(encoding="utf-8"))
    if value.get("id") != role:
        raise StoryboardInstallError("The installation manifest has an invalid ID.")
    for model in value["models"]:
        safe_child(Path("models").resolve(), model["folder"], model["filename"])
    return value


def safe_child(root: Path, *parts: str) -> Path:
    root = root.resolve()
    result = root.joinpath(*parts).resolve()
    if result == root or not result.is_relative_to(root):
        raise StoryboardInstallError("An installation path escapes the selected directory.")
    return result


def default_comfy_root() -> Path:
    return large_assets_root() / "engines" / "storyboard-comfyui" / "ComfyUI"


def is_local_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def endpoint_for(role: str, configuration: dict) -> tuple[str, dict]:
    key = {"llm": "ollama", "image": "comfyui", "video": "comfyui_video", "edit": "comfyui_image_edit"}[role]
    data = configuration.get(key, {})
    url = str(data.get("base_url") or ("http://127.0.0.1:11434" if role == "llm" else "http://127.0.0.1:8188")).rstrip("/")
    token = str(data.get("auth_token") or "")
    return url, {"Authorization": f"Bearer {token}"} if token else {}


def http_json(url: str, headers: dict | None = None, timeout: float = 15) -> dict:
    request = urllib.request.Request(url, headers=comfyui_request_headers(headers or {}))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise StoryboardInstallError("The service returned invalid JSON.")
    return value


def choices(info: dict, node: str, field: str) -> list[str]:
    inputs = info.get(node, {}).get("input", {})
    definition = inputs.get("required", {}).get(field) or inputs.get("optional", {}).get(field) or []
    if definition and isinstance(definition[0], list):
        return [str(v) for v in definition[0]]
    if len(definition) > 1 and isinstance(definition[1], dict):
        return [str(v) for v in definition[1].get("options", [])]
    return []


def match_model(model: dict, values: list[str]) -> str:
    filenames = {str(model["filename"]).casefold(), *(str(v).casefold() for v in model.get("aliases", []))}
    if model["filename"] in values:
        return model["filename"]
    matches = [v for v in values if v.replace("\\", "/").rsplit("/", 1)[-1].casefold() in filenames]
    return matches[0] if len(matches) == 1 else ""


def inspect_manifest(manifest: dict, info: dict) -> dict:
    missing_nodes = sorted(set(manifest["required_nodes"]) - set(info))
    matched = {}
    missing = []
    for model in manifest["models"]:
        found = match_model(model, choices(info, model["node"], model["input"]))
        if found:
            matched[model["filename"]] = found
        elif not model.get("optional"):
            missing.append(model["filename"])
    return {"state": "installed" if not missing and not missing_nodes else "missing",
            "missing_models": missing, "missing_nodes": missing_nodes, "matched_models": matched}


def probe_engines(configuration: dict) -> dict:
    result = {}
    cache: dict[tuple, Any] = {}
    for role in ROLES:
        url, headers = endpoint_for(role, configuration)
        key = (url, tuple(headers.items()), role == "llm")
        try:
            if key not in cache:
                try:
                    cache[key] = http_json(url + ("/api/tags" if role == "llm" else "/object_info"), headers, timeout=8)
                except Exception as exc:
                    cache[key] = exc
            info = cache[key]
            if isinstance(info, Exception):
                raise info
            manifest = load_manifest(role)
            if role == "llm":
                found = any(v.get("name", v.get("model")) == manifest["ollama_model"] for v in info.get("models", []))
                result[role] = {"state": "installed" if found else "missing", "missing_models": [] if found else [manifest["ollama_model"]]}
            else:
                result[role] = inspect_manifest(manifest, info)
        except Exception as exc:
            # Do not embed endpoint URLs or auth values in UI logs.
            result[role] = {"state": "unavailable", "detail": f"Service unavailable ({type(exc).__name__})."}
    return result


def export_package(role: str, destination: Path) -> None:
    """Portable documentation/workflow package, with no credentials."""
    manifest = load_manifest(role)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ["manifest.json", *manifest["workflows"]]:
            archive.write(manifest_directory(role) / name, f"{role}/{name}")
        guide = [manifest["name"], "", "Run these commands on the ComfyUI host using its Python environment:",
                 "python custom_nodes/ComfyUI-Manager/cm-cli.py install " + " ".join(n["name"] for n in manifest["custom_nodes"]),
                 "", "Download the following files into the indicated ComfyUI/models subfolders.",
                 "Verify size_bytes and sha256 from manifest.json after downloading."]
        for model in manifest["models"]:
            guide += ["", f"models/{model['folder']}/{model['filename']}" + (" (optional)" if model.get("optional") else ""), model["url"]]
        guide += ["", "Restart ComfyUI after installing nodes/models, then refresh the engine status in the app."]
        archive.writestr(f"{role}/INSTALL.txt", "\n".join(guide))


def check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise StoryboardInstallCancelled("Installation cancelled. Partial model downloads are kept for resuming.")


def verified_file(path: Path, size: int, sha256: str, progress: Progress, cancel: threading.Event) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    last = 0.0
    done = 0
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            check_cancel(cancel)
            digest.update(chunk)
            done += len(chunk)
            if time.monotonic() - last > 1:
                progress({"message": f"Verifying {path.name}", "current": done, "total": size})
                last = time.monotonic()
    return digest.hexdigest() == sha256


def download_file(url: str, target: Path, progress: Progress, cancel: threading.Event,
                  *, size: int = 0, sha256: str = "") -> None:
    """Resumable download, pinned model integrity, atomic publication."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if size and sha256 and verified_file(target, size, sha256, progress, cancel):
        progress({"message": f"✓ Already installed: {target.name}", "current": size, "total": size})
        return
    part = target.with_name(target.name + ".part")
    for attempt in range(3):
        check_cancel(cancel)
        offset = part.stat().st_size if part.exists() else 0
        if size and offset == size and verified_file(part, size, sha256, progress, cancel):
            os.replace(part, target)
            return
        if size and offset >= size:
            part.unlink()
            offset = 0
        headers = {"User-Agent": "LocalText2Voice-Installer/1.0", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
                if offset and response.status == 206:
                    if not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                        raise StoryboardInstallError("The download server returned an invalid resume range.")
                elif response.status == 200:
                    offset = 0
                else:
                    raise StoryboardInstallError(f"Unexpected download status: {response.status}")
                total = size or (offset + int(response.headers.get("Content-Length", 0)))
                required = max(0, total - offset)
                if required and shutil.disk_usage(target.parent).free < required + 256 * 1024**2:
                    raise StoryboardInstallError(f"Not enough disk space to download {target.name}.")
                downloaded, started, last = offset, time.monotonic(), 0.0
                with part.open("ab" if offset else "wb") as stream:
                    while chunk := response.read(1024 * 1024):
                        check_cancel(cancel)
                        stream.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if now - last >= 0.5:
                            speed = (downloaded - offset) / max(0.001, now - started)
                            progress({"message": f"↓ {target.name}", "current": downloaded, "total": total,
                                      "speed": speed, "eta": (total - downloaded) / speed if speed and total else 0})
                            last = now
            check_cancel(cancel)
            if total and part.stat().st_size != total:
                raise OSError("Incomplete download; retrying from the last byte.")
            if sha256 and not verified_file(part, size, sha256, progress, cancel):
                part.unlink(missing_ok=True)
                raise OSError("Downloaded checksum does not match the manifest; retrying.")
            os.replace(part, target)
            progress({"message": f"✓ {target.name}", "current": downloaded, "total": total})
            return
        except (OSError, urllib.error.URLError) as exc:
            check_cancel(cancel)
            if attempt == 2:
                raise StoryboardInstallError(f"Download failed for {target.name} ({type(exc).__name__}). Retry to resume.") from exc
            progress({"message": f"Retry {attempt + 1}/2: {target.name}"})


def run_command(args: list[str], cwd: Path, progress: Progress, cancel: threading.Event,
                env: dict | None = None) -> None:
    check_cancel(cancel)
    process = subprocess.Popen(args, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        communicate_with_live_output(process, lambda: check_cancel(cancel),
                                     lambda _source, line: progress({"message": line}))
        if process.returncode:
            raise StoryboardInstallError(f"{Path(args[0]).name} exited with code {process.returncode}. See the log above.")
    except BaseException:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()
            process.wait(timeout=15)
        raise


def find_comfy_python(root: Path) -> str:
    candidates = [root / ".venv/Scripts/python.exe", root / "venv/Scripts/python.exe",
                  root.parent / f".{root.name}-ltv-runtime/python/python.exe",
                  root.parent / "python_embeded/python.exe", root.parent / "runtime/python/python.exe",
                  root / ".venv/bin/python", root / "venv/bin/python", root.parent / "runtime/python/bin/python"]
    return str(next((p for p in candidates if p.is_file()), ""))


def python_script_command(python: str, script: Path, *args: str) -> list[str]:
    # Embedded Python ignores PYTHONPATH and may omit the script directory.
    # Add both import roots explicitly for ComfyUI and Manager.
    bootstrap = (
        "import os,runpy,sys; from pathlib import Path; "
        "script=sys.argv[1]; sys.path[0:0]=[str(Path(script).parent),os.getcwd()]; "
        "sys.argv=sys.argv[1:]; runpy.run_path(script,run_name='__main__')"
    )
    return [python, "-u", "-c", bootstrap, str(script), *args]


def _extract_zip(archive: Path, root: Path) -> None:
    with zipfile.ZipFile(archive) as stream:
        for name in stream.namelist():
            safe_child(root, name)
        stream.extractall(root)


def _ensure_git(progress: Progress, cancel: threading.Event) -> str:
    found = shutil.which("git")
    if found:
        return found
    root = large_assets_root() / "engines/storyboard-tools/git"
    executable = root / "cmd/git.exe"
    if executable.is_file():
        return str(executable)
    if os.name != "nt":
        raise StoryboardInstallError("Install Git with your system package manager, then retry.")
    release = http_json("https://api.github.com/repos/git-for-windows/git/releases/latest")
    asset = next((v for v in release.get("assets", []) if v["name"].startswith("MinGit-") and v["name"].endswith("-64-bit.zip") and "busybox" not in v["name"]), None)
    if not asset:
        raise StoryboardInstallError("Cannot locate the official MinGit Windows package.")
    archive = root.parent / "mingit.zip"
    download_file(asset["browser_download_url"], archive, progress, cancel)
    _extract_zip(archive, root)
    return str(executable)


def prepare_comfy(root: Path, python: str, progress: Progress, cancel: threading.Event) -> tuple[str, dict]:
    if root.exists() and not (root / "main.py").is_file() and any(root.iterdir()):
        raise StoryboardInstallError("Select the ComfyUI directory containing main.py, or an empty directory for a new installation.")
    managed = not (root / "main.py").is_file()
    git = _ensure_git(progress, cancel)
    env = dict(os.environ)
    env["PATH"] = str(Path(git).parent) + os.pathsep + env.get("PATH", "")
    env["GIT_PYTHON_GIT_EXECUTABLE"] = git
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["COMFYUI_PATH"] = str(root)
    if managed:
        root.parent.mkdir(parents=True, exist_ok=True)
        progress({"message": "Installing isolated ComfyUI and its Python runtime…"})
        runtime = PythonRuntimeManager(runtime_dir=root.parent / f".{root.name}-ltv-runtime")
        runtime.install(lambda current, total, msg: progress({"message": install_detail_text(msg) if is_install_detail(msg) else msg,
                                                            "current": current, "total": total}), cancel)
        python = str(runtime.python_exe)
        run_command([git, "clone", "--depth", "1", "https://github.com/Comfy-Org/ComfyUI.git", str(root)], root.parent, progress, cancel, env)
        # A receipt lets an interrupted dependency install be resumed safely.
        (root / ".ltv-managed-runtime").write_text(python, encoding="utf-8")
    if not python:
        python = find_comfy_python(root)
    if not python or not Path(python).is_file():
        raise StoryboardInstallError("Select the Python executable used by this ComfyUI installation.")
    env["PYTHONPATH"] = str(root)
    marker = root / ".ltv-managed-runtime"
    if marker.exists() and not (root / ".ltv-dependencies-ready").exists():
        # NVIDIA is the local default for Storyboard; existing environments are
        # never converted or have their torch installation replaced here.
        run_command([python, "-m", "pip", "install", "torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu128"], root, progress, cancel, env)
        run_command([python, "-m", "pip", "install", "-r", str(root / "requirements.txt")], root, progress, cancel, env)
        (root / ".ltv-dependencies-ready").write_text("installed", encoding="utf-8")
    return python, env


def install_comfy(role: str, options: dict, progress: Progress, cancel: threading.Event) -> dict:
    url = str(options["url"]).rstrip("/")
    if not is_local_url(url):
        raise StoryboardInstallError("Automatic installation requires a local ComfyUI directory. Export this workflow package to install it on the remote host.")
    root = Path(options["comfy_root"]).expanduser().resolve()
    manifest = load_manifest(role)
    headers = options.get("headers", {})
    try:
        info = http_json(url + "/object_info", headers, timeout=5)
    except Exception:
        info = {}
    if info and not (root / "main.py").is_file():
        raise StoryboardInstallError("ComfyUI is already running at this URL. Select its existing folder and Python executable in this dialog before installing components.")
    custom_classes = {c for node in manifest["custom_nodes"] for c in node["classes"]}
    missing_core = set(manifest["required_nodes"]) - set(info) - custom_classes
    if info and missing_core:
        raise StoryboardInstallError("Update this ComfyUI installation before installing the workflow; missing built-in nodes: " + ", ".join(sorted(missing_core)))
    python, env = prepare_comfy(root, str(options.get("python") or ""), progress, cancel)
    progress({"message": "✓ ComfyUI Python environment ready"})
    missing_nodes = [n for n in manifest["custom_nodes"] if not all(c in info for c in n["classes"])]
    if missing_nodes:
        manager = root / "custom_nodes/ComfyUI-Manager"
        if not (manager / "cm-cli.py").is_file():
            progress({"message": "Installing ComfyUI-Manager…"})
            run_command([env["GIT_PYTHON_GIT_EXECUTABLE"], "clone", "--depth", "1", "https://github.com/Comfy-Org/ComfyUI-Manager.git", str(manager)], root, progress, cancel, env)
        run_command([python, "-m", "pip", "install", "-r", str(manager / "requirements.txt")], root, progress, cancel, env)
        progress({"message": "Installing custom nodes and their dependencies with cm-cli…"})
        run_command(python_script_command(python, manager / "cm-cli.py", "install", *(n["name"] for n in missing_nodes)), root, progress, cancel, env)
    directory = root / "user/default/workflows/LocalText2Voice" / role
    directory.mkdir(parents=True, exist_ok=True)
    for name in ["manifest.json", *manifest["workflows"]]:
        shutil.copy2(manifest_directory(role) / name, directory / name)
    progress({"message": "✓ Workflow package installed"})
    models = [m for m in manifest["models"] if not m.get("optional") or options.get("include_camera")]
    for index, model in enumerate(models, 1):
        check_cancel(cancel)
        # The server may use extra_model_paths.yaml or shared subdirectories.
        # Its inventory is authoritative: never download a second copy.
        existing = match_model(model, choices(info, model["node"], model["input"]))
        if existing:
            progress({"message": f"✓ Available in ComfyUI: {existing}", "step": index, "steps": len(models)})
            continue
        progress({"message": f"Model {index}/{len(models)}: {model['filename']}"})
        download_file(model["url"], safe_child(root / "models", model["folder"], model["filename"]),
                      progress, cancel, size=model["size_bytes"], sha256=model["sha256"])
    check_cancel(cancel)
    if not info:
        progress({"message": "Starting ComfyUI. Initial startup may take several minutes…"})
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.path not in {"", "/"}:
            raise StoryboardInstallError("Files installed. Start ComfyUI with your HTTPS/proxy configuration, then refresh status.")
        with (root / "localtext2voice-server.log").open("ab") as log:
            process = subprocess.Popen(python_script_command(python, root / "main.py", "--listen", "127.0.0.1", "--port", str(parsed.port or 8188), "--lowvram"),
                                       cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            for _ in range(180):
                check_cancel(cancel)
                if process.poll() is not None:
                    raise StoryboardInstallError(f"ComfyUI stopped during startup. See {root / 'localtext2voice-server.log'}")
                try:
                    info = http_json(url + "/object_info", headers, timeout=2)
                    break
                except Exception:
                    cancel.wait(1)
            else:
                raise StoryboardInstallError(f"ComfyUI startup timed out. See {root / 'localtext2voice-server.log'}")
        except BaseException:
            process.terminate()
            process.wait(timeout=15)
            raise
    else:
        info = http_json(url + "/object_info", headers, timeout=15)
    result = inspect_manifest(manifest, info)
    result.update({"role": role, "comfy_root": str(root), "python": python, "url": url})
    if result["state"] != "installed":
        progress({"message": "Files installed. Restart this ComfyUI instance to load new nodes/models, then check status again."})
    else:
        progress({"message": "✓ Final validation: all required nodes and models are available."})
    return result


def install_ollama(options: dict, progress: Progress, cancel: threading.Event) -> dict:
    url = str(options["url"]).rstrip("/")
    if not is_local_url(url):
        raise StoryboardInstallError("Automatic Ollama installation is local only. Install the model on the remote host.")
    try:
        http_json(url + "/api/tags", timeout=3)
    except Exception:
        executable = shutil.which("ollama")
        if not executable and os.name == "nt":
            candidates = [large_assets_root() / "engines/storyboard-ollama/app/ollama.exe",
                          Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"]
            executable = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
        if not executable:
            if os.name != "nt":
                raise StoryboardInstallError("Install Ollama from https://ollama.com/download, then retry to download Qwen3.")
            root = large_assets_root() / "engines/storyboard-ollama"
            root.mkdir(parents=True, exist_ok=True)
            installer = root / "OllamaSetup.exe"
            download_file("https://ollama.com/download/OllamaSetup.exe", installer, progress, cancel)
            progress({"message": "Installing Ollama. Its installer will complete before model download starts…"})
            install_env = {**os.environ, "OLLAMA_HOST": url,
                           "OLLAMA_MODELS": str(large_assets_root() / "models/ollama")}
            run_command([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/DIR={root / 'app'}"], root, progress, cancel, install_env)
            executable = str(root / "app/ollama.exe")
        for _ in range(5):
            try:
                http_json(url + "/api/tags", timeout=2)
                break
            except Exception:
                cancel.wait(1)
        else:
            env = dict(os.environ)
            env["OLLAMA_HOST"] = url
            env["OLLAMA_MODELS"] = str(large_assets_root() / "models/ollama")
            progress({"message": f"Starting Ollama. Model storage: {env['OLLAMA_MODELS']}"})
            subprocess.Popen([executable, "serve"], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            for _ in range(30):
                check_cancel(cancel)
                try:
                    http_json(url + "/api/tags", timeout=2)
                    break
                except Exception:
                    cancel.wait(1)
            else:
                raise StoryboardInstallError("Ollama did not start. Start Ollama manually and retry.")
    model = load_manifest("llm")["ollama_model"]
    progress({"message": f"Downloading {model} via Ollama. Existing layers are reused; Ollama controls its storage directory."})
    request = urllib.request.Request(url + "/api/pull", data=json.dumps({"model": model, "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        for line in response:
            check_cancel(cancel)
            event = json.loads(line)
            if event.get("error"):
                raise StoryboardInstallError(str(event["error"]))
            progress({"message": str(event.get("status", "")) + (" · " + str(event["digest"])[:20] if event.get("digest") else ""),
                      "current": event.get("completed", 0), "total": event.get("total", 0)})
    tags = http_json(url + "/api/tags")
    if not any(v.get("name", v.get("model")) == model for v in tags.get("models", [])):
        raise StoryboardInstallError("Ollama finished without listing Qwen3:8b. Check its server logs.")
    progress({"message": "✓ Ollama + Qwen3:8b installed and validated."})
    return {"role": "llm", "state": "installed", "url": url}
