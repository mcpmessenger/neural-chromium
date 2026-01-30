# 🐛 Bug Bounty: Neural Audio Signal Chain Debugging

**Branch:** `audio-debugging`
**Component:** `NeuralAudioHook` (C++) -> `NexusAgent` (Python)
**Severity:** High (Blocks reliable Voice Control)

## 🚨 The Issue
The **Neural Audio Hook** successfully captures audio from Chrome's `AudioRendererImpl`, but the signal is **extremely attenuated (quiet)** and prone to **Whisper Hallucinations** when amplified.

### Symptoms
1.  **Low Volume:** Captured audio levels (RMS) are ~30-50 (INT16 scale), whereas a normal "speaking" voice should be ~1000-5000.
2.  **Hallucinations:** When amplified (150x Gain), the noise floor causes OpenAI Whisper to hallucinate phrases like *"MBC News"*, *"Click the button"*, *"Subscribe"*, or *"See you in the next video"*.
3.  **Sample Rate:** Validated as 48kHz (Chrome standard), confirmed fix for "Slow Motion" voice.

## 🛠️ Current Workarounds (Implemented in this branch)
To make the system minimally functional, we applied these aggressive patches:
1.  **Massive Digital Gain:** `NexusAgent.py` applies a **150x Digital Gain** to the signal before sending to OpenAI. This boosts the signal but destroys the Signal-to-Noise Ratio (SNR).
2.  **Hallucination Filter:** `NexusAgent.py` has a hardcoded blocklist (`skip_phrases`) to drop common Whisper hallucinations.
3.  **Ultra-Low Loopback:** `test_mic.html` sets volume to `0.001` to keep the data flowing to `AudioRendererImpl` without causing a feedback screech, but this might be contributing to the low capture volume.

## 🎯 The Goal (Help Wanted)
We need a robust fix for the signal chain that doesn't rely on 150x digital gain.

### Investigation Areas
1.  **AudioRendererImpl Tap Point:** Are we tapping `AudioBus` *after* volume attenuation? We need to tap *before* volume or ensuring `test_mic.html` volume doesn't affect the capture.
2.  **Direct Mic Access:** Can `NexusAgent` read the Microphone directly (PyAudio) instead of relying on the Chrome Loopback Hack?
    *   *Constraint:* We prefer Chrome capturing to support "Tab Audio" analysis in the future.
3.  **Noise Suppression:** Implementing a standard VAD (WebRTC VAD) or Noise Gate in Python to silence the "Hallucination" floor.

## 📂 Key Files
*   `src/glazyr/nexus_agent.py`: Contains the `AudioCortexClient`, `on_speech_complete` (Amplification), and `process_text_command` (Hallucination Filter).
*   `src/media/renderers/audio_renderer_impl.cc`: The C++ hook writing to Shared Memory.
*   `src/test_mic.html`: The user-facing loopback driver.
