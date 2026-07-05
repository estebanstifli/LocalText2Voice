Place kokoro_engine.exe here to enable Kokoro local synthesis.

Build it with:

  build_kokoro_engine.bat

The main LocalText2Voice app downloads Kokoro model assets on demand to the
per-user data folder:

  %LOCALAPPDATA%\LocalText2Voice\models\kokoro\

Do not commit generated executables or downloaded model files.
