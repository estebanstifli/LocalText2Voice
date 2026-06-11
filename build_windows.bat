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
    --name CourseToPodcast ^
    --paths "%CD%" ^
    --add-data "locales;locales" ^
    main.py
if errorlevel 1 goto :error

set "DIST_DIR=dist\CourseToPodcast"
if not exist "%DIST_DIR%\engines\piper" mkdir "%DIST_DIR%\engines\piper"
if not exist "%DIST_DIR%\voices" mkdir "%DIST_DIR%\voices"
if not exist "%DIST_DIR%\ffmpeg" mkdir "%DIST_DIR%\ffmpeg"
if not exist "%DIST_DIR%\music" mkdir "%DIST_DIR%\music"
if not exist "%DIST_DIR%\output" mkdir "%DIST_DIR%\output"

xcopy /E /I /Y "engines" "%DIST_DIR%\engines" >nul
xcopy /E /I /Y "voices" "%DIST_DIR%\voices" >nul
xcopy /E /I /Y "ffmpeg" "%DIST_DIR%\ffmpeg" >nul
xcopy /E /I /Y "music" "%DIST_DIR%\music" >nul
copy /Y "config.example.json" "%DIST_DIR%\config.example.json" >nul

echo.
echo Build complete:
echo   %CD%\%DIST_DIR%\CourseToPodcast.exe
echo.
echo Piper, voice models, and FFmpeg stay outside the executable.
exit /b 0

:error
echo.
echo Build failed. Review the errors above.
exit /b 1
