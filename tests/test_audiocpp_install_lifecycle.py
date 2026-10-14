import os
import time
from unittest.mock import Mock

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QWidget

from app.ui.audiocpp_settings import AudioCppSettingsMixin


@pytest.mark.parametrize("error", [False, True])
def test_install_result_published_after_thread_stops(error):
    app = QApplication.instance() or QApplication([])
    class Window(AudioCppSettingsMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.audiocpp_thread = self.audiocpp_worker = None
            self.audiocpp_manager = Mock()
            self.dialog = Mock()
            self.engine_install_dialogs = {"audiocpp": self.dialog}
            self.calls = []
        def tr(self, key, default):
            return default
        def _refresh_audiocpp_status(self):
            assert QThread.currentThread() == app.thread()
        def _refresh_tts_engine_table(self):
            assert QThread.currentThread() == app.thread()
        def _refresh_voices_page(self):
            assert QThread.currentThread() == app.thread()
    window = Window()
    def install(model, backend, progress):
        assert QThread.currentThread() != app.thread()
        progress(4_960_716_192, 4_960_716_192, "Verifying weights")
        if error:
            raise RuntimeError("Download cancelled")
    window.audiocpp_manager.install.side_effect = install
    def finish(ok, message):
        assert QThread.currentThread() == app.thread()
        assert window.audiocpp_thread is None and window.audiocpp_worker is None
        window.calls.append(ok)
    window.dialog.finish.side_effect = finish
    for _ in range(5):
        window._start_audiocpp_install({"model": "voxcpm2_orig", "backend": "cpu"}, window.dialog)
        deadline = time.monotonic() + 5
        while window.audiocpp_thread is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert window.audiocpp_thread is None
    assert window.calls == [not error] * 5
    window.deleteLater()
    app.processEvents()
