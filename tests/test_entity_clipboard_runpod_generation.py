import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.ui.video_storyboard_entity_dialog import VideoStoryboardEntityDialog
from app.core.video_storyboard_comfyui import generate_storyboard_frame, VideoStoryboardImageError
from app.core import video_storyboard_runpod as rp


def tr(key, default, **values):
    return default.format(**values)


@pytest.mark.parametrize("kind", ["character", "location", "object"])
def test_clipboard_buttons_context_menu_and_file_loading(kind, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardEntityDialog(tr, kind, 30)
    image = QImage(640, 480, QImage.Format.Format_ARGB32)
    image.fill(0xff123456)
    app.clipboard().setImage(image)
    dialog.paste_image_button.click()
    pasted = dialog.reference_image_path
    assert QImage(pasted).size() == image.size()
    app.clipboard().clear()
    dialog.copy_image_button.click()
    assert app.clipboard().image().size() == image.size()  # original, not preview
    menu = dialog._reference_menu()
    assert [a.text() for a in menu.actions()] == ["Copy", "Paste", "Load from file"]
    target = tmp_path / "loaded.png"
    Image.new("RGB", (200, 100), "red").save(target)
    monkeypatch.setattr("app.ui.video_storyboard_entity_dialog.QFileDialog.getOpenFileName", lambda *a: (str(target), ""))
    menu.actions()[2].trigger()
    assert dialog.reference_image_path == str(target)
    app.clipboard().setText("not an image")
    dialog.paste_image_button.click()
    assert dialog.reference_image_path == str(target)
    assert "no readable image" in dialog.image_status.text()
    dialog._set_generating(True)
    assert not dialog.paste_image_button.isEnabled()
    assert not dialog._reference_menu().actions()[2].isEnabled()
    dialog._set_generating(False)
    dialog.close()


def test_runpod_qwen_references_use_selected_generation_endpoint_and_lazy_staging(tmp_path, monkeypatch):
    plan = {"continuity": {}}
    scene = {"id": "1", "prompt": "Ana holds the key inside the house"}
    for collection, name in (("characters", "Ana"), ("locations", "house"), ("objects", "key")):
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (32, 32)).save(path)
        plan["continuity"][collection] = [{"id": name, "name": name, "reference_image_path": str(path)}]
        scene[collection] = [name]
    settings = {"image_provider": "runpod", "runpod": {"image_endpoint": "qwen-image-edit-2511",
                "edit_endpoint": "different-edit-endpoint", "reference_storage": "managed"}}
    uploads = []
    def source(path, config):
        assert config["reference_storage"] == "managed"
        uploads.append(path)
        return "https://temporary.test/" + str(len(uploads))
    monkeypatch.setattr(rp, "source_url", source)
    def execute(config, role, payload, target, **kwargs):
        assert role == "image" and config["runpod"]["image_endpoint"] == "qwen-image-edit-2511"
        assert callable(payload) and not uploads  # only upload for a new job
        assert len(kwargs["identity"]["sources"]) == 3
        sent = payload()
        assert len(sent["images"]) == 3 and sent["size"] == "1536*1080"
        assert "Ana" in sent["prompt"] and "house" in sent["prompt"]
        return {"model": "qwen-image-edit-2511"}
    monkeypatch.setattr(rp, "execute", execute)
    result = generate_storyboard_frame(scene, plan, settings, tmp_path / "result.png")
    assert result["model"] == "qwen-image-edit-2511"
    assert len(uploads) == 3


def test_qwen_without_references_does_not_submit(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "execute", lambda *a, **k: pytest.fail("No paid request expected"))
    with pytest.raises(VideoStoryboardImageError, match="requires at least one"):
        generate_storyboard_frame({"prompt": "Landscape"}, {}, {"image_provider": "runpod",
            "runpod": {"image_endpoint": "qwen-image-edit-2511"}}, tmp_path / "out.png")


def test_model_selection_round_trip():
    from app.ui.storyboard_runpod_settings import RunpodSettingsWidget
    app = QApplication.instance() or QApplication([])
    widget = RunpodSettingsWidget(tr)
    widget.image_model.setCurrentIndex(widget.image_model.findData("qwen-image-edit-2511"))
    settings = widget.configuration()
    assert settings["image_endpoint"] == "qwen-image-edit-2511"
    widget.set_configuration(settings)
    assert widget.image_model.currentData() == "qwen-image-edit-2511"
    widget.close()


def test_qwen_generation_managed_storage_cleanup_and_job_recovery(tmp_path, monkeypatch):
    from app.core import storyboard_temporary_storage as storage
    path = tmp_path / "ref.png"
    Image.new("RGB", (20, 20), "blue").save(path)
    monkeypatch.setattr(rp, "jobs_directory", lambda: tmp_path / "jobs")
    monkeypatch.setattr(rp, "_asset_index", lambda: tmp_path / "assets")
    uploaded, released, requests = [], [], []
    def upload(source, config):
        uploaded.append(source)
        return {"id": "ref-id", "url": "https://storage.test/media/ref-id"}
    monkeypatch.setattr(storage, "upload", upload)
    monkeypatch.setattr(storage, "delete", lambda *args: released.append(args) or True)
    def request(config, endpoint, action, payload=None, **kwargs):
        requests.append((endpoint, action, payload))
        assert payload["input"]["images"] == ["https://storage.test/media/ref-id"]
        return {"id": "job-1", "status": "COMPLETED", "output": {"image_url": "https://output.test/frame.png"}}
    monkeypatch.setattr(rp, "request", request)
    monkeypatch.setattr(rp, "download", lambda *args: None)
    monkeypatch.setattr(rp, "remember_asset", lambda *args: None)
    settings = {"image_provider": "runpod", "runpod": {"image_endpoint": "qwen-image-edit-2511",
                "reference_storage": "managed", "api_key": "test-only"}}
    scene = {"prompt": "A character in the garden", "generation_overrides": {"reference_images": [{"path": str(path), "label": "Ana"}]}}
    target = tmp_path / "output.png"
    generate_storyboard_frame(scene, {}, settings, target)
    generate_storyboard_frame(scene, {}, settings, target)
    assert len(requests) == 1 and requests[0][:2] == ("qwen-image-edit-2511", "run")
    assert len(uploaded) == 1 and len(released) == 1
