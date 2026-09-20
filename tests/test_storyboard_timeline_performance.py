import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import time
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
from PySide6.QtCore import QThreadPool
from app.ui.storyboard_thumbnail_cache import StoryboardThumbnailCache, _POOL
from app.ui.video_storyboard_frame_batch_dialog import parse_scene_selection
from app.ui.video_storyboard_page import VideoStoryboardPage

APP = QApplication.instance() or QApplication([])
def tr(key, default, **kw): return default.format(**kw)
def wait_cache(cache):
    deadline = time.monotonic() + 5
    while cache._pending and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.005)
    assert not cache._pending

def make_image(path, color):
    image=QImage(1280,720,QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    assert image.save(str(path))

@pytest.mark.parametrize('text,expected', [('3-10',list(range(2,10))),('3,7,8,10',[2,6,7,9]),(' 5-8,3,7 ',[2,4,5,6,7])])
def test_selection(text, expected): assert parse_scene_selection(text,10)==expected

@pytest.mark.parametrize('text',['','0','11','8-3','1,,3','1.5','1-1000000000','-2'])
def test_invalid_selection(text):
    with pytest.raises(ValueError): parse_scene_selection(text,10)

@pytest.mark.parametrize('overwrite',[False,True])
def test_selected_payload_keeps_other_scenes(tmp_path, overwrite):
    image=tmp_path/'frame.png';make_image(image,'red')
    page=VideoStoryboardPage(tr)
    page.set_scenes([{'id':f'id-{n}', 'duration':15, 'image_path':str(image)} for n in range(1,11)])
    emitted=[]; page.generateFramesRequested.connect(emitted.append)
    page._open_frame_batch_dialog('generate'); dialog=page._frame_batch_dialog
    assert dialog.all_radio.isChecked()
    dialog.scenes_radio.setChecked(True)
    dialog.scene_selection_edit.setText('0');dialog._start()
    assert not emitted and not dialog._running
    dialog.scene_selection_edit.setText('3,7-8,10')
    dialog.overwrite_checkbox.setChecked(overwrite);dialog._start()
    assert [s['scene_id'] for s in emitted[0]['scenes']]==['id-3','id-7','id-8','id-10']
    assert all(s['image_path']==('' if overwrite else str(image)) for s in emitted[0]['scenes'])
    assert len(page.project_state()['scenes'])==10
    assert all(s['image_path']==str(image) for s in page.project_state()['scenes'])
    page.set_frame_generated('id-3',str(image))
    assert dialog.progress_bar.value()==25
    dialog._running=False; page.close();page.deleteLater()

def test_thumbnail_persistence_budget_and_source_change(tmp_path):
    first=tmp_path/'a.png';second=tmp_path/'b.png'
    make_image(first,'red');make_image(second,'blue')
    cache=StoryboardThumbnailCache(budget_bytes=320*180*4)
    cache.set_project_dir(str(tmp_path))
    assert cache.request(str(first)).isNull();wait_cache(cache)
    assert cache.request(str(first)).size().width()==320
    saved=list((tmp_path/'storyboard'/'thumbnails').glob('*.png'))
    assert len(saved)==1
    cache.request(str(second));wait_cache(cache)
    assert len(cache._items)==1 and cache.memory_bytes<=cache.budget_bytes
    # Disk cache is reused even when decoding the original is unavailable.
    with patch('app.ui.storyboard_thumbnail_cache.QImageReader',side_effect=AssertionError('Decoded original again')):
        cache.request(str(first));wait_cache(cache)
    assert not cache.request(str(first)).isNull()
    make_image(first,'green')
    stat=first.stat();os.utime(first,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    assert cache.request(str(first)).isNull();wait_cache(cache)
    assert cache.request(str(first)).toImage().pixelColor(0,0)==QColor('green')

def test_project_switch_ignores_inflight_thumbnail(tmp_path):
    source=tmp_path/'a.png';make_image(source,'red')
    cache=StoryboardThumbnailCache();cache.set_project_dir(str(tmp_path/'old'))
    cache.request(str(source));cache.set_project_dir(str(tmp_path/'new'));wait_cache(cache)
    assert not cache._items
    cache.request(str(source));wait_cache(cache)
    assert list((tmp_path/'new'/'storyboard'/'thumbnails').glob('*.png'))

def test_timeline_only_paints_visible_scenes():
    page=VideoStoryboardPage(tr)
    page.set_scenes([{'id':str(n),'duration':15} for n in range(1000)])
    page.resize(1000,800);page.show();APP.processEvents()
    canvas=page.timeline_canvas
    with patch.object(canvas,'_paint_scene',wraps=canvas._paint_scene) as paint:
        canvas.repaint();APP.processEvents()
        assert 0 < paint.call_count < 10
    page.close();page.deleteLater()

def test_selected_batch_checkpoint_preserves_unselected(tmp_path, monkeypatch):
    from copy import deepcopy
    from app.core.video_storyboard_project import save_storyboard_state, load_storyboard_state
    from app.workers.video_storyboard_frame_worker import VideoStoryboardFrameWorker
    old=tmp_path/'old.png';make_image(old,'red')
    baseline={'source':{'text':'Story'},'plan':{},'scenes':[
        {'scene_id':str(n),'prompt':f'Scene {n}','image_path':str(old)} for n in range(1,5)]}
    save_storyboard_state(tmp_path,baseline)
    payload=deepcopy(baseline);payload['scenes']=[payload['scenes'][2]];payload['scenes'][0]['image_path']=''
    monkeypatch.setattr('app.workers.video_storyboard_frame_worker.prepare_image_runtime',lambda *a,**k:{})
    def generate(scene,plan,settings,target,**kwargs):
        make_image(target,'blue');return {}
    monkeypatch.setattr('app.workers.video_storyboard_frame_worker.generate_storyboard_frame',generate)
    worker=VideoStoryboardFrameWorker(payload,{},tmp_path/'frames',checkpoint_project_dir=tmp_path,checkpoint_state=baseline)
    worker.run()  # Recover without receiving any GUI signals.
    restored=load_storyboard_state(tmp_path)
    assert len(restored['scenes'])==4
    assert [s['image_path'] for i,s in enumerate(restored['scenes']) if i!=2]==[str(old)]*3
    assert restored['scenes'][2]['image_path']!=str(old)
    assert Path(restored['scenes'][2]['image_path']).is_file()
