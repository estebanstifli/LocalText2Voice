"""Headless voice design: synthesize, verify the 48 kHz master WAV, then publish."""
from __future__ import annotations

from dataclasses import asdict
from difflib import ndiff
import hashlib
import tempfile
import shutil
import uuid
import unicodedata
from pathlib import Path

from app.tts.audiocpp_config import BREEZE_MODEL, VOXCPM_MODELS, DEFAULTS, model_config, validated_config
from app.verification.faster_whisper_manager import FasterWhisperVerifier


SAMPLES = {
    "es": "Cada mañana abro la ventana y escucho los pájaros del jardín. Hoy tengo una nueva historia que compartir contigo. Busca un lugar cómodo y empecemos.",
    "en": "Every morning, I open the window and listen to the birds in the garden. Today, I have a new story to share with you. Take a comfortable seat, and let us begin.",
    "zh": "每天早晨，我都会打开窗户，听花园里的鸟儿歌唱。今天，我想和你分享一个新的故事。请找一个舒服的位置坐下，让我们一起开始吧。",
}
VOX_LANGUAGES = set("en es zh ar my da nl fi fr de el he hi id it ja km ko lo ms no pl pt ru sw sv tl th tr vi".split())
# Whisper's multilingual language codes; validation is independent of Qt/runtime installation.
WHISPER_LANGUAGES = set("en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta no th ur hr bg lt la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk sq sw gl mr pa si km sn yo so af oc ka be tg sd gu am yi lo uz fo ht ps tk nn mt sa lb my bo tl mg as tt haw ln ha ba jw su".split())


class VoiceDesignCancelled(Exception):
    """Cooperative cancellation before publication."""


class VoiceDesignError(ValueError):
    def __init__(self, code, stage, message, **details):
        super().__init__(message)
        self.payload = dict(status="failed", saved=False, code=code, stage=stage,
                            message=message, **details)


def normalize_transcript(text):
    # Keep accents, symbols and word boundaries; ignore only case, punctuation and spacing.
    text = unicodedata.normalize("NFC", text).casefold()
    text = "".join(c for c in text if not unicodedata.category(c).startswith("P"))
    return " ".join(text.split())


