"""Recoverable project moves; audio files are moved, never copied or rewritten."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata


def _is_link(path: Path) -> bool:
    # is_junction was added in Python 3.12; Windows reparse points are also
    # detectable on the supported Python 3.10 runtime.
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def safe_project_folder_name(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(title or ""))
    normalized = "".join(c for c in normalized if not unicodedata.category(c).startswith("C"))
    name = re.sub(r'[<>:"/\\|?*]+', "-", normalized).strip(" .-")
    name = re.sub(r"\s+", " ", name)[:80].rstrip(" .-") or "Audiobook"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = f"Audiobook - {name}"
    return name


def relocate_markup(text: str, old: Path, new: Path) -> str:
    """Only path arguments in LTV commands are changed, never prose."""
    def command(match):
        raw = match.group(0)
        if not re.match(r"\{\{\s*(?:play|voice(?:\.[\w]+)?|cmd|sendcommand|sendcomand)\b", raw, re.IGNORECASE):
            return raw
        def argument(quoted):
            value = quoted.group(2)
            updated = relocate_paths(value, old, new, markup=False)
            if updated == value:
                return quoted.group(0)
            return quoted.group(1) + Path(updated).as_posix() + quoted.group(1)
        return re.sub(r'''(["'])(.*?)\1''', argument, raw)
    return re.sub(r"\{\{.*?\}\}", command, text, flags=re.DOTALL)


def relocate_paths(value, old: Path, new: Path, *, markup: bool = True):
    """Remap complete absolute path values, never substrings or relative paths."""
    if isinstance(value, dict):
        return {k: relocate_paths(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate_paths(v, old, new) for v in value]
    if not isinstance(value, (str, Path)) or not str(value):
        return value
    path = Path(value)
    if not path.is_absolute():
        return relocate_markup(value, old, new) if markup and isinstance(value, str) and "{{" in value else value
    try:
        # Lexical normalization avoids resolving links to external assets.
        relative = Path(os.path.abspath(path)).relative_to(old)
    except (ValueError, OSError):
        return value
    result = new / relative
    return result if isinstance(value, Path) else str(result)


def _atomic_bytes(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _journal_path(store) -> Path:
    return store.db_path.with_name(store.db_path.name + ".rename.json")


def _metadata_changes(old: Path, new: Path) -> list[dict]:
    changes = []
    # Storyboard checkpoints/references contain paths. The store publishes
    # manifests after commit, retaining its conflict detection and revision ledger.
    # Do not traverse caches, imported documents or linked directories.
    candidates = []
    storyboard = old / "storyboard"
    if storyboard.is_dir() and not _is_link(storyboard):
        for root, directories, files in os.walk(storyboard, followlinks=False):
            directories[:] = [d for d in directories if not _is_link(Path(root) / d)]
            candidates.extend(Path(root) / f for f in files if f.endswith((".json", ".json.bak")))
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        original = path.read_bytes()
        try:
            data = json.loads(original.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            continue
        updated = relocate_paths(data, old, new)
        if isinstance(updated, dict) and "source_hash" in updated and isinstance(updated.get("source"), dict):
            source_text = updated["source"].get("text")
            if isinstance(source_text, str):
                updated["source_hash"] = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        if data != updated:
            changes.append({"relative": str(path.relative_to(old)),
                            "before": base64.b64encode(original).decode("ascii"),
                            "after": base64.b64encode(json.dumps(updated, ensure_ascii=False, indent=2).encode()).decode("ascii")})
    return changes


def _rewrite_table(connection, schema: str, table: str, where: str, params: tuple,
                   old: Path, new: Path) -> None:
    columns = [row[1] for row in connection.execute(f'PRAGMA {schema}.table_info("{table}")')]
    paths = [c for c in columns if c.endswith(("_path", "_dir", "_json"))
             or c in ("file_reference", "voice", "source_text", "normalized_source_text", "raw_command")]
    if not paths:
        return
    rows = connection.execute(f'SELECT rowid AS rename_rowid, * FROM {schema}."{table}" WHERE {where}', params).fetchall()
    for row in rows:
        changes = {}
        for column in paths:
            value = row[column]
            if column.endswith("_json") and value:
                try:
                    parsed = json.loads(value)
                except (ValueError, TypeError):
                    continue
                updated = relocate_paths(parsed, old, new)
                if updated != parsed:
                    changes[column] = json.dumps(updated, ensure_ascii=False)
            elif column in ("source_text", "normalized_source_text", "raw_command"):
                updated = relocate_markup(value or "", old, new)
                if updated != value:
                    changes[column] = updated
            else:
                updated = relocate_paths(value, old, new)
                if updated != value:
                    changes[column] = updated
        if changes:
            if table == "audiobooks" and "source_text" in changes:
                changes["source_hash"] = hashlib.sha256(changes["source_text"].encode("utf-8")).hexdigest()
            assignments = ", ".join(f'"{c}" = ?' for c in changes)
            connection.execute(f'UPDATE {schema}."{table}" SET {assignments} WHERE rowid = ?',
                               (*changes.values(), row["rename_rowid"]))


def _job_targets_project(value, project_id: int, root: Path) -> bool:
    """Match explicit project IDs or whole path references, never titles."""
    if isinstance(value, dict):
        for key in ("audiobook_id", "project_audiobook_id", "project_id"):
            if str(value.get(key, "")) == str(project_id):
                return True
        return any(_job_targets_project(v, project_id, root) for v in value.values())
    if isinstance(value, list):
        return any(_job_targets_project(v, project_id, root) for v in value)
    if not isinstance(value, str):
        return False
    try:
        path = Path(value)
        return path.is_absolute() and Path(os.path.abspath(path)).is_relative_to(root)
    except (ValueError, OSError):
        return False


def _blocking_history_job(connection, schema: str, table: str, project_id: int, root: Path):
    for row in connection.execute(f"SELECT * FROM {schema}.{table} WHERE status IN ('running', 'queued')"):
        job = dict(row)
        if schema == "bulk" and job["status"] == "queued" and "batch_id" in job:
            batch = connection.execute(
                f"SELECT status FROM {schema}.bulk_audiobook_batches WHERE id = ?", (job["batch_id"],)
            ).fetchone()
            # Paused batches deliberately retain resumable queued items. They
            # do not hold files, and their stored paths are remapped below.
            if batch is not None and batch["status"] == "paused":
                continue
        for key, value in list(job.items()):
            if key.endswith("_json") and value:
                try:
                    job[key] = json.loads(value)
                except (ValueError, TypeError):
                    pass
        if _job_targets_project(job, project_id, root):
            return job
    return None


def recover_project_rename(store) -> None:
    """SQLite's committed directory decides which side of an interrupted move wins."""
    journal = _journal_path(store)
    if not journal.exists():
        return
    data = json.loads(journal.read_text(encoding="utf-8"))
    old, new = Path(data["old"]), Path(data["new"])
    if old.parent != new.parent or str(old) == str(new):
        raise RuntimeError("Invalid project rename recovery journal.")
    book = store.get_audiobook(data["id"])
    if book is None or book.uuid != data["uuid"]:
        raise RuntimeError("Project rename recovery identity does not match.")
    if str(book.project_dir) not in (str(old), str(new)):
        raise RuntimeError("Project directory no longer matches its rename recovery journal.")
    committed = str(book.project_dir) == str(new)
    target, other = (new, old) if committed else (old, new)
    case_only = old == new  # WindowsPath compares without case.
    if target.exists() and other.exists() and not case_only:
        raise RuntimeError(f"Cannot recover project rename: both folders exist: {old}, {new}")
    if case_only and other.exists():
        other.rename(target)
    elif not target.exists():
        if not other.is_dir():
            raise RuntimeError(f"Cannot recover project rename: missing folder: {target}")
        other.rename(target)
    for change in data["files"]:
        path = target / change["relative"]
        if not path.resolve().is_relative_to(target.resolve()):
            raise RuntimeError("Invalid project metadata recovery path.")
        _atomic_bytes(path, base64.b64decode(change["after" if committed else "before"]))
    if committed:
        store.flush_project_manifest(book.id, force=True)
    journal.unlink()


