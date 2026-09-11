import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ebooklib import epub

from app.core.project_manager import ProjectManager, DocumentImportError


class TestProjectManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_epub(self, path: Path, title: str, author: str, chapters: list[tuple[str, str]], with_cover: bool = False):
        book = epub.EpubBook()
        book.set_title(title)
        book.set_language("en")
        book.add_author(author)
        book.set_identifier("test-id-123")
        
        if with_cover:
            book.set_cover("cover.jpg", b"fake_image_data")
            
        spine = ["nav"]
        toc_entries = []
        for i, (ch_title, content) in enumerate(chapters):
            chapter = epub.EpubHtml(title=ch_title, file_name=f"chap_{i}.xhtml", lang="en")
            chapter.content = f"<h1>{ch_title}</h1><p>{content}</p>"
            book.add_item(chapter)
            spine.append(chapter)
            toc_entries.append(chapter)
            
        book.toc = tuple(toc_entries)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        book.spine = spine
        epub.write_epub(str(path), book)
        return path

    def test_import_epub_metadata_and_structure(self):
        epub_path = self.dir_path / "test.epub"
        self._create_epub(
            epub_path, 
            "Test Book", 
            "Test Author", 
            [("Chapter 1", "This is chapter 1."), ("Chapter 2", "This is chapter 2.")],
            with_cover=True
        )

        text, metadata = ProjectManager.import_document_with_metadata(epub_path)
        
        self.assertEqual(metadata.get("title"), "Test Book")
        self.assertEqual(metadata.get("author"), "Test Author")
        self.assertEqual(metadata.get("language"), "en")
        self.assertEqual(metadata.get("isbn"), "test-id-123")
        self.assertEqual(metadata.get("cover_mode"), "custom")
        self.assertTrue(metadata.get("cover_source_path"))
        
        self.assertIn("# Chapter 1", text)
        self.assertIn("This is chapter 1.", text)
        self.assertIn("# Chapter 2", text)
        self.assertIn("This is chapter 2.", text)

    def test_import_unsupported(self):
        unsupported = self.dir_path / "test.xyz"
        unsupported.write_text("hello")
        with self.assertRaisesRegex(DocumentImportError, "Unsupported file type"):
            ProjectManager.import_document(unsupported)

    def test_import_txt(self):
        txt_path = self.dir_path / "test.txt"
        txt_path.write_text("Hello text")
        text, metadata = ProjectManager.import_document_with_metadata(txt_path)
        self.assertEqual(text, "Hello text")
        self.assertEqual(metadata, {})

if __name__ == "__main__":
    unittest.main()
