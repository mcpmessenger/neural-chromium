import sys
import time
import struct
import mmap
import threading
import numpy as np
import collections
from PIL import Image
import io
import io
import os
try:
    import winsound
except ImportError:
    winsound = None # Linux fallback (not needed on Windows but good practice)

try:
    import webrtcvad
    import webrtcvad
    HAS_WEBRTC_VAD = True
except ImportError:
    HAS_WEBRTC_VAD = False
    print("⚠️  webrtcvad not found. Using Energy-based VAD fallback.")

import re
try:
    import pyautogui
    pyautogui.FAILSAFE = True # Drag mouse to corner to abort
except ImportError:
    print("⚠️ pyautogui not found. Mouse control disabled.")
    pyautogui = None

class SimpleVad:
    def is_speech(self, chunk, rate):
        # Simple energy check: if > 1% max amplitude, treat as speech
        # Chunk is bytes int16.
        data = np.frombuffer(chunk, dtype=np.int16)
        rms = np.sqrt(np.mean(data**2))
        return rms > 50  # Lowered threshold for quieter audio

    def get_rms(self, chunk):
        data = np.frombuffer(chunk, dtype=np.int16)
        return np.sqrt(np.mean(data**2))

# Shared Memory Constants
SHM_SIZE = 32 * 1024 * 1024  # 32MB
VIDEO_SHM_SIZE = 1920 * 1080 * 4 + 256 # Exactly match C++ size
MAGIC_NUMBER = 0x4E43524D     # "NCRM" (Frame Header)
VIDEO_MAGIC_NUMBER = 0x5649444F # "VIDO" (Video Header)
AUDIO_MAGIC_NUMBER = 0x41554449 # "AUDI" (Audio Header)

class AgentSharedMemory:
    def __init__(self, name="NeuralChromium_Agent_SharedMem"):
        self.name = name
        self.shm = mmap.mmap(-1, SHM_SIZE, tagname=name)
        self.audio_buffer = collections.deque(maxlen=16000 * 5) # 5 seconds
        if HAS_WEBRTC_VAD:
            self.vad = webrtcvad.Vad(3) # Aggressiveness: 0-3
        else:
            self.vad = SimpleVad()

        # Video Shared Memory (Separate Header)
        try:
            self.video_shm = mmap.mmap(-1, VIDEO_SHM_SIZE, tagname="NeuralChromium_Video")
            print("📹 Video Return Path Connected")
        except Exception as e:
            print(f"⚠️ Video Path Init: {e} (Will retry)")
            self.video_shm = None
        
        self.last_video_ts = 0
        self.read_count = 0

    def connect_video(self):
        if self.video_shm: return True
        try:
            self.video_shm = mmap.mmap(-1, VIDEO_SHM_SIZE, tagname="NeuralChromium_Video")
            print("\n📹 >>> Video Return Path Connected! <<<")
            return True
        except Exception:
            return False

    def read_header(self):
        # Read header (first 256 bytes reserved)
        self.shm.seek(0)
        data = self.shm.read(32)
        # struct FrameHeader {
        #   uint32_t magic_number;
        #   uint32_t width;
        #   uint32_t height;
        #   uint32_t stride;
        #   uint32_t format;
        #   int64_t timestamp_us;
        # };
        magic, width, height, stride, fmt, ts = struct.unpack('IIIIIq', data)
        return {
            'magic': magic,
            'width': width,
            'height': height,
            'stride': stride,
            'format': fmt,
            'timestamp': ts
        }

    def read_audio_header(self):
        # Audio header is at offset 16MB (halfway point)
        self.shm.seek(16 * 1024 * 1024)
        data = self.shm.read(24)
        # struct AudioHeader {
        #   uint32_t magic_number;
        #   uint32_t sample_rate;
        #   uint32_t channels;
        #   uint32_t frames;
        #   int64_t timestamp_us;
        # };
        try:
            magic, rate, channels, frames, ts = struct.unpack('<IIIIq', data)
            return {
                'magic': magic,
                'rate': rate,
                'channels': channels,
                'frames': frames,
                'timestamp': ts
            }
        except Exception as e:
            return None

    def read_audio_data(self, frames):
        # Audio data follows header at 16MB + 256 bytes
        offset = 16 * 1024 * 1024 + 256
        size = frames * 4 # float32 = 4 bytes
        self.shm.seek(offset)
        return self.shm.read(size)

    def read_video_frame(self):
        if not self.video_shm: return None
        
        # Read Header (First 64 bytes)
        self.video_shm.seek(0)
        header_data = self.video_shm.read(32)
        # struct VideoHeader {
        #   uint32_t magic;
        #   uint32_t width;
        #   uint32_t height;
        #   uint32_t stride;
        #   uint32_t format; // 1=ARGB, 2=ABGR
        #   int64_t timestamp_us;
        # };
        try:
            magic, width, height, stride, fmt, ts = struct.unpack('IIIIIq', header_data)
            self.read_count += 1
            if magic != VIDEO_MAGIC_NUMBER:
                if self.read_count % 200 == 0:
                     print(f"⚠️ Video Wait: Magic={hex(magic)} (Expected {hex(VIDEO_MAGIC_NUMBER)})")
                return None
            
            # Read Pixel Data (Offset 256)
            pixel_size = width * height * 4
            if pixel_size > VIDEO_SHM_SIZE - 256:
                return None # Safety check

            self.video_shm.seek(256)
            pixel_data = self.video_shm.read(pixel_size)
            
            return {
                'width': width,
                'height': height,
                'stride': stride,
                'format': fmt,
                'timestamp': ts,
                'data': pixel_data
            }
        except Exception as e:
            self.read_count += 1
            if self.read_count % 200 == 0:
                print(f"⚠️ Video Read Error: {e}")
            return None

