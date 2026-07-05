from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .api_engines import AzureTTSEngine, ElevenLabsTTSEngine, OpenAITTSEngine
from .base import BaseTTSEngine, TTSEngineError
from .chatterbox_engine import ChatterboxTTSEngine
from .chatterbox_manager import ChatterboxManager
from .kokoro_engine import KokoroTTSEngine
from .kokoro_manager import KokoroManager
from .piper_engine import PiperTTSEngine


@dataclass(frozen=True)
class TTSEngineDefinition:
    engine_id: str
    display_name: str
    is_local: bool


TTS_ENGINES: tuple[TTSEngineDefinition, ...] = (
    TTSEngineDefinition("piper", "Piper Local (offline, free)", True),
    TTSEngineDefinition("kokoro", "Kokoro - Better local quality", True),
    TTSEngineDefinition("chatterbox", "Chatterbox - Advanced local GPU", True),
    TTSEngineDefinition("openai", "OpenAI TTS (API)", False),
    TTSEngineDefinition("elevenlabs", "ElevenLabs (API)", False),
    TTSEngineDefinition("azure", "Azure Speech (API)", False),
)


def engine_ids() -> set[str]:
    return {engine.engine_id for engine in TTS_ENGINES}


def create_tts_engine(engine_id: str, piper_path: Path) -> BaseTTSEngine:
    if engine_id == "piper":
        return PiperTTSEngine(piper_path)
    if engine_id == "kokoro":
        return KokoroTTSEngine(KokoroManager())
    if engine_id == "chatterbox":
        return ChatterboxTTSEngine(ChatterboxManager())
    if engine_id == "openai":
        return OpenAITTSEngine()
    if engine_id == "elevenlabs":
        return ElevenLabsTTSEngine()
    if engine_id == "azure":
        return AzureTTSEngine()
    raise TTSEngineError(f"Unknown TTS engine: {engine_id}")
