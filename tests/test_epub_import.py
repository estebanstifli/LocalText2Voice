from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from app.core.project_manager import ProjectManager, DocumentImportError
from app.core.book_metadata import book_metadata_from_settings
from app.core.document_source import settings_for_document, output_directory
from app.core.audio_pipeline import AudioPipeline, AudioGenerationOptions
from app.core.text_processor import TextProcessor


def make_epub(tmp_path, *, body=None, epub3=True, cover=None, title="Original title", toc=True):
    body = body or '<h1 id="first">Heading One</h1><p>First chapter.</p><h1 id="second">Heading Two</h1><p>Second chapter.</p>'
    path = tmp_path / 'source.epub'
    package = f'''<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <metadata><dc:title>{escape(title)}</dc:title><dc:creator>Author A</dc:creator><dc:creator>Author B</dc:creator><dc:language>es</dc:language><dc:identifier>urn:uuid:abc</dc:identifier>
      <dc:identifier xmlns:opf="http://www.idpf.org/2007/opf" opf:scheme="ISBN">9781234567897</dc:identifier>
      {('<meta name="cover" content="cover"/>' if cover and not epub3 else '')}</metadata>
      <manifest><item id="chapter" href="chapters/ch%201.xhtml" media-type="application/xhtml+xml"/>
      <item id="appendix" href="appendix.xhtml" media-type="application/xhtml+xml"/>
      <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
      <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
      {('<item id="cover" href="cover.png" media-type="image/png" ' + ('properties="cover-image"' if epub3 else '') + '/>') if cover else ''}</manifest>
      <spine toc="ncx"><itemref idref="nav"/><itemref idref="chapter"/><itemref idref="appendix" linear="no"/></spine></package>'''
    nav = '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><h1>Navigation only</h1><ol>
      <li><a href="chapters/ch%201.xhtml#first">TOC One</a></li><li><a href="chapters/ch%201.xhtml#second">TOC Two</a></li></ol></nav></body></html>'''
    ncx = '''<ncx><navMap><navPoint><navLabel><text>TOC One</text></navLabel><content src="chapters/ch%201.xhtml#first"/></navPoint>
      <navPoint><navLabel><text>TOC Two</text></navLabel><content src="chapters/ch%201.xhtml#second"/></navPoint></navMap></ncx>'''
    if not epub3 or not toc:
        package = package.replace(' properties="nav"', '')
    if not toc:
        package = package.replace(' toc="ncx"', '')
    with ZipFile(path, 'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        z.writestr('OPS/book.opf',package)
        z.writestr('OPS/chapters/ch 1.xhtml','<html><head><title>Not narration</title><style>Hidden CSS</style></head><body>'+body+'</body></html>')
        z.writestr('OPS/nav.xhtml',nav)
        z.writestr('OPS/toc.ncx',ncx)
        z.writestr('OPS/appendix.xhtml','<p>Nonlinear content</p>')
        if cover:
            z.writestr('OPS/cover.png',cover)
    return path


@pytest.mark.parametrize('epub3',[True,False])
def test_toc_spine_title_and_m4b_groups(tmp_path,epub3):
    source = make_epub(tmp_path,epub3=epub3)
    doc = ProjectManager.load_document(source)
    assert doc.metadata['title_follows_project'] is False
    assert doc.metadata['author'] == 'Author A, Author B'
    assert doc.metadata['isbn'] == '9781234567897'
    assert 'Navigation only' not in doc.text
    assert 'Nonlinear content' not in doc.text
    assert 'Not narration' not in doc.text
    assert [s.title for s in TextProcessor.split_by_headings(doc.text)] == ['TOC One','TOC Two']
    settings = {'book_metadata':doc.metadata}
    assert book_metadata_from_settings(settings,'Project1')['title'] == 'Original title'
    options = AudioGenerationOptions(tmp_path,{},'ffmpeg',audio_format='m4b',project_settings=settings)
    groups = AudioPipeline(None)._prepare_groups(doc.text,options)
    assert [g.title for g in groups] == ['TOC One','TOC Two']


def test_inline_text_lists_divs_tables_breaks_and_headings(tmp_path):
    path = make_epub(tmp_path,toc=False,body='<h1>Chapter <em>one</em></h1><p>Hello <em>world</em>, friend.</p><div>Important text.</div><ul><li>List item.</li></ul><p>Line 1<br/>Line 2</p><table><tr><td>Cell A</td><td>Cell B</td></tr></table><script>Never read</script>')
    text = ProjectManager.import_document(path)
    for expected in ['# Chapter one','Hello world, friend.','Important text.','List item.','Line 1 Line 2','Cell A Cell B']:
        assert expected in text
    assert 'Never read' not in text


@pytest.mark.parametrize('epub3',[True,False])
def test_cover_is_persisted_and_next_source_clears_metadata(tmp_path,epub3):
    from PySide6.QtGui import QImage
    png = tmp_path/'test.png'
    image=QImage(4,4,QImage.Format.Format_RGB32);image.fill(0xFFCC0000);assert image.save(str(png))
    source=make_epub(tmp_path,epub3=epub3,cover=png.read_bytes())
    doc=ProjectManager.load_document(source)
    old={'book_metadata':{'publisher':'Old publisher'},'image':{'style':'keep'}}
    result=settings_for_document(old,doc,source,'Project1',tmp_path/'project')
    assert result['book_metadata']['cover_mode']=='custom'
    assert result['book_metadata']['cover_path']=='assets/cover.jpg'
    assert (tmp_path/'project/assets/cover.jpg').is_file()
    assert 'cover_source_path' not in result['book_metadata']
    assert result['book_metadata']['publisher']==''
    assert result['image']==old['image']
    assert old['book_metadata']['publisher']=='Old publisher'
    source.unlink()
    assert not QImage(str(tmp_path/'project/assets/cover.jpg')).isNull()
    plain=tmp_path/'next.txt';plain.write_text('Next story')
    next_settings=settings_for_document(result,ProjectManager.load_document(plain),plain,'Project2',tmp_path/'project')
    assert next_settings['book_metadata']['cover_mode']=='auto'
    assert next_settings['book_metadata']['author']==''
    assert next_settings['book_metadata']['title']=='Project2'
    assert next_settings['book_metadata']['chapter_mode']=='markup'


def test_output_options_do_not_rename_project_or_change_defaults(tmp_path):
    source=tmp_path/'Book.epub';source.write_text('placeholder')
    fallback=tmp_path/'project/exports'
    settings={'document_source':{'path':str(source)}}
    assert output_directory(settings,fallback)==fallback
    settings['save_next_to_source']=True
    assert output_directory(settings,fallback)==tmp_path
    pipeline=AudioPipeline(None);pipeline._active_audiobook=SimpleNamespace(title='Project1')
    options=AudioGenerationOptions(fallback,{},'ffmpeg',project_settings=settings)
    assert pipeline._project_output_title(options)=='Project1'
    settings['use_source_filename']=True
    assert pipeline._project_output_title(options)=='Book'
    assert pipeline._active_audiobook.title=='Project1'
    assert pipeline._next_single_filenames(tmp_path,False,'.mp3','Book')[0]=='Book_voice.mp3'
    (tmp_path/'Book_voice.mp3').write_bytes(b'keep')
    assert pipeline._next_single_filenames(tmp_path,False,'.mp3','Book')[0]=='Book_2_voice.mp3'
    source.unlink()
    assert output_directory(settings,fallback)==fallback
    assert output_directory({'save_next_to_source':True},fallback)==fallback


def test_errors_and_existing_plain_text_api(tmp_path):
    txt=tmp_path/'story.txt';txt.write_text('Original text',encoding='utf-8')
    assert ProjectManager.import_document(txt)=='Original text'
    assert ProjectManager.load_document(txt).metadata=={}
    bad=tmp_path/'bad.epub';bad.write_bytes(b'not a ZIP')
    with pytest.raises(DocumentImportError,match='Could not import EPUB'):
        ProjectManager.import_document(bad)
    empty=make_epub(tmp_path,toc=False,body='<p> </p>')
    with pytest.raises(DocumentImportError,match='no readable text'):
        ProjectManager.import_document(empty)


def test_container_cannot_read_outside_zip(tmp_path):
    path=tmp_path/'bad.epub'
    with ZipFile(path,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfile full-path="../../private.txt"/></container>')
    with pytest.raises(DocumentImportError,match='Invalid EPUB resource path'):
        ProjectManager.import_document(path)


def test_real_m4b_export_keeps_epub_metadata_cover_and_chapters(tmp_path, monkeypatch):
    import json
    import shutil
    import subprocess
    from PySide6.QtGui import QImage
    from mutagen.mp4 import MP4
    from app.core.audiobook_store import AudiobookStore
    from tests.test_audio_pipeline import FakeTTSEngine

    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('FFmpeg and ffprobe required')
    monkeypatch.setattr('app.core.audiobook_store.app_data_root', lambda: tmp_path/'appdata')
    png=tmp_path/'cover.png';image=QImage(8,8,QImage.Format.Format_RGB32)
    image.fill(0xFFCC0000);image.save(str(png))
    source=make_epub(tmp_path,cover=png.read_bytes())
    doc=ProjectManager.load_document(source)
    project=tmp_path/'project'
    settings=settings_for_document({},doc,source,'Project1',project)
    settings.update(use_source_filename=True,save_next_to_source=True)
    store=AudiobookStore(tmp_path/'library.sqlite3')
    book=store.create_audiobook(doc.text,{},project/'exports','safe_chunks','single','Project1',settings,project)
    options=AudioGenerationOptions(output_directory(settings,project/'exports'),{'speed':1.0},ffmpeg,
        audio_format='m4b',metadata={'title':'Project1'},project_settings=settings,project_audiobook_id=book.id,
        pause_between_blocks_ms=0,pause_between_chapters_ms=0)
    [output]=AudioPipeline(FakeTTSEngine(),audiobook_store=store).generate(doc.text,options)
    assert output==tmp_path/'source_voice.m4b'
    tags=MP4(str(output)).tags
    assert tags['\xa9nam']==['Original title']
    assert tags['\xa9ART']==['Author A, Author B']
    assert tags['covr']
    probe=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_format','-show_chapters','-of','json',str(output)]))
    assert probe['format']['tags']['major_brand']=='M4B '
    assert [c['tags']['title'] for c in probe['chapters']]==['TOC One','TOC Two']
    saved=store.get_audiobook(book.id)
    assert saved.title=='Project1'
    assert saved.project_dir==project


def test_ui_import_applies_metadata_before_replacing_text(tmp_path):
    from unittest.mock import Mock, patch
    from app.ui.main_window import MainWindow
    source=make_epub(tmp_path)
    book=SimpleNamespace(title='Existing project',project_dir=tmp_path/'project')
    window=SimpleNamespace(tr=lambda key,default:default,current_audiobook_id=7,
        settings={'book_metadata':{'author':'Old author'}},audiobook_store=Mock(),
        text_editor=Mock(),_mark_project_dirty=Mock(),_show_original_text_tab=Mock(),
        log_view=Mock(),_show_error=Mock())
    window.audiobook_store.get_audiobook.return_value=book
    with patch('app.ui.main_window.QFileDialog.getOpenFileName',return_value=(str(source),'')):
        MainWindow._import_document(window)
    window._show_error.assert_not_called()
    assert window.settings['book_metadata']['title']=='Original title'
    assert window.settings['document_source']['path']==str(source.resolve())
    assert '# TOC One' in window.text_editor.setPlainText.call_args.args[0]
    window._mark_project_dirty.assert_called_once()


def test_preferences_survive_settings_save_with_safe_defaults(tmp_path):
    from app.core.settings_manager import SettingsManager
    manager=SettingsManager(tmp_path/'config.json')
    settings=manager.load()
    assert settings['save_next_to_source'] is False
    assert settings['use_source_filename'] is False
    settings.update(save_next_to_source=True,use_source_filename=True)
    manager.save(settings)
    restored=manager.load()
    assert restored['save_next_to_source'] is True
    assert restored['use_source_filename'] is True


def test_plain_import_keeps_manually_entered_book_properties(tmp_path):
    path=tmp_path/'chapter.md';path.write_text('New chapter text')
    settings={'book_metadata':{'author':'Manual author','title':'Manual title','title_follows_project':False}}
    result=settings_for_document(settings,ProjectManager.load_document(path),path,'Project1',tmp_path/'project')
    assert result['book_metadata']['author']=='Manual author'
    assert result['book_metadata']['title']=='Manual title'


def test_ui_import_error_leaves_previous_text_and_settings(tmp_path):
    from unittest.mock import Mock, patch
    from app.ui.main_window import MainWindow
    source=tmp_path/'bad.epub';source.write_bytes(b'Not an EPUB')
    original={'book_metadata':{'author':'Keep me'}}
    window=SimpleNamespace(tr=lambda key,default:default,current_audiobook_id=7,
        settings=original,text_editor=Mock(),_show_error=Mock())
    with patch('app.ui.main_window.QFileDialog.getOpenFileName',return_value=(str(source),'')):
        MainWindow._import_document(window)
    window._show_error.assert_called_once()
    window.text_editor.setPlainText.assert_not_called()
    assert window.settings is original