class NeuralAgent:
    def __init__(self):
        self.memory = AgentSharedMemory()
        self.running = True
        self.last_audio_ts = 0
        self.frames = []
        self.silence_frames = 0
        self.frame_count = 0
        self.video_status = "No Signal"
        self.last_vlm_ts = 0
        self.vlm_busy = False
        self.is_recording = False
        self.last_sample_rate = 48000
        self.last_channels = 1
        self.last_stale_print = 0
        self.recording_cooldown = 0 # Debounce for PTT release
        self.recording_start_time = 0
        self.last_state = -1
        self.stuck_frames = 0
        
        # Text Return Path (Agent -> Browser)
        try:
            self.text_shm = mmap.mmap(-1, 4096, tagname="NeuralChromium_Input_Text")
            print("📝 Text Return Path Connected")
        except Exception as e:
            print(f"⚠️ Text Path Failed (Chrome not ready?): {e}")
            self.text_shm = None

    def write_text_to_browser(self, text):
        if not self.text_shm: return
        try:
            # Protocol: Revision (u32), Length (u32), Data
            self.text_shm.seek(0)
            current_rev_bytes = self.text_shm.read(4)
            self.text_shm.seek(0)
            current_rev = struct.unpack('I', current_rev_bytes)[0]
            
            new_rev = current_rev + 1
            encoded = text.encode('utf-8')
            length = len(encoded)
            
            print(f"📝 Writing Text to SHM (Rev {current_rev} -> {new_rev}): '{text}'")

            # Pack: Rev, Len, Text
            # We explicitly overwrite
            self.text_shm.write(struct.pack('II', new_rev, length))
            self.text_shm.write(encoded)
            
            # WAKE UP CHROME! (Force a Compositor Frame)
            # If the page is static, Chrome stops calling OnFrame/Viz.
            # We need an input event to force a tick so it reads the SHM.
            self.wake_up_browser()
            
        except Exception as e:
            print(f"Write Failed: {e}")

    def wake_up_browser(self):
        try:
            if pyautogui:
                # Force Click at Top-Left to ensure Chrome is Focused and Awake
                # This fixes "Lazy Browser" ignoring Shared Memory updates.
                current_x, current_y = pyautogui.position()
                pyautogui.click(1, 1) 
                pyautogui.moveTo(current_x, current_y) # Restore position
        except: pass

    def run(self):
        print("🧠 Neural Agent Connected via Shared Memory")
        print("🔊 Listening for Audio (Neural Audio Hook v2)...")
        
        # Poll for State Shared Memory (created by Chrome)
        print("Waiting for UI Control State...")
        self.state_shm = None
        while not self.state_shm:
            try:
                self.state_shm = mmap.mmap(-1, 4, tagname="NeuralChromium_State")
                print("✅ Control State Connected!")
            except:
                time.sleep(1)
        
        # Start Terminal Input Thread (Fallback)
        self.start_terminal_listener()

        while self.running:
            # check state
            self.state_shm.seek(0)
            state = struct.unpack('i', self.state_shm.read(4))[0]
            
            if state != self.last_state:
                print(f"🔄 State Change: {self.last_state} -> {state}")
                self.last_state = state
            
            # Always try to connect/process video (for debug/preview)
            if not self.memory.video_shm:
                self.memory.connect_video()
            self.process_vision()
            
            # Check for manual file commands (Fallback for Audio)
            self.check_command_file()

            # Push-to-Talk Logic (Brain Switch) with Hysteresis
            if state == 1:
                # User pressed "Microphone" (Brain ON)
                if not self.is_recording:
                     print(f"\n🔴 RECORDING (Brain ON)... [TS={self.last_audio_ts}]")
                     if self.last_state != -1: # Don't beep on first loop init
                          try: winsound.Beep(1000, 100) # High Beep on Start
                          except: pass
                     self.is_recording = True
                     self.recording_start_time = time.time() # Timestamp start
                     self.frames = [] # Start fresh
                
                # Reset Cooldown (Keep alive for 1s after release)
                self.recording_cooldown = 100 # ~1.0s tail (Throttled loop)
                
                # CRITICAL: Fast Loop (No Sleep) to beat Windows 15ms Timer Resolution
                self.process_audio()
                continue 
                
            elif self.is_recording:
                # User released button, but we check cooldown/debounce AND Minimum Duration
                # Force at least 2.0 seconds of recording time to prevent premature triggers.
                if self.recording_cooldown > 0 or (time.time() - self.recording_start_time < 2.0):
                    self.recording_cooldown -= 1
                    # Continue capturing "Tail" audio
                    self.process_audio()
                    # We can sleep a tiny bit here since we are just capturing tail, 
                    # but safer to stay fast to avoid drops even in tail.
                    time.sleep(0.01) # Throttle to 10ms to make '30 frames' last 300ms
                    continue
                else:
                    # Cooldown expired -> Transcribe
                    print("\n🛑 STOP (Brain OFF) -> Transcribing...")
                    try: winsound.Beep(500, 100) # Low Beep on Stop
                    except: pass
                    self.is_recording = False
                    self.transcribe_buffer()
            else:
                # Idle Mode
                if self.frame_count % 100 == 0:
                     sys.stdout.write(f"\r💤 Idle (Toggle Brain to Talk) Video: {self.video_status} \033[K")
                     sys.stdout.flush()
                self.frame_count += 1
                
                # Vision Pipeline (Only when not recording)
                self.process_vision()
                time.sleep(0.01) # Standard UI poll rate

    def process_vision(self):
        frame = self.memory.read_video_frame()
        if not frame: return

        # Simple debug: Print FPS if frame changes
        # (TODO: Add VLM Logic here)
        if frame['timestamp'] != self.memory.last_video_ts:
             self.memory.last_video_ts = frame['timestamp']
             self.video_status = f"{frame['width']}x{frame['height']}"
             
             # --- VLM INFERENCE LOOP (Mutex Protected) ---
             current_time = time.time()
             if not self.vlm_busy and (current_time - self.last_vlm_ts > 1.0): 
                 self.last_vlm_ts = current_time
                 self.vlm_busy = True # Lock
                 
                 # 1. Prepare Image
                 img = Image.frombytes('RGBA', (frame['width'], frame['height']), frame['data'], 'raw', 'BGRA')
                 img.thumbnail((512, 512)) 
                 
                 # 2. Async Query (Threaded)
                 def vlm_task():
                     try:
                         desc = self.query_ollama_vision("Describe this screen in 5 words.", img)
                         if desc:
                             print(f"\n👁️ VLM Saw: {desc}")
                     finally:
                         self.vlm_busy = False # Unlock
                 
                 threading.Thread(target=vlm_task, daemon=True).start()
             # ----------------------------------

            # --- DEBUG: Save 1 frame to disk to verify ---
             if not os.path.exists("debug_frame.png"):
                 # Create Image from BGRA buffer
                 # Note: Chrome usually sends BGRA on Windows
                 img = Image.frombytes('RGBA', (frame['width'], frame['height']), frame['data'], 'raw', 'BGRA')
                 img.save("debug_frame.png")
                 print("\n📸 Saved debug_frame.png (Sanity Check)")
            # ---------------------------------------------100Hz

    def process_audio(self):
        header = self.memory.read_audio_header()
        if not header:
            print("DEBUG: No Audio Header found (Read failed)") 
            return
            
        if header['magic'] != AUDIO_MAGIC_NUMBER:
            # Only print if it's NOT zero (0x0 means just not initialized yet)
            if header['magic'] != 0:
                 print(f"DEBUG: Magic Mismatch: Read {hex(header['magic'])} (Expected {hex(AUDIO_MAGIC_NUMBER)})")
            else:
                 # Magic is 0. Chrome hasn't started writing audio yet.
                 if self.frame_count % 100 == 0:
                     print(f"⚠️ Audio Idle: SharedMem=0x0 (Chrome Mic Inactive?)")
            return
        
        # print(f"DEBUG: Header Found! TS={header['timestamp']} Last={self.last_audio_ts} Frames={header['frames']}")
        # print(f"DEBUG: Header Found! TS={header['timestamp']} Last={self.last_audio_ts} Frames={header['frames']}")
        self.last_sample_rate = header['rate']
        self.last_channels = header['channels']
        
        # Debug: Print header occasionally to verify we are reading *something*
        # Reduced frequency to avoid spamming the console
        if self.frame_count % 500 == 0:
             sys.stdout.write(f"\nDEBUG: Reading SHM Header: Magic={hex(header['magic'])} Rate={header['rate']} Frames={header['frames']}\n")
             sys.stdout.flush()

        # Check for Reset (New Page Load = New Audio Source = TS reset to 0)
        # If timestamp jumps backwards significantly, accept it.
        if header['timestamp'] < self.last_audio_ts:
            if (self.last_audio_ts - header['timestamp']) > 1000000000: # >1s (assuming ns?) Wait, header['timestamp'] unit?
                # Actually, let's just use a simpler heuristic:
                # If new TS is very small (< 1s) and old TS was large, it's a reset.
                pass
            
            # Simple Reset Detection: If difference is negative and substantial, reset.
            # Timestamp is usually microseconds or nanoseconds? C++ `TimeTicks::Now()` is usually microseconds.
            # Let's assume if it dropped by > 1 second (1,000,000 us), it's a reset.
            if (self.last_audio_ts - header['timestamp']) > 500000: # 0.5s tolerance
                 print(f"🔄 Audio Source Reset Detected! (TS {self.last_audio_ts} -> {header['timestamp']})")
                 self.last_audio_ts = header['timestamp'] - 1 # Allow update
        
        if header['timestamp'] <= self.last_audio_ts:
            # Silently return on stale data
            if self.is_recording:
                self.stuck_frames += 1
                if self.stuck_frames % 50 == 0:
                     print(f"⚠️ Audio Stuck (No new data from Chrome). Is Mic active on this page? (Stuck Count: {self.stuck_frames})")
            else:
                self.stuck_frames = 0 # Reset if not recording and timestamp is stale
            return 
            
        self.stuck_frames = 0
        self.last_audio_ts = header['timestamp']
        
        raw_bytes = self.memory.read_audio_data(header['frames'])
        # Convert raw bytes (float32) to numpy array
        audio_float = np.frombuffer(raw_bytes, dtype=np.float32)

        # 1. Gain/AGC (Automatic Gain Control)
        # Previously we used fixed 150x gain. Now we use dynamic normalization.
        rms = np.sqrt(np.mean(audio_float**2))
        
        gain = 1.0
        if 0.0001 < rms < 0.1:
            gain = 0.1 / rms
            gain = min(gain, 30.0) # Cap at 30x to avoid noise explosion
        
        audio_boosted = audio_float * gain
        # 2. VAD (Voice Activity Detection)
        # Check RMS of the boosted float audio
        boosted_rms = np.sqrt(np.mean(audio_boosted**2))
        
        # Convert to Int16 for storage/transcription
        audio_int16 = (audio_boosted * 32767).astype(np.int16)
        
        # VAD: Speech detected if RMS > 0.005 (after gain) - adjusted for sensitivity
        is_speech = boosted_rms > 0.005
        
        # Log Status (Heartbeat) - Throttled to prevent spam
        self.frame_count += 1
        if self.frame_count % 20 == 0:  # Update ~3 times per second
            bar_len = int(min(rms * 1000 * gain, 20))
            vol_bar = "█" * bar_len + " " * (20 - bar_len)
            
            status_text = "Listening..."
            if self.silence_frames == 0:
                status_text = "🔴 REC      "
            
            # Use ANSI escape code \033[K to clear the rest of the line and \r to return to start
            sys.stdout.write(f"\r{status_text} Vol: [{vol_bar}] Video: {self.video_status} \033[K")
            sys.stdout.flush()

        # Voice Activity Detection
        # In Push-to-Talk (self.is_recording), we capture EVERYTHING.
        # Otherwise, we rely on VAD.
        if is_speech or self.is_recording:
            # IMPORTANT: Buffer RAW float data to avoid per-chunk gain distortion
            self.frames.append(audio_float.tobytes())
            self.silence_frames = 0
        else:
            self.silence_frames += 1


    def transcribe_buffer(self):
        text = ""  # Initialize to prevent UnboundLocalError
        # Allow short audio for debugging purposes
        if len(self.frames) == 0:
             print(f"⚠️ Buffer empty. (TS={self.last_audio_ts}, Mic Active?)")
             return
             
        print(f"📝 Transcribing {len(self.frames)} frames...")
        audio_data = b''.join(self.frames)
        try:
            import whisper
            from scipy import signal
            
            # Convert int16 audio to float32 numpy array (Whisper's expected format)
            sample_rate = int(self.last_sample_rate)
            
            print(f"📊 Audio: {len(audio_data)} bytes, {sample_rate}Hz")
            
            # Convert raw bytes to float32 numpy array
            audio_float32 = np.frombuffer(audio_data, dtype=np.float32)
            
            # Source is already Mono (handled by C++ NeuralAudioWriter)
            # Decimation REMOVED (Was causing garbled audio if source was actually Mono)

            # Save Raw Audio for Debugging
            
            # Save Raw Audio for Debugging
            try:
                from scipy.io import wavfile
                # Scale to int16 for WAV saving
                wav_data = (audio_float32 * 32767).astype(np.int16)
                wavfile.write("debug_audio.wav", sample_rate, wav_data)
                print("💾 Saved debug_audio.wav")
            except Exception as e:
                print(f"⚠️ Could not save WAV: {e}")

            # Normalize using 95th percentile (Robust to clicks/pops)
            # If we just use max(), a single click will make the voice quiet.
            abs_audio = np.abs(audio_float32)
            p95 = np.percentile(abs_audio, 95)
            
            if p95 > 0.0001: # Threshold: 0.01% signal (Almost zero, accept everything)
                gain = 0.5 / p95 # Target 50% amplitude for the main body
                audio_float32 = audio_float32 * gain
                audio_float32 = np.clip(audio_float32, -1.0, 1.0)
                print(f"🔊 Boosted Audio (Gain={gain:.1f}x, p95={p95:.5f})")
            else:
                # Log but DO NOT ABORT (Unless strictly 0)
                # Some mics are incredibly quiet.
                if p95 == 0:
                     print(f"❌ Audio Discarded (Absolute Silence).")
                     return
                print(f"⚠️ Audio Very Quiet (p95={p95:.5f}). Proceeding anyway.")
                # Apply massive gain
                audio_float32 = audio_float32 * 1000.0  
                audio_float32 = np.clip(audio_float32, -1.0, 1.0)
            
            # Audio diagnostics
            duration = len(audio_float32) / sample_rate
            rms = np.sqrt(np.mean(audio_float32**2))
            peak = np.max(np.abs(audio_float32))
            print(f"🔍 Duration: {duration:.2f}s, RMS: {rms:.4f}, Peak: {peak:.4f}")
            
            # Resample to 16kHz (Whisper's native sample rate) for better accuracy
            if sample_rate != 16000:
                num_samples = int(len(audio_float32) * 16000 / sample_rate)
                audio_float32 = signal.resample(audio_float32, num_samples)
                print(f"🔄 Resampled to 16kHz ({num_samples} samples)")
            
            # Pad short audio to at least 1.5 seconds (24000 samples @ 16kHz) to reduce hallucinations
            MIN_SAMPLES = 24000
            if len(audio_float32) < MIN_SAMPLES:
                padding = MIN_SAMPLES - len(audio_float32)
                audio_float32 = np.pad(audio_float32, (0, padding), 'constant')
                print(f"🧱 Padded audio to 1.5s")

            # Low-latency model switch: Use 'small.en' for better accuracy than 'base.en'
            if not hasattr(self, 'whisper_model'):
                print("🔄 Loading Whisper model (small.en)...")
                self.whisper_model = whisper.load_model("small.en")
            
            # Transcribe with Context Prompt (Biasing)
            # This tells Whisper: "Expect these kinds of phrases", which prevents "Thank you" hallucinations.
            prompt = "Go to YouTube. Go to Twitter. Go to Google. Click. Type. Scroll. Search."
            result = self.whisper_model.transcribe(
                audio_float32, 
                language='en', 
                fp16=False,
                initial_prompt=prompt,
                condition_on_previous_text=False
            )
            print(f"📝 Result: \"{result['text']}\"")
            
            final_text = result['text'].strip().lower()

            # Hallucination Filter: Check for heavy repetition
            # e.g. "Search. Search. Search."
            words = final_text.split()
            if len(words) > 3:
                unique_words = set(words)
                # If unique words are less than 40% of total, it's likely a loop
                if len(unique_words) / len(words) < 0.4:
                     print(f"⚠️ Hallucination Detected (Repetitive): '{final_text}' -> Ignored.")
                     final_text = ""

            # Remove trailing punctuation
            final_text = final_text.strip(".,!?")
            text = final_text # Restore variable name for downstream logic

            # Correction Layer (Fix common mishearings)
            corrections = {
                "you tube": "youtube",
                "go to the": "go to",
                "show me": "go to"
            }
            text_lower = text.lower()
            for wrong, right in corrections.items():
                if wrong in text_lower:
                    text = text_lower.replace(wrong, right) # Apply correction
            
            # Intent Router (Multi-Modal Dispatacher)
            clean_text = text.lower().replace(".", "")
            is_navigation = False
            
            # 1. Navigation Intent
            if clean_text.startswith("go to "):
                target = clean_text.replace("go to ", "").strip()
                if " " not in target: # Single word target usually means domain
                    if "twitter" in target: target = "x.com"
                    elif "x" == target: target = "x.com"
                    elif "youtube" in target: target = "youtube.com"
                    elif "google" in target: target = "google.com"
                    elif "github" in target: target = "github.com"
                    elif "reddit" in target: target = "reddit.com"
                    elif "." not in target: target += ".com" 
                    print(f"🧠 Intent: Navigation -> {target}")
                    text = target
                    is_navigation = True
                else:
                     is_navigation = True
            
            elif "youtube" in clean_text:
                 print(f"🧠 Intent: Navigation (Phonetic Fix) -> youtube.com")
                 text = "youtube.com"
                 is_navigation = True
            
            # 2. Action Intent (Click, Type, Solve, Fill, Plan)
            elif any(x in clean_text for x in ["click", "type", "solve", "fill", "scroll", "press", "plan", "analyze", "think", "reason", "how to", "describe", "see", "look", "what"]):
                print(f"🧠 Intent: Agent Action -> \"{text}\"")
                # DO NOT write to browser text input (which triggers nav)
                # Instead, dispatch to Agent Action Loop
                self.execute_agent_action(text)
                is_navigation = False
            
            else:
                # Default: Treat as Navigation/Search
                is_navigation = True

            if is_navigation and text:
                print(f"✨ Sending Navigation Command: \"{text}\"")
                self.write_text_to_browser(text)
            
            if not text:
                print("🤷 (No speech detected)")
                
            print("🔊 Listening...") 
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
        
        self.frames = []
        self.silence_frames = 0

    def query_ollama(self, prompt):
        """
        Sends prompt to local Llama instance via Ollama.
        """
        try:
            import requests
            # Use 'llama3' or 'mistral' or 'qwen2.5-coder'
            model = "llama3" 
            
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False
            }
            start_time = time.time()
            response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=30)
            duration = time.time() - start_time
            
            if response.status_code == 200:
                fps = 1.0 / duration if duration > 0 else 0
                print(f"⚡ Llama Inference: {duration:.2f}s ({fps:.2f} FPS)")
                return response.json().get("response", "").strip()
        except ImportError:
            print("⚠️ 'requests' module not found. Run: pip install requests")
        except Exception as e:
            print(f"⚠️ Ollama Connection Failed: {e}")
        return None

    def check_command_file(self):
        try:
            if os.path.exists("manual_command.txt"):
                with open("manual_command.txt", "r") as f:
                    command = f.read().strip()
                if command:
                    print(f"\n📂 File Command Detected: {command}")
                    self.execute_agent_action(command)
                try:
                    os.remove("manual_command.txt")
                except: pass
        except Exception as e:
            print(f"⚠️ File Watcher Error: {e}")

    def query_ollama_vision(self, prompt, image):
        """
        Sends prompt + image to local Llama Vision instance.
        image: PIL Image object
        """
        try:
            import requests
            import base64
            from io import BytesIO
            
            # Use 'moondream' (Fast, efficient)
            model = "moondream" 
            
            # Convert PIL Image to Base64 (JPEG for speed)
            buffered = BytesIO()
            image = image.convert('RGB') # JPEG needs RGB
            image.save(buffered, format="JPEG", quality=50) # Low quality for speed
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            
            payload = {
                "model": model,
                "prompt": prompt,
                "images": [img_str],
                "stream": False
            }
            start_time = time.time()
            # print("  -> Sending to VLM...")
            response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=30)
            duration = time.time() - start_time
            
            if response.status_code == 200:
                fps = 1.0 / duration if duration > 0 else 0
                print(f"👁️ VLM Inference: {duration:.2f}s ({fps:.2f} FPS)")
                return response.json().get("response", "").strip()
            else:
                print(f"⚠️ VLM Error: {response.text}")
        except Exception as e:
            print(f"⚠️ VLM Connection Failed: {e}")
        return None

    def start_terminal_listener(self):
        def listener():
            print("\n⌨️  Terminal Input Active. Type a command (e.g. 'click search') and hit Enter:\n")
            while self.running:
                try:
                    text = input()
                    if text.strip():
                        print(f"⌨️  Manual Command: {text}")
                        self.execute_agent_action(text)
                except EOFError:
                    break
        t = threading.Thread(target=listener, daemon=True)
        t.start()
    
    def execute_agent_action(self, command):
        """
        Executes a complex agentic task using VLM + Input Injection.
        """
        print(f"🤖 AGENT EXECUTING: {command}")
        
        # 1. "Click X" (Visual Grounding)
        if command.lower().startswith("click "):
            target = command[6:].strip() # Remove "click "
            print(f"👁️ Grounding Target: '{target}'")
            
            # Snap a fresh frame
            frame = self.memory.read_video_frame()
            if not frame:
                print("❌ No video signal to ground against.")
                return

            # Prepare Image
            img = Image.frombytes('RGBA', (frame['width'], frame['height']), frame['data'], 'raw', 'BGRA')
            # Use lower res for VLM speed, but high res for coordinate mapping? 
            # Actually, standard VLM (Moondream) works well on 512x512 equivalents.
            # But Moondream outputs 0-1000 coordinates.
            
            prompt = f"Point to '{target}'. Return bounding box as [ymin, xmin, ymax, xmax] (0-1000). Only numbers."
            
            # Synchronous VLM Query (Blocking Audio Loop? No, this is fine for actions)
            response = self.query_ollama_vision(prompt, img)
            
            if response:
                print(f"  -> VLM Output: {response}")
                # Parse 4 numbers (ymin, xmin, ymax, xmax)
                # Flexible regex: handles [1,2,3,4] or 1, 2, 3, 4
                match = re.search(r"(\d+)\D+(\d+)\D+(\d+)\D+(\d+)", response)
                if match:
                    y1, x1, y2, x2 = map(int, match.groups())
                    
                    # Convert to Screen Coords
                    # VLM 0-1000 -> Screen Width/Height
                    screen_w = frame['width']
                    screen_h = frame['height']
                    
                    center_x = int(((x1 + x2) / 2 / 1000) * screen_w)
                    center_y = int(((y1 + y2) / 2 / 1000) * screen_h)
                    
                    print(f"🎯 Coordinates: ({center_x}, {center_y})")
                    
                    if pyautogui:
                        pyautogui.moveTo(center_x, center_y, duration=0.5)
                        pyautogui.click()
                        print("✅ Clicked.")
                    else:
                        print("⚠️ Skipping Click (pyautogui missing)")
                    return
                else:
                    print("⚠️ Could not parse coordinates.")
            return

        # 2. "Scroll"
        elif "scroll" in command.lower():
            if "down" in command.lower():
                if pyautogui: pyautogui.scroll(-500)
                print("  -> Scrolled Down")
            elif "up" in command.lower():
                if pyautogui: pyautogui.scroll(500)
                print("  -> Scrolled Up")
            return
             
        # 3. Complex Reasoning (LLM Path)
        # If it's not a simple UI command, ask Llama for a plan.
        print("🤔 Reasoning with Llama...")
        prompt = f"You are a browser automation agent. Convert this command into a sequence of actions (CLICK, TYPE, SCROLL, NAVIGATE). Command: '{command}'. keep it brief."
        
        plan = self.query_ollama(prompt)
        if plan:
            print(f"📜 Generated Plan:\n{plan}")
            self.write_text_to_browser(f"Plan: {plan}") # Feedback to Omnibox
        else:
            print("❌ Llama unavailable. Please install Ollama or check connection.")

if __name__ == "__main__":
    agent = NeuralAgent()
    try:
        agent.run()
    except KeyboardInterrupt:
        print("\n🛑 Agent Stopped")
