from __future__ import annotations

from pathlib import Path

from app.utils.ffmpeg_utils import FFmpegRunner, find_ffmpeg


def automatic_tail_cut_seconds(tail_analysis: dict[str, object]) -> float | None:
    """Return the conservative cut point used by automatic tail cleanup.

    The automatic operation keeps both the Whisper safety margin and the
    configured possible-artifact allowance. Only audio beyond that allowance
    is removed.
    """

    last_word_end = _number(tail_analysis.get("last_valid_word_end_seconds"))
    duration = _number(tail_analysis.get("audio_duration_seconds"))
    if last_word_end is None or duration is None:
        return None
    safety = max(0.0, _number(tail_analysis.get("safety_margin_seconds")) or 0.0)
    warning = max(
        0.0,
        _number(tail_analysis.get("warning_threshold_seconds")) or 0.0,
    )
    return _valid_cut_point(last_word_end + safety + warning, duration)


def full_tail_cut_seconds(tail_analysis: dict[str, object]) -> float | None:
    """Return the cut point that removes the whole detected unexplained tail."""

    last_word_end = _number(tail_analysis.get("last_valid_word_end_seconds"))
    duration = _number(tail_analysis.get("audio_duration_seconds"))
    if last_word_end is None or duration is None:
        return None
    safety = max(0.0, _number(tail_analysis.get("safety_margin_seconds")) or 0.0)
    return _valid_cut_point(last_word_end + safety, duration)


def removable_tail_seconds(
    tail_analysis: dict[str, object],
    cut_seconds: float,
) -> float:
    duration = _number(tail_analysis.get("audio_duration_seconds")) or 0.0
    return max(0.0, duration - max(0.0, float(cut_seconds)))


def trim_wav_at(
    source_path: Path,
    output_path: Path,
    cut_seconds: float,
    ffmpeg_path: str | Path,
    runner: FFmpegRunner | None = None,
) -> FFmpegRunner:
    """Write a PCM WAV ending at ``cut_seconds`` without changing the source."""

    source = Path(source_path)
    output = Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(f"Audio file not found: {source}")
    if cut_seconds <= 0.01:
        raise ValueError("The audio cut position must be after the beginning.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.stem}.tmp.wav")
    temporary.unlink(missing_ok=True)
    active_runner = runner or FFmpegRunner(find_ffmpeg(ffmpeg_path))
    try:
        active_runner.run(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-t",
                f"{cut_seconds:.6f}",
                "-vn",
                "-map",
                "0:a:0",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ]
        )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return active_runner


def _valid_cut_point(value: float, duration: float) -> float | None:
    if duration <= 0.02:
        return None
    cut = max(0.0, min(float(value), duration))
    if cut <= 0.01 or duration - cut <= 0.01:
        return None
    return cut


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
