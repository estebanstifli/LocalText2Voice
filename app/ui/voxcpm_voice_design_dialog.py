"""VoxCPM2 voice design using the common audio.cpp preview/catalog workflow."""
from app.tts.audiocpp_config import VOXCPM_MODEL, VOXCPM_MODELS, MODELS
from .breeze_voice_design_dialog import BreezeVoiceDesignDialog, EXAMPLES


class VoxCPMVoiceDesignDialog(BreezeVoiceDesignDialog):
    def __init__(self, client, gallery, config, ffmpeg_path, translate, parent=None):
        self.MODEL = config.get("model") if config.get("model") in VOXCPM_MODELS else VOXCPM_MODEL
        super().__init__(client, gallery, config, ffmpeg_path, translate, parent)
        self.setWindowTitle(self.windowTitle() + " · " + MODELS[self.MODEL].precision)

    MODEL = VOXCPM_MODEL
    TAG = "voxcpm2"
    GUIDANCE = 2
    TITLE = ("voxcpm_design_title", "VoxCPM 2 Voice Designer")
    HELP = ("voxcpm_design_help", "Describe a voice without parentheses and write a short sample in the desired language. VoxCPM 2 supports 30 languages, including Spanish; language is inferred from the sample. No reference is needed. Listen, then save the full WAV and its clean transcript as a reusable voice.")
    GENERATING = ("voxcpm_design_generating", "Generating a voice sample with VoxCPM 2. Please wait before closing.")
    EXAMPLES = {
        **EXAMPLES,
        "es": (
            "Una narradora adulta con voz cálida y clara, ritmo pausado y expresión natural.",
            "Cada mañana abro la ventana y escucho los pájaros del jardín. Hoy tengo una nueva historia que compartir contigo. Busca un lugar cómodo y empecemos.",
        ),
    }
    LANGUAGES = tuple((code, "language_" + name.lower(), name) for code, name in (
        ("en", "English"), ("es", "Spanish"), ("zh", "Chinese"), ("ar", "Arabic"),
        ("my", "Burmese"), ("da", "Danish"), ("nl", "Dutch"), ("fi", "Finnish"),
        ("fr", "French"), ("de", "German"), ("el", "Greek"), ("he", "Hebrew"),
        ("hi", "Hindi"), ("id", "Indonesian"), ("it", "Italian"), ("ja", "Japanese"),
        ("km", "Khmer"), ("ko", "Korean"), ("lo", "Lao"), ("ms", "Malay"),
        ("no", "Norwegian"), ("pl", "Polish"), ("pt", "Portuguese"), ("ru", "Russian"),
        ("sw", "Swahili"), ("sv", "Swedish"), ("tl", "Tagalog"), ("th", "Thai"),
        ("tr", "Turkish"), ("vi", "Vietnamese"),
    ))
    GENERATION_FIELDS = (
        ("guidance_scale", 0, 20, False), ("num_inference_steps", 1, 100, True),
        ("seed", -1, 2147483647, True), ("max_tokens", 1, 16384, True),
        ("text_chunk_size", 20, 2048, True),
    )
