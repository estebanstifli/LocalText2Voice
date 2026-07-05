@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-chatterbox\Scripts\python.exe" (
    echo Creating Chatterbox engine virtual environment...
    py -3.11 -m venv .venv-chatterbox
    if errorlevel 1 (
        echo Python 3.11 is recommended for Chatterbox. Trying default py launcher...
        py -m venv .venv-chatterbox
        if errorlevel 1 goto :error
    )
)

echo Installing Chatterbox engine dependencies...
".venv-chatterbox\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error

echo Installing PyTorch CUDA runtime. This can take a while...
".venv-chatterbox\Scripts\python.exe" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
if errorlevel 1 goto :error

".venv-chatterbox\Scripts\python.exe" -m pip install -r requirements-chatterbox-engine.txt
if errorlevel 1 goto :error

echo Building Chatterbox engine folder...
".venv-chatterbox\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --console ^
    --onedir ^
    --name chatterbox_engine ^
    --paths "%CD%" ^
    --collect-all chatterbox ^
    --collect-all perth ^
    --collect-all transformers ^
    --collect-all tokenizers ^
    --collect-all huggingface_hub ^
    app\tts\chatterbox_cli.py
if errorlevel 1 goto :error

if not exist "engines\chatterbox" mkdir "engines\chatterbox"
if exist "engines\chatterbox\chatterbox_engine" rmdir /S /Q "engines\chatterbox\chatterbox_engine"
xcopy /E /I /Y "dist\chatterbox_engine" "engines\chatterbox\chatterbox_engine" >nul
rmdir /S /Q "dist\chatterbox_engine" >nul 2>nul

if not exist "release_assets" mkdir "release_assets"
if exist "release_assets\LocalText2Voice-Chatterbox-CUDA.zip" del /Q "release_assets\LocalText2Voice-Chatterbox-CUDA.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Compress-Archive -Path 'engines\chatterbox\chatterbox_engine' -DestinationPath 'release_assets\LocalText2Voice-Chatterbox-CUDA.zip' -Force"
if errorlevel 1 goto :error
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Get-FileHash 'release_assets\LocalText2Voice-Chatterbox-CUDA.zip' -Algorithm SHA256 | ForEach-Object { $_.Hash } | Set-Content 'release_assets\LocalText2Voice-Chatterbox-CUDA.zip.sha256'"

echo.
echo Chatterbox engine build complete:
echo   %CD%\engines\chatterbox\chatterbox_engine\chatterbox_engine.exe
echo Runtime pack:
echo   %CD%\release_assets\LocalText2Voice-Chatterbox-CUDA.zip
echo.
echo The model files are still downloaded on demand to %%LOCALAPPDATA%%\LocalText2Voice\models\chatterbox.
exit /b 0

:error
echo.
echo Chatterbox engine build failed. Review the errors above.
exit /b 1
