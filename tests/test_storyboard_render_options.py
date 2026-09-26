import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from app.ui.video_storyboard_render_dialog import VideoStoryboardRenderDialog

APP = QApplication.instance() or QApplication([])


def test_dialog_preserves_settings_and_bounds_threads(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    settings = {"fps": 25, "supersample": 3, "preset": "fast"}
    dialog = VideoStoryboardRenderDialog(lambda k, d: d, settings)
    assert dialog.values() == {"fps": 25, "threads": 2}
    dialog.threads.setValue(100)
    assert dialog.values() == {"fps": 25, "threads": 24}
    assert settings == {"fps": 25, "supersample": 3, "preset": "fast"}
    dialog.reject()
    assert dialog.result() == 0


def test_single_thread_cpu(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    dialog = VideoStoryboardRenderDialog(lambda k, d: d, {})
    assert dialog.threads.value() == dialog.threads.maximum() == 1


def test_render_options_reach_scene_and_block_encoders(tmp_path, monkeypatch):
    from app.core import video_storyboard_renderer as renderer
    from tests.test_video_storyboard_renderer import _write_ppm, _write_silent_wav

    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    frame, audio = tmp_path / "frame.ppm", tmp_path / "audio.wav"
    _write_ppm(frame, 100, 20, 30)
    _write_silent_wav(audio, 1)
    commands = []

    def run(executable, arguments, *args, **kwargs):
        commands.append(arguments)
        from pathlib import Path
        Path(arguments[-1]).write_bytes(b"video")

    monkeypatch.setattr(renderer, "_run_ffmpeg_with_progress", run)
    renderer.render_storyboard_video(
        [{"image_path": str(frame), "duration_seconds": 1}] * 9,
        audio, tmp_path / "out.mp4",
        {"video": {"threads": 8, "fps": 24, "supersample": 2, "preset": "veryfast", "clip_audio_enabled": False}},
    )
    encoded = [c for c in commands if c[c.index("-c:v") + 1] != "copy"]
    assert len(encoded) == 11
    for command in encoded:
        last_threads = max(i for i, x in enumerate(command) if x == "-threads")
        assert command[last_threads + 1] == "8"
        assert command[command.index("-preset") + 1] == "veryfast"
        assert command[command.index("-r") + 1] == "24"


def test_render_lifecycle_background_cancel_and_logs():
    dialog = VideoStoryboardRenderDialog(lambda k, d: d, {})
    started, cancelled = [], []
    dialog.startRequested.connect(lambda: started.append(True))
    dialog.cancelRequested.connect(lambda: cancelled.append(True))
    dialog.show()
    dialog.start_button.click()
    assert started == [True]
    assert not dialog.start_button.isVisible()
    assert dialog.close_button.isVisible()
    assert not dialog.fps.isEnabled()
    dialog.set_progress("Encoding scene 2", 35)
    assert "35%" in dialog.log_edit.toPlainText()
    dialog.close_button.click()
    assert dialog._running and not dialog.isVisible()
    assert cancelled == []
    dialog.show_and_raise()
    dialog.cancel_process_button.click()
    dialog._cancel_process()
    assert cancelled == [True]
    dialog.set_finished(False, "Cancelled")
    assert not dialog._running
    assert "Cancelled" in dialog.log_edit.toPlainText()
    assert not dialog.cancel_process_button.isVisible()
    dialog.close()


def test_main_window_routes_render_progress_results_and_cancel():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.ui.main_window import MainWindow

    dialog = VideoStoryboardRenderDialog(lambda k, d: d, {})
    dialog._start()
    holder = SimpleNamespace(
        video_storyboard_render_dialog=dialog,
        video_storyboard_render_worker=Mock(),
        video_storyboard_page=Mock(),
        log_view=Mock(),
        tr=lambda k, d, **values: d.format(**values),
    )
    MainWindow._on_video_storyboard_render_progress(holder, "rendering", 42)
    assert dialog.progress_bar.value() == 42
    assert "42%" in dialog.log_edit.toPlainText()
    MainWindow._cancel_video_storyboard_render(holder)
    holder.video_storyboard_render_worker.cancel.assert_called_once()
    MainWindow._on_video_storyboard_render_failed(holder, "Cancelled by user")
    assert not dialog._running
    assert "Cancelled by user" in dialog.log_edit.toPlainText()
    MainWindow._on_video_storyboard_render_finished(holder, {"output_path": "movie.mp4"})
    assert dialog.progress_bar.value() == 100
    assert "movie.mp4" in dialog.log_edit.toPlainText()
    dialog.close()
