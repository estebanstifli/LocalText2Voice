"""Resolve project-relative resources through the project's stable identity."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    """A live resolver, not a cached directory. Keep this object across renames.

    Store internal resource references as relative paths; absolute references
    remain supported for external resources and legacy project data.
    """

    _locations: Callable[[], tuple[Path, Path]]

    @property
    def root(self) -> Path:
        return self._locations()[0]

    @property
    def output(self) -> Path:
        root, output = self._locations()
        return output if output.is_absolute() else root / output

    def resolve(self, reference: str | Path) -> Path:
        path = Path(reference)
        if path.is_absolute():
            return path
        if ".." in path.parts:
            raise ValueError("Project-relative paths cannot escape the project folder.")
        return self.root / path

    def reference(self, path: str | Path) -> str:
        path = Path(path)
        if not path.is_absolute():
            if ".." in path.parts:
                raise ValueError("Project-relative paths cannot escape the project folder.")
            return path.as_posix()
        path = Path(os.path.abspath(path))
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)
