from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.tts.base import TTSCancelled, TTSEngineError
from app.tts.indextts_cli import INDEXTTS_CLI
from app.tts.indextts_config import inference_arguments, validated_config
from app.tts.indextts_engine import IndexTTSTTSEngine
from app.tts.indextts_manager import IndexTTSCancelled, IndexTTSError, IndexTTSManager


@pytest.mark.parametrize(
    "mode,extra,expected",
    [
        ("reference", {}, {}),
        (
            "text",
            {"emo_text": "Restrained anger"},
            {"use_emo_text": True, "emo_text": "Restrained anger"},
        ),
        ("auto", {"emo_text": "ignored"}, {"use_emo_text": True, "emo_text": None}),
        (
            "audio",
            {"emo_audio_prompt": "emotion.wav"},
            {"emo_audio_prompt": "emotion.wav"},
        ),
        (
            "vector",
            {"emo_vector": [0, 0, 0.8, 0, 0, 0, 0, 0]},
            {"emo_vector": [0, 0, 0.8, 0, 0, 0, 0, 0]},
        ),
    ],
)
def test_emotion_routes_are_exclusive_and_do_not_change_script(mode, extra, expected):
    result = inference_arguments(
        {"emotion_mode": mode, **extra, "reference_audio_path": "voice.wav"},
        "Hola",
        "out.wav",
    )
    assert result["text"] == "Hola"
    assert result["spk_audio_prompt"] == "voice.wav"
    assert result["lang"] == "ES"
    assert {
        k: v
        for k, v in result.items()
        if k in {"use_emo_text", "emo_text", "emo_audio_prompt", "emo_vector"}
    } == expected


@pytest.mark.parametrize(
    "config",
    [
        {"device": "cpu"},
        {"dtype": "float16"},
        {"device": "auto"},
        {"language": "ru"},
        {"emo_alpha": float("nan")},
        {"emo_alpha": -1},
        {"duration_factor": 2.1},
        {"emo_vector": [0]},
        {"emo_vector": [0] * 7 + [float("inf")]},
        {"emo_vector": [1.21] + [0] * 7},
        {"top_k": 2.5},
        {"use_random": "false"},
    ],
)
def test_invalid_parameters_fail_before_inference(config):
    with pytest.raises((ValueError, TypeError)):
        validated_config(config)


@pytest.mark.parametrize("instruction", ["", "   "])
def test_empty_optional_emotion_uses_voice_reference(instruction):
    config = {"emotion_mode": "text", "emo_text": instruction}
    assert validated_config(config)["emotion_mode"] == "reference"
    args = inference_arguments(config, "Sin emoción marcada.", "out.wav")
    assert args["text"] == "Sin emoción marcada."
    assert (
        not {"use_emo_text", "emo_text", "emo_vector", "emo_audio_prompt"} & args.keys()
    )
    assert validated_config(config, require_inputs=False)["emotion_mode"] == "text"


def test_markup_instruction_alias_and_language():
    config = validated_config(
        {"instruct": "Sad but calm", "language": "Spanish", "emo_alpha": 0}
    )
    assert config["emotion_mode"] == "text"
    assert config["emo_text"] == "Sad but calm"
    assert config["emo_alpha"] == 0
    assert config["language"] == "ES"
    assert validated_config({"device": "cpu", "dtype": "float32"})["dtype"] == "float32"


def test_precision_check_does_not_silently_fallback():
    namespace = {"__name__": "test_worker"}
    exec(INDEXTTS_CLI, namespace)  # noqa: S102 - exercise the bundled worker source
    cuda = SimpleNamespace(
        is_available=lambda: True, is_bf16_supported=lambda **kw: False
    )
    torch = SimpleNamespace(cuda=cuda)
    with pytest.raises(RuntimeError, match="native BF16"):
        namespace["check_precision"](torch, "cuda", "bfloat16")
    namespace["check_precision"](torch, "cuda", "float32")


def test_runtime_requires_successful_manifest_and_complete_weights(tmp_path):
    manager = IndexTTSManager(tmp_path / "models", tmp_path / "deps")
    manager.REQUIRED_FILES = {"config.yaml": 1, "gpt.pth": 20}
    manager.python_exe.parent.mkdir(parents=True)
    manager.python_exe.touch()
    manager.runtime_command()
    manager.model_dir.mkdir(parents=True)
    (manager.model_dir / "config.yaml").write_text("config")
    (manager.model_dir / "gpt.pth").write_bytes(b"x" * 19)
    manager._write_manifest("installed", "cuda", "bfloat16")
    assert not manager.is_installed()
    (manager.model_dir / "gpt.pth").write_bytes(b"x" * 20)
    assert manager.is_installed()
    manager._write_manifest("failed", "cuda", "bfloat16")
    assert not manager.is_installed()


