"""Project-local, per-run copies of first-phase answers (never reasoning)."""
from datetime import datetime
import os
from pathlib import Path
import tempfile
import uuid


class SummaryFiles:
    def __init__(self, project_dir):
        self.directory = (Path(project_dir) / "storyboard" / "analysis" /
                          (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])) if project_dir else None

    def save(self, number, text):
        return self.save_named(f"resumen{number}.txt" if number is not None else "resumen_unificado.txt", text)

    def save_named(self, filename, text):
        if self.directory is None:
            return None
        if Path(filename).name != filename:
            raise ValueError("Summary filename must be a basename")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / filename
        handle, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target
