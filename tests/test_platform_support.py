"""Tests for Linux/cross-platform support helpers."""
from __future__ import annotations

import os
from pathlib import Path

from app.core.audio_pipeline import _move_file
from app.utils.ffmpeg_utils import find_ffmpeg
from app.utils.paths import resolve_executable


class TestResolveExecutable:
    def test_configured_exe_found_as_is(self, tmp_path: Path) -> None:
        exe = tmp_path / "tool.exe"
        exe.write_text("bin")
        assert resolve_executable(exe) == exe

    def test_extensionless_sibling_used_on_linux(self, tmp_path: Path) -> None:
        # Windows-style config points at tool.exe, but on Linux the bundled
        # folder holds an extension-less binary named "tool".
        sibling = tmp_path / "tool"
        sibling.write_text("bin")
        assert resolve_executable(tmp_path / "tool.exe") == sibling

    def test_missing_everywhere_returns_configured(self, tmp_path: Path) -> None:
        configured = tmp_path / "definitely-missing-tool-xyz.exe"
        assert resolve_executable(configured) == configured


class TestFindFFmpeg:
    def test_extensionless_bundled_binary(self, tmp_path: Path) -> None:
        bundled = tmp_path / "ffmpeg"
        bundled.write_text("bin")
        assert find_ffmpeg(tmp_path / "ffmpeg.exe") == bundled


class TestMoveFile:
    def test_same_filesystem(self, tmp_path: Path) -> None:
        src = tmp_path / "a.mp3"
        dst = tmp_path / "b.mp3"
        src.write_bytes(b"audio")
        _move_file(src, dst)
        assert dst.read_bytes() == b"audio"
        assert not src.exists()

    def test_cross_device_exdev_falls_back(self, tmp_path: Path, monkeypatch) -> None:
        """os.replace raising EXDEV (tmpfs /tmp -> ext4 home on Linux)."""
        src = tmp_path / "a.mp3"
        dst = tmp_path / "b.mp3"
        src.write_bytes(b"audio")

        def _raise_exdev(s, d):
            raise OSError(18, "Invalid cross-device link")

        monkeypatch.setattr(os, "replace", _raise_exdev)
        _move_file(src, dst)
        assert dst.read_bytes() == b"audio"
        assert not src.exists()
