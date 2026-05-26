@echo off
title Video2Subtitles - Debug Mode
cd /d "%~dp0"

echo ============================================
echo   Video2Subtitles - Debug Mode
echo ============================================
echo.

set "V2S_WHISPER_SERVER_DIR=%WHISPER_SERVER_DIR%"
if not defined V2S_WHISPER_SERVER_DIR set "V2S_WHISPER_SERVER_DIR=%~dp0whisper-server"

:: Start whisper server (if not running)
curl.exe -s http://127.0.0.1:8765/health >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    if exist "%V2S_WHISPER_SERVER_DIR%\server.py" (
        if exist "%V2S_WHISPER_SERVER_DIR%\venv\Scripts\activate.bat" (
            echo Starting Whisper server from %V2S_WHISPER_SERVER_DIR%...
            pushd "%V2S_WHISPER_SERVER_DIR%"
            start "Whisper-Server" cmd /k "call venv\Scripts\activate.bat && python server.py"
            popd
            ping -n 6 127.0.0.1 >nul
        ) else (
            echo [WARNING] Whisper server venv not found at %V2S_WHISPER_SERVER_DIR%\venv.
            echo Local files can still use faster-whisper directly; online URLs need a Whisper server.
        )
    ) else (
        echo [WARNING] Whisper server not found at %V2S_WHISPER_SERVER_DIR%.
        echo Local files can still use faster-whisper directly; online URLs need a Whisper server.
    )
)

echo Starting desktop client (debug)...
python app.py
pause
