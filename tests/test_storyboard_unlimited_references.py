import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PIL import Image
from app.core.video_storyboard_image_edit import _normalize_references, generate_edited_storyboard_image, VideoStoryboardImageEditError
from app.core.video_storyboard_comfyui import validate_generation_references
from app.ui.video_storyboard_reference_picker_dialog import VideoStoryboardReferencePickerDialog
from tests.test_storyboard_batch_resilience import run_worker, write_image
import pytest

APP=QApplication.instance() or QApplication([])
def refs(tmp_path):
    result=[]
    for i in range(20):
        path=tmp_path/f'{i}.png';Image.new('RGB',(8,8)).save(path)
        result.append({'path':str(path),'label':str(i)})
    return result

def test_selection_normalization_and_validation_keep_all(tmp_path):
    references=refs(tmp_path)
    assert len(_normalize_references(references))==20
    dialog=VideoStoryboardReferencePickerDialog(lambda k,d,**v:d.format(**v),{},references)
    assert len(dialog.selected_references())==20
    for settings in ({'image_provider':'litellm_image'},{'image_provider':'runpod','runpod':{'image_endpoint':'qwen-image-edit-2511'}}):
        validate_generation_references({'generation_overrides':{'reference_images':references}},settings)
    dialog.deleteLater()

def test_model_rejection_does_not_stop_batch(tmp_path):
    references=refs(tmp_path)
    def generate(scene,plan,settings,target,**kw):
        if scene['scene_id']=='001':
            assert len(scene['generation_overrides']['reference_images'])==20
            raise RuntimeError('HTTP 400: model accepts at most 16 input images')
        return write_image(scene,plan,settings,target)
    worker,ready,failed,finished,terminal,count=run_worker(tmp_path,generate,scenes=[
        {'scene_id':'001','generation_overrides':{'reference_images':references}}, {'scene_id':'002'}])
    assert count==2 and len(failed)==1 and len(ready)==1 and len(finished)==1 and not terminal
    assert '16 input images' in failed[0]['error']


def test_edit_sends_all_and_propagates_provider_error(tmp_path,monkeypatch):
    references=refs(tmp_path)
    def reject(references,*a,**kw):
        assert len(references)==20
        raise VideoStoryboardImageEditError('HTTP 400: too many images')
    monkeypatch.setattr('app.core.video_storyboard_image_edit._litellm_direct_edit',reject)
    with pytest.raises(VideoStoryboardImageEditError,match='too many images'):
        generate_edited_storyboard_image(references,'Edit',{'image_edit_provider':'litellm_image','litellm_image_edit':{'model':'openai/gpt-image-2'}},tmp_path/'output.png')
