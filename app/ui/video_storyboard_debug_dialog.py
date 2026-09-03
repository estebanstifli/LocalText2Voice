from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QCloseEvent, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import ui_icon


Translate = Callable[..., str]

_COLORS = (
    "#fecaca", "#fed7aa", "#fef08a", "#bbf7d0", "#a5f3fc",
    "#bfdbfe", "#c7d2fe", "#ddd6fe", "#f5d0fe", "#fbcfe8",
)


class VideoStoryboardDebugDialog(QDialog):
    """Temporary visual auditor for semantic scene and audio alignment."""

    def __init__(
        self,
        tr: Translate,
        text: str,
        scenes: list[dict[str, Any]],
        plan: dict[str, Any],
        audio_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr_text = tr
        self.text = str(text or "").strip()
        self.scenes = [dict(scene) for scene in scenes]
        self.plan = dict(plan)
        self.audio_path = str(audio_path or "")
        self._active_index = -1
        self.setWindowTitle(
            self.tr_text(
                "video_storyboard_debug_title",
                "Storyboard semantic alignment debug",
            )
        )
        self.resize(1380, 820)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._build_ui()
        self._populate()
        self._setup_player()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.play_button = QPushButton(self.tr_text("play", "Play"))
        self.play_button.setIcon(ui_icon("play"))
        self.pause_button = QPushButton(self.tr_text("pause", "Pause"))
        self.pause_button.setIcon(ui_icon("pause"))
        self.stop_button = QPushButton(self.tr_text("stop", "Stop"))
        self.stop_button.setIcon(ui_icon("stop"))
        self.time_label = QLabel("00:00 / 00:00")
        self.copy_button = QPushButton(
            self.tr_text(
                "video_storyboard_debug_copy",
                "Copy debug report",
            )
        )
        self.copy_button.setIcon(ui_icon("copy"))
        controls.addWidget(self.play_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.time_label)
        controls.addStretch(1)
        controls.addWidget(self.copy_button)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlaceholderText(
            self.tr_text(
                "video_storyboard_debug_no_text",
                "No audiobook text is available.",
            )
        )
        splitter.addWidget(self.text_view)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.scene_list = QListWidget()
        self.scene_list.setAlternatingRowColors(False)
        right_layout.addWidget(self.scene_list, 3)
        self.frame_preview = QLabel(
            self.tr_text(
                "video_storyboard_frame_pending",
                "Frame not generated",
            )
        )
        self.frame_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_preview.setMinimumHeight(220)
        self.frame_preview.setStyleSheet(
            "QLabel { background: #101827; color: #dbeafe; border-radius: 6px; }"
        )
        right_layout.addWidget(self.frame_preview, 2)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 4)

        log_label = QLabel(
            self.tr_text(
                "video_storyboard_debug_log",
                "Persisted alignment report",
            )
        )
        log_label.setObjectName("sectionTitle")
        layout.addWidget(log_label)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(210)
        layout.addWidget(self.log_view)

        self.play_button.clicked.connect(self._play)
        self.pause_button.clicked.connect(self._pause)
        self.stop_button.clicked.connect(self._stop)
        self.copy_button.clicked.connect(self._copy_report)
        self.scene_list.itemClicked.connect(self._scene_item_clicked)

    def _setup_player(self) -> None:
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self.player.setAudioOutput(self.audio_output)
        if self.audio_path and Path(self.audio_path).is_file():
            self.player.setSource(QUrl.fromLocalFile(str(Path(self.audio_path).resolve())))
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(lambda _value: self._update_time())
        self.player.playbackStateChanged.connect(lambda _value: self._sync_controls())
        self._sync_controls()

    def _populate(self) -> None:
        self.text_view.setPlainText(self.text)
        search_cursor = 0
        semantic_colors: dict[str, QColor] = {}
        for index, scene in enumerate(self.scenes):
            semantic_id = str(scene.get("semantic_scene_id") or scene.get("scene_id") or scene.get("id") or index)
            if semantic_id not in semantic_colors:
                semantic_colors[semantic_id] = QColor(
                    _COLORS[len(semantic_colors) % len(_COLORS)]
                )
            color = semantic_colors[semantic_id]
            start = _integer(scene.get("source_text_start"), -1)
            end = _integer(scene.get("source_text_end"), -1)
            narration = str(scene.get("narration") or "").strip()
            if not (0 <= start < end <= len(self.text)) and narration:
                start = self.text.find(narration, search_cursor)
                end = start + len(narration) if start >= 0 else -1
            if 0 <= start < end <= len(self.text):
                cursor = self.text_view.textCursor()
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                char_format = QTextCharFormat()
                char_format.setBackground(color)
                char_format.setForeground(QColor("#111827"))
                cursor.mergeCharFormat(char_format)
                search_cursor = end

            scene_start = float(scene.get("start_seconds") or 0.0)
            scene_end = scene_start + float(scene.get("duration_seconds") or scene.get("duration") or 0.0)
            item = QListWidgetItem(
                self.tr_text(
                    "video_storyboard_debug_scene_row",
                    "Scene {scene} · {start} → {end} · units {first}-{last}\n{narration}",
                    scene=str(scene.get("scene_id") or scene.get("id") or index + 1),
                    start=_time(scene_start),
                    end=_time(scene_end),
                    first=scene.get("source_unit_start", "?"),
                    last=scene.get("source_unit_end", "?"),
                    narration=narration,
                )
            )
            item.setBackground(color)
            item.setForeground(QColor("#111827"))
            item.setToolTip(
                "Semantic scene / coverage: "
                f"{semantic_id} / {scene.get('coverage_index', 1)} of {scene.get('coverage_count', 1)}\n"
                f"Shot strategy: {scene.get('shot_strategy') or scene.get('shot') or ''}\n"
                "Alignment confidence: "
                f"{float(scene.get('alignment_confidence') or 0.0):.3f}\n"
                "Original Ollama narration: "
                f"{scene.get('llm_narration') or ''}"
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.scene_list.addItem(item)

        report = {
            "source_duration_seconds": self.plan.get("source_duration_seconds"),
            "voice_start_offset_seconds": self.plan.get("voice_start_offset_seconds"),
            "alignment_debug": self.plan.get("alignment_debug", {}),
            "scenes": self.scenes,
        }
        self._report = json.dumps(report, ensure_ascii=False, indent=2)
        self.log_view.setPlainText(self._report)
        if self.scenes:
            self._set_active_scene(0)

    def _play(self) -> None:
        if self.player.duration() > 0 and self.player.position() >= self.player.duration() - 20:
            self.player.setPosition(0)
        self.player.play()

    def _pause(self) -> None:
        self.player.pause()

    def _stop(self) -> None:
        self.player.stop()
        self.player.setPosition(0)
        self._position_changed(0)

    def _scene_item_clicked(self, item: QListWidgetItem) -> None:
        index = int(item.data(Qt.ItemDataRole.UserRole))
        if 0 <= index < len(self.scenes):
            seconds = float(self.scenes[index].get("start_seconds") or 0.0)
            self.player.setPosition(round(seconds * 1000))
            self._position_changed(round(seconds * 1000))

    def _position_changed(self, position_ms: int) -> None:
        seconds = max(0.0, position_ms / 1000.0)
        active = next(
            (
                index
                for index, scene in enumerate(self.scenes)
                if float(scene.get("start_seconds") or 0.0)
                <= seconds
                < float(scene.get("start_seconds") or 0.0)
                + float(scene.get("duration_seconds") or scene.get("duration") or 0.0)
            ),
            len(self.scenes) - 1 if self.scenes else -1,
        )
        if active >= 0:
            self._set_active_scene(active)
        self._update_time(position_ms)

    def _set_active_scene(self, index: int) -> None:
        if not 0 <= index < len(self.scenes):
            return
        self._active_index = index
        self.scene_list.setCurrentRow(index)
        self.scene_list.scrollToItem(self.scene_list.item(index))
        scene = self.scenes[index]
        path = Path(str(scene.get("image_path") or ""))
        if path.is_file():
            pixmap = QPixmap(str(path))
            self.frame_preview.setPixmap(
                pixmap.scaled(
                    max(1, self.frame_preview.width() - 10),
                    max(1, self.frame_preview.height() - 10),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.frame_preview.setPixmap(QPixmap())
            self.frame_preview.setText(
                self.tr_text("video_storyboard_frame_pending", "Frame not generated")
            )

    def _update_time(self, position_ms: int | None = None) -> None:
        position = self.player.position() if position_ms is None else position_ms
        duration = max(
            self.player.duration(),
            round(float(self.plan.get("source_duration_seconds") or 0.0) * 1000),
        )
        self.time_label.setText(f"{_time(position / 1000)} / {_time(duration / 1000)}")

    def _sync_controls(self) -> None:
        available = bool(self.audio_path and Path(self.audio_path).is_file())
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setEnabled(available and not playing)
        self.pause_button.setEnabled(available and playing)
        self.stop_button.setEnabled(available)

    def _copy_report(self) -> None:
        QApplication.clipboard().setText(self._report)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self.player.stop()
        self.player.setSource(QUrl())
        super().closeEvent(event)


def _integer(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _time(seconds: float) -> str:
    value = max(0, round(seconds))
    return f"{value // 60:02d}:{value % 60:02d}"