class VoiceDesignService:
    def __init__(self, host, verifier_factory=FasterWhisperVerifier):
        self.host = host
        self.gallery = host.voice_gallery_manager
        self.verifier_factory = verifier_factory

    def get_voice(self, voice_id):
        voice = self.gallery.get_voice(voice_id)
        if voice is None:
            raise VoiceDesignError("VOICE_NOT_FOUND", "lookup", "Voice not found.")
        return self.voice_payload(voice)

    @staticmethod
    def voice_payload(voice):
        result = asdict(voice)
        result["audio_uri"] = Path(voice.installed_path).resolve().as_uri() if voice.installed_path else None
        result["audio_versions"] = {
            rate: str(Path(voice.installed_path).parent / filename)
            for rate, filename in (voice.metadata or {}).get("audio_versions", {}).items()
        }
        return result

    def prepare(self, request):
        request = dict(request)
        for key in ("name", "description", "language"):
            if not isinstance(request.get(key), str) or not request[key].strip():
                raise VoiceDesignError("INVALID_REQUEST", "validation", f"{key} is required.")
            request[key] = request[key].strip()
        language = request["language"].lower().replace("_", "-").split("-")[0]
        language = {"english": "en", "spanish": "es", "french": "fr", "german": "de", "italian": "it", "portuguese": "pt"}.get(language, language)
        if language not in WHISPER_LANGUAGES and not (request.get("preview_only") and language == "auto"):
            raise VoiceDesignError("UNSUPPORTED_LANGUAGE", "validation", "Language is not supported by Whisper.")
        engine = request.get("engine_id") or "audiocpp"
        model = request.get("model") or ("voxcpm2_q8_0" if engine == "audiocpp" else "omnivoice")
        if engine not in {"audiocpp", "omnivoice"} or (engine == "audiocpp" and model not in {BREEZE_MODEL, *VOXCPM_MODELS}) or (engine == "omnivoice" and model != "omnivoice"):
            raise VoiceDesignError("UNSUPPORTED_MODEL", "validation", "Model does not support voice design.")
        if engine == "audiocpp" and language not in ({"en", "zh"} if model == BREEZE_MODEL else VOX_LANGUAGES):
            raise VoiceDesignError("UNSUPPORTED_LANGUAGE", "validation", "Language is not supported by the selected model.")
        supplied = request.get("sample_text")
        if supplied is not None and not isinstance(supplied, str):
            raise VoiceDesignError("INVALID_REQUEST", "validation", "sample_text must be a string.")
        text = (supplied or "").strip() or SAMPLES.get(language, "")
        if not text:
            raise VoiceDesignError("SAMPLE_TEXT_REQUIRED", "validation", "Provide sample_text for this language.")
        if len(text) > 600 or "{{" in text or not normalize_transcript(text):
            raise VoiceDesignError("INVALID_REQUEST", "validation", "Use 1–600 characters of plain sample text without markup.")
        if not request.get("preview_only") and any(v.name.strip().casefold() == request["name"].casefold() for v in self.gallery.list_voices(engine)):
            raise VoiceDesignError("NAME_CONFLICT", "validation", "A voice with this name already exists.")
        if not self.host.faster_whisper_manager.is_installed():
            raise VoiceDesignError("WHISPER_UNAVAILABLE", "validation", "Install Faster Whisper small before designing voices.")
        request.update(engine_id=engine, model=model, language=language, sample_text=text,
                       sample_text_source="user" if (supplied or "").strip() else "template")
        return request

    def run(self, request, cancel_event, progress=lambda *_: None):
        stage = "validation"
        def check():
            if cancel_event.is_set():
                raise VoiceDesignCancelled("Voice design cancelled.")
        try:
            check()
            request = self.prepare(request)
            engine, model = request["engine_id"], request["model"]
            if engine == "audiocpp":
                config = model_config(self.host.settings.get("audiocpp", {}), model)
                config = {key: value for key, value in config.items() if key in DEFAULTS}
                options = request.get("generation_parameters", {})
                allowed = set(DEFAULTS) - {"model", "voices", "reference_audio_path", "reference_text", "language", "instruction"}
                config.update({k: v for k, v in options.items() if k in allowed})
                config.update(reference_audio_path="", reference_text="", voices={},
                              instruction=request["description"], language=request["language"])
                config = validated_config(config)
                installed = self.host.audiocpp_manager.is_installed(model, config["backend"])
            else:
                config = {k: v for k, v in self.host.settings.get("omnivoice", {}).items()
                          if k in {"device", "dtype", "num_step"}}
                config.update({k: v for k, v in request.get("generation_parameters", {}).items()
                               if k in {"device", "dtype", "num_step", "duration", "speed"}})
                config.update(model=model, mode="design", instruct=request["description"],
                              language=request["language"], reference_audio_path="", reference_text="")
                installed = self.host.omnivoice_manager.is_installed()
            config.update(engine=engine)
            config.setdefault("speed", 1.0)
            if not installed:
                raise VoiceDesignError("MODEL_UNAVAILABLE", stage, "Install the selected TTS model first.")
            with tempfile.TemporaryDirectory(prefix="voice-design-") as directory:
                raw, wav = Path(directory) / "raw.wav", Path(directory) / "reference.wav"
                stage = "generation"
                progress(1, 4, "Generating voice sample")
                self.host.synthesize_segment(request["sample_text"], raw, config)
                check()
                stage = "audio_validation"
                duration = self.gallery._normalize_reference_audio(raw, wav,
                    self.host.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"), 1, 60, sample_rate=48000)
                stage = "transcription"
                progress(2, 4, "Verifying with Whisper")
                verifier = self.verifier_factory(self.host.faster_whisper_manager)
                try:
                    transcription = verifier.transcribe(wav, language=request["language"],
                                                        beam_size=5, device="cpu", compute_type="int8")
                finally:
                    verifier.close(force=True)
                check()
                stage = "verification"
                expected = normalize_transcript(request["sample_text"])
                actual_text = str(transcription.get("text", "")).strip()
                actual = normalize_transcript(actual_text)
                if expected != actual:
                    raise VoiceDesignError("TRANSCRIPT_MISMATCH", stage, "Whisper transcript differs from the sample text.",
                        expected_text=request["sample_text"], asr_transcript=actual_text,
                        differences=list(ndiff(expected.split(), actual.split())))
                metadata = dict(source="user_import", origin="voice_design", design_prompt=request["description"],
                    generation_parameters=config, verification_status="passed", verification_model="Systran/faster-whisper-small",
                    asr_transcript=actual_text, comparison_policy_version="strict-normalized-v1",
                    sample_text_source=request["sample_text_source"], sample_template_version=1 if request["sample_text_source"] == "template" else None,
                    audio_sha256=hashlib.sha256(wav.read_bytes()).hexdigest(),
                    verified_audio_sha256=hashlib.sha256(wav.read_bytes()).hexdigest(), verified_sample_rate=48000, duration_seconds=duration,
                    normalized_format="wav pcm_s16le mono 48000hz", duration_min_seconds=1, duration_max_seconds=60)
                if request.get("preview_only"):
                    check()
                    destination = self.gallery.files_root / "design-previews" / (str(uuid.uuid4()) + ".wav")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(wav, destination)
                    return dict(status="preview", saved=False, output_wav=str(destination.resolve()),
                                design_request=request, verification=metadata)
                stage = "save"
                progress(3, 4, "Saving verified voice")
                check()
                voice = self.gallery.save_verified_design(wav, request, metadata,
                    ffmpeg_path=self.host.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"))
                return dict(status="saved", saved=True, voice_id=voice.voice_id, name=voice.name,
                            voice=self.voice_payload(voice))
        except (VoiceDesignError, VoiceDesignCancelled):
            raise
        except Exception as exc:
            code = {"generation": "GENERATION_FAILED", "audio_validation": "INVALID_AUDIO",
                    "transcription": "TRANSCRIPTION_FAILED", "save": "SAVE_FAILED"}.get(stage, "INVALID_REQUEST")
            raise VoiceDesignError(code, stage, str(exc)) from exc

    def save_preview(self, preview, name, *, replace_voice_id=None, labels=None, draft_id=""):
        if preview.get("status") != "preview" or not str(name).strip():
            raise VoiceDesignError("INVALID_PREVIEW", "save", "A verified preview and a name are required.")
        request = {**preview["design_request"], "name": str(name).strip()}
        metadata = {**preview["verification"], "draft_id": draft_id}
        source = Path(preview["output_wav"])
        if metadata.get("verification_status") != "passed" or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != metadata["audio_sha256"]:
            raise VoiceDesignError("INVALID_PREVIEW", "save", "Preview is missing or changed. Generate it again.")
        voice = self.gallery.save_verified_design(source, request, metadata,
                                                 replace_voice_id=replace_voice_id, labels=labels,
                                                 ffmpeg_path=self.host.settings.get("ffmpeg_path", "ffmpeg/ffmpeg.exe"))
        return dict(status="saved", saved=True, voice_id=voice.voice_id, name=voice.name,
                    voice=self.voice_payload(voice))
