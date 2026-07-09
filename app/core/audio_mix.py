from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.utils.ffmpeg_utils import FFmpegRunner, find_ffmpeg


@dataclass(frozen=True)
class AudioMixSettings:
    voice_volume_db: float = 0.0
    music_volume_db: float = -7.0
    voice_start_offset_ms: int = 2000
    music_tail_ms: int = 2000
    music_fade_in_seconds: float = 1.0
    music_fade_out_seconds: float = 1.0
    ducking_enabled: bool = True
    ducking_strength: str = "low"
    loop_background: bool = True
    normalize: bool = False
    mp3_bitrate: str = "128k"


def render_audio_preview_segment(
    voice_path: Path,
    output_path: Path,
    ffmpeg_path: str | Path,
    settings: AudioMixSettings,
    music_path: Path | None = None,
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
) -> Path:
    return render_audio_mix(
        voice_path=voice_path,
        output_path=output_path,
        ffmpeg_path=ffmpeg_path,
        settings=settings,
        music_path=music_path,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        voice_duration_seconds=duration_seconds,
    )


def render_audio_mix(
    voice_path: Path,
    output_path: Path,
    ffmpeg_path: str | Path,
    settings: AudioMixSettings,
    music_path: Path | None = None,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
    voice_duration_seconds: float | None = None,
    metadata: dict[str, str] | None = None,
) -> Path:
    if not voice_path.is_file():
        raise FileNotFoundError(f"Voice audio not found: {voice_path}")
    if music_path is not None and not music_path.is_file():
        raise FileNotFoundError(f"Music audio not found: {music_path}")

    runner = FFmpegRunner(find_ffmpeg(ffmpeg_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    arguments = ["-y", "-hide_banner", "-loglevel", "error"]
    arguments.extend(["-i", str(voice_path)])

    if music_path is not None:
        if settings.loop_background:
            arguments.extend(["-stream_loop", "-1"])
        arguments.extend(["-i", str(music_path)])

    filter_complex, final_label = _build_mix_filter(
        settings,
        has_music=music_path is not None,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        voice_duration_seconds=voice_duration_seconds,
    )
    arguments.extend(["-filter_complex", filter_complex, "-map", f"[{final_label}]"])
    if output_path.suffix.lower() == ".mp3":
        arguments.extend(["-codec:a", "libmp3lame", "-b:a", settings.mp3_bitrate])
        for key in ("title", "artist", "album"):
            value = str((metadata or {}).get(key, "")).strip()
            if value:
                arguments.extend(["-metadata", f"{key}={value}"])
    else:
        arguments.extend(["-codec:a", "pcm_s16le"])
    arguments.append(str(output_path))
    runner.run(arguments)
    return output_path


def _build_mix_filter(
    settings: AudioMixSettings,
    has_music: bool,
    start_seconds: float,
    duration_seconds: float | None,
    voice_duration_seconds: float | None,
) -> tuple[str, str]:
    audio_format = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
    filters: list[str] = []
    voice_filters = [audio_format]
    target_duration = _target_duration(settings, duration_seconds, voice_duration_seconds)
    offset_seconds = settings.voice_start_offset_ms / 1000
    if offset_seconds < 0:
        voice_filters.extend([f"atrim=start={abs(offset_seconds):.3f}", "asetpts=N/SR/TB"])
    voice_filters.append(f"volume={settings.voice_volume_db:.2f}dB")
    if offset_seconds > 0:
        voice_filters.append(f"adelay={round(offset_seconds * 1000)}:all=1")
    if target_duration is not None:
        voice_filters.extend(
            [
                f"apad=whole_dur={target_duration:.3f}",
                f"atrim=0:{target_duration:.3f}",
                "asetpts=N/SR/TB",
            ]
        )
    filters.append("[0:a]" + ",".join(voice_filters) + "[voice_base]")

    if has_music:
        music_filters = [audio_format]
        if target_duration is not None:
            music_filters.extend(
                [
                    f"apad=whole_dur={target_duration:.3f}",
                    f"atrim=0:{target_duration:.3f}",
                    "asetpts=N/SR/TB",
                ]
            )
        music_filters.append(f"volume={settings.music_volume_db:.2f}dB")
        if settings.music_fade_in_seconds > 0:
            music_filters.append(
                f"afade=t=in:st=0:d={settings.music_fade_in_seconds:.3f}"
            )
        if settings.music_fade_out_seconds > 0:
            if target_duration is not None:
                fade_start = max(0.0, target_duration - settings.music_fade_out_seconds)
                music_filters.append(
                    "afade=t=out:"
                    f"st={fade_start:.3f}:d={settings.music_fade_out_seconds:.3f}"
                )
            else:
                music_filters.extend(
                    [
                        "areverse",
                        f"afade=t=in:st=0:d={settings.music_fade_out_seconds:.3f}",
                        "areverse",
                    ]
                )
        filters.append("[1:a]" + ",".join(music_filters) + "[music_base]")
        if settings.ducking_enabled:
            filters.append("[voice_base]asplit=2[voice_mix][voice_side]")
            filters.append(
                "[music_base][voice_side]"
                + ducking_filter(settings.ducking_strength)
                + "[music_ducked]"
            )
            filters.append(
                "[voice_mix][music_ducked]amix=inputs=2:duration=first:normalize=0[mix]"
            )
        else:
            filters.append(
                "[voice_base][music_base]amix=inputs=2:duration=first:normalize=0[mix]"
            )
        final_label = "mix"
    else:
        final_label = "voice_base"

    if start_seconds > 0 or duration_seconds is not None:
        trim_filters: list[str] = []
        if start_seconds > 0:
            trim_filters.append(f"start={start_seconds:.3f}")
        if duration_seconds is not None:
            trim_filters.append(f"duration={duration_seconds:.3f}")
        filters.append(
            f"[{final_label}]atrim="
            + ":".join(trim_filters)
            + ",asetpts=N/SR/TB[mix_window]"
        )
        final_label = "mix_window"

    if settings.normalize:
        filters.append(
            f"[{final_label}]loudnorm=I=-16:LRA=11:TP=-1.5[mix_normalized]"
        )
        final_label = "mix_normalized"

    return ";".join(filters), final_label


def _target_duration(
    settings: AudioMixSettings,
    duration_seconds: float | None,
    voice_duration_seconds: float | None,
) -> float | None:
    if duration_seconds is not None:
        return duration_seconds
    if voice_duration_seconds is None:
        return None
    offset_seconds = settings.voice_start_offset_ms / 1000
    trimmed_voice = max(0.01, voice_duration_seconds - max(0.0, -offset_seconds))
    return (
        max(0.0, offset_seconds)
        + trimmed_voice
        + max(0.0, settings.music_tail_ms / 1000)
    )


def ducking_filter(strength: str) -> str:
    profiles = {
        "low": "sidechaincompress=threshold=0.035:ratio=3:attack=30:release=350",
        "medium": "sidechaincompress=threshold=0.025:ratio=6:attack=20:release=500",
        "high": "sidechaincompress=threshold=0.018:ratio=10:attack=15:release=650",
    }
    return profiles.get(strength, profiles["medium"])
