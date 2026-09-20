from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from app.ui.video_storyboard_video_dialog import VideoStoryboardVideoDialog


def _translate(_key: str, default: str, **values: object) -> str:
    return default.format(**values)


def test_video_dialog_generates_reviews_and_accepts_candidate(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    frame = tmp_path / "frame.png"
    clip = tmp_path / "candidate.mp4"
    pixmap = QPixmap(64, 36)
    pixmap.fill(QColor("#2868aa"))
    assert pixmap.save(str(frame))
    clip.write_bytes(b"video")
    dialog = VideoStoryboardVideoDialog(
        _translate,
        {
            "scene_id": "001",
            "image_path": str(frame),
            "prompt": "A girl plays in a village square.",
        },
        {"style": {}, "base_seed": 19},
    )
    requested: list[dict] = []
    accepted: list[dict] = []
    dialog.generateRequested.connect(requested.append)
    dialog.candidateAccepted.connect(accepted.append)

    assert dialog.accept_button.isHidden()
    assert "girl plays" in dialog.prompt_edit.toPlainText()
    assert (dialog.frame_preview.width(), dialog.frame_preview.height()) == (480, 270)
    assert (dialog.video_stack.width(), dialog.video_stack.height()) == (480, 270)
    assert (dialog.video_widget.width(), dialog.video_widget.height()) == (480, 270)
    assert dialog.video_stack.currentWidget() is dialog.video_placeholder
    dialog.frame_role_combo.setCurrentIndex(
        dialog.frame_role_combo.findData("end")
    )
    dialog.generate_button.click()
    application.processEvents()

    assert requested[0]["frame_role"] == "end"
    assert dialog.progress_bar.isVisible() or not dialog.isVisible()
    dialog.set_candidate(str(clip), 6.0)
    assert dialog.video_stack.currentWidget() is dialog.video_placeholder

    class _ValidFrame:
        @staticmethod
        def isValid() -> bool:  # noqa: N802 - mirrors Qt API
            return True

    dialog._on_video_frame_changed(_ValidFrame())
    application.processEvents()
    assert dialog.video_stack.currentWidget() is dialog.video_widget
    assert not dialog._pause_on_first_frame
    cover_requests: list[bool] = []
    dialog._prime_cover_frame = lambda: cover_requests.append(True)  # type: ignore[method-assign]
    dialog._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)
    application.processEvents()
    assert cover_requests == [True]
    dialog.accept_button.click()
    application.processEvents()

    assert accepted[0]["video_path"] == str(clip)
    assert accepted[0]["frame_role"] == "end"
    assert accepted[0]["video_duration_seconds"] == 6.0
    assert dialog.player.source().isEmpty()
    dialog.deleteLater()


def test_video_reference_picker_none_and_persisted_dispatch(tmp_path):
    from unittest.mock import patch
    from PySide6.QtWidgets import QDialog
    from app.ui.video_storyboard_page import VideoStoryboardPage
    application = QApplication.instance() or QApplication([])
    refs = []
    for i in range(3):
        path = tmp_path / f'reference-{i}.png'
        pixmap = QPixmap(64, 36)
        pixmap.fill(QColor('blue'))
        pixmap.save(str(path))
        refs.append({'path': str(path), 'label': f'Entity {i}'})
    page = VideoStoryboardPage(_translate)
    page.set_configuration({'video_provider': 'runpod', 'runpod': {'video_endpoint': 'kling-video-o1-r2v'}})
    page.set_scenes([{'scene_id': '001', 'duration_seconds': 8, 'prompt': 'Storm'}])
    page._select_scene('001')
    requests = []
    page.generateVideoRequested.connect(requests.append)
    page._request_video_generation()
    dialog = page._video_dialog
    assert dialog.frame_role_combo.currentData() == 'none'
    assert not dialog.reference_button.icon().isNull()
    with patch('app.ui.video_storyboard_video_dialog.VideoStoryboardReferencePickerDialog') as picker:
        picker.return_value.exec.return_value = QDialog.DialogCode.Accepted
        picker.return_value.selected_references.return_value = refs
        dialog.reference_button.click()
    assert 'Entity 2' in dialog.reference_summary.text()
    dialog.generate_button.click()
    assert requests[0]['scene']['generation_overrides']['video_reference_images'] == refs
    saved = page.project_state()
    assert saved['scenes'][0]['video_frame_role'] == 'none'
    assert saved['scenes'][0]['generation_overrides']['video_reference_images'] == refs
    dialog.set_failed('Test stopped before network submission')
    dialog.close()
    page.restore_project_state(saved)
    assert page.scenes()[0]['video_frame_role'] == 'none'
    page.deleteLater()


def test_wan_reference_limit_reports_error_before_submission(tmp_path):
    application = QApplication.instance() or QApplication([])
    frame = tmp_path / 'frame.png'
    extra = tmp_path / 'extra.png'
    pixmap = QPixmap(64, 36)
    pixmap.fill(QColor('blue'))
    pixmap.save(str(frame))
    pixmap.save(str(extra))
    dialog = VideoStoryboardVideoDialog(_translate, {'scene_id': '1', 'image_path': str(frame),
        'prompt': 'storm', '_video_provider': 'runpod', '_runpod_config': {'video_endpoint': 'wan-2-6-i2v'},
        'generation_overrides': {'video_reference_images': [{'path': str(extra)}]}}, {})
    requests = []
    dialog.generateRequested.connect(requests.append)
    dialog.generate_button.click()
    assert not requests and not dialog._generating
    assert 'one image' in dialog.status_label.text()
    assert dialog.generate_button.isEnabled()
    dialog._reference_images = []
    dialog.frame_role_combo.setCurrentIndex(dialog.frame_role_combo.findData('none'))
    assert 'T2V' in dialog.model_info.text()
    dialog.generate_button.click()
    assert requests[0]['frame_role'] == 'none'
    dialog.set_failed('Test stopped before network submission')
    dialog.close()
    dialog.deleteLater()
