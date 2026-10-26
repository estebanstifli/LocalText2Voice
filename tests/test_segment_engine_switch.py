import os
from copy import deepcopy
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication

from app.core.segment_engine import migrate, validate_destination
from app.tts.audiocpp_config import model_config, BREEZE_MODEL
from app.tts.indextts_config import DEFAULTS as INDEX_DEFAULTS
from app.ui.segment_detail_dialog import SegmentDetailDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def reference(tmp_path):
    p = tmp_path / 'voice.wav'
    with wave.open(str(p), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b'\0\0' * 240)
    return str(p)


def vox(**kw):
    return {**model_config({'model': 'voxcpm2_orig'}), 'engine': 'audiocpp', 'language': 'es', **kw}


def test_switch_rebuilds_fields_and_restores_draft_without_mutating_input(app, reference):
    source = vox(reference_audio_path=reference, reference_text='Hola.', instruction='Softly', num_inference_steps=22)
    untouched = deepcopy(source)
    target = {'engine': 'indextts', **deepcopy(INDEX_DEFAULTS)}
    options = [{'label': 'Index', 'config': target, 'voices': []}]
    dialog = SegmentDetailDialog('Hola.', source, [], lambda k,d:d, engine_options=options)
    dialog.fields['instruction'].setPlainText('Sad and quiet')
    dialog.engine_combo.setCurrentIndex(1)
    assert dialog.engine == 'indextts'
    assert 'num_inference_steps' not in dialog.fields
    assert len(dialog.vector_fields) == 8
    text, cfg = dialog.values()
    assert cfg['emo_text'] == 'Sad and quiet'
    assert cfg['reference_audio_path'] == reference
    assert 'instruction' not in cfg and 'num_inference_steps' not in cfg
    dialog.engine_combo.setCurrentIndex(0)
    assert dialog.fields['instruction'].toPlainText() == 'Sad and quiet'
    assert dialog.fields['num_inference_steps'].value() == 22
    assert source == untouched and target == options[0]['config']
    dialog.reject()
    dialog.deleteLater()


def test_breeze_rejects_spanish_and_missing_transcript(app, reference):
    target = {'engine': 'audiocpp', **model_config({'model': BREEZE_MODEL})}
    dialog = SegmentDetailDialog('Hola', vox(reference_audio_path=reference), [], lambda k,d:d,
                                 engine_options=[{'label':'Breeze','config':target,'voices':[]}])
    dialog.engine_combo.setCurrentIndex(1)
    with pytest.raises(ValueError, match='language'):
        dialog.values()
    dialog.fields['language'].setText('en')
    with pytest.raises(ValueError, match='transcript'):
        dialog.values()
    dialog.fields['reference_text'].setText('Hola')
    assert dialog.values()[1]['model'] == BREEZE_MODEL
    dialog.deleteLater()


def test_no_foreign_fields_secrets_or_double_speed_in_native_speed_engine():
    source = vox(instruction='Sad', api_key='secret', voices={'x':1}, _postprocess_speed=1.2)
    text, cfg, notices = migrate('Hello', source, {'engine':'kokoro','voice':'af_heart','lang':'en-us'})
    assert cfg['speed'] == 1.2 and '_postprocess_speed' not in cfg
    assert not {'instruction','api_key','voices','reference_audio_path','num_inference_steps'} & cfg.keys()
    assert 'segment_engine_emotion_removed' in notices


@pytest.mark.parametrize('source,text,expected', [
    ({'engine':'chatterbox','model':'turbo'}, '[happy] Hello [laugh] (a real aside).', 'Hello  (a real aside).'),
    ({'engine':'audiocpp','model':BREEZE_MODEL}, 'Hello (laugh) (a real aside).', 'Hello  (a real aside).'),
])
def test_native_tags_removed_but_ordinary_asides_preserved(source, text, expected):
    cleaned, _, notices = migrate(text, source, vox())
    assert cleaned == expected
    assert 'segment_engine_tags_removed' in notices


def test_qwen_clone_requires_reference_and_transcript(reference):
    target = {'engine':'qwen','model':'base_1_7b','generation_mode':'voice_clone','speaker':'Serena','instruct':'global style'}
    _, cfg, _ = migrate('Hola', vox(), target)
    assert cfg['language'] == 'Spanish' and 'instruct' not in cfg
    with pytest.raises(ValueError, match='missing_reference'):
        validate_destination(cfg)
    cfg['reference_audio_path'] = reference
    with pytest.raises(ValueError, match='missing_transcript'):
        validate_destination(cfg)
    cfg['reference_text'] = 'Hola'
    validate_destination(cfg)


