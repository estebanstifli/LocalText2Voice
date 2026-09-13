# Third-Party Notices

LocalText2Voice source code is licensed under the MIT License. The portable
application also uses or can invoke third-party software and models that are
not covered by the LocalText2Voice license.

This document is an attribution and distribution checklist, not legal advice.
Always review the license files shipped with the exact versions you distribute.
Common license texts are included in the portable distribution under
`licenses/`.

## SoundCard

- Project: SoundCard, a cross-platform real-time audio library
- Upstream: https://github.com/bastibe/SoundCard
- Package: `soundcard` 0.4.x
- License: BSD 3-Clause

LocalText2Voice uses SoundCard for microphone and operating-system loopback
capture. SoundCard maps to WASAPI on Windows, CoreAudio on macOS, and
PulseAudio-compatible services on Linux. Its license is reproduced in
`licenses/SOUNDCARD-BSD-3-CLAUSE.txt`.

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
- CustomVoice 0.6B model page: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice
- Base 1.7B model page: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
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

## OmniVoice Runtime and Model Assets

- Project: OmniVoice by k2-fsa
- Upstream: https://github.com/k2-fsa/OmniVoice
- Model page: https://huggingface.co/k2-fsa/OmniVoice
- Source-code license: Apache License 2.0
- Pretrained-model license: Creative Commons Attribution-NonCommercial
  (CC-BY-NC), as stated by the upstream model card

OmniVoice is installed only on demand into an isolated dependency folder, and
its model assets are downloaded to the user's selected AI storage location.
The permissive source-code license does not replace the non-commercial terms of
the pretrained model. Review the current upstream model card and license before
redistribution or commercial use, and use reference voices only with the
speaker's permission.

## F5-TTS Russian Model and Optional Runtime

- Russian model: F5-TTS Russian by Misha24-10
- Model page: https://huggingface.co/Misha24-10/F5-TTS_RUSSIAN
- Selected checkpoint: `F5TTS_v1_Base_v2/model_last_inference.safetensors`
- Model license: Creative Commons Attribution-NonCommercial 4.0 International
  (CC BY-NC 4.0)
- Base project: F5-TTS by Yushen Chen, Zhikang Niu, Ziyang Ma, Keqi Deng,
  Chunhui Wang, Jian Zhao, Kai Yu, and Xie Chen (SWivid),
  https://github.com/SWivid/F5-TTS and https://arxiv.org/abs/2410.06885
- Stress model: Silero Stress by Silero Team,
  https://github.com/snakers4/silero-stress (MIT)

F5-TTS Russian is an optional download and is not part of the base
LocalText2Voice installation. The app displays and requires acknowledgement of
the non-commercial restriction before installation. The restriction applies to
the Russian model and its generated output: do not use it for commercial
purposes. Give appropriate credit to Misha24-10 and F5-TTS/SWivid, link to the
license, and indicate modifications where required by CC BY-NC 4.0.

Installing this engine creates a dedicated dependency folder containing F5-TTS,
PyTorch, and Silero Stress. Silero Stress is not installed for or loaded by any
other engine. Its installed package metadata and MIT license are authoritative.
See `licenses/F5-TTS-RUSSIAN-CC-BY-NC-4.0.txt` for the attribution notice and
license link.

## OpenAI TTS API

- API reference: https://platform.openai.com/docs/api-reference/audio

OpenAI TTS is an optional remote provider. No OpenAI model files are bundled.
Users supply their own API credentials and are responsible for applicable API
terms, billing, data handling, voice requirements, and generated-audio usage.

## ElevenLabs TTS API

- Product documentation:
  https://elevenlabs.io/docs/overview/capabilities/text-to-speech
- API reference: https://elevenlabs.io/docs/api-reference/text-to-speech/convert

ElevenLabs is an optional remote provider. No ElevenLabs models or voices are
bundled. Users supply their own API credentials and remain responsible for
provider terms, voice permissions, billing, and generated-audio usage.

## Google Gemini TTS API

- Product documentation: https://ai.google.dev/gemini-api/docs/speech-generation
- Cloud Text-to-Speech Gemini-TTS documentation:
  https://docs.cloud.google.com/text-to-speech/docs/gemini-tts

Google Gemini TTS is integrated as an optional remote API provider. No Gemini
model files are bundled with LocalText2Voice. Users must provide their own API
key and are responsible for Google API terms, billing, regional availability,
data handling, safety policies, and permitted usage of generated audio.

## Microsoft Azure Speech API

- Product documentation:
  https://learn.microsoft.com/en-us/azure/ai-services/speech-service/overview

Azure Speech is an optional remote provider. No Azure speech models or voices
are bundled. Users supply their own subscription credentials and are
responsible for Microsoft terms, billing, regional availability, SSML usage,
voice permissions, and generated-audio usage.

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

### LiteLLM, boto3 and Pillow

- LiteLLM: https://github.com/BerriAI/litellm
- boto3: https://github.com/boto/boto3
- Pillow: https://github.com/python-pillow/Pillow

These are optional application dependencies used for OpenAI-compatible text
analysis, private reference storage integrations and image processing. Their
installed package metadata, bundled license files and upstream terms are
authoritative for the exact versions distributed.

The main application includes `num2words` 0.5.14 for multilingual number and
ordinal expansion. The package identifies its license as LGPL and its source
headers permit redistribution under LGPL 2.1 or later. Upstream:
https://github.com/savoirfairelinux/num2words

The Python environment may also include PyInstaller, python-docx, and their
dependencies. Their package metadata and license files are authoritative.
Review the installed versions before distributing a modified build.

## Music and User Content

LocalText2Voice includes a small starter library of background music and sound
effects and also lets users import their own media. One bundled track added in
version 1.5.0 has the following documented provenance:

- Track: `Universo` by Da-Roz
- Bundled file: `music/background/da-roz-universo-141508.mp3`
- Source: https://pixabay.com/music/ambient-universo-141508/
- License: Pixabay Content License,
  https://pixabay.com/service/license-summary/

The track is distributed as an application background-music asset, not as a
standalone media product. Review the current source and license terms before
redistribution. Users remain responsible for having the rights required to
process and publish their text, imported music, generated speech, and final
audio.

One bundled track added in version 1.5.1 has the following documented
provenance:

- Track: `To the gates of Eleusis` by SamuelFJohanns
- Bundled file:
  `music/background/samuelfjohanns-to-the-gates-of-eleusis-245492.mp3`
- Source: https://pixabay.com/music/world-to-the-gates-of-eleusis-245492/
- License: Pixabay Content License,
  https://pixabay.com/service/license-summary/

This track is also distributed as an application background-music asset, not
as a standalone media product.
