# Third-Party Notices

LocalText2Voice source code is licensed under the MIT License. The portable
application also uses or can invoke third-party software and models that are
not covered by the LocalText2Voice license.

This document is an attribution and distribution checklist, not legal advice.
Always review the license files shipped with the exact versions you distribute.
Common license texts are included in the portable distribution under
`licenses/`.

## Piper

- Project: Piper, a local neural text-to-speech system
- Upstream: https://github.com/rhasspy/piper
- Runtime used by the project: `2023.11.14-2` for Windows x64
- Upstream license file: https://github.com/rhasspy/piper/blob/master/LICENSE.md

The original Rhasspy Piper repository identifies its source code as MIT
licensed. Its Windows runtime contains bundled components and data files that
may have their own notices. Preserve all upstream files and notices when
redistributing the runtime.

## Piper Voice Models

- Catalog: https://huggingface.co/rhasspy/piper-voices

Voice models are independent downloadable assets. A repository-level license
does not replace the dataset and speaker conditions documented by each model.
Review the individual `MODEL_CARD` before commercial use or redistribution.

The public Windows release intentionally does not preinstall voice models.
Users can preview and download a selected voice from the application.

The Spanish demo in `docs/audio/localtext2voice-demo-es.mp3` was generated with:

- Model: `es_ES-davefx-medium`
- Model card: https://huggingface.co/rhasspy/piper-voices/blob/main/es/es_ES/davefx/medium/MODEL_CARD
- Dataset license stated by the model card: CC0

## FFmpeg

- Project: FFmpeg
- Legal and license information: https://ffmpeg.org/legal.html
- Windows build provider: https://www.gyan.dev/ffmpeg/builds/
- Bundled build: FFmpeg `7.1` essentials, static Windows x64 build
- Build license stated by the provider: GPLv3
- Corresponding FFmpeg source release: https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz

The application runs `ffmpeg.exe` as a separate process. The bundled executable
was configured with GPL components, so its own distribution is governed by the
GPL terms stated by the build provider. Preserve this notice and provide access
to the corresponding source and license information when redistributing it.

## PySide6 and Qt

- Project: Qt for Python / PySide6
- Licensing overview: https://doc.qt.io/qtforpython-6/
- Detailed notices: https://doc.qt.io/qtforpython-6/licenses.html

Qt for Python is offered under LGPLv3/GPLv3 and commercial licensing options.
The PyInstaller folder build keeps Qt libraries as separate files. Distributors
must comply with the licensing option applicable to their use and preserve the
relevant Qt and PySide6 notices.

## Other Python Packages

The Python environment may include PyInstaller, python-docx, and their
dependencies. Their package metadata and license files are authoritative.
Review the installed versions before distributing a modified build.

## Music and User Content

LocalText2Voice does not bundle intro, background, or outro music. Users are
responsible for having the rights required to process and publish their text,
music, generated speech, and final audio.
