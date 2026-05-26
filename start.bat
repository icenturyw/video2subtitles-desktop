@echo off
title Video2Subtitles
cd /d "%~dp0"

echo ============================================
echo   Video2Subtitles - Subtitle Generator
echo ============================================
echo.

:: Check if whisper server is running
echo [1/2] Checking Whisper server...
curl.exe -s http://127.0.0.1:8765/health >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    if defined WHISPER_SERVER_DIR (
        echo Starting Whisper server from %WHISPER_SERVER_DIR%...
        start /B cmd /c "cd /d "%WHISPER_SERVER_DIR%" && call venv\Scripts\activate.bat && python server.py"
        ping -n 10 127.0.0.1 >nul
    ) else (
        echo [WARNING] Whisper server not running and WHISPER_SERVER_DIR not set.
        echo The client will still start, but you need to start the server manually.
    )
)
echo.

:: Start desktop client
echo [2/2] Starting desktop client...
start "" pythonw app.py
echo Client started
timeout /t 2 >nul
exit
