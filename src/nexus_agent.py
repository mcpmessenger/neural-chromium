import sys
import json
import base64
import os
import time
import wave
import struct
import ctypes
import threading
from abc import ABC, abstractmethod

# --- Dependencies ---
# pip install openai google-generativeai anthropic

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None

try:
    import anthropic as anthropic_lib
except ImportError:
    anthropic_lib = None

# --- Windows Input Injection (ctypes) ---
SendInput = ctypes.windll.user32.SendInput
PUL = ctypes.POINTER(ctypes.c_ulong)

class KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]

class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong),
                ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]

class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]

class Input_I(ctypes.Union):
    _fields_ = [("ki", KeyBdInput),
                ("mi", MouseInput),
                ("hi", HardwareInput)]

class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong),
                ("ii", Input_I)]

# --- Visual Cortex (Shared Memory) ---
import mmap
try:
    from PIL import Image
except ImportError:
    Image = None

class VisualCortexHeader(ctypes.Structure):
    _fields_ = [
        ("magic_number", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("format", ctypes.c_uint32),
        ("frame_index", ctypes.c_uint64),
        ("timestamp_us", ctypes.c_int64),
        ("row_bytes", ctypes.c_uint32),
        ("reserved", ctypes.c_uint8 * 128)
    ]

class VisualCortexClient:
    def __init__(self, map_name="Local\\NeuralChromium_VisualCortex_V3", size=1024*1024*16):
        self.map_name = map_name
        self.size = size
        self.mm = None
        self.last_frame_index = 0
        self.connected = False
        self.connect()

    def connect(self):
        try:
            # Setup Security Descriptor (NULL DACL) to allow Low Integrity Access (GPU Process)
            # This is critical for IPC on Windows between User (Medium) and AppContainer/Sandbox (Low)
            SECURITY_DESCRIPTOR_MIN_LENGTH = 20
            SECURITY_DESCRIPTOR_REVISION = 1
            
            class SECURITY_DESCRIPTOR(ctypes.Structure):
                _fields_ = [("Revision", ctypes.c_byte),
                            ("Sbz1", ctypes.c_byte),
                            ("Control", ctypes.c_short),
                            ("Owner", ctypes.c_void_p),
                            ("Group", ctypes.c_void_p),
                            ("Sacl", ctypes.c_void_p),
                            ("Dacl", ctypes.c_void_p)]
            
            class SECURITY_ATTRIBUTES(ctypes.Structure):
                _fields_ = [("nLength", ctypes.c_ulong),
                            ("lpSecurityDescriptor", ctypes.c_void_p),
                            ("bInheritHandle", ctypes.c_bool)]

            # Initialize SD
            sd = ctypes.create_string_buffer(SECURITY_DESCRIPTOR_MIN_LENGTH)
            advapi32 = ctypes.windll.advapi32
            if not advapi32.InitializeSecurityDescriptor(sd, SECURITY_DESCRIPTOR_REVISION):
                log(f"Failed to Init SD: {ctypes.GetLastError()}")
                return
            
            if not advapi32.SetSecurityDescriptorDacl(sd, True, None, False): # None = NULL DACL (Allow All)
                log(f"Failed to Set Null DACL: {ctypes.GetLastError()}")
                return

            sa = SECURITY_ATTRIBUTES()
            sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
            sa.lpSecurityDescriptor = ctypes.addressof(sd)
            sa.bInheritHandle = False

            # Create File Mapping
            INVALID_HANDLE_VALUE = -1
            PAGE_READWRITE = 0x04
            
            # Explicitly call CreateFileMappingW
            kernel32 = ctypes.windll.kernel32
            
            # Create the mapping with Security Attributes
            hMap = kernel32.CreateFileMappingW(
                ctypes.c_void_p(INVALID_HANDLE_VALUE),
                ctypes.byref(sa),
                PAGE_READWRITE,
                0,
                self.size,
                self.map_name
            )
            
            if not hMap:
                 log(f"Failed to Create File Mapping: {ctypes.get_last_error()}")
                 self.connected = False
                 return

            # Note: mmap module in Python doesn't verify handle ownership well, 
            # so we use mmap to 'open' it again or ensure it stays alive.
            # Actually, easiest is to keep the handle open and use mmap to access it.
            # But python mmap requires a 'fileno' for file backing.
            # For pure Shared Mem, we can use the 'tagname' to attach to our own created mapping?
            
            # Better check: mmap.mmap(-1, ...) with the SAME NAME attempts to Open/Create.
            # But it uses Default Security.
            # If we created it FIRST with Custom Security, mmap will just open a view to it.
            
            # So: hMap holds the object alive with correct Security.
            self._hMap_keepalive = hMap 
            
            # Now allow Python mmap to access it (it opens a view)
            self.mm = mmap.mmap(-1, self.size, self.map_name, access=mmap.ACCESS_WRITE)
            self.connected = True
            log("Visual Cortex Connected (Host Mode + Null DACL).")
            
        except Exception as e:
            log(f"Visual Cortex Connection Failed: {e}")
            self.connected = False

    def read_frame_header(self):
        if not self.connected:
            self.connect()
            if not self.connected: return None

        # Read header
        header = VisualCortexHeader.from_buffer_copy(self.mm[:ctypes.sizeof(VisualCortexHeader)])
        
        if header.magic_number != 0x4E455552:
            return None
            
        return header

    def get_latest_frame(self):
        # Poll for new frame (Stateful)
        header = self.read_frame_header()
        if not header: return None
        
        if header.frame_index > self.last_frame_index:
            self.last_frame_index = header.frame_index
            log(f"New Frame: {header.width}x{header.height} #{header.frame_index} ts={header.timestamp_us}")
            return header
        return None

    def get_current_frame_header(self):
        # Stateless read for on-demand capture
        return self.read_frame_header()

    def capture_image(self):
        # Use CURRENT header (Stateless), don't wait for 'new'
        header = self.get_current_frame_header()
        if not header: return None
        
        if not Image:
            log("PIL not installed. Cannot capture image.")
            return None

        # Data follows header
        header_size = ctypes.sizeof(VisualCortexHeader)
        data_size = header.width * header.height * 4
        
        # Seek and read
        self.mm.seek(header_size)
        pixel_data = self.mm.read(data_size)
        
        try:
            # Create Image from raw RGBA bytes
            img = Image.frombytes('RGBA', (header.width, header.height), pixel_data)
            return img
        except Exception as e:
            log(f"Image Error: {e}")
            return None

# ... (inside handle_message/describe_screen)

    def describe_screen(self):
        log("Analysing Screen...")
        print(" [Agent] Capturing Screen...")
        img = self.visual_cortex.capture_image()
        if not img:
            log("No image captured.")
            print(" [Agent] Failed to capture image.")
            return
            
        print(" [Agent] Sending to Vision Model...")
        if self.provider:
            description = self.provider.generate_vision("Describe what is happening on this screen in detail.", img)
            log(f"I see: {description}")
            print(f" [Agent] I see: {description}")

def type_text(text):
    log(f"Typing text: {text}")
    for char in text:
        # Simple unicode injection
        i = Input()
        i.type = 1 # INPUT_KEYBOARD
        i.ii.ki.wScan = ord(char)
        i.ii.ki.dwFlags = 0x0004 # KEYEVENTF_UNICODE
        ctypes.windll.user32.SendInput(1, ctypes.pointer(i), ctypes.sizeof(i))
        
        # Release
        i.ii.ki.dwFlags = 0x0004 | 0x0002 # KEYEVENTF_KEYUP
        ctypes.windll.user32.SendInput(1, ctypes.pointer(i), ctypes.sizeof(i))
        time.sleep(0.01) # fast typing

# --- Logging ---
LOG_FILE = r"C:\tmp\nexus_agent.log"
TEMP_WAV = r"C:\tmp\nexus_audio.wav"

def log(msg):
    with open(LOG_FILE, "a", encoding='utf-8') as f:
        f.write(f"[{time.strftime('%X')}] {msg}\n")

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}

