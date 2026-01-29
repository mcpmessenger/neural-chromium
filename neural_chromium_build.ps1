# Helper Script to Build and Debug Neural Chromium
# Usage: .\neural_chromium_build.ps1 [-Build] [-Debug] [-Clean]

param (
    [switch]$Build,
    [switch]$Debug,
    [switch]$Clean
)

$DEPOT_TOOLS = "c:\operation-greenfield\depot_tools"
$SRC_DIR = "c:\operation-greenfield\neural-chromium\src"
$OUT_DIR = "out/AgentDebug"

Set-Location $SRC_DIR

if ($Clean) {
    Write-Host "Cleaning Build Directory..." -ForegroundColor Yellow
    & "$DEPOT_TOOLS\gn.bat" clean $OUT_DIR
}

if ($Build) {
    Write-Host "Building Chrome..." -ForegroundColor Cyan
    & "$DEPOT_TOOLS\autoninja.bat" -C $OUT_DIR chrome
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Build Failed!" -ForegroundColor Red
        exit
    }
}

if ($Debug) {
    Write-Host "Launching Chrome with Debug Logs..." -ForegroundColor Green
    $CHROME_EXE = "$OUT_DIR\chrome.exe"
    & $CHROME_EXE --enable-logging --v=1 --user-data-dir="C:\tmp\neural_chrome_v2"
}
