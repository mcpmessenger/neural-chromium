@echo off
echo [Neural-Container] Starting Neural-Chromium...

:: 1. Launch Chrome in Headless(ish) Mode with Remote Debugging
:: Note: For Zero-Copy Vision to work in container, we might need --no-sandbox
start "Chrome" C:\neural-chromium\bin\chrome.exe --remote-debugging-port=9222 --start-maximized --no-first-run --no-default-browser-check --disable-gpu

:: 2. Wait for Chrome to warm up
timeout /t 5

:: 3. Launch MCP Server
echo [Neural-Container] Starting MCP Server...
python C:\neural-chromium\bin\mcp_server.py --port 9222
