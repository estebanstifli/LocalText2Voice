import unittest
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import shutil

from app.ui.main_window import MainWindow
from app.core.settings_manager import DEFAULT_SETTINGS
from PySide6.QtWidgets import QApplication

class TestOutputLocation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        
        self.output_dir = self.temp_path / "output"
        self.output_dir.mkdir()
        
        self.window = MainWindow()
        self.window.settings["output_dir"] = str(self.output_dir)
        self.window.output_picker.set_path(self.output_dir)
        
    def tearDown(self):
        self.window.deleteLater()
        self.temp_dir.cleanup()

    def _trigger_generation(self):
        # Override worker to intercept output_dir
        captured_output_dir = None
        
        def mock_generate(*args, **kwargs):
            nonlocal captured_output_dir
            if len(args) > 2 and isinstance(args[2], dict):
                captured_output_dir = args[2].get("generation_settings", {}).get("output_dir")
            original_init(*args, **kwargs)
            
        from app.workers.engine_host_generation_worker import EngineHostGenerationWorker
        original_init = EngineHostGenerationWorker.__init__
        EngineHostGenerationWorker.__init__ = mock_generate
        
        from PySide6.QtCore import QThread
        original_start = QThread.start
        QThread.start = lambda self: None
        
        original_voice_config = self.window._current_voice_config
        self.window._current_voice_config = lambda: {"engine": "piper", "voice": "test"}
        
        try:
            self.window._start_generation()
        finally:
            EngineHostGenerationWorker.__init__ = original_init
            QThread.start = original_start
            self.window._current_voice_config = original_voice_config
            
        return captured_output_dir

    def test_manual_text_checkbox_false(self):
        self.window.save_next_to_source_checkbox.setChecked(False)
        self.window.text_editor.setPlainText("Hello world")
        
        out_dir = self._trigger_generation()
        self.assertEqual(str(out_dir), str(self.output_dir))

    def test_manual_text_checkbox_true(self):
        self.window.save_next_to_source_checkbox.setChecked(True)
        self.window.text_editor.setPlainText("Hello world")
        
        out_dir = self._trigger_generation()
        self.assertEqual(str(out_dir), str(self.output_dir))

    def _setup_imported_file(self, filename: str, content: str = "content"):
        file_path = self.temp_path / filename
        file_path.write_text(content, encoding="utf-8")
        
        from app.core.book_metadata import BOOK_METADATA_KEY
        self.window.settings[BOOK_METADATA_KEY] = {
            "imported_source_path": str(file_path)
        }
        self.window.text_editor.setPlainText(content)
        return file_path

    def test_txt_checkbox_false(self):
        self.window.save_next_to_source_checkbox.setChecked(False)
        self._setup_imported_file("book.txt")
        
        out_dir = self._trigger_generation()
        self.assertEqual(str(out_dir), str(self.output_dir))

    def test_txt_checkbox_true(self):
        self.window.save_next_to_source_checkbox.setChecked(True)
        file_path = self._setup_imported_file("book.txt")
        
        out_dir = self._trigger_generation()
        self.assertEqual(str(out_dir), str(file_path.parent))

    def test_docx_checkbox_true(self):
        self.window.save_next_to_source_checkbox.setChecked(True)
        file_path = self._setup_imported_file("book.docx")
        
        out_dir = self._trigger_generation()
        self.assertEqual(str(out_dir), str(file_path.parent))

    def test_epub_checkbox_true(self):
        self.window.save_next_to_source_checkbox.setChecked(True)
        file_path = self._setup_imported_file("book.epub")
        
        out_dir = self._trigger_generation()
        self.assertEqual(str(out_dir), str(file_path.parent))

    def test_file_without_path_checkbox_true(self):
        self.window.save_next_to_source_checkbox.setChecked(True)
        from app.core.book_metadata import BOOK_METADATA_KEY
        self.window.settings[BOOK_METADATA_KEY] = {
            "imported_source_path": "nonexistent/path/book.txt"
        }
        self.window.text_editor.setPlainText("Hello world")
        
        out_dir = self._trigger_generation()
        self.assertEqual(str(out_dir), str(self.output_dir))

    def test_settings_persistence(self):
        self.window.save_next_to_source_checkbox.setChecked(True)
        self.window._save_settings()
        self.assertTrue(self.window.settings["save_next_to_source"])
        
        self.window.save_next_to_source_checkbox.setChecked(False)
        self.window._save_settings()
        self.assertFalse(self.window.settings["save_next_to_source"])
