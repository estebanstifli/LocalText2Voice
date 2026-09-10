from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.video_storyboard_comfyui import compile_effective_scene_prompt
from app.core.video_storyboard_prompt_entities import (
    decorate_storyboard_prompt,
    strip_storyboard_prompt_markers,
)
from app.core.video_storyboard_video_comfyui import (
    VIDEO_WORKFLOW_PROFILES,
    video_generation_metrics,
    video_workflow_profile,
)
from app.ui.icons import ui_icon
from app.ui.video_storyboard_prompt_highlighter import (
    VideoStoryboardPromptHighlighter,
)


Translate = Callable[..., str]
PREVIEW_WIDTH = 480
PREVIEW_HEIGHT = 270


class _ReferenceFramePreview(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = QPixmap()
        self._empty_text = ""
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.setObjectName("storyboardPreview")

    def set_image(self, path: str, empty_text: str) -> None:
        self._source = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        self._empty_text = empty_text
        self._refresh()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt API
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self._source.isNull():
            self.clear()
            self.setText(self._empty_text)
            return
        self.setText("")
        self.setPixmap(
            self._source.scaled(
                max(1, self.width()),
                max(1, self.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class VideoStoryboardVideoDialog(QDialog):
    """Generate and review an optional ComfyUI video layer for one scene."""

    generateRequested = Signal(object)
    candidateAccepted = Signal(object)
    candidateRejected = Signal(str, str)

    def __init__(
        self,
        tr: Translate,
        scene: dict[str, Any],
        plan: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.scene = dict(scene)
        self.plan = dict(plan)
        self.scene_id = str(scene.get("scene_id") or scene.get("id") or "")
        self._candidate_path = ""
        self._candidate_duration_seconds = 0.0
        self._generating = False
        self._resolved = False
        self._pause_on_first_frame = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle(
            self.tr_text(
                "video_storyboard_generate_video_title",
                "Generate scene video",
            )
        )
        self.resize(1120, 820)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(
            self.tr_text(
                "video_storyboard_generate_video_scene",
                "Scene {scene} — optional video layer",
                scene=self.scene_id,
            )
        )
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        hint = QLabel(
            self.tr_text(
                "video_storyboard_generate_video_help",
                "The reference frame is always preserved. Accepting the candidate makes the scene use this clip instead of the still-frame motion.",
            )
        )
        hint.setObjectName("helperLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        previews = QHBoxLayout()
        frame_group = QGroupBox(
            self.tr_text("video_storyboard_reference_frame", "Reference frame")
        )
        frame_layout = QVBoxLayout(frame_group)
        self.frame_preview = _ReferenceFramePreview()
        self.frame_preview.set_image(
            str(self.scene.get("image_path") or ""),
            self.tr_text("video_storyboard_frame_pending", "Frame not generated"),
        )
        frame_layout.addWidget(self.frame_preview)

        candidate_group = QGroupBox(
            self.tr_text("video_storyboard_video_candidate", "Video candidate")
        )
        candidate_layout = QVBoxLayout(candidate_group)
        self.video_stack = QStackedWidget()
        self.video_stack.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.video_placeholder = QLabel(
            self.tr_text(
                "video_storyboard_candidate_not_generated",
                "Generate a candidate to compare it here",
            )
        )
        self.video_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_placeholder.setWordWrap(True)
        self.video_placeholder.setObjectName("storyboardPreview")
        self.video_widget = QVideoWidget()
        self.video_widget.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.video_widget.setStyleSheet("background: #090f1a;")
        self.video_stack.addWidget(self.video_placeholder)
        self.video_stack.addWidget(self.video_widget)
        candidate_layout.addWidget(self.video_stack, 1)
        playback = QHBoxLayout()
        self.play_button = QPushButton(self.tr_text("play", "Play"))
        self.play_button.setIcon(ui_icon("play"))
        self.pause_button = QPushButton(self.tr_text("pause", "Pause"))
        self.pause_button.setIcon(ui_icon("pause"))
        self.stop_button = QPushButton(self.tr_text("stop", "Stop"))
        self.stop_button.setIcon(ui_icon("stop"))
        playback.addWidget(self.play_button)
        playback.addWidget(self.pause_button)
        playback.addWidget(self.stop_button)
        playback.addStretch(1)
        candidate_layout.addLayout(playback)
        previews.addWidget(frame_group, 1)
        previews.addWidget(candidate_group, 1)
        layout.addLayout(previews, 1)

        options = QHBoxLayout()
        role_label = QLabel(
            self.tr_text("video_storyboard_reference_position", "Reference position")
        )
        self.frame_role_combo = QComboBox()
        self.frame_role_combo.addItem(
            self.tr_text(
                "video_storyboard_reference_at_start",
                "Frame at start",
            ),
            "start",
        )
        self.frame_role_combo.addItem(
            self.tr_text(
                "video_storyboard_reference_at_end",
                "Frame at end",
            ),
            "end",
        )
        stored_role = str(self.scene.get("video_frame_role") or "start")
        self.frame_role_combo.setCurrentIndex(
            max(0, self.frame_role_combo.findData(stored_role))
        )
        options.addWidget(role_label)
        options.addWidget(self.frame_role_combo)
        options.addStretch(1)
        scene_duration = max(
            0.1,
            float(self.scene.get("duration_seconds") or 0.1),
        )
        video_config = self.scene.get("_video_config", {})
        video_config = video_config if isinstance(video_config, dict) else {}
        video_fps = max(1, int(video_config.get("fps") or 24))
        width = max(64, int(video_config.get("width") or 640))
        height = max(64, int(video_config.get("height") or 360))
        profile = video_workflow_profile(video_config.get("workflow_profile"))
        frame_count, _generated_duration = video_generation_metrics(
            video_config,
            scene_duration,
        )
        description = f"{VIDEO_WORKFLOW_PROFILES[profile]} · {width} × {height} · {frame_count} frames · {video_fps} FPS · {scene_duration:.1f} s"
        if profile == "wan22_rapid" and self.scene.get("_video_provider") != "runpod":
            if video_config.get("wan_behavior") == "stretch":
                description = f"{VIDEO_WORKFLOW_PROFILES[profile]} · {frame_count} frames · {frame_count / scene_duration:.2f} FPS · {scene_duration:.2f} s"
            elif scene_duration > _generated_duration + 0.0001:
                description += "\n" + self.tr_text("wan_split_preview", "On acceptance: {video:.3f} s of video + a continuation scene of {still:.3f} s using the last frame.", video=_generated_duration, still=scene_duration - _generated_duration)
        if self.scene.get("_video_provider") == "runpod":
            from app.core.video_storyboard_runpod import video_duration, estimate_cost
            config = self.scene.get("_runpod_config", {})
            from app.core.runpod_video_models import model_name
            seconds = video_duration(scene_duration, config)
            cost = estimate_cost({"runpod": config}, durations=[scene_duration])
            price = f"~${cost:.2f}" if cost is not None else "price depends on endpoint"
            description = f"Runpod · {model_name(config)} · {config.get('video_size', '1280*720')} · {seconds}s generated → {scene_duration:.1f}s scene · {price}"
        elif self.scene.get("_video_provider") == "disabled":
            description = self.tr_text("storyboard_video_unconfigured", "Select a video provider in Settings > Video Storyboard.")
        info = QLabel(description)
        info.setWordWrap(True)
        options.addWidget(info)
        layout.addLayout(options)

        prompt_label = QLabel(
            self.tr_text("video_storyboard_video_prompt", "Video prompt")
        )
        prompt_label.setObjectName("sectionTitle")
        self.prompt_edit = QPlainTextEdit()
        default_prompt = str(self.scene.get("video_prompt") or "").strip()
        if not default_prompt:
            default_prompt = compile_effective_scene_prompt(self.plan, self.scene)
        self.prompt_edit.setPlainText(
            decorate_storyboard_prompt(default_prompt, self.plan)
        )
        self.prompt_edit.setMaximumHeight(145)
        self._prompt_highlighter = VideoStoryboardPromptHighlighter(
            self.prompt_edit.document(), self.plan
        )
        layout.addWidget(prompt_label)
        layout.addWidget(self.prompt_edit)

        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        self.status_label = QLabel(
            self.tr_text(
                "video_storyboard_video_ready",
                "Adjust the prompt and generate a video candidate. The current frame and any accepted clip remain unchanged until you accept.",
            )
        )
        self.status_label.setObjectName("helperLabel")
        self.status_label.setWordWrap(True)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(90)
        self.log_edit.hide()
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addWidget(self.log_edit)

        buttons = QHBoxLayout()
        self.generate_button = QPushButton(
            self.tr_text(
                "video_storyboard_generate_video_candidate",
                "Generate video candidate",
            )
        )
        self.generate_button.setIcon(ui_icon("convert_video"))
        self.accept_button = QPushButton(
            self.tr_text(
                "video_storyboard_accept_video",
                "Accept and use video",
            )
        )
        self.accept_button.setObjectName("primaryButton")
        self.discard_button = QPushButton(
            self.tr_text("video_storyboard_discard_video", "Discard")
        )
        self.close_button = QPushButton(self.tr_text("close", "Close"))
        self.accept_button.hide()
        self.discard_button.hide()
        buttons.addWidget(self.generate_button)
        buttons.addStretch(1)
        buttons.addWidget(self.accept_button)
        buttons.addWidget(self.discard_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setMuted(True)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.video_widget.videoSink().videoFrameChanged.connect(
            self._on_video_frame_changed
        )
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.play_button.clicked.connect(self._play_candidate)
        self.pause_button.clicked.connect(self.player.pause)
        self.stop_button.clicked.connect(self._stop_playback)
        self.generate_button.clicked.connect(self._request_generation)
        self.accept_button.clicked.connect(self._accept_candidate)
        self.discard_button.clicked.connect(self._discard_candidate)
        self.close_button.clicked.connect(self.close)
        self._sync_playback_buttons(False)

    def request_payload(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "prompt": strip_storyboard_prompt_markers(
                self.prompt_edit.toPlainText(), self.plan
            ).strip(),
            "frame_role": str(self.frame_role_combo.currentData() or "start"),
        }

    def _request_generation(self) -> None:
        prompt = strip_storyboard_prompt_markers(
            self.prompt_edit.toPlainText(), self.plan
        ).strip()
        if self._generating or not prompt:
            return
        rejected_path = self._candidate_path
        self._candidate_path = ""
        self._release_player()
        if rejected_path:
            self.candidateRejected.emit(self.scene_id, rejected_path)
        self._pause_on_first_frame = False
        self.video_placeholder.setText(
            self.tr_text(
                "video_storyboard_generating_scene_video",
                "Generating video for scene {scene}...",
                scene=self.scene_id,
            )
        )
        self.video_stack.setCurrentWidget(self.video_placeholder)
        self._generating = True
        self.generate_button.setEnabled(False)
        self.accept_button.hide()
        self.discard_button.hide()
        self.frame_role_combo.setEnabled(False)
        self.prompt_edit.setEnabled(False)
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)
        self.log_edit.clear()
        self.log_edit.show()
        self._sync_playback_buttons(False)
        self.generateRequested.emit(self.request_payload())

    def set_progress(self, message: str, percentage: int) -> None:
        self.status_label.setText(message)
        self.progress_bar.setRange(0, 0 if percentage < 0 else 100)
        if percentage >= 0:
            self.progress_bar.setValue(max(0, min(100, int(percentage))))
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{stamp}] {message}")

    def set_candidate(self, path: str, duration_seconds: float = 0.0) -> None:
        self._generating = False
        self._candidate_path = str(path or "")
        self._candidate_duration_seconds = max(0.0, float(duration_seconds or 0.0))
        self.video_placeholder.setText(
            self.tr_text(
                "video_storyboard_video_candidate_ready",
                "Video candidate ready. Play it before accepting or discarding it.",
            )
        )
        self.video_stack.setCurrentWidget(self.video_placeholder)
        self._pause_on_first_frame = bool(self._candidate_path)
        self.player.setSource(QUrl.fromLocalFile(self._candidate_path))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText(
            self.tr_text(
                "video_storyboard_video_candidate_ready",
                "Video candidate ready. Play it before accepting or discarding it.",
            )
        )
        self.generate_button.setEnabled(True)
        self.frame_role_combo.setEnabled(True)
        self.prompt_edit.setEnabled(True)
        self.accept_button.setVisible(bool(self._candidate_path))
        self.discard_button.setVisible(bool(self._candidate_path))
        self._sync_playback_buttons(bool(self._candidate_path))
        if self._candidate_path:
            # Prime Qt's decoder so the candidate surface shows its cover frame
            # immediately instead of remaining black until the user presses Play.
            QTimer.singleShot(0, self.player.play)

    def set_failed(self, error: str) -> None:
        self._generating = False
        self.progress_bar.hide()
        self.status_label.setText(str(error))
        self.log_edit.appendPlainText(str(error))
        self.generate_button.setEnabled(True)
        self.frame_role_combo.setEnabled(True)
        self.prompt_edit.setEnabled(True)
        self.accept_button.setVisible(bool(self._candidate_path))
        self.discard_button.setVisible(bool(self._candidate_path))
        self._sync_playback_buttons(bool(self._candidate_path))

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _accept_candidate(self) -> None:
        if not self._candidate_path:
            return
        payload = self.request_payload()
        payload["video_path"] = self._candidate_path
        payload["video_duration_seconds"] = self._candidate_duration_seconds
        self._resolved = True
        # Windows Media Foundation keeps the MP4 handle open after stop().
        # Clear the source before the receiver moves the candidate into the
        # project so os.replace() does not fail with WinError 32.
        self._release_player()
        self.candidateAccepted.emit(payload)
        self.accept()

    def _discard_candidate(self) -> None:
        path = self._candidate_path
        self._resolved = True
        self._release_player()
        if path:
            self.candidateRejected.emit(self.scene_id, path)
        self.reject()

    def _stop_playback(self) -> None:
        if not self._candidate_path:
            return
        self._prime_cover_frame()

    def _prime_cover_frame(self) -> None:
        if not self._candidate_path:
            return
        self.player.pause()
        self.player.setPosition(0)
        self._pause_on_first_frame = True
        self.video_stack.setCurrentWidget(self.video_placeholder)
        QTimer.singleShot(0, self.player.play)

    def _play_candidate(self) -> None:
        if not self._candidate_path:
            return
        self._pause_on_first_frame = False
        self.video_stack.setCurrentWidget(self.video_widget)
        self.player.play()

    def _on_video_frame_changed(self, frame: Any) -> None:
        if not frame.isValid():
            return
        self.video_stack.setCurrentWidget(self.video_widget)
        if self._pause_on_first_frame:
            self._pause_on_first_frame = False
            QTimer.singleShot(0, self.player.pause)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if (
            status == QMediaPlayer.MediaStatus.EndOfMedia
            and self._candidate_path
            and not self._generating
        ):
            # The native video surface goes black at EOF on Windows. Return to
            # the candidate cover frame instead of leaving an empty rectangle.
            QTimer.singleShot(0, self._prime_cover_frame)

    def _release_player(self) -> None:
        self._pause_on_first_frame = False
        self.player.stop()
        self.player.setSource(QUrl())
        self.video_stack.setCurrentWidget(self.video_placeholder)

    def _sync_playback_buttons(self, enabled: bool) -> None:
        self.play_button.setEnabled(enabled)
        self.pause_button.setEnabled(enabled)
        self.stop_button.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._generating:
            event.ignore()
            return
        self._release_player()
        if self._candidate_path and not self._resolved:
            self.candidateRejected.emit(self.scene_id, self._candidate_path)
            self._resolved = True
        super().closeEvent(event)
