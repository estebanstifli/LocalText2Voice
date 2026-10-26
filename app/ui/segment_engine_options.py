"""Read installed local engines without changing the main engine selector."""
from copy import deepcopy

from app.core.settings_manager import DEFAULT_SETTINGS
from app.core.segment_engine import clones
from app.tts.audiocpp_config import MODELS, model_config
from app.utils.paths import resolve_large_asset_path


def installed_options(window):
    options = []

    def add(label, config):
        engine = config['engine']
        voices = []
        if engine == 'piper':
            voices = [(v.display_name, v.as_config(1.0)) for v in window.voices]
        elif engine == 'kokoro':
            voices = [(v.display_name, {'voice': v.voice_id, 'lang': v.language})
                      for v in window.kokoro_python_manager.list_voices()]
        elif engine == 'qwen' and not clones(config):
            voices = [(v.display_name, {'speaker': v.voice_id}) for v in window.qwen_manager.list_voices()]
        elif clones(config):
            for voice in window.voice_gallery_manager.list_voices(engine):
                if not voice.is_reference_audio:
                    continue
                path = resolve_large_asset_path(voice.installed_path or voice.ref_audio_path or '')
                if path.is_file():
                    voices.append((voice.name, {'reference_audio_path': str(path), 'reference_text': voice.ref_text}))
        options.append({'label': label, 'config': config, 'voices': voices})

    def settings(engine):
        return {**deepcopy(DEFAULT_SETTINGS.get(engine, {})), **deepcopy(window.settings.get(engine, {})), 'engine': engine}

    if window._tts_engine_is_installed('piper') and window.voices:
        add('Piper', {'engine': 'piper', 'model_path': '', 'config_path': '', 'speaker_id': 0, 'speed': 1.0})
    if window.kokoro_python_manager.is_installed():
        add('Kokoro', {**settings('kokoro'), 'model_path': str(window.kokoro_python_manager.model_path_for_provider('auto'))})
    for engine, label in [('indextts', 'IndexTTS-2.5'), ('omnivoice', 'OmniVoice'), ('f5_russian', 'F5-TTS Russian')]:
        if getattr(window, engine + '_manager').is_installed():
            add(label, settings(engine))
    if window.chatterbox_manager.has_runtime():
        for model in window.chatterbox_manager.list_models():
            if window.chatterbox_manager.has_model_files(model.model_id):
                add('Chatterbox · ' + model.display_name, {**settings('chatterbox'), 'model': model.model_id})
    for model in window.qwen_manager.list_models():
        if window.qwen_manager.is_installed(model.model_id):
            add('Qwen · ' + model.display_name, {**settings('qwen'), 'model': model.model_id, 'generation_mode': model.kind})
    config = settings('audiocpp')
    for model in MODELS.values():
        candidate = model_config(config, model.id)
        backends = list(dict.fromkeys([candidate['backend'], 'cuda', 'vulkan', 'cpu', 'metal']))
        for backend in backends:
            if window.audiocpp_manager.is_installed(model.id, backend):
                add(model.name, {**candidate, 'backend': backend})
                break
    return options
