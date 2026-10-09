import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.core.audiobook_store import AudiobookStore, PROJECT_MANIFEST_NAME
from app.core import project_rename
from app.core.project_rename import relocate_paths, safe_project_folder_name


def make_project(root, *, database="projects.sqlite3"):
    store = AudiobookStore(root / database)
    old = root / "Project7"
    project = store.create_audiobook(
        "Unsullied source text", {"engine": "piper", "reference_audio": str(old / "voice.wav")},
        old / "exports", "safe_chunks", "single", "Old title",
        {"output_dir": str(old / "exports"), "external": str(root / "Project70" / "music.wav"),
         "server_request": {"output_dir": str(old / "exports")}}, old,
    )
    (old / "storyboard").mkdir()
    (old / "storyboard" / "storyboard.json").write_text(json.dumps({
        "image_path": str(old / "storyboard" / "image.png"), "relative": "image.png",
    }), encoding="utf-8")
    (old / "voice.wav").write_bytes(b"original audio")
    return store, project


def test_rename_updates_paths_without_recreating_project_or_losing_audio(tmp_path):
    store, book = make_project(tmp_path)
    old = book.project_dir
    with store._connect() as c:
        segment_id = c.execute("""INSERT INTO audiobook_segments
            (audiobook_id, sequence_index, chapter_index, chapter_title, paragraph_index, source_text, normalized_source_text, status, wav_path, created_at, updated_at)
            VALUES (?, 0, 0, '', 0, 'Untouched segment', 'untouched segment', 'rendered', ?, '', '')""", (book.id, str(old / "voice.wav"))).lastrowid
        c.execute("INSERT INTO segment_attempts (segment_id,attempt_number,wav_path,status,created_at) VALUES (?,1,?,'rendered','')",
                  (segment_id, str(old / "voice.wav")))
        c.execute("INSERT INTO audiobook_outputs VALUES (?,0,?)", (book.id, str(old / "exports" / "book.mp3")))
        c.execute("UPDATE audiobooks SET clean_audio_path=?, clean_mp3_path=? WHERE id=?",
                  (str(old / "voice.wav"), str(old / "voice.wav"), book.id))
    renamed = store.rename_audiobook(book.id, 'El principito: edición / 2')
    new = tmp_path / "El principito- edición - 2"
    assert renamed.project_dir == new
    assert renamed.id == book.id and renamed.uuid == book.uuid
    assert renamed.source_text == book.source_text
    assert not old.exists()
    assert (new / "voice.wav").read_bytes() == b"original audio"
    assert renamed.clean_audio_path == str(new / "voice.wav")
    settings = json.loads(renamed.project_settings_json)
    assert settings["server_request"]["output_dir"] == str(new / "exports")
    assert settings["external"] == str(tmp_path / "Project70" / "music.wav")
    assert json.loads(renamed.engine_config_json)["reference_audio"] == str(new / "voice.wav")
    assert store.get_segment(segment_id).wav_path == str(new / "voice.wav")
    with store._connect() as c:
        assert c.execute("SELECT wav_path FROM segment_attempts").fetchone()[0] == str(new / "voice.wav")
        assert c.execute("SELECT file_path FROM audiobook_outputs").fetchone()[0] == str(new / "exports" / "book.mp3")
    assert json.loads((new / "storyboard" / "storyboard.json").read_text(encoding="utf-8"))["image_path"] == str(new / "storyboard" / "image.png")
    assert json.loads((new / PROJECT_MANIFEST_NAME).read_text(encoding="utf-8"))["title"] == renamed.title
    assert not store.db_path.with_name(store.db_path.name + ".rename.json").exists()
    assert store.open_project_manifest(new / PROJECT_MANIFEST_NAME).id == book.id


@pytest.mark.parametrize("name,expected", [("CON.txt", "Audiobook - CON.txt"), (".. / : *", "Audiobook"),
                                         ("ＡＢＣ", "ABC"), ("a\x00b. ", "ab"), ("Español 日本語", "Español 日本語")])
