from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.audio_pipeline import (
    AudioGenerationOptions,
    AudioGroup,
    AudioPipeline,
    GenerationCancelled,
)
from app.core.audiobook_store import PROJECT_MANIFEST_NAME, AudiobookStore
from app.core.generation_progress import GenerationProgress
from app.core.text_processor import TextChunk
from app.utils.ffmpeg_utils import FFmpegCancelled, FFmpegError, FFmpegRunner
from tests.test_audio_pipeline import FakeTTSEngine


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.audiobook_store.app_data_root", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    store = AudiobookStore(tmp_path / "projects.sqlite3")
    book = store.create_audiobook(
        "One. Two. Three.",
        {"engine": "probe"},
        tmp_path / "exports",
        "safe_chunks",
        "single",
        project_dir=tmp_path / "Book ñ",
    )
    groups = [
        AudioGroup(
            "Chapter 1",
            (
                TextChunk(
                    "One.", True, markup_pause_before_ms=50, markup_pause_after_ms=200
                ),
                TextChunk("Two.", True, markup_pause_after_ms=200),
            ),
        ),
        AudioGroup("Chapter 2", (TextChunk("Three.", True, markup_pause_after_ms=0),)),
    ]
    mapping = store.replace_segments(book, groups)
    segments = store.list_segments(book.id)
    for segment in segments:
        FakeTTSEngine().synthesize_to_wav(
            segment.source_text, Path(segment.wav_path), {}
        )
    return store, book, groups, mapping, segments


def read_manifest(book):
    return json.loads(
        (book.project_dir / PROJECT_MANIFEST_NAME).read_text(encoding="utf-8")
    )


def test_bulk_updates_are_durable_before_snapshot_and_flush_once(project):
    store, book, _, _, segments = project
    old = read_manifest(book)
    with patch.object(
        store, "_write_text_atomic", wraps=store._write_text_atomic
    ) as writes:
        with store.defer_project_manifests(book.id):
            for segment in segments:
                store.mark_segment_rendered(segment.id, Path(segment.wav_path), 80, 1)
            assert read_manifest(book) == old
            other_store = AudiobookStore(store.db_path)
            assert all(
                s.status == "rendered" for s in other_store.list_segments(book.id)
            )
            assert writes.call_count == 0
        manifests = [c for c in writes.call_args_list if c.args[0].suffix == ".json"]
        assert len(manifests) == 2
    assert all(s["status"] == "rendered" for s in read_manifest(book)["segments"])


def test_nested_deferral_preserves_other_projects_and_skips_clean_flush(project):
    store, book, _, _, segments = project
    with patch.object(
        store, "_write_text_atomic", wraps=store._write_text_atomic
    ) as writes:
        with store.defer_project_manifests():
            with store.defer_project_manifests(book.id):
                store.update_segment_pause(segments[0].id, 1, 2)
            assert writes.call_count == 0
            store.flush_project_manifest(book.id)
            calls = writes.call_count
        store.flush_project_manifest(book.id)
        assert writes.call_count == calls == 2


def test_clone_does_not_copy_the_source_publish_lock_or_foreign_manifests(
    project, tmp_path
):
    from app.core.project_manifest_lock import project_manifest_lock

    store, book, _, _, _ = project
    with project_manifest_lock(book.project_dir):
        clone = store.clone_audiobook(
            book.id,
            "Clone",
            book.source_text,
            {"engine": "probe"},
            tmp_path / "clone-exports",
            "safe_chunks",
            "single",
            target_project_dir=tmp_path / "clone",
        )
    assert clone.uuid != book.uuid
    assert read_manifest(clone)["uuid"] == clone.uuid
    assert len(read_manifest(clone)["segments"]) == 3
    assert not list(clone.project_dir.glob("*.external-*.json"))


def test_interruption_flush_preserves_original_error_if_disk_fails(project):
    store, book, _, _, segments = project
    with (
        pytest.raises(RuntimeError, match="original failure"),
        patch.object(store, "_write_text_atomic", side_effect=OSError("disk failure")),
        store.defer_project_manifests(book.id),
    ):
        store.update_segment_pause(segments[0].id, 123, 456)
        raise RuntimeError("original failure")
    recovered = AudiobookStore(store.db_path)
    recovered.open_project_manifest(book.project_dir / PROJECT_MANIFEST_NAME)
    assert read_manifest(book)["segments"][0]["resolved_pause_after_ms"] == 456


