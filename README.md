# Neural-Chromium

**Neural-Chromium** is an experimental build of the Chromium browser designed for high-fidelity AI agent integration. It exposes a low-latency perception and action interface ("System Interface") directly from the browser process, enabling next-generation research into synthetic user interaction models.

## Mission
To bridge the gap between AI agents and web interfaces by removing the overhead of accessibility layers and screen scraping, providing agents with:
*   **Direct Perception**: Zero-copy access to the rendering pipeline.
*   **High-Fidelity Action**: Millisecond-level input injection.
*   **Deep State Awareness**: Direct access to the DOM and internal browser states.

## Architecture
*   **Embedded Agent Process**: A dedicated process lifecycle coupled with the browser execution environment.
*   **Zero-Copy Vision**: Direct shared memory access to the composition surface (Viz) for sub-16ms inference latency.
*   **High-Precision Input**: Coordinate transformation pipeline for mapping agent actions to internal browser events.

## Repository Structure
This project utilizes a **Source Overlay** pattern to maintain a lightweight footprint while modifying the massive Chromium codebase.
*   `src/`: Contains the specific modified files that overlay the official Chromium source.
*   `challenges_report.md`: Deep dive into build system analysis and blocks.
*   `development_log.md`: Implementation task checklist.
*   `scripts/`: Utilities for applying changes to and capturing snapshots from a full Chromium build.

## 🧠 Current Status: Local Intelligence Active (Day 5)

### ✅ Auditory Cortex - Audio Hook (PROVEN WORKING)
*   **Feature**: Native Audio Bridge (`NetworkSpeechRecognitionEngineImpl` → `chrome_debug.log` → `nexus_agent.py`)
*   **Status**: **Audio capture verified** - Chrome successfully logs 800+ base64-encoded PCM audio chunks
*   **Evidence**: `LOG(WARNING) << "AUDIO_DATA:" << b64_pcm;` confirmed working in production
*   **Capabilities**: 
    - Real-time microphone capture via Web Speech API
    - Base64 PCM encoding (16kHz, 16-bit, mono)
    - File-based IPC to Python agent

### ✅ Visual Cortex - Vision Hook (PROVEN WORKING)
*   **Feature**: Shared Memory Bridge (`RenderWidgetHostViewAura` → `Local\NeuralChromium_VisualCortex_V3` → `nexus_agent.py`)
*   **Status**: **Vision Verified** (60+ FPS) - C++ captures frames at display refresh rate.
*   **Performance**:
    - **Idle**: ~1-15 FPS (Power efficient, damage-driven rendering)
    - **Active**: **60-65 FPS** (Scrolling/Video playback)
*   **Requirement**: Must run with `--in-process-gpu` to enable frame capture in the correct process context.
*   **Evidence**: "Nexus, describe screen" returns accurate UI descriptions; Auto-Benchmark confirms high throughput.
*   **Capabilities**:
    - High-performance (Shared Memory) frame streaming
    - Zero-copy architecture using NULL DACL for cross-process access
    - Zero-copy architecture using NULL DACL for cross-process access
    - **Dynamic Resolution**: Automatic downscaling of large inputs for faster VLM inference.

### ✅ Local Intelligence - Agent Core (PROVEN WORKING)
*   **Feature**: Local Vision-Language Model Integration (`Ollama` + `llama3.2-vision`)
*   **Status**: **Functional** - Agent observes screen, plans actions, and executes them.
*   **Architecture**: 
    - **Observe**: Screen captured via Visual Cortex (Shared Memory).
    - **Orient**: Image resized and sent to local Ollama instance.
    - **Decide**: Llama 3.2 Vision outputs JSON action plan (`click`, `type`, `scroll`).
    - **Act**: Nexus Agent executes browser commands via Extension or CDP.
*   **Capabilities**:
    - **Privacy-First**: No images leave the local machine.
    - **Resilience**: Handles repeated inputs and connection drops.

### ✅ Transcription Pipeline (WORKING)
*   **Status**: Audio continuously processed by Python agent
*   **Fix**: Implemented file stat refresh to detect log file growth on Windows
*   **Tuning**: VAD Threshold lowered to 1500 (High Sensitivity), Latency reduced to ~1.5s
*   **Evidence**: Wake word "Nexus" triggers reliably.

## Running the Agent (Local VLM Mode)
To ensure all hooks (Audio + Vision) and the Local LLM work correctly, use the provided launch script:
```powershell
.\RESTART_ALL.bat
```
This script handles:
1.  **Ollama**: Starts the local inference server (if not running).
2.  **Chrome**: Launches Neural-Chromium with `--in-process-gpu` (Crucial for Vision).
3.  **Agent**: Launches `nexus_agent.py` to bridge Vision/Audio to the VLM.

**Note**: You must have [Ollama](https://ollama.com/) installed and `llama3.2-vision` pulled (`ollama pull llama3.2-vision`).


## Getting Started

### Prerequisites
*   Windows 10/11 (64-bit)
*   **Critical:** The source code path must **NOT** contain spaces (e.g., use `C:\neural-chromium`, NOT `C:\My Projects\neural-chromium`). Chromium build tools (gn, ninja) will fail if there are spaces in the path.
*   Visual Studio 2022 (with Desktop development with C++, MFC/ATL support)
*   Visual Studio 2022 (with Desktop development with C++, MFC/ATL support)
*   [depot_tools](https://commondatastorage.googleapis.com/chrome-infra-docs/flat/depot_tools/docs/html/depot_tools_tutorial.html) installed and in your PATH.
*   [Ollama](https://ollama.com/) installed for local VLM inference.

### 1. Fetch Chromium Source
Create a working directory (e.g., `c:\Operation Greenfield\neural-chromium`) and fetch the code. **Note:** This will download >50GB of data.

```powershell
mkdir c:\Operation Greenfield\neural-chromium
cd c:\Operation Greenfield\neural-chromium
fetch chromium
cd src
git checkout main
gclient sync
```

### 2. Apply the Overlay
Apply the Neural-Chromium modifications to your vanilla source tree.

```powershell
# From this repository's root
powershell -ExecutionPolicy Bypass -File scripts\apply_snapshot.ps1
```
*Note: You may need to adjust paths in `scripts\apply_snapshot.ps1` if your directory structure differs.*

### 3. Build
Generate build files and compile.

```powershell
cd c:\Operation Greenfield\neural-chromium\src
gn gen out\AgentDebug
autoninja -C out\AgentDebug chrome
```

## Contributing
We welcome contributions! Because we don't host the full Chromium source, the workflow is slightly different:

1.  **Modify**: Make your changes in your full Chromium `src` directory.
2.  **Track**: If you modified a new file, add its path to `$TrackedFiles` in `scripts/save_snapshot.ps1`.
3.  **Snapshot**: Run the snapshot script to copy your changes back to this overlay repository.
    ```powershell
    powershell -ExecutionPolicy Bypass -File scripts\save_snapshot.ps1
    ```
4.  **Commit**: Commit the updated files in this repository and open a Pull Request.

## Discussion & Roadmap
We are currently focusing on:
*   [ ] Reducing IPC latency for the Agent Interface.
*   [ ] Exposing the localized accessibility tree to the Agent Process.
*   [ ] Headless operation with full GPU support.

Join the discussion in the [Issues](https://github.com/mcpmessenger/neural-chromium/issues) tab!

## Disclaimer
This project is for educational and research purposes only, focusing on the intersection of browser engines and autonomous agents. It is not affiliated with Google or the Chromium project.