def test_install_failure_never_marks_runtime_ready(tmp_path, monkeypatch):
    manager = IndexTTSManager(tmp_path / "models", tmp_path / "deps")
    monkeypatch.setattr(
        manager, "_download_source", Mock(side_effect=IndexTTSError("network"))
    )
    with pytest.raises(IndexTTSError, match="network"):
        manager.install()
    assert manager.install_manifest()["state"] == "failed"


def test_cancelled_download_records_cancelled(tmp_path, monkeypatch):
    manager = IndexTTSManager(tmp_path / "models", tmp_path / "deps")

    def cancel(_):
        manager._cancel_requested.set()
        manager._check_cancelled()

    monkeypatch.setattr(manager, "_download_source", cancel)
    with pytest.raises(IndexTTSCancelled):
        manager.install()
    assert manager.install_manifest()["state"] == "cancelled"


@pytest.fixture
def fake_runtime(tmp_path):
    """Exercise the real subprocess protocol with a lightweight fake upstream API."""
    package = tmp_path / "indextts"
    package.mkdir()
    (package / "__init__.py").touch()
    (tmp_path / "torch.py").write_text("""from contextlib import nullcontext
class CUDA:
    def is_available(self): return True
    def is_bf16_supported(self, **kwargs): return True
cuda = CUDA()
inference_mode = nullcontext
""")
    (package / "infer_v2_5.py").write_text("""import json, wave
from pathlib import Path
class QwenEmotion:
    def __init__(self, path):
        with open("emotion_loads.txt", "a") as f: f.write("loaded\\n")
    def inference(self, prompt):
        if prompt == "FAIL": raise RuntimeError("emotion interpreter failed")
        with open("emotion_prompts.txt", "a") as f: f.write(prompt + "\\n")
        return dict(zip(("happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"), [0.7, 0, 0, 0, 0, 0, 0.1, 0]))
class IndexTTS2:
    def __init__(self, **kwargs):
        self.options = kwargs
        Path("constructor.json").write_text(json.dumps(kwargs))
        print("upstream log on stdout")
    def infer(self, **kwargs):
        if kwargs.get("use_emo_text") and not self.options["use_qwen_emo"]:
            raise RuntimeError("QwenEmotion missing")
        if kwargs["text"] == "ERROR": raise RuntimeError("upstream failure")
        Path("inference.json").write_text(json.dumps(kwargs))
        with wave.open(kwargs["output_path"], "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(22050); f.writeframes(b"\\x00\\x00" * 100)
""")
    cli = tmp_path / "worker.py"
    cli.write_text(INDEXTTS_CLI)
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"reference")
    manager = SimpleNamespace(
        is_installed=lambda: True,
        model_dir=tmp_path,
        cache_dir=tmp_path / "cache",
        runtime_command=lambda: [
            sys.executable,
            str(cli),
            "--source-dir",
            str(tmp_path),
        ],
        runtime_environment=lambda: dict(os.environ),
    )
    return manager, {"reference_audio_path": str(reference)}, tmp_path


def test_instruction_uses_separate_qwen_phase_then_tts_without_qwen(fake_runtime):
    manager, config, root = fake_runtime
    engine = IndexTTSTTSEngine(manager)
    try:
        engine.synthesize_to_wav("Hola", root / "first.wav", config)
        first_process = engine._process
        assert not json.loads((root / "constructor.json").read_text())["use_qwen_emo"]
        engine.synthesize_to_wav("Otra frase", root / "second.wav", config)
        assert engine._process is first_process
        instruction = {**config, "emotion_mode": "text", "emo_text": "I am delighted!"}
        engine.synthesize_to_wav("Hola de nuevo", root / "third.wav", instruction)
        assert engine._process is not first_process
        assert first_process.poll() is not None
        constructor = json.loads((root / "constructor.json").read_text())
        assert constructor["use_bf16"] is True
        assert constructor["use_qwen_emo"] is False
        assert constructor["use_cuda_kernel"] is False
        args = json.loads((root / "inference.json").read_text())
        assert args["emo_vector"] == [0.7, 0, 0, 0, 0, 0, 0.1, 0]
        assert "use_emo_text" not in args
        assert engine._emotion_process is None
        assert args["text"] == "Hola de nuevo"
        emotional_process = engine._process
        engine.synthesize_to_wav("Neutral again", root / "fourth.wav", config)
        assert engine._process is emotional_process
        assert "use_emo_text" not in json.loads((root / "inference.json").read_text())
        with pytest.raises(TTSEngineError, match="upstream failure"):
            engine.synthesize_to_wav("ERROR", root / "failure.wav", instruction)
        assert engine._process is emotional_process
        assert (root / "emotion_loads.txt").read_text().splitlines() == ["loaded"]
    finally:
        process = engine._process
        engine.close()
    assert process.poll() is not None


