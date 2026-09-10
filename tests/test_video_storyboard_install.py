from __future__ import annotations

import hashlib
import json
import os
import threading
import zipfile
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from app.core import video_storyboard_install as installer
from app.core.settings_manager import DEFAULT_SETTINGS
from app.ui.video_storyboard_install_dialog import StoryboardInstallDialog
from app.ui.video_storyboard_settings import VideoStoryboardSettingsWidget

APP = QApplication.instance() or QApplication([])


def tr(key, fallback, **values):
    return fallback.format(**values)


def _inventory(manifest):
    info = {node: {"input": {"required": {}}} for node in manifest["required_nodes"]}
    for model in manifest["models"]:
        info.setdefault(model["node"], {"input": {"required": {}}})
        info[model["node"]]["input"]["required"].setdefault(model["input"], [[]])[0].append(model["filename"])
    return info


def test_manifests_have_pinned_complete_models_and_match_their_workflows():
    for role in installer.ROLES:
        manifest = installer.load_manifest(role)
        nodes = set()
        for workflow in manifest["workflows"]:
            data = json.loads((installer.manifest_directory(role) / workflow).read_text(encoding="utf-8"))
            nodes.update(n["class_type"] for n in data.values())
        assert nodes == set(manifest["required_nodes"])
        for model in manifest["models"]:
            assert len(model["sha256"]) == 64
            assert model["size_bytes"] > 1000000
            assert "/resolve/main/" not in model["url"]
        if role != "llm":
            info = _inventory(manifest)
            assert installer.inspect_manifest(manifest, info)["state"] == "installed"
            info.pop(manifest["required_nodes"][0])
            assert installer.inspect_manifest(manifest, info)["state"] == "missing"


def test_shared_models_and_camera_aliases_match_server_subfolders():
    model = installer.load_manifest("edit")["models"][-1]
    assert installer.match_model(model, ["Qwen/Qwen-Edit-2509-Multiple-Angles.safetensors"]) == "Qwen/Qwen-Edit-2509-Multiple-Angles.safetensors"
    assert installer.match_model(model, ["a/镜头转换.safetensors", "b/镜头转换.safetensors"]) == ""
    assert installer.match_model(model, ["wrong.safetensors"]) == ""


def test_remote_installation_never_creates_local_files(tmp_path):
    with pytest.raises(installer.StoryboardInstallError, match="local"):
        installer.install_comfy("video", {"url": "https://server.example", "comfy_root": str(tmp_path / "new")}, lambda e: None, threading.Event())
    assert not (tmp_path / "new").exists()


def test_export_package_contains_workflow_manifest_and_commands(tmp_path):
    path = tmp_path / "package.zip"
    installer.export_package("edit", path)
    with zipfile.ZipFile(path) as archive:
        assert "edit/manifest.json" in archive.namelist()
        assert "edit/workflow-3.json" in archive.namelist()
        assert b"cm-cli.py install ComfyUI-GGUF" in archive.read("edit/INSTALL.txt")


@pytest.fixture
def download_server():
    content = b"test-model-content" * 100000
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            requests.append(offset)
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header("Content-Range", f"bytes {offset}-{len(content)-1}/{len(content)}")
            self.send_header("Content-Length", str(len(content) - offset))
            self.end_headers()
            self.wfile.write(content[offset:])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/model", content, requests
    server.shutdown()
    server.server_close()
    thread.join()


def test_downloader_resumes_verifies_and_reuses_the_same_model(tmp_path, download_server):
    url, content, requests = download_server
    target = tmp_path / "model.gguf"
    part = tmp_path / "model.gguf.part"
    part.write_bytes(content[:100])
    events = []
    digest = hashlib.sha256(content).hexdigest()
    installer.download_file(url, target, events.append, threading.Event(), size=len(content), sha256=digest)
    assert requests == [100]
    assert target.read_bytes() == content
    assert not part.exists()
    assert any(e.get("total") == len(content) for e in events)
    installer.download_file(url, target, events.append, threading.Event(), size=len(content), sha256=digest)
    assert requests == [100]  # No second transfer for a shared model.


def test_cancelled_download_does_not_publish_a_partial_model(tmp_path, download_server):
    url, content, _ = download_server
    cancel = threading.Event()
    target = tmp_path / "model.gguf"
    def progress(event):
        if event.get("current"):
            cancel.set()
    with pytest.raises(installer.StoryboardInstallCancelled):
        installer.download_file(url, target, progress, cancel, size=len(content), sha256=hashlib.sha256(content).hexdigest())
    assert not target.exists()
    assert target.with_name("model.gguf.part").exists()


