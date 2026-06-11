# CourseToPodcast

CourseToPodcast is a lightweight Windows desktop application that converts long
texts into one or more MP3 files using local Piper TTS and FFmpeg. Synthesis is
offline, runs without a GPU, and keeps Piper voices outside the application
executable.

## Current features

- Runtime-switchable PySide6 interface in 10 languages, with UI strings stored
  in auto-discovered JSON locale files.
- Paste text or import UTF-8/Windows text, Markdown, and DOCX files.
- Recursive discovery of Piper `.onnx` voices with matching `.onnx.json` files.
- Built-in manager for browsing, installing, and removing public Piper voices.
- Streaming voice samples before a model is downloaded.
- Language filtering and voice-speed control.
- Safe chunk splitting or chapter/heading detection.
- Detection of Markdown headings, English/Spanish chapter labels, and short
  uppercase headings.
- One `course_full.mp3` file or numbered `chapter_001.mp3` files.
- Configurable pauses, MP3 bitrate, ID3 metadata, and optional loudness
  normalization through `config.json`.
- Randomized paragraph pauses configurable directly from the interface.
- Natural timing rules based on paragraph length and periodic reading breaks.
- Optional podcast output with intro, looped background music, outro, fades,
  section gaps, -16 LUFS normalization, and basic sidechain ducking.
- Background generation with live progress, visible logs, and cancellation.
- Portable PyInstaller folder build; models and runtimes are not embedded.

## Project layout

```text
course_to_podcast/
|-- main.py
|-- requirements.txt
|-- config.example.json
|-- build_windows.bat
|-- app/
|   |-- core/
|   |-- llm/
|   |-- tts/
|   |-- ui/
|   |-- utils/
|   `-- workers/
|-- engines/piper/
|-- ffmpeg/
|-- locales/
|-- music/
|-- output/
|-- tests/
`-- voices/
```

## 1. Install Python dependencies

Python 3.10 or newer is recommended. From the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`python-docx` is only used for DOCX imports. TXT and Markdown imports do not
need it.

## 2. Piper runtime

This distribution already includes the official Windows x64 Piper runtime
`2023.11.14-2` under:

```text
engines/piper/
|-- piper.exe
|-- required DLL files
|-- espeak-ng-data/
`-- pkgconfig/
```

CourseToPodcast calls this executable directly with `subprocess`; Piper does
not need to be globally installed. Runtime source and checksum details are in
`engines/piper/PIPER_RUNTIME_INFO.txt`.

## 3. Add voices

Use **Manage voices** in the application to browse the public
`rhasspy/piper-voices` catalog. It supports search, language and quality
filters, displays download sizes, and installs the selected `.onnx` and
`.onnx.json` files.

Select a voice and use **Preview** to stream its `samples/speaker_0.mp3`
recording before downloading the model.

Downloads run in the background and can be cancelled. Files are first written
under `voices/.downloads`, checked for the expected size and model SHA-256,
and only then moved into the active voice directory. Installed voices can be
removed from the same dialog.

Voice models may have different licenses or dataset conditions. Use **Open
repository** to review their source information before redistribution.

Manual installation remains supported. Copy each Piper model and its JSON
configuration under `voices/`. Both files must share the complete model
filename:

```text
voices/
|-- es_ES/
|   |-- model.onnx
|   `-- model.onnx.json
`-- en_US/
    |-- model.onnx
    `-- model.onnx.json
```

Subfolders are scanned recursively. Use **Refresh** after adding a voice while
the app is open.

## 4. Add FFmpeg

The preferred portable location is:

```text
ffmpeg/ffmpeg.exe
```

If that file does not exist, the app tries `ffmpeg` from the Windows `PATH`.
MP3 export requires an FFmpeg build with the `libmp3lame` encoder.

## 5. Run

```powershell
python main.py
```

Paste or import a `.txt`, `.md`, or `.docx` file with the single import
button, then select a discovered voice and click **Generate Audio**. The
default result is `output/course_full.mp3`.

The interface language selector and **Settings** button are in the top-right
header. The main generation view only contains the source editor, voice
language, voice selection, progress, and log. Output, narration, and podcast
options live under **Settings > General**; natural pauses and detailed podcast
controls live under **Settings > Advanced**.

The interface is available in English, Spanish, French, German, Italian,
Portuguese, Simplified Chinese, Japanese, Arabic, and Hindi. Arabic
automatically switches the application to a right-to-left layout. Additional
languages can be added by placing another complete JSON file in `locales/`;
the selector discovers it automatically through its `language_name` and
`language_direction` entries.