def test_abrupt_process_exit_recovers_sqlite_instead_of_importing_stale_json(project):
    store, book, _, _, segments = project
    code = """
import os, sys
from pathlib import Path
from app.core.audiobook_store import AudiobookStore
s = AudiobookStore(Path(sys.argv[1]))
with s.defer_project_manifests(int(sys.argv[2])):
    s.update_segment_pause(int(sys.argv[3]), 123, 789)
    os._exit(17)
"""
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            code,
            str(store.db_path),
            str(book.id),
            str(segments[0].id),
        ],
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert child.returncode == 17, child.stderr.decode(errors="replace")
    assert read_manifest(book)["segments"][0]["resolved_pause_after_ms"] is None
    recovered = AudiobookStore(store.db_path)
    recovered.open_project_manifest(book.project_dir / PROJECT_MANIFEST_NAME)
    assert recovered.get_segment(segments[0].id).resolved_pause_after_ms == 789
    assert read_manifest(book)["segments"][0]["resolved_pause_after_ms"] == 789


def test_external_changes_conflict_with_pending_local_progress(project):
    store, book, _, _, segments = project
    path = book.project_dir / PROJECT_MANIFEST_NAME
    external = read_manifest(book)
    external["title"] = "External change"
    path.write_text(json.dumps(external), encoding="utf-8")
    with store._connect() as connection:
        connection.execute(
            "UPDATE audiobook_segments SET resolved_pause_after_ms=321 WHERE id=?",
            (segments[0].id,),
        )
    with pytest.raises(ValueError, match="newer progress"):
        store.open_project_manifest(path)
    assert read_manifest(book)["title"] == "External change"
    assert store.get_segment(segments[0].id).resolved_pause_after_ms == 321
    store.flush_project_manifest(book.id)
    backups = list(book.project_dir.glob("*.external-*.json"))
    assert len(backups) == 1
    assert (
        json.loads(backups[0].read_text(encoding="utf-8"))["title"] == "External change"
    )
    assert read_manifest(book)["segments"][0]["resolved_pause_after_ms"] == 321


def test_legacy_project_gets_recovery_ledger_before_deferred_changes(project):
    store, book, _, _, segments = project
    legacy = read_manifest(book)
    legacy.pop("persistence")
    (book.project_dir / PROJECT_MANIFEST_NAME).write_text(
        json.dumps(legacy), encoding="utf-8"
    )
    with store._connect() as connection:
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'manifest_%'"
        ).fetchall()
        for trigger in triggers:
            connection.execute(f'DROP TRIGGER "{trigger[0]}"')
        connection.execute("DROP TABLE project_manifest_state")
        connection.execute(
            "UPDATE schema_info SET value='5' WHERE key='schema_version'"
        )
    migrated = AudiobookStore(store.db_path)
    with migrated.defer_project_manifests(book.id):
        migrated.update_segment_pause(segments[0].id, 90, 180)
        recovered = AudiobookStore(store.db_path)
        recovered.open_project_manifest(book.project_dir / PROJECT_MANIFEST_NAME)
        assert recovered.get_segment(segments[0].id).resolved_pause_after_ms == 180


def test_locked_legacy_copy_reopens_newer_database_without_reverting(project):
    store, book, _, _, segments = project
    real_replace = os.replace

    def deny_legacy(source, destination):
        if Path(destination).name == "project.json":
            raise PermissionError(13, "Legacy reader holds file")
        return real_replace(source, destination)

    with (
        patch("app.core.audiobook_store.os.replace", side_effect=deny_legacy),
        patch("app.core.audiobook_store.time.sleep"),
    ):
        store.update_segment_pause(segments[0].id, 12, 34)
    store.open_project_manifest(book.project_dir / "project.json")
    assert store.get_segment(segments[0].id).resolved_pause_after_ms == 34