# --- LLM Provider Abstraction ---

class LLMProvider(ABC):
    @abstractmethod
    def generate_text(self, prompt: str) -> str:
        pass

    @abstractmethod
    def transcribe(self, audio_path: str) -> str:
        pass

    @abstractmethod
    def generate_vision(self, prompt: str, image) -> str:
        pass

class OpenAIProvider(LLMProvider):
    def __init__(self, api_key):
        if not OpenAI:
            raise ImportError("openai module not installed")
        self.client = OpenAI(api_key=api_key)

    def generate_text(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

    def transcribe(self, audio_path: str) -> str:
        with open(audio_path, "rb") as f:
            transcript = self.client.audio.transcriptions.create(
                model="whisper-1", 
                file=f,
                response_format="text"
            )
        return transcript

    def generate_vision(self, prompt: str, image) -> str:
        return "OpenAI Vision not implemented yet."

class GeminiProvider(LLMProvider):
    def __init__(self, api_key):
        if not genai:
            raise ImportError("google-generativeai module not installed")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')

    def generate_text(self, prompt: str) -> str:
        response = self.model.generate_content(prompt)
        return response.text

    def transcribe(self, audio_path: str) -> str:
        try:
            # 1. Upload the file
            log(f"Uploading {audio_path} to Gemini...")
            audio_file = genai.upload_file(path=audio_path)
            
            # 2. Transcribe
            # Gemini 1.5 Pro is multimodal. We can just ask it to listen.
            prompt = "Listen to this audio and transcribe it exactly. Return ONLY the transcription text, no preamble."
            response = self.model.generate_content([prompt, audio_file])
            
            # 3. Cleanup (Optional but good practice to delete from cloud if not needed)
            # genai.delete_file(audio_file.name)
            
            return response.text
        except Exception as e:
            log(f"Gemini Transcription Error: {e}")
            return ""

    def generate_vision(self, prompt: str, image) -> str:
        try:
            # Gemini 1.5 Pro Vision
            response = self.model.generate_content([prompt, image])
            return response.text
        except Exception as e:
            log(f"Gemini Vision Error: {e}")
            return f"Error seeing: {e}"

class AnthropicProvider(LLMProvider):
    def __init__(self, api_key):
        if not anthropic_lib:
            raise ImportError("anthropic module not installed")
        self.client = anthropic_lib.Anthropic(api_key=api_key)

    def generate_text(self, prompt: str) -> str:
        message = self.client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text

    def transcribe(self, audio_path: str) -> str:
        # Anthropic does not have a native transcription API.
        log("Anthropic does not support transcription. Returning empty.")
        return ""

    def generate_vision(self, prompt: str, image) -> str:
        return "Anthropic Vision not implemented yet."


class MockProvider(LLMProvider):
    """Fallback provider for testing infrastructure without API keys"""
    def generate_text(self, prompt): return "I am a Mock Agent. Set your API keys to make me smart."
    def generate_vision(self, prompt, image): return "I see a screen (Mock Vision)."
    def transcribe(self, audio_path): return "Computer mock command" # Auto-trigger for testing

class NexusAgent:
    def __init__(self):
        self.config = load_config()
        self.provider: LLMProvider = None
        self.initialize_provider()
        
        self.audio_buffer = bytearray()
        self.silence_chunks = 0
        self.SILENCE_THRESHOLD = 50 # Very low threshold for quick testing
        self.SILENCE_DURATION = 0.5 # Faster trigger
        self.MIN_RECORD_SECONDS = 0.2
        
        self.visual_cortex = VisualCortexClient()
        self.last_audio_trigger = 0



    def initialize_provider(self):
        requested = self.config.get("active_provider", "openai")
        providers = [requested, "gemini", "openai", "mock"] # Added Mock
        
        # Deduplicate while preserving order
        seen = set()
        priority_list = [x for x in providers if not (x in seen or seen.add(x))]

        for p_name in priority_list:
            log(f"Attempting to initialize Provider: {p_name}")
            try:
                if p_name == "openai":
                    key = self.config.get("openai_api_key")
                    if key: 
                        self.provider = OpenAIProvider(key)
                        log(f"Provider {p_name} initialized successfully.")
                        return
                elif p_name == "gemini":
                    key = self.config.get("gemini_api_key")
                    if key: 
                        self.provider = GeminiProvider(key)
                        log(f"Provider {p_name} initialized successfully.")
                        return
                elif p_name == "anthropic":
                    key = self.config.get("anthropic_api_key")
                    if key: 
                        self.provider = AnthropicProvider(key)
                        log(f"Provider {p_name} initialized successfully.")
                        return
                elif p_name == "mock":
                    self.provider = MockProvider()
                    log("Fallback to MockProvider (Echo Mode).")
                    print(" [Agent] WARNING: Using Mock Brain (Echo Mode).", flush=True)
                    return
            except Exception as e:
                log(f"Failed to initialize provider {p_name}: {e}")
        
        log("CRITICAL: All AI Providers failed to initialize.")
        print(" [Agent] WARNING: No AI Brain available. Voice/Vision will not work.", flush=True)

    def run(self):
        log("Listening on Stdio...")
        
        # Audio Bridge: Tail the log file directly
        # We assume Chrome is running separately and writing to this file.
        log_dir = r"C:\tmp\neural_chrome_profile"
        log_file = os.path.join(log_dir, "chrome_debug.log")
        t = threading.Thread(target=self.tail_chrome_log, args=(log_file,), daemon=True)
        t.start()

        if sys.platform == 'win32':
            import msvcrt
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            sys.stdout.reconfigure(encoding='utf-8')

        # DISABLED stdin processing - only use audio from log file
        # while True:
        #     try:
        #         line = sys.stdin.readline()
        #         if not line:
        #             break
        #         self.check_vision()
        #         self.handle_message(line)
        #     except Exception as e:
        #         log(f"Error loop: {e}")
        
        # Simple loop to keep agent running while audio thread processes
        print(" [Agent] Audio-only mode. Listening for voice input...", flush=True)
        while True:
            time.sleep(1)
            # Audio processing happens in background thread


    def check_vision(self):
        # Poll Visual Cortex
        header = self.visual_cortex.get_latest_frame()
        if header:
            log(f"Visual Cortex Frame: {header.width}x{header.height} #{header.frame_index}")

    def handle_message(self, line):
        try:
            msg = json.loads(line)
            method = msg.get("method")
            if method == "audio.stream":
                self.process_audio(msg["params"])
            elif method == "config.update":
                self.update_config(msg["params"])
            elif method == "agent.action":
                # Handle direct actions if needed
                pass
        except json.JSONDecodeError:
            # Fallback: Treat raw input as text command (Debug/Chat Mode)
            line = line.strip()
            if line:
                log(f"User (Text): {line}")
                if "describe" in line.lower() and "screen" in line.lower():
                    self.describe_screen()
                elif line.lower().startswith("computer"):
                    self.execute_command(line[8:].strip())
                else:
                    self.execute_command(line)

    def update_config(self, params):
        # Update in-memory and disk
        for k, v in params.items():
            self.config[k] = v
        
        with open(os.path.join(os.path.dirname(__file__), "config.json"), "w") as f:
            json.dump(self.config, f, indent=2)
        
        self.initialize_provider() # Re-init with new keys/provider

    def calculate_rms(self, chunk):
        count = len(chunk) // 2
        shorts = struct.unpack(f"<{count}h", chunk)
        sum_squares = sum(s*s for s in shorts)
        return (sum_squares / count) ** 0.5

    def process_audio(self, pcm_data):
        chunk = pcm_data
        log(f"Received Audio Chunk: {len(chunk)} bytes")

        self.audio_buffer.extend(chunk)
        self.silence_chunks += 1  # Use as chunk counter instead
        
        # BYPASS SILENCE DETECTION - Force transcription every 20 chunks (~2 seconds)
        if self.silence_chunks >= 20:
            if len(self.audio_buffer) > 16000:  # At least 1 second of audio
                log(f"Forcing transcription after {self.silence_chunks} chunks, buffer size: {len(self.audio_buffer)}")
                self.on_speech_complete()
            self.audio_buffer = bytearray()
            self.silence_chunks = 0

    def on_speech_complete(self):
        log("Speech detected. Transcribing...")
        print(" [Agent] Thinking...", flush=True) # Immediate Feedback
        
        if len(self.audio_buffer) < 2000: # Ignore tiny blips (<0.06s)
             log("Audio too short, ignoring.")
             self.audio_buffer = bytearray()
             return

        # Save to WAV
        with wave.open(TEMP_WAV, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000) # Reverted to 16kHz (Native)
            wf.writeframes(self.audio_buffer)
        
        self.audio_buffer = bytearray()
        
        if not self.provider:
            log("No provider active. Ignoring speech.")
            return

        try:
            # 1. Transcribe (Currently only OpenAI supports easy audio-to-text here)
            # If using another provider, we might need a dedicated transcriber.
            # For now, fallback to OpenAI for transcription if available, or skip?
            
            # Robustness: Check if provider supports transcription
            text = self.provider.transcribe(TEMP_WAV)
            
            if text:
                text = text.strip()
                log(f"Heard: {text}")
                print(f" [Agent] I heard: '{text}'")
                
                 # 2. Check for Voice Command
                if text.lower().startswith("computer"):
                    command = text[8:].strip()
                    log(f"Command detected: {command}")
                    
                    if "describe" in command.lower() and "screen" in command.lower():
                        self.describe_screen()
                    else:
                        self.execute_command(command)
                else:
                    # Dictation Mode
                    type_text(text + " ")
            else:
                log("Transcription empty.")
                
        except Exception as e:
            log(f"Transcription/Action Error: {e}")

    def execute_command(self, command):
        # Ask the LLM what to do
        prompt = f"You are a browser agent. The user said: '{command}'. Respond with a JSON action: {{'action': 'navigate', 'url': '...'}} or {{'action': 'type', 'text': '...'}}."
        try:
            response = self.provider.generate_text(prompt)
            log(f"Agent thought: {response}")
            # Here we would parse the JSON and call C++ methods via stdout/IPC
            # For now, just type the thought
            # type_text(f"[Cmd: {command}]")
        except Exception as e:
            log(f"LLM Error: {e}")

    def describe_screen(self):
        log("Analysing Screen...")
        print(" [Agent] Capturing Screen...", flush=True)
        img = self.visual_cortex.capture_image()
        if not img:
            log("No image captured.")
            print(" [Agent] Failed to capture image (Check Connection/PIL).", flush=True)
            return
            
        print(" [Agent] Sending to Vision Model...", flush=True)
        if self.provider:
            try:
                description = self.provider.generate_vision("Describe what is happening on this screen in detail.", img)
                log(f"I see: {description}")
                print(f" [Agent] I see: {description}", flush=True)
            except Exception as e:
                log(f"Vision provider error: {e}")
                print(f" [Agent] Vision Error: {e}", flush=True)
        else:
            log("No provider available for vision.")
            print(" [Agent] No AI Provider configured (Check keys).", flush=True)

    def tail_chrome_log(self, log_path):
        """Redundant Reader: Follows the chrome_debug.log file directly"""
        log(f"Tailing Log File: {log_path}")
        try:
            # Wait for file to exist
            while not os.path.exists(log_path):
                time.sleep(0.1)
                
            with open(log_path, "r", encoding='utf-8', errors='ignore') as f:
                # Seek to end? No, we want fresh logs.
                f.seek(0, 2)
                
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.01) # fast poll
                        continue
                        
                    line = line.strip()
                    if "AUDIO_DATA:" in line:
                         try:
                             # Format: ... AUDIO_DATA:<Base64> ...
                             # Extraction
                             parts = line.split("AUDIO_DATA:")
                             if len(parts) > 1:
                                 b64_data = parts[1].strip()
                                 # It might have trailing garbage if log continues?
                                 # Base64 usually ends with = or alphanumeric.
                                 # We assume newline ended the log statement.
                                 
                                 audio_bytes = base64.b64decode(b64_data)
                                 # Feed to Audio Buffer
                                 self.process_audio(audio_bytes)
                                 
                                 # Optional: Log rate limited
                                 # log(f"Received Audio Chunk: {len(audio_bytes)} bytes")
                         except Exception as e:
                             log(f"Audio Decode Error: {e}")

                    elif "AUDIO_DEBUG" in line:
                         # Keep this as a backup signal indicator
                         pass
                    
                    if line.startswith("{"): # If valid JSON appears in log (unlikely unless I put it there)
                         self.handle_message(line)
        except Exception as e:
            log(f"Log Tail Error: {e}")

if __name__ == "__main__":
    # Standalone Mode (Default)
    # User must run Chrome manually with: 
    # chrome.exe --enable-logging --v=1 --user-data-dir=C:\tmp\neural_chrome_profile
    NexusAgent().run()