@pytest.mark.parametrize("mode", ["text", "audio", "reference"])
def test_preload_needs_no_synthesis_inputs_but_generation_still_validates(
    fake_runtime, mode
):
    manager, _, root = fake_runtime
    engine = IndexTTSTTSEngine(manager)
    config = {"emotion_mode": mode, "emo_text": "", "reference_audio_path": ""}
    try:
        engine.preload(config)
        constructor = json.loads((root / "constructor.json").read_text())
        assert constructor["use_qwen_emo"] is False
        with pytest.raises(TTSEngineError):
            engine.synthesize_to_wav("Hola", root / "invalid.wav", config)
    finally:
        engine.close()


def test_fixed_presets_never_load_qwen_and_reuse_worker(fake_runtime):
    from app.tts.indextts_emotions import emotion_preset

    manager, config, root = fake_runtime
    engine = IndexTTSTTSEngine(manager)
    try:
        engine.preload({**config, "emotion_mode": "text", "emo_text": ""})
        process = engine._process
        for name in ("happy", "sad", "off"):
            engine.synthesize_to_wav(
                "Hola.", root / (name + ".wav"), {**config, **emotion_preset(name)}
            )
            assert engine._process is process
            assert (
                json.loads((root / "constructor.json").read_text())["use_qwen_emo"]
                is False
            )
            assert "use_emo_text" not in json.loads(
                (root / "inference.json").read_text()
            )
    finally:
        engine.close()


def test_batch_deduplicates_persists_and_invalidates_vectors(fake_runtime, monkeypatch):
    manager, config, root = fake_runtime
    manager.MODEL_REVISION = "revision-one"
    engine = IndexTTSTTSEngine(manager)
    custom = {
        **config,
        "emotion_mode": "text",
        "emo_text": "Friendly",
        "emotion_source": "custom",
    }
    prepared = engine.prepare_emotions(
        [custom, {**custom, "emo_alpha": 0.2}, {**custom, "emo_text": "Sad"}, config],
        ["One", "Two", "Three", "Four"],
    )
    assert (root / "emotion_loads.txt").read_text().splitlines() == ["loaded"]
    assert (root / "emotion_prompts.txt").read_text().splitlines() == [
        "Friendly",
        "Sad",
    ]
    assert engine._process is None and engine._emotion_process is None
    assert prepared[0]["emo_vector"] == prepared[1]["emo_vector"]
    assert prepared[1]["emo_alpha"] == 0.2
    assert prepared[0]["emotion_source"] == "custom"
    assert prepared[0]["emotion_revision"] == "revision-one"
    assert prepared[3]["emotion_mode"] == "reference"
    assert custom["emotion_mode"] == "text"

    restored = IndexTTSTTSEngine(manager)
    restored.remember_emotions(json.loads(json.dumps(prepared)))
    calculate = Mock(return_value=[[0, 0, 0.6, 0, 0, 0, 0, 0.1]])
    monkeypatch.setattr(restored, "_calculate_emotions", calculate)
    assert restored.prepare_emotions([custom], ["Changed narration"])[0] == prepared[0]
    calculate.assert_not_called()
    manager.MODEL_REVISION = "revision-two"
    updated = restored.prepare_emotions([prepared[0]], ["One"])[0]
    calculate.assert_called_once_with(["Friendly"], "cuda")
    assert updated["emotion_revision"] == "revision-two"


def test_reference_validation_and_cancellation(fake_runtime):
    manager, config, root = fake_runtime
    engine = IndexTTSTTSEngine(manager)
    with pytest.raises(TTSEngineError, match="emo_audio_prompt"):
        engine.validate({**config, "emotion_mode": "audio"})
    with pytest.raises(TTSEngineError, match="reference_audio_path"):
        engine.validate({})
    engine.preload(config)
    process = engine._process
    engine.cancel_current()
    assert process.poll() is not None
    with pytest.raises(TTSCancelled):
        engine.synthesize_to_wav("Hola", root / "cancelled.wav", config)