def test_crash_between_file_replace_and_ledger_commit_recovers(project):
    store, book, _, _, segments = project
    original = store._write_text_atomic

    def fail_after_canonical(path, text):
        original(path, text)
        if path.name == PROJECT_MANIFEST_NAME:
            raise OSError("crash after replacement")

    with (
        patch.object(store, "_write_text_atomic", side_effect=fail_after_canonical),
        pytest.raises(OSError),
    ):
        store.update_segment_pause(segments[0].id, 7, 8)
    recovered = AudiobookStore(store.db_path)
    recovered.open_project_manifest(book.project_dir / PROJECT_MANIFEST_NAME)
    assert read_manifest(book)["segments"][0]["resolved_pause_after_ms"] == 8
    with recovered._connect() as connection:
        state = connection.execute("SELECT * FROM project_manifest_state").fetchone()
    assert state["revision"] == state["published_revision"]
    assert not state["pending_sha256"]


def test_partial_snapshot_write_keeps_previous_manifest_and_removes_temp(project):
    store, book, _, _, segments = project
    previous = read_manifest(book)
    original = Path.write_text

    def partial_write(path, text, *args, **kwargs):
        if path.name.endswith(".tmp"):
            original(path, text[:20], *args, **kwargs)
            raise OSError("No space left")
        return original(path, text, *args, **kwargs)

    with patch.object(Path, "write_text", partial_write), pytest.raises(OSError):
        store.update_segment_pause(segments[0].id, 100, 200)
    assert read_manifest(book) == previous
    assert not list(book.project_dir.glob(".*.tmp"))
    store.open_project_manifest(book.project_dir / PROJECT_MANIFEST_NAME)
    assert read_manifest(book)["segments"][0]["resolved_pause_after_ms"] == 200


def test_two_process_publishers_cannot_overwrite_newer_snapshot(project):
    store, book, _, _, segments = project
    code = """
import sys
from pathlib import Path
from app.core.audiobook_store import AudiobookStore, PROJECT_MANIFEST_NAME
s = AudiobookStore(Path(sys.argv[1]))
original = s._write_text_atomic
def paused(path, text):
    if path.name == PROJECT_MANIFEST_NAME:
        print('snapshot ready', flush=True)
        sys.stdin.readline()
    original(path, text)
s._write_text_atomic = paused
s.flush_project_manifest(int(sys.argv[2]), force=True)
"""
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(store.db_path), str(book.id)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ready = pool.submit(child.stdout.readline)
            assert ready.result(timeout=20).strip() == "snapshot ready"
            with store._connect() as connection:
                connection.execute(
                    "UPDATE audiobook_segments SET resolved_pause_after_ms=999 WHERE id=?",
                    (segments[0].id,),
                )
            future = pool.submit(store.flush_project_manifest, book.id)
            child.stdin.write("continue\n")
            child.stdin.flush()
            future.result(timeout=20)
            assert child.wait(timeout=20) == 0, child.stderr.read()
        assert read_manifest(book)["segments"][0]["resolved_pause_after_ms"] == 999
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()


def test_pause_batch_is_atomic_and_does_not_clear_events_twice(project):
    store, book, _, _, segments = project
    with pytest.raises(ValueError):
        store.update_segment_pauses(book.id, [(segments[0].id, 10, 20), (-1, 30, 40)])
    assert store.get_segment(segments[0].id).resolved_pause_after_ms is None
    with patch.object(
        store,
        "_invalidate_audio_events_from_segment",
        wraps=store._invalidate_audio_events_from_segment,
    ) as invalidations:
        store.update_segment_pauses(book.id, [(s.id, 0, 200) for s in segments])
        assert invalidations.call_count == 1
        store.update_segment_pauses(book.id, [(s.id, 0, 200) for s in segments])
        assert invalidations.call_count == 1


def prepared_pipeline(project):
    store, book, groups, mapping, segments = project
    progress = []
    pipeline = AudioPipeline(
        FakeTTSEngine(), audiobook_store=store, stage_callback=progress.append
    )
    pipeline._active_audiobook = book
    pipeline._segment_ids = mapping
    rendered = [
        [Path(s.wav_path) for s in segments if s.chapter_index == index]
        for index in range(1, len(groups) + 1)
    ]
    return pipeline, rendered, progress


