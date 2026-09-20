import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from app.ui.video_storyboard_entity_dialog import VideoStoryboardEntityDialog
from app.workers.character_image_worker import CharacterImageWorker


def tr(key, default, **values):
    return default.format(**values)


def test_prompt_tracks_description_until_customized():
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardEntityDialog(tr, "character", 30)
    dialog.name_edit.setText("Marco")
    dialog.identity_edit.setPlainText("Sixteen-year-old boy with short black hair")
    dialog.state_edit.setPlainText("Cobalt-blue shirt")
    assert "Marco" in dialog.prompt_edit.toPlainText()
    assert "Cobalt-blue shirt" in dialog.prompt_edit.toPlainText()
    dialog.prompt_edit.setPlainText("My custom sheet")
    dialog.name_edit.setText("Ana")
    assert dialog.values()["reference_image_prompt"] == "My custom sheet"
    dialog.reset_prompt_button.click()
    assert "Ana" in dialog.prompt_edit.toPlainText()
    dialog.generate_image_button.click()
    assert dialog.tabs.currentIndex() == 1
    dialog.close()


def test_worker_uses_exact_prompt_and_normalizes_without_cropping(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = {"image": {"width": 512, "height": 512}}
    target = tmp_path / "sheet.png"
    def generate(scene, plan, config, output, **kwargs):
        assert scene["generation_overrides"]["raw_prompt"] == "Custom sheet"
        assert config["image"] == {"width": 1280, "height": 720}
        Image.new("RGB", (1024, 1024), "red").save(output)
    worker = CharacterImageWorker("Custom sheet", settings, target)
    with patch("app.workers.character_image_worker.prepare_image_runtime"), patch(
        "app.workers.character_image_worker.generate_storyboard_frame", side_effect=generate
    ):
        worker.run()
    assert not worker.error
    assert settings["image"]["width"] == 512
    with Image.open(target) as result:
        assert result.size == (1280, 720)
        assert result.getpixel((640, 360)) == (255, 0, 0)
        assert result.getpixel((0, 0)) == (235, 235, 235)


def test_completion_and_failure_preserve_reference(tmp_path):
    app = QApplication.instance() or QApplication([])
    target = tmp_path / "sheet.png"
    Image.new("RGB", (1280, 720), "blue").save(target)
    dialog = VideoStoryboardEntityDialog(tr, "character", 30, {"reference_image_prompt": "Saved prompt"})
    worker = CharacterImageWorker("Saved prompt", {}, target)
    dialog._image_worker = worker
    dialog._set_generating(True)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog._generation_finished()
    assert dialog.values()["reference_image_path"] == str(target)
    assert dialog.prompt_edit.toPlainText() == "Saved prompt"
    worker = CharacterImageWorker("Saved prompt", {}, tmp_path / "failed.png")
    worker.error = "Provider unavailable"
    dialog._image_worker = worker
    dialog._generation_finished()
    assert dialog.reference_image_path == str(target)
    assert "Provider unavailable" in dialog.image_status.text()
    dialog.close()


def test_cancel_waits_for_worker_and_location_stays_compatible():
    app = QApplication.instance() or QApplication([])
    dialog = VideoStoryboardEntityDialog(tr, "character", 30)
    worker = CharacterImageWorker("prompt", {}, Path("unused.png"))
    dialog._image_worker = worker
    dialog.reject()
    assert worker.cancelled.is_set()
    assert dialog._close_when_finished
    dialog._generation_finished()
    location = VideoStoryboardEntityDialog(tr, "location", 30)
    assert location.tabs.count() == 3
    assert "location reference sheet" in location.values()["reference_image_prompt"]
    location.close()
