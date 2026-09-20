from __future__ import annotations

import logging
import sys
from pathlib import Path


def configure_logging(log_file: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    sys.excepthook = log_uncaught_exception


def log_uncaught_exception(exc_type, exc_value, traceback):
    """Also retain exceptions raised by Qt slots, which otherwise only hit stderr."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, traceback)
        return
    logging.getLogger(__name__).error(
        "Unhandled application exception", exc_info=(exc_type, exc_value, traceback)
    )