## Configuration

The app creates `config.json` beside the executable/source project after the
first run. Missing keys automatically receive safe defaults. To preconfigure a
distribution, copy `config.example.json` to `config.json`.

Important settings:

| Key | Purpose |
| --- | --- |
| `piper_path` | Portable or custom Piper executable |
| `ffmpeg_path` | Portable or custom FFmpeg executable |
| `chunk_size` | Maximum characters sent to Piper per block |
| `pause_between_blocks_ms` | Silence between safe chunks |
| `pause_between_chapters_ms` | Silence between chapter groups |
| `paragraph_pause_min_ms` | Minimum randomized paragraph silence |
| `paragraph_pause_max_ms` | Maximum randomized paragraph silence |
| `adaptive_paragraph_pause` | Enable length and reading-rhythm adjustments |
| `paragraph_length_extra_ms` | Maximum extra pause after long paragraphs |
| `periodic_pause_every_paragraphs` | Add a breathing pause every N paragraphs |
| `normalize_audio` | Apply FFmpeg `loudnorm` during MP3 encoding |
| `podcast_enabled` | Create a second podcast mix while keeping clean narration |
| `background_volume_percent` | Background music level; defaults to 12% |
| `podcast_normalize` | Normalize the podcast mix to -16 LUFS |
| `podcast_ducking` | Lower music while narration is active |
| `mp3_bitrate` | MP3 bitrate such as `128k` or `192k` |
| `metadata` | Default title, artist, and album tags |

Voice speed is translated to Piper's `length_scale`: a higher UI value speaks
faster and a lower value speaks slower.

### Paragraph pauses

The **Paragraph pause** controls natural silence between source paragraphs.
The default chooses a new random duration from `0.45` to `0.90` seconds at
each boundary. Set both values to the same number for a fixed pause.

Technical splits inside a very long paragraph still use
`pause_between_blocks_ms`. Chapter boundaries use
`pause_between_chapters_ms` and take priority over paragraph pauses.

**Settings > Advanced** can add extra silence after long paragraphs and a
small breathing pause every configurable number of paragraphs. These
adjustments are added to the randomized base pause.

## Podcast mix

Enable **Create podcast mix** to retain the clean narration and create a
second produced file:

```text
course_full.mp3
course_podcast.mp3
```

Chapter export similarly creates `chapter_001.mp3` and
`chapter_001_podcast.mp3`.

**Settings > General** selects the background track and enables the mix.
**Settings > Advanced** configures intro/outro files, background looping and
volume, fade in/out, silence between sections, -16 LUFS normalization, and
ducking. MP3 and WAV music files are supported.

Basic ducking uses FFmpeg's `sidechaincompress` filter so background music is
reduced while the narration is speaking. Podcast normalization is enabled by
default; clean-narration normalization remains independently configurable.

Music is not bundled with CourseToPodcast. Only use tracks whose licenses
permit the intended publication and redistribution.

For a portable personal library, place tracks under `music/intro`,
`music/background`, and `music/outro`. The file selectors can also use music
from any other location.

## Chapter detection

The chapter mode recognizes:

- `Chapter 1`, `Lesson 1`, and `Module 1`
- `Capítulo 1`, `Lección 1`, and `Módulo 1`
- Markdown headings such as `## Lesson title`
- Short uppercase lines such as `GETTING STARTED`

Long chapters are still divided into safe TTS blocks before synthesis.

## Build a portable Windows folder

Run:

```bat
build_windows.bat
```

The script creates a virtual environment, installs build dependencies, runs
PyInstaller in `--onedir` mode, and copies the external runtime folders. The
result is:

```text
dist/CourseToPodcast/
|-- CourseToPodcast.exe
|-- engines/
|-- voices/
|-- ffmpeg/
|-- output/
`-- config.example.json
```

Piper, downloaded voices, and FFmpeg remain outside `CourseToPodcast.exe`,
which keeps updates and voice changes manageable.

## Tests

The text processing tests use only the Python standard library:

```powershell
python -m unittest discover -s tests -v
```

An end-to-end audio test requires real Piper, voice, and FFmpeg files.

## Extending the app

New TTS engines should implement `BaseTTSEngine` in `app/tts/base.py`. The
pipeline only depends on this interface, so cloud or local engines can be
added without rewriting the UI flow.

Future AI course-writing providers can implement `BaseLLMProvider` in
`app/llm/base.py`. No network provider is enabled in this first version.
