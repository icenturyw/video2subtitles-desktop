@echo off
title Video2Subtitles
cd /d "%~dp0"

echo ============================================
echo   Video2Subtitles - Subtitle Generator
echo ============================================
echo.

set "V2S_WHISPER_SERVER_DIR=%WHISPER_SERVER_DIR%"
if not defined V2S_WHISPER_SERVER_DIR set "V2S_WHISPER_SERVER_DIR=%~dp0whisper-server"

:: Check if whisper server is running
echo [1/2] Checking local Whisper service...
curl.exe -s http://127.0.0.1:8765/health >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    set "V2S_SERVER_SCRIPT="
    if exist "%V2S_WHISPER_SERVER_DIR%\main.py" set "V2S_SERVER_SCRIPT=main.py"
    if not defined V2S_SERVER_SCRIPT if exist "%V2S_WHISPER_SERVER_DIR%\server.py" set "V2S_SERVER_SCRIPT=server.py"

    if defined V2S_SERVER_SCRIPT (
        if exist "%V2S_WHISPER_SERVER_DIR%\venv\Scripts\activate.bat" (
            echo Starting local Whisper service from %V2S_WHISPER_SERVER_DIR%\%V2S_SERVER_SCRIPT%...
            pushd "%V2S_WHISPER_SERVER_DIR%"
            set "API_AUTH_KEY="
            start /B "" cmd /c "call venv\Scripts\activate.bat && python %V2S_SERVER_SCRIPT%"
            popd
            ping -n 10 127.0.0.1 >nul
        ) else (
            echo [WARNING] Whisper service venv not found at %V2S_WHISPER_SERVER_DIR%\venv.
            echo Local files can still use faster-whisper directly; online URLs need the local Whisper service.
        )
    ) else (
        echo [WARNING] Whisper service entry not found at %V2S_WHISPER_SERVER_DIR%.
        echo Expected main.py ^(youtube-live-subtitles^) or server.py.
        echo Local files can still use faster-whisper directly; online URLs need the local Whisper service.
    )
)
echo.

:: Find Python interpreter (pythonw > python > py)
set "PYTHON_CMD="
where pythonw >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=pythonw"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set "PYTHON_CMD=python"
    ) else (
        where py >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            set "PYTHON_CMD=py"
        )
    )
)

if not defined PYTHON_CMD (
    echo [ERROR] Python not found! Please install Python 3.10+ and ensure it is in PATH.
    echo You can also try running: py app.py
    pause
    exit /b 1
)

:: Start desktop client
echo [2/2] Starting desktop client with %PYTHON_CMD%...
start "" %PYTHON_CMD% app.py
echo Client started
timeout /t 2 >nul
exit
