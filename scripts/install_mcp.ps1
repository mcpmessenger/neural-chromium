$ErrorActionPreference = "Stop"

$ConfigPath = "$env:APPDATA\Claude\claude_desktop_config.json"
$ConfigDir = Split-Path $ConfigPath
if (-not (Test-Path $ConfigDir)) {
    Write-Host "Creating Claude Config Directory: $ConfigDir"
    New-Item -ItemType Directory -Path $ConfigDir | Out-Null
}

$NeuralConfig = @{
    "command" = "C:\operation-greenfield\neural-chromium\src\run_mcp_server.bat"
    "args"    = @()
    "cwd"     = "C:\operation-greenfield\neural-chromium\src"
}

# 3. Read or Create Config File
$Config = @{ "mcpServers" = @{} }
if (Test-Path $ConfigPath) {
    try {
        $Content = Get-Content $ConfigPath -Raw
        if (-not [string]::IsNullOrWhiteSpace($Content)) {
            $Config = $Content | ConvertFrom-Json -AsHashtable
        }
        if (-not $Config.ContainsKey("mcpServers")) {
            $Config["mcpServers"] = @{}
        }
    }
    catch {
        Write-Warning "Existing config file was invalid JSON. Overwriting."
    }
}

# 4. Inject Neural-Chromium Config
Write-Host "Injecting 'neural-chromium' configuration..."
$Config["mcpServers"]["neural-chromium"] = $NeuralConfig

# 5. Write Back
$Json = $Config | ConvertTo-Json -Depth 10
Set-Content -Path $ConfigPath -Value $Json

Write-Host "---------------------------------------------------"
Write-Host "SUCCESS: Claude Desktop Configuration Updated!"
Write-Host "Path: $ConfigPath"
Write-Host "---------------------------------------------------"
Write-Host "Please RESTART Claude Desktop app to pick up changes."