def test_timeline_reuses_silences_and_preserves_chapter_times(project, tmp_path):
    store, book, groups, _, _ = project
    pipeline, rendered, progress = prepared_pipeline(project)
    options = AudioGenerationOptions(tmp_path, {}, "ffmpeg")
    with store.defer_project_manifests(book.id):
        timeline, chapters, duration = pipeline._prepare_timeline(
            groups, rendered, options, tmp_path, random.Random(1)
        )
    assert chapters == [("Chapter 1", 0, 610), ("Chapter 2", 610, 690)]
    assert duration == 690
    assert len(timeline) == 6
    assert timeline[2] == timeline[4]  # Two 200-ms pauses reference one file.
    assert len(list(tmp_path.glob("pause_*.wav"))) == 2
    assert progress[-1]["current"] == progress[-1]["total"] == 3
    assert all(p["stage"] == "preparation" for p in progress)


def test_preparation_cancel_stops_before_processing_whole_book(project, tmp_path):
    store, _book, groups, _, segments = project
    pipeline, rendered, _ = prepared_pipeline(project)
    calls = 0
    original = pipeline._rendered_duration_ms

    def cancel_after_first(path):
        nonlocal calls
        calls += 1
        pipeline.cancel()
        return original(path)

    pipeline._rendered_duration_ms = cancel_after_first
    with pytest.raises(GenerationCancelled):
        pipeline._prepare_timeline(
            groups,
            rendered,
            AudioGenerationOptions(tmp_path, {}, "ffmpeg"),
            tmp_path,
            random.Random(1),
        )
    assert calls == 1
    assert all(
        store.get_segment(s.id).resolved_pause_after_ms is None for s in segments
    )


def test_large_silence_creation_can_be_cancelled(project, tmp_path):
    _, _, _, _, segments = project
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise GenerationCancelled("Stop creating silence")

    pipeline = AudioPipeline(FakeTTSEngine())
    pipeline._check_cancelled = cancel
    with pytest.raises(GenerationCancelled):
        pipeline._create_silence(
            Path(segments[0].wav_path), tmp_path / "silence.wav", 60000
        )
    assert calls == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_pipeline_interruption_keeps_the_last_committed_speech(
    project, tmp_path, ffmpeg, cancel
):
    from app.core.audio_pipeline import AudioPipelineError
    from app.tts.base import TTSEngineError

    store, book, _, _, _ = project

    class InterruptedEngine(FakeTTSEngine):
        def synthesize_to_wav(self, text, output_wav, voice_config):
            if self.synthesized_texts:
                raise TTSEngineError("Synthesis failed")
            result = super().synthesize_to_wav(text, output_wav, voice_config)
            if cancel:
                pipeline.cancel()
            return result

    pipeline = AudioPipeline(InterruptedEngine(), audiobook_store=store)
    pipeline._prepare_groups = lambda *_: [
        AudioGroup("Chapter", tuple(TextChunk(str(i), True) for i in range(8)))
    ]
    options = AudioGenerationOptions(
        tmp_path / "exports", {}, ffmpeg, project_audiobook_id=book.id
    )
    with pytest.raises(GenerationCancelled if cancel else AudioPipelineError):
        pipeline.generate("A book", options)
    manifest = read_manifest(book)
    assert manifest["segments"][0]["status"] == "rendered"
    assert manifest["segments"][1]["status"] == ("pending" if cancel else "failed")
    assert Path(store.list_segments(book.id)[0].wav_path).is_file()


def test_progress_uses_stage_clock_and_throttles_updates():
    now = [0.0]
    seen = []
    reporter = GenerationProgress(lambda *_: None, seen.append, clock=lambda: now[0])
    reporter.emit("synthesis", 0, 8000, "Speech")
    now[0] = 7200.0
    reporter.emit("synthesis", 8000, 8000, "Speech")
    reporter.emit("preparation", 0, 8000, "Pauses")
    assert seen[-1]["stage_eta_seconds"] is None
    assert seen[-1]["overall_eta_seconds"] is None
    for i in range(1, 1000):
        reporter.emit("preparation", i, 8000, "Pauses")
    assert len(seen) == 3
    now[0] += 2
    reporter.emit("preparation", 4000, 8000, "Pauses")
    assert seen[-1]["stage_eta_seconds"] == 2
    reporter.emit("finalizing", 0, 0, "Saving")
    assert seen[-1]["percent"] is None


