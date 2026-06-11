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

echo Building portable application folder...
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --onedir ^
    --name LocalText2Voice ^
    --icon "assets\LocalText2Voice.ico" ^
    --paths "%CD%" ^
    --add-data "locales;locales" ^
    --add-data "assets;assets" ^
    main.py
if errorlevel 1 goto :error

set "DIST_DIR=dist\LocalText2Voice"
if not exist "%DIST_DIR%\engines\piper" mkdir "%DIST_DIR%\engines\piper"
if not exist "%DIST_DIR%\voices" mkdir "%DIST_DIR%\voices"
if not exist "%DIST_DIR%\ffmpeg" mkdir "%DIST_DIR%\ffmpeg"
if not exist "%DIST_DIR%\music" mkdir "%DIST_DIR%\music"
if not exist "%DIST_DIR%\output" mkdir "%DIST_DIR%\output"
if not exist "%DIST_DIR%\licenses" mkdir "%DIST_DIR%\licenses"

xcopy /E /I /Y "engines" "%DIST_DIR%\engines" >nul
xcopy /E /I /Y "voices" "%DIST_DIR%\voices" >nul
xcopy /E /I /Y "ffmpeg" "%DIST_DIR%\ffmpeg" >nul
xcopy /E /I /Y "music" "%DIST_DIR%\music" >nul
xcopy /E /I /Y "licenses" "%DIST_DIR%\licenses" >nul
copy /Y "config.example.json" "%DIST_DIR%\config.example.json" >nul
copy /Y "README.md" "%DIST_DIR%\README.md" >nul
copy /Y "LICENSE" "%DIST_DIR%\LICENSE" >nul
copy /Y "THIRD_PARTY_NOTICES.md" "%DIST_DIR%\THIRD_PARTY_NOTICES.md" >nul

echo.
echo Build complete:
echo   %CD%\%DIST_DIR%\LocalText2Voice.exe
echo.
echo Piper, voice models, and FFmpeg stay outside the executable.
exit /b 0

:error
echo.
echo Build failed. Review the errors above.
exit /b 1
