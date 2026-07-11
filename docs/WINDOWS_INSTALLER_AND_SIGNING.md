# Windows installer and future code signing

This document records the current LocalText2Voice Windows installer setup so it can be resumed later when code signing is available.

## Current status

- Installer technology: Inno Setup 6.
- Local tooling folder: `.util_instalador_y_firmas/`.
- This folder is intentionally ignored by Git.
- Inno Setup was installed locally into:
  - `.util_instalador_y_firmas/InnoSetup/`
- Installer script:
  - `.util_instalador_y_firmas/installer/LocalText2Voice.iss`
- Installer output:
  - `.util_instalador_y_firmas/output/LocalText2Voice-Setup-1.0.0.exe`

The installer is not signed yet.

## Why the installer folder is ignored

The installer tooling is local build infrastructure, not application source code. It may later contain certificate tooling, signing configuration, temporary build artifacts, and private signing experiments. It should not be committed.

The app source, installer behavior, and first-run setup hooks are committed in the normal repository files.

## Installer behavior

The installer installs LocalText2Voice per-user into:

```text
%LOCALAPPDATA%\Programs\LocalText2Voice
```

It does not require administrator privileges.

It currently offers two installation profiles:

- CPU light: installs the bundled portable distribution and defaults to Piper.
- Powerful GPU: installs the bundled portable distribution, selects OmniVoice, enables Review, and marks OmniVoice + Faster Whisper for first-run installation.

The installer writes `config.json` only when it does not already exist. This avoids overwriting user settings during reinstall or upgrade.

## First-run GPU setup flow

When the GPU profile is selected, the installer writes:

```json
{
  "tts_engine": "omnivoice",
  "review": {
    "enabled": true,
    "auto_verify_after_generation": true
  },
  "installer_setup": {
    "profile": "gpu",
    "pending_installs": ["omnivoice", "faster_whisper"],
    "completed": false
  }
}
```

On first launch, `MainWindow._run_pending_installer_setup()` reads this state and starts the app-managed install flow:

1. Open Settings.
2. Select the TTS Engines tab.
3. Install OmniVoice using the existing progress/cancel worker.
4. Install Faster Whisper using the existing progress/cancel worker.
5. Mark `installer_setup.completed = true`.

This keeps all model downloads, validation, progress, errors, and cancellation inside the normal application UI instead of duplicating that logic in Inno Setup.

## Files included in the installer

The installer packages the current `dist/LocalText2Voice/` folder, with exclusions for runtime engine dependency caches:

```text
runtimes/python311/engine-deps/*
output/*
logs/*
__pycache__/*
*.pyc
```

This keeps the base installer from accidentally bundling locally installed optional engines such as OmniVoice, Qwen, Chatterbox, or Faster Whisper dependency folders created during development.

The base installer still includes the application, Piper runtime, FFmpeg, bundled music, voices present in the portable dist, licenses, docs, and the embedded Python runtime itself.

## Rebuild steps

From the repository root:

```powershell
cmd /c build_windows.bat
& .\.util_instalador_y_firmas\InnoSetup\ISCC.exe .\.util_instalador_y_firmas\installer\LocalText2Voice.iss
```

The generated installer is:

```text
.util_instalador_y_firmas/output/LocalText2Voice-Setup-1.0.0.exe
```

## Validation performed

The installer was tested in silent mode:

CPU profile:

```powershell
LocalText2Voice-Setup-1.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TYPE=cpu /DIR="<temp>"
```

Expected config:

```json
"tts_engine": "piper",
"installer_setup": {
  "profile": "cpu",
  "pending_installs": [],
  "completed": true
}
```

GPU profile:

```powershell
LocalText2Voice-Setup-1.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TYPE=gpu /DIR="<temp>"
```

Expected config:

```json
"tts_engine": "omnivoice",
"review": {
  "enabled": true,
  "auto_verify_after_generation": true
},
"installer_setup": {
  "profile": "gpu",
  "pending_installs": ["omnivoice", "faster_whisper"],
  "completed": false
}
```

Spanish language mapping:

```powershell
LocalText2Voice-Setup-1.0.0.exe /VERYSILENT /LANG=spanish /TYPE=cpu /DIR="<temp>"
```

Expected config:

```json
"ui_language": "es"
```

## Code signing plan

The installer is the primary artifact for Windows distribution.

Recommended signing order when a certificate is available:

1. Sign `dist/LocalText2Voice/LocalText2Voice.exe`.
2. Build the Inno Setup installer.
3. Sign `LocalText2Voice-Setup-<version>.exe`.

Signing only the installer is workable for early distribution, but signing both is more professional because the installed EXE still has publisher identity if Windows Defender, SmartScreen, or antivirus inspects it directly.

For open source signing, SignPath Foundation is the preferred future path:

- Public GitHub repository.
- OSI-approved license.
- GitHub Actions build.
- Release artifacts produced by CI.
- SignPath signing policy.
- Manual approval of signed releases.

Until the certificate/signing flow is ready, distribute the unsigned installer through GitHub Releases and clearly label it as unsigned.

