$DepotToolsPath = "c:\Operation Greenfield\depot_tools"
$env:PATH = "$DepotToolsPath;$env:PATH"
$env:DEPOT_TOOLS_WIN_TOOLCHAIN = 0
Write-Host "Initialized Neural-Chromium environment. Depot Tools added to PATH."
