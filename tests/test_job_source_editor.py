from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.audiobook_store import AudiobookStore, PROJECT_MANIFEST_NAME
from app.server.job_manager import ServerJob
from app.server.job_source_editor import JobSourceEditor, MAX_READ_ALL_CHARS


class StaticJobManager:
    def __init__(self, job: ServerJob) -> None:
        self.job = job

    def get_job(self, job_id: str) -> ServerJob | None:
        return self.job if job_id == self.job.job_id else None


def _editor(tmp_path: Path, text: str) -> tuple[JobSourceEditor, AudiobookStore, int]:
    store = AudiobookStore(tmp_path / "projects.sqlite3")
    project = store.create_audiobook(
        text,
        {"engine": "piper"},
        tmp_path / "output",
        "safe_chunks",
        "single",
        "Editable project",
        project_dir=tmp_path / "project",
    )
    job = ServerJob(
        job_id="job-editable",
        status="complete",
        title=project.title,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:01:00Z",
        audiobook_id=project.id,
    )
    return JobSourceEditor(StaticJobManager(job), store), store, project.id


def test_reads_source_by_character_pages_and_searches_offsets(tmp_path):
    text = ("alpha " * 60) + "\nNeedle one\n" + ("beta " * 80) + "Needle two"
    editor, _store, _project_id = _editor(tmp_path, text)

    first = editor.read("job-editable", page=1, page_size_chars=256)
    second = editor.read(
        "job-editable",
        page=2,
        page_size_chars=256,
        page_count=2,
    )
    results = editor.search(
        "job-editable",
        "needle",
        case_sensitive=False,
        max_results=1,
    )

    assert first["content"] == text[:256]
    assert first["source_sha256"] == second["source_sha256"]
    assert second["start_offset"] == 256
    assert second["content"] == text[256:768]
    assert results["results"][0]["match"] == "Needle"
    assert results["results"][0]["line"] == 2
    assert results["has_more"] is True
    assert results["next_result_offset"] == 1


def test_source_edits_update_database_file_and_manifest(tmp_path):
    editor, store, project_id = _editor(tmp_path, "Uno dos dos tres.")
    original = editor.read("job-editable", read_all=True)

    replaced = editor.replace_text(
        "job-editable",
        "dos",
        "DOS",
        replace_all=True,
        expected_sha256=original["source_sha256"],
    )
    inserted = editor.edit(
        "job-editable",
        "insert",
        0,
        text="Inicio. ",
        expected_sha256=replaced["source_sha256"],
    )
    deleted = editor.edit(
        "job-editable",
        "delete",
        0,
        end_offset=len("Inicio. "),
        expected_sha256=inserted["source_sha256"],
    )

    assert replaced["replacements"] == 2
    assert replaced["render_required"] is True
    assert inserted["edit"]["inserted_chars"] == len("Inicio. ")
    assert deleted["edit"]["removed_chars"] == len("Inicio. ")
    assert store.get_audiobook(project_id).source_text == "Uno DOS DOS tres."
    source_path = tmp_path / "project" / "source.txt"
    assert source_path.read_text(encoding="utf-8") == "Uno DOS DOS tres."
    manifest = json.loads(
        (tmp_path / "project" / PROJECT_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert manifest["source_text"] == "Uno DOS DOS tres."
    assert manifest["source_hash"] == deleted["source_sha256"]


def test_rejects_stale_writes_and_oversized_read_all(tmp_path):
    editor, _store, _project_id = _editor(tmp_path, "Initial source")
    original = editor.read("job-editable", read_all=True)
    changed = editor.write(
        "job-editable",
        "Changed source",
        expected_sha256=original["source_sha256"],
    )

    with pytest.raises(ValueError, match="Source changed since it was read"):
        editor.write(
            "job-editable",
            "Stale overwrite",
            expected_sha256=original["source_sha256"],
        )

    large_text = "x" * (MAX_READ_ALL_CHARS + 1)
    editor.write(
        "job-editable",
        large_text,
        expected_sha256=changed["source_sha256"],
    )
    with pytest.raises(ValueError, match="Read it in pages instead"):
        editor.read("job-editable", read_all=True)
    page = editor.read("job-editable", page=2, page_size_chars=50_000)
    assert page["start_offset"] == 50_000
    assert len(page["content"]) == 50_000


def test_missing_or_running_job_has_no_editable_source(tmp_path):
    store = AudiobookStore(tmp_path / "projects.sqlite3")
    running_job = ServerJob(
        job_id="running-job",
        status="running",
        title="Pending",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:01Z",
    )
    editor = JobSourceEditor(StaticJobManager(running_job), store)

    with pytest.raises(ValueError, match="no editable project yet"):
        editor.read("running-job")
    with pytest.raises(ValueError, match="Job not found"):
        editor.read("missing-job")
