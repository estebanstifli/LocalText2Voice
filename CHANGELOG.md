# Changelog

All notable changes to LocalText2Voice are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.4.1...HEAD
[1.4.1]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/estebanstifli/LocalText2Voice/releases/tag/v1.2.0
