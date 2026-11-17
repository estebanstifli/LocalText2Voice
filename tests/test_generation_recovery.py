from __future__ import annotations

import json
import os
import shutil
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.audio_pipeline import (
    AudioGenerationOptions,
    AudioGroup,
    AudioPipeline,
    AudioPipelineError,
    GenerationCancelled,
)
from app.core.audiobook_store import PROJECT_MANIFEST_NAME, AudiobookStore
from app.core.text_processor import TextChunk
from app.tts.base import BaseTTSEngine, TTSCancelled
from app.workers.verification_worker import AudiobookRebuildWorker


class IntermittentEngine(BaseTTSEngine):
    def __init__(self):
        self.calls = []
        self.fail_text = "second"
        self.cancel_text = None

    def validate(self, config):
        pass

    def cancel_current(self):
        pass

    def synthesize_to_wav(self, text, output_wav, voice_config):
        self.calls.append(text)
        if text == self.cancel_text:
            raise TTSCancelled("Cancelled")
        with wave.open(str(output_wav), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"" if text == self.fail_text else b"\x01\x00" * 160)
        return output_wav


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.audiobook_store.app_data_root", lambda: tmp_path)
    store = AudiobookStore(tmp_path / "db.sqlite3")
    book = store.create_audiobook(
        "first second", {"engine": "omnivoice"}, tmp_path / "exports",
        "safe_chunks", "single", "Recovery", {}, tmp_path / "project",
    )
    options = AudioGenerationOptions(
        tmp_path / "exports", {"engine": "omnivoice", "speed": 1.0}, "unused",
        project_audiobook_id=book.id,
    )
    engine = IntermittentEngine()
    groups = [AudioGroup("Chapter", (TextChunk("first", True), TextChunk("second", True)))]
    exports = Mock(return_value=SimpleNamespace(audio_path=tmp_path / "exports" / "book.mp3"))
    monkeypatch.setattr("app.core.audio_pipeline.find_ffmpeg", lambda _: "unused")
    monkeypatch.setattr("app.core.audio_pipeline.export_audiobook_subtitles", lambda *a: SimpleNamespace(files=[]))

    def run(opts=None):
        # Reopen the database and pipeline to exercise recovery after restarting the app.
        pipeline = AudioPipeline(engine, audiobook_store=AudiobookStore(store.db_path))
        monkeypatch.setattr(pipeline, "_prepare_groups", lambda *a: groups)
        monkeypatch.setattr(pipeline, "_export_single", exports)
        pipeline.generate("first second", opts or options)
        return pipeline

    return SimpleNamespace(**locals())


