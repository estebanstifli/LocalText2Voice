from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.core.audio_formats import (
    AUDIO_FORMATS,
    audio_format_from_path,
    encoding_arguments,
)
from app.core.audio_pipeline import AudioGenerationOptions, AudioPipeline
from app.core.audio_mix import AudioMixSettings, render_audio_mix
from app.core.audiobook_store import AudiobookStore, PROJECT_MANIFEST_NAME
from app.server.job_manager import ServerJob
from tests.test_audio_pipeline import FakeTTSEngine


def test_central_registry_describes_all_supported_output_formats() -> None:
    assert set(AUDIO_FORMATS) == {"mp3", "m4a", "m4b", "opus", "flac", "ogg"}
    assert {spec.extension for spec in AUDIO_FORMATS.values()} == {
        ".mp3",
        ".m4a",
        ".m4b",
        ".opus",
        ".flac",
        ".ogg",
    }
    assert all(spec.mime_type.startswith("audio/") for spec in AUDIO_FORMATS.values())
    assert all(spec.qualities for spec in AUDIO_FORMATS.values())
    assert AUDIO_FORMATS["m4b"].supports_chapters
    assert all(
        not spec.supports_chapters
        for format_id, spec in AUDIO_FORMATS.items()
        if format_id != "m4b"
    )


@pytest.mark.parametrize("format_id", AUDIO_FORMATS)
def test_ffmpeg_arguments_use_registered_encoder_and_container(format_id: str) -> None:
    spec = AUDIO_FORMATS[format_id]
    arguments = encoding_arguments(format_id, spec.default_quality)

    assert arguments[arguments.index("-codec:a") + 1] == spec.encoder
    assert arguments[arguments.index("-f") + 1] == spec.container


@pytest.mark.parametrize("format_id", AUDIO_FORMATS)
def test_pipeline_exports_one_file_in_selected_format(
    tmp_path: Path,
    format_id: str,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is not available")
    spec = AUDIO_FORMATS[format_id]
    options = AudioGenerationOptions(
        output_dir=tmp_path,
        voice_config={"speed": 1.0},
        ffmpeg_path=ffmpeg,
        split_mode="chapters",
        export_mode="chapters",  # accepted as a legacy API value
        audio_format=format_id,
        audio_quality=spec.default_quality,
        pause_between_blocks_ms=0,
    )

    outputs = AudioPipeline(FakeTTSEngine()).generate(
        "Chapter 1\nFirst body.\n\nChapter 2\nSecond body.",
        options,
    )

    assert len(outputs) == 1
    assert outputs[0].suffix == spec.extension
    assert outputs[0].stat().st_size > 0
    assert audio_format_from_path(outputs[0]) == spec


def test_project_store_uses_generic_paths_and_keeps_mp3_aliases(
    tmp_path: Path,
) -> None:
    store = AudiobookStore(tmp_path / "projects.sqlite3")
    project_dir = tmp_path / "project"
    project = store.create_audiobook(
        "Text",
        {"engine": "piper"},
        project_dir / "exports",
        "safe_chunks",
        "single",
        project_dir=project_dir,
        project_settings={"audio_format": "m4a", "audio_quality": "standard"},
    )
    clean = project.output_dir / "podcast1.m4a"
    mix = project.output_dir / "podcast1_mix.m4a"
    store.complete_audiobook(project.id, [clean, mix])

    saved = store.get_audiobook(project.id)
    assert saved is not None
    assert saved.clean_audio_path == str(clean)
    assert saved.mix_audio_path == str(mix)
    assert saved.clean_mp3_path == saved.clean_audio_path
    assert saved.mix_mp3_path == saved.mix_audio_path
    manifest = json.loads(
        (project_dir / PROJECT_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert manifest["clean_audio_path"] == "exports/podcast1.m4a"
    assert manifest["clean_mp3_path"] == manifest["clean_audio_path"]


@pytest.mark.parametrize("format_id", AUDIO_FORMATS)
def test_audio_mix_renders_in_selected_format(
    tmp_path: Path,
    format_id: str,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is not available")
    spec = AUDIO_FORMATS[format_id]
    voice_path = tmp_path / "voice.wav"
    FakeTTSEngine().synthesize_to_wav("Voice", voice_path, {"speed": 1.0})
    output_path = tmp_path / f"mix{spec.extension}"

    render_audio_mix(
        voice_path,
        output_path,
        ffmpeg,
        AudioMixSettings(
            voice_start_offset_ms=0,
            music_tail_ms=0,
            audio_format=format_id,
            audio_quality=spec.default_quality,
        ),
        voice_duration_seconds=0.08,
    )

    assert output_path.is_file()
    assert output_path.stat().st_size > 0


def test_server_job_exposes_generic_paths_and_legacy_api_aliases() -> None:
    job = ServerJob(
        job_id="format-job",
        status="complete",
        title="Book",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:01:00Z",
        clean_audio_path="book.m4a",
        mix_audio_path="book_mix.m4a",
    )

    payload = job.to_dict()
    assert payload["clean_audio_path"] == "book.m4a"
    assert payload["mix_audio_path"] == "book_mix.m4a"
    assert payload["clean_mp3_path"] == payload["clean_audio_path"]
    assert payload["mix_mp3_path"] == payload["mix_audio_path"]
