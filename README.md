<p align="center">
  <img src="assets/logotipo.png" alt="LocalText2Voice logo" width="128">
</p>

<h1 align="center">LocalText2Voice</h1>

<p align="center">
  <strong>Open-source, offline AI text-to-speech for long-form content.</strong><br>
  Convert documents into MP3 narration, podcasts, audiobooks, lessons, and course audio on Windows.
</p>

<p align="center">
  <a href="https://github.com/estebanstifli/LocalText2Voice/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/estebanstifli/LocalText2Voice?display_name=tag&sort=semver"></a>
  <a href="https://github.com/estebanstifli/LocalText2Voice/blob/main/LICENSE"><img alt="MIT license" src="https://img.shields.io/github/license/estebanstifli/LocalText2Voice"></a>
  <img alt="Windows 10 and 11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows">
  <img alt="Python 3.10 or newer" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Offline and local" src="https://img.shields.io/badge/AI-local%20%26%20offline-5C2D91">
</p>

<p align="center">
  <a href="https://github.com/estebanstifli/LocalText2Voice/releases/latest"><strong>Download for Windows</strong></a>
  ·
  <a href="https://github.com/estebanstifli/LocalText2Voice/raw/main/docs/audio/localtext2voice-demo-en.mp3"><strong>Listen to an MP3 demo</strong></a>
  ·
  <a href="https://andromedanova.com"><strong>AndromedaNova.com</strong></a>
</p>

