from __future__ import annotations

import base64
import html
import http.client
import json
import os
import threading
import wave
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from .base import BaseTTSEngine, TTSCancelled, TTSEngineError


class HttpTTSEngine(BaseTTSEngine):
    host: str
    timeout_seconds = 120

    def __init__(self) -> None:
        self._cancel_requested = threading.Event()
        self._connection: http.client.HTTPSConnection | None = None
        self._lock = threading.Lock()

    def cancel_current(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            connection = self._connection
        if connection is not None:
            connection.close()

    def _post(
        self,
        path: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> tuple[bytes, str]:
        if self._cancel_requested.is_set():
            raise TTSCancelled("Generation cancelled.")

        connection = http.client.HTTPSConnection(
            self.host,
            timeout=max(10, timeout_seconds),
        )
        with self._lock:
            self._connection = connection
        try:
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "")
            response_body = response.read()
        except OSError as exc:
            if self._cancel_requested.is_set():
                raise TTSCancelled("Generation cancelled.") from exc
            raise TTSEngineError(f"TTS API request failed: {exc}") from exc
        finally:
            with self._lock:
                if self._connection is connection:
                    self._connection = None
            connection.close()

        if self._cancel_requested.is_set():
            raise TTSCancelled("Generation cancelled.")

        if response.status >= 400:
            raise TTSEngineError(
                f"TTS API returned HTTP {response.status}: "
                f"{self._extract_error_message(response_body)}"
            )
        if not response_body:
            raise TTSEngineError("TTS API returned an empty audio response.")
        return response_body, content_type

    @staticmethod
    def _json_body(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload).encode("utf-8")

    @staticmethod
    def _extract_error_message(body: bytes) -> str:
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            return "No error details were returned."
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text[:1000]
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            detail = data.get("detail")
            if isinstance(detail, dict) and detail.get("message"):
                return str(detail["message"])
            if isinstance(detail, str):
                return detail
            message = data.get("message")
            if message:
                return str(message)
        return text[:1000]

    @staticmethod
    def _write_wav_bytes(audio_bytes: bytes, output_wav: Path) -> Path:
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        output_wav.write_bytes(audio_bytes)
        try:
            with wave.open(str(output_wav), "rb"):
                pass
        except wave.Error as exc:
            raise TTSEngineError(
                "TTS API returned audio, but it was not a valid WAV file."
            ) from exc
        return output_wav

    @staticmethod
    def _write_pcm_wav(
        pcm_bytes: bytes,
        output_wav: Path,
        sample_rate: int,
    ) -> Path:
        if len(pcm_bytes) < 2:
            raise TTSEngineError("TTS API returned an empty PCM audio response.")
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            audio.writeframes(pcm_bytes)
        return output_wav

    @staticmethod
    def _timeout(voice_config: dict[str, Any]) -> int:
        try:
            return int(voice_config.get("timeout_seconds", 120))
        except (TypeError, ValueError):
            return 120


class OpenAITTSEngine(HttpTTSEngine):
    host = "api.openai.com"

    def validate(self, voice_config: dict[str, Any]) -> None:
        api_key = self._api_key(voice_config)
        if not api_key:
            raise TTSEngineError(
                "OpenAI TTS is selected, but no API key is configured."
            )
        if not str(voice_config.get("model", "")).strip():
            raise TTSEngineError("OpenAI TTS requires a model.")
        if not str(voice_config.get("voice", "")).strip():
            raise TTSEngineError("OpenAI TTS requires a voice.")

    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        self.validate(voice_config)
        payload: dict[str, Any] = {
            "model": str(voice_config.get("model", "gpt-4o-mini-tts")),
            "voice": str(voice_config.get("voice", "marin")),
            "input": text,
            "response_format": "wav",
            "speed": float(voice_config.get("speed", 1.0)),
        }
        instructions = str(voice_config.get("instructions", "")).strip()
        if instructions:
            payload["instructions"] = instructions

        response, _ = self._post(
            "/v1/audio/speech",
            self._json_body(payload),
            {
                "Authorization": f"Bearer {self._api_key(voice_config)}",
                "Content-Type": "application/json",
            },
            self._timeout(voice_config),
        )
        return self._write_wav_bytes(response, output_wav)

    @staticmethod
    def _api_key(voice_config: dict[str, Any]) -> str:
        return str(
            voice_config.get("api_key")
            or os.environ.get("OPENAI_API_KEY", "")
        ).strip()


class ElevenLabsTTSEngine(HttpTTSEngine):
    host = "api.elevenlabs.io"

    def validate(self, voice_config: dict[str, Any]) -> None:
        if not self._api_key(voice_config):
            raise TTSEngineError(
                "ElevenLabs TTS is selected, but no API key is configured."
            )
        if not str(voice_config.get("voice_id", "")).strip():
            raise TTSEngineError("ElevenLabs TTS requires a voice ID.")
        output_format = str(voice_config.get("output_format", "pcm_24000"))
        if not (
            output_format.startswith("pcm_")
            or output_format.startswith("wav_")
        ):
            raise TTSEngineError(
                "ElevenLabs output must be PCM or WAV so LocalText2Voice can "
                "build the narration timeline."
            )

    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        self.validate(voice_config)
        output_format = str(voice_config.get("output_format", "pcm_24000"))
        voice_id = quote(str(voice_config.get("voice_id", "")).strip(), safe="")
        query = urlencode({"output_format": output_format})
        path = f"/v1/text-to-speech/{voice_id}?{query}"
        payload = {
            "text": text,
            "model_id": str(
                voice_config.get("model_id", "eleven_flash_v2_5")
            ),
            "voice_settings": {
                "stability": float(voice_config.get("stability", 0.5)),
                "similarity_boost": float(
                    voice_config.get("similarity_boost", 0.75)
                ),
                "style": float(voice_config.get("style", 0.0)),
                "use_speaker_boost": bool(
                    voice_config.get("use_speaker_boost", True)
                ),
            },
        }
        response, _ = self._post(
            path,
            self._json_body(payload),
            {
                "xi-api-key": self._api_key(voice_config),
                "Content-Type": "application/json",
            },
            self._timeout(voice_config),
        )
        if output_format.startswith("wav_"):
            return self._write_wav_bytes(response, output_wav)
        return self._write_pcm_wav(
            response,
            output_wav,
            self._sample_rate_from_output_format(output_format),
        )

    @staticmethod
    def _api_key(voice_config: dict[str, Any]) -> str:
        return str(
            voice_config.get("api_key")
            or os.environ.get("ELEVENLABS_API_KEY", "")
        ).strip()

    @staticmethod
    def _sample_rate_from_output_format(output_format: str) -> int:
        try:
            return int(output_format.split("_", 1)[1])
        except (IndexError, ValueError):
            return 24000


class AzureTTSEngine(HttpTTSEngine):
    def validate(self, voice_config: dict[str, Any]) -> None:
        if not self._api_key(voice_config):
            raise TTSEngineError(
                "Azure Speech TTS is selected, but no API key is configured."
            )
        region = str(voice_config.get("region", "")).strip()
        if not region:
            raise TTSEngineError("Azure Speech TTS requires a region.")
        if not str(voice_config.get("voice", "")).strip():
            raise TTSEngineError("Azure Speech TTS requires a voice name.")
        output_format = str(
            voice_config.get("output_format", "riff-24khz-16bit-mono-pcm")
        )
        if not output_format.startswith("riff-"):
            raise TTSEngineError(
                "Azure Speech output must use a RIFF WAV format."
            )

    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        self.validate(voice_config)
        self.host = (
            f"{str(voice_config.get('region', '')).strip()}"
            ".tts.speech.microsoft.com"
        )
        output_format = str(
            voice_config.get("output_format", "riff-24khz-16bit-mono-pcm")
        )
        response, _ = self._post(
            "/cognitiveservices/v1",
            self._ssml(text, voice_config).encode("utf-8"),
            {
                "Ocp-Apim-Subscription-Key": self._api_key(voice_config),
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": output_format,
                "User-Agent": "LocalText2Voice",
            },
            self._timeout(voice_config),
        )
        return self._write_wav_bytes(response, output_wav)

    @staticmethod
    def _api_key(voice_config: dict[str, Any]) -> str:
        return str(
            voice_config.get("api_key")
            or os.environ.get("AZURE_SPEECH_KEY", "")
        ).strip()

    @classmethod
    def _ssml(cls, text: str, voice_config: dict[str, Any]) -> str:
        voice = str(voice_config.get("voice", "en-US-JennyNeural")).strip()
        locale = cls._locale_from_voice(voice)
        rate = cls._speed_to_rate(float(voice_config.get("speed", 1.0)))
        escaped_text = html.escape(text, quote=False)
        style = str(voice_config.get("style", "")).strip()
        content = f'<prosody rate="{rate}">{escaped_text}</prosody>'
        if style:
            content = f'<mstts:express-as style="{html.escape(style)}">{content}</mstts:express-as>'
        return (
            '<speak version="1.0" '
            'xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" '
            f'xml:lang="{locale}">'
            f'<voice name="{html.escape(voice)}" xml:lang="{locale}">'
            f"{content}</voice></speak>"
        )

    @staticmethod
    def _locale_from_voice(voice: str) -> str:
        parts = voice.split("-")
        if len(parts) >= 2:
            return f"{parts[0]}-{parts[1]}"
        return "en-US"

    @staticmethod
    def _speed_to_rate(speed: float) -> str:
        percentage = round((max(0.5, min(2.0, speed)) - 1.0) * 100)
        return f"{percentage:+d}%"


class GeminiTTSEngine(HttpTTSEngine):
    host = "generativelanguage.googleapis.com"

    DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
    DEFAULT_VOICE = "Kore"

    def validate(self, voice_config: dict[str, Any]) -> None:
        if not self._api_key(voice_config):
            raise TTSEngineError(
                "Google Gemini TTS is selected, but no API key is configured."
            )
        if not str(voice_config.get("model", "")).strip():
            raise TTSEngineError("Google Gemini TTS requires a model.")
        if not str(voice_config.get("voice", "")).strip():
            raise TTSEngineError("Google Gemini TTS requires a voice.")

    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        self.validate(voice_config)
        payload = {
            "model": str(voice_config.get("model", self.DEFAULT_MODEL)),
            "input": self._input_text(text, voice_config),
            "response_format": {"type": "audio"},
            "generation_config": {
                "speech_config": [
                    {"voice": str(voice_config.get("voice", self.DEFAULT_VOICE))}
                ]
            },
        }
        response, _ = self._post(
            "/v1beta/interactions",
            self._json_body(payload),
            {
                "x-goog-api-key": self._api_key(voice_config),
                "Content-Type": "application/json",
                "Api-Revision": "2026-05-20",
            },
            self._timeout(voice_config),
        )
        audio_bytes, mime_type = self._extract_audio(response)
        if audio_bytes.startswith(b"RIFF"):
            return self._write_wav_bytes(audio_bytes, output_wav)
        return self._write_pcm_wav(
            audio_bytes,
            output_wav,
            self._sample_rate_from_mime_type(mime_type),
        )

    @staticmethod
    def _api_key(voice_config: dict[str, Any]) -> str:
        return str(
            voice_config.get("api_key")
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        ).strip()

    @staticmethod
    def _input_text(text: str, voice_config: dict[str, Any]) -> str:
        prompt = str(
            voice_config.get("prompt")
            or voice_config.get("instructions")
            or ""
        ).strip()
        if not prompt:
            return text
        return f"{prompt}\n\nTranscript:\n{text}"

    @classmethod
    def _extract_audio(cls, response_body: bytes) -> tuple[bytes, str]:
        try:
            data = json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TTSEngineError(
                "Google Gemini TTS returned a response that was not valid JSON."
            ) from exc
        audio_blocks = cls._audio_blocks(data)
        for block in reversed(audio_blocks):
            encoded = block.get("data") or block.get("audio")
            if not encoded:
                continue
            try:
                return (
                    base64.b64decode(str(encoded)),
                    str(
                        block.get("mime_type")
                        or block.get("mimeType")
                        or block.get("mime")
                        or ""
                    ),
                )
            except (TypeError, ValueError) as exc:
                raise TTSEngineError(
                    "Google Gemini TTS returned audio data that could not be decoded."
                ) from exc
        raise TTSEngineError("Google Gemini TTS did not return audio data.")

    @classmethod
    def _audio_blocks(cls, data: Any) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if not isinstance(data, dict):
            return blocks

        output_audio = data.get("output_audio")
        if isinstance(output_audio, dict):
            blocks.append(output_audio)

        steps = data.get("steps", [])
        if not isinstance(steps, list):
            steps = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            content = step.get("content", [])
            if not isinstance(content, list):
                content = []
            for item in content:
                if isinstance(item, dict) and str(item.get("type", "")) == "audio":
                    blocks.append(item)

        outputs = data.get("outputs", [])
        if not isinstance(outputs, list):
            outputs = []
        for item in outputs:
            if isinstance(item, dict) and str(item.get("type", "")) == "audio":
                blocks.append(item)
        return blocks

    @staticmethod
    def _sample_rate_from_mime_type(mime_type: str) -> int:
        lowered = mime_type.lower()
        for marker in ("rate=", "rate:"):
            if marker not in lowered:
                continue
            value = lowered.split(marker, 1)[1].split(";", 1)[0].strip()
            try:
                return int(value)
            except ValueError:
                break
        return 24000
