# LocalText2Voice Architecture And Addons

This document is a technical map for future LocalText2Voice development. It
exists so optional modules can be added without mixing model code, UI code,
project storage, and audio postproduction into one large block.

## Current Product Shape

LocalText2Voice is becoming a modular AI voice and audio production desktop app.
The current stable workflow is:

1. Import or paste source text.
2. Process text into chapters, paragraphs, and safe TTS chunks.
3. Apply optional LTV Markup commands.
4. Generate segment WAV files with the selected TTS engine.
5. Store project and segment state in SQLite.
6. Optionally verify segments with Faster Whisper.
7. Rebuild clean narration from approved segment audio.
8. Mix narration with music in Audio Mix.
9. Export final MP3 files.

## Existing Extension Points

### TTS Engines

TTS engines implement `BaseTTSEngine`:

```python
class BaseTTSEngine:
    def validate(self, voice_config: dict) -> None: ...
    def synthesize_to_wav(self, text: str, output_wav: Path, voice_config: dict) -> Path: ...
    def cancel_current(self) -> None: ...
```

Current engine families:

- Built-in local engines: Piper, Kokoro, Chatterbox, Qwen3 TTS, OmniVoice.
- Built-in cloud/API engines: OpenAI, ElevenLabs, Gemini, Azure.
- User-defined HTTP engines: configurable endpoint, headers, body template, and response format.

The UI should not call model libraries directly. New engines should be exposed
through the registry and consume `voice_config`.

### Persistent Workers

Heavy engines should support persistent workers when possible. The worker loads
the model once, accepts segment requests, and shuts down when unloaded or when
the app closes. This avoids reloading large models for every chunk.

Current examples:

- Kokoro worker.
- Chatterbox worker.
- Qwen3 worker.
- OmniVoice worker.
- Faster Whisper verifier worker.

### Project Storage

SQLite stores audiobook/project metadata and generated segments. Segment data
is the foundation for review, retries, regeneration, rebuilds, subtitles, and
future timeline features.

Segment records should keep:

- Source text.
- Generated WAV path.
- Voice/language/config used.
- Transcript and similarity metrics.
- Word timestamps from Whisper.
- Review status.
- Dirty/rebuild state.

### Markup

LTV Markup is app-level control syntax. App-level commands should be processed
before sending text to a model. Model-specific commands should only be passed to
engines that explicitly support them.

Useful future commands:

- `{{play "door-slam.mp3" track=sfx}}`
- `{{play "soft-piano.mp3" track=music loop=true duration=30}}`
- `{{image "wide shot of a classroom"}}`
- `{{scene "forest at night"}}`
- `{{subtitle.on}}`

## Proposed Addon System

Future optional modules should be installable on demand. A simple addon manifest
can describe capabilities without hardcoding everything in the main UI.

Example concept:

```json
{
  "id": "localtext2voice.fastapi_server",
  "name": "Local Web Server",
  "type": "server",
  "version": "0.1.0",
  "entrypoint": "addons/fastapi_server/run.py",
  "requires_python_runtime": true,
  "capabilities": ["http_api", "project_control"],
  "install": {
    "packages": ["fastapi", "uvicorn"],
    "models": []
  }
}
```

Recommended addon categories:

- `server`: local FastAPI/web UI, REST API, batch automation.
- `mcp`: MCP server and tools for external AI agents.
- `sfx`: sound effect generation or sound library search.
- `music`: local/remote music generation or curated music libraries.
- `image`: scene image generation or stock media search.
- `video`: subtitles, covers, waveform videos, visual audiobooks.
- `llm`: course/script creation, chapter rewriting, summarization.

## Future Modules

### Local Web Server With FastAPI

Goal: run LocalText2Voice as a local backend and expose a browser UI or REST API.

Pros:

- Easier cross-platform UI experiments.
- Easier remote control from scripts.
- Can share the same project database and engine managers.

Risks:

- Security surface: local server must bind to localhost by default.
- File access needs clear permissions.
- Long-running jobs need job IDs, progress events, and cancellation endpoints.

### MCP Server

Goal: expose LocalText2Voice capabilities as tools for AI agents.

Possible tools:

- `create_audiobook_project`
- `generate_segments`
- `verify_segments`
- `regenerate_segment`
- `render_audio_mix`
- `export_project`

Risks:

- Tools must never expose API keys or arbitrary filesystem access.
- Project operations need explicit paths and validation.

### SFX Layer

Goal: add sound effects from either:

- Local libraries.
- Remote stock APIs.
- AI generation providers.

SFX should be timeline-based and non-destructive. Segment audio should not be
overwritten; mixes should be rendered as new outputs.

### Music Generation Layer

Goal: generate or retrieve intro/background/outro music.

Important design choice: generated music should enter the existing Music Library
instead of bypassing it. That keeps Audio Mix simple.

### Image/Scene Layer

Goal: create or fetch images for chapters/scenes, later useful for video,
YouTube covers, visual audiobooks, or chapter cards.

Providers may be:

- Local image models.
- Remote image APIs.
- Stock APIs such as Pixabay or Pexels.

The project database should store media references separately from narration
segments, because images can be scene/chapter-level rather than voice-level.

## Design Rules For Addons

- The base app must remain useful without any addon installed.
- Heavy dependencies must be optional and installed on demand.
- Addons should write to local app data or project folders, not source folders.
- Addons should declare whether they are free, paid, local, remote, or hybrid.
- Long-running addon work must expose progress and cancellation.
- Generated artifacts should be non-destructive.
- API keys must stay in local config and never be committed.
- Model and provider licenses must be visible before distribution.

## Near-Term Recommendation

Keep the main app focused on:

- TTS engines.
- Segment database.
- Review/verification.
- Audio Mix.
- Project import/export.

Build optional systems as installable modules that integrate through:

- `BaseTTSEngine` for voice generation.
- A future `BaseAddon` manifest for non-TTS modules.
- SQLite project records for durable state.
- Worker threads/processes for progress and cancellation.