def test_safe_names(name, expected):
    assert safe_project_folder_name(name) == expected


def test_collision_same_title_and_case_only_are_safe(tmp_path):
    store, book = make_project(tmp_path)
    (tmp_path / "Old title").mkdir()
    (tmp_path / "Old title" / "keep").write_text("keep")
    renamed = store.rename_audiobook(book.id, book.title)
    assert renamed.project_dir.name == "Old title (2)"
    assert store.rename_audiobook(book.id, book.title).project_dir == renamed.project_dir
    assert (tmp_path / "Old title" / "keep").read_text() == "keep"
    renamed = store.rename_audiobook(book.id, "OLD TITLE (2)")
    assert (renamed.project_dir / "voice.wav").exists()
    assert renamed.project_dir.name == "OLD TITLE (2)"
    assert "OLD TITLE (2)" in [p.name for p in tmp_path.iterdir()]


@pytest.mark.parametrize("failure", ["move", "metadata", "database"])
def test_errors_restore_folder_and_database(tmp_path, monkeypatch, failure):
    store, book = make_project(tmp_path)
    original_json = (book.project_dir / "storyboard" / "storyboard.json").read_bytes()
    original_atomic = project_rename._atomic_bytes
    if failure == "move":
        monkeypatch.setattr(Path, "rename", lambda *args: (_ for _ in ()).throw(PermissionError("locked")))
    elif failure == "metadata":
        def fail_once(path, content):
            if path.parent.name == "storyboard" and path.parent.parent.name == "New":
                raise OSError("disk full")
            original_atomic(path, content)
        monkeypatch.setattr(project_rename, "_atomic_bytes", fail_once)
    else:
        monkeypatch.setattr(project_rename, "_rewrite_table", lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError("write failed")))
    with pytest.raises((OSError, sqlite3.OperationalError)):
        store.rename_audiobook(book.id, "New")
    assert store.get_audiobook(book.id) == book
    assert not (tmp_path / "New").exists()
    assert (book.project_dir / "voice.wav").read_bytes() == b"original audio"
    assert (book.project_dir / "storyboard" / "storyboard.json").read_bytes() == original_json


@pytest.mark.parametrize("committed", [False, True])
def test_process_crash_recovery(tmp_path, committed):
    store, book = make_project(tmp_path)
    script = '''
import os, sys
from pathlib import Path
from app.core.audiobook_store import AudiobookStore
from app.core import project_rename
store = AudiobookStore(Path(sys.argv[1]))
if sys.argv[2] == 'True':
    store.flush_project_manifest = lambda *args, **kwargs: os._exit(73)
else:
    project_rename._rewrite_table = lambda *args, **kwargs: os._exit(73)
store.rename_audiobook(1, 'Recovered')
'''
    result = subprocess.run([sys.executable, "-c", script, str(store.db_path), str(committed)], timeout=30)
    assert result.returncode == 73
    restarted = AudiobookStore(store.db_path)
    actual = restarted.get_audiobook(book.id)
    expected = tmp_path / ("Recovered" if committed else "Project7")
    assert actual.project_dir == expected
    assert (expected / "voice.wav").read_bytes() == b"original audio"
    assert json.loads((expected / "storyboard" / "storyboard.json").read_text())["image_path"] == str(expected / "storyboard" / "image.png")
    assert json.loads((expected / PROJECT_MANIFEST_NAME).read_text(encoding="utf-8"))["title"] == actual.title


def test_path_remapping_respects_boundaries_and_relative_assets(tmp_path):
    old, new = tmp_path / "Project7", tmp_path / "New"
    values = [str(old / "a.wav"), str(tmp_path / "Project70" / "b.wav"), "assets/a.wav", "Text " + str(old)]
    assert relocate_paths(values, old, new) == [str(new / "a.wav"), *values[1:]]


