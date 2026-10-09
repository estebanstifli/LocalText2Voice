from __future__ import annotations

import os
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from app.ui.audio_mix_preview_panel import AudioMixPreviewPanel
from app.ui.audio_tail_cut_dialog import AudioTailCutDialog
from app.ui.main_window import MainWindow


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def wav_path(tmp_path):
    path = tmp_path / "segment.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 160)
    return path


def test_release_clears_nested_and_stopped_players(application, wav_path):
    window = QWidget()
    nested = QWidget(window)
    players = [QMediaPlayer(window), QMediaPlayer(nested)]
    try:
        for player in players:
            player.setSource(QUrl.fromLocalFile(str(wav_path)))
            player.stop()
            assert not player.source().isEmpty()
        MainWindow._release_audio_previews(window)
        assert all(player.source().isEmpty() for player in players)
    finally:
        window.deleteLater()


@pytest.mark.parametrize("job", ["worker_thread", "bulk_audiobook_thread", "segment_regeneration_thread", "audio_tail_cut_thread", "audiobook_rebuild_thread"])
def test_review_cannot_reopen_audio_while_job_writes(application, wav_path, job):
    holder = SimpleNamespace(review_player=Mock(), **{job: object()})
    holder._project_audio_write_in_progress = lambda: MainWindow._project_audio_write_in_progress(holder)
    MainWindow._play_review_audio(holder, str(wav_path))
    holder.review_player.setSource.assert_not_called()
    setattr(holder, job, None)
    MainWindow._play_review_audio(holder, str(wav_path))
    holder.review_player.play.assert_called_once()


def test_review_stop_unloads_source(application, wav_path):
    player = QMediaPlayer()
    try:
        player.setSource(QUrl.fromLocalFile(str(wav_path)))
        MainWindow._stop_review_audio(SimpleNamespace(review_player=player))
        assert player.source().isEmpty()
    finally:
        player.deleteLater()


def test_mix_cannot_restart_from_a_delayed_callback(application, wav_path):
    panel = AudioMixPreviewPanel(lambda _key, default, **values: default.format(**values))
    callbacks = []
    try:
        panel.preview_render_path = wav_path
        panel.total_duration_seconds = 1.0
        with patch("app.ui.audio_mix_preview_panel.QTimer.singleShot", side_effect=lambda _delay, callback: callbacks.append(callback)):
            panel._play_cached_preview(0)
        assert callbacks
        panel.set_playback_blocked(True)
        for callback in callbacks:
            callback()
        panel._play_cached_preview(0)
        panel._play_advanced_cached(0)
        panel._start_pending_player_playback()
        panel._set_playback_controls_enabled(True)
        panel._set_advanced_playback_controls_enabled(True)
        assert panel.preview_player.source().isEmpty()
        assert not panel.play_cursor_button.isEnabled()
        assert not panel.advanced_play_button.isEnabled()
        panel.set_playback_blocked(False)
        panel._set_playback_controls_enabled(True)
        assert panel.play_cursor_button.isEnabled()
    finally:
        panel.preview_player.setSource(QUrl())
        panel.deleteLater()


def test_tail_dialog_unloads_audio_on_accept(application, wav_path):
    # Exercise dialog lifecycle without needing FFmpeg waveform generation.
    dialog = AudioTailCutDialog.__new__(AudioTailCutDialog)
    QDialog.__init__(dialog)
    dialog.player = QMediaPlayer(dialog)
    try:
        dialog.player.setSource(QUrl.fromLocalFile(str(wav_path)))
        dialog.done(QDialog.DialogCode.Accepted)
        assert dialog.player.source().isEmpty()
    finally:
        dialog.deleteLater()
