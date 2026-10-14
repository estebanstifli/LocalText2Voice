from __future__ import annotations

import logging
import faulthandler
import sys
from pathlib import Path


_fault_log = None  # Must remain open while native crash reporting is enabled.

def configure_logging(log_file: Path | None = None) -> None:
    global _fault_log
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
        try:
            if _fault_log is not None:
                faulthandler.disable()
                _fault_log.close()
            _fault_log = log_file.with_suffix(".fault.log").open("a", encoding="utf-8")
            faulthandler.enable(file=_fault_log, all_threads=True)
        except OSError:
            _fault_log = None

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
