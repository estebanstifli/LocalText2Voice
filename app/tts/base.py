from __future__ import annotations

from abc import ABC, abstractmethod
from contextvars import ContextVar
from functools import wraps
import logging
from pathlib import Path
from typing import Any, Callable

from app.utils.atomic_audio import EmptyWavError, atomic_wav_output


_active_synthesis: ContextVar[tuple[int, ...]] = ContextVar("active_synthesis", default=())


class TTSEngineError(RuntimeError):
    pass


class TTSCancelled(TTSEngineError):
    pass


class BaseTTSEngine(ABC):
    def __init_subclass__(cls, **kwargs) -> None:
        """Apply the same output contract to every engine and direct preview call.

        Keep the public engine API unchanged. Only concrete implementations
        declared by a subclass are wrapped; inherited implementations stay wrapped.
        """
        super().__init_subclass__(**kwargs)
        synthesize = cls.__dict__.get("synthesize_to_wav")
        if synthesize is None or getattr(synthesize, "__isabstractmethod__", False):
            return

        @wraps(synthesize)
        def checked(self, text, output_wav, voice_config):
            # An override may delegate to super(); the outer call owns retries.
            if id(self) in _active_synthesis.get():
                return synthesize(self, text, output_wav, voice_config)

            def check_cancelled():
                if self.cancellation_requested():
                    raise TTSCancelled("Generation cancelled.")

            token = _active_synthesis.set((*_active_synthesis.get(), id(self)))
            try:
                for attempt in range(3):
                    check_cancelled()
                    try:
                        with atomic_wav_output(Path(output_wav), check_cancelled) as candidate:
                            synthesize(self, text, candidate, voice_config)
                        return Path(output_wav)
                    except EmptyWavError as exc:
                        check_cancelled()
                        if attempt == 2:
                            raise TTSEngineError(
                                f"{type(self).__name__} returned empty WAV audio after 3 attempts "
                                "(2 retries). The segment was not rendered."
                            ) from exc
                        message = f"{type(self).__name__}: empty WAV; retry {attempt + 1}/2."
                        logging.getLogger(__name__).warning(message)
                        callback = getattr(self, "_log_callback", None) or getattr(self, "log_callback", None)
                        if callable(callback):
                            callback(message)
            finally:
                _active_synthesis.reset(token)

        cls.synthesize_to_wav = checked

    def cancellation_requested(self) -> bool:
        """Return whether this instance has received a sticky cancellation."""
        event = getattr(self, "_cancel_requested", None)
        is_set = getattr(event, "is_set", None)
        return bool(is_set()) if callable(is_set) else False

    def set_log_callback(self, callback: Callable[[str], None]) -> None:
        """Allow engines to report optional runtime diagnostics."""
        self._log_callback = callback

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
