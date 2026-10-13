# Native TTS with audio.cpp

[Guia en espanol](AUDIOCPP.es.md)

LocalText2Voice 3.0 adds an optional native inference path alongside the existing
Python engines and cloud providers. The application and MCP still use Python;
audio.cpp inference does not need another Python environment or PyTorch install.
More native models are planned, but each needs catalog integration, compatible
runtime support and tests. This is not an automatic catalog of all upstream models.

## Available models

| Model | MCP model ID | Weight download | Reference |
| --- | --- | ---: | --- |
| Higgs Audio v3 TTS 4B Q8 | `higgs_audio_tts_4b_q8_0` | 5.10 GB | Required |
| Fish Audio S2 Pro Q8 | `fish_audio_s2_pro_q8_0` | 6.32 GB | Optional |

Both produce WAV narration, not live streaming. Their languages are inferred
from text. `{{lang ...}}` records the language but does not force native language
selection. Speed and volume use the application's WAV postprocessing.

## Install and use

1. Open Settings > TTS Engines and select the Higgs or Fish row.
2. Choose CPU, CUDA 12.4 or Vulkan on Windows. CPU is the initial default, not
   automatic GPU detection. A compatible GPU driver is required for GPU use.
3. Install the selected model/runtime after reading its separate license.
4. Select a catalog reference with its transcript, or clone/import your own voice.
   Use a clean PCM WAV and accurate reference text. References can be shared
   between the two models; generation settings are saved per model.
5. Optionally load the engine into memory, then test a short passage. EngineHost
   keeps the native process alive between segments. Changing model, backend or
   GPU replaces the process. Cancellation stops it, so the next job reloads it.

Higgs uses 200-character chunks by default and Fish uses 400. Configure their
separate limits under Advanced. Temperature, top-p, top-k, seed, maximum tokens
and internal chunk size are exposed in settings. Higgs retries the specific
max-tokens-before-EOC failure at most twice, raising the limit up to 4096 (without
reducing an already larger limit), halving internal chunks and varying fixed
seeds. It retains the text/tags and does not save incomplete audio on failure.

## Native expression controls

The Emotion > Higgs/Fish menus insert literal native controls, not application
`{{emotion}}` commands. Selecting a menu does not select the engine. Native Fish
tag names remain in English because they are editable model instructions.

```text
<|emotion:elation|>What wonderful news!
<|style:whispering|>Let me tell you a secret.
Wait <|prosody:long_pause|> now we can continue.
```

For Fish, try `[excited]`, `[whisper]` or `[volume down]`. Remove a tag to remove
its instruction. Normalization preserves supported controls and chunking keeps
multiword tags together. The menus are shortcuts, not a promise that every tag
has the same strength or reliability in every language.

## Storage and platform limits

Runtime v0.9.1 and the model revision are pinned in `audiocpp_assets.py` and
`audiocpp_config.py`. Downloaded packages and GGUFs are SHA-256 verified. Failed
downloads retain resumable `.part` files when possible. Verified files are reused.

Files live in `data/audiocpp/{runtime,models,downloads}` under the configured AI
storage root, not in the source tree. Removing a model keeps the shared runtime,
other model and download cache. Unload it before changing installed files.

Windows runtime downloads: CPU 26.95 MB, Vulkan 61.73 MB, CUDA 1.07 GB (two ZIPs).
The measured CUDA runtime expands to 2.08 GB. Both models plus CUDA and retained
download ZIPs occupy about 14.56 GB. Figures use decimal units and are not RAM/VRAM
requirements. None of these optional assets are bundled in the base installer.

Linux CPU/Vulkan and macOS CPU/Metal packages are mapped, but real distribution
and inference validation on those platforms is still required. The maintainer
has reported successful use of both models; that does not establish compatibility
or performance on every GPU. No GPU driver is installed by LocalText2Voice.

## MCP

Existing tools support `engine_id="audiocpp"`, `model`, `backend`, reference WAV and
transcript. `list_engines` exposes both models and licenses; `engine_memory` reports
the loaded model/backend. Installation is performed through desktop settings.

```json
{
  "text": "This is a local narration test.",
  "engine_id": "audiocpp",
  "model": "higgs_audio_tts_4b_q8_0",
  "backend": "cuda",
  "reference_audio_path": "D:/voices/reference.wav",
  "reference_text": "Exact transcript of the reference recording."
}
```

Use these arguments with `create_audiobook` or `generate_audio`. A saved `voice`
name may replace the reference arguments; `{{voice "Name"}}` changes saved
references by segment. The private native server binds to loopback on a free port.

## Licenses and release status

Model terms are independent of the application/runtime license. Read the actual
[Higgs license](https://huggingface.co/bosonai/higgs-tts-3-4b/blob/main/LICENSE) and
[Fish license](https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md) before
installation/use. Quantization does not remove those conditions. Upstream legal
notices are retained, including "Built with Fish Audio".

The 3.0.0 preparation is local and unsigned while the production signing
certificate is pending. It must not ask users to trust the test certificate.
