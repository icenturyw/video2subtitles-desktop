@echo off
title Video2Subtitles - Debug Mode
cd /d "%~dp0"

echo ============================================
echo   Video2Subtitles - Debug Mode
echo ============================================
echo.

set "V2S_WHISPER_SERVER_DIR=%WHISPER_SERVER_DIR%"
if not defined V2S_WHISPER_SERVER_DIR set "V2S_WHISPER_SERVER_DIR=%~dp0whisper-server"

:: Start whisper service (if not running)
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
            start "Whisper-Service" cmd /k "call venv\Scripts\activate.bat && python %V2S_SERVER_SCRIPT%"
            popd
            ping -n 6 127.0.0.1 >nul
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

echo Starting desktop client (debug)...
python app.py
pause