def test_ui_displays_phase_progress_without_using_synthesis_eta():
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app.ui.main_window import MainWindow

    holder = SimpleNamespace(
        progress_bar=Mock(),
        status_label=Mock(),
        time_label=Mock(),
        generation_started_at=time.monotonic() - 7200,
        tr=lambda _key, fallback, **kwargs: fallback.format(**kwargs),
        _format_duration=MainWindow._format_duration,
    )
    holder._update_generation_time = lambda: MainWindow._update_generation_time(holder)
    MainWindow._on_stage_progress(
        holder,
        {
            "stage": "encoding",
            "current": 60000,
            "total": 120000,
            "unit": "milliseconds",
            "stage_eta_seconds": None,
        },
    )
    holder.progress_bar.setValue.assert_called_with(50)
    assert "01:00 / 02:00" in holder.status_label.setText.call_args.args[0]
    assert "Total remaining: unknown" in holder.time_label.setText.call_args.args[0]
    MainWindow._on_stage_progress(
        holder, {"stage": "finalizing", "current": 0, "total": 0}
    )
    holder.progress_bar.setRange.assert_called_with(0, 0)


@pytest.fixture
def ffmpeg():
    executable = Path(__file__).resolve().parents[1] / "ffmpeg" / "ffmpeg.exe"
    if not executable.exists():
        import shutil

        found = shutil.which("ffmpeg")
        if not found:
            pytest.skip("FFmpeg is unavailable")
        executable = Path(found)
    return executable


def test_ffmpeg_reports_media_time_and_drains_error_pipe(ffmpeg):
    runner = FFmpegRunner(ffmpeg)
    progress = []
    runner.progress_callback = progress.append
    runner.run(
        [
            "-hide_banner",
            "-loglevel",
            "verbose",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "2",
            "-f",
            "null",
            "-",
        ]
    )
    assert progress and progress[-1] >= 1900
    assert runner._process is None


def test_ffmpeg_progress_can_cancel_and_reports_errors(ffmpeg):
    runner = FFmpegRunner(ffmpeg)
    runner.progress_callback = lambda _: runner.cancel_current()
    with pytest.raises(FFmpegCancelled):
        runner.run(
            ["-re", "-f", "lavfi", "-i", "anullsrc", "-t", "60", "-f", "null", "-"]
        )
    assert runner._process is None
    runner = FFmpegRunner(ffmpeg)
    runner.progress_callback = lambda _: None
    with pytest.raises(FFmpegError, match="FFmpeg failed"):
        runner.run(["-not_a_valid_option"])


def test_real_export_finishes_snapshots_after_audio_and_reports_stages(
    project, tmp_path, ffmpeg
):
    store, book, _, _, _ = project
    stages = []
    options = AudioGenerationOptions(
        tmp_path / "exports",
        {},
        ffmpeg,
        audio_format="m4b",
        project_audiobook_id=book.id,
        pause_between_blocks_ms=200,
    )
    pipeline = AudioPipeline(
        FakeTTSEngine(), audiobook_store=store, stage_callback=stages.append
    )
    outputs = pipeline.generate(
        '{{chapter "Chapter 1"}} First. {{pause 200}} Second. '
        '{{chapter "Chapter 2"}} Third.',
        options,
    )
    assert outputs[0].is_file()
    from mutagen.mp4 import MP4

    chapters = list(MP4(outputs[0]).chapters)
    assert [chapter.title for chapter in chapters] == ["Chapter 1", "Chapter 2"]
    assert [chapter.start for chapter in chapters] == pytest.approx(
        [0, 1.26], abs=0.002
    )
    assert [
        s["stage"]
        for i, s in enumerate(stages)
        if not i or stages[i - 1]["stage"] != s["stage"]
    ] == ["synthesis", "preparation", "joining", "encoding", "finalizing"]
    assert all(s["overall_eta_seconds"] is None for s in stages)
    assert read_manifest(book)["clean_audio_path"]
    with store._connect() as connection:
        row = connection.execute(
            "SELECT revision, published_revision FROM project_manifest_state"
        ).fetchone()
    assert row[0] == row[1]


