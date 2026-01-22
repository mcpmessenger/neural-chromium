@echo off
echo ===================================================
echo   RESTARTING NEURAL CHROMIUM SYSTEM (FULL STACK)
echo ===================================================

echo [1/3] CHECKING OLLAMA...
where ollama >nul 2>nul
if %errorlevel% neq 0 goto SKIP_OLLAMA

echo Launching Ollama CLI...
start "OLLAMA SERVER" cmd /k "ollama run llama3.2-vision"
timeout /t 2
goto NEXT_STEP

:SKIP_OLLAMA
echo WARNING: 'ollama' command not found in PATH.
echo Assuming Ollama Service is running in background.

:NEXT_STEP
echo [2/3] Launching Neural Chromium...
cd /d c:\operation-greenfield\neural-chromium-overlay
start "NEURAL CHROME" cmd /k "src\START_NEURAL_CHROME.bat"
timeout /t 5

echo [3/3] Launching Nexus Agent...
start "NEXUS AGENT" cmd /k "python src/nexus_agent.py"

echo ===================================================
echo   SYSTEM RESTART COMPLETE
echo ===================================================
echo Check the 3 new windows for status.
pause
