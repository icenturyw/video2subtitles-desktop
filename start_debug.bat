@echo off
title Video2Subtitles - Debug Mode
cd /d "%~dp0"

echo ============================================
echo   Video2Subtitles - Debug Mode
echo ============================================
echo.

:: Start whisper server (if not running)
curl.exe -s http://127.0.0.1:8765/health >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    if defined WHISPER_SERVER_DIR (
        echo Starting Whisper server from %WHISPER_SERVER_DIR%...
        start "Whisper-Server" cmd /c "cd /d "%WHISPER_SERVER_DIR%" && call venv\Scripts\activate.bat && python server.py"
        ping -n 6 127.0.0.1 >nul
    ) else (
        echo [WARNING] Whisper server not running and WHISPER_SERVER_DIR not set.
    )
)

echo Starting desktop client (debug)...
python app.py
pause
