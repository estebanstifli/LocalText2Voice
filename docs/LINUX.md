# LocalText2Voice on Linux

The application core is cross-platform (PySide6). On Windows the installer
bundles everything; on Linux you run from source. Tested on Arch/CachyOS
(KDE Plasma, Wayland) with Python 3.12.

## Requirements

- Python 3.10+ with virtual-environment support. On Debian/Ubuntu install
  `python3-venv`; on Arch this is included with `python`.
- `ffmpeg` available in `PATH` (`sudo pacman -S ffmpeg` / `sudo apt install ffmpeg`).
  The bundled `ffmpeg/` folder is only needed on Windows; on Linux the app
  finds the system binary automatically (see `app/utils/ffmpeg_utils.py`).
- For local TTS engines: they are downloaded by the app itself
  (Settings -> Engine management). Piper and Kokoro work on CPU;
  Chatterbox / Qwen3 TTS / OmniVoice can use an NVIDIA GPU.

## Run

```bash
./run_dev.sh          # creates .venv and installs deps on first run
# or manually:
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
QT_QPA_PLATFORM=wayland .venv/bin/python main.py   # or omit for X11
```

## Desktop integration (optional)

```bash
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/localtext2voice.desktop <<EOF
[Desktop Entry]
Type=Application
Name=LocalText2Voice
Exec=$(pwd)/run_dev.sh
Icon=$(pwd)/assets/logotipo.png
Categories=Audio;Utility;
EOF
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

## Known platform notes

- Auto-update flow (`app/core/update_manager.py`) is Windows-only and is
  skipped on other platforms; update with `git pull`.
- Writable data lives in `$XDG_DATA_HOME/LocalText2Voice`
  (`~/.local/share/LocalText2Voice`) via `app/utils/paths.py:app_data_root()`.
- If Qt multimedia warnings about FFmpeg appear on startup, they are
  informational - playback still uses the system FFmpeg.