def test_empty_audio_failure_resumes_without_resynthesizing_completed_segments(job):
    with pytest.raises(AudioPipelineError, match="3 attempts"):
        job.run()
    assert job.engine.calls == ["first", "second", "second", "second"]
    job.exports.assert_not_called()
    first, second = job.store.list_segments(job.book.id)
    assert (first.status, second.status) == ("rendered", "failed")
    original = Path(first.wav_path).read_bytes()
    assert first.render_signature
    manifest = json.loads((job.book.project_dir / PROJECT_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["segments"][0]["render_signature"] == first.render_signature

    job.engine.fail_text = None
    job.run()
    assert job.engine.calls == ["first", "second", "second", "second", "second"]
    current = job.store.list_segments(job.book.id)
    assert [s.id for s in current] == [first.id, second.id]
    assert Path(current[0].wav_path).read_bytes() == original
    job.exports.assert_called_once()
    job.run()
    assert len(job.engine.calls) == 5  # A completed, unchanged project only re-exports.


def test_regenerate_all_failure_keeps_usable_previous_audio_and_metadata(job):
    job.engine.fail_text = None
    job.run()
    previous = job.store.list_segments(job.book.id)[0]
    original = Path(previous.wav_path).read_bytes()
    job.engine.calls.clear()
    job.exports.reset_mock()
    job.engine.fail_text = "first"
    with pytest.raises(AudioPipelineError):
        job.run(replace(job.options, generation_mode="regenerate"))
    current = job.store.get_segment(previous.id)
    assert current.status == previous.status == "rendered"
    assert current.engine_config_json == previous.engine_config_json
    assert current.render_signature == previous.render_signature
    assert current.attempt_count == previous.attempt_count
    assert Path(current.wav_path).read_bytes() == original
    assert "3 attempts" in current.error_message
    assert job.engine.calls == ["first"] * 3
    job.exports.assert_not_called()
    job.run()  # Resume still recognizes the original successful recording.
    assert job.engine.calls == ["first"] * 3


@pytest.mark.parametrize("change", ["voice", "speed", "postprocess", "reference", "text", "corrupt", "missing"])
def test_changed_or_invalid_segments_are_not_reused(job, change):
    reference = job.tmp_path / "reference.wav"
    reference.write_bytes(b"reference one")
    config = {**job.options.voice_config, "reference_audio_path": str(reference)}
    options = replace(job.options, voice_config=config)
    job.engine.fail_text = None
    job.run(options)
    previous = job.store.list_segments(job.book.id)
    job.engine.calls.clear()
    if change == "voice":
        options = replace(options, voice_config={**config, "voice": "another"})
    elif change == "speed":
        options = replace(options, voice_config={**config, "speed": 1.2})
    elif change == "postprocess":
        options = replace(options, voice_config={**config, "_postprocess_volume_db": -3})
        job.monkeypatch.setattr(AudioPipeline, "_postprocess_chunk_wav", lambda *a: None)
    elif change == "reference":
        reference.write_bytes(b"reference two, changed")
    elif change == "text":
        job.groups[:] = [AudioGroup("Chapter", (TextChunk("new first", True), TextChunk("second", True)))]
    elif change == "corrupt":
        Path(previous[0].wav_path).write_bytes(b"broken")
    elif change == "missing":
        Path(previous[0].wav_path).unlink()
    job.run(options)
    assert job.engine.calls == (["new first"] if change == "text" else
                                ["first"] if change in {"corrupt", "missing"} else ["first", "second"])
    if change == "text":
        current = job.store.list_segments(job.book.id)
        assert current[0].source_text == "new first"
        assert current[1].id == previous[1].id


def test_inserting_segment_reuses_shifted_audio_without_overwriting_it(job):
    job.engine.fail_text = None
    job.run()
    previous = job.store.list_segments(job.book.id)
    job.engine.calls.clear()
    job.groups[:] = [AudioGroup("Chapter", (TextChunk("inserted", True), *job.groups[0].chunks))]
    job.run()
    current = job.store.list_segments(job.book.id)
    assert job.engine.calls == ["inserted"]
    assert [s.id for s in current[1:]] == [s.id for s in previous]
    assert len({s.wav_path for s in current}) == 3


def test_failure_invalidates_later_stale_segments_and_blocks_review_export(job):
    job.engine.fail_text = None
    job.run()
    job.engine.fail_text = "first"
    changed = replace(job.options, voice_config={"engine": "omnivoice", "speed": 1.4})
    with pytest.raises(AudioPipelineError):
        job.run(changed)
    assert [s.status for s in job.store.list_segments(job.book.id)] == ["failed", "pending"]
    worker = AudiobookRebuildWorker(job.store, job.book.id, job.options.output_dir, "unused", changed)
    failures = []
    worker.failed.connect(failures.append)
    worker._encode_audio = Mock()
    worker.run()
    assert failures and "Pending/failed segments" in failures[0]
    worker._encode_audio.assert_not_called()


def test_export_only_settings_do_not_require_synthesis(job):
    job.engine.fail_text = None
    job.run()
    job.engine.calls.clear()
    job.run(replace(job.options, audio_quality="high", pause_between_blocks_ms=700))
    assert job.engine.calls == []


def test_regenerate_all_replaces_every_segment(job):
    job.engine.fail_text = None
    job.run()
    job.engine.calls.clear()
    job.run(replace(job.options, generation_mode="regenerate"))
    assert job.engine.calls == ["first", "second"]
    assert all(s.attempt_count == 2 for s in job.store.list_segments(job.book.id))


def test_cancelled_generation_resumes_at_unfinished_segment(job):
    job.engine.fail_text = None
    job.engine.cancel_text = "second"
    with pytest.raises(GenerationCancelled):
        job.run()
    job.exports.assert_not_called()
    job.engine.cancel_text = None
    job.run()
    assert job.engine.calls == ["first", "second", "second"]


def test_legacy_recordings_acquire_signatures_and_keep_review_metadata(job):
    job.engine.fail_text = None
    job.run()
    first = job.store.list_segments(job.book.id)[0]
    job.store.update_segment_verification(first.id, "first", 1.0, 0, 0, "approved", 10)
    with job.store._connect() as connection:
        connection.execute("UPDATE audiobook_segments SET render_signature = ''")
    job.engine.calls.clear()
    job.run()
    assert job.engine.calls == []
    assert job.store.get_segment(first.id).verification_status == "approved"
    assert all(s.render_signature for s in job.store.list_segments(job.book.id))


def test_manifest_import_and_clone_preserve_recovery_signatures(job):
    job.engine.fail_text = None
    job.run()
    signatures = [s.render_signature for s in job.store.list_segments(job.book.id)]
    imported_store = AudiobookStore(job.tmp_path / "imported.sqlite3")
    imported = imported_store.import_project_manifest(job.book.project_dir / PROJECT_MANIFEST_NAME)
    assert [s.render_signature for s in imported_store.list_segments(imported.id)] == signatures
    clone = job.store.clone_audiobook(
        job.book.id, "Copy", "first second", job.options.voice_config, job.tmp_path / "copy-exports",
        "safe_chunks", "single", {}, job.tmp_path / "copy",
    )
    assert [s.render_signature for s in job.store.list_segments(clone.id)] == signatures


@pytest.mark.parametrize("mode", ["resume", "regenerate"])
def test_engine_host_receives_generation_choice(job, mode):
    from app.core.settings_manager import SettingsManager
    from app.server.ltv_service import LocalText2VoiceService

    service = LocalText2VoiceService(SettingsManager(job.tmp_path / "settings.json"))
    options = service._generation_options({"generation_mode": mode}, job.options.voice_config)
    assert options.generation_mode == mode


def test_cancel_dialog_returns_before_saving_or_starting_a_job():
    from app.ui.main_window import MainWindow

    window = SimpleNamespace(
        worker_thread=None, bulk_audiobook_thread=None,
        text_editor=SimpleNamespace(toPlainText=lambda: "A short story."),
        _current_voice_config=lambda: {"engine": "omnivoice", "language": "en"},
        current_audiobook_id=1, _choose_generation_mode=lambda: None,
        _save_settings=Mock(), engine_host_client=Mock(),
    )
    MainWindow._start_generation(window)
    window._save_settings.assert_not_called()
    window.engine_host_client.submit_job.assert_not_called()


@pytest.mark.parametrize("selection, expected", [(0, "resume"), (1, "regenerate"), (2, None), (None, None)])
def test_existing_audio_dialog_choices(job, monkeypatch, selection, expected):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

    from app.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    job.engine.fail_text = None
    job.run()
    parent = QWidget()
    parent.current_audiobook_id = job.book.id
    parent.audiobook_store = job.store
    parent.tr = lambda key, fallback: fallback
    selected = []

    def execute(dialog):
        buttons = dialog.buttons()
        # Identify by label, since Qt orders buttons according to the platform.
        labels = ["Continue generation", "Regenerate all", "Cancel"]
        selected.append(next((b for b in buttons if selection is not None and b.text() == labels[selection]), None))
        assert dialog.defaultButton().text() == labels[0]
        return 0

    monkeypatch.setattr(QMessageBox, "exec", execute)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda _: selected[0])
    assert MainWindow._choose_generation_mode(parent) == expected
    parent.close()
    assert application is not None


def test_new_project_does_not_prompt(job, monkeypatch):
    from app.ui.main_window import MainWindow, QMessageBox
    prompt = Mock()
    monkeypatch.setattr(QMessageBox, "exec", prompt)
    window = SimpleNamespace(current_audiobook_id=job.book.id, audiobook_store=job.store)
    assert MainWindow._choose_generation_mode(window) == "resume"
    prompt.assert_not_called()


def test_recovery_produces_real_m4b_only_after_all_segments_succeed(job, monkeypatch):
    from mutagen.mp4 import MP4
    from PySide6.QtWidgets import QApplication

    from app.core.subtitle_export import export_audiobook_subtitles

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr("app.core.audio_pipeline.find_ffmpeg", lambda _: ffmpeg)
    monkeypatch.setattr("app.core.audio_pipeline.export_audiobook_subtitles", export_audiobook_subtitles)
    options = replace(job.options, audio_format="m4b", ffmpeg_path=ffmpeg)

    def generate():
        pipeline = AudioPipeline(job.engine, audiobook_store=job.store)
        monkeypatch.setattr(pipeline, "_prepare_groups", lambda *a: job.groups)
        return pipeline.generate("first second", options)

    with pytest.raises(AudioPipelineError):
        generate()
    assert list(options.output_dir.glob("*.m4b")) == []
    job.engine.fail_text = None
    outputs = generate()
    assert job.engine.calls == ["first", "second", "second", "second", "second"]
    assert len(outputs) == 1 and outputs[0].suffix == ".m4b"
    assert MP4(outputs[0]).info.length > 0
    assert job.store.get_audiobook(job.book.id).clean_audio_path == str(outputs[0])
    assert application is not None
