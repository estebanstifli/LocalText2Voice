# Changelog

All notable changes to LocalText2Voice are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.2] - 2026-09-13

### Fixed

- Fixed Windows startup failure while importing QtGui ([#24](https://github.com/estebanstifli/LocalText2Voice/issues/24)). The Windows build now
  isolates PyInstaller's DLL search paths so unrelated Poppler ICU libraries
  cannot replace the Windows ICU library used by Qt.
- Added a packaging validation step that rejects bundles containing the
  conflicting ICU DLLs before an installer is created.
- Upgrades remove the incompatible `icuuc.dll` and `icudt78.dll` files that were
  accidentally shipped in v2.0.1, while preserving user configuration,
  projects, models and generated data.

## [2.0.1] - 2026-09-13

### Added

- Added DRM-free EPUB 2/3 import in reading order, with navigation-based
  chapter headings, original book metadata, a project-owned cover and M4B
  export options. The implementation uses Python's standard ZIP/XML/HTML
  readers and adapts the contribution proposed by
  [Tomas-Falcon in PR #23](https://github.com/estebanstifli/LocalText2Voice/pull/23).
- Added Video Storyboard as a beta workflow for turning audiobook narration
  into editable scenes, images, video clips and a final MP4.
- Added conversational narration analysis, character and location discovery,
  appearance states, continuity review, scene alignment and long-scene
  subdivision.
- Added 50 bundled visual style samples, custom styles, image generation and
  import, image editing, reference images, camera controls and ComfyUI/Runpod
  video workflows.
- Added storyboard project persistence, atomic media references, temporary
  storage controls, installation identity and optional S3/R2-compatible storage.
- Added LiteLLM-compatible text analysis, Pillow image handling and new
  Runpod image/video model configuration.

### Changed

- Video Storyboard is explicitly marked beta. AI continuity, scene timing and
  generated media still require human review.
- Review segment regeneration now uses the persistent Engine Host to reuse the
  loaded TTS engine instead of creating a second local engine process.
- Voice Library selection now applies the stored Qwen voice audio, transcript
  and language consistently.
- Project and storyboard paths are stored atomically and normalized so projects
  can be moved between folders more reliably.

### Fixed

- Fixed [#20](https://github.com/estebanstifli/LocalText2Voice/issues/20), where
  selecting a Qwen voice from Voice Library did not always change the active
  audio, transcript and language.
- Fixed [#21](https://github.com/estebanstifli/LocalText2Voice/issues/21), where
  Review segment regeneration could remain at 0% because it launched a second
  local TTS path instead of using the persistent Engine Host.
- Preserved inline word spacing and text in lists, tables and generic blocks
  during EPUB import, and prevented imported metadata leaking into later books.

## [1.5.1] - 2026-08-21

### Added

- Added Hindi and free-form language selection for OmniVoice. Users can now
  enter any language name or ISO code supported by the engine.
- Added the `To the gates of Eleusis` background track by SamuelFJohanns, with
  its Pixabay source and license documented in the third-party notices.

### Changed

- Final Audio Mix filenames now use the current project title and the `_mix`
  suffix, with safe numbering when a file already exists.
- Voice lookup now gives exact IDs and display names priority over generic
  descriptions, styles, and tags before applying fuzzy matching.
- Added the OmniVoice language-field guidance to all eleven supported UI
  languages.

### Fixed

- Fixed non-looping background tracks that were longer than the narration
  extending the final mix with unwanted music-only audio.
- Fixed preview duration calculations for long non-looping music so the
  timeline ends with the intended narration and configured music tail.
- Fixed OmniVoice language markup rejecting supported languages outside the
  small built-in alias list.

## [1.5.0] - 2026-08-16

### Added

- Added M4B, M4A, Opus, FLAC, and OGG export alongside MP3, with compact,
  standard, and high-quality presets appropriate to each format.
- Added M4B book properties, embedded cover art, and named chapters generated
  from LTV chapter markers or detected text headings.
- Added automatic project covers, normalized custom cover images, persistent
  inline project names, and metadata updates without rerunning TTS.
- Added the Bulk Audiobooks workspace for importing multiple TXT books,
  creating independent editable projects, and rendering them sequentially
  with pause, resume, cancel, retry, recovery, and review metrics.
- Added per-book cover and music selection to bulk tasks, plus the option to
  create projects without immediately generating audio.
- Added the `Universo` background track by Da-Roz with its Pixabay source and
  license documented in the third-party notices.

### Changed

- Generation, review rebuilds, subtitles, Audio Mix, HTTP, MCP, and persisted
  project/job paths now follow the selected audio format and MIME type.
  Deprecated `clean_mp3` and `mix_mp3` API aliases remain available.
- Export now produces one finished audio file; use named M4B chapters when a
  navigable chapter structure is required.
- Qwen CustomVoice 0.6B and Base 1.7B now appear as separate engine rows. The
  Base model can reuse the default Harold cloning reference from Voice Gallery.
- Qwen model loading now materializes complete Hugging Face snapshots and
  keeps synthesis workers offline after installation.
- Added and updated all release UI strings in Arabic, Chinese, English,
  French, German, Hindi, Italian, Japanese, Portuguese, Russian, and Spanish.

### Fixed

- Fixed Qwen installations that appeared complete but were missing nested
  tokenizer configuration or preprocessing files required at synthesis time.
- Fixed the TTS engine table repeatedly probing hardware and model state while
  changing Qwen models, which could stall the interface and UI tests.
- Preserved legacy MP3 settings, project manifests, and server-job records
  while migrating them to generic audio-format fields.

## [1.4.4] - 2026-08-11

### Added

- Replaced the Voices-page reference import action with a Clone Voice dialog
  supporting common uploaded audio formats, selectable microphones, and
  selectable system-audio loopback sources.
- Added optional local Faster Whisper transcription for new reference samples,
  plus manual transcript entry and clear 12-second recording guidance.
- Added live recording levels, sample playback, and 3-20 second validation to
  the voice-cloning workflow.

### Changed

- Voice cloning now accepts WAV, MP3, FLAC, M4A, OGG, OPUS, AAC, and WEBM
  sources before normalizing them to the engine-compatible WAV format.
- Added all voice-cloning labels, guidance, progress states, and validation
  messages to the eleven supported UI languages.
- Added SoundCard and NumPy to the Windows application build for local
  microphone and WASAPI loopback capture.

## [1.4.3] - 2026-08-08

### Added

- Added an automatic, cancellable recovery flow for AI assets created in the
  legacy nested `data/data` location. Files are copied non-destructively and
  the original directory is retained as a backup.
- Added visible modal progress and distinct loading/unloading states while a
  local TTS engine is moved into or out of shared memory.

### Changed

- Optional local engines can only become active after their installation is
  complete. A successful install now refreshes its manager and selects the new
  engine automatically.
- Added localized engine-memory, installation, and legacy-migration guidance
  to all eleven UI languages.

### Fixed

- Voice Gallery database entries and selected reference-audio paths are now
  relocated when legacy assets are recovered, preserving imported voices and
  previews.
- Saved selections that point to an unavailable local engine now fall back to
  the first installed engine instead of leaving the application in an invalid
  state.
- Engine-memory controls now update the sidebar and table consistently during
  both load and unload operations.

## [1.4.2] - 2026-08-08

### Changed

- Voice Library actions and the Generation voice selector now expose only
  voices whose local engine is installed and ready. Catalog entries remain
  visible for discovery but cannot be selected or tested prematurely.
- TTS engine changes are persisted immediately. Choosing an engine other than
  OmniVoice now completes and clears the optional pending OmniVoice first-run
  bundle created by the GPU installer profile.
- Markup toolbar buttons and syntax highlighting now use theme-aware colors,
  backgrounds, hover states, and error underlines in dark mode.
- Added localized engine-readiness guidance to all eleven UI languages.

### Fixed

- Chatterbox no longer trusts an installed manifest when `torch`, `torchaudio`,
  or the `chatterbox` package is missing after an interrupted dependency
  installation; the runtime is reported as incomplete and can be repaired.
- Legacy storage settings that already point to the managed `data` directory
  no longer create a nested `data/data` tree.
- Existing model and voice paths containing the old duplicated `data/data`
  segment are resolved against the original managed asset directory.

## [1.4.1] - 2026-08-07

### Added

- Added an optional shared Silero Stress installation under Text Normalization,
  with asynchronous installation, removal, preview processing, and managed
  model storage independent from the F5-TTS runtime.
- Added pause, stop, and restart controls for Voice Library sample playback.
- Added in-app links to curated external voice-library resources.
- Added persistent engine-host stderr logging to simplify startup diagnostics.

### Changed

- Russian Silero processing now runs after dictionaries and deterministic
  rules for any TTS engine. F5-TTS Russian receives explicit `+` stress marks,
  while other engines receive pronunciation-safe `Ё` spelling.
- Safe chunking now uses explicit per-engine defaults: 300 characters for
  Piper, Kokoro, Chatterbox, OmniVoice, and F5-TTS Russian, and 520 for Qwen.
  Short explicit requests remain valid from 50 characters upward.
- The persistent engine host now uses a dedicated loopback-only port, separate
  from the future remote MCP transport port.
- User-imported reference voices can now be edited, replaced with a normalized
  WAV, or permanently deleted; synchronized catalog voices remain read-only.
- Voice Gallery synchronization now removes stale remote entries while
  preserving locally imported references.
- Fallback Python runtimes are stored with the other managed AI assets so the
  selected large-asset location remains authoritative.

### Fixed

- Whisper review now compares normalized text without overwriting the raw
  transcript, and strips Russian stress marks only for segments that were
  actually preprocessed for F5-TTS Russian.
- Tail review now handles missing alignment, stale metrics, retranscription
  after automatic trimming, and review status selection more consistently.
- Cancelled persistent engines are discarded before the next generation, and
  repeated cancellation or shutdown requests no longer race the engine host.
- Pending OmniVoice setup no longer overrides a different engine selected by
  the user.
- New Windows installations and the example configuration now start directly
  on settings schema 20 with safe per-engine chunk limits.

## [1.4.0] - 2026-08-01

### Added

- Added the optional F5-TTS Russian engine using the pinned
  `F5TTS_v1_Base_v2` safetensors checkpoint, persistent local inference,
  reference-audio cloning, selected-GPU support, previews, server generation,
  and voice-gallery imports.
- Added Russian deterministic text normalization for numbers, ordinals,
  percentages, ruble amounts, common abbreviations, and units.
- Added Silero Stress as an F5-only isolated dependency with lazy loading,
  automatic Russian stress marks, and preservation of manual `+` marks.
- Added mandatory CC BY-NC 4.0 acknowledgement and non-commercial notices for
  F5-TTS Russian in its installation dialog, engine UI, README, and
  third-party notices.
- Added a global NVIDIA GPU selector under General settings, with an automatic
  mode and a sidebar shortcut when multiple CUDA GPUs are available.
- Added a persistent light/dark interface theme with a localized sun/moon
  toggle, accessible tooltips, and an automatic progress dialog while the
  interface switches themes.
- Added the Qwen3 TTS Base 1.7B checkpoint with full ICL voice cloning from
  reference audio and an exact transcript, alongside the existing CustomVoice
  0.6B checkpoint.
- Added model-specific Qwen controls, cache detection, validation, preview, and
  local-server configuration for reference audio and transcripts.
- Added reusable, hardware-aware PyTorch runtime profiles for optional engines.
- Added separate Qwen CPU, CUDA 12.6 legacy, and CUDA 13.0 Blackwell profiles,
  with versioned dependency directories and profile metadata in install
  manifests.
- Added real CUDA kernel execution checks during Qwen runtime validation.
- Added a developer validation tool that can install profiles, run CUDA probes,
  load the cached Qwen model, and generate a test WAV for every profile.

### Changed

- F5-TTS Russian installations now configure the Voice Gallery `Russian Man`
  reference audio and its exact Russian transcript by default.
- Official F5-TTS long-silence trimming is now enabled by default, including
  its documented handling of long internal pauses, and remains user-configurable.
- F5-TTS Russian now shares the remote `Russian Man` and `Russian Woman`
  reference voices with OmniVoice, including automatic download and selection.
- Theme switching now restyles the existing interface and recolors icons in
  place instead of rebuilding every page and reloading application state.
- CUDA-based TTS engines and Faster Whisper now run on the selected GPU, and
  loaded models are released safely when the selection changes.
- Renamed the existing Qwen model in the UI to `CustomVoice 0.6B (Fast)` and
  documented the experimental Base 1.7B emotion/style instruction path.
- Qwen runtime upgrades are staged and validated before a profile is activated,
  preserving other engine environments and previously installed profiles.
- Qwen installs each pinned PyTorch profile and its engine dependencies in one
  resolver pass, avoiding repeated multi-gigabyte PyTorch installations.
- Existing working Qwen CUDA 12.6 installations migrate into the legacy profile
  without another package download, and cached profiles can be reactivated
  after a hardware change.

### Fixed

- Selecting a TTS engine now keeps its row selected and visible, while routine
  status refreshes preserve the engine table's current scroll position.
- Qwen now selects a PyTorch 2.11 CUDA 13.0 build for RTX 50-series and other
  Blackwell GPUs instead of installing the incompatible PyTorch 2.6 CUDA 12.6
  wheel.
- A Qwen CUDA profile that cannot be installed or execute kernels now records a
  CPU fallback instead of repeatedly treating the runtime as missing.
- New Windows installations now write the current settings schema instead of
  relying on a first-launch migration.

## [1.3.0] - 2026-07-24

### Added

- Added a supported Linux-from-source workflow with a Bash launcher, Wayland
  detection, setup documentation, and platform-focused tests.
- Added configurable storage for models, isolated engine dependencies,
  voice-gallery data, and download caches.
- Added safe in-app migration of managed AI assets between drives.
- Added a Windows installer page for selecting the large-model storage
  location during a fresh installation.
- Added detailed live output for optional-engine package installation and
  downloads.

### Changed

- Piper and FFmpeg executable discovery now accepts extensionless Linux/macOS
  binaries and system `PATH` installations.
- Non-Windows optional-engine runtimes now use a virtual environment created
  from the system Python installation.
- Model storage uses a marked `data` tree, while older configurations retain
  their historical locations until explicitly migrated.
- Windows uninstall cleanup recognizes the selected model location and only
  removes it when the LocalText2Voice ownership marker is present.

### Fixed

- Generated audio can now move from a temporary filesystem to a different
  output filesystem without failing with `EXDEV`.
- Piper status, preview, regeneration, verification, and server generation now
  resolve the executable consistently across platforms.
- Non-`EXDEV` file errors are no longer hidden by the cross-filesystem fallback.
- Linux runtime tests now use the platform-specific runtime version and layout.

## [1.2.1] - 2026-07-21

### Added

- Added a complete Russian desktop translation, expanding the interface to eleven languages.
- Added Russian to the Faster Whisper language selector and to the Windows installer language choices.
- Added five project-source tools to both MCP transports and the local HTTP API:
  `read_job_source`, `write_job_source`, `search_job_source`,
  `edit_job_source`, and `replace_job_source_text`.
- Added paginated source reads, literal or regular-expression searches, Unicode
  character offsets, and SHA-256 optimistic concurrency checks for agent edits.
- Added synchronized source updates across SQLite, `source.txt`, and the project
  manifest. Edited projects are marked for a new render while existing audio is preserved.
- Added physical Hugging Face cache discovery for Chatterbox, Qwen3 TTS,
  OmniVoice, and Faster Whisper, plus direct asset validation for Kokoro.
- Added repair/update actions that reuse existing model assets and download only
  missing or outdated engine files.

### Changed

- Engine status now distinguishes a missing runtime from a missing model and
  offers install, repair/update, or reinstall/update as appropriate.
- The normalized-text editor tab is inserted only when text normalization is
  enabled and is restored reliably after UI or project refreshes.
- The Generation Review table now reserves a stable fifteen-row working area.
- MCP and HTTP documentation now explains large-source pagination, result
  pagination, editable project behavior, and concurrency-safe mutations.

### Fixed

- Faster Whisper no longer aborts verification when CUDA was selected but its
  required CUDA libraries cannot be loaded or the selected backend cannot run
  `float16` efficiently. It logs the problem, restarts on CPU `int8`, and
  reuses that fallback for subsequent segments. CPU `float16` selections are
  normalized to `int8` before the worker starts.
- Fixed local engine installations being reported as missing solely because an
  installation manifest was stale or absent after a rebuild or interrupted setup.
- Fixed `auto` being treated as a real text-normalization dictionary language;
  the selected voice language is now used as the normalization hint when available.
- Fixed the normalized-text tab remaining hidden after normalization was re-enabled.

## [1.2.0] - 2026-07-18

### Added

- Added automatic SRT and karaoke-style ASS subtitles from Faster Whisper word timestamps.
- Added multilingual text normalization with editable SQLite dictionaries and
  rules for structured values such as dates, currencies, measurements, and ordinals.
- Added audio-tail artifact detection, manual trimming, and conservative automatic cleanup.

### Changed

- Made OmniVoice installation reproducible with pinned PyTorch resolution and
  more resilient Windows cleanup.
- Improved uninstall behavior so downloaded AI assets can be removed while
  projects, exports, settings, music, and logs are preserved.

[Unreleased]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.4.4...v1.5.0
[1.4.4]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.4.3...v1.4.4
[1.4.3]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.4.2...v1.4.3
[1.4.2]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/estebanstifli/LocalText2Voice/releases/tag/v1.2.0
