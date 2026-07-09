from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable


class TTSEngineError(RuntimeError):
    pass


class TTSCancelled(TTSEngineError):
    pass


class BaseTTSEngine(ABC):
    def set_log_callback(self, callback: Callable[[str], None]) -> None:
        """Allow engines to report optional runtime diagnostics."""

    def close(self) -> None:
        """Release any persistent runtime resources held by the engine."""

    def preload(self, voice_config: dict[str, Any]) -> None:
        """Optionally load a persistent runtime and wait until it is ready."""
        self.validate(voice_config)

    @abstractmethod
    def validate(self, voice_config: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        raise NotImplementedError

    @abstractmethod
    def cancel_current(self) -> None:
        raise NotImplementedError
