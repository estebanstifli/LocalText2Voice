from __future__ import annotations

import wave
import subprocess
from pathlib import Path

import pytest

from app.core.video_storyboard_renderer import (
    _generated_scene_video_filter,
    _scene_video_filter,
    _transition_edge_durations,
    _two_phase_zoom_expression,
    render_storyboard_video,
)
from app.utils.ffmpeg_utils import FFmpegError


def _write_ppm(path: Path, red: int, green: int, blue: int) -> None:
    width, height = 64, 36
    path.write_bytes(
        f"P6\n{width} {height}\n255\n".encode("ascii")
        + bytes((red, green, blue)) * width * height
    )


def _write_silent_wav(path: Path, duration: float) -> None:
    sample_rate = 8_000
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * round(sample_rate * duration))


def test_renderer_rejects_a_scene_without_a_generated_frame(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    _write_silent_wav(audio, 1.0)
    with pytest.raises(FFmpegError, match="Scene 001 has no generated frame"):
        render_storyboard_video(
            [{"scene_id": "001", "duration_seconds": 1.0}],
            audio,
            tmp_path / "video.mp4",
            {"ffmpeg_path": "ffmpeg/ffmpeg.exe"},
        )


def test_scene_filters_match_reference_supersampled_linear_zoom() -> None:
    zoom_in = _scene_video_filter(
        {"motion": "zoom_in"}, 6.0, 30, 1280, 720, 8.0, 4
    )
    zoom_out = _scene_video_filter(
        {"motion": "zoom_out"}, 6.0, 30, 1280, 720, 8.0, 4
    )

    assert "scale=5120:2880:force_original_aspect_ratio=increase:flags=lanczos" in zoom_in
    assert "zoompan=z='1+0.080000*on/179'" in zoom_in
    assert "zoompan=z='1.080000-0.080000*on/179'" in zoom_out
    assert "d=180:s=1280x720:fps=30" in zoom_in


def test_scene_filter_supports_independent_in_and_out_motion() -> None:
    expression = _two_phase_zoom_expression(
        "zoom_in", "zoom_out", 0.3, 179
    )
    filtered = _scene_video_filter(
        {"motion_in": "zoom_in", "motion_out": "zoom_out"},
        6.0,
        30,
        1280,
        720,
        30.0,
        4,
    )

    assert expression.startswith("if(lte(on,89)")
    assert "1.000000+(0.300000)*on/89" in expression
    assert "1.300000+(-0.300000)*(on-89)/90" in expression
    assert f"zoompan=z='{expression}'" in filtered


def test_none_transition_creates_a_hard_cut() -> None:
    scenes = [
        {"duration": 8.0, "transition": "fade"},
        {"duration": 6.0, "transition": "none"},
        {"duration": 5.0, "transition": "dissolve"},
    ]

    assert _transition_edge_durations(scenes, 0.7, "fade") == [0.0, 0.7]


def test_generated_video_filter_fits_scene_without_bars_or_still_motion() -> None:
    filtered = _generated_scene_video_filter(
        {}, 8.5, 30, 1280, 720, 30.0, 4, 2.0
    )

    assert "scale=5120:2880:force_original_aspect_ratio=increase" in filtered
    assert "crop=5120:2880" in filtered
    assert "setpts=4.250000000*(PTS-STARTPTS)" in filtered
    assert "tpad=stop_mode=clone:stop_duration=8.5" in filtered
    assert "trim=duration=8.5" in filtered
    assert "zoompan" not in filtered


def test_generated_video_filter_uses_independent_video_motion() -> None:
    filtered = _generated_scene_video_filter(
        {"video_motion_in": "zoom_in", "video_motion_out": "zoom_out"},
        8.0,
        24,
        1280,
        720,
        30.0,
        2,
        8.0,
    )

    assert "zoompan=z='if(lte(on,95)" in filtered
    assert "d=1:s=1280x720:fps=24" in filtered


@pytest.mark.skipif(
    not Path("ffmpeg/ffmpeg.exe").is_file(),
    reason="Bundled FFmpeg is required for the real render test",
)
def test_renderer_creates_real_mp4_with_transition_audio_and_progress(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.ppm"
    second = tmp_path / "second.ppm"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "storyboard.mp4"
    _write_ppm(first, 200, 30, 20)
    _write_ppm(second, 20, 60, 210)
    _write_silent_wav(audio, 1.2)
    updates: list[tuple[str, int]] = []

    result = render_storyboard_video(
        [
            {
                "scene_id": "001",
                "duration_seconds": 0.6,
                "image_path": str(first),
                "motion": "zoom_in",
                "transition": "dissolve",
            },
            {
                "scene_id": "002",
                "duration_seconds": 0.6,
                "image_path": str(second),
                "motion": "zoom_out",
            },
        ],
        audio,
        output,
        {
            "ffmpeg_path": "ffmpeg/ffmpeg.exe",
            "image": {"width": 320, "height": 180},
            "video": {
                "fps": 12,
                "transition": "fade",
                "transition_seconds": 0.2,
                "zoom_percent": 4,
                "codec": "libx264",
                "crf": 28,
            },
        },
        progress=lambda stage, percentage: updates.append((stage, percentage)),
    )

    assert output.is_file()
    assert output.stat().st_size > 1_000
    assert result["output_path"] == str(output)
    assert result["duration_seconds"] == pytest.approx(1.2)
    assert updates[0] == ("validating", 0)
    assert updates[-1] == ("complete", 100)
    assert not (tmp_path / "storyboard.part.mp4").exists()


@pytest.mark.skipif(
    not Path("ffmpeg/ffmpeg.exe").is_file(),
    reason="Bundled FFmpeg is required for the real video-layer test",
)
def test_renderer_retimes_generated_video_and_applies_video_motion(
    tmp_path: Path,
) -> None:
    ffmpeg = Path("ffmpeg/ffmpeg.exe").resolve()
    frame = tmp_path / "reference.ppm"
    source_video = tmp_path / "generated.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "with-video.mp4"
    _write_ppm(frame, 40, 130, 210)
    _write_silent_wav(audio, 1.2)
    subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x2882d2:s=160x90:r=12:d=0.5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source_video),
        ],
        check=True,
    )

    result = render_storyboard_video(
        [
            {
                "scene_id": "001",
                "duration_seconds": 1.2,
                "image_path": str(frame),
                "video_path": str(source_video),
                "video_duration_seconds": 0.5,
                "video_motion_in": "zoom_in",
                "video_motion_out": "zoom_out",
            }
        ],
        audio,
        output,
        {
            "ffmpeg_path": str(ffmpeg),
            "image": {"width": 320, "height": 180},
            "video": {
                "fps": 12,
                "transition_seconds": 0,
                "zoom_percent": 10,
                "supersample": 1,
                "crf": 28,
            },
        },
    )

    assert result["duration_seconds"] == pytest.approx(1.2)
    assert output.is_file()
    assert output.stat().st_size > 1_000
