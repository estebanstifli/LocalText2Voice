from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from app.utils.ffmpeg_utils import FFmpegCancelled, FFmpegError, find_ffmpeg


RenderProgress = Callable[[str, int], None]

_XFADE_TRANSITIONS = {
    "fade",
    "dissolve",
    "wipeleft",
    "wiperight",
    "smoothleft",
    "smoothright",
    "circleopen",
    "circleclose",
}


def render_storyboard_video(
    scenes: list[dict[str, Any]],
    audio_path: Path,
    output_path: Path,
    settings: dict[str, Any],
    *,
    progress: RenderProgress | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Render storyboard stills and narration into an atomic MP4 output."""
    report = progress or (lambda _stage, _percentage: None)
    is_cancelled = cancelled or (lambda: False)
    report("validating", 0)

    if not scenes:
        raise FFmpegError("The storyboard has no scenes to render.")
    if not audio_path.is_file():
        raise FFmpegError(
            "The audiobook audio file is missing. Generate the audiobook audio "
            "before rendering the storyboard."
        )

    normalized: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes, start=1):
        image = Path(str(scene.get("image_path") or ""))
        if not image.is_file():
            scene_id = str(scene.get("scene_id") or index)
            raise FFmpegError(
                f"Scene {scene_id} has no generated frame. Generate all frames "
                "before rendering the final video."
            )
        generated_video = Path(str(scene.get("video_path") or ""))
        normalized.append(
            {
                **scene,
                "image": image,
                "generated_video": (
                    generated_video if generated_video.is_file() else None
                ),
                "duration": max(0.1, float(scene.get("duration_seconds") or 0.0)),
            }
        )

    video = settings.get("video", {}) if isinstance(settings, dict) else {}
    fps = max(12, min(60, int(video.get("fps") or 30)))
    transition_seconds = max(
        0.0,
        min(3.0, float(video.get("transition_seconds") or 0.0)),
    )
    raw_zoom = video.get("zoom_percent", 30.0)
    zoom_percent = max(
        0.0,
        float(30.0 if raw_zoom is None else raw_zoom),
    )
    codec = str(video.get("codec") or "libx264")
    crf = max(0, min(51, int(video.get("crf") or 18)))
    preset = str(video.get("preset") or "medium")
    supersample = max(1, min(4, int(video.get("supersample") or 4)))
    generated_frames = max(
        1,
        int(settings.get("comfyui_video", {}).get("frames") or 49),
    )
    generated_fps = max(
        1.0,
        float(settings.get("comfyui_video", {}).get("fps") or 24.0),
    )
    default_generated_duration = generated_frames / generated_fps
    width = max(64, int(settings.get("image", {}).get("width") or 1280))
    height = max(64, int(settings.get("image", {}).get("height") or 720))
    width -= width % 2
    height -= height % 2

    edge_durations = _transition_edge_durations(
        normalized,
        transition_seconds,
        str(video.get("transition") or "fade"),
    )
    total_duration = sum(scene["duration"] for scene in normalized)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_name(f"{output_path.stem}.part.mp4")
    temporary_output.unlink(missing_ok=True)
    executable = find_ffmpeg(settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"))
    try:
        # FFmpeg 7.x can lose the frame-rate metadata when xfade consumes
        # image-pipe filters directly. Small CFR scene clips make xfade stable
        # and also keep very large storyboards below command/filter limits.
        with tempfile.TemporaryDirectory(
            prefix="storyboard-render-",
            dir=output_path.parent,
        ) as temporary_dir:
            clip_paths: list[Path] = []
            for index, scene in enumerate(normalized):
                extended = scene["duration"] + (
                    edge_durations[index] if index < len(edge_durations) else 0.0
                )
                clip = Path(temporary_dir) / f"scene-{index:04d}.mp4"
                clip_paths.append(clip)
                generated_video = scene.get("generated_video")
                if isinstance(generated_video, Path):
                    scene_filter = _generated_scene_video_filter(
                        scene,
                        extended,
                        fps,
                        width,
                        height,
                        zoom_percent,
                        supersample,
                        max(
                            0.1,
                            float(
                                scene.get("video_duration_seconds")
                                or default_generated_duration
                            ),
                        ),
                    )
                    media_input = generated_video
                else:
                    scene_filter = _scene_video_filter(
                        scene,
                        extended,
                        fps,
                        width,
                        height,
                        zoom_percent,
                        supersample,
                    )
                    media_input = scene["image"]
                clip_arguments = [
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(media_input),
                    "-vf",
                    scene_filter,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    preset,
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-frames:v",
                    str(max(1, round(extended * fps))),
                    "-r",
                    str(fps),
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    str(clip),
                ]
                _run_ffmpeg_with_progress(
                    executable,
                    clip_arguments,
                    extended,
                    lambda _stage, value, item=index: report(
                        "preparing",
                        min(45, round((item + value / 100) / len(normalized) * 45)),
                    ),
                    is_cancelled,
                )

            arguments = ["-y", "-hide_banner", "-loglevel", "error"]
            for clip in clip_paths:
                arguments.extend(["-i", str(clip)])
            audio_input = len(clip_paths)
            arguments.extend(["-i", str(audio_path)])
            filters: list[str] = []
            video_output = "0:v:0"
            elapsed = normalized[0]["duration"]
            for index in range(1, len(normalized)):
                output_label = f"vx{index}"
                duration = edge_durations[index - 1]
                left = f"[{video_output}]"
                if duration > 0:
                    transition = str(
                        normalized[index].get("transition")
                        or video.get("transition")
                        or "fade"
                    ).lower()
                    if transition not in _XFADE_TRANSITIONS:
                        transition = "fade"
                    filters.append(
                        f"{left}[{index}:v:0]xfade=transition={transition}:"
                        f"duration={_decimal(duration)}:offset={_decimal(elapsed)}"
                        f"[{output_label}]"
                    )
                else:
                    filters.append(
                        f"{left}[{index}:v:0]concat=n=2:v=1:a=0[{output_label}]"
                    )
                video_output = output_label
                elapsed += normalized[index]["duration"]
            filters.append(
                f"[{audio_input}:a]apad,atrim=duration={_decimal(total_duration)},"
                "asetpts=PTS-STARTPTS[aout]"
            )
            arguments.extend(["-filter_complex", ";".join(filters)])
            arguments.extend(
                [
                    "-map",
                    f"[{video_output}]" if video_output.startswith("vx") else video_output,
                    "-map",
                    "[aout]",
                    "-t",
                    _decimal(total_duration),
                    "-c:v",
                    codec,
                    "-preset",
                    preset,
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    str(temporary_output),
                ]
            )
            _run_ffmpeg_with_progress(
                executable,
                arguments,
                total_duration,
                lambda _stage, value: report(
                    "rendering", min(98, 45 + round(value * 0.53))
                ),
                is_cancelled,
            )
        if not temporary_output.is_file() or temporary_output.stat().st_size <= 0:
            raise FFmpegError("FFmpeg completed without creating the final video.")
        report("finalizing", 99)
        os.replace(temporary_output, output_path)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise
    report("complete", 100)
    return {
        "output_path": str(output_path),
        "duration_seconds": total_duration,
        "scene_count": len(normalized),
        "fps": fps,
    }


def _scene_video_filter(
    scene: dict[str, Any],
    duration: float,
    fps: int,
    width: int,
    height: int,
    zoom_percent: float,
    supersample: int,
) -> str:
    frame_count = max(1, round(duration * fps))
    last_frame = max(1, frame_count - 1)
    zoom_fraction = zoom_percent / 100.0
    motion_in = str(scene.get("motion_in") or "").lower()
    motion_out = str(scene.get("motion_out") or "").lower()
    if motion_in or motion_out:
        zoom = _two_phase_zoom_expression(
            motion_in or "none",
            motion_out or "none",
            zoom_fraction,
            last_frame,
        )
    else:
        motion = str(scene.get("motion") or "zoom_in").lower()
        if motion == "zoom_in":
            zoom = f"1+{zoom_fraction:.6f}*on/{last_frame}"
        elif motion == "zoom_out":
            zoom = f"{1 + zoom_fraction:.6f}-{zoom_fraction:.6f}*on/{last_frame}"
        else:
            zoom = "1"
    render_width = width * supersample
    render_height = height * supersample
    return (
        f"scale={render_width}:{render_height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={render_width}:{render_height},"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':d={frame_count}:s={width}x{height}:fps={fps},"
        "setsar=1,format=yuv420p"
    )


def _generated_scene_video_filter(
    scene: dict[str, Any],
    duration: float,
    fps: int,
    width: int,
    height: int,
    zoom_percent: float,
    supersample: int,
    source_duration: float,
) -> str:
    """Fit an accepted I2V clip to its scene and apply video-only motion."""
    target_duration = max(0.1, float(duration))
    original_duration = max(0.1, float(source_duration))
    speed_factor = target_duration / original_duration
    render_width = width * max(1, supersample)
    render_height = height * max(1, supersample)
    filters = [
        (
            f"scale={render_width}:{render_height}:"
            "force_original_aspect_ratio=increase:flags=lanczos"
        ),
        f"crop={render_width}:{render_height}",
        f"fps={fps}",
    ]
    motion_in = str(scene.get("video_motion_in") or "none").lower()
    motion_out = str(scene.get("video_motion_out") or "none").lower()
    if motion_in in {"zoom_in", "zoom_out"} or motion_out in {
        "zoom_in",
        "zoom_out",
    }:
        source_frames = max(1, round(original_duration * fps))
        zoom = _two_phase_zoom_expression(
            motion_in,
            motion_out,
            max(0.0, float(zoom_percent)) / 100.0,
            max(1, source_frames - 1),
        )
        filters.append(
            f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}"
        )
    else:
        filters.append(f"scale={width}:{height}:flags=lanczos")
    filters.extend(
        [
            f"setpts={speed_factor:.9f}*(PTS-STARTPTS)",
            f"fps={fps}",
            f"tpad=stop_mode=clone:stop_duration={_decimal(target_duration)}",
            f"trim=duration={_decimal(target_duration)}",
            "setpts=PTS-STARTPTS",
            "setsar=1",
            "format=yuv420p",
        ]
    )
    return ",".join(filters)


def _transition_edge_durations(
    scenes: list[dict[str, Any]],
    transition_seconds: float,
    default_transition: str,
) -> list[float]:
    durations: list[float] = []
    for index in range(len(scenes) - 1):
        transition = str(
            scenes[index + 1].get("transition")
            or default_transition
            or "fade"
        ).lower()
        durations.append(
            0.0
            if transition in {"none", "cut"}
            else min(
                transition_seconds,
                float(scenes[index]["duration"]) / 2,
                float(scenes[index + 1]["duration"]) / 2,
            )
        )
    return durations


def _two_phase_zoom_expression(
    motion_in: str,
    motion_out: str,
    zoom_fraction: float,
    last_frame: int,
) -> str:
    def delta(value: str) -> float:
        if value == "zoom_in":
            return zoom_fraction
        if value == "zoom_out":
            return -zoom_fraction
        return 0.0

    first_delta = delta(motion_in)
    second_delta = delta(motion_out)
    minimum_offset = min(0.0, first_delta, first_delta + second_delta)
    start = 1.0 - minimum_offset
    if last_frame <= 1:
        return f"{start + first_delta + second_delta:.6f}"
    midpoint_frame = max(1, last_frame // 2)
    second_frames = max(1, last_frame - midpoint_frame)
    midpoint = start + first_delta
    first = f"{start:.6f}+({first_delta:.6f})*on/{midpoint_frame}"
    second = (
        f"{midpoint:.6f}+({second_delta:.6f})*"
        f"(on-{midpoint_frame})/{second_frames}"
    )
    return f"if(lte(on,{midpoint_frame}),{first},{second})"


def _run_ffmpeg_with_progress(
    executable: Path,
    arguments: list[str],
    total_duration: float,
    progress: RenderProgress,
    cancelled: Callable[[], bool],
) -> None:
    creation_flags = (
        subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    )
    with tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                [str(executable), *arguments],
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise FFmpegError(f"Could not start FFmpeg: {exc}") from exc

        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                if cancelled():
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise FFmpegCancelled("Video rendering was cancelled.")
                key, separator, value = raw_line.strip().partition("=")
                if separator and key == "out_time":
                    rendered = _parse_ffmpeg_time(value)
                    percentage = min(
                        98,
                        max(1, round(rendered / max(0.1, total_duration) * 98)),
                    )
                    progress("rendering", percentage)
            return_code = process.wait()
        finally:
            process.stdout.close()

        if return_code != 0:
            stderr_file.seek(0)
            details = stderr_file.read().decode("utf-8", errors="replace").strip()
            if len(details) > 4000:
                details = details[-4000:]
            raise FFmpegError(
                f"FFmpeg failed with exit code {return_code}:\n"
                f"{details or 'No error details were returned.'}"
            )


def _parse_ffmpeg_time(value: str) -> float:
    try:
        hours, minutes, seconds = value.split(":", 2)
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return 0.0


def _decimal(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")
