"""Reproducible issue #27 persistence benchmark; no TTS or GPU required.

Run with the application's Python and an empty --root on a scratch drive.
Baseline sampling is explicitly extrapolated, not a measured complete export.
The --store-source option can point to a saved pre-change audiobook_store.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import random
import sys
import time
import wave
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.text_processor import TextChunk


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--store-source", type=Path)
    parser.add_argument("--sizes", type=int, nargs="+", default=[500, 2000, 8000])
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--full", action="store_true")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Measure the complete export preparation, stopping before FFmpeg starts",
    )
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    if args.store_source:
        spec = importlib.util.spec_from_file_location(
            "app.core.benchmark_store", args.store_source
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    else:
        from app.core import audiobook_store as module
    module.app_data_root = lambda: args.root / "app-data"
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 240)
    wav_bytes = buffer.getvalue()
    results = []
    for count in args.sizes:
        root = args.root / str(count)
        store = module.AudiobookStore(root / "projects.sqlite3")
        text = ("A reproducible narration segment with dialogue and pauses. " * 8)[:400]
        chunks = [
            TextChunk(text, True, 400, i + 1, markup_pause_after_ms=250)
            for i in range(count)
        ]
        groups = [
            SimpleNamespace(title=f"Chapter {i // 135 + 1}", chunks=chunks[i : i + 135])
            for i in range(0, count, 135)
        ]
        book = store.create_audiobook(
            "\n".join(c.text for c in chunks),
            {"engine": "probe"},
            root / "exports",
            "safe_chunks",
            "single",
            "Issue 27",
            project_dir=root / "project",
        )
        mapping = store.replace_segments(book, groups)
        segments = store.list_segments(book.id)
        for segment in segments:
            Path(segment.wav_path).write_bytes(wav_bytes)
        with store._connect() as connection:
            connection.execute(
                "UPDATE audiobook_segments SET status='rendered', duration_ms=10"
            )
        writes = 0
        written_bytes = 0
        original_write = store._write_text_atomic

        def counted_write(path, content, _writer=original_write):
            nonlocal writes, written_bytes
            if path.name in (
                module.PROJECT_MANIFEST_NAME,
                module.LEGACY_PROJECT_MANIFEST_NAME,
            ):
                writes += 1
                written_bytes += len(content.encode("utf-8"))
            return _writer(path, content)

        store._write_text_atomic = counted_write
        start = time.perf_counter()
        store._write_project_manifest(store.get_audiobook(book.id))
        snapshot_seconds = time.perf_counter() - start
        writes = written_bytes = 0
        updated = count if args.full else min(count, args.samples)
        start = time.perf_counter()
        if args.prepare_only:
            pass
        elif args.full and hasattr(store, "defer_project_manifests"):
            with store.defer_project_manifests(book.id):
                for i, segment in enumerate(segments, 1):
                    store.mark_segment_rendered(
                        segment.id, Path(segment.wav_path), 10, 1
                    )
                    if i % max(1, (count + 3) // 4) == 0:
                        store.flush_project_manifest(book.id)
        else:
            for segment in segments[:updated]:
                store.mark_segment_rendered(segment.id, Path(segment.wav_path), 10, 1)
        rendered_seconds = time.perf_counter() - start
        render_writes, render_bytes = writes, written_bytes
        writes = written_bytes = 0
        start = time.perf_counter()
        if args.prepare_only:
            from app.core import audio_pipeline as pipeline_module

            if args.store_source:
                source = args.store_source.with_name("audio_pipeline.py")
                spec = importlib.util.spec_from_file_location(
                    "app.core.benchmark_pipeline", source
                )
                pipeline_module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = pipeline_module
                spec.loader.exec_module(pipeline_module)
            pipeline = pipeline_module.AudioPipeline(None, audiobook_store=store)
            pipeline._active_audiobook = book
            pipeline._segment_ids = mapping
            rendered = [
                [Path(s.wav_path) for s in segments if s.chapter_index == i]
                for i in range(1, len(groups) + 1)
            ]
            options = pipeline_module.AudioGenerationOptions(
                root / "exports", {}, "ffmpeg"
            )
            temporary = root / "temporary-audio"
            temporary.mkdir()
            options.output_dir.mkdir(exist_ok=True)

            class PreparationComplete(Exception):
                pass

            class StopBeforeFFmpeg:
                progress_callback = None

                def run(self, arguments):
                    raise PreparationComplete

            scope = (
                store.defer_project_manifests(book.id)
                if hasattr(store, "defer_project_manifests")
                else nullcontext()
            )
            start = time.perf_counter()
            with scope:
                try:
                    pipeline._export_single(
                        groups,
                        rendered,
                        options,
                        temporary,
                        StopBeforeFFmpeg(),
                        random.Random(27),
                    )
                except PreparationComplete:
                    pass
                else:
                    raise AssertionError("Expected to stop immediately before FFmpeg")
        elif args.full and hasattr(store, "update_segment_pauses"):
            with store.defer_project_manifests(book.id):
                store.update_segment_pauses(book.id, [(s.id, 0, 250) for s in segments])
        else:
            for segment in segments[:updated]:
                store.update_segment_pause(segment.id, 0, 250)
        pause_seconds = time.perf_counter() - start
        row = {
            "segments": count,
            "updated": updated,
            "full_run": args.full,
            "snapshot_seconds": snapshot_seconds,
            "render_seconds": rendered_seconds,
            "render_manifest_writes": render_writes,
            "render_manifest_bytes": render_bytes,
            "pause_seconds": pause_seconds,
            "pause_manifest_writes": writes,
            "pause_manifest_bytes": written_bytes,
        }
        if args.prepare_only:
            row["scope"] = "complete_export_preparation_before_ffmpeg"
            row["unique_silence_files"] = len(list(temporary.glob("pause_*.wav")))
            row["updated"] = count
            row["full_run"] = True
        if not args.full and not args.prepare_only:
            row.update(
                extrapolated_render_seconds=rendered_seconds * count / updated,
                extrapolated_pause_seconds=pause_seconds * count / updated,
            )
        results.append(row)
        (args.root / "results.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
