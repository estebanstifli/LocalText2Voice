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

The English demo in `docs/audio/localtext2voice-demo-en.mp3` was generated with:

- Model: `en_GB-alan-medium`
- Model card: https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_GB/alan/medium/MODEL_CARD

Review the model card and its linked dataset terms before redistributing the
voice model or using generated audio commercially.

## Kokoro Runtime and Model Assets

- Python package: `kokoro-onnx`
- Upstream: https://github.com/thewh1teagle/kokoro-onnx
- Backend: ONNX Runtime CPU
- Model assets source: https://github.com/thewh1teagle/kokoro-onnx/releases

Kokoro runs through the embedded Python runtime. The large
Kokoro ONNX model and `voices-v1.0.bin` voice bundle are downloaded on demand
to the user's local app data folder and are not embedded in the main
application executable.

The Kokoro installer adds Python dependencies such as `kokoro-onnx`,
`onnxruntime`, `soundfile`, `numpy`, `espeakng-loader`, `phonemizer-fork`, and
`language-tags` into the private runtime. Their installed package metadata and
upstream license files are authoritative. In particular, the installed
`phonemizer-fork` package identifies itself as GPLv3; review its
terms before redistributing a portable build.

## Chatterbox Runtime and Model Assets

- Project: Chatterbox TTS by Resemble AI
- Upstream: https://github.com/resemble-ai/chatterbox
- Model pages: https://huggingface.co/ResembleAI/chatterbox and
  https://huggingface.co/ResembleAI/chatterbox-turbo
- Package: `chatterbox-tts`

Chatterbox is integrated as an optional advanced runtime. It is not required by
the main application executable. The app can install `chatterbox-tts`, PyTorch,
Torchaudio, Transformers, Hugging Face Hub, and related dependencies into its
private embedded Python runtime; their installed package metadata and upstream
license files are authoritative. Chatterbox model assets are downloaded on
demand to the user's local app data folder.

Chatterbox supports reference-audio voice cloning. Users are responsible for
having permission to use any reference voice and generated audio.

## Qwen3 TTS Runtime and Model Assets

- Project: Qwen3 TTS by QwenLM / Alibaba Cloud
- Upstream: https://github.com/QwenLM/Qwen3-TTS
- Fast runtime wrapper: https://github.com/andimarafioti/faster-qwen3-tts
- Model page: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice
- Python packages: `qwen-tts`, `faster-qwen3-tts`

Qwen3 TTS is integrated as an optional advanced local runtime. It is not
required by the main application executable. The app can install
`faster-qwen3-tts`, `qwen-tts`, PyTorch, Torchaudio, Transformers, Accelerate,
SoundFile, ONNX Runtime, and related dependencies into an isolated folder under
the private embedded Python runtime; their installed package metadata and
upstream license files are authoritative.

Qwen3 TTS model assets are downloaded on demand to the user's local app data
folder through Hugging Face tooling. Review the model card, license, and usage
terms before redistributing model files or using generated audio commercially.

## Google Gemini TTS API

- Product documentation: https://ai.google.dev/gemini-api/docs/speech-generation
- Cloud Text-to-Speech Gemini-TTS documentation:
  https://docs.cloud.google.com/text-to-speech/docs/gemini-tts

Google Gemini TTS is integrated as an optional remote API provider. No Gemini
model files are bundled with LocalText2Voice. Users must provide their own API
key and are responsible for Google API terms, billing, regional availability,
data handling, safety policies, and permitted usage of generated audio.

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
