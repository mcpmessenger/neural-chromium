# Neural-Chromium Overlay - Snapshot Scripts

This directory contains PowerShell scripts for managing the overlay pattern.

## Scripts

### `apply_snapshot.ps1`
Copies modified files from the overlay to the main Chromium source tree.

**Usage:**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\apply_snapshot.ps1
```

**What it does:**
- Copies `src/nexus_agent.py` → `neural-chromium/src/`
- Copies `src/config.json` → `neural-chromium/src/`
- Copies `src/settings.html` → `neural-chromium/src/`
- Copies modified C++ files to their respective locations
- Preserves directory structure

### `save_snapshot.ps1`
Copies modified files from the main Chromium tree back to the overlay for version control.

**Usage:**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\save_snapshot.ps1
```

**What it does:**
- Scans tracked files in `$TrackedFiles` array
- Copies them from `neural-chromium/src/` to `neural-chromium-overlay/src/`
- Preserves directory structure
- Updates the overlay repository

## Tracked Files

Currently tracked files (defined in `save_snapshot.ps1`):
- `nexus_agent.py`
- `config.json`
- `settings.html`
- `content/browser/speech/network_speech_recognition_engine_impl.cc`

To track additional files, edit the `$TrackedFiles` array in `save_snapshot.ps1`.

## Workflow

### Making Changes

1. Edit files in `neural-chromium-overlay/src/`
2. Run `apply_snapshot.ps1` to copy to main tree
3. Build Chrome: `autoninja -C out\AgentDebug chrome`
4. Test changes

### Saving Changes

1. Make changes in `neural-chromium/src/`
2. Run `save_snapshot.ps1` to copy back to overlay
3. Commit overlay changes to git

## Why This Pattern?

The overlay pattern allows us to:
- ✅ Version control only our modifications (not entire Chromium)
- ✅ Keep repository size small (~100KB vs ~50GB)
- ✅ Easily sync with upstream Chromium updates
- ✅ Share modifications without sharing entire codebase
