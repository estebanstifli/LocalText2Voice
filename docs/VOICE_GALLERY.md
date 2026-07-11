# LocalText2Voice Voice Gallery

The voice gallery is the external catalog used by the **Voices** page.

Repository:

<https://github.com/estebanstifli/LocalText2Voice-VoiceGallery>

## Goals

- Keep preview/reference audio outside the main application repository.
- Let users preview voices quickly before installing anything.
- Download only the voices the user wants.
- Support multiple engines with one common UI.
- Cache catalog metadata locally in SQLite for fast browsing.

## App Storage

Catalog cache:

```text
%LOCALAPPDATA%/LocalText2Voice/voice-gallery/voice-gallery.sqlite3
```

Installed gallery voices:

```text
%LOCALAPPDATA%/LocalText2Voice/voice-gallery/installed/<engine>/<voice_id>/
```

Imported local voices:

```text
%LOCALAPPDATA%/LocalText2Voice/voice-gallery/imported/<engine>/
```

## Catalog Format

`catalog.json` points to one or more engine indexes:

```json
{
  "schema_version": 1,
  "indexes": [
    "engines/chatterbox/index.json",
    "engines/omnivoice/index.json"
  ],
  "voices": []
}
```

Each engine index points to `voice.json` files:

```json
{
  "schema_version": 1,
  "engine": "chatterbox",
  "voices": ["en/chatterbox_abigail/voice.json"]
}
```

Each `voice.json` describes one usable voice:

```json
{
  "id": "chatterbox_abigail",
  "name": "Abigail",
  "engine": "chatterbox",
  "language": "en",
  "language_name": "English",
  "type": "Reference voice",
  "install_type": "reference_audio",
  "preview_audio": "preview.wav",
  "ref_audio": "reference.wav",
  "ref_text": "Hello, this is a short sample for audiobook narration.",
  "short_description": "Reference voice sample",
  "gender": "",
  "age_style": "",
  "voice_style": "narrator",
  "tags": ["english", "reference", "podcast", "audiobook"]
}
```

Recommended metadata fields for browsing/filtering:

| Field | Meaning |
| --- | --- |
| `short_description` | One-line marketing-style summary shown in the Voices table. |
| `gender` | Broad voice identity, for example `female`, `male`, or `neutral`. |
| `age_style` | Voice age/tone bucket such as `child`, `young_adult`, `middle_aged`, `mature`, or `elderly`. |
| `voice_style` | Functional style such as `warm_storyteller`, `cinematic_trailer`, `educational`, or `energetic_promo`. |
| `tags` | Searchable free-form labels. |
| `style_description` | Optional long-form style description for catalog/documentation. |
| `instruct` | Engine-specific instruction sent to the TTS engine. OmniVoice currently accepts controlled labels such as `female, young adult, high pitch`, not arbitrary prose. |

## Install Types

| Type | Meaning |
| --- | --- |
| `engine_builtin` | The voice is part of an installed model, such as Qwen or Kokoro. The catalog can still provide preview audio. |
| `reference_audio` | The app downloads or copies a WAV/MP3 reference file for engines such as Chatterbox or OmniVoice. |
| `piper_model` | Reserved for a future unified Piper `.onnx` download flow. Piper currently keeps its dedicated manager. |

## Compatible Engines

An engine index can declare that its voices are usable by another engine:

```json
{
  "schema_version": 1,
  "engine": "omnivoice",
  "compatible_engines": ["chatterbox"],
  "voices": ["en/omnivoice_en_harold_storyteller/voice.json"]
}
```

The desktop app expands these entries into engine-specific rows during sync.
For example, an OmniVoice WAV reference can appear as a Chatterbox reference
voice without duplicating the audio files in the gallery repository.

## Current Seed

The current public seed contains 138 direct catalog entries in this development
workspace. OmniVoice voices are also exposed to Chatterbox through
`compatible_engines`, so the app sees 24 additional Chatterbox-compatible rows
after syncing:

- 4 OmniVoice reference voices with preview/reference WAV.
- 20 OmniVoice designed voices with preview WAV, 10 English and 10 Spanish.
- 21 Kokoro built-in voice entries.
- 90 Qwen built-in speaker/language entries.
- 3 discovered Piper voice entries with generated preview WAV files.

The seed can add more Piper entries whenever `tools/create_voice_gallery_seed.py`
detects local `.onnx` voices in the app `voices/` directory.
Preview WAV files currently exist for 138 of 138 direct entries:

- 24/24 OmniVoice previews.
- 21/21 Kokoro previews.
- 90/90 Qwen previews.
- 3/3 Piper previews.

## Maintenance Tools

Run these commands from the main LocalText2Voice repository.

Validate the external gallery before committing:

```powershell
python tools/validate_voice_gallery.py ..\LocalText2Voice-VoiceGallery
```

Regenerate the catalog seed:

```powershell
python tools/create_voice_gallery_seed.py
```

Generate missing preview WAV files for one engine:

```powershell
python tools/generate_voice_gallery_previews.py kokoro
python tools/generate_voice_gallery_previews.py qwen
python tools/generate_voice_gallery_previews.py omnivoice
python tools/generate_voice_gallery_previews.py piper
```

When the engines are installed in the portable Windows build, point the script
at that app root:

```powershell
python tools/generate_voice_gallery_previews.py qwen --app-root .\dist\LocalText2Voice
```

Use `--force` to replace existing previews:

```powershell
python tools/generate_voice_gallery_previews.py qwen --force
```

Preview generation uses the same local runtime managers as the desktop app, so
the target engine must already be installed locally. Piper preview generation
also requires `engines/piper/piper.exe` and the matching local `.onnx` voice.

## Release Checklist

Before pushing the voice gallery repository:

1. Generate or update voice entries.
2. Generate preview WAV files for every visible voice.
3. Run `python tools/validate_voice_gallery.py ..\LocalText2Voice-VoiceGallery`.
4. Open the desktop app, use **Voices > Sync catalog**, and verify:
   - the selected engine only shows compatible voices,
   - preview playback starts immediately,
   - reference voices can be installed/removed,
   - built-in voices are marked as built in.
5. Commit and push `LocalText2Voice-VoiceGallery`.
