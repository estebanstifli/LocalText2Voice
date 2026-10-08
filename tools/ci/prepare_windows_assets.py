"""Prepare verified third-party build inputs on a clean GitHub Windows runner.

Never copy an existing LocalText2Voice release into a trusted signing build.
No optional AI models, user settings or personal runtime caches are included.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def download(url: str, expected: str, target: Path) -> None:
    if not url.startswith("https://") or len(expected) != 64:
        raise ValueError("A HTTPS URL and SHA-256 are required")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LocalText2Voice-CI"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as out:
            shutil.copyfileobj(response, out)
        with temporary.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"SHA-256 mismatch for {target.name}: {actual}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def extract_checked(archive: Path, target: Path) -> None:
    target = target.resolve()
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            name = item.filename.replace("\\", "/")
            if ":" in name or PurePosixPath(name).is_absolute():
                raise ValueError("Unsafe archive entry")
            path = (target / name).resolve()
            if not path.is_relative_to(target):
                raise ValueError("Archive entry escapes target")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive symlinks are not allowed")
        source.extractall(target)


def main() -> None:
    manifest = json.loads((ROOT / "tools/ci/windows-assets.json").read_text(encoding="utf-8"))
    cache = ROOT / "build/ci-assets"
    for name, asset in manifest.items():
        suffix = ".exe" if name == "inno" else ".zip"
        target = cache / (name + suffix)
        download(asset["url"], asset["sha256"], target)
        if name != "inno":
            extract_checked(target, cache / name)
        print(f"Verified {name}: {asset['sha256']}")
    piper = list((cache / "piper").rglob("piper.exe"))
    if len(piper) != 1:
        raise ValueError("Expected one Piper runtime")
    shutil.copytree(piper[0].parent, ROOT / "engines/piper", dirs_exist_ok=True)
    ffmpeg = list((cache / "ffmpeg").rglob("ffmpeg.exe"))
    if len(ffmpeg) != 1:
        raise ValueError("Expected one FFmpeg runtime")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        shutil.copy2(ffmpeg[0].parent / name, ROOT / "ffmpeg" / name)
    # Keep the upstream license and accompanying documentation with the runtime.
    shutil.copytree(ffmpeg[0].parent.parent, ROOT / "ffmpeg/upstream", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("bin"))


if __name__ == "__main__":
    main()