def test_missing_and_invalid_wav_are_rejected(tmp_path):
    p = tmp_path / 'bad.wav'
    with pytest.raises(ValueError, match='missing_reference'):
        validate_destination(vox(reference_audio_path=str(p)))
    p.write_bytes(b'not audio')
    with pytest.raises(ValueError, match='invalid_wav'):
        validate_destination(vox(reference_audio_path=str(p)))


def test_can_leave_incomplete_draft(app):
    options = [{'label':'Index','config':{'engine':'indextts',**INDEX_DEFAULTS},'voices':[]}]
    dialog = SegmentDetailDialog('Hola', vox(), [], lambda k,d:d, engine_options=options)
    dialog.engine_combo.setCurrentIndex(1)
    mode = dialog.fields['emotion_mode']
    mode.setCurrentIndex(mode.findData('text'))
    dialog.text_edit.clear()
    dialog.engine_combo.setCurrentIndex(0)
    assert dialog.values()[0] == 'Hola'
    dialog.deleteLater()


def test_voice_language_mismatch():
    with pytest.raises(ValueError, match='voice_language'):
        validate_destination({'engine':'kokoro','voice':'af_heart','lang':'en-us'}, 'es')


def test_changed_engine_snapshot_reaches_host_and_keeps_old_audio(app, tmp_path, reference):
    import json
    from unittest.mock import Mock
    from pathlib import Path
    from app.core.audiobook_store import AudiobookStore
    from app.core.audio_pipeline import AudioGroup
    from app.core.text_processor import TextChunk
    from app.workers.verification_worker import EngineHostSegmentRegenerationWorker
    source = vox(reference_audio_path=reference, instruction='Tender and sad')
    target = {'engine':'indextts', **INDEX_DEFAULTS}
    text, cfg, _ = migrate('Hola.', source, target)
    store = AudiobookStore(tmp_path/'book.db')
    book = store.create_audiobook(text, source, tmp_path/'out', 'safe_chunks', 'single', 'Test', {}, tmp_path/'project')
    sid = store.replace_segments(book, [AudioGroup('Chapter', (TextChunk(text, True),))])[(1,1)]
    old = tmp_path/'old.wav'
    old.write_bytes(Path(reference).read_bytes())
    store.mark_segment_rendering(sid, source)
    store.mark_segment_rendered(sid, old, 100, 10)
    store.update_segment_details(sid, text, cfg)
    saved = store.get_segment(sid)
    client = Mock()
    def synthesize(payload):
        Path(payload['output_wav']).write_bytes(Path(reference).read_bytes())
        return {'output_wav':payload['output_wav']}
    client.synthesize_review_segment.side_effect = synthesize
    worker = EngineHostSegmentRegenerationWorker(client, sid, saved.source_text,
                json.loads(saved.engine_config_json), tmp_path/'candidate.wav')
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert not errors
    assert client.synthesize_review_segment.call_args.args[0]['voice_config']['engine'] == 'indextts'
    assert old.read_bytes() == Path(reference).read_bytes()
    assert store.get_segment(sid).wav_path == str(old)
    assert json.loads(store.get_audiobook(book.id).engine_config_json)['engine'] == 'audiocpp'


def test_only_installed_models_are_offered():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.ui.segment_engine_options import installed_options
    absent = Mock()
    absent.is_installed.return_value = False
    chatter = Mock()
    chatter.has_runtime.return_value = True
    chatter.list_models.return_value = [SimpleNamespace(model_id=m,display_name=m) for m in ('turbo','multilingual_v3')]
    chatter.has_model_files.side_effect = lambda m: m == 'multilingual_v3'
    qwen = Mock()
    qwen.list_models.return_value = [SimpleNamespace(model_id='base_1_7b',display_name='Base',kind='voice_clone')]
    qwen.is_installed.return_value = False
    cpp = Mock()
    cpp.is_installed.side_effect = lambda m,b: m == 'voxcpm2_orig' and b == 'cpu'
    gallery = Mock()
    gallery.list_voices.return_value = []
    window = SimpleNamespace(settings={}, voices=[], _tts_engine_is_installed=lambda _:False,
        kokoro_python_manager=absent,indextts_manager=absent,omnivoice_manager=absent,
        f5_russian_manager=absent,chatterbox_manager=chatter,qwen_manager=qwen,
        audiocpp_manager=cpp,voice_gallery_manager=gallery)
    options = installed_options(window)
    assert [(o['config']['engine'],o['config']['model']) for o in options] == [
        ('chatterbox','multilingual_v3'), ('audiocpp','voxcpm2_orig')]