def rename_project(store, audiobook_id: int, title: str):
    recover_project_rename(store)
    book = store.get_audiobook(audiobook_id)
    if book is None:
        raise ValueError(f"Audiobook project not found: {audiobook_id}")
    old = store.project_paths(book.id).root.absolute()
    if not old.is_dir() or _is_link(old):
        raise ValueError("Project folder must be an existing regular directory.")
    if store.db_path.resolve().is_relative_to(old.resolve()):
        raise ValueError("Cannot rename a folder containing the project database.")
    name = safe_project_folder_name(title)
    # Collisions use a suffix and never replace another project. Windows allows
    # renaming only the casing; compare strings when deciding whether to move.
    new = old.parent / name
    index = 2
    while new.exists() and new != old:
        new = old.parent / f"{name} ({index})"
        index += 1
    moving = str(new) != str(old)
    if moving:
        # Respect legacy Windows consumers too; reserve room for every existing
        # descendant. Refuse a longer path instead of truncating user filenames.
        if os.name == "nt":
            for root, dirs, files in os.walk(old, followlinks=False):
                dirs[:] = [d for d in dirs if not _is_link(Path(root) / d)]
                for child in [Path(root), *(Path(root) / f for f in files)]:
                    proposed = str(new / child.relative_to(old))
                    if len(proposed.encode("utf-16-le")) // 2 >= 260:
                        raise ValueError("The resulting project path is too long; use a shorter title.")
    original_settings = store._json_to_dict(book.project_settings_json)
    settings = relocate_paths(original_settings, old, new)
    fixed_output = original_settings.get("output_dir_mode") == "absolute"
    if fixed_output:
        settings["output_dir"] = original_settings.get("output_dir", str(book.output_dir))
    metadata = settings.get("book_metadata", {})
    if isinstance(metadata, dict) and bool(metadata.get("title_follows_project", True)):
        settings["book_metadata"] = {**metadata, "title": title}
    journal = _journal_path(store)
    try:
        with store._connect() as connection:
            # Attach existing histories only. Opening a test/custom database
            # never touches the installation's real histories.
            histories = []
            if store.db_path.name == "localtext2voice.sqlite3":
                for schema, path, table in (
                    ("bulk", store.db_path.parent / "bulk_audiobooks.sqlite3", "bulk_audiobook_items"),
                    ("server", store.db_path.parent.parent / "server" / "local_server_jobs.sqlite3", "server_jobs"),
                ):
                    if path.is_file():
                        connection.execute(f"ATTACH DATABASE ? AS {schema}", (str(path),))
                        histories.append((schema, table))
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM audiobooks WHERE id = ? AND status = 'rendering'", (book.id,)).fetchone():
                raise RuntimeError("Wait for project generation to finish before renaming it.")
            for row in connection.execute("SELECT id, project_dir FROM audiobooks WHERE id != ?", (book.id,)):
                other = Path(row["project_dir"]).absolute()
                if other.is_relative_to(old) or old.is_relative_to(other):
                    raise ValueError("Project folders overlap; move projects into separate folders before renaming.")
            for schema, table in histories:
                job = _blocking_history_job(connection, schema, table, book.id, old)
                if job is not None:
                    identity = job.get("job_id", job.get("id", ""))
                    raise RuntimeError(
                        f"Wait for this project's generation jobs to finish before renaming it "
                        f"({schema}: {identity}, {job['status']})."
                    )
            if store._manifests_deferred(audiobook_id):
                raise RuntimeError("Wait for project processing to finish before renaming it.")
            # Headless storyboard jobs persist their lifecycle outside SQLite.
            # Their submission uses the same operation lock as this rename.
            jobs_dir = old / "storyboard" / "jobs"
            if jobs_dir.is_dir():
                for job_path in jobs_dir.glob("*.json"):
                    job = json.loads(job_path.read_text(encoding="utf-8"))
                    if job.get("status") in {"queued", "running"}:
                        raise RuntimeError("Finish or recover pending storyboard jobs before renaming the project.")
            if moving:
                changes = _metadata_changes(old, new)
                data = {"id": book.id, "uuid": book.uuid, "old": str(old), "new": str(new), "files": changes}
                _atomic_bytes(journal, json.dumps(data, ensure_ascii=False).encode())
                old.rename(new)
                for change in changes:
                    _atomic_bytes(new / change["relative"], base64.b64decode(change["after"]))
                for table in ("audiobooks", "audiobook_segments", "audiobook_outputs", "audio_events"):
                    column = "id" if table == "audiobooks" else "audiobook_id"
                    _rewrite_table(connection, "main", table, f"{column} = ?", (book.id,), old, new)
                _rewrite_table(connection, "main", "segment_attempts",
                               "segment_id IN (SELECT id FROM audiobook_segments WHERE audiobook_id = ?)", (book.id,), old, new)
                for schema, table in histories:
                    _rewrite_table(connection, schema, table, "1 = 1", (), old, new)
                    if schema == "bulk":
                        _rewrite_table(connection, schema, "bulk_audiobook_batches", "1 = 1", (), old, new)
            connection.execute("UPDATE audiobooks SET title = ?, project_settings_json = ?, updated_at = ? WHERE id = ?",
                               (title, json.dumps(settings, ensure_ascii=False), store._now(), book.id))
            if fixed_output:
                connection.execute("UPDATE audiobooks SET output_dir = ? WHERE id = ?", (str(book.output_dir), book.id))
    except BaseException:
        # If rollback itself fails (e.g. an external file lock), keep the journal
        # for startup recovery instead of losing the only recovery information.
        recover_project_rename(store)
        raise
    result = store.get_audiobook(book.id)
    store.flush_project_manifest(book.id, force=True)
    journal.unlink(missing_ok=True)
    return result
