import logging

from app.utils.logging_utils import log_uncaught_exception


def test_qt_slot_exception_retains_traceback_in_log(caplog):
    try:
        raise TypeError("changed() only accepts 0 argument(s), 1 given!")
    except TypeError as exc:
        with caplog.at_level(logging.ERROR):
            log_uncaught_exception(type(exc), exc, exc.__traceback__)
    assert "Unhandled application exception" in caplog.text
    assert "Traceback" in caplog.text
    assert "changed() only accepts" in caplog.text
