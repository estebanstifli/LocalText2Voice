import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from PIL import Image
from app.ui.storyboard_entity_scenes import EntityScenesPanel, matching_scenes
from app.ui.video_storyboard_entity_dialog import VideoStoryboardEntityDialog
APP=QApplication.instance() or QApplication([])
def tr(k,d,**v): return d.format(**v)
class Page(QObject):
    scenesChanged=Signal(object)
    projectChanged=Signal(object)
    def __init__(self,rows,path):
        super().__init__();self.rows=rows;self._source_project_dir=str(path)
    def scenes(self):return self.rows

@pytest.mark.parametrize('kind',['character','location','object'])
def test_live_appearances_and_preview(tmp_path,kind):
    record={'id':'entity','name':'Entity','aliases':['Alias'],'states':[{'id':'state1'},{'id':'state2'}]}
    field=kind+'s'
    rows=[{'scene_id':'a',field:['state1','entity'],'duration':15},
          {'scene_id':'b',field:[],'duration':15},
          {'scene_id':'c',field:['Alias'],'duration':15},
          {'scene_id':'d','generation_overrides':{kind+'_state_ids':['state2']},'duration':15}]
    page=Page(rows,tmp_path)
    dialog=VideoStoryboardEntityDialog(tr,kind,60,record,storyboard_page=page)
    panel=dialog.scene_appearances
    assert panel.list.count()==3
    assert panel.list.item(1).text()=='Scene 3 · 00:00:30'
    panel._open(panel.list.item(0));APP.processEvents()
    assert panel.preview.pixmap.isNull()
    assert panel.preview.image.text()=='Frame not generated'
    image=tmp_path/'new.png';Image.new('RGB',(640,360),'red').save(image)
    rows[0]['image_path']=str(image);page.scenesChanged.emit(rows)
    assert not panel.preview.pixmap.isNull()
    assert panel.list.item(0).data(256)[2]['image_path']==str(image)
    rows[0][field]=[];page.scenesChanged.emit(rows)
    assert panel.list.count()==2 and panel.preview is None
    dialog.close();dialog.deleteLater();APP.processEvents()


def test_wrap_scroll_and_pending_frames(tmp_path):
    page=Page([{'scene_id':str(i),'characters':['x'],'duration':15} for i in range(35)],tmp_path)
    panel=EntityScenesPanel(tr,{'id':'x'},'character',page)
    panel.resize(740,400);panel.show();APP.processEvents()
    first=panel.list.visualItemRect(panel.list.item(0))
    second=panel.list.visualItemRect(panel.list.item(1))
    fourth=panel.list.visualItemRect(panel.list.item(3))
    assert first.top()==second.top() and second.left()>first.left()
    assert fourth.top()>first.top()
    assert panel.list.verticalScrollBar().maximum()>0
    panel.list.verticalScrollBar().setValue(120)
    page.scenesChanged.emit(page.rows)
    assert panel.list.verticalScrollBar().value()==120
    panel.resize(500,400);APP.processEvents()
    assert panel.list.visualItemRect(panel.list.item(2)).top()>panel.list.visualItemRect(panel.list.item(0)).top()
    panel.close();panel.deleteLater()
