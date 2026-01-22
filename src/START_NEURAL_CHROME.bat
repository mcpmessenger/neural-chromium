@echo off
REM ============================================================================
REM Neural-Chromium Complete Launch Script
REM Starts Chrome + Python Agent + Opens Settings (if needed)
REM ============================================================================

echo.
echo ========================================
echo   NEURAL-CHROMIUM LAUNCHER
echo ========================================
echo.

REM Check if config.json exists
if not exist "%~dp0config.json" (
    echo [!] No config.json found!
    echo [!] Opening settings page to configure API keys...
    echo.
    start "" "%~dp0settings.html"
    echo.
    echo Please configure your API keys in the browser window.
    echo After saving, the config.json will be downloaded.
    echo Move it to: %~dp0
    echo.
    echo Press any key once you've saved the config...
    pause
    
    REM Check again
    if not exist "%~dp0config.json" (
        echo [X] Still no config.json found. Exiting...
        pause
        exit /b 1
    )
)

echo [✓] Config found!
echo.

REM Kill existing processes
echo [1/4] Cleaning up old processes...
taskkill /F /IM chrome.exe 2>nul
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul
echo     Done.
echo.

REM Clear old logs
echo [2/4] Clearing old logs...
del /Q C:\tmp\neural_chrome_profile\chrome_debug.log 2>nul
del /Q C:\tmp\nexus_agent.log 2>nul
echo     Done.
echo.

REM Start Native WebSocket Server
echo [3/4] Starting Native Server...
start "Native Server" cmd /k "cd /d %~dp0 && python -u native_server.py"
timeout /t 1 /nobreak >nul
echo     Done.
echo.

REM Start Python Agent
echo [3.5/4] Starting Nexus Agent (Python)...
start "Nexus Agent" cmd /k "cd /d %~dp0 && python -u nexus_agent.py"
timeout /t 2 /nobreak >nul
echo     Done.
echo.

REM Launch Chrome with flags to force pure software compositing
REM --disable-gpu: Disables hardware acceleration
REM --disable-software-rasterizer: Forces pure software compositing (bypasses SwiftShader)
REM --in-process-gpu: Runs GPU logic in browser process for easier debugging
REM --remote-debugging-port=9222: Enables Chrome DevTools Protocol for browser control
echo [4/4] Launching Neural-Chromium...
start "" cmd /c "c:\\operation-greenfield\\neural-chromium\\src\\out\\AgentDebug\\chrome.exe --load-extension=c:\\operation-greenfield\\neural-chromium-overlay\\extension --in-process-gpu --disable-gpu --disable-software-rasterizer --remote-debugging-port=9222 --enable-logging --v=1 --disable-features=OnDeviceSpeechRecognition --vmodule=network_speech_recognition_engine_impl=1 --user-data-dir=C:\\tmp\\neural_chrome_profile 2> C:\\tmp\\neural_chrome_profile\\chrome_debug.log"
echo     Done.
echo.

echo [5/5] Opening Log Monitor...
start "Chrome Logs" powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -Wait -Path C:\tmp\neural_chrome_profile\chrome_debug.log"
echo     Done.
echo.

echo ========================================
echo   NEURAL-CHROMIUM IS RUNNING!
echo ========================================
echo.
echo Chrome: Running with audio hook
echo Agent:  Running in separate window
echo Logs:   C:\tmp\nexus_agent.log
echo.
echo To test voice:
echo   1. Click microphone icon in Chrome
echo   2. Speak clearly
echo   3. Check agent window for transcription
echo.
echo To manage API keys:
echo   Double-click: open_settings.bat
echo.
pause
