from copy import deepcopy
import pytest
from app.core.storyboard_object_discovery import unify_object_reports, object_addition_text
from app.core import storyboard_conversation as c
from app.core import video_storyboard_planner as p
from tests.test_storyboard_conversation import SOURCE, fake_backend


def test_objects_compare_against_accumulated_registry_without_mutating_reports():
    reports=[{'objects':'Portrait of Dorian Gray: painted canvas.'},
             {'objects':'The aging painting of Dorian. Brass key.'},
             {'objects':'The canvas has moved upstairs. The same key.'},
             {'objects':'A separate portrait of Basil.'}]
    original=deepcopy(reports);calls=[];results=iter(['Brass key: brass.','NONE','Portrait of Basil: painted canvas.'])
    def ask(history,prompt,label):
        calls.append(prompt)
        assert 'Portrait of Dorian Gray' in prompt.split('SECOND TEXT:')[0]
        if len(calls)>1: assert 'Brass key' in prompt.split('SECOND TEXT:')[0]
        return next(results)
    summary=unify_object_reports(reports,ask)
    assert summary.count('Portrait of Dorian Gray')==1
    assert summary.count('Brass key')==1
    assert 'Portrait of Basil' in summary
    assert reports==original

@pytest.mark.parametrize('answer',['NONE','None.','**NONE**','No new objects.','Ninguno.'])
def test_no_new_objects(answer): assert object_addition_text(answer)==''


def test_unified_objects_review_json_and_resume(monkeypatch):
    from app.core import storyboard_combined_discovery as discovery
    fake_backend(monkeypatch)
    free, structured = p._request_free_text,p._request_plan
    monkeypatch.setattr(discovery,'balanced_passages',lambda text,limit: [text[:22],text[22:]])
    calls=[];drafts=[]
    def ask(*args,**kw):
        label=kw['request_label'];calls.append(label)
        if 'important objects summary' in label:
            return 'Portrait of Dorian Gray: canvas.' if '1/2' in label else 'The aging painting of Dorian Gray: canvas.'
        if 'new important objects' in label: return 'NONE'
        return free(*args,**kw)
    monkeypatch.setattr(p,'_request_free_text',ask)
    class ReviewStop(Exception): pass
    def review(draft):
        drafts.append(draft)
        raise ReviewStop()
    settings={'analysis_choices':{'plan':'scenes','objects':True,'review':True}}
    with pytest.raises(ReviewStop): c.plan_conversation(SOURCE,settings,review=review)
    draft=drafts[0]
    assert draft['original']['objects']=='Portrait of Dorian Gray: canvas.'
    assert draft['unified_objects']==draft['original']['objects']
    assert len(draft['reports'])==2
    calls.clear()
    class JsonStop(Exception): pass
    def convert(settings,schema,system,user,**kw):
        if schema==c.OBJECT_SCHEMA:
            assert user=='Portrait of Dorian Gray: canvas.'
            raise JsonStop()
        return structured(settings,schema,system,user,**kw)
    monkeypatch.setattr(p,'_request_plan',convert)
    with pytest.raises(JsonStop):
        c.plan_conversation(SOURCE,{**settings,'review_checkpoint':draft},review=lambda d:d)
    assert not calls  # Reuse the merged summary when resuming.


def test_unified_locations_review_json_and_resume(monkeypatch):
    from app.core import storyboard_combined_discovery as discovery
    fake_backend(monkeypatch)
    free, structured = p._request_free_text,p._request_plan
    monkeypatch.setattr(discovery,'balanced_passages',lambda text,limit: [text[:22],text[22:]])
    calls=[];drafts=[]
    def ask(*args,**kw):
        label=kw['request_label'];calls.append(label)
        if 'place summary' in label:
            return 'Dorian house salon: blue walls.' if '1/2' in label else 'Drawing room of Dorian house: blue walls.'
        if 'new locations' in label: return 'NONE'
        return free(*args,**kw)
    monkeypatch.setattr(p,'_request_free_text',ask)
    class ReviewStop(Exception): pass
    def review(draft):
        drafts.append(draft)
        raise ReviewStop()
    settings={'analysis_choices':{'plan':'scenes','locations':True,'review':True}}
    with pytest.raises(ReviewStop): c.plan_conversation(SOURCE,settings,review=review)
    draft=drafts[0]
    assert draft['original']['locations']=='Dorian house salon: blue walls.'
    assert draft['unified_locations']==draft['original']['locations']
    assert len(draft['reports'])==2
    calls.clear()
    class JsonStop(Exception): pass
    def convert(settings,schema,system,user,**kw):
        if schema==c.LOCATION_SCHEMA:
            assert user=='Dorian house salon: blue walls.'
            raise JsonStop()
        return structured(settings,schema,system,user,**kw)
    monkeypatch.setattr(p,'_request_plan',convert)
    with pytest.raises(JsonStop):
        c.plan_conversation(SOURCE,{**settings,'review_checkpoint':draft},review=lambda d:d)
    assert not calls  # Reuse the merged summary when resuming.


def test_location_identity_and_distinct_rooms():
    from app.core.storyboard_object_discovery import unify_location_reports
    reports=[{'locations':'Dorian salon: blue walls.'}, {'locations':'His drawing room at night.'},
             {'locations':'Dorian kitchen: stone floor.'}, {'locations':'The same kitchen in daylight.'}]
    calls=[];answers=iter(['NONE','Dorian kitchen: stone floor.','NONE'])
    def ask(history,prompt,label):
        calls.append(prompt)
        if len(calls)==3: assert 'Dorian kitchen' in prompt.split('SECOND TEXT:')[0]
        return next(answers)
    assert unify_location_reports(reports,ask)=='Dorian salon: blue walls.\n\nDorian kitchen: stone floor.'
