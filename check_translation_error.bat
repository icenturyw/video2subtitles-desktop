@echo off
echo Viewing latest localization service log...
echo.
tail -100 .cache\localization-service.log
echo.
echo ================================================
echo Latest error from tasks:
echo ================================================
python view_latest_error.py
pause
