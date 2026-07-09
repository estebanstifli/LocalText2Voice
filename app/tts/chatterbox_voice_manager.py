from __future__ import annotations

import shutil
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.utils.paths import app_data_root


class ChatterboxVoiceError(RuntimeError):
    pass


class ChatterboxVoiceCancelled(ChatterboxVoiceError):
    pass


ChatterboxVoiceProgress = Callable[[int, int, str], None]

CHATTERBOX_REFERENCE_RAW_BASE = (
    "https://raw.githubusercontent.com/devnen/Chatterbox-TTS-Server/main/voices"
)


@dataclass(frozen=True)
class ChatterboxReferenceVoice:
    voice_id: str
    display_name: str
    file_name: str
    source_url: str = ""
    source_name: str = "Local"
    size: int = 0

    @property
    def is_remote(self) -> bool:
        return bool(self.source_url)


class ChatterboxReferenceVoiceManager:
    """Manage short reference clips used by Chatterbox voice cloning."""

    REPOSITORY_URL = "https://github.com/devnen/Chatterbox-TTS-Server"
    RAW_BASE = CHATTERBOX_REFERENCE_RAW_BASE
    SAMPLE_LICENSE_URL = (
        "https://github.com/devnen/Chatterbox-TTS-Server/blob/main/LICENSE"
    )
    REMOTE_VOICE_FILES: tuple[tuple[str, int], ...] = (
        ("Abigail.wav", 685_976),
        ("Adrian.wav", 771_414),
        ("Alexander.wav", 694_316),
        ("Alice.wav", 772_916),
        ("Austin.wav", 721_672),
        ("Axel.wav", 805_932),
        ("Connor.wav", 749_118),
        ("Cora.wav", 766_728),
        ("Elena.wav", 714_272),
        ("Eli.wav", 757_804),
        ("Emily.wav", 837_180),
        ("Everett.wav", 663_886),
        ("Gabriel.wav", 735_276),
        ("Gianna.wav", 858_156),
        ("Henry.wav", 677_932),
        ("Ian.wav", 734_370),
        ("Jade.wav", 712_748),
        ("Jeremiah.wav", 725_036),
        ("Jordan.wav", 685_128),
        ("Julian.wav", 639_894),
        ("Layla.wav", 740_380),
        ("Leonardo.wav", 659_500),
        ("Michael.wav", 674_734),
        ("Miles.wav", 783_052),
        ("Olivia.wav", 810_028),
        ("Ryan.wav", 656_428),
        ("Taylor.wav", 700_350),
        ("Thomas.wav", 726_910),
    )
    DEFAULT_REMOTE_VOICES: tuple[ChatterboxReferenceVoice, ...] = tuple(
        ChatterboxReferenceVoice(
            f"chatterbox_{Path(file_name).stem.lower()}",
            Path(file_name).stem,
            file_name,
            f"{CHATTERBOX_REFERENCE_RAW_BASE}/{file_name}",
            "Chatterbox TTS Server",
            size,
        )
        for file_name, size in REMOTE_VOICE_FILES
    )

    def __init__(self, voices_dir: Path | None = None, timeout_seconds: int = 30) -> None:
        self.voices_dir = voices_dir or app_data_root() / "models" / "chatterbox" / "voices"
        self.timeout_seconds = timeout_seconds
        self._cancel_requested = threading.Event()

    def cancel(self) -> None:
        self._cancel_requested.set()

    def list_remote_voices(self) -> list[ChatterboxReferenceVoice]:
        return list(self.DEFAULT_REMOTE_VOICES)

    def list_installed_voices(self) -> list[ChatterboxReferenceVoice]:
        if not self.voices_dir.is_dir():
            return []
        known = {voice.file_name.lower(): voice for voice in self.DEFAULT_REMOTE_VOICES}
        voices: list[ChatterboxReferenceVoice] = []
        for path in sorted(self.voices_dir.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file() or path.suffix.lower() not in {".wav", ".mp3"}:
                continue
            known_voice = known.get(path.name.lower())
            if known_voice is not None:
                voices.append(known_voice)
            else:
                voices.append(
                    ChatterboxReferenceVoice(
                        f"local_{path.stem.casefold()}",
                        path.stem,
                        path.name,
                        "",
                        "Imported",
                        path.stat().st_size,
                    )
                )
        return voices

    def path_for(self, voice: ChatterboxReferenceVoice) -> Path:
        return self.voices_dir / Path(voice.file_name).name

    def is_installed(self, voice: ChatterboxReferenceVoice) -> bool:
        path = self.path_for(voice)
        return path.is_file() and (voice.size <= 0 or path.stat().st_size == voice.size)

    def install(
        self,
        voice: ChatterboxReferenceVoice,
        progress_callback: ChatterboxVoiceProgress | None = None,
    ) -> Path:
        if not voice.source_url:
            raise ChatterboxVoiceError("This Chatterbox reference voice has no download URL.")
        progress = progress_callback or (lambda current, total, message: None)
        self._cancel_requested.clear()
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(voice)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        downloaded = 0
        expected = max(0, int(voice.size))
        progress(0, expected or 1, f"Downloading Chatterbox voice: {voice.display_name}")
        request = urllib.request.Request(
            voice.source_url,
            headers={"User-Agent": "LocalText2Voice/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                with temporary.open("wb") as output:
                    while True:
                        self._check_cancelled()
                        chunk = response.read(1024 * 128)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        progress(downloaded, expected or downloaded or 1, f"Downloading {voice.display_name}...")
        except ChatterboxVoiceCancelled:
            self._remove_path(temporary)
            raise
        except (OSError, urllib.error.URLError) as exc:
            self._remove_path(temporary)
            raise ChatterboxVoiceError(f"Could not download Chatterbox voice: {exc}") from exc
        if expected and downloaded != expected:
            self._remove_path(temporary)
            raise ChatterboxVoiceError(
                f"Downloaded Chatterbox voice has the wrong size: {downloaded} bytes, expected {expected}."
            )
        temporary.replace(destination)
        progress(expected or downloaded, expected or downloaded or 1, f"Installed {voice.display_name}.")
        return destination

    def install_default_pack(
        self,
        progress_callback: ChatterboxVoiceProgress | None = None,
    ) -> list[Path]:
        installed: list[Path] = []
        voices = self.list_remote_voices()
        for index, voice in enumerate(voices, start=1):
            self._check_cancelled()
            if self.is_installed(voice):
                installed.append(self.path_for(voice))
                continue
            def progress(current: int, total: int, message: str, index: int = index) -> None:
                callback = progress_callback or (lambda current, total, message: None)
                base = index - 1
                percent_current = base * 100 + (current / total * 100 if total else 0)
                callback(int(percent_current), len(voices) * 100, message)

            installed.append(self.install(voice, progress))
        return installed

    def import_voice(self, source: Path) -> Path:
        if not source.is_file():
            raise ChatterboxVoiceError(f"Reference audio file not found: {source}")
        if source.suffix.lower() not in {".wav", ".mp3"}:
            raise ChatterboxVoiceError("Chatterbox reference audio must be WAV or MP3.")
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        destination = self._unique_destination(source.name)
        shutil.copy2(source, destination)
        return destination

    def remove(self, voice: ChatterboxReferenceVoice) -> None:
        path = self.path_for(voice)
        if path.is_file():
            path.unlink()

    def _unique_destination(self, file_name: str) -> Path:
        destination = self.voices_dir / Path(file_name).name
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        counter = 2
        while True:
            candidate = destination.with_name(f"{stem}_{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise ChatterboxVoiceCancelled("Chatterbox voice download cancelled.")

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.exists():
            path.unlink()
