"""Audio from scene videos, synchronized independently of the audiobook mix."""
import re
import subprocess
from pathlib import Path

from app.utils.ffmpeg_utils import FFmpegError, FFmpegCancelled


def probe_clip(executable, path):
    try:
        result = subprocess.run(
            [str(executable), '-hide_banner', '-i', str(path)],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FFmpegError(f'Cannot inspect clip audio: {path}: {exc}') from exc
    text = result.stderr.decode('utf-8', errors='replace')
    match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
    duration = (int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])) if match else 0
    if not re.search(r'Stream #.*(?:Video|Audio):', text):
        raise FFmpegError(f'Cannot read clip: {path}')
    return bool(re.search(r'Stream #.*Audio:', text)), duration


def tempo_filter(rate):
    """Chain atempo within 0.5–2 so slowed dialogue retains its pitch."""
    rate = max(0.001, float(rate))
    parts = []
    while rate < 0.5:
        parts.append('atempo=0.5')
        rate /= 0.5
    while rate > 2:
        parts.append('atempo=2')
        rate /= 2
    parts.append(f'atempo={rate:.9f}')
    return ','.join(parts)


def build_clip_audio_track(scenes, executable, directory, run, cancelled, report):
    """Create a separate lossless track; silent/muted scenes consume no inputs."""
    inputs, filters, labels = [], [], []
    elapsed = 0.0
    for scene in scenes:
        if cancelled():
            raise FFmpegCancelled('Video rendering cancelled.')
        duration = float(scene['duration'])
        path = scene.get('generated_video')
        volume = max(0, min(4, float(scene.get('video_audio_volume', 1))))
        if path and scene.get('video_audio_enabled', True) and volume > 0:
            has_audio, measured = probe_clip(executable, path)
            if has_audio:
                index = len(labels)
                inputs.extend(['-i', str(path)])
                original = float(scene.get('video_duration_seconds') or measured or duration)
                label = f'clip{index}'
                # Rebuild timestamps from samples after tempo/delay. Otherwise
                # FFmpeg can drop the leading silence when a later atrim runs.
                filters.append(
                    f'[{index}:a:0]asetpts=PTS-STARTPTS,{tempo_filter(original / duration)},'
                    f'aresample=48000,aformat=channel_layouts=stereo,asetpts=N/SR/TB,apad=whole_dur={duration:.6f},'
                    f'atrim=duration={duration:.6f},volume={volume:.6f},'
                    f'adelay={round(elapsed * 48000)}S:all=1,asetpts=N/SR/TB[{label}]'
                )
                labels.append(label)
        elapsed += duration
    if not labels:
        return None
    mix = f'amix=inputs={len(labels)}:normalize=0:duration=longest' if len(labels) > 1 else 'anull'
    filters.append(''.join(f'[{label}]' for label in labels) +
                   f'{mix},asetpts=N/SR/TB,apad=whole_dur={elapsed:.6f},atrim=duration={elapsed:.6f}[track]')
    script = Path(directory) / 'clip-audio.filters'
    script.write_text(';'.join(filters), encoding='utf-8')
    target = Path(directory) / 'clip-audio.wav'
    run(executable, ['-y', '-hide_banner', '-loglevel', 'error', *inputs,
        '-filter_complex_script', str(script), '-map', '[track]', '-t', f'{elapsed:.6f}', '-c:a', 'pcm_f32le',
        '-progress', 'pipe:1', '-nostats', str(target)], elapsed, report, cancelled)
    return target
