"""Conservative conversion of a Review draft between synthesis engines.

Only semantic controls cross the boundary. Sampling settings, model paths and
speaker IDs always come from the destination, never from the source engine.
"""
from copy import deepcopy
import re

from app.tts.audiocpp_config import BREEZE_MODEL, VOXCPM_MODELS, MODELS
from app.utils.paths import resolve_large_asset_path

POST_SPEED_ENGINES = {'chatterbox', 'audiocpp', 'qwen', 'indextts', 'omnivoice', 'gemini', 'elevenlabs'}
LANGUAGE_NAMES = dict(en='English', es='Spanish', zh='Chinese', ja='Japanese', ko='Korean',
                      de='German', fr='French', ru='Russian', pt='Portuguese', it='Italian', hi='Hindi')


def language_code(value):
    key = str(value).casefold().replace('_', '-').split('-')[0]
    return {name.casefold(): code for code, name in LANGUAGE_NAMES.items()}.get(key, key)


def identity(config):
    return config.get('engine'), config.get('model', '')


def clones(config):
    engine = config.get('engine')
    return engine in {'audiocpp', 'indextts', 'omnivoice', 'f5_russian', 'chatterbox'} or (
        engine == 'qwen' and config.get('generation_mode') == 'voice_clone')


def instruction_key(config):
    engine = config.get('engine')
    if engine == 'audiocpp' and config.get('model') in {BREEZE_MODEL, *VOXCPM_MODELS}:
        return 'instruction'
    if engine == 'indextts':
        return 'emo_text'
    if engine == 'qwen' and config.get('generation_mode') != 'voice_clone':
        return 'instruct'
    return None


def migrate(text, source, destination):
    config = deepcopy(destination)
    notices = ['segment_engine_reset']
    # A destination's globally selected reference must not silently replace this character.
    for key in ('voices', 'model_settings', 'voice_id', 'emotion_prompt', 'emotion_revision', 'emotion_source'):
        config.pop(key, None)
    if clones(config):
        for key in ('reference_audio_path', 'reference_text'):
            config[key] = source.get(key, '')
        config.pop('voice', None)
        if config['engine'] == 'omnivoice':
            config['mode'] = 'clone'
        notices.append('segment_engine_reference' if config['reference_audio_path'] else 'segment_engine_choose_reference')
    else:
        for key in ('reference_audio_path', 'reference_text'):
            config.pop(key, None)
        if config['engine'] == 'kokoro':
            config['voice'] = ''
        elif config['engine'] == 'qwen' and config.get('generation_mode') == 'custom_voice':
            config['speaker'] = ''
        notices.append('segment_engine_choose_voice')
    language = source.get('language') or source.get('lang') or source.get('locale')
    if language:
        config['language' if config['engine'] != 'kokoro' else 'lang'] = language
        if config['engine'] == 'qwen':
            config['language'] = LANGUAGE_NAMES.get(language_code(language), language)
        elif config['engine'] == 'chatterbox':
            config['language'] = language_code(language)
    speed = source.get('_postprocess_speed', source.get('speed', 1.0))
    config['speed'] = speed
    config.pop('_postprocess_speed', None)
    if config['engine'] in POST_SPEED_ENGINES:
        config['_postprocess_speed'] = speed
    for key in ('_postprocess_volume_db', '_postprocess_normalize_lufs'):
        if key in source:
            config[key] = source[key]
    description = source.get(instruction_key(source)) if instruction_key(source) else None
    target_key = instruction_key(config)
    if config['engine'] == 'indextts':
        config.update(emo_vector=[0.0] * 8, emo_audio_prompt='', use_random=False)
    if config['engine'] == 'omnivoice' or (config['engine'] == 'qwen' and clones(config)):
        config.pop('instruct', None)
    if config['engine'] == 'qwen' and clones(config):
        config.pop('speaker', None)
    if target_key:
        config[target_key] = description or ''
        if target_key == 'emo_text':
            config['emotion_mode'] = 'text' if description else 'reference'
    if description:
        notices.append('segment_engine_emotion_kept' if target_key else 'segment_engine_emotion_removed')
    if source.get('engine') == 'indextts' and source.get('emotion_mode') in {'vector', 'audio', 'auto'}:
        notices.append('segment_engine_emotion_removed')
    original_text = text
    if source.get('engine') == 'chatterbox':
        from app.tts.chatterbox_tags import NATIVE_TAG_PATTERN
        text = re.sub(NATIVE_TAG_PATTERN, '', text)
    if source.get('engine') == 'audiocpp' and source.get('model') == BREEZE_MODEL:
        from app.tts.breeze_tags import TAG_PATTERN
        text = re.sub(TAG_PATTERN, '', text)
    if text != original_text:
        notices.append('segment_engine_tags_removed')
    return text.strip(), config, list(dict.fromkeys(notices))


def validate_destination(config, source_language=None):
    """Cheap preflight; actual engine validation still runs in the host."""
    engine, model = identity(config)
    language = language_code(config.get('language') or config.get('lang') or '')
    allowed = None
    if engine == 'f5_russian':
        allowed = {'ru'}
    elif engine == 'audiocpp' and model == BREEZE_MODEL:
        allowed = {'en', 'zh'}
    elif engine == 'chatterbox' and model in {'turbo', 'original'}:
        allowed = {'en'}
    elif engine == 'qwen':
        allowed = {'en', 'es', 'zh', 'ja', 'ko', 'de', 'fr', 'ru', 'pt', 'it', 'auto'}
    if allowed and language not in allowed:
        raise ValueError('segment_engine_language')
    if engine in {'piper', 'kokoro'} and source_language and language_code(source_language) not in {'auto', '', language}:
        raise ValueError('segment_engine_voice_language')
    if engine == 'kokoro' and not config.get('voice'):
        raise ValueError('segment_engine_choose_voice')
    if engine == 'qwen' and config.get('generation_mode') == 'custom_voice' and not config.get('speaker'):
        raise ValueError('segment_engine_choose_voice')
    reference = str(config.get('reference_audio_path', '')).strip()
    required = engine in {'indextts', 'f5_russian', 'omnivoice'} or (
        engine == 'qwen' and config.get('generation_mode') == 'voice_clone') or (
        engine == 'audiocpp' and MODELS[model].requires_reference)
    if (required or reference) and (not reference or not resolve_large_asset_path(reference).is_file()):
        raise ValueError('segment_engine_missing_reference')
    if reference and (engine in {'qwen', 'f5_russian'} or model == BREEZE_MODEL) and not str(config.get('reference_text', '')).strip():
        raise ValueError('segment_engine_missing_transcript')
    if reference and engine == 'audiocpp':
        import wave
        try:
            with wave.open(str(resolve_large_asset_path(reference)), 'rb') as audio:
                if not audio.getnframes() or audio.getcomptype() != 'NONE':
                    raise ValueError()
        except (OSError, EOFError, ValueError, wave.Error) as exc:
            raise ValueError('segment_engine_invalid_wav') from exc
    if engine == 'piper' and any(not config.get(k) or not resolve_large_asset_path(config[k]).is_file() for k in ('model_path', 'config_path')):
        raise ValueError('segment_engine_choose_voice')
