"""Small real-media checks for the bundled FFmpeg integration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.core.storyboard_video_edit import export_arguments
from app.ui.storyboard_video_clipboard import VideoLastFrameCopy
from app.ui.storyboard_video_edit_dialog import StoryboardVideoEditDialog
from app.workers.video_storyboard_video_import_worker import VideoStoryboardVideoImportWorker
from app.utils.ffmpeg_utils import find_ffmpeg


def tr(key, default, **values):
    return default.format(**values)


class VideoMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        try:
            cls.ffmpeg = find_ffmpeg("ffmpeg/ffmpeg.exe")
        except Exception as exc:
            raise unittest.SkipTest(str(exc))
        cls.temp = TemporaryDirectory()
        cls.source = Path(cls.temp.name) / "source.mp4"
        cls.run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(cls.source),
        ])

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.temp.cleanup()

    @classmethod
    def run_ffmpeg(cls, arguments):
        return subprocess.run([str(cls.ffmpeg), "-hide_banner", "-loglevel", "error", *arguments],
                              capture_output=True, check=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout

    def wait_until(self, predicate, timeout=15000):
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        timer.start()
        deadline.start(timeout)
        loop.exec()
        timer.stop()
        deadline.stop()
        self.assertTrue(predicate(), "Media operation timed out")

    def test_copy_uses_actual_last_frame(self):
        reference = self.run_ffmpeg([
            "-i", str(self.source), "-vf", "select=eq(n\\,29)", "-frames:v", "1",
            "-f", "image2pipe", "-c:v", "png", "pipe:1",
        ])
        task = VideoLastFrameCopy()
        received = []
        task.finished.connect(lambda ok, error: received.append((ok, error)))
        task.start(str(self.source), str(self.ffmpeg))
        self.wait_until(lambda: bool(received))
        self.assertEqual(received, [(True, "")])
        self.assertEqual(self.app.clipboard().image(), QImage.fromData(reference))
        task.deleteLater()

    def test_local_video_import_normalizes_a_copy(self):
        source = Path(self.temp.name) / "external.mkv"
        target = Path(self.temp.name) / "imported.mp4"
        self.run_ffmpeg(["-i", str(self.source), "-c", "copy", "-y", str(source)])
        original = source.read_bytes()
        worker = VideoStoryboardVideoImportWorker("001", source, target, str(self.ffmpeg))
        result, errors = [], []
        worker.finished.connect(result.append)
        worker.failed.connect(errors.append)
        worker.run()
        self.assertFalse(errors)
        self.assertEqual(result[0]["video_path"], str(target))
        self.assertAlmostEqual(result[0]["video_duration_seconds"], 3, delta=0.2)
        self.assertEqual(source.read_bytes(), original)
        self.run_ffmpeg(["-i", str(target), "-f", "null", "-"])

    def test_export_trim_concat_preserves_audio_and_duration(self):
        target = Path(self.temp.name) / "export.mp4"
        arguments = export_arguments(str(self.source), str(target), [(0.5, 1), (2, 2.5), (0.5, 1)], True)
        self.run_ffmpeg(arguments)
        self.assertTrue(target.stat().st_size > 0)
        probe = shutil.which("ffprobe")
        if probe:
            data = json.loads(subprocess.check_output([probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)],
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
            self.assertAlmostEqual(float(data["format"]["duration"]), 1.5, delta=0.12)
            self.assertEqual({s["codec_type"] for s in data["streams"]}, {"video", "audio"})
        # Decode the whole result as an additional corruption check.
        self.run_ffmpeg(["-i", str(target), "-f", "null", "-"])

    def test_editor_fallback_probe_and_async_export(self):
        with patch("app.ui.storyboard_video_edit_dialog.shutil.which", return_value=None):
            dialog = StoryboardVideoEditDialog(tr, "001", str(self.source), str(self.ffmpeg))
        self.wait_until(lambda: dialog._process is None)
        self.assertTrue(dialog._probe_ready, dialog.status.text())
        self.assertAlmostEqual(dialog._source_duration, 3, delta=0.1)
        self.assertTrue(dialog._has_audio)
        self.assertAlmostEqual(dialog._fps, 10)
        self.assertAlmostEqual(dialog.timeline.start, 0.3)
        self.assertAlmostEqual(dialog.timeline.end, 2.7)
        self.assertEqual(dialog.segments, [(0, 3)])
        self.wait_until(lambda: dialog._thumbnail_process is None)
        self.assertFalse(dialog.timeline.filmstrip.isNull())
        received = []
        dialog.videoAccepted.connect(lambda scene, path, length: received.append((scene, path, length)))
        dialog._set_range(0.5, 2.5)
        dialog._edit("keep")
        dialog._export()
        self.wait_until(lambda: bool(received))
        self.assertEqual(received[0][0], "001")
        self.assertEqual(received[0][2], 2)
        self.assertTrue(Path(received[0][1]).is_file())
        self.assertTrue(self.source.is_file())

    def test_silent_video_export_and_cancel_leave_source_intact(self):
        silent = Path(self.temp.name) / "silent.mp4"
        self.run_ffmpeg(["-i", str(self.source), "-an", "-c:v", "copy", "-y", str(silent)])
        target = Path(self.temp.name) / "silent-edit.mp4"
        self.run_ffmpeg(export_arguments(str(silent), str(target), [(0.2, 1.2)], False))
        self.run_ffmpeg(["-i", str(target), "-f", "null", "-"])
        dialog = StoryboardVideoEditDialog(tr, "001", str(silent), str(self.ffmpeg))
        self.wait_until(lambda: dialog._process is None and dialog._thumbnail_process is None)
        dialog._set_range(0.5, 2)
        dialog._edit("keep")
        accepted, closed = [], []
        dialog.videoAccepted.connect(lambda *args: accepted.append(args))
        dialog.finished.connect(lambda code: closed.append(code))
        dialog._export()
        partial = dialog._target
        self.assertIsNotNone(partial)
        dialog.reject()
        self.wait_until(lambda: bool(closed))
        self.assertFalse(accepted)
        self.assertFalse(partial.exists())
        self.assertTrue(silent.is_file())
