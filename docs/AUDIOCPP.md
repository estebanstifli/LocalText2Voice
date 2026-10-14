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
| Breeze TTS 2 Q8 | `breeze_tts_2_q8_0` | 5.08 GB | Optional; exact transcript required when cloning |
| VoxCPM 2 Q8 | `voxcpm2_q8_0` | 2.96 GB | Optional speaker reference |
| VoxCPM 2 BF16 | `voxcpm2_bf16` | 4.77 GB | Optional speaker reference |
| VoxCPM 2 Original | `voxcpm2_orig` | 4.96 GB | Optional speaker reference |

All six variants produce WAV narration, not live streaming. Their languages are inferred
from text. `{{lang ...}}` records the language but does not force native language
selection. Speed and volume use the application's WAV postprocessing.

## Install and use

Run the updated source application and restart any persistent EngineHost before
testing new code (stop/start in host settings). Existing packaged executables
have not been rebuilt by this change.

1. Open Settings > TTS Engines and select the Higgs, Fish, Breeze or VoxCPM 2 row.
2. Choose CPU, CUDA 12.4 or Vulkan on Windows. CPU is the initial default, not
   automatic GPU detection. A compatible GPU driver is required for GPU use.
3. Install the selected model/runtime after reading its separate license.
4. Select a catalog reference with its transcript, or clone/import your own voice.
   Use a clean PCM WAV and accurate reference text. References can be shared
   between the models; generation settings are saved per model.
5. Optionally load the engine into memory, then test a short passage. EngineHost
   keeps the native process alive between segments. Changing model, backend or
   GPU replaces the process. Cancellation stops it, so the next job reloads it.

Higgs uses 200-character chunks by default and Fish uses 400; Breeze uses 600. Configure their
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
other models and download cache. Unload it before changing installed files.

Windows runtime downloads: CPU 26.95 MB, Vulkan 61.73 MB, CUDA 1.07 GB (two ZIPs).
The measured CUDA runtime expands to 2.08 GB. Higgs and Fish plus CUDA and retained
download ZIPs occupy about 14.56 GB. Figures use decimal units and are not RAM/VRAM
requirements. None of these optional assets are bundled in the base installer.

Linux CPU/Vulkan and macOS CPU/Metal packages are mapped, but real distribution
and inference validation on those platforms is still required. The maintainer
has reported successful use of both models; that does not establish compatibility
or performance on every GPU. No GPU driver is installed by LocalText2Voice.

## MCP

Existing tools support `engine_id="audiocpp"`, `model`, `backend`, reference WAV and
transcript. `list_engines` exposes all six packages and licenses; `engine_memory` reports
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

## Breeze instructions and vocal events

