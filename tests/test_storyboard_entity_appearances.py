import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from app.ui.storyboard_analysis_sidebar import entity_scene_counts
from app.ui.video_storyboard_analysis_dialog import VideoStoryboardAnalysisDialog

APP = QApplication.instance() or QApplication([])
def tr(key, default, **values): return default.format(**values)

def plan():
    return {'continuity': {
        'locations': [{'id':'room','name':'Salon','aliases':['Living room'], 'states':[{'id':'room_day'},{'id':'room_night'}]},
                      {'id':'garden','name':'Garden','states':[]}],
        'objects': [{'id':'key','name':'Key','states':[{'id':'key_1'}]}]},
        'scenes': [
            {'locations':['room_day','ROOM','Living room'], 'objects':['Key'], 'generation_overrides':{'object_state_ids':['key_1']}},
            {'locations':['room_night'],'generation_overrides':{'object_state_ids':['key_1']}},
            {'locations':[], 'prompt':'A key in the garden'}]}

def test_counts_states_aliases_and_objects_once_per_scene():
    assert entity_scene_counts(plan(),'locations') == [('Salon',2),('Garden',0)]
    assert entity_scene_counts(plan(),'objects') == [('Key',2)]

def test_final_tabs_selected_categories_no_duplicate_tabs():
    dialog=VideoStoryboardAnalysisDialog(tr,analysis_choices={'locations':True,'objects':True})
    result=plan()
    dialog.update_plan(result)
    assert all(dialog.tabs.indexOf(t)==-1 for t in dialog.entity_summaries.values())
    dialog.update_plan(result,final=True)
    assert dialog.tabs.tabText(3)=='Character appearances'
    for category, name in [('locations','Salon'),('objects','Key')]:
        table=dialog.entity_summaries[category]
        assert dialog.tabs.indexOf(table)>3
        assert table.item(0,0).text()==name and table.item(0,1).text()=='2'
    count=dialog.tabs.count()
    dialog.update_plan(result,final=True)
    assert dialog.tabs.count()==count
    dialog.deleteLater()

def test_disabled_and_empty_categories():
    dialog=VideoStoryboardAnalysisDialog(tr,analysis_choices={'locations':False,'objects':False})
    dialog.entity_checks['locations'].setChecked(False)
    dialog.entity_checks['objects'].setChecked(False)
    result={'continuity':{'locations':[],'objects':[]},'scenes':[]}
    dialog.update_plan(result,final=True)
    assert all(dialog.tabs.indexOf(t)==-1 for t in dialog.entity_summaries.values())
    dialog.entity_checks['objects'].setChecked(True)
    dialog.update_plan(result,final=True)
    assert dialog.tabs.indexOf(dialog.entity_summaries['objects'])>=0
    assert dialog.entity_summaries['objects'].rowCount()==0
    dialog.entity_checks['objects'].setChecked(False)
    dialog.update_plan(result,final=True)
    assert dialog.tabs.indexOf(dialog.entity_summaries['objects'])==-1
    dialog.deleteLater()
