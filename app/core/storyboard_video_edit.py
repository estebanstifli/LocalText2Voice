"""Non-destructive edits expressed as ordered ranges of one source video."""
from __future__ import annotations

import math

Segment = tuple[float, float]


def duration(segments: list[Segment]) -> float:
    return sum(end - start for start, end in segments)


def slice_segments(segments: list[Segment], start: float, end: float) -> list[Segment]:
    result = []
    offset = 0.0
    for source_in, source_out in segments:
        length = source_out - source_in
        left, right = max(start, offset), min(end, offset + length)
        if right - left > 0.000001:
            result.append((source_in + left - offset, source_in + right - offset))
        offset += length
    return result


def source_position(segments: list[Segment], position: float) -> tuple[int, float]:
    offset = 0.0
    for index, (start, end) in enumerate(segments):
        length = end - start
        if position < offset + length or index == len(segments) - 1:
            return index, min(end, start + max(0, position - offset))
        offset += length
    return 0, 0.0


def export_arguments(source: str, target: str, segments: list[Segment], has_audio: bool) -> list[str]:
    if not segments or any(not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a for a, b in segments):
        raise ValueError("The edit must contain valid video ranges.")
    count = len(segments)
    filters = []
    if count > 1:
        filters.append(f"[0:v:0]split={count}" + "".join(f"[srcv{i}]" for i in range(count)))
        if has_audio:
            filters.append(f"[0:a:0]asplit={count}" + "".join(f"[srca{i}]" for i in range(count)))
    for i, (start, end) in enumerate(segments):
        video = f"srcv{i}" if count > 1 else "0:v:0"
        filters.append(f"[{video}]trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS,setsar=1[v{i}]")
        if has_audio:
            audio = f"srca{i}" if count > 1 else "0:a:0"
            filters.append(f"[{audio}]atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS[a{i}]")
    inputs = "".join(f"[v{i}]" + (f"[a{i}]" if has_audio else "") for i in range(count))
    filters.append(inputs + f"concat=n={count}:v=1:a={int(has_audio)}[v]" + ("[a]" if has_audio else ""))
    # Pad odd dimensions for H.264 without stretching the image.
    filters.append("[v]pad=ceil(iw/2)*2:ceil(ih/2)*2[outv]")
    args = ["-hide_banner", "-loglevel", "error", "-i", source, "-filter_complex", ";".join(filters), "-map", "[outv]"]
    if has_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-y", target]
    return args
