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
