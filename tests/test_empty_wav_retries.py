from concurrent.futures import ThreadPoolExecutor
import importlib
import threading
import wave

import pytest

from app.tts.base import BaseTTSEngine, TTSCancelled, TTSEngineError
from app.utils.atomic_audio import EmptyWavError, validate_wav


def write_wav(path, frames):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * frames)


class Probe(BaseTTSEngine):
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self._cancel_requested = threading.Event()

    def validate(self, config):
        pass

    def cancel_current(self):
        self._cancel_requested.set()

    def synthesize_to_wav(self, text, output_wav, voice_config):
        self.calls.append((text, output_wav, voice_config))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "cancel":
            self.cancel_current()
            outcome = 0
        if outcome == "zero_bytes":
            output_wav.write_bytes(b"")
        else:
            write_wav(output_wav, outcome)
        return output_wav


@pytest.mark.parametrize("empty", [0, "zero_bytes", EmptyWavError("empty")])
@pytest.mark.parametrize("failures", [0, 1, 2, 3])
def test_bounded_retries_preserve_old_audio(tmp_path, empty, failures):
    target = tmp_path / "segment.wav"
    write_wav(target, 10)
    original = target.read_bytes()
    engine = Probe([empty] * failures + [1])
    config = {"verification": {"max_retries": 0}}
    logs = []
    engine.set_log_callback(logs.append)
    if failures == 3:
        with pytest.raises(TTSEngineError, match="3 attempts.*2 retries"):
            engine.synthesize_to_wav("What?", target, config)
        assert target.read_bytes() == original
    else:
        assert engine.synthesize_to_wav("What?", target, config) == target
        validate_wav(target)  # Even one silent sample is valid audio.
        with wave.open(str(target)) as audio:
            assert audio.getnframes() == 1
    assert len(engine.calls) == min(failures + 1, 3)
    assert len(logs) == min(failures, 2)
    assert all(text == "What?" and cfg is config for text, _, cfg in engine.calls)
    assert len({path for _, path, _ in engine.calls}) == len(engine.calls)
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("outcome", [TTSEngineError("Unauthorized"), TTSCancelled("cancel"), "cancel"])
def test_errors_and_cancellation_do_not_retry(tmp_path, outcome):
    engine = Probe([outcome, 1])
    with pytest.raises(TTSEngineError):
        engine.synthesize_to_wav("Hello", tmp_path / "test.wav", {})
    assert len(engine.calls) == 1
    assert not list(tmp_path.iterdir())


def test_delegating_override_has_one_retry_budget(tmp_path):
    class Derived(Probe):
        def synthesize_to_wav(self, text, output_wav, voice_config):
            return super().synthesize_to_wav(text, output_wav, voice_config)
    engine = Derived([0] * 10)
    with pytest.raises(TTSEngineError):
        engine.synthesize_to_wav("Hello", tmp_path / "test.wav", {})
    assert len(engine.calls) == 3


def test_parallel_calls_each_validate_and_retry(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    class Parallel(Probe):
        def synthesize_to_wav(self, text, output_wav, voice_config):
            if text == "blocked":
                entered.set()
                assert release.wait(5)
                write_wav(output_wav, 1)
                return output_wav
            return super().synthesize_to_wav(text, output_wav, voice_config)
    engine = Parallel([0, 0, 1])
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(engine.synthesize_to_wav, "blocked", tmp_path / "first.wav", {})
        try:
            assert entered.wait(5)
            engine.synthesize_to_wav("retry", tmp_path / "second.wav", {})
            assert len(engine.calls) == 3
        finally:
            release.set()
        future.result()


@pytest.mark.parametrize("module", ["piper_engine", "kokoro_python_engine", "chatterbox_engine", "qwen_engine", "omnivoice_engine", "f5_russian_engine", "indextts_engine", "audiocpp_engine", "api_engines"])
def test_all_concrete_engines_use_common_validation(module):
    import inspect
    module = importlib.import_module("app.tts." + module)
    engines = [cls for cls in vars(module).values() if inspect.isclass(cls)
               and issubclass(cls, BaseTTSEngine) and not inspect.isabstract(cls)]
    assert engines
    for cls in engines:
        assert hasattr(cls.synthesize_to_wav, "__wrapped__")


def test_header_claiming_samples_but_containing_none_is_empty(tmp_path):
    path = tmp_path / "test.wav"
    write_wav(path, 10)
    path.write_bytes(path.read_bytes()[:44])
    with pytest.raises(EmptyWavError):
        validate_wav(path)


def test_rebuild_rejects_previously_saved_empty_segment(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from app.workers.verification_worker import AudiobookRebuildWorker
    path = tmp_path / "old.wav"
    write_wav(path, 0)
    store = Mock()
    store.list_segments.return_value = [SimpleNamespace(status="rendered", sequence_index=7, wav_path=str(path))]
    worker = AudiobookRebuildWorker(store, 1, tmp_path, "unused", Mock())
    failures = []
    worker.failed.connect(failures.append)
    with patch("app.workers.verification_worker.find_ffmpeg") as find:
        worker.run()
    assert len(failures) == 1 and "Segment 7" in failures[0]
    assert "Regenerate" in failures[0]
    find.assert_not_called()
    store.complete_audiobook.assert_not_called()


@pytest.mark.parametrize("kind", ["omnivoice", "indextts"])
@pytest.mark.parametrize("recover", [True, False])
def test_real_worker_engines_retry_header_only_result(tmp_path, monkeypatch, kind, recover):
    from pathlib import Path
    from unittest.mock import Mock
    from app.tts.omnivoice_engine import OmniVoiceTTSEngine
    from app.tts.indextts_engine import IndexTTSTTSEngine
    cls = OmniVoiceTTSEngine if kind == "omnivoice" else IndexTTSTTSEngine
    engine = cls(manager=Mock())
    monkeypatch.setattr(engine, "validate", lambda config: None)
    monkeypatch.setattr(engine, "_ensure_worker", lambda config: object())
    monkeypatch.setattr(engine, "_wait_for_response", lambda *args: None)
    if kind == "indextts":
        config = {"reference_audio_path": "", "emo_audio_prompt": ""}
        monkeypatch.setattr(engine, "prepare_emotions", lambda *args: [config])
        monkeypatch.setattr("app.tts.indextts_engine.inference_arguments",
                            lambda config, text, output: {"output": output})
    calls = []
    def send(process, request):
        calls.append(request)
        path = request["output"] if kind == "omnivoice" else request["arguments"]["output"]
        write_wav(Path(path), 1 if recover and len(calls) == 3 else 0)
    monkeypatch.setattr(engine, "_send_request", send)
    target = tmp_path / "result.wav"
    if recover:
        engine.synthesize_to_wav("What?", target, {})
        validate_wav(target)
    else:
        with pytest.raises(TTSEngineError, match="3 attempts"):
            engine.synthesize_to_wav("What?", target, {})
        assert not target.exists()
    assert len(calls) == 3
    assert len({request["id"] for request in calls}) == 3
