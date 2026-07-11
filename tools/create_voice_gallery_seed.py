from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GALLERY_ROOT = WORKSPACE_ROOT / "LocalText2Voice-VoiceGallery"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def app_data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "LocalText2Voice"
    return Path.home() / ".local" / "share" / "LocalText2Voice"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_audio(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def write_voice(base: Path, voice: dict[str, Any]) -> str:
    voice_dir = base / voice["language"] / voice["id"]
    if (voice_dir / "preview.wav").is_file() and not voice.get("preview_audio"):
        voice["preview_audio"] = "preview.wav"
    write_json(voice_dir / "voice.json", voice)
    return str((voice_dir / "voice.json").relative_to(base)).replace("\\", "/")


def reference_voice(
    *,
    engine: str,
    voice_id: str,
    name: str,
    language: str,
    language_name: str,
    source_wav: Path,
    ref_text: str,
    tags: list[str],
) -> dict[str, Any]:
    voice_dir = GALLERY_ROOT / "engines" / engine / language / voice_id
    has_audio = copy_audio(source_wav, voice_dir / "reference.wav")
    if has_audio:
        copy_audio(source_wav, voice_dir / "preview.wav")
    return {
        "id": voice_id,
        "name": name,
        "engine": engine,
        "language": language,
        "language_name": language_name,
        "type": "Reference voice",
        "install_type": "reference_audio",
        "ref_audio": "reference.wav" if has_audio else "",
        "preview_audio": "preview.wav" if has_audio else "",
        "ref_text": ref_text,
        "short_description": "Reference voice sample",
        "gender": "",
        "age_style": "",
        "voice_style": "narrator",
        "tags": tags,
        "mode": "reference_audio",
        "source": "LocalText2Voice seed gallery",
        "license_note": (
            "Use only voices you have permission to use. These starter references are "
            "sample clips intended for testing voice selection and installation."
        ),
    }


def builtin_voice(
    *,
    engine: str,
    voice_id: str,
    name: str,
    language: str,
    language_name: str,
    engine_voice_id: str,
    speaker_id: str = "",
    model_id: str = "",
    ref_text: str = "",
    tags: list[str],
) -> dict[str, Any]:
    return {
        "id": voice_id,
        "name": name,
        "engine": engine,
        "language": language,
        "language_name": language_name,
        "type": "Model speaker",
        "install_type": "engine_builtin",
        "preview_audio": "",
        "ref_audio": "",
        "ref_text": ref_text,
        "engine_voice_id": engine_voice_id,
        "speaker_id": speaker_id or engine_voice_id,
        "model_id": model_id,
        "short_description": "Built-in model speaker",
        "gender": "",
        "age_style": "",
        "voice_style": "",
        "tags": tags,
        "source": "LocalText2Voice engine preset",
        "license_note": "Built into the selected local TTS model. Preview audio can be generated later.",
    }


def piper_gallery_voice(voice: Any) -> dict[str, Any]:
    language = str(getattr(voice, "language", "") or "unknown")
    voice_id = str(getattr(voice, "voice_id", ""))
    display_name = str(getattr(voice, "display_name", "") or voice_id)
    safe_id = "piper_" + "".join(
        character.lower() if character.isalnum() else "_"
        for character in voice_id.replace(".onnx", "")
    ).strip("_")
    return {
        "id": safe_id,
        "name": display_name,
        "engine": "piper",
        "language": language,
        "language_name": language,
        "type": "Piper model",
        "install_type": "engine_builtin",
        "preview_audio": "",
        "ref_audio": "",
        "ref_text": preview_text_for_language(language),
        "engine_voice_id": voice_id,
        "speaker_id": voice_id,
        "model_id": "piper",
        "short_description": "Offline Piper voice model",
        "gender": "",
        "age_style": "",
        "voice_style": "local_offline",
        "tags": [language, "piper", "offline", "local"],
        "source": "Local Piper voice discovery",
        "license_note": "Piper voice model metadata. Check the original model license before redistribution.",
    }


def preview_text_for_language(language: str) -> str:
    key = language.strip().casefold().replace("_", "-")
    if key.startswith("es"):
        return "La luna esta preciosa esta noche."
    if key.startswith("fr"):
        return "La lune est magnifique ce soir."
    if key.startswith("it"):
        return "La luna e bellissima stasera."
    if key.startswith("de"):
        return "Der Mond ist heute Nacht wunderschoen."
    if key.startswith("pt"):
        return "A lua esta linda esta noite."
    if key.startswith("zh"):
        return "今晚的月亮很美。"
    if key.startswith("ja"):
        return "今夜の月はとてもきれいです。"
    return "The moon looks beautiful tonight."


def language_display_name(language: str) -> str:
    key = language.strip().casefold().replace("_", "-")
    names = {
        "en": "English",
        "en-us": "English",
        "en-gb": "English",
        "es": "Spanish",
        "fr": "French",
        "fr-fr": "French",
        "it": "Italian",
        "de": "German",
        "pt": "Portuguese",
        "pt-br": "Portuguese",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
        "ru": "Russian",
        "hi": "Hindi",
    }
    return names.get(key, language)


def safe_identifier(value: str) -> str:
    return "".join(
        character.lower() if character.isalnum() else "_"
        for character in value
    ).strip("_")


def kokoro_gallery_voices() -> list[dict[str, Any]]:
    from app.tts.kokoro_python_manager import KokoroPythonManager

    existing_ids = {
        "ef_dora": "kokoro_es_dora",
        "em_alex": "kokoro_es_alex",
        "af_heart": "kokoro_en_heart",
        "ff_siwis": "kokoro_fr_siwis",
        "if_sara": "kokoro_it_sara",
    }
    voices: list[dict[str, Any]] = []
    for voice in KokoroPythonManager().list_voices():
        language_name = language_display_name(voice.language)
        voices.append(
            builtin_voice(
                engine="kokoro",
                voice_id=existing_ids.get(
                    voice.voice_id,
                    f"kokoro_{safe_identifier(voice.voice_id)}",
                ),
                name=voice.display_name,
                language=voice.language,
                language_name=language_name,
                engine_voice_id=voice.voice_id,
                ref_text=preview_text_for_language(voice.language),
                tags=[
                    language_name.lower(),
                    "kokoro",
                    "offline",
                    "local",
                ],
            )
        )
    return voices


def qwen_gallery_voices() -> list[dict[str, Any]]:
    from app.tts.qwen_manager import QwenManager

    language_codes = {
        "Chinese": "zh",
        "English": "en",
        "Japanese": "ja",
        "Korean": "ko",
        "German": "de",
        "French": "fr",
        "Russian": "ru",
        "Portuguese": "pt",
        "Spanish": "es",
        "Italian": "it",
    }
    manager = QwenManager()
    voices: list[dict[str, Any]] = []
    for language in manager.list_languages():
        language_id = language.language_id
        language_code = language_codes.get(language_id, language_id.lower())
        for voice in manager.list_voices():
            voice_id = f"qwen_{safe_identifier(voice.voice_id)}_{safe_identifier(language_id)}"
            voices.append(
                builtin_voice(
                    engine="qwen",
                    voice_id=voice_id,
                    name=f"{voice.display_name} - {language.display_name}",
                    language=language_code,
                    language_name=language.display_name,
                    engine_voice_id=voice.voice_id,
                    speaker_id=voice.voice_id,
                    model_id="custom_voice_0_6b",
                    ref_text=preview_text_for_language(language_code),
                    tags=[
                        language.display_name.lower(),
                        "qwen",
                        "expressive",
                        "multilingual",
                    ],
                )
            )
    return voices


def omnivoice_supported_instruct(
    *,
    gender: str,
    age_style: str,
    voice_style: str,
) -> str:
    items: list[str] = []
    if gender in {"male", "female"}:
        items.append(gender)
    age_map = {
        "child": "child",
        "teenager": "teenager",
        "young_adult": "young adult",
        "adult": "middle-aged",
        "middle_aged": "middle-aged",
        "mature": "middle-aged",
        "elderly": "elderly",
        "ancient": "elderly",
    }
    age_item = age_map.get(age_style)
    if age_item and age_item not in items:
        items.append(age_item)
    pitch_map = {
        "energetic_promo": "high pitch",
        "dark_character": "very low pitch",
        "comedic_blunt": "moderate pitch",
        "playful_character": "high pitch",
        "calm_documentary": "moderate pitch",
        "documentary": "moderate pitch",
        "cinematic_trailer": "low pitch",
        "warm_explainer": "moderate pitch",
        "warm_storyteller": "low pitch",
        "upbeat_social": "high pitch",
        "gentle_bedtime": "moderate pitch",
        "warm_conversational": "moderate pitch",
        "educational": "moderate pitch",
        "literary_audiobook": "moderate pitch",
    }
    pitch_item = pitch_map.get(voice_style)
    if pitch_item and pitch_item not in items:
        items.append(pitch_item)
    return ", ".join(items) or "middle-aged, moderate pitch"


def designed_voice(
    *,
    voice_id: str,
    name: str,
    language: str,
    language_name: str,
    ref_text: str,
    short_description: str,
    gender: str,
    age_style: str,
    voice_style: str,
    instruct: str,
    tags: list[str],
) -> dict[str, Any]:
    return {
        "id": voice_id,
        "name": name,
        "engine": "omnivoice",
        "language": language,
        "language_name": language_name,
        "type": "Designed voice",
        "install_type": "engine_builtin",
        "preview_audio": "",
        "ref_audio": "",
        "ref_text": ref_text,
        "engine_voice_id": "design",
        "speaker_id": "",
        "model_id": "omnivoice",
        "short_description": short_description,
        "gender": gender,
        "age_style": age_style,
        "voice_style": voice_style,
        "style_description": instruct,
        "instruct": omnivoice_supported_instruct(
            gender=gender,
            age_style=age_style,
            voice_style=voice_style,
        ),
        "tags": tags,
        "mode": "voice_design",
        "source": "LocalText2Voice synthetic voice design preset",
        "license_note": (
            "Synthetic voice design preset. Do not use cloned third-party voices "
            "without permission."
        ),
    }


def main() -> None:
    from app.tts.voice_manager import VoiceManager

    chatterbox_dir = app_data_root() / "models" / "chatterbox" / "voices"
    chatterbox_sources = {
        "abigail": chatterbox_dir / "Abigail.wav",
        "adrian": chatterbox_dir / "Adrian.wav",
        "alice": chatterbox_dir / "Alice.wav",
        "connor": chatterbox_dir / "Connor.wav",
        "elena": chatterbox_dir / "Elena.wav",
        "gabriel": chatterbox_dir / "Gabriel.wav",
    }
    common_english = "Hello, this is a short sample for audiobook narration."
    designed_omnivoice_voices = [
        designed_voice(
            voice_id="omnivoice_en_sandra_dynamic",
            name="Sandra",
            language="en",
            language_name="English",
            ref_text="Hello, I am Sandra, a dynamic and energetic voice for modern podcasts.",
            short_description="Dynamic, engaging and energetic",
            gender="female",
            age_style="young_adult",
            voice_style="energetic_promo",
            instruct=(
                "A dynamic young female voice full of energy and charisma. "
                "Persuasive and engaging, perfect for promotions, calls to action, "
                "product sales, and upbeat podcast openings."
            ),
            tags=["english", "female", "young", "energetic", "promo", "podcast"],
        ),
        designed_voice(
            voice_id="omnivoice_en_victor_ancient",
            name="Victor",
            language="en",
            language_name="English",
            ref_text="The ancient gate opens, and Victor speaks from the shadows.",
            short_description="Deep, malevolent and ancient",
            gender="male",
            age_style="ancient",
            voice_style="dark_character",
            instruct=(
                "A deep ancient malevolence voice with slow gravity, dark texture, "
                "and cinematic menace. Great for characters, animations, fantasy, "
                "horror, and dramatic storytelling."
            ),
            tags=["english", "male", "deep", "dark", "character", "fantasy"],
        ),
        designed_voice(
            voice_id="omnivoice_en_joe_confident_idiot",
            name="Joe",
            language="en",
            language_name="English",
            ref_text="Listen, I am absolutely sure about this, even when I am totally wrong.",
            short_description="Confident idiot",
            gender="male",
            age_style="adult",
            voice_style="comedic_blunt",
            instruct=(
                "An uneducated, oblivious American male voice. Endlessly opinionated, "
                "blunt, funny, annoying, and confidently incorrect."
            ),
            tags=["english", "male", "comedy", "blunt", "character", "american"],
        ),
        designed_voice(
            voice_id="omnivoice_en_mini_lively",
            name="Mini",
            language="en",
            language_name="English",
            ref_text="Hi, I am Mini, and today we are going on a tiny adventure.",
            short_description="Lively cute little female",
            gender="female",
            age_style="child",
            voice_style="playful_character",
            instruct=(
                "A lively, adorable little girl character voice bursting with energy, "
                "innocent charm, playful giggles, and animated storytelling warmth."
            ),
            tags=["english", "female", "child", "cute", "animation", "edtech"],
        ),
        designed_voice(
            voice_id="omnivoice_en_evelyn_documentary",
            name="Evelyn",
            language="en",
            language_name="English",
            ref_text="Across the quiet valley, history still whispers through every stone.",
            short_description="Calm documentary narrator",
            gender="female",
            age_style="mature",
            voice_style="calm_documentary",
            instruct=(
                "A calm, intelligent female documentary narrator. Clear, measured, "
                "trustworthy, and elegant for educational videos and long-form history."
            ),
            tags=["english", "female", "documentary", "education", "calm"],
        ),
        designed_voice(
            voice_id="omnivoice_en_marcus_trailer",
            name="Marcus",
            language="en",
            language_name="English",
            ref_text="In a world where one choice changes everything, Marcus begins the story.",
            short_description="Epic trailer voice",
            gender="male",
            age_style="adult",
            voice_style="cinematic_trailer",
            instruct=(
                "A powerful cinematic trailer voice with depth, impact, suspense, "
                "and dramatic pacing. Big, confident, and polished."
            ),
            tags=["english", "male", "trailer", "cinematic", "deep", "promo"],
        ),
        designed_voice(
            voice_id="omnivoice_en_ava_product_guide",
            name="Ava",
            language="en",
            language_name="English",
            ref_text="Let me guide you through the next step in a simple and friendly way.",
            short_description="Friendly product guide",
            gender="female",
            age_style="young_adult",
            voice_style="warm_explainer",
            instruct=(
                "A friendly, modern female product guide. Warm, clear, helpful, "
                "and conversational for tutorials, onboarding, and SaaS explainers."
            ),
            tags=["english", "female", "explainer", "tutorial", "friendly"],
        ),
        designed_voice(
            voice_id="omnivoice_en_harold_storyteller",
            name="Harold",
            language="en",
            language_name="English",
            ref_text="Sit by the fire, and I will tell you how the old road was found.",
            short_description="Wise old storyteller",
            gender="male",
            age_style="elderly",
            voice_style="warm_storyteller",
            instruct=(
                "A wise elderly male storyteller with a warm, patient tone, gentle "
                "roughness, and classic audiobook pacing."
            ),
            tags=["english", "male", "elderly", "storyteller", "audiobook"],
        ),
        designed_voice(
            voice_id="omnivoice_en_riley_social_host",
            name="Riley",
            language="en",
            language_name="English",
            ref_text="Here is the quick version, and yes, this part is actually useful.",
            short_description="Bright social media host",
            gender="neutral",
            age_style="young_adult",
            voice_style="upbeat_social",
            instruct=(
                "A bright social media host voice. Fast enough to feel modern, "
                "but still clear, friendly, and easy to follow."
            ),
            tags=["english", "neutral", "social", "upbeat", "short-form"],
        ),
        designed_voice(
            voice_id="omnivoice_en_nora_bedtime",
            name="Nora",
            language="en",
            language_name="English",
            ref_text="The stars were soft above the rooftops, and the city finally slept.",
            short_description="Soft bedtime narrator",
            gender="female",
            age_style="adult",
            voice_style="gentle_bedtime",
            instruct=(
                "A soft, soothing female bedtime narrator. Gentle, intimate, slow, "
                "and peaceful for sleep stories and relaxing audiobooks."
            ),
            tags=["english", "female", "soft", "sleep", "relaxing", "audiobook"],
        ),
        designed_voice(
            voice_id="omnivoice_es_sandra_dinamica",
            name="Sandra",
            language="es",
            language_name="Spanish",
            ref_text="Hola, soy Sandra, una voz dinámica y llena de energía para tus podcasts.",
            short_description="Dinámica, atractiva y enérgica",
            gender="female",
            age_style="young_adult",
            voice_style="energetic_promo",
            instruct=(
                "Voz femenina joven, dinámica, atractiva y llena de carisma. "
                "Persuasiva y enérgica, ideal para promociones, llamadas a la acción "
                "y presentaciones comerciales."
            ),
            tags=["spanish", "female", "young", "energetic", "promo", "podcast"],
        ),
        designed_voice(
            voice_id="omnivoice_es_juan_carlos_calido",
            name="Juan Carlos",
            language="es",
            language_name="Spanish",
            ref_text="Hola, soy Juan Carlos, y te acompaño con un tono cercano y natural.",
            short_description="Cálido y conversacional",
            gender="male",
            age_style="middle_aged",
            voice_style="warm_conversational",
            instruct=(
                "Hablante latinoamericano de mediana edad. Tono cálido, agradable "
                "y conversacional. Ideal para narraciones, redes sociales y cursos cercanos."
            ),
            tags=["spanish", "male", "latin", "warm", "conversation", "course"],
        ),
        designed_voice(
            voice_id="omnivoice_es_victor_antiguo",
            name="Victor",
            language="es",
            language_name="Spanish",
            ref_text="Desde lo profundo de la tierra, una voz antigua despierta.",
            short_description="Profundo, malévolo y antiguo",
            gender="male",
            age_style="ancient",
            voice_style="dark_character",
            instruct=(
                "Voz masculina profunda, antigua y malévola. Lenta, grave, oscura "
                "y cinematográfica, perfecta para personajes, fantasía, terror y animación."
            ),
            tags=["spanish", "male", "deep", "dark", "character", "fantasy"],
        ),
        designed_voice(
            voice_id="omnivoice_es_mini_alegre",
            name="Mini",
            language="es",
            language_name="Spanish",
            ref_text="Hola, soy Mini, y hoy vamos a vivir una aventura pequeñita.",
            short_description="Niña alegre y adorable",
            gender="female",
            age_style="child",
            voice_style="playful_character",
            instruct=(
                "Voz de niña alegre, adorable y llena de energía. Natural, inocente, "
                "juguetona y expresiva para animación, juegos, cuentos y educación infantil."
            ),
            tags=["spanish", "female", "child", "cute", "animation", "edtech"],
        ),
        designed_voice(
            voice_id="omnivoice_es_lucia_documental",
            name="Lucía",
            language="es",
            language_name="Spanish",
            ref_text="Cada ciudad guarda una memoria que solo aparece cuando sabemos escuchar.",
            short_description="Narradora documental clara",
            gender="female",
            age_style="adult",
            voice_style="documentary",
            instruct=(
                "Voz femenina clara, serena e inteligente para documental. "
                "Ritmo medido, buena dicción y tono de confianza para educación e historia."
            ),
            tags=["spanish", "female", "documentary", "education", "clear"],
        ),
        designed_voice(
            voice_id="omnivoice_es_mateo_trailer",
            name="Mateo",
            language="es",
            language_name="Spanish",
            ref_text="Este verano, una decisión cambiará el destino de todos.",
            short_description="Tráiler épico cinematográfico",
            gender="male",
            age_style="adult",
            voice_style="cinematic_trailer",
            instruct=(
                "Voz masculina potente de tráiler cinematográfico. Profunda, intensa, "
                "con suspense, impacto y ritmo dramático."
            ),
            tags=["spanish", "male", "trailer", "cinematic", "deep", "promo"],
        ),
        designed_voice(
            voice_id="omnivoice_es_carmen_profesora",
            name="Carmen",
            language="es",
            language_name="Spanish",
            ref_text="Vamos paso a paso, porque aprender también puede ser sencillo.",
            short_description="Profesora cercana y paciente",
            gender="female",
            age_style="mature",
            voice_style="educational",
            instruct=(
                "Voz femenina madura, cercana y paciente. Explica con claridad, "
                "amabilidad y calma, ideal para cursos, tutoriales y educación."
            ),
            tags=["spanish", "female", "teacher", "education", "patient"],
        ),
        designed_voice(
            voice_id="omnivoice_es_diego_comico",
            name="Diego",
            language="es",
            language_name="Spanish",
            ref_text="Yo lo tengo clarísimo, aunque probablemente no haya entendido nada.",
            short_description="Cómico seguro y torpe",
            gender="male",
            age_style="adult",
            voice_style="comedic_blunt",
            instruct=(
                "Voz masculina cómica, demasiado segura de sí misma, torpe y opinadora. "
                "Graciosa, directa, un poco pesada y deliberadamente incorrecta."
            ),
            tags=["spanish", "male", "comedy", "blunt", "character"],
        ),
        designed_voice(
            voice_id="omnivoice_es_valeria_audiolibro",
            name="Valeria",
            language="es",
            language_name="Spanish",
            ref_text="La carta permanecía sobre la mesa, como si todavía respirara.",
            short_description="Audiolibro íntimo y elegante",
            gender="female",
            age_style="adult",
            voice_style="literary_audiobook",
            instruct=(
                "Voz femenina elegante, íntima y literaria. Lectura expresiva pero "
                "contenida, perfecta para audiolibros, novela y narración emocional."
            ),
            tags=["spanish", "female", "audiobook", "literary", "warm"],
        ),
        designed_voice(
            voice_id="omnivoice_es_rafael_cuento",
            name="Rafael",
            language="es",
            language_name="Spanish",
            ref_text="Venid cerca, que esta historia empezó antes de que existieran los mapas.",
            short_description="Anciano sabio de cuento",
            gender="male",
            age_style="elderly",
            voice_style="warm_storyteller",
            instruct=(
                "Voz masculina anciana, sabia y cálida. Ritmo pausado, tono de cuento "
                "clásico y una textura amable para narración fantástica o audiolibros."
            ),
            tags=["spanish", "male", "elderly", "storyteller", "audiobook"],
        ),
    ]
    voices_by_engine: dict[str, list[dict[str, Any]]] = {
        "omnivoice": [
            reference_voice(
                engine="omnivoice",
                voice_id=f"omnivoice_{key}",
                name=key.title(),
                language="en",
                language_name="English",
                source_wav=path,
                ref_text=common_english,
                tags=["english", "reference", "voice-clone", "podcast"],
            )
            for key, path in list(chatterbox_sources.items())[:4]
        ]
        + designed_omnivoice_voices,
        "_kokoro_seed_legacy": [
            builtin_voice(
                engine="kokoro",
                voice_id="kokoro_es_dora",
                name="Spanish - Dora",
                language="es",
                language_name="Spanish",
                engine_voice_id="ef_dora",
                ref_text="La luna esta preciosa esta noche.",
                tags=["spanish", "female", "kokoro", "audiobook"],
            ),
            builtin_voice(
                engine="kokoro",
                voice_id="kokoro_es_alex",
                name="Spanish - Alex",
                language="es",
                language_name="Spanish",
                engine_voice_id="em_alex",
                ref_text="La luna esta preciosa esta noche.",
                tags=["spanish", "male", "kokoro", "course"],
            ),
            builtin_voice(
                engine="kokoro",
                voice_id="kokoro_en_heart",
                name="American English - Heart",
                language="en-us",
                language_name="English",
                engine_voice_id="af_heart",
                ref_text="The moon looks beautiful tonight.",
                tags=["english", "female", "kokoro"],
            ),
            builtin_voice(
                engine="kokoro",
                voice_id="kokoro_fr_siwis",
                name="French - Siwis",
                language="fr-fr",
                language_name="French",
                engine_voice_id="ff_siwis",
                ref_text="La lune est magnifique ce soir.",
                tags=["french", "kokoro"],
            ),
            builtin_voice(
                engine="kokoro",
                voice_id="kokoro_it_sara",
                name="Italian - Sara",
                language="it",
                language_name="Italian",
                engine_voice_id="if_sara",
                ref_text="La luna e bellissima stasera.",
                tags=["italian", "female", "kokoro"],
            ),
        ],
        "_qwen_seed_legacy": [
            builtin_voice(
                engine="qwen",
                voice_id="qwen_serena_spanish",
                name="Serena - Spanish",
                language="es",
                language_name="Spanish",
                engine_voice_id="Serena",
                speaker_id="Serena",
                model_id="custom_voice_0_6b",
                ref_text="La luna esta preciosa esta noche.",
                tags=["spanish", "qwen", "expressive", "course"],
            ),
            builtin_voice(
                engine="qwen",
                voice_id="qwen_sohee_english",
                name="Sohee - English",
                language="en",
                language_name="English",
                engine_voice_id="Sohee",
                speaker_id="Sohee",
                model_id="custom_voice_0_6b",
                ref_text="The moon looks beautiful tonight.",
                tags=["english", "qwen", "podcast"],
            ),
            builtin_voice(
                engine="qwen",
                voice_id="qwen_uncle_fu_chinese",
                name="Uncle Fu - Chinese",
                language="zh",
                language_name="Chinese",
                engine_voice_id="Uncle_fu",
                speaker_id="Uncle_fu",
                model_id="custom_voice_0_6b",
                ref_text="今晚的月亮很美。",
                tags=["chinese", "qwen"],
            ),
            builtin_voice(
                engine="qwen",
                voice_id="qwen_vivian_french",
                name="Vivian - French",
                language="fr",
                language_name="French",
                engine_voice_id="Vivian",
                speaker_id="Vivian",
                model_id="custom_voice_0_6b",
                ref_text="La lune est magnifique ce soir.",
                tags=["french", "qwen"],
            ),
            builtin_voice(
                engine="qwen",
                voice_id="qwen_ryan_german",
                name="Ryan - German",
                language="de",
                language_name="German",
                engine_voice_id="Ryan",
                speaker_id="Ryan",
                model_id="custom_voice_0_6b",
                ref_text="Der Mond ist heute Nacht wunderschoen.",
                tags=["german", "qwen"],
            ),
        ],
    }
    voices_by_engine["kokoro"] = kokoro_gallery_voices()
    voices_by_engine["qwen"] = qwen_gallery_voices()
    voices_by_engine.pop("_kokoro_seed_legacy", None)
    voices_by_engine.pop("_qwen_seed_legacy", None)
    piper_voices = VoiceManager(PROJECT_ROOT / "voices").discover()
    if piper_voices:
        voices_by_engine["piper"] = [
            piper_gallery_voice(voice)
            for voice in piper_voices
        ]

    indexes: list[str] = []
    for engine, voices in voices_by_engine.items():
        engine_base = GALLERY_ROOT / "engines" / engine
        paths = [write_voice(engine_base, voice) for voice in voices]
        index_path = engine_base / "index.json"
        write_json(
            index_path,
            {
                "schema_version": 1,
                "engine": engine,
                **(
                    {"compatible_engines": ["chatterbox"]}
                    if engine == "omnivoice"
                    else {}
                ),
                "voices": paths,
            },
        )
        indexes.append(str(index_path.relative_to(GALLERY_ROOT)).replace("\\", "/"))

    write_json(
        GALLERY_ROOT / "catalog.json",
        {
            "schema_version": 1,
            "name": "LocalText2Voice Voice Gallery",
            "description": "Previewable and installable voice catalog for LocalText2Voice.",
            "indexes": indexes,
            "voices": [],
        },
    )
    schema_path = GALLERY_ROOT / "schema" / "voice.schema.json"
    if not schema_path.is_file():
        write_json(
            schema_path,
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "LocalText2Voice gallery voice",
                "type": "object",
                "required": ["id", "name", "engine", "language", "install_type"],
            },
        )
    (GALLERY_ROOT / "tools").mkdir(parents=True, exist_ok=True)
    (GALLERY_ROOT / "tools" / "README.md").write_text(
        "# Gallery tools\n\n"
        "This gallery is maintained from the LocalText2Voice app repository.\n\n"
        "Useful commands from the workspace root:\n\n"
        "```powershell\n"
        "python course_to_podcast/tools/create_voice_gallery_seed.py\n"
        "python course_to_podcast/tools/generate_voice_gallery_previews.py qwen "
        "--app-root course_to_podcast/dist/LocalText2Voice --force\n"
        "python course_to_podcast/tools/validate_voice_gallery.py LocalText2Voice-VoiceGallery\n"
        "```\n\n"
        "Preview generation uses the app's local runtime managers, so the target TTS "
        "engine must already be installed on the developer machine or in the portable "
        "Windows app passed through `--app-root`.\n",
        encoding="utf-8",
    )
    (GALLERY_ROOT / "README.md").write_text(
        "# LocalText2Voice Voice Gallery\n\n"
        "External voice catalog for LocalText2Voice. The app can sync this catalog into a local "
        "SQLite cache, play fast preview audio, and download only the voices the user wants.\n\n"
        "Current seed:\n\n"
        "- OmniVoice reference and designed voices with previews.\n"
        "- Chatterbox-compatible rows are generated by the app from the OmniVoice index through `compatible_engines`.\n"
        "- Kokoro built-in model speakers with generated previews where supported by the local backend.\n"
        "- Qwen built-in speaker/language combinations with generated previews.\n"
        "- Piper voices discovered from the LocalText2Voice development workspace with previews.\n\n"
        "Current preview coverage: 138 of 138 direct catalog entries.\n\n"
        "Validate before pushing:\n\n"
        "```powershell\n"
        "python course_to_podcast/tools/validate_voice_gallery.py LocalText2Voice-VoiceGallery\n"
        "```\n",
        encoding="utf-8",
    )
    print(f"Voice gallery seed written to: {GALLERY_ROOT}")


if __name__ == "__main__":
    main()
