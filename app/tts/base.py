from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class TTSEngineError(RuntimeError):
    pass


class TTSCancelled(TTSEngineError):
    pass


class BaseTTSEngine(ABC):
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
