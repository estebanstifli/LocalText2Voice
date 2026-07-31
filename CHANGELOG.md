# Changelog

All notable changes to LocalText2Voice are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

- Qwen now selects a PyTorch 2.11 CUDA 13.0 build for RTX 50-series and other
  Blackwell GPUs instead of installing the incompatible PyTorch 2.6 CUDA 12.6
  wheel.
- A Qwen CUDA profile that cannot be installed or execute kernels now records a
  CPU fallback instead of repeatedly treating the runtime as missing.

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

[1.3.0]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/estebanstifli/LocalText2Voice/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/estebanstifli/LocalText2Voice/releases/tag/v1.2.0
