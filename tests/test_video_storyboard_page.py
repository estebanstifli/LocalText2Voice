from __future__ import annotations

import os
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt, QUrl
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox
from PySide6.QtTest import QTest

from app.ui.video_storyboard_page import VideoStoryboardPage


def _translate(_key: str, default: str, **values: object) -> str:
    return default.format(**values)


class VideoStoryboardPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = VideoStoryboardPage(_translate)
        self.addCleanup(self.page.deleteLater)
        self.page.set_scenes(
            [
                {
                    "id": "001",
                    "duration": 6,
                    "narration": "The train crossed the green valley.",
                    "prompt": "Wide shot of a train crossing a green valley",
                },
                {
                    "id": "002",
                    "duration": 7,
                    "narration": "Mara looked through the window.",
                    "prompt": "Close-up of Mara looking through a train window",
                },
            ]
        )

    def test_timeline_edits_contiguous_scenes_and_zoom(self) -> None:
        self.assertTrue(self.page.beta_guide_link.openExternalLinks())
        self.assertIn("https://github.com/estebanstifli/LocalText2Voice/blob/main/docs/VIDEO_STORYBOARD.md", self.page.beta_guide_link.text())
        self.assertIn("Guide, demos and development", self.page.beta_guide_link.text())
        self.assertEqual(self.page.track_labels.width(), 58)
        self.assertIn("Video", self.page.track_labels.toolTip())
        self.assertTrue(self.page.current_preview.hasHeightForWidth())
        self.assertTrue(self.page.scene_video_widget.hasHeightForWidth())
        self.assertEqual(self.page.current_preview.heightForWidth(320), 180)
        self.assertEqual(self.page.scene_video_widget.heightForWidth(320), 180)
        self.page.resize(1280, 900)
        self.page.show()
        self.page.inspector_tabs.setTabVisible(0, True)
        self.page.inspector_tabs.setCurrentIndex(0)
        self.application.processEvents()
        self.assertEqual(
            self.page.scene_video_widget.size(),
            self.page.current_preview.size(),
        )
        self.assertTrue(self.page.narration_edit.isReadOnly())
        self.assertTrue(self.page.render_button.isHidden())
        self.assertEqual(self.page.scenes()[1]["start_seconds"], 6.0)

        self.page.timeline_canvas.adjust_boundary(0, 2.0)
        scenes = self.page.scenes()
        self.assertEqual(scenes[0]["duration_seconds"], 8.0)
        self.assertEqual(scenes[1]["duration_seconds"], 5.0)
        self.assertEqual(scenes[1]["start_seconds"], 8.0)

        width = self.page.timeline_canvas.width()
        self.page.zoom_in_button.click()
        self.assertGreater(self.page.timeline_canvas.width(), width)
        self.assertNotEqual(self.page.zoom_label.text(), "100%")

        self.page._select_scene("002")
        self.page.duration_spin.setValue(9.0)
        self.assertEqual(self.page.scenes()[1]["duration_seconds"], 9.0)
        self.assertEqual(
            self.page.narration_edit.toPlainText(),
            "Mara looked through the window.",
        )

    def test_output_folder_action_is_exposed_by_the_toolbar(self) -> None:
        requests: list[bool] = []
        self.page.openOutputFolderRequested.connect(lambda: requests.append(True))

        self.assertEqual(
            self.page.open_output_folder_button.text(),
            "Open output folder",
        )
        self.assertTrue(self.page.open_output_folder_button.isHidden())
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "storyboard.mp4"
            output.write_bytes(b"video")
            self.page.set_render_finished(str(output))
            self.assertFalse(self.page.open_output_folder_button.isHidden())
            self.page.open_output_folder_button.click()

        self.assertEqual(requests, [True])

    def test_timeline_frame_actions_split_clear_and_request_replacement(self) -> None:
        replacements: list[tuple[str, str]] = []
        self.page.replaceFrameRequested.connect(
            lambda scene_id, path: replacements.append((scene_id, path))
        )
        self.assertEqual(self.page.timeline_heading.text(), "Timeline")
        self.assertFalse(self.page.convert_frame_video_button.isEnabled())
        self.assertEqual(self.page.regenerate_button.text(), "Regenerate frame")
        self.assertTrue(self.page.timeline_regenerate_button.text() == "")
        self.page.timeline_regenerate_button.click()
        self.assertIsNotNone(self.page._regeneration_dialog)
        assert self.page._regeneration_dialog is not None
        self.page._regeneration_dialog.ignore_button.click()
        self.application.processEvents()

        with patch(
            "app.ui.video_storyboard_page.QFileDialog.getOpenFileName",
            return_value=("C:/images/replacement.png", ""),
        ):
            self.page.replace_frame_button.click()
        self.assertEqual(replacements, [("001", "C:/images/replacement.png")])

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        frame = Path(temporary.name) / "frame.png"
        frame.write_bytes(b"frame")
        self.page.set_frame_replaced("001", str(frame))
        self.page.split_frame_button.click()
        scenes = self.page.scenes()
        self.assertEqual(len(scenes), 3)
        self.assertEqual(scenes[0]["duration_seconds"], 3.0)
        self.assertEqual(scenes[1]["duration_seconds"], 3.0)
        self.assertEqual(scenes[1]["prompt"], scenes[0]["prompt"])
        self.assertEqual(scenes[1]["narration"], scenes[0]["narration"])
        self.assertEqual(scenes[1]["image_path"], scenes[0]["image_path"])
        self.assertEqual(scenes[1]["scene_id"], "001-split")

        with patch(
            "app.ui.video_storyboard_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.page.delete_frame_button.click()
        self.assertEqual(len(self.page.scenes()), 3)

        with patch(
            "app.ui.video_storyboard_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.page.delete_frame_button.click()
        deleted = self.page.scenes()
        self.assertEqual(len(deleted), 2)
        self.assertEqual(deleted[0]["scene_id"], "001")
        self.assertEqual(deleted[0]["duration_seconds"], 6.0)
        self.assertEqual(deleted[1]["scene_id"], "002")
        self.assertEqual(deleted[1]["start_seconds"], 6.0)

        self.assertTrue(self.page.undo_button.isEnabled())
        self.page.undo_button.click()
        restored = self.page.scenes()
        self.assertEqual(len(restored), 3)
        self.assertEqual(restored[0]["duration_seconds"], 3.0)
        self.assertEqual(restored[1]["scene_id"], "001-split")
        self.assertTrue(self.page.redo_button.isEnabled())
        self.page.redo_button.click()
        self.assertEqual(len(self.page.scenes()), 2)
        self.assertTrue(self.page.render_button.isHidden())

    def test_undo_and_redo_restore_timeline_duration_edits(self) -> None:
        self.page.timeline_canvas.adjust_boundary(0, 2.0)
        self.assertEqual(
            [scene["duration_seconds"] for scene in self.page.scenes()],
            [8.0, 5.0],
        )
        self.page.undo_button.click()
        self.assertEqual(
            [scene["duration_seconds"] for scene in self.page.scenes()],
            [6.0, 7.0],
        )
        self.page.redo_button.click()
        self.assertEqual(
            [scene["duration_seconds"] for scene in self.page.scenes()],
            [8.0, 5.0],
        )

    def test_frame_motion_in_and_out_are_persisted_and_undoable(self) -> None:
        self.assertEqual(self.page.motion_in_combo.currentData(), "zoom_in")
        self.assertEqual(self.page.motion_out_combo.currentData(), "none")

        self.page.motion_in_combo.setCurrentIndex(
            self.page.motion_in_combo.findData("none")
        )
        self.page.motion_out_combo.setCurrentIndex(
            self.page.motion_out_combo.findData("zoom_out")
        )
        scene = self.page.scenes()[0]
        self.assertEqual(scene["motion_in"], "none")
        self.assertEqual(scene["motion_out"], "zoom_out")

        self.page.undo_button.click()
        self.assertEqual(self.page.scenes()[0]["motion_out"], "none")
        self.page.redo_button.click()
        self.assertEqual(self.page.scenes()[0]["motion_out"], "zoom_out")

    def test_generate_frames_dialog_can_keep_or_overwrite_existing_frames(self) -> None:
        requests: list[object] = []
        self.page.generateFramesRequested.connect(requests.append)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        frame = Path(temporary.name) / "existing.png"
        frame.write_bytes(b"frame")
        self.page.set_frame_generated("001", str(frame))

        self.page.generate_frames_button.click()
        self.assertEqual(requests, [])
        dialog = self.page._frame_batch_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        self.assertTrue(dialog.overwrite_checkbox.isVisible())
        self.assertFalse(dialog.overwrite_checkbox.isChecked())
        dialog.overwrite_checkbox.setChecked(True)
        dialog.start_button.click()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["scenes"][0]["image_path"], "")

    def test_frame_batch_restarts_after_success_failure_and_cancellation(self) -> None:
        requests = []
        self.page.generateFramesRequested.connect(requests.append)
        for mode, error in (("generate", None), ("regenerate", "provider error"),
                            ("generate", "cancelled"), ("regenerate", None)):
            self.page._open_frame_batch_dialog(mode)
            dialog = self.page._frame_batch_dialog
            assert dialog is not None
            self.assertFalse(dialog._finished)
            self.assertEqual(dialog.progress_bar.value(), 0)
            self.assertEqual(dialog.log_edit.toPlainText(), "")
            self.assertEqual(dialog.mode, mode)
            dialog.start_button.click()
            if error:
                self.page.set_frame_generation_failed(error)
            else:
                self.page.set_frame_generation_finished()
            self.assertIsNone(self.page._frame_batch_dialog)
            # Destruction of the previous result must not clear a new dialog.
            self.page._open_frame_batch_dialog(mode)
            following = self.page._frame_batch_dialog
            self.page._clear_frame_batch_dialog(dialog)
            self.assertIs(self.page._frame_batch_dialog, following)
            dialog.close_button.click()
            following.close()
            self.page._clear_frame_batch_dialog(following)
        self.assertEqual(len(requests), 4)

    def test_project_change_discards_idle_frame_confirmation(self) -> None:
        self.page._open_frame_batch_dialog("generate")
        self.page.set_audiobook_source(99, "New Project", "New text", [], 0)
        self.assertIsNone(self.page._frame_batch_dialog)

    def test_frame_batch_can_hide_in_background_and_cancel_the_worker(self) -> None:
        cancellations: list[bool] = []
        self.page.cancelFrameGenerationRequested.connect(
            lambda: cancellations.append(True)
        )
        self.page.generate_frames_button.click()
        dialog = self.page._frame_batch_dialog
        assert dialog is not None
        dialog.start_button.click()
        dialog.close()
        self.assertFalse(dialog.isVisible())
        self.page._open_frame_batch_dialog("generate")
        self.assertIs(self.page._frame_batch_dialog, dialog)
        dialog.cancel_process_button.click()
        self.assertEqual(cancellations, [True])
        self.page.set_frame_generation_failed("cancelled")

    def test_rendered_video_replaces_provider_status_and_can_be_cleared(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        video = Path(temporary.name) / "storyboard-final.mp4"
        video.write_bytes(b"video")

        self.page.set_existing_render_output(str(video))

        self.assertTrue(self.page.status_label.isHidden())
        self.assertFalse(self.page.rendered_video_button.isHidden())
        self.assertEqual(self.page.rendered_video_button.text(), video.name)

        self.page.set_existing_render_output("")
        self.assertFalse(self.page.status_label.isHidden())
        self.assertTrue(self.page.rendered_video_button.isHidden())

    def test_inspector_generate_video_only_when_scene_has_no_video(self):
        with TemporaryDirectory() as directory, patch("PySide6.QtMultimedia.QMediaPlayer.setSource"):
            root = Path(directory)
            source = root / "frame.png"
            image = QImage(16, 9, QImage.Format.Format_RGB32)
            image.fill(QColor("blue"))
            image.save(str(source))
            self.page._select_scene("001")
            button = self.page.inspector_generate_video_button
            self.assertFalse(button.isHidden())
            self.assertFalse(button.isEnabled())
            self.page._scenes[0].image_path = str(source)
            self.page._select_scene("001")
            self.assertTrue(button.isEnabled())
            self.assertFalse(button.icon().isNull())
            button.click()
            self.assertEqual(self.page._video_dialog.scene_id, "001")
            self.page._video_dialog.close()
            video = root / "scene.mp4"
            video.write_bytes(b"video")
            self.page._scenes[0].video_path = str(video)
            self.page._select_scene("001")
            self.assertTrue(button.isHidden())
            video.unlink()
            self.page._select_scene("001")
            self.assertFalse(button.isHidden())
            self.page.set_scenes([])
            self.assertTrue(button.isHidden())

    def test_edit_frame_is_available_in_toolbar_context_and_inspector(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        first = root / "first.png"
        second = root / "second.png"
        image = QImage(4, 2, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        image.setPixelColor(0, 0, QColor("blue"))
        self.assertTrue(image.save(str(first)))
        self.assertTrue(image.save(str(second)))
        previous_video = root / "previous.mp4"
        previous_video.write_bytes(b"video")
        self.page._scenes[0].image_path = str(first)
        self.page._scenes[0].video_path = str(previous_video)
        self.page._scenes[1].image_path = str(second)
        self.page._select_scene("002")
        edits: list[tuple[str, QImage]] = []
        self.page.editFrameRequested.connect(
            lambda scene_id, edited: edits.append((scene_id, edited))
        )

        self.assertTrue(self.page.edit_frame_button.isEnabled())
        self.assertTrue(self.page.inspector_edit_frame_button.isEnabled())
        self.page.edit_frame_button.click()
        dialog = self.page._image_edit_dialog
        assert dialog is not None
        self.assertTrue(dialog.previous_video_button.isEnabled())
        dialog.flip_horizontal_button.click()
        dialog.accept_button.click()

        self.assertEqual(edits[0][0], "002")
        self.assertEqual(edits[0][1].pixelColor(3, 0), QColor("blue"))

    def test_edit_frame_can_replace_and_crop_back_to_the_same_resolution(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        current = root / "current.png"
        replacement = root / "replacement.png"
        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        self.assertTrue(image.save(str(current)))
        other = QImage(4, 4, QImage.Format.Format_ARGB32)
        other.fill(QColor("blue"))
        self.assertTrue(other.save(str(replacement)))
        self.page._scenes[0].image_path = str(current)
        self.page._select_scene("001")
        self.page.edit_frame_button.click()
        dialog = self.page._image_edit_dialog
        assert dialog is not None

        self.assertEqual(dialog.file_link_button.text(), current.name)
        with patch(
            "app.ui.video_storyboard_image_edit_dialog.QFileDialog.getOpenFileName",
            return_value=(str(replacement), ""),
        ):
            dialog.replace_button.click()
        self.assertEqual(dialog.file_link_button.text(), replacement.name)
        dialog._apply_crop(QRect(0, 0, 2, 4))
        self.assertEqual(dialog._working.size(), other.size())
        self.assertEqual(dialog._working.pixelColor(3, 2), QColor("blue"))
        dialog.reject()

    def test_context_menu_exposes_the_frame_actions(self) -> None:
        menu = self.page._build_frame_context_menu("002")
        self.assertIsNotNone(menu)
        assert menu is not None
        labels = [action.text() for action in menu.actions() if action.text()]
        self.assertEqual(
            labels,
            [
                "Copy",
                "Paste",
                "Replace frame with an image",
                "Regenerate frame",
                "Edit frame",
                "Split frame into two",
                "Delete frame",
                "Generate scene video",
                "Undo",
                "Redo",
            ],
        )
        self.assertEqual(self.page.selected_scene().scene_id, "002")
        convert_action = next(
            action for action in menu.actions()
            if action.text() == "Generate scene video"
        )
        self.assertFalse(convert_action.isEnabled())
        menu.deleteLater()

    def test_native_context_menu_event_selects_the_clicked_frame(self) -> None:
        requested: list[tuple[str, QPoint]] = []
        self.page.timeline_canvas.frameContextMenuRequested.disconnect()
        self.page.timeline_canvas.frameContextMenuRequested.connect(
            lambda scene_id, position: requested.append((scene_id, position))
        )
        position = QPoint(50, self.page.timeline_canvas.VIDEO_TOP + 40)
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            position,
            position,
        )

        self.page.timeline_canvas.contextMenuEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(requested, [("001", position)])
        self.assertEqual(self.page.selected_scene().scene_id, "001")

    def test_video_layer_keeps_reference_frame_and_is_undoable(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        frame = Path(temporary.name) / "frame.png"
        video = Path(temporary.name) / "clip.mp4"
        frame.write_bytes(b"frame")
        video.write_bytes(b"video")
        self.page.set_frame_replaced("001", str(frame))
        original_frame = self.page.scenes()[0]["image_path"]

        self.page.set_scene_video(
            "001",
            str(video),
            "The girl walks naturally through the square.",
            "end",
            6.0,
        )

        scene = self.page.scenes()[0]
        self.assertEqual(scene["image_path"], original_frame)
        self.assertEqual(scene["video_path"], str(video))
        self.assertEqual(scene["video_frame_role"], "end")
        self.assertEqual(scene["video_duration_seconds"], 6.0)
        self.assertEqual(scene["video_motion_in"], "none")
        self.assertEqual(scene["video_motion_out"], "none")
        self.assertEqual(self.page.inspector_heading.text(), "Selected scene")
        self.assertTrue(self.page.inspector_tabs.isTabVisible(0))
        self.assertEqual(self.page.inspector_tabs.currentIndex(), 0)
        self.page.video_motion_in_combo.setCurrentIndex(
            self.page.video_motion_in_combo.findData("zoom_in")
        )
        self.assertEqual(self.page.scenes()[0]["video_motion_in"], "zoom_in")
        self.assertTrue(self.page.convert_frame_video_button.isEnabled())
        self.page.undo_button.click()
        self.assertEqual(self.page.scenes()[0]["video_motion_in"], "none")
        self.page.undo_button.click()
        self.assertEqual(self.page.scenes()[0]["video_path"], "")

    def test_generate_all_videos_only_queues_scenes_without_a_video(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        first_frame = Path(temporary.name) / "first.png"
        second_frame = Path(temporary.name) / "second.png"
        existing_video = Path(temporary.name) / "existing.mp4"
        first_frame.write_bytes(b"frame")
        second_frame.write_bytes(b"frame")
        existing_video.write_bytes(b"video")
        self.page._scenes[0].image_path = str(first_frame)
        self.page._scenes[1].image_path = str(second_frame)
        self.page._scenes[1].video_path = str(existing_video)
        queued: list[object] = []
        self.page.generateAllVideosRequested.connect(queued.append)

        with patch.object(
            self.page,
            "_choose_generate_all_videos_scope",
            return_value="missing",
        ) as choose_scope:
            self.page.generate_all_videos_button.click()

        choose_scope.assert_called_once_with(1, 2, 1)
        assert len(queued) == 1
        request = queued[0]
        assert isinstance(request, list)
        assert len(request) == 1
        assert request[0]["scene"]["scene_id"] == "001"
        assert request[0]["automatic"] is True
        self.assertFalse(self.page.generate_all_videos_button.isEnabled())

    def test_generate_all_videos_can_overwrite_existing_scene_videos(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        first_frame = Path(temporary.name) / "first.png"
        second_frame = Path(temporary.name) / "second.png"
        existing_video = Path(temporary.name) / "existing.mp4"
        first_frame.write_bytes(b"frame")
        second_frame.write_bytes(b"frame")
        existing_video.write_bytes(b"video")
        self.page._scenes[0].image_path = str(first_frame)
        self.page._scenes[1].image_path = str(second_frame)
        self.page._scenes[1].video_path = str(existing_video)
        queued: list[object] = []
        self.page.generateAllVideosRequested.connect(queued.append)

        with patch.object(
            self.page,
            "_choose_generate_all_videos_scope",
            return_value="all",
        ):
            self.page.generate_all_videos_button.click()

        assert len(queued) == 1
        request = queued[0]
        assert isinstance(request, list)
        assert [item["scene"]["scene_id"] for item in request] == ["001", "002"]
        assert all(item["automatic"] is True for item in request)

    def test_generate_all_videos_cancel_keeps_the_queue_empty(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        frame = Path(temporary.name) / "first.png"
        frame.write_bytes(b"frame")
        self.page._scenes[0].image_path = str(frame)
        queued: list[object] = []
        self.page.generateAllVideosRequested.connect(queued.append)

        with patch.object(
            self.page,
            "_choose_generate_all_videos_scope",
            return_value=None,
        ):
            self.page.generate_all_videos_button.click()

        self.assertEqual(queued, [])
        self.assertTrue(self.page.generate_all_videos_button.isEnabled())

    def test_clicking_a_cut_selects_and_edits_only_its_transition(self) -> None:
        boundary_x = round(
            self.page.scenes()[0]["duration_seconds"]
            * self.page.timeline_canvas.pixels_per_second
        )
        QTest.mouseClick(
            self.page.timeline_canvas,
            Qt.MouseButton.LeftButton,
            pos=QPoint(boundary_x, self.page.timeline_canvas.VIDEO_TOP + 40),
        )

        self.assertEqual(self.page._selected_transition_index, 0)
        self.assertIsNone(self.page.selected_scene())
        self.assertFalse(self.page.transition_label.isHidden())
        self.assertEqual(self.page.transition_combo.currentData(), "fade")
        self.page.transition_combo.setCurrentIndex(
            self.page.transition_combo.findData("none")
        )
        self.assertEqual(self.page.scenes()[1]["transition"], "none")

        self.page.undo_button.click()
        self.assertEqual(self.page.scenes()[1]["transition"], "fade")
        self.assertEqual(self.page._selected_transition_index, 0)

        QTest.mouseClick(
            self.page.timeline_canvas,
            Qt.MouseButton.LeftButton,
            pos=QPoint(50, self.page.timeline_canvas.VIDEO_TOP + 40),
        )
        self.assertIsNotNone(self.page.selected_scene())
        self.assertTrue(self.page.transition_label.isHidden())

    def test_selected_frame_combines_and_edits_visual_prompt_and_shot(self) -> None:
        self.page.set_scenes(
            [
                {
                    "id": "001",
                    "duration": 8,
                    "narration": "Mara enters the station.",
                    "prompt": "Mara enters a busy nineteenth-century station",
                    "shot": "wide establishing shot, eye level, 35mm viewpoint",
                }
            ]
        )

        self.assertFalse(self.page.prompt_edit.isReadOnly())
        self.assertEqual(
            self.page.prompt_edit.toPlainText(),
            "Mara enters a busy nineteenth-century station\n\n"
            "SHOT AND COMPOSITION: wide establishing shot, eye level, 35mm viewpoint",
        )

        self.page.prompt_edit.setPlainText(
            "Mara pauses beneath the station clock\n\n"
            "SHOT AND COMPOSITION: medium profile shot, 50mm viewpoint"
        )
        scene = self.page.scenes()[0]
        self.assertEqual(
            scene["prompt"],
            "Mara pauses beneath the station clock",
        )
        self.assertEqual(
            scene["shot"],
            "medium profile shot, 50mm viewpoint",
        )

        self.page.regenerate_button.click()
        dialog = self.page._regeneration_dialog
        assert dialog is not None
        self.assertEqual(
            dialog.prompt_edit.toPlainText(),
            "Mara pauses beneath the station clock",
        )
        self.assertEqual(
            dialog.shot_edit.text(),
            "medium profile shot, 50mm viewpoint",
        )
        dialog.ignore_button.click()

    def test_prompt_regeneration_review_and_render_contract(self) -> None:
        regeneration_requests: list[dict] = []
        accepted: list[tuple[str, str, str]] = []
        renders: list[object] = []
        self.page.regenerateRequested.connect(
            regeneration_requests.append
        )
        self.page.regenerationAccepted.connect(
            lambda scene_id, path, prompt: accepted.append(
                (scene_id, path, prompt)
            )
        )
        self.page.renderRequested.connect(renders.append)

        self.page.regenerate_button.click()
        dialog = self.page._regeneration_dialog
        self.assertIsNotNone(dialog)
        assert dialog is not None
        self.assertTrue(dialog.accept_button.isHidden())
        self.assertTrue(dialog.ignore_button.isHidden())
        self.assertIn("SCENE:", dialog.raw_prompt_edit.toPlainText())
        self.assertEqual(dialog.tabs.count(), 4)
        self.assertEqual(
            [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())],
            ["Styles", "Style details", "Narrative context", "Characters"],
        )
        watercolor_item = dialog._style_gallery_item("watercolor")
        dialog.style_gallery.setCurrentItem(watercolor_item)
        self.assertIn("watercolor style", dialog.medium_edit.text())
        dialog.style_gallery.setCurrentItem(
            dialog._style_gallery_item("custom")
        )
        dialog.prompt_edit.setPlainText("Edited English prompt")
        dialog.shot_edit.setText("low-angle close-up")
        dialog.medium_edit.setText("charcoal cinematic illustration")
        dialog.seed_edit.setText("123456")
        dialog.generate_button.click()
        self.assertEqual(regeneration_requests[0]["scene_id"], "001")
        self.assertEqual(
            regeneration_requests[0]["prompt"],
            "Edited English prompt",
        )
        self.assertEqual(regeneration_requests[0]["shot"], "low-angle close-up")
        compiled_prompt = dialog.raw_prompt_edit.toPlainText()
        self.assertIn("STYLE: charcoal cinematic illustration", compiled_prompt)
        self.assertIn(
            "SHOT AND COMPOSITION: low-angle close-up",
            compiled_prompt,
        )
        self.assertIn("SCENE: Edited English prompt", compiled_prompt)
        self.assertEqual(
            regeneration_requests[0]["overrides"]["seed"],
            123456,
        )
        self.assertEqual(
            regeneration_requests[0]["overrides"]["style_mode"],
            "custom",
        )
        self.assertFalse(dialog.generate_button.isEnabled())
        self.assertFalse(dialog.progress_bar.isHidden())

        with TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate.png"
            pixmap = QPixmap(32, 18)
            pixmap.fill(QColor("#2463a8"))
            self.assertTrue(pixmap.save(str(candidate)))

            self.page.set_regeneration_preview("001", str(candidate))
            self.assertTrue(dialog.accept_button.isEnabled())
            self.assertFalse(dialog.accept_button.isHidden())
            self.assertFalse(dialog.ignore_button.isHidden())
            self.assertFalse(dialog.candidate_preview.pixmap().isNull())
            dialog.accept_button.click()
            self.assertEqual(accepted[0][0], "001")
            self.assertEqual(accepted[0][1], str(candidate))
            self.assertEqual(
                self.page.scenes()[0]["image_path"],
                str(candidate),
            )
            self.assertEqual(
                self.page.scenes()[0]["generation_overrides"]["seed"],
                123456,
            )
            self.assertEqual(self.page.scenes()[0]["shot"], "low-angle close-up")
            self.assertEqual(
                self.page.scenes()[0]["generation_overrides"]["style"]["medium"],
                "charcoal cinematic illustration",
            )
        self.application.processEvents()
        self.assertIsNone(self.page._regeneration_dialog)
        self.page.regenerate_button.click()
        self.assertIsNotNone(self.page._regeneration_dialog)
        assert self.page._regeneration_dialog is not None
        self.page._regeneration_dialog.ignore_button.click()

        with TemporaryDirectory() as temporary:
            for index, scene_id in enumerate(("001", "002"), start=1):
                frame = Path(temporary) / f"frame-{index}.png"
                frame.write_bytes(b"frame")
                self.page.set_frame_generated(scene_id, str(frame))
            self.assertFalse(self.page.render_button.isHidden())
            self.page.render_button.click()
        self.assertEqual(len(renders), 1)
        self.assertEqual(len(renders[0]), 2)
        self.assertIn("Rendering final video", self.page.activity_log.toPlainText())

    def test_regeneration_raw_prompt_bypasses_composition_and_warns_on_field_change(self) -> None:
        regeneration_requests: list[dict] = []
        self.page.regenerateRequested.connect(regeneration_requests.append)
        self.page.regenerate_button.click()
        dialog = self.page._regeneration_dialog
        assert dialog is not None

        raw_prompt = "RAW FINAL PROMPT with all manually composed instructions"
        dialog.raw_prompt_edit.setPlainText(raw_prompt)
        self.assertTrue(dialog._raw_prompt_customized)
        dialog.generate_button.click()

        self.assertEqual(
            regeneration_requests[0]["overrides"]["raw_prompt"],
            raw_prompt,
        )
        self.assertEqual(dialog.raw_prompt_edit.toPlainText(), raw_prompt)

        dialog._generating = False
        dialog.generate_button.setEnabled(True)
        with patch.object(QMessageBox, "warning") as warning:
            dialog.medium_edit.setText("new structured watercolor style")
        self.assertEqual(warning.call_count, 1)
        self.assertFalse(dialog._raw_prompt_customized)
        self.assertNotIn("raw_prompt", dialog.request_payload()["overrides"])
        self.assertIn(
            "STYLE: new structured watercolor style",
            dialog.raw_prompt_edit.toPlainText(),
        )
        dialog.close()

    def test_regeneration_dialog_discard_keeps_the_current_frame(self) -> None:
        rejected: list[tuple[str, str]] = []
        self.page.regenerationRejected.connect(
            lambda scene_id, path: rejected.append((scene_id, path))
        )
        with TemporaryDirectory() as temporary:
            current = Path(temporary) / "current.png"
            candidate = Path(temporary) / "candidate.png"
            for path, color in (
                (current, QColor("#17324d")),
                (candidate, QColor("#c9782a")),
            ):
                pixmap = QPixmap(32, 18)
                pixmap.fill(color)
                self.assertTrue(pixmap.save(str(path)))
            self.page.set_frame_generated("001", str(current))
            self.page.regenerate_button.click()
            dialog = self.page._regeneration_dialog
            assert dialog is not None
            dialog._generating = True
            self.page.set_regeneration_preview("001", str(candidate))
            dialog.ignore_button.click()

            self.assertEqual(
                self.page.scenes()[0]["image_path"],
                str(current),
            )
            self.assertEqual(rejected, [("001", str(candidate))])

    def test_audiobook_source_is_passive_until_explicit_button(self) -> None:
        page = VideoStoryboardPage(_translate)
        self.addCleanup(page.deleteLater)
        analysis_requests: list[object] = []
        generation_requests: list[object] = []
        page.analyzeRequested.connect(analysis_requests.append)
        page.generateFramesRequested.connect(generation_requests.append)

        page.set_audiobook_source(
            21,
            "Mara's journey",
            "The train crossed the valley.",
            [
                {
                    "segment_id": 4,
                    "start_seconds": 1.25,
                    "duration_seconds": 5.5,
                    "text": "The train crossed the valley.",
                    "timing_ready": True,
                }
            ],
            7.0,
        )
        self.assertEqual(page.scenes(), [])
        self.assertEqual(len(page.timeline_canvas.narration_cues), 1)
        self.assertEqual(analysis_requests, [])
        self.assertTrue(page.analyze_button.isEnabled())
        self.assertFalse(page.generate_frames_button.isEnabled())

        page.analyze_button.click()
        self.assertEqual(len(analysis_requests), 1)
        payload = analysis_requests[0]
        self.assertEqual(payload["project_id"], 21)
        self.assertEqual(payload["duration_seconds"], 7.0)
        self.assertEqual(payload["narration_cues"][0]["start_seconds"], 1.25)
        self.assertEqual(page.scenes(), [])

        page.set_analysis_result(
            [
                {
                    "id": "001",
                    "duration": 7,
                    "narration": "The train crossed the valley.",
                    "prompt": "A train crossing a green valley",
                }
            ]
        )
        self.assertEqual(page.scenes()[0]["image_path"], "")
        self.assertTrue(page.generate_frames_button.isEnabled())
        page.generate_frames_button.click()
        assert page._frame_batch_dialog is not None
        page._frame_batch_dialog.start_button.click()
        self.assertEqual(len(generation_requests), 1)

    def test_reanalysis_confirmation_is_delegated_to_analysis_preflight(self) -> None:
        self.page.set_audiobook_source(9, "Book", "Text", [], 12.0)
        self.page.set_analysis_result(
            [{"id": "001", "duration": 12, "prompt": "Existing scene"}]
        )
        requests: list[object] = []
        self.page.analyzeRequested.connect(requests.append)

        self.page.analyze_button.click()
        self.assertEqual(len(requests), 1)
        self.assertTrue(self.page.analyze_button.isEnabled())

    def test_partial_analysis_appears_immediately_and_is_exportable(self) -> None:
        self.page.set_audiobook_source(9, "Book", "Text", [], 12.0)
        self.page.set_analysis_partial_result(
            {
                "base_seed": 10,
                "completed_blocks": 1,
                "total_blocks": 3,
                "scenes": [
                    {
                        "id": "001",
                        "duration": 4,
                        "narration": "Text",
                        "prompt": "A visual scene",
                    }
                ],
            }
        )

        self.assertEqual(len(self.page.scenes()), 1)
        self.assertEqual(self.page.project_state()["plan"]["completed_blocks"], 1)
        self.assertIn("block 1/3", self.page.activity_log.toPlainText())

    def test_light_timeline_uses_contrasting_text_track_and_repeats_frame(self) -> None:
        palette = self.page.timeline_canvas.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f3f4f6"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#111827"))
        self.page.timeline_canvas.setPalette(palette)
        self.page.set_audiobook_source(
            3,
            "Book",
            "Visible narration",
            [
                {
                    "segment_id": 1,
                    "sequence_index": 0,
                    "start_seconds": 0,
                    "duration_seconds": 6,
                    "text": "Visible narration",
                    "timing_ready": True,
                }
            ],
            6,
        )
        with TemporaryDirectory() as temporary:
            frame_path = Path(temporary) / "frame.png"
            frame = QImage(160, 90, QImage.Format.Format_RGB32)
            for x in range(frame.width()):
                color = QColor("#e11d48") if x < 80 else QColor("#2563eb")
                for y in range(frame.height()):
                    frame.setPixelColor(x, y, color)
            self.assertTrue(frame.save(str(frame_path)))
            self.page.set_scenes(
                [{"id": "001", "duration": 6, "image_path": str(frame_path)}]
            )
            canvas = self.page.timeline_canvas
            rendered = QImage(
                canvas.size(),
                QImage.Format.Format_RGB32,
            )
            rendered.fill(QColor("#ffffff"))
            canvas.render(rendered)

            narration_fill = rendered.pixelColor(
                20,
                canvas.TEXT_TOP + 8,
            )
            self.assertGreater(narration_fill.lightness(), 150)

            video_rect = QRect(
                1,
                canvas.VIDEO_TOP + 3,
                round(6 * canvas.pixels_per_second) - 2,
                canvas.VIDEO_HEIGHT - 6,
            )
            image_rect = video_rect.adjusted(4, 23, -4, -22)
            tile_width = round(image_rect.height() * 16 / 9)
            first = rendered.pixelColor(
                image_rect.left() + 20,
                image_rect.center().y(),
            )
            repeated = rendered.pixelColor(
                image_rect.left() + tile_width + 20,
                image_rect.center().y(),
            )
            self.assertEqual(first, repeated)

    def test_track_icons_are_raised_and_background_matches_dark_tracks(self) -> None:
        self.page.resize(1280, 900)
        self.page.show()
        self.application.processEvents()
        labels = self.page.track_labels
        self.assertEqual(
            labels.mapTo(self.page, QPoint(0, 0)).y(),
            self.page.timeline_scroll.mapTo(self.page, QPoint(0, 0)).y(),
        )
        video_track = QRect(
            0,
            self.page.timeline_canvas.VIDEO_TOP,
            labels.width(),
            self.page.timeline_canvas.VIDEO_HEIGHT,
        )
        text_track = QRect(
            0,
            self.page.timeline_canvas.TEXT_TOP,
            labels.width(),
            self.page.timeline_canvas.TEXT_HEIGHT,
        )
        self.assertEqual(
            labels._video_icon_rect().center().y(),
            video_track.center().y() - 1,
        )
        self.assertEqual(
            labels._audiobook_icon_rect().center().y(),
            text_track.center().y() - 1,
        )

        palette = labels.palette()
        base = QColor("#111827")
        palette.setColor(QPalette.ColorRole.Base, base)
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1f2937"))
        labels.setPalette(palette)
        rendered = QImage(labels.size(), QImage.Format.Format_RGB32)
        rendered.fill(QColor("#ffffff"))
        labels.render(rendered)

        self.assertEqual(
            rendered.pixelColor(2, video_track.top() + 4),
            QColor("#1a2940"),
        )
        self.assertEqual(
            rendered.pixelColor(2, text_track.top() + 4),
            base,
        )

    def test_audio_preview_moves_playhead_and_selects_active_frame(self) -> None:
        with TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / "preview.wav"
            with wave.open(str(audio_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 8000)

            self.page.set_audiobook_source(
                4,
                "Preview book",
                "Narration",
                [],
                13.0,
                str(audio_path),
            )
            self.page.set_scenes(
                [
                    {"id": "001", "duration": 6, "prompt": "First"},
                    {"id": "002", "duration": 7, "prompt": "Second"},
                ]
            )
            self.page._on_preview_position_changed(6500)

            self.assertEqual(self.page.selected_scene().scene_id, "002")
            self.assertEqual(self.page.timeline_canvas.playhead_seconds, 6.5)
            self.assertIn("00:06", self.page.preview_time_label.text())

            rendered = QImage(
                self.page.timeline_canvas.size(),
                QImage.Format.Format_RGB32,
            )
            rendered.fill(QColor("#000000"))
            self.page.timeline_canvas.render(rendered)
            playhead_x = round(
                6.5 * self.page.timeline_canvas.pixels_per_second
            )
            self.assertGreater(
                rendered.pixelColor(
                    playhead_x,
                    self.page.timeline_canvas.VIDEO_TOP + 10,
                ).lightness(),
                240,
            )

            self.page.stop_preview()
            self.assertIsNone(self.page.timeline_canvas.playhead_seconds)
            self.page.preview_player.setSource(QUrl())
            self.application.processEvents()

    def test_project_direction_persists_and_overrides_regeneration_and_render(self) -> None:
        self.assertEqual(self.page.project_tabs.count(), 6)
        self.assertEqual(
            [
                self.page.project_tabs.tabText(index)
                for index in range(self.page.project_tabs.count())
            ],
            [
                "Styles",
                "Style details",
                "Historical periods",
                "Characters",
                "Locations",
                "Video motion",
            ],
        )
        self.page.set_configuration(
            {
                "enabled": True,
                "video": {
                    "zoom_percent": 14.0,
                    "transition_seconds": 0.7,
                },
            }
        )
        self.page.set_analysis_result(
            {
                "base_seed": 42,
                "narrative_context": {
                    "era": "late nineteenth century",
                    "era_source": "detected",
                },
                "style": {
                    "medium": "cinematic realism",
                    "palette": "amber and teal",
                    "lighting": "soft dusk light",
                    "continuity": "same wardrobe and locations",
                    "characters": ["Mara: short dark hair, green coat"],
                    "negative": "text, watermark",
                },
                "scenes": self.page.scenes(),
            }
        )
        self.assertEqual(self.page.seed_edit.text(), "42")
        self.assertEqual(
            self.page.story_era_edit.text(),
            "late nineteenth century",
        )
        self.assertNotIn(
            "continuity",
            self.page.project_state()["plan"]["style"],
        )
        self.assertEqual(
            self.page.style_gallery.currentItem().data(Qt.ItemDataRole.UserRole),
            "custom",
        )
        comic_item = self.page._style_gallery_item("comic_book")
        self.page.style_gallery.setCurrentItem(comic_item)
        self.assertEqual(
            self.page.project_state()["plan"]["style_mode"],
            "comic_book",
        )
        self.assertIn(
            "comic book style",
            self.page.style_medium_edit.text(),
        )
        self.page.seed_edit.setText("987654321")
        self.page.style_palette_edit.setText("desaturated blue and gold")
        self.page.story_era_edit.setText("Ancient Rome, 1st century CE")
        self.page.video_zoom_spin.setValue(125.0)
        self.page.video_transition_spin.setValue(1.1)
        self.page.default_motion_combo.setCurrentIndex(
            self.page.default_motion_combo.findData("zoom_out")
        )

        state = self.page.project_state()
        self.assertEqual(
            state["plan"]["style"]["palette"],
            "desaturated blue and gold",
        )
        self.assertEqual(
            state["plan"]["video_overrides"]["zoom_percent"],
            125.0,
        )
        self.assertEqual(
            state["plan"]["narrative_context"]["era"],
            "Ancient Rome, 1st century CE",
        )
        self.assertEqual(state["plan"]["base_seed"], 987654321)
        self.assertEqual(
            state["source"]["storyboard_overrides"]["seed"],
            987654321,
        )
        self.assertEqual(
            state["source"]["storyboard_overrides"]["narrative"]["era"],
            "Ancient Rome, 1st century CE",
        )

        renders: list[object] = []
        generations: list[object] = []
        self.page.renderRequested.connect(renders.append)
        self.page.generateFramesRequested.connect(generations.append)
        with TemporaryDirectory() as temporary:
            for index, scene_id in enumerate(("001", "002"), start=1):
                frame = Path(temporary) / f"frame-{index}.png"
                frame.write_bytes(b"frame")
                self.page.set_frame_generated(scene_id, str(frame))
            self.page.render_button.click()
            self.assertTrue(
                all(scene["motion"] == "zoom_out" for scene in renders[0])
            )

        self.page.regenerate_all_button.click()
        assert self.page._frame_batch_dialog is not None
        self.page._frame_batch_dialog.start_button.click()
        self.assertTrue(
            all(
                not scene["image_path"]
                for scene in generations[0]["scenes"]
            )
        )
        self.assertEqual(
            generations[0]["plan"]["style"]["palette"],
            "desaturated blue and gold",
        )
        self.assertEqual(generations[0]["plan"]["base_seed"], 987654321)
        self.assertFalse(
            self.page.project_tabs.widget(3).isAncestorOf(
                self.page.regenerate_all_button
            )
        )

    def test_continuity_entities_and_periods_are_visible_and_editable(self) -> None:
        self.page.set_analysis_result(
            {
                "style": {"medium": "comic book", "characters": []},
                "continuity": {
                    "version": 1,
                    "characters": [
                        {
                            "id": "char_luis",
                            "name": "Luis",
                            "aliases": [],
                            "identity_description": "square face and brown eyes",
                            "states": [
                                {
                                    "id": "char_luis_state_1",
                                    "description": "glasses, blue shirt and jeans",
                                    "from_seconds": 0.0,
                                    "to_seconds": 6.0,
                                },
                                {
                                    "id": "char_luis_state_2",
                                    "description": "without glasses, white shirt and shorts",
                                    "from_seconds": 6.0,
                                    "to_seconds": 13.0,
                                },
                            ],
                        }
                    ],
                    "locations": [
                        {
                            "id": "loc_house",
                            "name": "Maria's house",
                            "identity_description": "white house with two windows",
                            "states": [],
                        }
                    ],
                    "eras": [
                        {
                            "id": "era_1950s",
                            "description": "1950s Spain",
                            "material_culture": "period cars and clothing",
                            "from_seconds": 0.0,
                            "to_seconds": 13.0,
                        }
                    ],
                    "assignments": [],
                },
                "scenes": self.page.scenes(),
            }
        )

        self.assertEqual(self.page.continuity_characters_tree.topLevelItemCount(), 1)
        character = self.page.continuity_characters_tree.topLevelItem(0)
        self.assertEqual(character.text(0), "Luis")
        self.assertGreaterEqual(
            self.page.continuity_characters_tree.sizeHintForIndex(
                self.page.continuity_characters_tree.indexFromItem(character)
            ).height(),
            38,
        )
        self.assertEqual(character.childCount(), 2)
        self.assertEqual(character.child(1).text(1), "00:06")
        self.assertTrue(self.page.style_characters_edit.isHidden())
        self.assertEqual(self.page.continuity_locations_tree.topLevelItemCount(), 1)
        self.assertEqual(self.page.continuity_eras_tree.topLevelItemCount(), 1)

        character.child(1).setText(3, "older man in a white shirt")
        self.application.processEvents()

        plan = self.page.project_state()["plan"]
        self.assertEqual(
            plan["continuity"]["characters"][0]["states"][1]["description"],
            "older man in a white shirt",
        )
        self.assertTrue(
            any(
                value.startswith("char_luis_state_2:")
                for value in plan["style"]["characters"]
            )
        )

    def test_character_states_can_be_added_edited_and_deleted(self) -> None:
        scenes = self.page.scenes()
        scenes[0]["characters"] = ["char_luis_state_1"]
        scenes[1]["characters"] = ["char_luis_state_2"]
        self.page.set_analysis_result(
            {
                "style": {"medium": "comic book", "characters": []},
                "continuity": {
                    "characters": [
                        {
                            "id": "char_luis",
                            "name": "Luis",
                            "identity_description": "square face and brown eyes",
                            "states": [
                                {
                                    "id": "char_luis_state_1",
                                    "description": "blue shirt",
                                    "from_seconds": 0.0,
                                    "to_seconds": 7.0,
                                },
                                {
                                    "id": "char_luis_state_2",
                                    "description": "white shirt",
                                    "from_seconds": 7.0,
                                    "to_seconds": 13.0,
                                },
                            ],
                        }
                    ],
                    "locations": [],
                    "eras": [],
                    "assignments": [],
                },
                "scenes": scenes,
            }
        )
        character_item = self.page.continuity_characters_tree.topLevelItem(0)
        self.page.continuity_characters_tree.setCurrentItem(character_item)
        self.assertTrue(self.page.new_character_state_button.isEnabled())
        self.assertFalse(self.page.delete_character_state_button.isEnabled())

        character = self.page.project_state()["plan"]["continuity"]["characters"][0]
        state_id = self.page._add_character_state_record(
            character,
            8.0,
            13.0,
            "green coat",
        )

        self.assertEqual(state_id, "char_luis_state_3")
        self.assertTrue(self.page.delete_character_state_button.isEnabled())
        self.assertEqual(
            self.page.scenes()[1]["characters"],
            ["char_luis_state_3"],
        )
        selected = self.page.continuity_characters_tree.currentItem()
        selected.setText(1, "00:09")
        self.application.processEvents()
        states = self.page.project_state()["plan"]["continuity"]["characters"][0]["states"]
        added = next(state for state in states if state["id"] == state_id)
        self.assertEqual(added["from_seconds"], 9.0)

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.page._delete_character_state()

        states = self.page.project_state()["plan"]["continuity"]["characters"][0]["states"]
        self.assertNotIn(state_id, {state["id"] for state in states})
        self.assertEqual(
            self.page.scenes()[1]["characters"],
            ["char_luis_state_2"],
        )

        character_item = self.page.continuity_characters_tree.topLevelItem(0)
        self.page.continuity_characters_tree.setCurrentItem(character_item)
        self.assertTrue(self.page.delete_character_button.isEnabled())
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.page._delete_character()
        self.assertEqual(
            self.page.project_state()["plan"]["continuity"]["characters"],
            [],
        )
        self.assertEqual(self.page.scenes()[0]["characters"], [])
        self.assertFalse(self.page.delete_character_button.isEnabled())

    def test_regeneration_entity_buttons_insert_markup_and_bind_states(self) -> None:
        scenes = self.page.scenes()
        self.page.set_analysis_result(
            {
                "style": {"medium": "comic book", "characters": []},
                "continuity": {
                    "characters": [
                        {
                            "id": "ana",
                            "name": "Ana",
                            "identity_description": "dark curly hair",
                            "states": [
                                {
                                    "id": "ana_blue",
                                    "description": "blue coat",
                                    "from_seconds": 0,
                                    "to_seconds": 20,
                                }
                            ],
                        }
                    ],
                    "locations": [
                        {
                            "id": "plaza",
                            "name": "Central Plaza",
                            "identity_description": "stone fountain",
                            "states": [
                                {
                                    "id": "plaza_day",
                                    "description": "sunny morning",
                                    "from_seconds": 0,
                                    "to_seconds": 20,
                                }
                            ],
                        }
                    ],
                    "eras": [],
                    "assignments": [],
                },
                "scenes": scenes,
            }
        )
        self.page.regenerate_button.click()
        dialog = self.page._regeneration_dialog
        assert dialog is not None
        character = dialog.plan["continuity"]["characters"][0]
        location = dialog.plan["continuity"]["locations"][0]
        dialog._insert_entity(character, character["states"][0], "characters")
        dialog._insert_entity(location, location["states"][0], "locations")

        self.assertIn("@Ana", dialog.prompt_edit.toPlainText())
        self.assertIn("@Central Plaza", dialog.prompt_edit.toPlainText())
        payload = dialog.request_payload()
        self.assertNotIn("@", payload["prompt"])
        self.assertEqual(
            payload["overrides"]["location_state_ids"],
            ["plaza_day"],
        )
        self.assertTrue(
            any(
                line.startswith("ana_blue:")
                for line in payload["overrides"]["style"]["characters"]
            )
        )
        self.assertNotIn("@", dialog.raw_prompt_edit.toPlainText())
        dialog.close()

    def test_timeline_click_moves_preview_playhead(self) -> None:
        requested: list[float] = []
        self.page.timeline_canvas.seekRequested.connect(requested.append)
        target_seconds = 4.25
        target_x = round(
            target_seconds * self.page.timeline_canvas.pixels_per_second
        )

        QTest.mouseClick(
            self.page.timeline_canvas,
            Qt.MouseButton.LeftButton,
            pos=QPoint(target_x, 12),
        )

        self.assertAlmostEqual(requested[-1], target_seconds, places=1)
        self.assertAlmostEqual(
            self.page.timeline_canvas.playhead_seconds,
            target_seconds,
            places=1,
        )

    def test_restored_legacy_storyboard_extends_first_scene_for_voice_offset(self) -> None:
        page = VideoStoryboardPage(_translate)
        self.addCleanup(page.deleteLater)
        page.set_audiobook_source(
            7,
            "Offset book",
            "Narration",
            [
                {
                    "segment_id": 1,
                    "start_seconds": 2.0,
                    "duration_seconds": 11.0,
                    "text": "Narration",
                    "timing_ready": True,
                }
            ],
            13.0,
            "",
            2.0,
        )
        page.restore_project_state(
            {
                "plan": {"base_seed": 10},
                "scenes": [
                    {"id": "001", "duration": 6.0},
                    {"id": "002", "duration": 5.0},
                ],
                "analysis_status": "ready",
            }
        )

        scenes = page.scenes()
        self.assertEqual(scenes[0]["start_seconds"], 0.0)
        self.assertEqual(scenes[0]["duration_seconds"], 8.0)
        self.assertEqual(scenes[1]["start_seconds"], 8.0)
        self.assertEqual(
            page.project_state()["plan"]["voice_start_offset_seconds"],
            2.0,
        )
        migrated_state = page.project_state()
        page.restore_project_state(
            {
                **migrated_state,
                "analysis_status": "ready",
            }
        )
        self.assertEqual(page.scenes()[0]["duration_seconds"], 8.0)

    def test_development_alignment_debug_ui_is_removed(self) -> None:
        self.assertFalse(hasattr(self.page, "debug_alignment_button"))
        self.assertFalse(hasattr(self.page, "_debug_dialog"))


if __name__ == "__main__":
    unittest.main()
