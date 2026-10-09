from __future__ import annotations

import ctypes
import os
import threading
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.core.audio_pipeline import AudioGenerationOptions, AudioGroup, AudioPipeline, GenerationCancelled
from app.core.text_processor import TextChunk
from app.tts.engine_registry import engine_ids
from app.utils.atomic_audio import AudioFileInUseError, atomic_wav_output, validate_wav


def write_wav(path, value=1):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes([value, 0]) * 160)


def sharing_violation(code=32):
    error = PermissionError("File is in use")
    error.winerror = code
    return error


@pytest.mark.parametrize("engine_id", sorted(engine_ids() | {"custom:test", "kokoro_python"}))
def test_all_engine_routes_synthesize_and_postprocess_before_replacing(tmp_path, engine_id):
    destination = tmp_path / "group_001_block_0001.wav"
    write_wav(destination)
    original = destination.read_bytes()
    paths = []

    def synthesize(text, path, config):
        assert config["engine"] == engine_id
        assert path != destination and path.parent == destination.parent
        assert destination.read_bytes() == original
        paths.append(path)
        # Covers both deletion-before-writing (IndexTTS) and direct overwrites.
        if engine_id == "indextts":
            path.unlink()
        write_wav(path, 2)

    def postprocess(path, *args):
        assert destination.read_bytes() == original
        write_wav(path, 3)

    pipeline = AudioPipeline(SimpleNamespace(synthesize_to_wav=synthesize))
    options = AudioGenerationOptions(output_dir=tmp_path, voice_config={"engine": engine_id}, ffmpeg_path="unused")
    with patch.object(pipeline, "_postprocess_chunk_wav", side_effect=postprocess):
        result = pipeline._render_groups(
            [AudioGroup("Test", (TextChunk("New text", True),))], options, tmp_path, 1, Mock(),
        )
    assert result == [[destination]]
    assert len(paths) == 1 and not paths[0].exists()
    with wave.open(str(destination), "rb") as audio:
        assert audio.readframes(1) == b"\x03\x00"


@pytest.mark.parametrize("failure", ["synthesis", "postprocess", "invalid", "truncated"])
def test_failed_segment_preserves_previous_audio(tmp_path, failure):
    destination = tmp_path / "audio.wav"
    write_wav(destination)
    original = destination.read_bytes()
    with pytest.raises((RuntimeError, ValueError, wave.Error, EOFError)):
        with atomic_wav_output(destination) as temporary:
            write_wav(temporary, 2)
            if failure in {"synthesis", "postprocess"}:
                raise RuntimeError(failure)
            if failure == "invalid":
                temporary.write_bytes(b"not audio")
            else:
                temporary.write_bytes(temporary.read_bytes()[:-4])
    assert destination.read_bytes() == original
    assert not temporary.exists()


@pytest.mark.parametrize("code", [32, 33])
def test_transient_lock_retries_only_publication(tmp_path, code):
    destination = tmp_path / "audio.wav"
    write_wav(destination)
    real_replace = os.replace
    calls = []

    def replace(source, target):
        calls.append((source, target))
        if len(calls) < 3:
            raise sharing_violation(code)
        real_replace(source, target)

    with patch("app.utils.atomic_audio.os.replace", side_effect=replace), patch("app.utils.atomic_audio.time.sleep"):
        with atomic_wav_output(destination) as temporary:
            write_wav(temporary, 2)
    assert len(calls) == 3
    assert len({source for source, _ in calls}) == 1
    validate_wav(destination)


def test_persistent_lock_preserves_original_and_valid_candidate(tmp_path):
    destination = tmp_path / "audio.wav"
    write_wav(destination)
    original = destination.read_bytes()
    with patch("app.utils.atomic_audio.os.replace", side_effect=sharing_violation()) as replace, patch("app.utils.atomic_audio.time.sleep"):
        with pytest.raises(AudioFileInUseError) as error:
            with atomic_wav_output(destination) as temporary:
                write_wav(temporary, 2)
    assert replace.call_count == 11
    assert destination.read_bytes() == original
    validate_wav(temporary)
    assert str(destination) in str(error.value)
    assert str(temporary) in str(error.value)