@pytest.mark.parametrize("count", [8, 80])
def test_full_pipeline_snapshot_count_is_bounded(project, tmp_path, ffmpeg, count):
    store, book, _, _, _ = project
    pipeline = AudioPipeline(FakeTTSEngine(), audiobook_store=store)
    chunks = tuple(
        TextChunk(f"Block {i}.", True, markup_pause_after_ms=10) for i in range(count)
    )
    pipeline._prepare_groups = lambda *_: [AudioGroup("Chapter", chunks)]
    options = AudioGenerationOptions(
        tmp_path / "exports", {}, ffmpeg, project_audiobook_id=book.id
    )
    with patch.object(
        store, "_write_text_atomic", wraps=store._write_text_atomic
    ) as writes:
        pipeline.generate("Synthetic book", options)
    canonical = [
        call
        for call in writes.call_args_list
        if call.args[0].name == PROJECT_MANIFEST_NAME
    ]
    assert (
        len(canonical) == 7
    )  # Initial, four speech checkpoints, timeline, completed project.
    assert len(read_manifest(book)["segments"]) == count


def test_host_and_worker_transport_structured_progress_and_terminal_state(
    project, tmp_path, ffmpeg
):
    from types import SimpleNamespace

    from app.server.job_manager import LocalServerJobManager
    from app.workers.engine_host_generation_worker import EngineHostGenerationWorker

    store, book, _, _, _ = project
    snapshots = []

    class Service:
        def generate_audio(
            self, request, progress_callback=None, log_callback=None, on_pipeline=None
        ):
            pipeline = AudioPipeline(
                FakeTTSEngine(),
                progress_callback=progress_callback,
                log_callback=log_callback,
                audiobook_store=store,
            )
            on_pipeline(pipeline)
            original = pipeline.stage_callback

            def capture(details):
                original(details)
                snapshots.append(manager.get_job(job.job_id).to_dict())

            pipeline.stage_callback = capture
            options = AudioGenerationOptions(
                tmp_path / "exports", {}, ffmpeg, project_audiobook_id=book.id
            )
            outputs = pipeline.generate("First. {{pause 200}} Second.", options)
            return {
                "audiobook_id": book.id,
                "outputs": [str(p) for p in outputs],
                "clean_audio": str(outputs[0]),
            }

    manager = LocalServerJobManager(
        service=Service(), db_path=tmp_path / "jobs.sqlite3"
    )
    try:
        with patch.object(manager, "_ensure_worker"):
            job = manager.submit({"text": "A book"})
        manager._run_job(job.job_id)
        final = manager.get_job(job.job_id).to_dict()
        assert final["status"] == "complete", final
        assert final["progress"]["percent"] == 100
        assert final["progress"]["stage"] == "complete"
        assert final["progress"]["overall_eta_seconds"] == 0
        assert any(s["progress"]["stage"] == "preparation" for s in snapshots)
        assert all(s["progress"]["overall_eta_seconds"] is None for s in snapshots)
        responses = iter([*snapshots, final])
        client = SimpleNamespace(
            submit_job=lambda _: {"job_id": job.job_id},
            get_job=lambda _: next(responses),
        )
        worker = EngineHostGenerationWorker(client, {})
        detailed, legacy, completed = [], [], []
        worker.stage_progress.connect(detailed.append)
        worker.progress.connect(
            lambda current, total, message: legacy.append((current, total, message))
        )
        worker.finished.connect(completed.append)
        with patch("app.workers.engine_host_generation_worker.time.sleep"):
            worker.run()
        assert len(detailed) == len(legacy) == len(snapshots) + 1
        assert detailed[-1]["stage"] == "complete"
        assert completed[0]["audiobook_id"] == book.id
    finally:
        manager.shutdown()
