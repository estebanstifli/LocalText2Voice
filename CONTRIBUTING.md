# Contributing to LocalText2Voice

Thanks for helping improve LocalText2Voice. Contributions are welcome for bug
fixes, accessibility, translations, documentation, tests, voice-management
improvements, and new TTS engine adapters.

## Development Setup

```powershell
git clone https://github.com/estebanstifli/LocalText2Voice.git
cd LocalText2Voice
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

## Pull Requests

1. Keep each change focused and follow the existing module boundaries.
2. Add or update tests when behavior changes.
3. Keep UI text in the locale JSON files instead of scattering hardcoded
   strings through widgets.
4. Keep TTS providers behind the `BaseTTSEngine` interface.
5. Describe user-visible behavior and the validation you performed.

## Repository Hygiene

Do not commit:

- downloaded `.onnx` voice models or their generated configuration files;
- Piper or FFmpeg binaries;
- music without explicit redistribution rights;
- API keys, tokens, personal configuration, build output, or generated audio.

When documenting a voice, link its model card and state its dataset license.
See `THIRD_PARTY_NOTICES.md` for distribution considerations.
