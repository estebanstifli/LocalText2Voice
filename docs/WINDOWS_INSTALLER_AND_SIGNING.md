# Windows installer and future code signing

This document records the current LocalText2Voice Windows installer setup so it can be resumed later when code signing is available.

## Current status

- Installer technology: Inno Setup 6.
- Local tooling folder: `.util_instalador_y_firmas/`.
- This folder is intentionally ignored by Git.
- Inno Setup was installed locally into:
  - `.util_instalador_y_firmas/InnoSetup/`
- Tracked installer script:
  - `installer/LocalText2Voice.iss`
- Release outputs:
  - `.util_instalador_y_firmas/output/LocalText2Voice-Setup.exe`
  - `.util_instalador_y_firmas/output/LocalText2Voice-Setup.exe.sha256`

The installer is not signed yet.

## Why the local tooling folder is ignored

The Inno Setup installation and signing tooling are local build infrastructure. They may later contain certificate tooling, signing configuration, temporary build artifacts, and private signing experiments. They should not be committed.

The installer definition is now tracked in `installer/`, while the compiler remains local in `.util_instalador_y_firmas/`.

## Installer behavior

The installer installs LocalText2Voice per-user into:

```text
%LOCALAPPDATA%\Programs\LocalText2Voice
```

It does not require administrator privileges.

The next installer page selects the base folder for large downloadable AI
assets. LocalText2Voice creates a managed `data` child below it. For example:

```text
D:\LocalText2Voice\data\
|-- models\
|-- engine-deps\
|-- voice-gallery\
`-- downloads\
```

The default base is the application installation folder. The same location can
later be changed from **Settings > General > AI model storage**. The app copies
and verifies the new tree before changing `config.json`, then removes only the
known source directories.

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

## Uninstall behavior

Fresh installations store downloaded TTS models, Faster Whisper caches,
voice-gallery files, and isolated optional-engine Python dependencies under the
managed location selected by the user:

```text
<selected base>\data\
```

Configurations created before storage selection was introduced remain
compatible with the historical locations:

```text
%LOCALAPPDATA%\LocalText2Voice\
<installation>\runtimes\python311\engine-deps\
```

When the Windows uninstaller detects those downloads, it asks whether they
should also be removed. **Yes** is the default because these files can occupy
many gigabytes. A `.localtext2voice-assets.json` ownership marker is required
before the uninstaller removes a user-selected `data` tree; arbitrary selected
folders are never recursively deleted.

The cleanup deliberately preserves audiobook projects, generated WAV/MP3
files, settings, music, and logs. Server coordination files and downloaded
update caches are always removed. Silent uninstallations preserve downloaded AI
data because they cannot ask the user for confirmation.

## Rebuild steps

From the repository root:

```powershell
.\tools\build_windows_installer.ps1
```

The script reads the version from `app/__init__.py`, builds the portable app with a clean embedded Python runtime, passes that same version to Inno Setup, and generates the SHA-256 file. It deliberately ignores locally preserved runtime packages so development-only engine dependencies cannot inflate the public installer. To rebuild only the installer from an existing `dist/LocalText2Voice/` folder:

```powershell
.\tools\build_windows_installer.ps1 -SkipAppBuild
```

If the normal `dist/` is being used by a running MCP or app process, build a clean release distribution elsewhere without interrupting it:

```powershell
.\tools\build_windows_installer.ps1 -DistRoot "build\release_dist"
```

The generated installer is:

```text
.util_instalador_y_firmas/output/LocalText2Voice-Setup.exe
.util_instalador_y_firmas/output/LocalText2Voice-Setup.exe.sha256
```

The filename deliberately stays unchanged across releases. The version remains embedded in the EXE metadata and in the application itself.

## Automatic update flow

The installed Windows app checks:

```text
https://api.github.com/repos/estebanstifli/LocalText2Voice/releases/latest
```

The stable release endpoint excludes drafts and prereleases. The updater:

1. Compares `tag_name` with the version in `app/__init__.py` using PEP 440 version ordering.
2. Requires exact assets named `LocalText2Voice-Setup.exe` and `LocalText2Voice-Setup.exe.sha256`.
3. Downloads both files into `%LOCALAPPDATA%\LocalText2Voice\updates\<version>\`.
4. Verifies the downloaded size and SHA-256 hash.
5. Offers to close LocalText2Voice and open the verified installer.

Automatic checks run only in the frozen app and at most once every 24 hours. Manual checks are always available from **Help > Check for updates**. Automatic errors stay silent and are written to the app log; manual errors are shown to the user.

Version `1.1.0` is the first build containing the updater. Existing `1.0.0` installations must install it manually once; releases after that can be detected automatically.

## Publishing an update

1. Update `__version__` in `app/__init__.py`.
2. Run `tools/build_windows_installer.ps1`.
3. Create a matching Git tag, for example `v1.3.0`.
4. Create a normal GitHub Release for that tag (not a draft or prerelease).
5. Add the release notes.
6. Upload exactly:
   - `LocalText2Voice-Setup.exe`
   - `LocalText2Voice-Setup.exe.sha256`
7. Publish the release.

The permanent installer URL is:

```text
https://github.com/estebanstifli/LocalText2Voice/releases/latest/download/LocalText2Voice-Setup.exe
```

## Validation performed

The installer was tested in silent mode:

CPU profile:

```powershell
LocalText2Voice-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TYPE=cpu /DIR="<temp>"
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
LocalText2Voice-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TYPE=gpu /DIR="<temp>"
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
LocalText2Voice-Setup.exe /VERYSILENT /LANG=spanish /TYPE=cpu /DIR="<temp>"
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
3. Sign `LocalText2Voice-Setup.exe`.

Signing only the installer is workable for early distribution, but signing both is more professional because the installed EXE still has publisher identity if Windows Defender, SmartScreen, or antivirus inspects it directly.

For open source signing, SignPath Foundation is the preferred future path:

- Public GitHub repository.
- OSI-approved license.
- GitHub Actions build.
- Release artifacts produced by CI.
- SignPath signing policy.
- Manual approval of signed releases.

Until the certificate/signing flow is ready, distribute the unsigned installer through GitHub Releases and clearly label it as unsigned.
