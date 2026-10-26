from unittest.mock import Mock
import wave

import pytest

from app.core.audio_pipeline import AudioPipeline, AudioGenerationOptions
from app.core.ltv_markup import LTVMarkupCompiler, LTVMarkupParser
from app.core.text_processor import TextChunk, TextProcessor
from app.tts.chatterbox_engine import ChatterboxTTSEngine
from app.tts.chatterbox_manager import ChatterboxManager
from app.tts.chatterbox_tags import EMOTION_TAGS, VOCAL_TAGS, prepare_text


@pytest.mark.parametrize("name", EMOTION_TAGS)
def test_turbo_emotions_and_styles_reach_engine(name):
    result = LTVMarkupCompiler.compile("{{emotion " + name + "}}Hello there.", "chatterbox")
    assert not result.warnings
    segment = result.sections[0].segments[0]
    base = {"engine": "chatterbox", "model": "turbo", "reference_audio_path": "voice.wav"}
    config = AudioPipeline(Mock())._voice_config_for_chunk(base, TextChunk(segment.text, True, markup_state=segment.state))
    assert prepare_text(segment.text, config, Mock()) == f"[{name}] Hello there."
    assert "chatterbox_emotion" not in base
    assert config["reference_audio_path"] == "voice.wav"


@pytest.mark.parametrize("name", VOCAL_TAGS)
def test_vocal_stays_inside_sentence_and_keeps_emotion(name):
    source = "{{emotion happy}}Hello {{vocal " + name + "}} my friend."
    result = LTVMarkupCompiler.compile(source, "chatterbox")
    assert not result.warnings
    segments = result.sections[0].segments
    assert len(segments) == 1
    assert segments[0].text == "Hello [" + name.replace("_", " ") + "] my friend."
    assert segments[0].state["emotion"] == {"chatterbox_emotion": "happy"}


@pytest.mark.parametrize("name", ["tristeza", "calm", "disgust", "melancholic", 'custom "Friendly"'])
def test_no_approximate_emotion_mapping(name):
    result = LTVMarkupCompiler.compile("{{emotion " + name + "}}Hello.", "chatterbox")
    assert result.ignored_commands
    assert "emotion" not in result.sections[0].segments[0].state
    assert result.sections[0].segments[0].text == "Hello."


@pytest.mark.parametrize("name,token", [("alegria", "happy"), ("enfado", "angry"), ("miedo", "fear"), ("sorpresa", "surprised")])
def test_shared_spanish_aliases(name, token):
    result = LTVMarkupCompiler.compile("{{emotion " + name + " 90%}}Hello.", "chatterbox")
    assert "percentage ignored" in result.warnings[0]
    assert result.sections[0].segments[0].state["emotion"]["chatterbox_emotion"] == token


def test_off_zero_and_reset_do_not_leak():
    result = LTVMarkupCompiler.compile("{{emotion happy}}Happy.{{emotion off}}Normal.{{emotion angry 0}}Normal again.{{emotion whispering}}{{reset}}Last.", "chatterbox")
    assert not result.warnings
    segments = result.sections[0].segments
    assert [s.state.get("emotion", {}).get("chatterbox_emotion") for s in segments] == ["happy", "off", "off", None]


@pytest.mark.parametrize("engine", ["indextts", "qwen", "piper"])
def test_unsupported_engines_never_speak_vocal_markup(engine):
    result = LTVMarkupCompiler.compile("Hello {{vocal laugh}} there.", engine)
    assert result.warnings
    assert result.sections[0].segments[0].text == "Hello there."


def test_index_rejects_turbo_only_style():
    result = LTVMarkupCompiler.compile("{{emotion whispering}}Hello.", "indextts")
    assert result.ignored_commands
    assert "emotion" not in result.sections[0].segments[0].state


@pytest.mark.parametrize("model", ["english", "multilingual_v3"])
def test_classic_models_warn_and_remove_native_tags(model):
    warn = Mock()
    text = prepare_text("Hello [laugh] there.", {"model": model, "chatterbox_emotion": "happy"}, warn)
    assert "[" not in text
    warn.assert_called_once()


@pytest.mark.parametrize("command", ["vocal nope", "vocal", "vocal laugh 90%", "emotion whispering 200%"])
def test_bad_commands_are_reported(command):
    assert LTVMarkupParser.parse("{{" + command + "}}Hello.").warnings


def test_multiword_vocal_is_not_split_across_chunks():
    chunks = TextProcessor.split_safe_chunks("Hello [clear throat] my old friend, I have some news.", max_chars=19)
    assert sum("[clear throat]" in chunk for chunk in chunks) == 1
    assert all(chunk.count("[") == chunk.count("]") for chunk in chunks)


def test_emotion_survives_long_segment_chunking(tmp_path):
    result = LTVMarkupCompiler.compile("{{emotion dramatic}}" + "This is a complete sentence. " * 30, "chatterbox")
    pipeline = AudioPipeline(Mock())
    options = AudioGenerationOptions(output_dir=tmp_path, voice_config={"engine":"chatterbox", "model":"turbo"}, ffmpeg_path="ffmpeg", chunk_size=100)
    chunks = [chunk for group in pipeline._prepare_markup_groups(result.sections, options) for chunk in group.chunks]
    assert len(chunks) > 1
    for chunk in chunks:
        config = pipeline._voice_config_for_chunk(options.voice_config, chunk)
        assert prepare_text(chunk.text, config, Mock()).startswith("[dramatic] ")


def test_worker_request_contains_native_tags_and_no_ignored_tuning(tmp_path):
    engine = ChatterboxTTSEngine(Mock())
    engine.validate = Mock()
    engine._ensure_worker = Mock(return_value=Mock())
    engine._send_request = Mock()
    output = tmp_path / "audio.wav"
    def write_worker_audio(*args):
        request = engine._send_request.call_args.args[1]
        with wave.open(request["output"], "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(480))

    engine._wait_for_response = Mock(side_effect=write_worker_audio)
    engine.synthesize_to_wav("Hello [chuckle] there.", output, {"model":"turbo", "chatterbox_emotion":"happy"})
    request = engine._send_request.call_args.args[1]
    assert request["text"] == "[happy] Hello [chuckle] there."
    assert request["exaggeration"] == request["cfg_weight"] == 0
    with wave.open(str(output), "rb") as audio:
        assert audio.getnframes() == 240


def test_inherited_hub_cache_cannot_hide_newly_installed_models(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "external-cache"))
    manager = ChatterboxManager(install_dir=tmp_path / "chatterbox")
    env = manager.runtime_environment()
    assert env["HF_HUB_CACHE"] == env["HUGGINGFACE_HUB_CACHE"] == str(manager.cache_dir / "hub")
