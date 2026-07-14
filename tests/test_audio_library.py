from __future__ import annotations

from pathlib import Path

from app.core.audio_library import (
    audio_library_files,
    library_directory,
    resolve_audio_reference,
)


def test_library_paths_can_be_relative_or_absolute(tmp_path: Path) -> None:
    external = tmp_path / "external-sfx"
    settings = {
        "music_library_dir": "custom/music",
        "sfx_library_dir": str(external),
    }

    assert library_directory(settings, "music", root=tmp_path) == (
        tmp_path / "custom/music"
    ).resolve()
    assert library_directory(settings, "sfx", root=tmp_path) == external.resolve()


def test_bare_play_name_searches_sfx_then_music_recursively(tmp_path: Path) -> None:
    music = tmp_path / "music/background/albums"
    sfx = tmp_path / "music/sfx/doors"
    music.mkdir(parents=True)
    sfx.mkdir(parents=True)
    (music / "hit.mp3").write_bytes(b"music")
    expected = sfx / "hit.mp3"
    expected.write_bytes(b"sfx")
    settings = {
        "music_library_dir": "music/background",
        "sfx_library_dir": "music/sfx",
    }

    assert resolve_audio_reference("HIT.MP3", settings, root=tmp_path) == expected.resolve()
    assert [path.name for path in audio_library_files(tmp_path / "music/sfx")] == [
        "hit.mp3"
    ]


def test_explicit_play_path_does_not_fall_back_to_an_unrelated_basename(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "music/sfx/doors"
    nested.mkdir(parents=True)
    (nested / "close.mp3").write_bytes(b"sfx")

    assert resolve_audio_reference(
        "wrong/close.mp3",
        {},
        root=tmp_path,
    ) is None
