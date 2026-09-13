import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication

from app.core.storyboard_video_edit import duration, slice_segments, source_position, export_arguments
from app.ui.storyboard_video_edit_dialog import StoryboardVideoEditDialog
from app.ui.video_storyboard_page import VideoStoryboardPage


def tr(key, default, **values):
    return default.format(**values)


class SegmentTests(unittest.TestCase):
    def test_slice_across_edits_retains_source_coordinates(self):
        segments = [(2, 5), (8, 10), (0, 2)]
        self.assertEqual(slice_segments(segments, 1, 6), [(3, 5), (8, 10), (0, 1)])
        self.assertEqual(duration(segments), 7)

    def test_playhead_maps_cut_boundaries_to_following_clip(self):
        self.assertEqual(source_position([(2, 5), (8, 10)], 3), (1, 8))
        self.assertEqual(source_position([(2, 5), (8, 10)], 5), (1, 10))

    def test_export_rejects_invalid_ranges(self):
        for segments in ([], [(2, 1)], [(-1, 3)], [(0, float("nan"))]):
            with self.assertRaises(ValueError):
                export_arguments("in.mp4", "out.mp4", segments, False)


class VideoEditUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.video = Path(self.temp.name) / "video.mp4"
        self.video.write_bytes(b"test video")
        with patch.object(StoryboardVideoEditDialog, "_probe"), patch("app.ui.storyboard_video_edit_dialog.QMediaPlayer.setSource"):
            self.dialog = StoryboardVideoEditDialog(tr, "001", str(self.video), "ffmpeg")
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.dialog.reject)
        self.dialog._source_duration = 10
        self.dialog._probe_ready = True
        self.dialog.segments = [(0, 10)]
        self.dialog.timeline.end = 10
        self.dialog._refresh()

    def test_trim_delete_and_history(self):
        d = self.dialog
        d._set_range(2, 8)
        d._edit("keep")
        self.assertEqual(d.segments, [(2, 8)])
        d._set_range(1, 3)
        d._edit("delete")
        self.assertEqual(d.segments, [(2, 3), (5, 8)])
        d._seek(4)
        self.assertEqual(d.timeline.position, 4)
        d._history(False)
        self.assertEqual(d.segments, [(2, 8)])
        d._history(True)
        self.assertEqual(duration(d.segments), 4)

    def test_delete_all_can_be_undone(self):
        d = self.dialog
        d._edit("delete")
        self.assertEqual(d.segments, [])
        self.assertFalse(d.apply_button.isEnabled())
        d._history(False)
        self.assertEqual(d.segments, [(0, 10)])

    def test_playhead_and_range_mouse_events(self):
        from PySide6.QtTest import QTest
        timeline = self.dialog.timeline
        timeline.resize(532, 110)
        QTest.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(266, 15))
        self.assertAlmostEqual(timeline.position, 5, places=2)
        QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(16, 55))
        QTest.mouseMove(timeline, QPoint(116, 55))
        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(116, 55))
        self.assertAlmostEqual(timeline.start, 2, places=2)

    def test_busy_editor_does_not_mutate_ranges(self):
        d = self.dialog
        d._busy = True
        d._set_range(3, 5)
        d._edit("delete")
        self.assertEqual(d.segments, [(0, 10)])
        self.assertEqual((d.timeline.start, d.timeline.end), (0, 10))


class VideoActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.video = Path(self.temp.name) / "video.mp4"
        self.video.write_bytes(b"video")
        self.image = Path(self.temp.name) / "frame.png"
        frame = QImage(16, 16, QImage.Format.Format_RGB32)
        frame.fill(QColor("red"))
        frame.save(str(self.image))
        self.page = VideoStoryboardPage(tr)
        self.addCleanup(self.page.deleteLater)
        self.page.set_scenes([
            {"id": "001", "duration": 6, "image_path": str(self.image), "video_path": str(self.video), "video_duration_seconds": 5},
            {"id": "002", "duration": 4, "image_path": str(self.image)},
        ])

    def test_video_menu_and_selection_actions(self):
        menu = self.page._build_frame_context_menu("001")
        self.addCleanup(menu.deleteLater)
        self.assertEqual([a.text() for a in menu.actions() if not a.isSeparator()],
                         ["Copy Last Frame", "Edit Video", "Delete Video", "Regenerate Video", "Undo", "Redo"])
        self.assertTrue(self.page.copy_frame_button.isHidden())
        self.assertFalse(self.page.copy_last_frame_button.isHidden())
        self.assertFalse(self.page.inspector_edit_video_button.isHidden())
        self.page._select_scene("002")
        self.assertTrue(self.page.edit_video_button.isHidden())
        self.assertFalse(self.page.copy_frame_button.isHidden())
        self.page._clear_timeline_selection()
        self.assertTrue(self.page.copy_last_frame_button.isHidden())

    def test_delete_video_preserves_image_file_timing_and_undo(self):
        self.page._select_scene("001")
        self.page._delete_selected_video()
        self.assertEqual(self.page.scenes()[0]["video_path"], "")
        self.assertEqual(self.page.scenes()[0]["image_path"], str(self.image))
        self.assertEqual(self.page.scenes()[0]["duration_seconds"], 6)
        self.assertTrue(self.video.is_file())
        self.page._undo_scene_edit()
        self.assertEqual(self.page.scenes()[0]["video_path"], str(self.video))
        self.page._redo_scene_edit()
        self.assertEqual(self.page.scenes()[0]["video_path"], "")

    def test_sidebar_context_menu_targets_selected_scene(self):
        self.page._select_scene("001")
        for preview in (self.page.current_preview, self.page.scene_video_widget):
            self.assertEqual(preview.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
            with patch.object(self.page, "_open_frame_context_menu") as menu:
                preview.customContextMenuRequested.emit(QPoint(20, 30))
                self.assertEqual(menu.call_args.args[0], "001")

    def test_edited_video_keeps_scene_slot_and_can_be_undone(self):
        self.page._select_scene("001")
        edited = Path(self.temp.name) / "edited.mp4"
        edited.write_bytes(b"new video")
        self.page._apply_edited_video("001", str(edited), 2)
        self.assertEqual(self.page.scenes()[0]["video_duration_seconds"], 2)
        self.assertEqual(self.page.scenes()[0]["duration_seconds"], 6)
        self.page._undo_scene_edit()
        self.assertEqual(self.page.scenes()[0]["video_path"], str(self.video))
