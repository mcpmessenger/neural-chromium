@echo off
echo Starting Neural-Chromium MCP Server (Manual)...
echo.
echo Make sure 'start_browser.bat' is already running!
echo.
cd src
python -u -m neural_chromium.mcp_server --port 9222
pause
