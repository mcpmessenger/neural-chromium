@echo off
REM Open Neural-Chromium Settings Page
REM This launches the API key configuration UI

echo Opening Neural-Chromium Settings...
echo.

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0

REM Launch settings page in default browser
start "" "%SCRIPT_DIR%settings.html"

echo Settings page opened in your browser.
echo Configure your API keys and save the config.json file to this directory.
echo.
pause
