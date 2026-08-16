from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.audiobook_store import AudiobookStore
from app.core.audio_pipeline import AudioGroup
from app.core.bulk_audiobook_store import BulkAudiobookStore
from app.core.text_processor import TextChunk
from app.workers.bulk_audiobook_worker import (
    BulkAudiobookWorker,
    bulk_generation_request,
)


class FakeEngineHostClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def submit_job(self, payload: dict[str, object]) -> dict[str, object]:
        self.requests.append(payload)
        return {"job_id": f"job-{len(self.requests)}"}

    def get_job(self, job_id: str) -> dict[str, object]:
        index = int(job_id.rsplit("-", 1)[-1])
        return {
            "status": "complete",
            "audiobook_id": index,
            "result": {
                "clean_mp3": f"book-{index}.mp3",
                "mix_mp3": "",
            },
            "progress": {"current": 1, "total": 1, "message": "Done"},
            "logs": [],
        }

    def cancel_job(self, _job_id: str) -> dict[str, object]:
        return {"status": "cancelled"}


class OneFailureEngineHostClient(FakeEngineHostClient):
    def get_job(self, job_id: str) -> dict[str, object]:
        if job_id == "job-1":
            return {
                "status": "failed",
                "error_message": "Synthetic engine failure",
                "progress": {"current": 0, "total": 1},
                "logs": [],
            }
        return super().get_job(job_id)


class StopFailureEngineHostClient(FakeEngineHostClient):
    worker: BulkAudiobookWorker | None = None

    def get_job(self, _job_id: str) -> dict[str, object]:
        if self.worker is not None:
            self.worker.request_stop()
        raise RuntimeError("Engine host stopped during application shutdown")


