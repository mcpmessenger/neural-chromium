# Neural-Chromium Windows Container
# Requires Windows Host Process Containers or Hyper-V Isolation
FROM mcr.microsoft.com/windows/servercore:ltsc2022

# Install Chocolatey
RUN powershell -NoProfile -InputFormat None -ExecutionPolicy Bypass -Command \
    "[System.Net.ServicePointManager]::SecurityProtocol = 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))"

# Install Python and Git
RUN choco install -y python git

# Set Workdir
WORKDIR C:/neural-chromium

# Copy Pre-built Binaries (This assumes user has built them)
# practical deployment would likely download from a private S3/GCS bucket
COPY out/x64.release/chrome.exe C:/neural-chromium/bin/chrome.exe
COPY out/x64.release/chrome.dll C:/neural-chromium/bin/chrome.dll
COPY out/x64.release/chrome_100_percent.pak C:/neural-chromium/bin/chrome_100_percent.pak
COPY out/x64.release/resources.pak C:/neural-chromium/bin/resources.pak
COPY out/x64.release/icudtl.dat C:/neural-chromium/bin/icudtl.dat
COPY out/x64.release/snapshot_blob.bin C:/neural-chromium/bin/snapshot_blob.bin
COPY out/x64.release/v8_context_snapshot.bin C:/neural-chromium/bin/v8_context_snapshot.bin

# Copy SDK
COPY src/neural_chromium C:/neural-chromium/sdk/neural_chromium
COPY src/neural_chromium/mcp_server.py C:/neural-chromium/bin/mcp_server.py

# Install dependencies
RUN pip install mcp websocket-client requests

# Expose CDP
EXPOSE 9222

# Entrypoint: Run MCP Server (which launches Chrome)
# Note: We need a wrapper script to launch execution
COPY deployment/entrypoint.bat C:/neural-chromium/entrypoint.bat

ENTRYPOINT ["C:/neural-chromium/entrypoint.bat"]
