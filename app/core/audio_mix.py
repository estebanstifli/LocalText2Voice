from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import wave
from array import array
from dataclasses import dataclass, replace
from pathlib import Path

from app.utils.ffmpeg_utils import FFmpegRunner, find_ffmpeg
from app.core.waveform_preview import probe_audio_duration

from .audio_event_timeline import ResolvedAudioClip, SpeechInterval


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
    markup_music_volume_db: float = 0.0
    ambient_volume_db: float = 0.0
    sfx_volume_db: float = 0.0
    voice_muted: bool = False
    background_music_muted: bool = False
    markup_music_muted: bool = False
    ambient_muted: bool = False
    sfx_muted: bool = False
    solo_track: str = ""


def render_audio_preview_segment(
    voice_path: Path,
    output_path: Path,
    ffmpeg_path: str | Path,
    settings: AudioMixSettings,
    music_path: Path | None = None,
    start_seconds: float = 0.0,
    duration_seconds: float = 60.0,
    timeline_clips: tuple[ResolvedAudioClip, ...] = (),
    speech_intervals: tuple[SpeechInterval, ...] = (),
    stem_cache_dir: Path | None = None,
) -> Path:
    offset_seconds = settings.voice_start_offset_ms / 1000
    voice_input_start = max(0.0, start_seconds - offset_seconds)
    preview_voice_offset = max(0.0, offset_seconds - start_seconds)
    music_input_start = 0.0 if settings.loop_background else max(0.0, start_seconds)
    preview_settings = replace(
        settings,
        voice_start_offset_ms=round(preview_voice_offset * 1000),
        music_fade_in_seconds=(
            settings.music_fade_in_seconds
            if start_seconds <= 0.001
            else 0.0
        ),
        music_fade_out_seconds=0.0,
    )
    return render_audio_mix(
        voice_path=voice_path,
        output_path=output_path,
        ffmpeg_path=ffmpeg_path,
        settings=preview_settings,
        music_path=music_path,
        start_seconds=0.0,
        duration_seconds=duration_seconds,
        voice_duration_seconds=duration_seconds,
        voice_input_start_seconds=voice_input_start,
        music_input_start_seconds=music_input_start,
        timeline_clips=timeline_clips,
        speech_intervals=speech_intervals,
        stem_cache_dir=stem_cache_dir,
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
    voice_input_start_seconds: float = 0.0,
    music_input_start_seconds: float = 0.0,
    timeline_clips: tuple[ResolvedAudioClip, ...] = (),
    speech_intervals: tuple[SpeechInterval, ...] = (),
    stem_cache_dir: Path | None = None,
) -> Path:
    if not voice_path.is_file():
        raise FileNotFoundError(f"Voice audio not found: {voice_path}")
    if music_path is not None and not music_path.is_file():
        raise FileNotFoundError(f"Music audio not found: {music_path}")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    runner = FFmpegRunner(ffmpeg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_duration = _target_duration(
        settings,
        duration_seconds,
        voice_duration_seconds,
    )
    if duration_seconds is None and timeline_clips:
        target_duration = _timeline_target_duration(
            timeline_clips,
            ffmpeg,
            settings.voice_start_offset_ms,
            target_duration or 0.0,
        )
    temporary_owner: tempfile.TemporaryDirectory[str] | None = None
    if stem_cache_dir is None:
        temporary_owner = tempfile.TemporaryDirectory(
            prefix="local_text_2_voice_stems_"
        )
        stem_cache_dir = Path(temporary_owner.name)
    voice_input_path = _prepare_voice_stem(
        voice_path,
        stem_cache_dir,
        ffmpeg,
        voice_input_start_seconds,
        (
            None
            if target_duration is None
            else target_duration
            + max(0.0, -settings.voice_start_offset_ms / 1000)
        ),
    )
    stem_paths: dict[str, Path] = {}
    if target_duration is not None:
        stem_paths = _render_timeline_stems(
            tuple(timeline_clips),
            tuple(speech_intervals),
            stem_cache_dir,
            ffmpeg,
            max(0.1, target_duration),
            settings.voice_start_offset_ms,
        )
    arguments = ["-y", "-hide_banner", "-loglevel", "error"]
    arguments.extend(["-i", str(voice_input_path)])

    if music_path is not None:
        if settings.loop_background:
            arguments.extend(["-stream_loop", "-1"])
        if music_input_start_seconds > 0:
            arguments.extend(["-ss", f"{music_input_start_seconds:.3f}"])
        arguments.extend(["-i", str(music_path)])

    next_input_index = 2 if music_path is not None else 1
    stem_input_indexes: dict[str, int] = {}
    for track in ("music", "ambient", "sfx"):
        stem_path = stem_paths.get(track)
        if stem_path is None:
            continue
        arguments.extend(["-i", str(stem_path)])
        stem_input_indexes[track] = next_input_index
        next_input_index += 1

    filter_complex, final_label = _build_mix_filter(
        settings,
        has_music=music_path is not None,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        voice_duration_seconds=voice_duration_seconds,
        stem_input_indexes=stem_input_indexes,
        target_duration_override=target_duration,
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
    try:
        runner.run(arguments)
        if duration_seconds is None and stem_cache_dir is not None:
            _publish_stem_alias(
                output_path,
                stem_cache_dir / f"master{output_path.suffix.lower()}",
                allow_copy=False,
            )
    finally:
        if temporary_owner is not None:
            temporary_owner.cleanup()
    return output_path


def _build_mix_filter(
    settings: AudioMixSettings,
    has_music: bool,
    start_seconds: float,
    duration_seconds: float | None,
    voice_duration_seconds: float | None,
    stem_input_indexes: dict[str, int] | None = None,
    target_duration_override: float | None = None,
) -> tuple[str, str]:
    audio_format = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
    filters: list[str] = []
    voice_filters = [audio_format]
    target_duration = (
        target_duration_override
        if target_duration_override is not None
        else _target_duration(settings, duration_seconds, voice_duration_seconds)
    )
    if target_duration is not None and duration_seconds is not None and start_seconds > 0:
        target_duration = max(target_duration, start_seconds + duration_seconds)
    offset_seconds = settings.voice_start_offset_ms / 1000
    if offset_seconds < 0:
        voice_filters.extend([f"atrim=start={abs(offset_seconds):.3f}", "asetpts=N/SR/TB"])
    voice_volume_db = settings.voice_volume_db
    if settings.voice_muted or (
        settings.solo_track and settings.solo_track != "voice"
    ):
        voice_volume_db = -96.0
    voice_filters.append(f"volume={voice_volume_db:.2f}dB")
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
        music_volume_db = settings.music_volume_db
        if settings.background_music_muted or (
            settings.solo_track and settings.solo_track != "background"
        ):
            music_volume_db = -96.0
        music_filters.append(f"volume={music_volume_db:.2f}dB")
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

    stem_labels: list[str] = []
    track_volumes = {
        "music": settings.markup_music_volume_db,
        "ambient": settings.ambient_volume_db,
        "sfx": settings.sfx_volume_db,
    }
    track_muted = {
        "music": settings.markup_music_muted,
        "ambient": settings.ambient_muted,
        "sfx": settings.sfx_muted,
    }
    for track, input_index in (stem_input_indexes or {}).items():
        label = f"{track}_timeline_stem"
        volume_db = track_volumes.get(track, 0.0)
        if track_muted.get(track, False) or (
            settings.solo_track and settings.solo_track != track
        ):
            volume_db = -96.0
        filters.append(
            f"[{input_index}:a]"
            + audio_format
            + f",volume={volume_db:.2f}dB"
            + f"[{label}]"
        )
        stem_labels.append(label)
    if stem_labels:
        inputs = f"[{final_label}]" + "".join(
            f"[{label}]" for label in stem_labels
        )
        filters.append(
            inputs
            + f"amix=inputs={len(stem_labels) + 1}:duration=first:normalize=0"
            + "[timeline_mix]"
        )
        final_label = "timeline_mix"

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


def _timeline_target_duration(
    clips: tuple[ResolvedAudioClip, ...],
    ffmpeg_path: str | Path,
    voice_offset_ms: int,
    base_duration_seconds: float,
) -> float:
    target = max(0.1, base_duration_seconds)
    for clip in clips:
        source_duration = probe_audio_duration(Path(clip.file_path), ffmpeg_path)
        available = max(0.01, source_duration - clip.source_start_ms / 1000)
        playback = (
            clip.playback_duration_ms / 1000
            if clip.playback_duration_ms is not None
            else available
        )
        if not clip.loop:
            playback = min(playback, available)
        start = max(0.0, (clip.timeline_start_ms + voice_offset_ms) / 1000)
        target = max(target, start + max(0.01, playback))
    return min(target, 24 * 60 * 60)


def _render_timeline_stems(
    clips: tuple[ResolvedAudioClip, ...],
    speech_intervals: tuple[SpeechInterval, ...],
    cache_dir: Path,
    ffmpeg_path: str | Path,
    target_duration_seconds: float,
    voice_offset_ms: int,
) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    stems: dict[str, Path] = {}
    for track in ("music", "ambient", "sfx"):
        track_clips = tuple(
            clip
            for clip in clips
            if clip.track == track
            and clip.timeline_start_ms + voice_offset_ms
            < target_duration_seconds * 1000
        )
        if not track_clips:
            (cache_dir / _canonical_stem_name(track)).unlink(missing_ok=True)
            continue
        signature = _stem_signature(
            track_clips,
            speech_intervals,
            target_duration_seconds,
            voice_offset_ms,
        )
        output = cache_dir / f"{track}_{signature}.wav"
        if output.is_file() and output.stat().st_size > 44:
            _publish_stem_alias(
                output,
                cache_dir / _canonical_stem_name(track),
            )
            stems[track] = output
            continue
        _render_single_stem(
            track_clips,
            speech_intervals,
            output,
            cache_dir,
            ffmpeg_path,
            target_duration_seconds,
            voice_offset_ms,
        )
        _publish_stem_alias(
            output,
            cache_dir / _canonical_stem_name(track),
        )
        stems[track] = output
    return stems


def _prepare_voice_stem(
    voice_path: Path,
    cache_dir: Path,
    ffmpeg_path: str | Path,
    input_start_seconds: float,
    target_duration_seconds: float | None,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    stat = voice_path.stat()
    signature = hashlib.sha256(
        repr(
            (
                str(voice_path.resolve()),
                stat.st_mtime_ns,
                stat.st_size,
                round(input_start_seconds, 3),
                None
                if target_duration_seconds is None
                else round(target_duration_seconds, 3),
            )
        ).encode("utf-8")
    ).hexdigest()[:20]
    output = cache_dir / f"voice_{signature}.wav"
    if not output.is_file() or output.stat().st_size <= 44:
        arguments = ["-y", "-hide_banner", "-loglevel", "error"]
        if input_start_seconds > 0:
            arguments.extend(["-ss", f"{input_start_seconds:.3f}"])
        arguments.extend(["-i", str(voice_path)])
        if target_duration_seconds is not None:
            arguments.extend(["-t", f"{max(0.1, target_duration_seconds):.3f}"])
        arguments.extend(
            [
                "-ar",
                "44100",
                "-ac",
                "2",
                "-codec:a",
                "pcm_s16le",
                str(output),
            ]
        )
        FFmpegRunner(find_ffmpeg(ffmpeg_path)).run(arguments)
    _publish_stem_alias(output, cache_dir / "voice.wav")
    return output


def _canonical_stem_name(track: str) -> str:
    return "music_markup.wav" if track == "music" else f"{track}.wav"


def _publish_stem_alias(
    source: Path,
    alias: Path,
    allow_copy: bool = True,
) -> None:
    try:
        if alias.is_file() and os.path.samefile(source, alias):
            return
    except OSError:
        pass
    try:
        alias.unlink(missing_ok=True)
        os.link(source, alias)
    except OSError:
        if not allow_copy:
            return
        shutil.copy2(source, alias)


def _render_single_stem(
    clips: tuple[ResolvedAudioClip, ...],
    speech_intervals: tuple[SpeechInterval, ...],
    output_path: Path,
    cache_dir: Path,
    ffmpeg_path: str | Path,
    target_duration_seconds: float,
    voice_offset_ms: int,
) -> None:
    runner = FFmpegRunner(find_ffmpeg(ffmpeg_path))
    arguments = [
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-t",
        f"{target_duration_seconds:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
    ]
    prepared: list[tuple[ResolvedAudioClip, Path, float, float]] = []
    for clip in clips:
        source, source_seek, natural_duration = _prepared_clip_source(
            clip,
            cache_dir,
            ffmpeg_path,
        )
        if clip.loop:
            arguments.extend(["-stream_loop", "-1"])
        if source_seek > 0:
            arguments.extend(["-ss", f"{source_seek:.3f}"])
        arguments.extend(["-i", str(source)])
        prepared.append((clip, source, source_seek, natural_duration))

    ducking_clips = [clip for clip in clips if clip.duck_db > 0]
    mask_index: int | None = None
    if ducking_clips:
        mask_path = cache_dir / (
            "duck_mask_"
            + _speech_signature(speech_intervals, target_duration_seconds)
            + ".wav"
        )
        if not mask_path.is_file():
            _write_duck_mask(mask_path, speech_intervals, target_duration_seconds)
        mask_index = len(clips) + 1
        arguments.extend(["-i", str(mask_path)])

    audio_format = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
    filters = [
        f"[0:a]{audio_format},atrim=duration={target_duration_seconds:.3f},"
        "asetpts=N/SR/TB[stem_base]"
    ]
    mask_labels: list[str] = []
    if mask_index is not None:
        mask_labels = [f"duck_mask_{index}" for index in range(len(ducking_clips))]
        if len(mask_labels) == 1:
            filters.append(
                f"[{mask_index}:a]aresample=44100,anull[{mask_labels[0]}]"
            )
        else:
            filters.append(
                f"[{mask_index}:a]aresample=44100,"
                + f"asplit={len(mask_labels)}"
                + "".join(f"[{label}]" for label in mask_labels)
            )

    output_labels: list[str] = []
    duck_index = 0
    for index, (clip, _source, _seek, natural_duration) in enumerate(
        prepared,
        start=1,
    ):
        available = max(0.01, natural_duration)
        playback = (
            clip.playback_duration_ms / 1000
            if clip.playback_duration_ms is not None
            else available
        )
        if not clip.loop:
            playback = min(playback, available)
        playback = max(0.01, playback)
        start_seconds = max(
            0.0,
            (clip.timeline_start_ms + voice_offset_ms) / 1000,
        )
        chain = [
            audio_format,
            f"atrim=duration={playback:.3f}",
            "asetpts=N/SR/TB",
            f"volume={clip.volume_db:.3f}dB",
        ]
        if abs(clip.pan) > 0.001:
            chain.append(f"stereotools=balance_out={clip.pan:.4f}")
        fade_in = min(playback / 2, max(0.0, clip.fade_in_ms / 1000))
        fade_out = min(playback / 2, max(0.0, clip.fade_out_ms / 1000))
        if fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={fade_in:.3f}")
        if fade_out > 0:
            chain.append(
                f"afade=t=out:st={max(0.0, playback - fade_out):.3f}:d={fade_out:.3f}"
            )
        if start_seconds > 0:
            chain.append(f"adelay={round(start_seconds * 1000)}:all=1")
        pre_label = f"clip_{index}_pre"
        filters.append(f"[{index}:a]" + ",".join(chain) + f"[{pre_label}]")
        output_label = f"clip_{index}"
        if clip.duck_db > 0 and mask_labels:
            ratio = 20.0
            threshold_db = -clip.duck_db / (1.0 - 1.0 / ratio)
            threshold = max(0.000976, min(1.0, 10 ** (threshold_db / 20)))
            filters.append(
                f"[{pre_label}][{mask_labels[duck_index]}]"
                "sidechaincompress="
                f"threshold={threshold:.6f}:ratio={ratio:.1f}:"
                "attack=30:release=250"
                f"[{output_label}]"
            )
            duck_index += 1
        else:
            filters.append(f"[{pre_label}]anull[{output_label}]")
        output_labels.append(output_label)

    mix_inputs = "[stem_base]" + "".join(f"[{label}]" for label in output_labels)
    filters.append(
        mix_inputs
        + f"amix=inputs={len(output_labels) + 1}:duration=first:normalize=0[stem]"
    )
    arguments.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[stem]",
            "-codec:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    runner.run(arguments)


def _prepared_clip_source(
    clip: ResolvedAudioClip,
    cache_dir: Path,
    ffmpeg_path: str | Path,
) -> tuple[Path, float, float]:
    source = Path(clip.file_path)
    source_seek = max(0.0, clip.source_start_ms / 1000)
    source_duration = probe_audio_duration(source, ffmpeg_path)
    if not clip.trim_silence and not (clip.loop and source_seek > 0):
        return source, source_seek, max(0.01, source_duration - source_seek)

    stat = source.stat()
    signature = hashlib.sha256(
        json.dumps(
            [
                str(source),
                stat.st_mtime_ns,
                stat.st_size,
                clip.source_start_ms,
                clip.trim_silence,
                clip.loop,
            ],
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:16]
    prepared = cache_dir / f"prepared_{signature}.wav"
    if not prepared.is_file():
        arguments = ["-y", "-hide_banner", "-loglevel", "error"]
        if source_seek > 0:
            arguments.extend(["-ss", f"{source_seek:.3f}"])
        arguments.extend(["-i", str(source)])
        if clip.trim_silence:
            arguments.extend(
                [
                    "-filter:a",
                    (
                        "silenceremove=start_periods=1:start_silence=0.05:"
                        "start_threshold=-50dB,areverse,"
                        "silenceremove=start_periods=1:start_silence=0.05:"
                        "start_threshold=-50dB,areverse"
                    ),
                ]
            )
        arguments.extend(["-codec:a", "pcm_s16le", str(prepared)])
        FFmpegRunner(find_ffmpeg(ffmpeg_path)).run(arguments)
    return prepared, 0.0, max(0.01, probe_audio_duration(prepared, ffmpeg_path))


def _write_duck_mask(
    output_path: Path,
    intervals: tuple[SpeechInterval, ...],
    duration_seconds: float,
) -> None:
    sample_rate = 1000
    sample_count = max(1, math.ceil(duration_seconds * sample_rate))
    samples = array("h", [0]) * sample_count
    for interval in intervals:
        start = max(0, min(sample_count, round(interval.start_ms)))
        end = max(start, min(sample_count, round(interval.end_ms)))
        if end > start:
            samples[start:end] = array("h", [32767]) * (end - start)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples.tobytes())


def _stem_signature(
    clips: tuple[ResolvedAudioClip, ...],
    intervals: tuple[SpeechInterval, ...],
    duration_seconds: float,
    voice_offset_ms: int,
) -> str:
    payload: list[object] = [round(duration_seconds, 3), voice_offset_ms]
    for clip in clips:
        path = Path(clip.file_path)
        try:
            stat = path.stat()
            file_signature: object = (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            file_signature = (str(path), 0, 0)
        payload.append((clip, file_signature))
    if any(clip.duck_db > 0 for clip in clips):
        payload.append([(item.start_ms, item.end_ms) for item in intervals])
    return hashlib.sha256(
        repr(payload).encode("utf-8")
    ).hexdigest()[:20]


def _speech_signature(
    intervals: tuple[SpeechInterval, ...],
    duration_seconds: float,
) -> str:
    payload = [
        round(duration_seconds, 3),
        [(item.start_ms, item.end_ms) for item in intervals],
    ]
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:20]


def ducking_filter(strength: str) -> str:
    profiles = {
        "low": "sidechaincompress=threshold=0.035:ratio=3:attack=30:release=350",
        "medium": "sidechaincompress=threshold=0.025:ratio=6:attack=20:release=500",
        "high": "sidechaincompress=threshold=0.018:ratio=10:attack=15:release=650",
    }
    return profiles.get(strength, profiles["medium"])