def test_paths_cannot_escape_manifest_destination(tmp_path):
    with pytest.raises(installer.StoryboardInstallError):
        installer.safe_child(tmp_path, "../outside")


def test_comfy_install_reuses_existing_models_and_validates_before_ready(tmp_path, monkeypatch):
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("# existing ComfyUI", encoding="utf-8")
    manifest = installer.load_manifest("image")
    info = _inventory(manifest)
    missing = manifest["models"][0]
    info[missing["node"]]["input"]["required"][missing["input"]] = [[]]
    captures = []
    monkeypatch.setattr(installer, "prepare_comfy", lambda *args: ("python.exe", {}))
    monkeypatch.setattr(installer, "http_json", lambda *args, **kwargs: info)
    def download(url, path, progress, cancel, **kwargs):
        captures.append((url, path, kwargs))
        info[missing["node"]]["input"]["required"][missing["input"]] = [[missing["filename"]]]
    monkeypatch.setattr(installer, "download_file", download)
    result = installer.install_comfy("image", {"url": "http://127.0.0.1:8188", "comfy_root": str(root)}, lambda e: None, threading.Event())
    assert result["state"] == "installed"
    assert len(captures) == 1
    assert captures[0][1] == root / "models/diffusion_models" / missing["filename"]
    assert captures[0][2]["sha256"] == missing["sha256"]
    assert (root / "user/default/workflows/LocalText2Voice/image/manifest.json").is_file()


def test_running_comfy_requires_its_existing_folder_before_any_install(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "http_json", lambda *args, **kwargs: _inventory(installer.load_manifest("image")))
    monkeypatch.setattr(installer, "prepare_comfy", lambda *args: pytest.fail("Must not create a second ComfyUI"))
    with pytest.raises(installer.StoryboardInstallError, match="already running"):
        installer.install_comfy("image", {"url": "http://127.0.0.1:8188", "comfy_root": str(tmp_path / "new")}, lambda e: None, threading.Event())


def test_ollama_pull_streams_layer_progress_and_validates_model(monkeypatch):
    import io
    monkeypatch.setattr(installer, "http_json", lambda *args, **kwargs: {"models": [{"name": "qwen3:8b"}]})
    events = []
    def urlopen(request, **kwargs):
        payload = json.loads(request.data)
        assert payload == {"model": "qwen3:8b", "stream": True}
        return io.BytesIO(b'{"status":"pulling","completed":100,"total":200,"digest":"abc"}\n{"status":"success"}\n')
    monkeypatch.setattr(installer.urllib.request, "urlopen", urlopen)
    result = installer.install_ollama({"url": "http://127.0.0.1:11434"}, events.append, threading.Event())
    assert result["state"] == "installed"
    assert any(event.get("current") == 100 and event.get("total") == 200 for event in events)


def test_summary_cards_follow_provider_settings_and_focus_sections():
    widget = VideoStoryboardSettingsWidget(tr)
    widget._checked_on_show = True
    widget.resize(1150, 800)
    widget.show()
    APP.processEvents()
    assert tuple(widget.engine_cards) == installer.ROLES
    assert "qwen3:8b" in widget.engine_cards["llm"]["model"].text()
    widget._focus_engine_section("edit")
    APP.processEvents()
    assert widget.settings_scroll.verticalScrollBar().value() > 0
    widget.llm_provider_combo.setCurrentIndex(widget.llm_provider_combo.findData("litellm"))
    widget.litellm_custom_model_edit.setText("provider/test-model")
    assert "test-model" in widget.engine_cards["llm"]["model"].text()
    widget._engine_inventory["image"] = {"state": "installed"}
    widget._refresh_engine_cards()
    assert widget.engine_cards["image"]["install"].text() == "Installed"
    widget.close()
    widget.deleteLater()


def test_install_modal_requires_confirmation_and_can_continue_hidden():
    config = deepcopy(DEFAULT_SETTINGS["video_storyboard"])
    config["comfyui_video"]["base_url"] = "https://remote.example"
    dialog = StoryboardInstallDialog(tr, "video", config)
    received = []
    dialog.installRequested.connect(received.append)
    assert not dialog.install_button.isEnabled()
    assert not received
    dialog.url_edit.setText("http://127.0.0.1:8188")
    dialog.install_button.click()
    assert len(received) == 1
    assert received[0]["headers"] == {}
    dialog.reject()
    assert dialog.active
    assert not dialog.isVisible()
    dialog.finish_install("Done", True)
    dialog.reject()
    dialog.deleteLater()