def test_markup_paths_remapped_without_changing_narration(tmp_path):
    store, book = make_project(tmp_path)
    old = book.project_dir
    text = f'Keep {old} in prose. {{{{PLAY "{old.as_posix()}/voice.wav" volume=-3}}}} End.'
    store.save_audiobook_project(book.id, text, {}, book.output_dir, "safe_chunks", "single", book.title)
    renamed = store.rename_audiobook(book.id, "Markup")
    assert f"Keep {old} in prose." in renamed.source_text
    assert f'PLAY "{renamed.project_dir.as_posix()}/voice.wav"' in renamed.source_text
    assert (renamed.project_dir / "source.txt").read_text(encoding="utf-8") == renamed.source_text


def test_long_windows_paths_rejected_before_moving(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows legacy path limit")
    store, book = make_project(tmp_path)
    nested = book.project_dir / ("x" * 70)
    nested.mkdir()
    (nested / ("f" * 60)).write_bytes(b"keep")
    with pytest.raises(ValueError, match="too long"):
        store.rename_audiobook(book.id, "n" * 80)
    assert book.project_dir.exists()


def test_histories_remapped_and_active_jobs_block_rename(tmp_path):
    store, book = make_project(tmp_path / "projects", database="localtext2voice.sqlite3")
    server = tmp_path / "server"
    server.mkdir()
    db = server / "local_server_jobs.sqlite3"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE server_jobs (status TEXT, audiobook_id INTEGER, clean_audio_path TEXT, request_json TEXT)")
        c.execute("INSERT INTO server_jobs VALUES ('running', ?, ?, ?)",
                  (book.id, str(book.project_dir / "voice.wav"), json.dumps({"output_dir": str(book.output_dir)})))
    with pytest.raises(RuntimeError, match="generation jobs"):
        store.rename_audiobook(book.id, "History")
    assert book.project_dir.exists()
    with sqlite3.connect(db) as c:
        c.execute("UPDATE server_jobs SET status='complete'")
    result = store.rename_audiobook(book.id, "History")
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT clean_audio_path, request_json FROM server_jobs").fetchone()
    assert row[0] == str(result.project_dir / "voice.wav")
    assert json.loads(row[1])["output_dir"] == str(result.output_dir)


def test_headless_storyboard_job_blocks_folder_move(tmp_path):
    store, book = make_project(tmp_path)
    jobs = book.project_dir / "storyboard" / "jobs"
    jobs.mkdir()
    job = jobs / "job1.json"
    job.write_text('{"status": "running"}')
    with pytest.raises(RuntimeError, match="storyboard jobs"):
        store.rename_audiobook(book.id, "Storyboard")
    assert book.project_dir.exists()
    job.write_text('{"status": "complete"}')
    assert store.rename_audiobook(book.id, "Storyboard").project_dir.name == "Storyboard"


def test_unrelated_old_server_job_and_paused_batch_do_not_block(tmp_path):
    store, book = make_project(tmp_path / "projects", database="localtext2voice.sqlite3")
    server = tmp_path / "server"
    server.mkdir()
    with sqlite3.connect(server / "local_server_jobs.sqlite3") as c:
        c.execute("CREATE TABLE server_jobs (status TEXT, audiobook_id INTEGER, request_json TEXT, updated_at TEXT)")
        c.execute("INSERT INTO server_jobs VALUES ('running', NULL, '{}', '2026-09-27')")
        c.execute("INSERT INTO server_jobs VALUES ('queued', 999, '{}', '2026-10-09')")
        c.execute("INSERT INTO server_jobs VALUES ('running',NULL,?,'2026-10-09')",
                  (json.dumps({"output_dir": str(book.project_dir.parent / "Project70" / "exports")}),))
    with sqlite3.connect(store.db_path.parent / "bulk_audiobooks.sqlite3") as c:
        c.execute("CREATE TABLE bulk_audiobook_batches (id INTEGER PRIMARY KEY, status TEXT, config_json TEXT)")
        c.execute("CREATE TABLE bulk_audiobook_items (id INTEGER PRIMARY KEY, batch_id INTEGER, status TEXT, audiobook_id INTEGER, source_path TEXT)")
        c.execute("INSERT INTO bulk_audiobook_batches VALUES (1,'paused','{}')")
        # Even a queued item for this project is safe while its batch is paused.
        c.execute("INSERT INTO bulk_audiobook_items VALUES (1,1,'queued',?,?)", (book.id, str(book.project_dir / "source.txt")))
    renamed = store.rename_audiobook(book.id, "Usable")
    assert renamed.project_dir.name == "Usable"
    with sqlite3.connect(store.db_path.parent / "bulk_audiobooks.sqlite3") as c:
        status, path = c.execute("SELECT status,source_path FROM bulk_audiobook_items").fetchone()
    assert status == "queued"
    assert path == str(renamed.project_dir / "source.txt")


@pytest.mark.parametrize("kind", ["request_id", "request_path", "active_bulk"])
def test_jobs_for_this_project_still_block_before_id_is_published(tmp_path, kind):
    store, book = make_project(tmp_path / "projects", database="localtext2voice.sqlite3")
    if kind == "active_bulk":
        with sqlite3.connect(store.db_path.parent / "bulk_audiobooks.sqlite3") as c:
            c.execute("CREATE TABLE bulk_audiobook_batches (id INTEGER PRIMARY KEY, status TEXT)")
            c.execute("CREATE TABLE bulk_audiobook_items (id INTEGER PRIMARY KEY, batch_id INTEGER, status TEXT, audiobook_id INTEGER)")
            c.execute("INSERT INTO bulk_audiobook_batches VALUES (1,'running')")
            c.execute("INSERT INTO bulk_audiobook_items VALUES (1,1,'queued',?)", (book.id,))
    else:
        server = tmp_path / "server"
        server.mkdir()
        request = {"project_audiobook_id": book.id} if kind == "request_id" else {"output_dir": str(book.output_dir)}
        with sqlite3.connect(server / "local_server_jobs.sqlite3") as c:
            c.execute("CREATE TABLE server_jobs (status TEXT, audiobook_id INTEGER, request_json TEXT)")
            c.execute("INSERT INTO server_jobs VALUES ('queued',NULL,?)", (json.dumps(request),))
    with pytest.raises(RuntimeError, match="this project's generation jobs"):
        store.rename_audiobook(book.id, "Blocked")
    assert book.project_dir.exists()


def test_live_paths_follow_rename_even_from_another_store_instance(tmp_path):
    store, book = make_project(tmp_path)
    paths = store.project_paths(book.id)
    audio_reference = paths.reference(book.project_dir / "voice.wav")
    assert audio_reference == "voice.wav"
    assert paths.root == book.project_dir
    other = AudiobookStore(store.db_path)
    renamed = other.rename_audiobook(book.id, "Live paths")
    assert paths.root == renamed.project_dir
    assert paths.output == renamed.output_dir
    assert paths.resolve(audio_reference).read_bytes() == b"original audio"
    external = tmp_path / "external.wav"
    assert paths.reference(external) == str(external)
    assert paths.resolve(external) == external
    with pytest.raises(ValueError, match="escape"):
        paths.resolve("../outside.wav")
    with pytest.raises(ValueError, match="not found"):
        store.project_paths(9999).root


@pytest.mark.skipif(os.name != "nt", reason="Native Windows sharing handles")
def test_windows_open_file_does_not_leave_a_partial_rename(tmp_path):
    import ctypes
    from ctypes import wintypes
    store, book = make_project(tmp_path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(book.project_dir / "voice.wav"), 0x80000000, 1, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        with pytest.raises(PermissionError):
            store.rename_audiobook(book.id, "Unlocked")
        assert store.get_audiobook(book.id) == book
        assert book.project_dir.exists()
    finally:
        kernel.CloseHandle(handle)
    renamed = store.rename_audiobook(book.id, "Unlocked")
    assert (renamed.project_dir / "voice.wav").read_bytes() == b"original audio"