class BulkAudiobookTests(unittest.TestCase):
    def test_store_recovers_interrupted_batch_as_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            db_path = root / "bulk.sqlite3"
            store = BulkAudiobookStore(db_path)
            batch = store.create_batch(
                "Collection",
                {"voice_config": {"engine": "piper"}},
                [
                    {"source_path": "one.txt", "title": "One"},
                    {"source_path": "two.txt", "title": "Two"},
                ],
            )
            first = store.list_items(batch.id)[0]
            store.set_batch_status(batch.id, "running")
            store.update_item(first.id, status="running", engine_job_id="job-1")

            recovered = BulkAudiobookStore(db_path)

            recovered_batch = recovered.get_batch(batch.id)
            self.assertIsNotNone(recovered_batch)
            self.assertEqual(recovered_batch.status, "paused")  # type: ignore[union-attr]
            recovered_first = recovered.list_items(batch.id)[0]
            self.assertEqual(recovered_first.status, "queued")
            self.assertEqual(recovered_first.engine_job_id, "")
            self.assertIn("resume", recovered_first.message.casefold())

    def test_request_uses_frozen_profile_and_existing_editable_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audiobook_store = AudiobookStore(root / "projects.sqlite3")
            project = audiobook_store.create_audiobook(
                "Frozen source text",
                {"engine": "qwen", "model": "base_1_7b"},
                root / "Book" / "exports",
                "safe_chunks",
                "single",
                "Book",
                {},
                root / "Book",
            )
            bulk_store = BulkAudiobookStore(root / "bulk.sqlite3")
            batch = bulk_store.create_batch(
                "Collection",
                {
                    "voice_config": {
                        "engine": "qwen",
                        "model": "base_1_7b",
                        "speaker": "",
                        "speed": 1.1,
                    },
                    "generation_settings": {"music_volume_db": -11.0},
                    "chunk_size": 520,
                },
                [
                    {
                        "source_path": "book.txt",
                        "source_sha256": "abc",
                        "title": "Book",
                        "music_path": str(root / "music.mp3"),
                        "audiobook_id": project.id,
                    }
                ],
            )
            item = bulk_store.list_items(batch.id)[0]

            request = bulk_generation_request(batch, item, audiobook_store)

            self.assertEqual(request["text"], "Frozen source text")
            self.assertEqual(request["project_audiobook_id"], project.id)
            self.assertEqual(request["engine_id"], "qwen")
            self.assertEqual(request["voice_config"]["model"], "base_1_7b")  # type: ignore[index]
            self.assertEqual(request["generation_settings"]["music_volume_db"], -11.0)  # type: ignore[index]
            self.assertEqual(request["mix_policy"], "always")

    def test_request_preserves_the_selected_creation_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audiobook_store = AudiobookStore(root / "projects.sqlite3")
            project = audiobook_store.create_audiobook(
                "Review me",
                {"engine": "piper", "voice": "test"},
                root / "Book" / "exports",
                "safe_chunks",
                "single",
                "Book",
                {},
                root / "Book",
            )
            bulk_store = BulkAudiobookStore(root / "bulk.sqlite3")
            batch = bulk_store.create_batch(
                "Collection",
                {
                    "voice_config": {"engine": "piper", "voice": "test"},
                    "review_policy": "on",
                    "generation_settings": {
                        "review": {"enabled": True, "max_retries": 2}
                    },
                },
                [
                    {
                        "source_path": "book.txt",
                        "title": "Book",
                        "audiobook_id": project.id,
                    }
                ],
            )

            request = bulk_generation_request(
                batch,
                bulk_store.list_items(batch.id)[0],
                audiobook_store,
            )

            self.assertEqual(request["review_policy"], "on")
            self.assertEqual(
                request["generation_settings"]["review"]["max_retries"],  # type: ignore[index]
                2,
            )

    def test_worker_records_segment_review_metrics_in_monitor_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audiobook_store = AudiobookStore(root / "projects.sqlite3")
            project = audiobook_store.create_audiobook(
                "First. Second.",
                {"engine": "piper", "voice": "test"},
                root / "Book" / "exports",
                "safe_chunks",
                "single",
                "Book",
                {},
                root / "Book",
            )
            audiobook_store.replace_segments(
                project,
                [
                    AudioGroup(
                        "Book",
                        (
                            TextChunk("First.", True),
                            TextChunk("Second.", True),
                        ),
                    )
                ],
            )
            segments = audiobook_store.list_segments(project.id)
            for segment in segments:
                wav = root / f"segment-{segment.id}.wav"
                wav.write_bytes(b"RIFF")
                audiobook_store.mark_segment_rendered(segment.id, wav, 1000, 10)
            audiobook_store.update_segment_verification(
                segments[0].id,
                "First.",
                99.0,
                0.0,
                0.0,
                "approved",
                10,
            )
            audiobook_store.update_segment_verification(
                segments[1].id,
                "Different.",
                40.0,
                1.0,
                1.0,
                "retry_needed",
                10,
            )
            bulk_store = BulkAudiobookStore(root / "bulk.sqlite3")
            batch = bulk_store.create_batch(
                "Collection",
                {"voice_config": {"engine": "piper", "voice": "test"}},
                [
                    {
                        "source_path": "book.txt",
                        "title": "Book",
                        "audiobook_id": project.id,
                    }
                ],
            )
            worker = BulkAudiobookWorker(
                bulk_store,
                audiobook_store,
                FakeEngineHostClient(),  # type: ignore[arg-type]
                batch.id,
            )

            worker.run()

            item = bulk_store.list_items(batch.id)[0]
            self.assertEqual(item.metrics()["segments"], 2)
            self.assertEqual(item.metrics()["reviewed"], 2)
            self.assertEqual(item.metrics()["approved"], 1)
            self.assertEqual(item.metrics()["needs_attention"], 1)
            self.assertIn("2 reviewed", item.message)
            self.assertIn("1 need attention", item.message)

    def test_worker_continues_sequentially_and_completes_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audiobook_store = AudiobookStore(root / "projects.sqlite3")
            item_rows = []
            for index in range(2):
                project = audiobook_store.create_audiobook(
                    f"Text {index}",
                    {"engine": "piper", "voice": "test"},
                    root / f"Book {index}" / "exports",
                    "safe_chunks",
                    "single",
                    f"Book {index}",
                    {},
                    root / f"Book {index}",
                )
                item_rows.append(
                    {
                        "source_path": f"book-{index}.txt",
                        "title": f"Book {index}",
                        "audiobook_id": project.id,
                    }
                )
            bulk_store = BulkAudiobookStore(root / "bulk.sqlite3")
            batch = bulk_store.create_batch(
                "Collection",
                {"voice_config": {"engine": "piper", "voice": "test"}},
                item_rows,
            )
            client = FakeEngineHostClient()
            worker = BulkAudiobookWorker(
                bulk_store,
                audiobook_store,
                client,  # type: ignore[arg-type]
                batch.id,
            )
            finished: list[tuple[int, str]] = []
            worker.finished.connect(
                lambda batch_id, status: finished.append((batch_id, status))
            )

            worker.run()

            self.assertEqual(len(client.requests), 2)
            self.assertEqual(
                [request["title"] for request in client.requests],
                ["Book 0", "Book 1"],
            )
            self.assertTrue(
                all(item.status == "complete" for item in bulk_store.list_items(batch.id))
            )
            completed_batch = bulk_store.get_batch(batch.id)
            self.assertIsNotNone(completed_batch)
            self.assertEqual(completed_batch.status, "complete")  # type: ignore[union-attr]
            self.assertEqual(finished, [(batch.id, "complete")])

    def test_worker_continues_after_failure_and_batch_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audiobook_store = AudiobookStore(root / "projects.sqlite3")
            rows = []
            for index in range(2):
                project = audiobook_store.create_audiobook(
                    f"Text {index}",
                    {"engine": "piper", "voice": "test"},
                    root / f"Book {index}" / "exports",
                    "safe_chunks",
                    "single",
                    f"Book {index}",
                    {},
                    root / f"Book {index}",
                )
                rows.append(
                    {
                        "source_path": f"book-{index}.txt",
                        "title": f"Book {index}",
                        "audiobook_id": project.id,
                    }
                )
            bulk_store = BulkAudiobookStore(root / "bulk.sqlite3")
            batch = bulk_store.create_batch(
                "Collection",
                {"voice_config": {"engine": "piper", "voice": "test"}},
                rows,
            )

            worker = BulkAudiobookWorker(
                bulk_store,
                audiobook_store,
                OneFailureEngineHostClient(),  # type: ignore[arg-type]
                batch.id,
            )
            worker.run()

            self.assertEqual(
                [item.status for item in bulk_store.list_items(batch.id)],
                ["failed", "complete"],
            )
            completed_batch = bulk_store.get_batch(batch.id)
            self.assertIsNotNone(completed_batch)
            self.assertEqual(completed_batch.status, "complete_with_errors")  # type: ignore[union-attr]
            self.assertTrue(completed_batch.finished_at)  # type: ignore[union-attr]
            self.assertEqual(bulk_store.retry_failed(batch.id), 1)
            self.assertEqual(
                [item.status for item in bulk_store.list_items(batch.id)],
                ["queued", "complete"],
            )

    def test_shutdown_failure_keeps_the_current_item_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            audiobook_store = AudiobookStore(root / "projects.sqlite3")
            project = audiobook_store.create_audiobook(
                "Text",
                {"engine": "piper", "voice": "test"},
                root / "Book" / "exports",
                "safe_chunks",
                "single",
                "Book",
                {},
                root / "Book",
            )
            bulk_store = BulkAudiobookStore(root / "bulk.sqlite3")
            batch = bulk_store.create_batch(
                "Collection",
                {"voice_config": {"engine": "piper", "voice": "test"}},
                [
                    {
                        "source_path": "book.txt",
                        "title": "Book",
                        "audiobook_id": project.id,
                    }
                ],
            )
            client = StopFailureEngineHostClient()
            worker = BulkAudiobookWorker(
                bulk_store,
                audiobook_store,
                client,  # type: ignore[arg-type]
                batch.id,
            )
            client.worker = worker

            worker.run()

            stopped_batch = bulk_store.get_batch(batch.id)
            self.assertIsNotNone(stopped_batch)
            self.assertEqual(stopped_batch.status, "paused")  # type: ignore[union-attr]
            item = bulk_store.list_items(batch.id)[0]
            self.assertEqual(item.status, "queued")
            self.assertEqual(item.engine_job_id, "")
            self.assertIn("resume", item.message.casefold())


if __name__ == "__main__":
    unittest.main()
