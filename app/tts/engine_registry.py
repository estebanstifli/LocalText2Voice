from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .api_engines import (
    AzureTTSEngine,
    ElevenLabsTTSEngine,
    GeminiTTSEngine,
    OpenAITTSEngine,
)
from .base import BaseTTSEngine, TTSEngineError
from .chatterbox_engine import ChatterboxTTSEngine
from .chatterbox_manager import ChatterboxManager
from .kokoro_python_engine import KokoroPythonTTSEngine
from .kokoro_python_manager import KokoroPythonManager
from .piper_engine import PiperTTSEngine
from .qwen_engine import QwenTTSEngine
from .qwen_manager import QwenManager


@dataclass(frozen=True)
class TTSEngineDefinition:
    engine_id: str
    display_name: str
    is_local: bool


TTS_ENGINES: tuple[TTSEngineDefinition, ...] = (
    TTSEngineDefinition("piper", "Piper", True),
    TTSEngineDefinition("kokoro", "Kokoro", True),
    TTSEngineDefinition("chatterbox", "Chatterbox", True),
    TTSEngineDefinition("qwen", "Qwen3 TTS", True),
    TTSEngineDefinition("openai", "OpenAI TTS (API)", False),
    TTSEngineDefinition("elevenlabs", "ElevenLabs (API)", False),
    TTSEngineDefinition("gemini", "Google Gemini TTS (API)", False),
    TTSEngineDefinition("azure", "Azure Speech (API)", False),
)


def engine_ids() -> set[str]:
    return {engine.engine_id for engine in TTS_ENGINES}


def create_tts_engine(engine_id: str, piper_path: Path) -> BaseTTSEngine:
    if engine_id == "piper":
        return PiperTTSEngine(piper_path)
    if engine_id in {"kokoro", "kokoro_python"}:
        return KokoroPythonTTSEngine(KokoroPythonManager())
    if engine_id == "chatterbox":
        return ChatterboxTTSEngine(ChatterboxManager())
    if engine_id == "qwen":
        return QwenTTSEngine(QwenManager())
    if engine_id == "openai":
        return OpenAITTSEngine()
    if engine_id == "elevenlabs":
        return ElevenLabsTTSEngine()
    if engine_id == "gemini":
        return GeminiTTSEngine()
    if engine_id == "azure":
        return AzureTTSEngine()
    raise TTSEngineError(f"Unknown TTS engine: {engine_id}")
