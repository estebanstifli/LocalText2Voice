@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-kokoro\Scripts\python.exe" (
    echo Creating Kokoro engine virtual environment...
    py -m venv .venv-kokoro
    if errorlevel 1 goto :error
)

echo Installing Kokoro engine dependencies...
".venv-kokoro\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv-kokoro\Scripts\python.exe" -m pip install -r requirements-kokoro-engine.txt
if errorlevel 1 goto :error

echo Building Kokoro engine executable...
".venv-kokoro\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --console ^
    --onefile ^
    --name kokoro_engine ^
    --paths "%CD%" ^
    --collect-all kokoro_onnx ^
    --collect-all espeakng_loader ^
    --collect-all language_tags ^
    app\tts\kokoro_cli.py
if errorlevel 1 goto :error

if not exist "engines\kokoro" mkdir "engines\kokoro"
copy /Y "dist\kokoro_engine.exe" "engines\kokoro\kokoro_engine.exe" >nul
del /Q "dist\kokoro_engine.exe" >nul 2>nul

echo.
echo Kokoro engine build complete:
echo   %CD%\engines\kokoro\kokoro_engine.exe
echo.
exit /b 0

:error
echo.
echo Kokoro engine build failed. Review the errors above.
exit /b 1
