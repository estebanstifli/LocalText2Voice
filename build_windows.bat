@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 goto :error
)

echo Installing build dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

set "DIST_DIR=dist\LocalText2Voice"
set "RUNTIME_BACKUP=build\preserved_runtime\python311"
if exist "%DIST_DIR%\runtimes\python311\python\python.exe" (
    echo Preserving installed embedded Python runtime from previous build...
    if exist "%RUNTIME_BACKUP%" rmdir /S /Q "%RUNTIME_BACKUP%"
    xcopy /E /I /Y "%DIST_DIR%\runtimes\python311" "%RUNTIME_BACKUP%" >nul
    if errorlevel 1 goto :error
)

echo Building portable application folder...
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --onedir ^
    --name LocalText2Voice ^
    --icon "assets\LocalText2Voice.ico" ^
    --paths "%CD%" ^
    --collect-data qtawesome ^
    --add-data "locales;locales" ^
    --add-data "assets;assets" ^
    main.py
if errorlevel 1 goto :error

echo Preparing bundled embedded Python runtime...
".venv\Scripts\python.exe" tools\prepare_python_runtime.py
if errorlevel 1 goto :error

if not exist "%DIST_DIR%\engines\piper" mkdir "%DIST_DIR%\engines\piper"
if not exist "%DIST_DIR%\voices" mkdir "%DIST_DIR%\voices"
if not exist "%DIST_DIR%\ffmpeg" mkdir "%DIST_DIR%\ffmpeg"
if not exist "%DIST_DIR%\music" mkdir "%DIST_DIR%\music"
if not exist "%DIST_DIR%\output" mkdir "%DIST_DIR%\output"
if not exist "%DIST_DIR%\licenses" mkdir "%DIST_DIR%\licenses"
if not exist "%DIST_DIR%\runtimes" mkdir "%DIST_DIR%\runtimes"

xcopy /E /I /Y "engines" "%DIST_DIR%\engines" >nul
if exist "%DIST_DIR%\engines\kokoro" rmdir /S /Q "%DIST_DIR%\engines\kokoro"
if exist "%DIST_DIR%\engines\chatterbox\chatterbox_engine" rmdir /S /Q "%DIST_DIR%\engines\chatterbox\chatterbox_engine"
echo Chatterbox installs optional dependencies into the embedded Python runtime.
xcopy /E /I /Y "voices" "%DIST_DIR%\voices" >nul
xcopy /E /I /Y "ffmpeg" "%DIST_DIR%\ffmpeg" >nul
xcopy /E /I /Y "music" "%DIST_DIR%\music" >nul
xcopy /E /I /Y "licenses" "%DIST_DIR%\licenses" >nul
if exist "%RUNTIME_BACKUP%\python\python.exe" (
    echo Restoring preserved embedded Python runtime with installed engine dependencies...
    xcopy /E /I /Y "%RUNTIME_BACKUP%" "%DIST_DIR%\runtimes\python311" >nul
) else (
    xcopy /E /I /Y "build\python_runtime\python311" "%DIST_DIR%\runtimes\python311" >nul
)
".venv\Scripts\python.exe" tools\stamp_python_runtime.py "%DIST_DIR%\runtimes\python311"
if errorlevel 1 goto :error
copy /Y "config.example.json" "%DIST_DIR%\config.example.json" >nul
copy /Y "README.md" "%DIST_DIR%\README.md" >nul
copy /Y "LICENSE" "%DIST_DIR%\LICENSE" >nul
copy /Y "THIRD_PARTY_NOTICES.md" "%DIST_DIR%\THIRD_PARTY_NOTICES.md" >nul

echo.
echo Build complete:
echo   %CD%\%DIST_DIR%\LocalText2Voice.exe
echo.
echo Piper, Python runtime, voice models, and FFmpeg stay outside the executable.
exit /b 0

:error
echo.
echo Build failed. Review the errors above.
exit /b 1