def test_failed_emotion_batch_releases_process_without_starting_tts(fake_runtime):
    manager, config, _ = fake_runtime
    engine = IndexTTSTTSEngine(manager)
    with pytest.raises(TTSEngineError, match="emotion interpreter failed"):
        engine.prepare_emotions(
            [{**config, "emotion_mode": "text", "emo_text": "FAIL"}], ["Hola"]
        )
    assert engine._emotion_process is None
    assert engine._process is None
    assert not engine._emotion_cache


def test_auto_emotion_tracks_text_changes_and_preserves_official_scores(
    fake_runtime, monkeypatch
):
    manager, config, _ = fake_runtime
    engine = IndexTTSTTSEngine(manager)
    calculate = Mock(return_value=[[1.2, 0, 0, 0, 0, 0, 0, 0]])
    monkeypatch.setattr(engine, "_calculate_emotions", calculate)
    prepared = engine.prepare_emotions([{**config, "emotion_mode": "auto"}], ["Hola"])[
        0
    ]
    assert prepared["emo_vector"][0] == 1.2
    assert engine.prepare_emotions([prepared], ["Hola"])[0] == prepared
    assert calculate.call_count == 1
    changed = engine.prepare_emotions([prepared], ["Adiós"])[0]
    assert changed["emotion_prompt"] == "Adiós"
    assert calculate.call_count == 2


def test_pipeline_prepares_all_emotions_and_recovers_saved_vectors(
    fake_runtime, monkeypatch
):
    from app.core.audio_pipeline import AudioGenerationOptions, AudioPipeline
    from app.core.audiobook_store import AudiobookStore

    manager, config, root = fake_runtime
    manager.MODEL_REVISION = "test-revision"
    engine = IndexTTSTTSEngine(manager)
    monkeypatch.setattr("app.core.audiobook_store.app_data_root", lambda: root)
    store = AudiobookStore(root / "projects.sqlite")
    pipeline = AudioPipeline(engine, audiobook_store=store)
    source = '{{lang es}}{{emotion custom "Friendly" 90%}}Hola. Bienvenidos.{{emotion custom "Sad"}}Adiós.{{emotion off}}Normal.'
    options = AudioGenerationOptions(
        root / "out", {**config, "engine": "indextts"}, "ffmpeg"
    )
    original = pipeline._prepare_groups(source, options)
    prepared = pipeline._prepare_indextts_emotions(original, options)
    chunks = [c for g in prepared for c in g.chunks]
    assert [c.markup_state["emotion"]["emotion_mode"] for c in chunks] == [
        "vector",
        "vector",
        "reference",
    ]
    assert chunks[0].markup_state["emotion"]["emotion_prompt"] == "Friendly"
    assert engine._process is None and engine._emotion_process is None
    book = store.create_audiobook(
        source, options.voice_config, options.output_dir, "safe_chunks", "single"
    )
    store.replace_segments(book, prepared)
    saved = store.list_segments(book.id)
    state = json.loads(saved[0].markup_state_json)
    assert state["emotion"]["emotion_revision"] == "test-revision"
    assert state["emotion"]["emo_alpha"] == 0.9
    assert (
        state["emotion"]["emo_vector"]
        == chunks[0].markup_state["emotion"]["emo_vector"]
    )
    recovered = IndexTTSTTSEngine(manager)
    calculate = Mock(side_effect=AssertionError("Saved vectors must be reused"))
    monkeypatch.setattr(recovered, "_calculate_emotions", calculate)
    options.project_audiobook_id = book.id
    restarted = AudioPipeline(recovered, audiobook_store=store)
    assert restarted._prepare_indextts_emotions(original, options) == prepared
    calculate.assert_not_called()


def test_install_uses_pinned_uv_environment_and_prepare(tmp_path, monkeypatch):
    runtime = SimpleNamespace(install=Mock(), run_python=Mock())
    manager = IndexTTSManager(tmp_path / "models", tmp_path / "deps", runtime)
    uv = (
        manager.dependency_dir
        / "bootstrap"
        / ("bin/uv.exe" if os.name == "nt" else "bin/uv")
    )
    uv.parent.mkdir(parents=True)
    uv.touch()
    calls = []
    monkeypatch.setattr(manager, "_download_source", lambda progress: None)
    monkeypatch.setattr(manager, "_run", lambda command, *args: calls.append(command))
    monkeypatch.setattr(manager, "has_model_files", lambda: True)
    manager.install()
    assert "--frozen" in calls[0] and "--managed-python" in calls[0]
    assert "--all-extras" not in calls[0]
    assert calls[1][calls[1].index("--model-revision") + 1] == manager.MODEL_REVISION
    assert calls[1][calls[1].index("--use-qwen-emo") + 1] == "1"
    assert manager.install_manifest()["state"] == "installed"
