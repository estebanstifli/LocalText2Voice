from unittest.mock import Mock

import pytest

from app.core.audio_pipeline import AudioPipeline
from app.core.ltv_markup import LTVMarkupCompiler, LTVMarkupParser
from app.core.text_processor import TextChunk
from app.tts.indextts_config import inference_arguments
from app.tts.indextts_emotions import EMOTION_PRESETS, emotion_preset


@pytest.mark.parametrize("name", EMOTION_PRESETS)
def test_fixed_vectors_and_single_intensity_scaling(name):
    config = emotion_preset(name, "70%")
    assert len(config["emo_vector"]) == 8
    assert sum(config["emo_vector"]) <= 0.800001
    args = inference_arguments(config, "Texto.", "out.wav")
    assert args["emo_vector"] == list(EMOTION_PRESETS[name])
    assert args["emo_alpha"] == 0.7
    assert "use_emo_text" not in args


@pytest.mark.parametrize(
    "command", ["emotion tristeza 70%", "emotion.sad 0.7", 'emotion "sad" 0.7']
)
def test_preset_overrides_global_text_and_resets_without_changing_voice(command):
    source = "{{" + command + "}}Triste.{{emotion off}}Normal.{{reset}}Otra frase."
    result = LTVMarkupCompiler.compile(source, "indextts")
    assert not result.warnings
    pipeline = AudioPipeline(Mock())
    base = {
        "engine": "indextts",
        "emotion_mode": "text",
        "emo_text": "Happy",
        "instruct": "Excited",
        "reference_audio_path": "voice.wav",
    }
    configs = [
        pipeline._voice_config_for_chunk(
            base,
            TextChunk(
                text=s.text,
                ends_paragraph=True,
                markup_state=s.state,
            ),
        )
        for s in result.sections[0].segments
    ]
    assert configs[0]["emotion_mode"] == "vector"
    assert "instruct" not in configs[0]
    assert inference_arguments(configs[0], "Triste.", "out.wav")["emo_alpha"] == 0.7
    assert configs[1]["emotion_mode"] == "reference"
    assert "use_emo_text" not in inference_arguments(configs[1], "Normal.", "out.wav")
    assert configs[2] == base
    assert all(c["reference_audio_path"] == "voice.wav" for c in configs)
    assert base["instruct"] == "Excited"


@pytest.mark.parametrize(
    "value",
    ["unknown", "sad nan", "sad 101%", "sad -1", "sad 2", "sad abc", "sad 0.2 extra"],
)
def test_invalid_emotion_has_visible_warning(value):
    result = LTVMarkupParser.parse("{{emotion " + value + "}}Texto.")
    assert result.warnings


def test_other_engines_ignore_command_and_keep_script():
    result = LTVMarkupCompiler.compile("{{emotion sad}}Hola.", "qwen")
    assert result.ignored_commands == ["{{emotion sad}}"]
    assert result.sections[0].segments[0].text == "Hola."
    assert "emotion" not in result.sections[0].segments[0].state


def test_reset_before_text_clears_pending_preset():
    result = LTVMarkupCompiler.compile("{{emotion sad}}{{reset}}Hola.", "indextts")
    assert "emotion" not in result.sections[0].segments[0].state


def test_custom_quoted_description_and_intensity_are_segment_state():
    result = LTVMarkupCompiler.compile(
        '{{emotion custom "Simpática y enérgica" 90%}}Hola. Qué alegría.{{emotion off}}Normal.',
        "indextts",
    )
    assert not result.warnings
    first, second = result.sections[0].segments
    assert first.state["emotion"]["emo_text"] == "Simpática y enérgica"
    assert first.state["emotion"]["emotion_mode"] == "text"
    assert first.state["emotion"]["emotion_source"] == "custom"
    assert first.state["emotion"]["emo_alpha"] == 0.9
    assert second.state["emotion"]["emotion_mode"] == "reference"


@pytest.mark.parametrize(
    "value", ["custom", 'custom ""', 'custom "hi" 110%', 'custom "hi" 0.5 extra']
)
def test_invalid_custom_description_has_warning(value):
    assert LTVMarkupParser.parse("{{emotion " + value + "}}Texto.").warnings


def test_last_emotion_or_instruction_wins():
    source = '{{emotion sad}}{{cmd "instruct": "Happy"}}Hola.'
    state = LTVMarkupCompiler.compile(source, "indextts").sections[0].segments[0].state
    assert "emotion" not in state
    assert state["config_overrides"]["instruct"] == "Happy"


@pytest.mark.parametrize(
    "command", ["{{emotion}}", "{{emotion neutral}}", "{{emotion happy 0}}"]
)
def test_neutral_and_zero_use_reference(command):
    state = (
        LTVMarkupCompiler.compile(command + "Hola.", "indextts")
        .sections[0]
        .segments[0]
        .state
    )
    args = inference_arguments(state["emotion"], "Hola.", "out.wav")
    assert "emo_vector" not in args and "use_emo_text" not in args
