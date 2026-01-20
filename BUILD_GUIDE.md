# Neural-Chromium Build & Run Guide

## Quick Start (Already Built)

If you already have the Chrome binary built at `out\AgentDebug\chrome.exe`:

1. **Configure API Keys** (first time only)
   ```powershell
   # Double-click this file:
   open_settings.bat
   
   # Or open directly:
   start settings.html
   ```
   - Fill in your OpenAI/Anthropic/Gemini keys
   - Click "Save Settings"
   - Move downloaded `config.json` to this directory

2. **Launch Everything**
   ```powershell
   # Double-click this file:
   START_NEURAL_CHROME.bat
   ```
   This will:
   - ✅ Check for config.json (opens settings if missing)
   - ✅ Start Python agent (`nexus_agent.py`)
   - ✅ Launch modified Chrome with audio hook
   - ✅ Show status in console

---

## Full Build from Source

If you need to rebuild Chrome with the Neural modifications:

### Prerequisites
- Windows 10/11 (64-bit)
- Visual Studio 2022 (Desktop C++ workload)
- ~100GB free disk space
- ~8GB RAM minimum

### Step 1: Apply the Overlay

```powershell
cd c:\operation-greenfield\neural-chromium-overlay

# Copy modified files to Chromium source tree
powershell -ExecutionPolicy Bypass -File scripts\apply_snapshot.ps1
```

This copies:
- `src/nexus_agent.py` → Agent host
- `src/config.json` → API keys
- `src/settings.html` → Settings UI
- `src/content/browser/speech/network_speech_recognition_engine_impl.cc` → Audio hook

### Step 2: Generate Build Files

```powershell
cd c:\operation-greenfield\neural-chromium\src

# Generate ninja build files
gn gen out\AgentDebug
```

### Step 3: Build Chrome

```powershell
# Build Chrome (this takes 1-3 hours on first build)
autoninja -C out\AgentDebug chrome
```

**Build time:**
- First build: 1-3 hours
- Incremental builds: 5-15 minutes

### Step 4: Test the Build

```powershell
# Launch with our script
.\START_NEURAL_CHROME.bat
```

---

## What Gets Built

### Modified Chrome Binary
- **Location**: `out\AgentDebug\chrome.exe`
- **Size**: ~500MB
- **Modifications**:
  - Audio hook in `NetworkSpeechRecognitionEngineImpl`
  - Logs raw PCM audio to `chrome_debug.log`
  - Base64 encoded for Python agent

### Python Agent
- **File**: `nexus_agent.py`
- **Dependencies**: 
  ```powershell
  pip install openai google-generativeai anthropic pillow
  ```
- **Features**:
  - Tails Chrome's debug log
  - Transcribes audio via Gemini/OpenAI
  - Types transcriptions via Windows SendInput
  - Voice commands ("Computer, describe screen")

### Settings UI
- **File**: `settings.html`
- **Type**: Standalone HTML (no build needed)
- **Purpose**: Manage API keys visually

---

## Build Troubleshooting

### Error: "gn: command not found"
```powershell
# Add depot_tools to PATH
$env:PATH = "c:\operation-greenfield\depot_tools;$env:PATH"
```

### Error: "ninja: cannot make progress"
```powershell
# Kill Chrome processes holding file locks
taskkill /F /IM chrome.exe
taskkill /F /IM python.exe

# Retry build
autoninja -C out\AgentDebug chrome
```

### Error: "permission denied" during link
Chrome is still running. Kill all instances:
```powershell
Get-Process chrome | Stop-Process -Force
```

### Build is too slow
Use more CPU cores:
```powershell
# Use all cores
autoninja -C out\AgentDebug -j 16 chrome
```

---

## Development Workflow

### Making Changes

1. **Edit files** in `neural-chromium-overlay\src\`
2. **Apply changes** to main tree:
   ```powershell
   cd neural-chromium-overlay
   powershell -ExecutionPolicy Bypass -File scripts\apply_snapshot.ps1
   ```
3. **Rebuild** (incremental):
   ```powershell
   cd ..\neural-chromium\src
   autoninja -C out\AgentDebug chrome
   ```

### Saving Changes Back

After modifying files in the main Chromium tree:
```powershell
cd neural-chromium-overlay
powershell -ExecutionPolicy Bypass -File scripts\save_snapshot.ps1
```

This copies your changes back to the overlay for version control.

---

## No Build Required

The **settings page** (`settings.html`) is **pure HTML/CSS/JavaScript** - no build step needed!

Just:
1. Double-click `open_settings.bat`
2. Configure keys
3. Save config.json

That's it! The settings UI works immediately.

---

## Directory Structure

```
neural-chromium-overlay/
├── src/
│   ├── settings.html          ← Settings UI (no build)
│   ├── open_settings.bat      ← Opens settings
│   ├── START_NEURAL_CHROME.bat ← Main launcher
│   ├── nexus_agent.py         ← Python agent
│   ├── config.json            ← API keys (created by settings)
│   └── content/browser/speech/
│       └── network_speech_recognition_engine_impl.cc  ← Audio hook
├── scripts/
│   ├── apply_snapshot.ps1     ← Copy to main tree
│   └── save_snapshot.ps1      ← Save changes back
└── README.md

neural-chromium/src/
└── out/AgentDebug/
    └── chrome.exe             ← Built binary (after build)
```

---

## Summary

**If Chrome is already built:**
- Just run `START_NEURAL_CHROME.bat` ✅

**If you need to rebuild Chrome:**
1. Apply overlay: `scripts\apply_snapshot.ps1`
2. Generate build: `gn gen out\AgentDebug`
3. Build: `autoninja -C out\AgentDebug chrome`
4. Run: `START_NEURAL_CHROME.bat`

**Settings page:**
- No build needed - it's pure HTML!
- Just open `settings.html` in any browser
