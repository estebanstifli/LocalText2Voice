from __future__ import annotations

import math
import re
import subprocess
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from app.utils.ffmpeg_utils import FFmpegRunner, find_ffmpeg


@dataclass(frozen=True)
class WaveformEnvelope:
    times: tuple[float, ...]
    minimums: tuple[float, ...]
    maximums: tuple[float, ...]
    duration_seconds: float
    source_duration_seconds: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.times


def db_to_gain(db_value: float) -> float:
    return 10 ** (db_value / 20)


def generate_waveform_preview(
    audio_path: Path,
    ffmpeg_path: str | Path,
    temp_dir: Path,
    target_sample_rate: int = 8000,
    max_points: int = 24000,
    max_duration_seconds: float | None = None,
) -> WaveformEnvelope:
    """Create a lightweight min/max envelope for UI drawing.

    FFmpeg converts any supported source to a temporary mono PCM WAV first. The
    WAV is then read in blocks so long audiobooks do not need to be loaded into
    memory all at once.
    """

    source = Path(audio_path)
    if not source.is_file():
        raise FileNotFoundError(f"Audio file not found: {source}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    preview_wav = temp_dir / f"{source.stem[:40]}_waveform_{abs(hash(source))}.wav"
    ffmpeg = find_ffmpeg(ffmpeg_path)
    source_duration = probe_audio_duration(source, ffmpeg)
    arguments = [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
    ]
    if max_duration_seconds is not None and max_duration_seconds > 0:
        arguments.extend(["-t", f"{max_duration_seconds:.3f}"])
    arguments.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(target_sample_rate),
            "-codec:a",
            "pcm_s16le",
            str(preview_wav),
        ]
    )
    runner = FFmpegRunner(ffmpeg)
    runner.run(arguments)
    return build_waveform_envelope(
        preview_wav,
        max_points=max_points,
        source_duration_seconds=source_duration,
    )


def build_waveform_envelope(
    wav_path: Path,
    max_points: int = 5000,
    source_duration_seconds: float = 0.0,
) -> WaveformEnvelope:
    with wave.open(str(wav_path), "rb") as audio:
        frame_count = audio.getnframes()
        sample_rate = audio.getframerate()
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()

        if frame_count <= 0 or sample_rate <= 0:
            return WaveformEnvelope((), (), (), 0.0, source_duration_seconds)
        if sample_width != 2:
            raise ValueError("Waveform preview expects 16-bit PCM WAV data.")

        block_frames = max(1, math.ceil(frame_count / max(1, max_points)))
        times: list[float] = []
        minimums: list[float] = []
        maximums: list[float] = []
        peak = 1
        frames_read = 0

        while frames_read < frame_count:
            raw = audio.readframes(block_frames)
            if not raw:
                break
            values = array("h")
            values.frombytes(raw)
            if sys.byteorder == "big":
                values.byteswap()
            if channels > 1:
                values = _mix_interleaved_to_mono(values, channels)
            if not values:
                break

            block_min = min(values)
            block_max = max(values)
            peak = max(peak, abs(block_min), abs(block_max))
            center_frame = frames_read + len(values) / 2
            times.append(center_frame / sample_rate)
            minimums.append(float(block_min))
            maximums.append(float(block_max))
            frames_read += block_frames

    scale = float(peak)
    duration = frame_count / sample_rate
    return WaveformEnvelope(
        tuple(times),
        tuple(value / scale for value in minimums),
        tuple(value / scale for value in maximums),
        duration,
        source_duration_seconds or duration,
    )


def probe_audio_duration(audio_path: Path, ffmpeg_path: str | Path) -> float:
    ffmpeg = find_ffmpeg(ffmpeg_path)
    creation_flags = (
        subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "CREATE_NO_WINDOW")
        else 0
    )
    process = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(audio_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
        check=False,
    )
    output = process.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        return 0.0
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _mix_interleaved_to_mono(values: array, channels: int) -> array:
    mono = array("h")
    for index in range(0, len(values), channels):
        frame = values[index : index + channels]
        if frame:
            mono.append(round(sum(frame) / len(frame)))
    return mono
