from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from app.core.audiobook_store import AudiobookStore
from app.core.wav_cache_stats import WavCacheStats, scan_wav_cache
from app.ui.main_window import MainWindow
from app.ui.wav_cache_stats import WavCacheStatsController, _POOL


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


def wait_until(application, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def controller(application):
    controller = WavCacheStatsController()
    yield controller
    assert _POOL.waitForDone(5000)
    application.processEvents()
    controller.deleteLater()


def test_scan_counts_real_files_and_excludes_active_preview(tmp_path):
    store = AudiobookStore(tmp_path / "test.sqlite3")
    book = store.create_audiobook(
        "Test text", {"engine": "piper"}, tmp_path / "output",
        "safe_chunks", "single", "Cache test", project_dir=tmp_path / "project",
    )
    segments = book.project_dir / "segments"
    segments.mkdir(exist_ok=True)
    (segments / "one.wav").write_bytes(b"12345")
    (segments / "two.wav").write_bytes(b"123")
    (segments / "ignore.txt").write_text("not audio")
    temp_root = tmp_path / "temp"
    active = temp_root / "local_text_2_voice_active"
    abandoned = temp_root / "local_text_2_voice_old"
    unrelated = temp_root / "unrelated"
    for directory in (active, abandoned, unrelated):
        directory.mkdir(parents=True)
        (directory / "audio.wav").write_bytes(b"123456")
    (abandoned / "nested").mkdir()
    (abandoned / "nested" / "mix.wav").write_bytes(b"12")

    stats = scan_wav_cache(store, book.id, temp_root, (active,))
    assert stats == WavCacheStats((2, 8), (2, 8), (2, 8))
    # No selected project retains the existing latest-project fallback.
    assert scan_wav_cache(store, None, temp_root, (active,)) == stats
    assert (active / "audio.wav").read_bytes() == b"123456"


def test_settings_remains_responsive_during_slow_scan(application, controller, tmp_path):
    gate = threading.Event()
    started = threading.Event()
    scan_threads = []
    expected = WavCacheStats((1, 20), (2, 40), (3, 60))

    def slow_scan(*args):
        scan_threads.append(threading.get_ident())
        started.set()
        assert gate.wait(5)
        return expected

    holder = SimpleNamespace(
        wav_cache_stats_controller=controller,
        audiobook_store=SimpleNamespace(db_path=tmp_path / "test.sqlite3"),
        current_audiobook_id=1,
        _active_temp_audio_dirs=lambda: (),
        wav_cache_stats_label=QLabel(),
        cleanup_current_wavs_button=QPushButton(),
        cleanup_all_wavs_button=QPushButton(),
        cleanup_temp_audio_button=QPushButton(),
        _can_cleanup_wav_cache=lambda: True,
        _format_bytes=lambda value: str(value),
        tr=lambda _key, default, **values: default.format(**values),
    )
    holder._refresh_wav_cache_stats = lambda **kwargs: MainWindow._refresh_wav_cache_stats(holder, **kwargs)
    holder._show_page = Mock()
    controller.busy.connect(lambda: MainWindow._wav_cache_stats_busy(holder))
    results = []
    controller.ready.connect(results.append)
    controller.ready.connect(lambda stats: MainWindow._apply_wav_cache_stats(holder, stats))
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    try:
        with patch("app.ui.wav_cache_stats.scan_wav_cache", side_effect=slow_scan):
            MainWindow._show_settings_page(holder)
            assert holder.wav_cache_stats_label.text() == "Calculating..."
            assert not holder.cleanup_all_wavs_button.isEnabled()
            wait_until(application, lambda: started.is_set() and len(ticks) >= 5)
            assert not results
            assert len(scan_threads) == 1
            assert scan_threads[0] != threading.get_ident()
            gate.set()
            wait_until(application, lambda: bool(results))
            assert results == [expected]
            assert holder.cleanup_all_wavs_button.isEnabled()
            assert "Current project: 1 WAV(s), 20" in holder.wav_cache_stats_label.text()
    finally:
        gate.set()
        timer.stop()


def test_cached_results_expire_and_force_refresh_after_cleanup(application, controller, tmp_path):
    store = SimpleNamespace(db_path=tmp_path / "test.sqlite3")
    results = []
    controller.ready.connect(results.append)
    before = WavCacheStats((1, 20), (1, 20), (0, 0))
    after = WavCacheStats((0, 0), (0, 0), (0, 0))
    with patch("app.ui.wav_cache_stats.scan_wav_cache", side_effect=[before, after, after]) as scan:
        controller.request(store, 1, tmp_path)
        wait_until(application, lambda: len(results) == 1)
        controller.request(store, 1, tmp_path)
        assert results == [before, before]
        assert scan.call_count == 1
        controller.request(store, 1, tmp_path, force=True)
        wait_until(application, lambda: len(results) == 3)
        assert results[-1] == after
        controller._cached_at -= 31
        controller.request(store, 1, tmp_path)
        wait_until(application, lambda: len(results) == 4)
        assert scan.call_count == 3


def test_inflight_project_switch_discards_old_results_and_coalesces(application, controller, tmp_path):
    gate = threading.Event()
    started = threading.Event()
    store = SimpleNamespace(db_path=tmp_path / "test.sqlite3")
    results, calls = [], []
    controller.ready.connect(results.append)

    def scan(_store, project_id, *_args):
        calls.append(project_id)
        if project_id == 1:
            started.set()
            assert gate.wait(5)
        return WavCacheStats((project_id, 0), (10, 0), (0, 0))

    try:
        with patch("app.ui.wav_cache_stats.scan_wav_cache", side_effect=scan):
            controller.request(store, 1, tmp_path)
            wait_until(application, started.is_set)
            controller.request(store, 1, tmp_path)
            controller.request(store, 2, tmp_path)
            controller.request(store, 3, tmp_path)
            gate.set()
            wait_until(application, lambda: bool(results))
            assert calls == [1, 3]
            assert [result.current for result in results] == [(3, 0)]
    finally:
        gate.set()


def test_scan_failure_can_be_retried(application, controller, tmp_path):
    store = SimpleNamespace(db_path=tmp_path / "test.sqlite3")
    failures, results = [], []
    controller.failed.connect(failures.append)
    controller.ready.connect(results.append)
    empty = WavCacheStats((0, 0), (0, 0), (0, 0))
    with patch("app.ui.wav_cache_stats.scan_wav_cache", side_effect=[OSError("Unavailable"), empty]):
        controller.request(store, 1, tmp_path)
        wait_until(application, lambda: bool(failures))
        assert failures == ["Unavailable"]
        controller.request(store, 1, tmp_path)
        wait_until(application, lambda: bool(results))
        assert results == [empty]


def test_invalidation_during_scan_does_not_publish_old_counts(application, controller, tmp_path):
    gate, started = threading.Event(), threading.Event()
    store = SimpleNamespace(db_path=tmp_path / "test.sqlite3")
    results, calls = [], []
    controller.ready.connect(results.append)

    def scan(*args):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert gate.wait(5)
            return WavCacheStats((5, 50), (5, 50), (0, 0))
        return WavCacheStats((0, 0), (0, 0), (0, 0))

    try:
        with patch("app.ui.wav_cache_stats.scan_wav_cache", side_effect=scan):
            controller.request(store, 1, tmp_path)
            wait_until(application, started.is_set)
            controller.request(store, 1, tmp_path, force=True)
            gate.set()
            wait_until(application, lambda: bool(results))
            assert len(calls) == 2
            assert [result.current for result in results] == [(0, 0)]
    finally:
        gate.set()


def test_window_can_be_destroyed_during_scan(application, tmp_path):
    parent = QObject()
    controller = WavCacheStatsController(parent)
    gate, started = threading.Event(), threading.Event()

    def scan(*args):
        started.set()
        assert gate.wait(5)
        return WavCacheStats((0, 0), (0, 0), (0, 0))

    try:
        with patch("app.ui.wav_cache_stats.scan_wav_cache", side_effect=scan):
            controller.request(SimpleNamespace(db_path=tmp_path / "test.sqlite3"), 1, tmp_path)
            wait_until(application, started.is_set)
            import shiboken6
            shiboken6.delete(parent)
            gate.set()
            assert _POOL.waitForDone(5000)
            application.processEvents()
    finally:
        gate.set()
