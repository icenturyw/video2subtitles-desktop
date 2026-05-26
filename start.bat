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
echo [1/2] Checking Whisper server...
curl.exe -s http://127.0.0.1:8765/health >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    if exist "%V2S_WHISPER_SERVER_DIR%\server.py" (
        if exist "%V2S_WHISPER_SERVER_DIR%\venv\Scripts\activate.bat" (
            echo Starting Whisper server from %V2S_WHISPER_SERVER_DIR%...
            pushd "%V2S_WHISPER_SERVER_DIR%"
            start /B "" cmd /c "call venv\Scripts\activate.bat && python server.py"
            popd
            ping -n 10 127.0.0.1 >nul
        ) else (
            echo [WARNING] Whisper server venv not found at %V2S_WHISPER_SERVER_DIR%\venv.
            echo Local files can still use faster-whisper directly; online URLs need a Whisper server.
        )
    ) else (
        echo [WARNING] Whisper server not found at %V2S_WHISPER_SERVER_DIR%.
        echo Local files can still use faster-whisper directly; online URLs need a Whisper server.
    )
)
echo.

:: Start desktop client
echo [2/2] Starting desktop client...
start "" pythonw app.py
echo Client started
timeout /t 2 >nul
exit
