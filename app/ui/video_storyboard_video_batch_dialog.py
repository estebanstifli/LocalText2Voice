"""Video batch options and progress, sharing the frame batch interaction."""
from PySide6.QtWidgets import QLabel, QPlainTextEdit

from app.ui.video_storyboard_frame_batch_dialog import VideoStoryboardFrameBatchDialog

DEFAULT_VIDEO_MOTION_PROMPT = (
    "Bring this scene to life with natural, purposeful motion appropriate to the scene. "
    "Animate any characters with subtle, believable gestures and body movement, and add gentle environmental motion where appropriate. "
    "Preserve the subjects, composition, and visual style; avoid a static freeze-frame or unnecessary camera movement."
)


class VideoStoryboardVideoBatchDialog(VideoStoryboardFrameBatchDialog):
    def __init__(self, tr, *, total_count, existing_count, motion_prompt=DEFAULT_VIDEO_MOTION_PROMPT, parent=None):
        super().__init__(tr, mode="generate", total_count=total_count, existing_count=existing_count, parent=parent)
        title = tr("storyboard_video_batch_title", "Generate storyboard videos")
        self.setWindowTitle(title)
        self.heading_label.setText(title)
        self.info_label.setText(tr("storyboard_video_batch_info", "Select scenes by their timeline numbers. Scenes without an image or prompt are skipped. Existing videos are kept unless overwrite is enabled. Each generated video is accepted automatically; existing clips are replaced only after a successful generation."))
        self.summary_label.setText(tr("storyboard_video_batch_summary", "Scenes: {total} · Existing videos: {existing}", total=total_count, existing=existing_count))
        self.overwrite_checkbox.setText(tr("storyboard_video_batch_overwrite", "Overwrite existing videos in selection"))
        label = QLabel(tr("storyboard_video_motion_label", "Additional motion instruction (editable; leave empty to use only the scene prompt)"))
        label.setWordWrap(True)
        self.motion_prompt_edit = QPlainTextEdit(motion_prompt)
        self.motion_prompt_edit.setAccessibleName(label.text())
        self.motion_prompt_edit.setMaximumHeight(115)
        index = self.layout().indexOf(self.overwrite_checkbox) + 1
        self.layout().insertWidget(index, label)
        self.layout().insertWidget(index + 1, self.motion_prompt_edit)
        self.cancel_process_button.setText(tr("storyboard_video_batch_stop", "Stop after current video"))
        self.resize(800, 700)

    def _starting_message(self):
        return self.tr_text("storyboard_video_batch_starting", "Starting video generation...")

    def _start(self):
        super()._start()
        if self._running or self._finished:
            self.motion_prompt_edit.setReadOnly(True)

    def _cancel_process(self):
        if not self._running or self._cancel_requested:
            return
        self._cancel_requested = True
        self.cancel_process_button.setEnabled(False)
        message = self.tr_text("storyboard_video_batch_stopping", "Stopping after the current video finishes. Remaining scenes will not be generated.")
        self.status_label.setText(message)
        self.append_log(message)
        self.cancelRequested.emit()
