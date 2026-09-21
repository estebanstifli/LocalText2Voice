import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication

from app.ui.video_storyboard_page import VideoStoryboardPage
from app.ui.video_storyboard_video_batch_dialog import DEFAULT_VIDEO_MOTION_PROMPT

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def page(tmp_path):
    page = VideoStoryboardPage(lambda key, default, **kw: default.format(**kw))
    frame = tmp_path / "image.png"
    frame.write_bytes(b"frame")
    page.set_scenes([{"scene_id": f"scene-{i}", "duration": 5, "prompt": f"Scene {i} image prompt", "image_path": str(frame)} for i in range(1, 6)])
    yield page
    page.finish_auto_video_generation(0, 0, 0)
    if page._video_batch_dialog is not None:
        page._video_batch_dialog.close()
    page.close()
    page.deleteLater()
    APP.processEvents()


def test_ranges_use_timeline_numbers_and_append_editable_motion(page):
    queued = []
    page.generateAllVideosRequested.connect(queued.append)
    page._scenes[0].image_path = ""
    page._request_generate_all_videos()
    dialog = page._video_batch_dialog
    assert dialog.motion_prompt_edit.toPlainText() == DEFAULT_VIDEO_MOTION_PROMPT
    dialog.scenes_radio.setChecked(True)
    dialog.scene_selection_edit.setText("2-3, 5, 3")
    dialog.motion_prompt_edit.setPlainText("Bring this scene to life.")
    dialog.start_button.click()
    assert [item["scene"]["scene_id"] for item in queued[0]] == ["scene-2", "scene-3", "scene-5"]
    assert all(item["prompt"].endswith("\n\nBring this scene to life.") for item in queued[0])
    assert all("Bring this scene" not in scene.prompt for scene in page._scenes)
    assert dialog.motion_prompt_edit.isReadOnly()
    dialog.close_button.click()
    assert not dialog.isVisible()
    page.generate_all_videos_button.click()
    assert page._video_batch_dialog is dialog and dialog.isVisible()
    assert len(queued) == 1


def test_invalid_selection_does_not_start(page):
    queued = []
    page.generateAllVideosRequested.connect(queued.append)
    page._request_generate_all_videos()
    dialog = page._video_batch_dialog
    dialog.scenes_radio.setChecked(True)
    for selection in ("0", "6", "3-2", "1,", "abc"):
        dialog.scene_selection_edit.setText(selection)
        dialog.start_button.click()
        assert not queued and not dialog._running
        assert not dialog.motion_prompt_edit.isReadOnly()


def test_motion_instruction_is_replaced_or_removed_without_accumulation(page):
    queued = []
    page.generateAllVideosRequested.connect(queued.append)
    scene = page._scenes[0]
    scene.video_prompt = "A custom video prompt\n\nOld motion"
    scene.generation_overrides["video_batch_motion_prompt"] = "Old motion"
    page._start_video_batch(True, [scene.scene_id], "New motion")
    assert queued[-1][0]["prompt"] == "A custom video prompt\n\nNew motion"
    page.set_scene_video(scene.scene_id, "clip.mp4", queued[-1][0]["prompt"], "start", 5)
    assert scene.generation_overrides["video_batch_motion_prompt"] == "New motion"
    page._start_video_batch(True, [scene.scene_id], "New motion")
    assert queued[-1][0]["prompt"].count("New motion") == 1
    page._start_video_batch(True, [scene.scene_id], "")
    assert queued[-1][0]["prompt"] == "A custom video prompt"
    assert page.project_state()["plan"]["video_batch_motion_prompt"] == ""


def test_batch_logs_progress_errors_and_final_counts(page):
    page._request_generate_all_videos()
    dialog = page._video_batch_dialog
    dialog.start_button.click()
    page.set_auto_video_generation_progress(1, 5, "scene-1")
    page.set_video_generation_progress("scene-1", "Waiting for provider", 50)
    assert dialog.progress_bar.value() == 10
    page.set_video_generation_failed("scene-1", "Provider failed")
    page.finish_auto_video_generation(3, 5, 1)
    log = dialog.log_edit.toPlainText()
    assert "Waiting for provider" in log and "Provider failed" in log
    assert "3/5 accepted" in log and "1 failed" in log and "1 not generated" in log
    assert not dialog._running and dialog._finished
    assert not dialog.cancel_process_button.isVisible()


def test_stop_finishes_current_video_and_discards_queued_scenes():
    from app.ui.main_window import MainWindow
    holder = SimpleNamespace(video_storyboard_auto_video_queue=[{}, {}], video_storyboard_auto_video_waiting=True,
                             _start_next_video_storyboard_automatic_video=Mock())
    MainWindow._cancel_video_storyboard_automatic_videos(holder)
    assert holder.video_storyboard_auto_video_queue == []
    holder._start_next_video_storyboard_automatic_video.assert_not_called()
    holder.video_storyboard_auto_video_waiting = False
    MainWindow._cancel_video_storyboard_automatic_videos(holder)
    holder._start_next_video_storyboard_automatic_video.assert_called_once()

def test_automatic_failure_reaches_storyboard_error_panel():
    from app.ui.main_window import MainWindow
    page = SimpleNamespace(set_video_generation_failed=Mock(), append_activity=Mock())
    holder = SimpleNamespace(video_storyboard_auto_video_waiting=True, video_storyboard_auto_video_failed=0,
                             video_storyboard_video_scene_id="scene-4", video_storyboard_page=page,
                             video_storyboard_video_thread=object(), tr=lambda key, default, **kw: default.format(**kw))
    MainWindow._complete_video_storyboard_automatic_video(holder, succeeded=False, error="Provider rejected the request")
    page.set_video_generation_failed.assert_called_once_with("scene-4", "Provider rejected the request")
    assert holder.video_storyboard_auto_video_failed == 1
    assert not holder.video_storyboard_auto_video_waiting
