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
from urllib.parse import quote as url_quote
from urllib.parse import urljoin
from urllib.parse import urlparse
from urllib.parse import quote, urlencode

from app.utils.ffmpeg_utils import FFmpegError, FFmpegRunner, find_ffmpeg

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


class CustomHTTPTTSEngine(BaseTTSEngine):
    """Generic HTTP bridge for user-defined local or remote TTS services."""

    def __init__(self) -> None:
        self._cancel_requested = threading.Event()
        self._connection: http.client.HTTPConnection | http.client.HTTPSConnection | None = None
        self._lock = threading.Lock()

    def cancel_current(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            connection = self._connection
        if connection is not None:
            connection.close()

    def validate(self, voice_config: dict[str, Any]) -> None:
        name = str(voice_config.get("name", "")).strip()
        url = str(voice_config.get("url", "")).strip()
        if not name:
            raise TTSEngineError("Custom TTS engine requires a name.")
        if not url:
            raise TTSEngineError("Custom TTS engine requires an endpoint URL.")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise TTSEngineError(
                "Custom TTS endpoint must be a full http:// or https:// URL."
            )
        response_mode = str(voice_config.get("response_mode", "audio_wav"))
        if response_mode not in {
            "audio_wav",
            "audio_pcm",
            "json_base64",
            "json_url",
            "json_path",
        }:
            raise TTSEngineError(f"Unsupported custom TTS response mode: {response_mode}")
        if response_mode.startswith("json_") and not str(
            voice_config.get("json_audio_path", "")
        ).strip():
            raise TTSEngineError(
                "Custom TTS JSON response mode requires a JSON field path."
            )

    def synthesize_to_wav(
        self,
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        self.validate(voice_config)
        values = self._template_values(text, output_wav, voice_config)
        url = self._render_template(str(voice_config.get("url", "")), values, for_url=True)
        method = str(voice_config.get("method", "POST")).upper()
        headers = self._headers(voice_config, values)
        body = b""
        if method not in {"GET", "DELETE"}:
            body_template = str(
                voice_config.get("body_template")
                or self._default_body_template()
            )
            is_form_body = any(
                key.lower() == "content-type"
                and "application/x-www-form-urlencoded" in value.lower()
                for key, value in headers.items()
            )
            body = self._render_template(
                body_template,
                values,
                for_url=is_form_body,
            ).encode("utf-8")
            if not any(key.lower() == "content-type" for key in headers):
                headers["Content-Type"] = "application/json"

        timeout_seconds = self._timeout(voice_config)
        response, content_type = self._request(
            method,
            url,
            body,
            headers,
            timeout_seconds,
        )
        return self._write_custom_response(
            response,
            content_type,
            output_wav,
            voice_config,
            timeout_seconds,
        )

    def _request(
        self,
        method: str,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> tuple[bytes, str]:
        if self._cancel_requested.is_set():
            raise TTSCancelled("Generation cancelled.")

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise TTSEngineError(
                "Custom TTS endpoint must be a full http:// or https:// URL."
            )
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        connection_class = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(parsed.netloc, timeout=max(10, timeout_seconds))
        with self._lock:
            self._connection = connection
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "")
            response_body = response.read()
        except OSError as exc:
            if self._cancel_requested.is_set():
                raise TTSCancelled("Generation cancelled.") from exc
            raise TTSEngineError(f"Custom TTS request failed: {exc}") from exc
        finally:
            with self._lock:
                if self._connection is connection:
                    self._connection = None
            connection.close()

        if self._cancel_requested.is_set():
            raise TTSCancelled("Generation cancelled.")
        if response.status >= 400:
            raise TTSEngineError(
                f"Custom TTS returned HTTP {response.status}: "
                f"{HttpTTSEngine._extract_error_message(response_body)}"
            )
        if not response_body:
            raise TTSEngineError("Custom TTS returned an empty response.")
        return response_body, content_type

    def _write_custom_response(
        self,
        response: bytes,
        content_type: str,
        output_wav: Path,
        voice_config: dict[str, Any],
        timeout_seconds: int,
    ) -> Path:
        response_mode = str(voice_config.get("response_mode", "audio_wav"))
        lowered_type = content_type.lower()
        if response_mode == "audio_wav" or (
            response_mode == "auto" and "wav" in lowered_type
        ):
            return self._write_wav_bytes_compatible(response, output_wav, voice_config)
        if response_mode == "audio_pcm":
            return HttpTTSEngine._write_pcm_wav(
                response,
                output_wav,
                self._sample_rate(voice_config),
            )

        data = self._json_response(response)
        json_path = str(voice_config.get("json_audio_path", "")).strip()
        value = self._json_value(data, json_path)
        if response_mode == "json_base64":
            try:
                audio = base64.b64decode(str(value))
            except (TypeError, ValueError) as exc:
                raise TTSEngineError(
                    "Custom TTS JSON audio field is not valid base64."
                ) from exc
            if audio.startswith(b"RIFF"):
                return self._write_wav_bytes_compatible(audio, output_wav, voice_config)
            return HttpTTSEngine._write_pcm_wav(
                audio,
                output_wav,
                self._sample_rate(voice_config),
            )
        if response_mode == "json_url":
            audio_url = str(value).strip()
            if not audio_url:
                raise TTSEngineError("Custom TTS JSON URL field is empty.")
            audio_url = urljoin(str(voice_config.get("url", "")), audio_url)
            audio, audio_type = self._request("GET", audio_url, b"", {}, timeout_seconds)
            if "wav" in audio_type.lower() or audio.startswith(b"RIFF"):
                return self._write_wav_bytes_compatible(audio, output_wav, voice_config)
            return HttpTTSEngine._write_pcm_wav(
                audio,
                output_wav,
                self._sample_rate(voice_config),
            )
        if response_mode == "json_path":
            audio_path = Path(str(value).strip()).expanduser()
            if not audio_path.exists():
                raise TTSEngineError(
                    f"Custom TTS JSON file path does not exist: {audio_path}"
            )
            audio = audio_path.read_bytes()
            if audio.startswith(b"RIFF"):
                return self._write_wav_bytes_compatible(audio, output_wav, voice_config)
            return HttpTTSEngine._write_pcm_wav(
                audio,
                output_wav,
                self._sample_rate(voice_config),
            )
        raise TTSEngineError(f"Unsupported custom TTS response mode: {response_mode}")

    @classmethod
    def _write_wav_bytes_compatible(
        cls,
        audio_bytes: bytes,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        output_wav.write_bytes(audio_bytes)
        try:
            with wave.open(str(output_wav), "rb"):
                pass
            return output_wav
        except wave.Error:
            return cls._convert_wav_to_pcm(output_wav, voice_config)

    @staticmethod
    def _convert_wav_to_pcm(
        wav_path: Path,
        voice_config: dict[str, Any],
    ) -> Path:
        temporary = wav_path.with_name(f"{wav_path.stem}_pcm_tmp.wav")
        try:
            runner = FFmpegRunner(
                find_ffmpeg(voice_config.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"))
            )
            runner.run(
                [
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(wav_path),
                    "-ac",
                    "1",
                    "-codec:a",
                    "pcm_s16le",
                    str(temporary),
                ]
            )
            temporary.replace(wav_path)
            with wave.open(str(wav_path), "rb"):
                pass
            return wav_path
        except (FFmpegError, OSError, wave.Error) as exc:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
            raise TTSEngineError(
                "Custom TTS returned WAV audio, but it was not PCM-compatible. "
                "FFmpeg conversion failed."
            ) from exc

    @staticmethod
    def _json_response(response: bytes) -> Any:
        try:
            return json.loads(response.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TTSEngineError("Custom TTS returned invalid JSON.") from exc

    @staticmethod
    def _json_value(data: Any, path: str) -> Any:
        current = data
        for part in path.split("."):
            part = part.strip()
            if not part:
                continue
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue
            if isinstance(current, list):
                try:
                    current = current[int(part)]
                    continue
                except (ValueError, IndexError):
                    pass
            raise TTSEngineError(f"Custom TTS JSON field not found: {path}")
        return current

    @classmethod
    def _headers(
        cls,
        voice_config: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        raw = str(voice_config.get("headers_json", "") or "").strip()
        if raw:
            try:
                parsed = json.loads(cls._render_template(raw, values))
            except json.JSONDecodeError as exc:
                raise TTSEngineError("Custom TTS headers must be valid JSON.") from exc
            if not isinstance(parsed, dict):
                raise TTSEngineError("Custom TTS headers JSON must be an object.")
            headers.update({str(key): str(value) for key, value in parsed.items()})

        api_key = str(voice_config.get("api_key", "") or "").strip()
        auth_header = str(voice_config.get("auth_header", "") or "").strip()
        if api_key and auth_header:
            name, separator, value = auth_header.partition(":")
            if not separator:
                raise TTSEngineError(
                    "Custom TTS auth header must use the format 'Header: value'."
                )
            headers[name.strip()] = cls._render_template(
                value.strip(),
                values,
            )
        return headers

    @staticmethod
    def _template_values(
        text: str,
        output_wav: Path,
        voice_config: dict[str, Any],
    ) -> dict[str, Any]:
        language = voice_config.get("language") or voice_config.get("lang") or ""
        values = {
            "text": text,
            "voice": str(voice_config.get("voice", "") or ""),
            "language": str(language),
            "lang": str(language),
            "speed": voice_config.get("speed", 1.0),
            "output_path": str(output_wav),
            "api_key": str(voice_config.get("api_key", "") or ""),
            "model": str(voice_config.get("model", "") or ""),
        }
        for key, value in voice_config.items():
            normalized = str(key).strip()
            if not normalized or normalized.startswith("_"):
                continue
            values.setdefault(normalized, value)
        if "instruct" in values and "instructions" not in values:
            values["instructions"] = values["instruct"]
        if "instructions" in values and "instruct" not in values:
            values["instruct"] = values["instructions"]
        return values

    @staticmethod
    def _render_template(
        template: str,
        values: dict[str, Any],
        for_url: bool = False,
    ) -> str:
        rendered = template
        for key, value in values.items():
            if key == "speed":
                replacement = str(value)
            elif for_url:
                replacement = url_quote(str(value))
            else:
                replacement = json.dumps(str(value))[1:-1]
            rendered = rendered.replace(f"{{{{{key}}}}}", replacement)
        return rendered

    @staticmethod
    def _default_body_template() -> str:
        return (
            '{\n'
            '  "text": "{{text}}",\n'
            '  "voice": "{{voice}}",\n'
            '  "language": "{{language}}",\n'
            '  "speed": {{speed}}\n'
            '}'
        )

    @staticmethod
    def _sample_rate(voice_config: dict[str, Any]) -> int:
        try:
            return int(voice_config.get("sample_rate", 24000))
        except (TypeError, ValueError):
            return 24000

    @staticmethod
    def _timeout(voice_config: dict[str, Any]) -> int:
        try:
            return int(voice_config.get("timeout_seconds", 120))
        except (TypeError, ValueError):
            return 120
