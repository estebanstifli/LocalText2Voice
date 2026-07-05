# LocalText2Voice Chatterbox CUDA Runtime v3

Optional GPU runtime for the Chatterbox engine.

## Fixes

- Fixed Chatterbox Multilingual V3 startup with `chatterbox-tts 0.1.7`.
- Removed the unsupported `t3_model` keyword argument from the runtime CLI.
- Verified CUDA with `torch 2.6.0+cu126`.
- Verified short multilingual synthesis creates a WAV successfully.

## Assets

The ZIP is split into two parts for GitHub Release upload:

- `LocalText2Voice-Chatterbox-CUDA.zip.part01`
- `LocalText2Voice-Chatterbox-CUDA.zip.part02`
- `LocalText2Voice-Chatterbox-CUDA.zip.sha256`

LocalText2Voice v0.4.5 downloads and joins the two parts automatically.