Breeze supports **English and Chinese**, with reference-free voice design or
reference-guided voice cloning/direction. Spanish is not a declared language.
Its Q8 weights use the existing v0.9.1 runtime and pinned, SHA-256 checked downloads.
The [BreezeBlue model license](https://huggingface.co/BreezeBlue/Breeze-TTS-2/blob/main/LICENSE)
limits weights and self-hosted outputs to research and non-commercial use.

Emotion > Breeze TTS 2 offers emotions, styles and vocal sounds. For example:
`{{emotion happy}}Hello (laugh).` or
`{{emotion custom "Speak softly and slowly."}}Good night.`
The wrappers become `options.instruction`, separate from the spoken text, for
the next markup segment and its chunks. Application presets are natural-language
directions, not native emotion vectors. Intermediate percentages warn and are
ignored; Off uses a neutral instruction. Use instructions in the target language.
Review > Segment detail > Emotions edits the saved instruction before regeneration.

Native English vocal events stay in the text: `(laugh)`, `(cough)`,
`(clears throat)`, `(sigh)`. Chinese equivalents `[笑]`, `[咳嗽]`, `[清嗓子]`,
`[叹气]` are also preserved. Normalization/chunking protect these exact tokens;
verification excludes them from expected speech without removing normal parentheses.

Breeze defaults: temperature/depth_temperature 0.9, top-p 1, top-k 50,
max_tokens 1500, text_chunk_size 600, guidance_scale 1. Settings are isolated per
model; reference voices are shared. Seed -1 generates a random request seed.
MCP `create_audiobook` and `generate_audio` accept `instruction` alongside
`model="breeze_tts_2_q8_0"` and optional reference audio/transcript.

See [audio.cpp's pinned model guide](https://github.com/0xShug0/audio.cpp/blob/v0.9.1/docs/models/breeze_tts.md)
and the [official Breeze repository](https://github.com/breezeblue-ai/breeze-tts).
Integration tests cover simulated downloads/runtime reuse, HTTP payloads, UI,
markup, normalization and Review persistence/regeneration. Actual Q8 audio
quality, speed and memory use still need testing on the user's hardware.

## Breeze Voice Designer

Select the Breeze row in TTS Engines; the redundant Model dropdown has been removed.
Once installed, Voices > Design Voice opens a dedicated dialog with English/Chinese,
a free-text voice description, custom sample text (up to 600 characters), and native
sampling controls. Description and sample should use the same language. Guidance
scale starts at 4, following the official design examples, without changing global
settings. Generation uses the shared host and clears any selected reference or
playback processing. The dialog waits for completion before allowing closure.

Generate, listen, then Save as Voice. The complete 1–60 second WAV and its exact
sample text are imported into the audio.cpp gallery and selected for reuse by
Breeze, Higgs or Fish. Changed synthesis inputs require a new preview before saving;
renaming does not. Closing removes temporary previews, not saved voices.


## VoxCPM 2 Q8

The fourth optional row uses the same pinned v0.9.1 runtime and a SHA-256 verified
2.96 GB GGUF. VoxCPM 2 provides 30 languages including Spanish, 48 kHz output,
natural-language voice design and speaker-reference cloning under Apache-2.0.
Download size is not a RAM/VRAM or speed benchmark.

Emotion > VoxCPM 2 > Emotions inserts `{{emotion happy}}`, Custom or Off. As with
IndexTTS, the command applies to the next text segment and all its derived chunks.
Instructions remain metadata in Review; the adapter sends `(instruction) text`
on **every** chunk, including internal chunks and regenerated segments. Off uses
no prefix; reset restores base settings. Numeric intermediate intensity is warned
and ignored; describe it with Custom. Enter descriptions without parentheses or
markup (up to 1000 characters); the adapter adds the parentheses.

Native VoxCPM chunking only prefixes the first chunk, so this adapter splits first,
uses independent requests with the same reference/seed, prevents further native
splitting and combines validated WAVs atomically. It does not carry latent audio
continuation across requests. Prefer saved references for consistent voice identity.

Defaults: 400 characters, 4096 tokens, 10 diffusion steps, guidance 2 and random seed
(-1). Unsupported temperature/top-p/top-k controls are hidden. Segment detail offers
emotion presets, Off, free text and diffusion settings for regeneration.

Voices > Design Voice has 30 language choices, descriptions, custom sample text
(up to 600 characters) and supported generation controls. Examples cover English,
Spanish and Chinese; other languages require custom sample text. Generation clears
any selected reference. Save retains the full 1–60 second WAV and its exact **clean**
transcript as a reusable catalog voice; the description remains metadata.

Existing catalog voices and Clone Voice work through `voice_ref`. The native speech
endpoint does not expose continuation audio, so this integration implements speaker
reference cloning, not upstream's “Ultimate Cloning” audio-plus-transcript mode.
MCP also accepts `instruction`, `guidance_scale` and `num_inference_steps`.

Transport, chunking, persistence and UI have automated coverage with a simulated
native process. Real Q8 audio quality/performance still needs a local inference test.
See the [complete Spanish analysis](AUDIOCPP.es.md#voxcpm-2-q8-análisis-e-integración)
and primary sources: [OpenBMB](https://github.com/OpenBMB/VoxCPM),
[audio.cpp contract](https://github.com/0xShug0/audio.cpp/blob/v0.9.1/model_specs/voxcpm2.json).


### VoxCPM 2 precision comparison

BF16 and Original are separate optional rows using the same pinned runtime.
Original preserves original tensor dtypes; it is not a claim of all-FP32 weights.
Installations coexist and removal affects only the selected GGUF. Each has an
independent generation profile with identical defaults. The Advanced VoxCPM 2
chunk limit is shared. Markup, Review, reference voices, Design Voice and MCP
preserve the selected package ID. For comparison, use the same reference, text,
backend and controls, and fix the same seed instead of -1. Higher precision does
not guarantee audibly better output; download sizes are not runtime memory sizes.
