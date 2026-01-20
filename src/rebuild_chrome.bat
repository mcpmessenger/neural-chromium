@echo off
echo ========================================
echo   NEURAL-CHROMIUM REBUILDER
echo ========================================
echo.
echo [1/3] Setting up build environment...
set PATH=C:\operation-greenfield\depot_tools;%PATH%
set DEPOT_TOOLS_WIN_TOOLCHAIN=0

echo.
echo [2/3] Cleaning up locks...
taskkill /F /IM chrome.exe 2>nul
taskkill /F /IM ninja.exe 2>nul
taskkill /F /IM python.exe 2>nul

echo.
echo [3/3] Starting Incremental Build...
echo This will only recompile modified files (approx 5-10 mins).
echo.

REM Force touch the file to ensure Ninja sees the change
copy /b "c:\operation-greenfield\neural-chromium\src\content\browser\speech\network_speech_recognition_engine_impl.cc" +,, "c:\operation-greenfield\neural-chromium\src\content\browser\speech\network_speech_recognition_engine_impl.cc"

cd c:\operation-greenfield\neural-chromium\src
autoninja -C out\AgentDebug chrome

echo.
echo ========================================
echo   BUILD COMPLETE!
echo ========================================
echo.
echo You can now run: START_NEURAL_CHROME.bat
pause
