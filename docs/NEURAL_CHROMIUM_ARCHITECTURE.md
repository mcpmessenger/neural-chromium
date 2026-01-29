# Neural Chromium Architecture

> **Notice**: This document describes the *Neural Chromium* custom patches applied to the Chromium source code to enable Agentic capabilities.

## 1. Visual Cortex (Video Stream)
**Purpose**: Expose high-performance, low-latency video frames from the browser compositor directly to the Agent via Shared Memory, bypassing screen capture APIs.

### Implementation Details
- **Hook Location**: `components/viz/service/display/software_output_device.cc` (and Windows specific subclass `software_output_device_win.cc`).
- **Mechanism**:
    - We intercept the `EndPaint` or `SwapBuffers` call in the software compositor.
    - We copy the `SkBitmap` or pixel buffer to a named Shared Memory segment.
- **Shared Memory**: `Local\NeuralChromium_VisualCortex_V3`
    - **Header**: Contains `magic_number` (0x4E455552), `width`, `height`, `frame_index`, `timestamp`.
    - **Data**: Raw BGRA pixels.
- **Latency**: < 16ms (Target). effectively Zero-Copy relative to OS capture.

## 2. Audio Stream (Hearing)
**Purpose**: Allow the Agent to "hear" system audio (specifically microphone input) *always*, even when the browser is in the background or the user is typing, without requiring an active WebRTC tab.

### Implementation Details
- **Hook Location**: `media/audio/win/audio_low_latency_input_win.cc`
    - Class: `WASAPIAudioInputStream`
    - Method: `OnData` (The real-time audio callback from Windows WASAPI).
- **Mechanism**:
    - We inject a call to `AgentSharedMemory::WriteAudio` inside the high-priority audio thread.
    - This captures raw PCM samples *before* they are processed or discarded by Chrome's upper layers.
- **Shared Memory**: `Local\NeuralChromium_Audio_V1`
    - **Format**: Raw PCM (typically Float32 or Int16 depending on OS negotiation).
    - **Ring Buffer**: The shared memory acts as a high-speed ring buffer.
- **Always-On Strategy**:
    - `AgentCommandManager` instantiates a "Dummy" `AgentAudioStream` (`agent_audio_stream.cc`) in the background.
    - This stream opens a WASAPI loopback/input stream but discards the data in the browser (No-Op `OnData`).
    - **Crucially**, simply *opening* this stream activates the hardware and triggers our `AudioLowLatencyInputWin` hook, which then duplicates the data to Shared Memory.

## 3. Agent Command Manager (Brain)
**Purpose**: Central logic hub for Agent interactions within the Browser Process.

### Implementation Details
- **Location**: `chrome/browser/ui/agent_command_manager/`
- **Singleton**: `AgentCommandManager::GetInstance()`
- **Initialization**:
    - Called from `ChromeBrowserMainExtraPartsViews::PreProfileInit` (Early UI startup).
    - Initializes Shared Memory (`VisualCortex`, `Audio`, `Command`).
    - Starts the `AgentAudioStream`.
- **Mode Switching**:
    - Toggles `OmniboxMode` between `NORMAL` (Google Search) and `AGENT` (Neural Input).
    - Updates UI (Placeholder text, Icons) via `AgentModeButton`.
- **Navigation Interception**:
    - Hooks `ChromeOmniboxClient` to intercept Enter key presses in the Omnibox.
    - If in `AGENT` mode, routes text to the Agent via Shared Memory IPC instead of navigating.

## 4. Shared Memory IPC
- **Library**: `components/agent_interface/`
- **Files**: `agent_shared_memory.h/.cc`, `agent_command_shared_memory.h/.cc`.
- **Usage**: Provides a thread-safe (or "safe enough" for atomic reads) bridge between the C++ Browser Process and the external Python Agent process.