def test_cancellation_interrupts_retries_and_preserves_original(tmp_path):
    destination = tmp_path / "audio.wav"
    write_wav(destination)
    original = destination.read_bytes()
    cancelled = threading.Event()

    def check():
        if cancelled.is_set():
            raise GenerationCancelled("Cancelled")

    with patch("app.utils.atomic_audio.os.replace", side_effect=sharing_violation()) as replace, patch("app.utils.atomic_audio.time.sleep", side_effect=lambda _: cancelled.set()):
        with pytest.raises(GenerationCancelled):
            with atomic_wav_output(destination, check) as temporary:
                write_wav(temporary, 2)
    assert replace.call_count == 1
    assert destination.read_bytes() == original
    assert not temporary.exists()


def test_non_windows_permission_error_is_not_retried(tmp_path):
    with patch("app.utils.atomic_audio.os.replace", side_effect=PermissionError("Denied")) as replace, patch("app.utils.atomic_audio.time.sleep") as sleep:
        with pytest.raises(PermissionError):
            with atomic_wav_output(tmp_path / "audio.wav") as temporary:
                write_wav(temporary)
    assert replace.call_count == 1
    sleep.assert_not_called()


def test_completed_segments_survive_later_failure(tmp_path):
    first = tmp_path / "group_001_block_0001.wav"
    second = tmp_path / "group_001_block_0002.wav"
    write_wav(second)
    original_second = second.read_bytes()

    def synthesize(text, path, config):
        write_wav(path, 2)
        if text == "second":
            raise RuntimeError("failed")

    pipeline = AudioPipeline(SimpleNamespace(synthesize_to_wav=synthesize))
    with pytest.raises(RuntimeError):
        pipeline._render_groups(
            [AudioGroup("Test", (TextChunk("first", True), TextChunk("second", True)))],
            AudioGenerationOptions(output_dir=tmp_path, voice_config={}, ffmpeg_path="unused"), tmp_path, 2, Mock(),
        )
    validate_wav(first)
    assert second.read_bytes() == original_second


def test_pipeline_postprocess_failure_keeps_previous_segment(tmp_path):
    destination = tmp_path / "group_001_block_0001.wav"
    write_wav(destination)
    original = destination.read_bytes()
    pipeline = AudioPipeline(SimpleNamespace(
        synthesize_to_wav=lambda text, path, config: write_wav(path, 2)
    ))
    with patch.object(pipeline, "_postprocess_chunk_wav", side_effect=RuntimeError("FFmpeg failed")):
        with pytest.raises(RuntimeError, match="FFmpeg failed"):
            pipeline._render_groups(
                [AudioGroup("Test", (TextChunk("Text", True),))],
                AudioGenerationOptions(output_dir=tmp_path, voice_config={}, ffmpeg_path="unused"),
                tmp_path, 1, Mock(),
            )
    assert destination.read_bytes() == original
    assert list(tmp_path.glob("*.pending.wav")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_real_windows_lock_is_retried_until_handle_closes(tmp_path):
    from ctypes import wintypes

    destination = tmp_path / "audio.wav"
    write_wav(destination)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(destination), 0x80000000, 1, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    timer = threading.Timer(0.25, lambda: kernel.CloseHandle(handle))
    timer.start()
    real_replace = os.replace
    violations = []

    def replace(source, target):
        try:
            real_replace(source, target)
        except OSError as exc:
            violations.append(exc.winerror)
            raise

    try:
        with patch("app.utils.atomic_audio.os.replace", side_effect=replace):
            with atomic_wav_output(destination) as temporary:
                write_wav(temporary, 2)
        assert violations and set(violations) <= {5, 32}
        validate_wav(destination)
    finally:
        timer.join()
