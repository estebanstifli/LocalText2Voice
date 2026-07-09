@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 goto :error
)

echo Installing/updating app dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Starting LocalText2Voice from source...
".venv\Scripts\python.exe" main.py
exit /b 0

:error
echo.
echo Could not start LocalText2Voice in development mode.
exit /b 1