LocalText2Voice is a lightweight Python and PySide6 desktop application that
runs neural text-to-speech locally with [Piper TTS](https://github.com/rhasspy/piper).
It is designed for long documents, works without a GPU, and uses
[FFmpeg](https://ffmpeg.org/) to produce clean narration or a complete podcast
mix with music, fades, loudness normalization, and ducking.

> **Resumen en español:** aplicación de escritorio open source que convierte
> textos largos en voz y podcasts MP3 mediante inteligencia artificial local.
> Funciona sin conexión, sin GPU y con voces Piper descargables desde la propia
> interfaz.

## Screenshots

### Generate long-form audio without freezing the interface

![LocalText2Voice offline AI text-to-speech desktop app generating an MP3 podcast with Piper TTS](capturas/generating.png)

### Browse, preview, download, and manage multilingual Piper voices

![LocalText2Voice Piper voice manager with downloadable multilingual neural TTS models and voice previews](capturas/voice_manager.png)

## Why LocalText2Voice?

- **Private by default:** text and speech synthesis stay on the computer.
- **Built for long content:** safe text chunking, chapter detection, paragraph
  pauses, and sequential output files.
- **No GPU required:** Piper provides fast neural TTS inference on normal CPUs.
- **Podcast-ready:** keep clean narration and optionally create a second mix
  with intro, background music, outro, fades, normalization, and ducking.
- **Visual mix preview:** inspect voice, music, and combined waveforms before
  rendering another podcast mix.
- **Portable Windows app:** extract a folder, run the executable, and download
  a voice from the built-in manager.
- **Open architecture:** new local or cloud TTS engines can implement one
  stable interface without rewriting the application.

Typical uses include course narration, study materials, accessibility,
podcasts, audiobooks, training content, articles, documentation, and
voice-over drafts.

## Features

### AI speech synthesis

- Local neural TTS with Piper and ONNX voice models.
- Optional API engines for OpenAI TTS, ElevenLabs, Google Gemini TTS, and
  Azure Speech.
- Optional Kokoro local engine with on-demand model download to the user's
  local app data folder. It uses the bundled Python runtime internally and
  can automatically use CUDA through ONNX Runtime when supported.
- Optional Chatterbox local GPU engine for advanced voice cloning and
  expressive multilingual speech, installed into the embedded Python runtime.
- Optional Qwen3 TTS local neural engine with on-demand model cache and
  isolated Python dependencies. On NVIDIA GPUs it uses `faster-qwen3-tts`
  with CUDA Graphs for much faster synthesis.
- Built-in voice catalog with language/quality filters.
- Remote voice sample playback before downloading a model.
- Background downloads with cancellation, size validation, SHA-256 checks,
  and atomic installation.
- Voice speed control and automatic discovery of installed models.
- The default local workflow requires no cloud account, API key, GPU, or
  global Piper installation.

### Long-form text processing

- Paste text or import `.txt`, `.md`, and `.docx` files.
- Normalize whitespace and unsupported characters.
- Preserve paragraph boundaries and split long text into TTS-safe blocks.
- Detect Markdown headings, chapters, lessons, modules, and short uppercase
  headings.
- Export one MP3 or one MP3 per chapter.
- Natural randomized paragraph pauses with adaptive timing after long
  paragraphs and periodic reading breaks.

### Podcast production

- Export clean narration as `podcast1.mp3`, `podcast2.mp3`, and so on without
  overwriting previous work.
- Create a separate podcast mix while retaining the clean narration.
- Optional intro, looped background music, and outro from MP3 or WAV files.
- Voice/music volume in dB, fade in/out, and silence between sections.
- Voice start offset in milliseconds, useful for leaving a music-only intro
  before narration starts.
- Optional music tail after narration for a more natural ending.
- Basic FFmpeg sidechain ducking while the voice is speaking.
- Optional loudness normalization to `-16 LUFS`.
- Configurable MP3 bitrate and ID3 title, artist, and album metadata.
- Post-generation **Audio Mix Preview** page with three lightweight waveform
  graphs: Voice, Music, and Mix Preview.
- Timeline zoom and horizontal scrolling so long audiobooks can be inspected
  without compressing the whole project into one tiny waveform.
- The preview panel can render and play a temporary 15-second mix, then render
  a new full podcast mix without running TTS again.

### Desktop experience

- Responsive PySide6 interface with worker-thread generation.
- Live block progress, elapsed time, estimated remaining time, and visible log.
- Safe cancellation of the active Piper process and temporary-file cleanup.
- Open-output-folder action after generation.
- English, Spanish, French, German, Italian, Portuguese, Simplified Chinese,
  Japanese, Arabic, and Hindi interface translations.
- Right-to-left layout support for Arabic.

## Quick Start on Windows

1. Open the [latest release](https://github.com/estebanstifli/LocalText2Voice/releases/latest).
2. Download `LocalText2Voice-v0.4.6-windows-x64.zip`.
3. Extract the ZIP to a folder where you have write permission.
4. Run `LocalText2Voice.exe`.
5. Open **Manage voices**, preview a voice, and download it.
6. Paste or import your text, choose the voice, and select **Generate Audio**.

The portable release includes the application, Piper runtime, embedded Python
runtime, and FFmpeg. Piper voice models, Kokoro model assets and dependencies,
and optional Chatterbox/Qwen3 GPU assets are downloaded or installed
separately because their licenses, model cards, package size, and hardware
requirements can differ. Remote API engines only need provider credentials in
Settings > TTS Engines.

### Audio example

[Listen to the English MP3 demo generated locally with Piper TTS](https://github.com/estebanstifli/LocalText2Voice/raw/main/docs/audio/localtext2voice-demo-en.mp3)

The demo was generated by LocalText2Voice with the `en_GB-alan-medium` Piper
voice. It contains no cloud-generated speech.

## How It Works

```mermaid
flowchart LR
    A["TXT, Markdown, DOCX, or pasted text"] --> B["Text normalization and heading detection"]
    B --> C["Safe chunks and natural pause plan"]
    C --> D["Selected TTS engine: Piper, Kokoro, Chatterbox, Qwen3, or API"]
    D --> E["Temporary WAV blocks"]
    E --> F["FFmpeg assembly and MP3 encoding"]
    F --> G["Clean narration"]
    F --> H["Optional podcast mix"]
```

## AI Engineering Highlights

LocalText2Voice is an applied AI engineering project rather than a model
training project. It integrates pretrained neural speech models into a
reliable end-user workflow:

- **Local model inference:** orchestrates Piper ONNX voices through an isolated
  subprocess adapter with executable and model validation.
- **Model lifecycle management:** discovers a remote model catalog, previews
  samples, validates downloads, and installs voices atomically.
- **NLP-oriented preprocessing:** applies language-agnostic text cleanup,
  sentence-aware chunking, heading heuristics, and prosody-oriented pause
  planning for long-form synthesis.
- **Production concurrency:** keeps the GUI responsive with Qt worker threads,
  progress signals, ETA calculation, cancellation, and child-process cleanup.
- **Audio DSP pipeline:** composes narration and music with FFmpeg filters for
  looping, fading, sidechain compression, metadata, and loudness normalization.
- **Extensible providers:** isolates TTS behind `BaseTTSEngine` and reserves a
  provider interface for future LLM-assisted course generation.

This demonstrates practical experience with AI model integration, ONNX-based
inference, desktop product engineering, multimedia processing, asynchronous
workflows, internationalization, packaging, and automated testing.

## Technology Stack

| Technology | Role |
| --- | --- |
| Python 3.10+ | Application, processing pipeline, configuration, and tests |
| PySide6 / Qt 6 | Native desktop interface, signals, threads, and i18n-ready UI |
| Piper TTS | Fast local neural text-to-speech engine |
| Kokoro ONNX | Optional higher-quality local CPU TTS engine |
| Chatterbox TTS | Optional advanced local GPU TTS and voice cloning engine |
| Qwen3 TTS | Optional advanced local neural TTS engine with preset speakers |
| OpenAI / ElevenLabs / Gemini / Azure APIs | Optional cloud TTS providers |
| ONNX voice models | Portable pretrained speech models |
| FFmpeg | WAV assembly, MP3 encoding, mixing, ducking, fades, and loudnorm |
| PyInstaller | Portable Windows folder distribution |
| python-docx | Optional Microsoft Word document import |

## Architecture

```text
LocalText2Voice/
|-- main.py
|-- app/
|   |-- core/       # Text processing, settings, project and audio pipeline
|   |-- tts/        # Engine interface, Piper adapter and voice management
|   |-- ui/         # PySide6 windows and reusable widgets
|   |-- workers/    # Background generation and download workers
|   |-- utils/      # Paths, logging and FFmpeg helpers
|   `-- llm/        # Future provider interface; no cloud integration yet
|-- locales/        # Auto-discovered JSON translations
|-- engines/piper/  # Portable Piper runtime
|-- engines/chatterbox/ # Reserved for future native runtimes
|-- runtimes/python311/ # Bundled embedded Python runtime for Python engines
|-- voices/         # External ONNX voice models
|-- ffmpeg/         # Portable FFmpeg executable
|-- music/          # Optional intro, background and outro library
|-- tests/
`-- output/
```

The audio pipeline depends on the abstract `BaseTTSEngine` contract. Piper is
the default local implementation. Kokoro is the optional local neural TTS
implementation and uses the bundled embedded Python runtime internally with
on-demand model download and automatic CPU/CUDA backend selection. Chatterbox
is available as an advanced local GPU engine through the embedded Python runtime.
Qwen3 TTS is available as a separate local neural engine with isolated
dependencies and a Hugging Face model cache. OpenAI TTS, ElevenLabs, Google
Gemini TTS, and Azure Speech are available as API engines. Future local engines
such as XTTS can be added later without coupling them to the UI.

## Run from Source

Python 3.10 or newer is recommended:

```powershell
git clone https://github.com/estebanstifli/LocalText2Voice.git
cd LocalText2Voice
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

The repository excludes large third-party binaries and voice models. Place the
Windows Piper runtime in `engines/piper/` and FFmpeg in `ffmpeg/`, or point the
application settings at compatible local installations. Voices can be
installed from **Manage voices**.

Expected Piper files:

```text
engines/piper/
|-- piper.exe
|-- required DLL files
`-- espeak-ng-data/
```

Expected voice pair:

```text
voices/es_ES/davefx/medium/
|-- es_ES-davefx-medium.onnx
`-- es_ES-davefx-medium.onnx.json
```

FFmpeg is resolved from `ffmpeg/ffmpeg.exe` first and then from the Windows
`PATH`. MP3 export requires the `libmp3lame` encoder.

## Configuration

The application creates `config.json` beside the executable or source project.
If the file is missing, safe defaults are created automatically.
`config.example.json` documents the available values, including:

- output folder, voice, language, speed, split mode, and export mode;
- selected voice generation engine: Piper local, Kokoro local, Chatterbox,
  Qwen3 TTS, OpenAI TTS, ElevenLabs, Google Gemini TTS, or Azure Speech;
- optional Kokoro voice settings;
- API provider settings such as API keys, model IDs, voice IDs, Azure region,
  output format, and style parameters;
- Piper and FFmpeg paths;
- chunk size and block/chapter pauses;
- randomized and adaptive paragraph pause rules;
- MP3 bitrate, metadata, and clean-audio normalization;
- voice/music dB levels, intro, background, outro, fades, gaps, ducking,
  visual mix preview, and podcast normalization.

## Build the Portable App

```bat
build_windows.bat
```

The build uses PyInstaller `--onedir` mode and creates:

```text
dist/LocalText2Voice/
|-- LocalText2Voice.exe
|-- engines/
|-- voices/
|-- ffmpeg/
|-- music/
|-- output/
|-- licenses/
|-- runtimes/python311/
|-- config.example.json
|-- LICENSE
`-- THIRD_PARTY_NOTICES.md
```

Piper, voices, FFmpeg, and the embedded Python runtime remain outside the main
executable so they can be updated independently. Do not redistribute a voice
until you have reviewed its model card and dataset license. Third-party
license texts are copied into the portable folder under `licenses/`.

### Embedded Python Runtime

LocalText2Voice includes an embedded Python runtime in the Windows portable
build. It is copied into the distribution under:

```text
dist/LocalText2Voice/runtimes/python311/
```

In development builds, the runtime manager can also install the same private
runtime under `%LOCALAPPDATA%/LocalText2Voice/runtimes/python311/` if a bundled
copy is not present. The installer downloads the official Windows embedded
Python ZIP, enables `site-packages`, bootstraps `pip`, and installs a small
core package set for model download workflows such as Hugging Face Hub access.
This runtime is the foundation for Python-native TTS providers without
requiring users to install Python globally.

### Optional Kokoro Engine

Kokoro is the supported local neural TTS path in LocalText2Voice. It installs
Python dependencies into the embedded runtime:

```text
kokoro-onnx
soundfile
onnxruntime-gpu[cuda,cudnn]   # installed automatically when NVIDIA CUDA is detected
```

Its model files are downloaded independently to:

```text
%LOCALAPPDATA%/LocalText2Voice/models/kokoro/
```

At runtime Kokoro starts in automatic backend mode. If ONNX Runtime exposes
`CUDAExecutionProvider`, the app uses CUDA and writes it in the logs; otherwise
it falls back to CPU without user setup.

### Optional Chatterbox GPU Engine

Chatterbox is treated as an advanced local engine because it depends on
PyTorch and benefits strongly from a CUDA-capable NVIDIA GPU. The main
LocalText2Voice executable does not depend on Chatterbox.

For end users, the Chatterbox **Install** button installs the Python
dependencies into LocalText2Voice's private embedded Python runtime. When an
NVIDIA GPU is detected, the installer first tries the CUDA PyTorch runtime and
falls back to CPU if it cannot be validated. Dependency metadata is stored in:

```text
%LOCALAPPDATA%/LocalText2Voice/runtimes/python311/engine-deps/
```

Model files are downloaded on demand to:

```text
%LOCALAPPDATA%/LocalText2Voice/models/chatterbox/
```

The Chatterbox panel supports:

- Chatterbox Multilingual V3, English, and Turbo model modes;
- CUDA, Auto, CPU fallback, and Apple MPS device choices;
- optional reference audio for voice cloning;
- consent checkbox for reference-voice usage;
- emotion exaggeration and CFG weight controls.

Turbo mode requires a reference audio file. For long-form content, the
existing chunking pipeline is reused, and Chatterbox runs as a persistent
Python worker so the selected model stays loaded while the blocks render.

### Optional Qwen3 TTS Engine

Qwen3 TTS is integrated as an advanced local neural TTS provider. It is not
included in the base portable ZIP. The **Install** button creates an isolated
dependency folder under the embedded Python runtime and downloads the model
cache on demand:

```text
%LOCALAPPDATA%/LocalText2Voice/models/qwen/
%LOCALAPPDATA%/LocalText2Voice/runtimes/python311/engine-deps/qwen/
```

The first supported model is `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`, selected
because it exposes built-in speakers such as Serena, Vivian, Ryan, Eric, and
Dylan without requiring reference audio. The runtime tries CUDA automatically
when an NVIDIA GPU is visible, using `faster-qwen3-tts` with CUDA Graphs for
the fast path, and falls back to CPU when CUDA cannot be used.

For long-form content, LocalText2Voice still splits text into safe blocks.
Qwen3 runs as a persistent JSON-lines worker so the model loads once per
generation job and then receives synthesis requests for each block.

### Optional Google Gemini TTS API Engine

Google Gemini TTS is available as a remote API provider. It does not install
models locally; add a Gemini API key in **Settings > TTS Engines**, select a
Gemini TTS model, choose one of the built-in Gemini voices, and optionally add
a style prompt such as "Say this as a warm podcast narrator."

The first configured models are:

- `gemini-3.1-flash-tts-preview`
- `gemini-2.5-flash-tts`
- `gemini-2.5-flash-lite-preview-tts`
- `gemini-2.5-pro-tts`

LocalText2Voice calls the Gemini Interactions API directly, decodes the
returned audio block, wraps PCM output as WAV, and then reuses the normal
FFmpeg pipeline for MP3 export, podcast mixing, and normalization.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The test suite covers text normalization, safe splitting, chapter detection,
settings, output naming, natural pauses, localization, and voice catalog
behavior. End-to-end synthesis additionally requires real Piper, voice, and
FFmpeg files.

## Roadmap

- [x] Offline Piper TTS generation for long-form text
- [x] Downloadable voice catalog with remote previews
- [x] Clean narration and music-backed podcast exports
- [x] Natural pauses, progress, ETA, cancellation, and multilingual UI
- [x] Optional API engines for OpenAI TTS, ElevenLabs, Gemini TTS, and Azure Speech
- [x] Optional Kokoro local engine with dynamic model installation
- [x] Optional Chatterbox local GPU engine scaffolding and runtime integration
- [x] Bundled Python runtime manager for future Python-native engines
- [x] Persistent Kokoro worker with automatic CPU/CUDA backend selection
- [x] Optional Qwen3 TTS engine with isolated on-demand runtime installation
- [ ] User-configurable custom HTTP/local-server TTS providers
- [ ] Short product video and animated workflow demo
- [ ] Visual chapter editor before synthesis
- [ ] More local engines such as XTTS
- [ ] PDF and richer document import
- [ ] Optional LLM-assisted lesson and course generation
- [ ] Signed Windows installer, automatic updates, and release automation

## Contributing

Issues, feature proposals, translations, and pull requests are welcome. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change. Please never add
large voice models, copyrighted music, API keys, or generated build folders to
the repository.

## License

LocalText2Voice source code is released under the [MIT License](LICENSE).
Piper, FFmpeg, PySide6/Qt, voice models, and other dependencies keep their own
licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before
redistributing a portable build.

## Author

Created by [Esteban](https://andromedanova.com) at
[AndromedaNova.com](https://andromedanova.com).

If LocalText2Voice helps your work, starring the repository makes the project
easier to discover.
