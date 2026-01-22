import sys
import re
import json
import base64
import os
import time
import wave
import struct
import ctypes
import threading
import io
import socket
from abc import ABC, abstractmethod

# --- Dependencies ---
# pip install openai google-generativeai anthropic
# pip install websocket-client
try:
    import websocket
except ImportError:
    pass # Will fail in ExtensionController if not installed

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import warnings
    # Suppress the noisy deprecation warning
    warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
    import google.generativeai as genai
except ImportError:
    genai = None

try:
    import anthropic as anthropic_lib
except ImportError:
    anthropic_lib = None

try:
    import pychrome
except ImportError:
    pychrome = None

try:
    import ollama
except ImportError:
    ollama = None

# --- Windows Input Injection (ctypes) ---
user32 = ctypes.windll.user32
SendInput = user32.SendInput
PUL = ctypes.POINTER(ctypes.c_ulong)

# Input Constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

# Mouse Flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_WHEEL = 0x0800

# Keyboard Flags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

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

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long),
                 ("y", ctypes.c_long)]

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long)]

class InputManager:
    def __init__(self):
        self.screen_width = user32.GetSystemMetrics(0)
        self.screen_height = user32.GetSystemMetrics(1)
        log(f"Input Manager Initialized. Screen: {self.screen_width}x{self.screen_height}")

    def find_browser_window(self):
        # Find Chromium window. Class is usually "Chrome_WidgetWin_1"
        hwnd = user32.FindWindowW("Chrome_WidgetWin_1", None)
        return hwnd

    def activate_browser_window(self):
        """Brings Chrome window to foreground before input."""
        hwnd = self.find_browser_window()
        if not hwnd:
            return False
            
        # Multi-step activation to bypass Windows focus restrictions
        # 1. Restore if minimized
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        
        # 2. Bring to top
        user32.BringWindowToTop(hwnd)
        
        # 3. Try to set foreground
        user32.SetForegroundWindow(hwnd)
        
        # 4. Attach to foreground thread and try again
        foreground_hwnd = user32.GetForegroundWindow()
        if foreground_hwnd != hwnd:
            foreground_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None)
            current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            
            user32.AttachThreadInput(current_thread, foreground_thread, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(current_thread, foreground_thread, False)
        
        # 5. Verify activation
        time.sleep(0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True
        
        log("Warning: Browser window activation may have failed")
        return True  # Proceed anyway

    def get_client_rect(self, hwnd):
        if not hwnd: return None
        rect = RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return rect
        return None

    def map_coordinates(self, img_x, img_y, img_w, img_h):
        """Maps relative image coordinates to absolute screen coordinates using ClientToScreen."""
        hwnd = self.find_browser_window()
        if not hwnd:
            log("Cannot find Browser Window for coordinate mapping.")
            return None, None
            
        # 1. Get Client Area Dimensions (The actual web content area)
        client_rect = self.get_client_rect(hwnd)
        if not client_rect: return None, None
        
        client_w = client_rect.right - client_rect.left
        client_h = client_rect.bottom - client_rect.top
        
        if client_w == 0 or client_h == 0: return None, None

        # 2. Scale Image Coords to Client Area
        scale_x = client_w / img_w
        scale_y = client_h / img_h
        
        client_x = int(img_x * scale_x)
        client_y = int(img_y * scale_y)
        
        # 3. Convert Client Point to Screen Point
        pt = POINT(client_x, client_y)
        if user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return pt.x, pt.y
            
        return None, None

    def move_mouse(self, x, y):
        # Normalize to 0-65535 for Absolute Move
        norm_x = int(x * 65535 / self.screen_width)
        norm_y = int(y * 65535 / self.screen_height)
        
        ii = Input_I()
        ii.mi = MouseInput(norm_x, norm_y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
        x = Input(INPUT_MOUSE, ii)
        user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    def click(self, x, y):
        # Activate Chrome first
        self.activate_browser_window()
        
        self.move_mouse(x, y)
        time.sleep(0.05) # Stabilize
        
        # Down
        ii_down = Input_I()
        ii_down.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
        x_down = Input(INPUT_MOUSE, ii_down)
        user32.SendInput(1, ctypes.pointer(x_down), ctypes.sizeof(x_down))
        
        time.sleep(0.05)
        
        # Up
        ii_up = Input_I()
        ii_up.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
        x_up = Input(INPUT_MOUSE, ii_up)
        user32.SendInput(1, ctypes.pointer(x_up), ctypes.sizeof(x_up))

    def type_text(self, text):
        # Activate Chrome first
        if not self.activate_browser_window():
            log("Warning: Could not activate browser window before typing")
            
        log(f"Typing: {text}")
        for char in text:
            if char == '\n':
                self.press_key(0x0D) # VK_RETURN
                continue
            
            ii_down = Input_I()
            ii_down.ki = KeyBdInput(0, ord(char), KEYEVENTF_UNICODE, 0, None)
            x_down = Input(INPUT_KEYBOARD, ii_down)
            user32.SendInput(1, ctypes.pointer(x_down), ctypes.sizeof(x_down))
            
            ii_up = Input_I()
            ii_up.ki = KeyBdInput(0, ord(char), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
            x_up = Input(INPUT_KEYBOARD, ii_up)
            user32.SendInput(1, ctypes.pointer(x_up), ctypes.sizeof(x_up))
            time.sleep(0.01)

    def press_key(self, vk_code):
        # VK_RETURN = 0x0D, VK_BACK = 0x08, VK_TAB = 0x09
        ii_down = Input_I()
        ii_down.ki = KeyBdInput(vk_code, 0, 0, 0, None)
        x_down = Input(INPUT_KEYBOARD, ii_down)
        user32.SendInput(1, ctypes.pointer(x_down), ctypes.sizeof(x_down))
        
        time.sleep(0.05)
        
        ii_up = Input_I()
        ii_up.ki = KeyBdInput(vk_code, 0, KEYEVENTF_KEYUP, 0, None)
        x_up = Input(INPUT_KEYBOARD, ii_up)
        user32.SendInput(1, ctypes.pointer(x_up), ctypes.sizeof(x_up))

    def press_special_key(self, key_name):
        key_map = {
            "enter": 0x0D,
            "return": 0x0D,
            "backspace": 0x08,
            "tab": 0x09,
            "space": 0x20,
            "escape": 0x1B,
            "left": 0x25,
            "up": 0x26,
            "right": 0x27,
            "down": 0x28,
            "delete": 0x2E
        }
        vk = key_map.get(key_name.lower())
        if vk:
            log(f"Pressing Special Key: {key_name} (VK: {vk})")
            self.press_key(vk)
        else:
            log(f"Unknown Special Key: {key_name}")

class ExtensionController:
    """Controls browser via Standalone WebSocket Server"""
    def __init__(self, port=9223):
        self.url = f"ws://127.0.0.1:{port}"
        self.ws = None
        self.connected = False
        
    def _connect(self):
        try:
            if self.ws:
                try:
                    self.ws.close()
                except:
                    pass
            
            import websocket 
            self.ws = websocket.create_connection(self.url, timeout=2)
            
            # Handshake: Register as Agent
            self.ws.send(json.dumps({"type": "agent"}))
            self.connected = True
            return True
        except Exception as e:
            self.connected = False
            return False

    def check_connection(self):
        """Check if WebSocket Server is reachable"""
        if self.connected:
            return True
        return self._connect()

    def send_command(self, action, params):
        if not self.connected or not self.ws:
            if not self._connect():
                return None
                
        try:
            command = {
                "id": int(time.time() * 1000),
                "action": action,
                "params": params
            }
            
            self.ws.send(json.dumps(command))
            result = self.ws.recv()
            if result:
                return json.loads(result)
                
        except (BrokenPipeError, websocket.WebSocketException, OSError) as e:
            log(f"WebSocket Error: {e}")
            self.connected = False
            # Retry once
            if self._connect():
                return self.send_command(action, params)
        except Exception as e:
            log(f"Command Error: {e}")
            
        return None

    def click(self, x, y):
        resp = self.send_command('click', {'x': x, 'y': y})
        if resp and resp.get('success'):
            log(f"Extension Click: ({x}, {y})")
            return True
        self._handle_error("Click", resp)
        return False

    def type_text(self, text):
        resp = self.send_command('type', {'text': text})
        if resp and resp.get('success'):
            log(f"Extension Typed: {text}")
            return True
        self._handle_error("Type", resp)
        return False

    def press_key(self, key):
        resp = self.send_command('press_key', {'key': key})
        if resp and resp.get('success'):
            log(f"Extension Key: {key}")
            return True
        self._handle_error("Key", resp)
        return False

    def _handle_error(self, action, resp):
        err = resp.get('error', '') if resp else 'Unknown'
        if "chrome://" in str(err) or "restricted" in str(err):
            log(f"⚠️  RESTRICTED: Cannot control internal Chrome pages.")
            log(f"    Please navigate to a real website (e.g. google.com)")
        else:
            log(f"Extension {action} Failed: {resp}")

    def navigate(self, url):
        resp = self.send_command('navigate', {'url': url})
        if resp and resp.get('success'):
            log(f"Extension Navigate: {url}")
            return True
        return False

# --- CDP Browser Controller ---
class CDPController:
    """Controls browser via Chrome DevTools Protocol - no focus issues!"""
    def __init__(self, port=9222):
        self.port = port
        self.browser = None
        self.tab = None
        self.connected = False
        
    def connect(self):
        """Connect to Chrome via CDP"""
        if not pychrome:
            log("pychrome not installed. CDP disabled.")
            return False
            
        try:
            self.browser = pychrome.Browser(url=f"http://127.0.0.1:{self.port}")
            tabs = self.browser.list_tab()
            
            log("--- [DEBUG] Available CDP Targets ---")
            for t in tabs:
                log(f"Target: {t.type} | {t.title} | {t.url}")
            log("-------------------------------------")

            if not tabs:
                log("No tabs found in Chrome")
                return False
                
            # Use first valid page
            self.tab = next((t for t in tabs if t.type == 'page'), tabs[0])
            self.tab.start()
            
            # Don't enable domains - they should work without explicit enable
            # Some Chrome builds don't support Input.enable
            
            self.connected = True
            log(f"CDP Connected to Chrome (tab count: {len(tabs)})")
            return True
            
        except Exception as e:
            log(f"CDP connection failed: {e}")
            self.connected = False
            return False
    
    def click(self, x, y):
        """Click at coordinates via CDP"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            # Use JavaScript injection instead of Input domain (more compatible)
            js_code = f"""
            (function() {{
                const el = document.elementFromPoint({x}, {y});
                if (el) {{
                    el.click();
                    return true;
                }}
                return false;
            }})()
            """
            
            result = self.tab.call_method("Runtime.evaluate", expression=js_code)
            log(f"CDP Click at ({x}, {y})")
            return True
            
        except Exception as e:
            log(f"CDP click failed: {e}")
            return False
    
    def type_text(self, text):
        """Type text via CDP"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            # Use JavaScript to insert text - try multiple approaches
            escaped_text = text.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
            js_code = f"""
            (function() {{
                let el = document.activeElement;
                let found = false;
                
                // If nothing useful is focused, find first visible input
                if (!el || (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && !el.isContentEditable)) {{
                    // Try different selectors
                    el = document.querySelector('input[type="text"]:not([style*="display: none"])') ||
                         document.querySelector('input[type="search"]') ||
                         document.querySelector('textarea') ||
                         document.querySelector('[contenteditable="true"]') ||
                         document.querySelector('input:not([type="hidden"])');
                    
                    if (el) {{
                        el.focus();
                        found = true;
                    }}
                }}
                
                if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {{
                    // For input/textarea, set value directly
                    el.value = '{escaped_text}';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return {{ success: true, element: el.tagName, found: found }};
                }} else if (el && el.isContentEditable) {{
                    // For contenteditable, use textContent
                    el.textContent = '{escaped_text}';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    return {{ success: true, element: 'contenteditable', found: found }};
                }}
                
                return {{ success: false, element: el ? el.tagName : 'none', found: found }};
            }})()
            """
            
            result = self.tab.call_method("Runtime.evaluate", expression=js_code)
            log(f"CDP Type result: {result}")
            log(f"CDP Typed: {text}")
            return True
            
        except Exception as e:
            log(f"CDP type failed: {e}")
            return False
    
    def press_key(self, key):
        """Press a key via CDP"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            # Use JavaScript to simulate key press
            js_code = f"""
            (function() {{
                const el = document.activeElement;
                if (el) {{
                    const event = new KeyboardEvent('keydown', {{ key: '{key}', bubbles: true }});
                    el.dispatchEvent(event);
                    const event2 = new KeyboardEvent('keyup', {{ key: '{key}', bubbles: true }});
                    el.dispatchEvent(event2);
                    return true;
                }}
                return false;
            }})()
            """
            
            self.tab.call_method("Runtime.evaluate", expression=js_code)
            log(f"CDP Pressed key: {key}")
            return True
            
        except Exception as e:
            log(f"CDP key press failed: {e}")
            return False
    
    def navigate(self, url):
        """Navigate to URL via CDP"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            self.tab.call_method("Page.navigate", url=url)
            log(f"CDP Navigate to: {url}")
            return True
            
        except Exception as e:
            log(f"CDP navigate failed: {e}")
            return False


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
            # log(f"New Frame: {header.width}x{header.height} #{header.frame_index} ts={header.timestamp_us}") # SILENCED FOR PERFORMANCE
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
            
# Removed old type_text function, replaced by InputManager method
# def type_text(text):
#     ...

# --- Logging ---
LOG_FILE = r"C:\tmp\nexus_agent.log"
TEMP_WAV = r"C:\tmp\nexus_audio.wav"

def log(msg):
    try:
        print(f"[LOG] {msg}", flush=True)
        with open(LOG_FILE, "a", encoding='utf-8') as f:
            f.write(f"[{time.strftime('%X')}] {msg}\n")
    except Exception:
        pass

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
        try:
            # Convert PIL Image to Base64
            buffered = io.BytesIO()
            image.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_str}"
                                },
                            },
                        ],
                    }
                ],
                max_tokens=500,
            )
            return response.choices[0].message.content
        except Exception as e:
            log(f"OpenAI Vision Error: {e}")
            return f"Error seeing: {e}"

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


class OllamaProvider(LLMProvider):
    def __init__(self, model_name="llama3.2-vision"):
        if not ollama:
            raise ImportError("ollama module not installed. pip install ollama")
        self.model_name = model_name

    def generate_text(self, prompt: str) -> str:
        response = ollama.chat(model=self.model_name, messages=[
            {'role': 'user', 'content': prompt}
        ])
        return response['message']['content']

    def transcribe(self, audio_path: str) -> str:
        return "" # Ollama doesn't support audio transcription natively yet

    def generate_vision(self, prompt: str, image) -> str:
        # Image is expected to be a PIL Image
        # Convert to bytes for Ollama
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        img_bytes = buffered.getvalue()

        # System prompt to enforce JSON
        messages = [
            {
                'role': 'system', 
                'content': 'You are a precise browser automation agent. You must output ONLY valid JSON. Coordinates must be absolute pixels based on the image provided.'
            },
            {
                'role': 'user',
                'content': prompt,
                'images': [img_bytes]
            }
        ]

        response = ollama.chat(model=self.model_name, messages=messages)
        return response['message']['content']


class MockProvider(LLMProvider):
    """Fallback provider for testing infrastructure without API keys"""
    def generate_text(self, prompt): return "I am a Mock Agent. Set your API keys to make me smart."
    def generate_vision(self, prompt, image): return "I see a screen (Mock Vision)."
    def transcribe(self, audio_path): return "Computer mock command" # Auto-trigger for testing

class NexusAgent:
    def __init__(self):
        self.config = load_config()
        self.input_manager = InputManager() # Init HWA Input (fallback)
        self.cdp = CDPController() # Init CDP Browser Control (Legacy)
        self.extension = ExtensionController() # Init Extension Control (Primary)
        self.provider: LLMProvider = None
        self.initialize_provider()
        
        self.audio_buffer = bytearray()
        self.silence_chunks = 0
        self.SILENCE_THRESHOLD = 1500 # Lowered from 2500 to catch quieter mics
        
        self.visual_cortex = VisualCortexClient()
        self.last_audio_trigger = 0



    def initialize_provider(self):
        requested = self.config.get("active_provider", "ollama") # Default to ollama for this sprint
        providers = [requested, "ollama", "gemini", "openai", "mock"] # Added Ollama
        
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
                elif p_name == "ollama":
                    model = self.config.get("ollama_model", "llama3.2-vision")
                    self.provider = OllamaProvider(model_name=model)
                    log(f"Provider {p_name} initialized successfully with model {model}.")
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

    def file_command_listener(self):
        """Watches for commands in C:\\tmp\\neural_command.txt"""
        cmd_file = r"C:\tmp\neural_command.txt"
        log(f"Watching for commands in: {cmd_file}")
        
        # Ensure file exists
        if not os.path.exists(cmd_file):
            with open(cmd_file, "w") as f:
                f.write("")
                
        last_mtime = 0
        while True:
            try:
                time.sleep(0.5)
                if not os.path.exists(cmd_file): continue
                
                mtime = os.path.getmtime(cmd_file)
                if mtime > last_mtime:
                    last_mtime = mtime
                    with open(cmd_file, "r") as f:
                        content = f.read().strip()
                        
                    if content:
                        log(f"File Command: {content}")
                        print(f" [Agent] 📁 File Command: {content}", flush=True)
                        self.process_text_command(content)
                        
                        # Clear file to acknowledge
                        with open(cmd_file, "w") as f:
                            f.write("")
            except Exception as e:
                log(f"File Command Error: {e}")

    def run(self):
        log("Listening on Stdio...")
        
        # Audio Bridge: Tail the log file directly
        # We assume Chrome is running separately and writing to this file.
        log_dir = r"C:\tmp\neural_chrome_profile"
        log_file = os.path.join(log_dir, "chrome_debug.log")
        t = threading.Thread(target=self.tail_chrome_log, args=(log_file,), daemon=True)
        t.start()
        
        # Console Input Thread
        t_console = threading.Thread(target=self.console_listener, daemon=True)
        t_console.start()
        
        # File Command Thread (Alternative Input)
        t_file = threading.Thread(target=self.file_command_listener, daemon=True)
        t_file.start()

        if sys.platform == 'win32':
            # import msvcrt
            # msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY) # Disable Binary Mode on Stdin (Breaks text input?)
            sys.stdout.reconfigure(encoding='utf-8')

        if self.visual_cortex.connected:
             print(" [Agent] Visual Cortex Linked 👁️ + Audio Active 🔊. Listening...", flush=True)
             print(" [Agent] AUTO-BENCHMARK MODE ACTIVE: Switch tabs and scroll/play video to test FPS!", flush=True)
             print(" [Agent] To test input: Write 'debug type hello' to C:\\tmp\\neural_command.txt", flush=True)
        else:
             print(" [Agent] Audio-only mode 🔊. Listening for voice input...", flush=True)
        
        # Connect to Chrome via Extension (Primary)
        print(" [Agent] Connecting to Chrome Extension...", flush=True)
        
        # Retry connection for up to 10 seconds (Chrome startup can be slow)
        extension_connected = False
        for i in range(5):
            if self.extension.check_connection():
                print(" [Agent] ✅ Extension Connected - Native Control Ready!", flush=True)
                extension_connected = True
                break
            time.sleep(2)
            if i < 4: print(f" [Agent] Waiting for Extension... ({i+1}/5)", flush=True)
            
        if not extension_connected:
             print(" [Agent] ⚠️ Extension connection failed. Is Chrome running?", flush=True)

        # Connect to Chrome via CDP (Legacy Fallback)
        print(" [Agent] Connecting to Chrome via CDP...", flush=True)
        time.sleep(1)  # Give Chrome time to start
        if self.cdp.connect():
            print(" [Agent] ✅ CDP Connected - Browser control ready!", flush=True)
        else:
            print(" [Agent] ⚠️  CDP connection failed - falling back to SendInput", flush=True)
        
        # AUTO-BENCHMARK LOOP
        frame_count = 0
        last_time = time.time()
        
        while True:
            # Poll for frames as fast as possible
            if self.visual_cortex.connected:
                header = self.visual_cortex.get_latest_frame()
                if header:
                    frame_count += 1
            
            # FPS Report every 1.0s
            now = time.time()
            if now - last_time >= 1.0:
                elapsed = now - last_time
                fps = frame_count / elapsed
                if fps > 0:
                    # Use \r to overwrite the same line
                    print(f"\r [Agent] Input FPS: {fps:5.2f} (Frames: {frame_count:3d})", end='', flush=True)
                frame_count = 0
                last_time = now
            
            # Yield slightly to prevent 100% CPU core usage if idle
            time.sleep(0.001)

    def calculate_rms(self, chunk):
        if not chunk: return 0
        count = len(chunk) // 2
        if count == 0: return 0
        try:
            shorts = struct.unpack(f"<{count}h", chunk)
            sum_squares = sum(s*s for s in shorts)
            return (sum_squares / count) ** 0.5
        except Exception:
            return 0

    def convert_audio_data(self, raw_bytes):
        # ... (Same as before)
        """Attempts to detect and convert Float32 audio to Int16 PCM."""
        if not raw_bytes: return b""
        
        # Try unpacking as Float32
        try:
            count = len(raw_bytes) // 4
            floats = struct.unpack(f"<{count}f", raw_bytes)
            
            # Heuristic: If valid float audio, max amplitude usually <= 1.0 (or slightly above if clipped)
            # If interpreted as float but actually int16, numbers would be HUGE (e.g. 10^30).
            max_val = max(abs(f) for f in floats) if floats else 0
            
            if max_val < 2.0: # It's likely Float32
                # Convert to Int16
                shorts = [int(max(min(f, 1.0), -1.0) * 32767) for f in floats]
                return struct.pack(f"<{len(shorts)}h", *shorts)
        except Exception:
            pass # Not float32 or wrong size
            
        return raw_bytes # Assume it's already Int16

    def process_audio(self, pcm_data):
        # 1. Normalize Audio (Float32 -> Int16 if needed)
        # This fixes the "Static" problem where Float32 silence looks like loud Int16 noise.
        chunk = self.convert_audio_data(pcm_data)
        
        rms = self.calculate_rms(chunk)
        
        if rms > self.SILENCE_THRESHOLD:
            # SPEECH DETECTED
            if self.silence_chunks > 0:
                 log(f"[TRIGGER] Voice detected! Level: {rms:.2f}")
                 self.silence_chunks = 0
            
            self.audio_buffer.extend(chunk)
            self.last_audio_trigger = time.time()
        else:
            # SILENCE (Background Noise)
            if len(self.audio_buffer) > 0:
                # We are trailing an existing speech segment. Keep it briefly.
                self.audio_buffer.extend(chunk)
                self.silence_chunks += 1
                
                # If silence lasts > 1.5 seconds, cut it.
                if self.silence_chunks > 8: 
                    log(f"End of speech detected. Transcribing {len(self.audio_buffer)} bytes...")
                    self.on_speech_complete()
                elif self.silence_chunks % 5 == 0:
                    log(f"Silence count: {self.silence_chunks}/8")
            else:
                 # Buffer is empty. Log occasional noise floor stats
                 if self.silence_chunks % 50 == 0:
                      log(f"[NOISE] Current Level: {rms:.2f} (Threshold: {self.SILENCE_THRESHOLD})")
                      pass
                 self.silence_chunks += 1

        # Safety Valve: Don't buffer forever (Max 15 seconds)
        if len(self.audio_buffer) > 1500000:
             log("Max buffer size reached. Forcing flush.")
             self.on_speech_complete()

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
            wf.setframerate(16000) # Reverting to 16kHz (Standard for ASR)
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
            
            if text and len(text) > 1 and "{" not in text:
                text = text.strip()
                log(f"Heard: {text}")
                print(f" [Agent] I heard: '{text}'")
                self.process_text_command(text)
            else:
                 log(f"Ignored garbage transcription: '{text}'")
                
        except Exception as e:
            log(f"Transcription/Action Error: {e}")

    def console_listener(self):
        """Allows typing commands directly into the agent console."""
        print(" [Agent] Console Ready. Type commands below:", flush=True)
        while True:
            try:
                # Use standard input() which is thread-safe enough for this
                text = input(" > ")
                if text:
                    print(f"\n [Agent] Console Command: {text}")
                    self.process_text_command(text)
            except EOFError:
                break
            except Exception as e:
                log(f"Console Error: {e}")
                pass

    def process_text_command(self, text):
        # --- INPUT SANITIZATION ---
        # Fix for console paste "jamming" where text is repeated (e.g. "nexus type xnexus type x")
        if len(text) > 20: # Only check reasonably long strings
            # Check for simple repetition: "A A" or "AA"
            # Regex captures a group of at least 10 chars that repeats at least once at the start of the string
            match = re.match(r"^(.{10,}?)\1+$", text, flags=re.DOTALL)
            if match:
                sanitized = match.group(1)
                log(f"🧹 Input Sanitized: Detected repeated pattern. Reduced from {len(text)} to {len(sanitized)} chars.")
                text = sanitized
        
        # 1. Check for Wake Word ("NEXUS")
        # VERIFICATION SHORTCUT: Allow "benchmark" without wake word
        if "benchmark" in text.lower():
            self.benchmark_system()
            return

        if text.lower().startswith("nexus") or text.lower().startswith("debug"):
            # "nexus" is 5 chars. Command follows.
            # "debug" allows typing "debug click the button" without saying nexus
            trigger = text.split(" ")[0]
            command = text[len(trigger):].strip()
            
            log(f"Command detected: {command}")
            
            # Direct debug commands (bypass LLM)
            if text.lower().startswith("debug"):
                if command.lower().startswith("type "):
                    text_to_type = command[5:]  # Remove "type "
                    print(f" [Agent] ⌨️ Direct Type: '{text_to_type}'", flush=True)
                    if self.extension.connected:
                        self.extension.type_text(text_to_type)
                    elif self.cdp.connected:
                        self.cdp.type_text(text_to_type)
                    else:
                        self.input_manager.type_text(text_to_type)
                    return
                    
                elif command.lower().startswith("press "):
                    key = command[6:]  # Remove "press "
                    print(f" [Agent] ⌨️ Direct Press: '{key}'", flush=True)
                    if self.extension.connected:
                        self.extension.press_key(key)
                    elif self.cdp.connected:
                        self.cdp.press_key(key)
                    else:
                        self.input_manager.press_special_key(key)
                    return
                    
                elif command.lower().startswith("click "):
                    # Parse "click X Y" or let LLM handle "click on the button"
                    parts = command[6:].split()
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        x, y = int(parts[0]), int(parts[1])
                        print(f" [Agent] 🖱️ Direct Click: ({x}, {y})", flush=True)
                        if self.extension.connected:
                            self.extension.click(x, y)
                        elif self.cdp.connected:
                            self.cdp.click(x, y)
                        else:
                            self.input_manager.click(x, y)
                        return
            
            # LLM-powered commands
            if "describe" in command.lower() and "screen" in command.lower():
                self.describe_screen()
            elif "benchmark" in command.lower():
                self.benchmark_system()
            else:
                self.execute_command(command)
        else:
            # Dictation Mode (No wake word)
            # Only if it came from voice? 
            # If console, maybe we assume command? NO, keep consistent.
            # print(" [Agent] Dictating...", flush=True)
            # self.input_manager.type_text(text + " ")
            pass

    def benchmark_system(self, duration=5.0):
        if not self.visual_cortex.connected:
             print(" [Agent] Cannot benchmark: Visual Cortex Disconnected.", flush=True)
             return

        print(f" [Agent] Starting {duration}s Benchmark...", flush=True)
        count = 0
        latencies = []
        start_time = time.time()
        
        while time.time() - start_time < duration:
            header = self.visual_cortex.get_latest_frame()
            if header:
                # Calculate Latency (C++ Timestamp vs Python Time)
                # Timestamp is DeltaSinceWindowsEpoch in Microseconds
                # We need to be careful with clocks. 
                # Ideally, we just check relative delta if clocks are synced, 
                # but C++ base::Time::Now() should align with Python time.time() roughly.
                
                # Conversion: Windows Epoch (1601) vs Unix Epoch (1970). 
                # Actually base::Time::ToDeltaSinceWindowsEpoch() is raw. 
                # base::Time::Now().InSecondsFSinceUnixEpoch() is better for comparison, 
                # but we used ToDelta... in C++.
                # Let's rely on frame delta time for FPS primarily.
                
                # Actually, let's just count FPS for now as implicit throughput verification.
                count += 1
            
            time.sleep(0.001) # Yield slightly
            
        elapsed = time.time() - start_time
        fps = count / elapsed
        print(f" [Agent] Benchmark Complete.", flush=True)
        print(f" [Agent] Frames Processed: {count}", flush=True)
        print(f" [Agent] Average FPS: {fps:.2f}", flush=True)
        log(f"Benchmark: {count} frames in {elapsed:.2f}s = {fps:.2f} FPS")

    def execute_command(self, command):
        # Ask the LLM what to do
        log(f"Executing Decision for: {command}")
        
        # 1. Capture Context (Screen)
        img = self.visual_cortex.capture_image()
        if not img:
            print(" [Agent] Blind - Cannot see screen to act.", flush=True)
            return

        # 2. Construct Prompt with Image
        # --- Preprocessing: Naive Dynamic Resolution ---
        if img:
             # Qwen2.5-VL/Llama3.2-Vision work best with images divisible by 28
             # AND we want to limit size for speed (e.g. max 960px dimension)
            original_width, original_height = img.size
            MAX_DIMENSION = 960 
            
            scale_ratio = 1.0
            if original_width > MAX_DIMENSION or original_height > MAX_DIMENSION:
                scale_ratio = min(MAX_DIMENSION / original_width, MAX_DIMENSION / original_height)
                
            new_w = int(original_width * scale_ratio)
            new_h = int(original_height * scale_ratio)
            
            # Align to 28 (Grid requirement for some VLMs)
            new_w = (new_w // 28) * 28
            new_h = (new_h // 28) * 28
            
            # Ensure at least 28x28
            new_w = max(28, new_w)
            new_h = max(28, new_h)
            
            # Resize
            if scale_ratio < 1.0 or new_w != original_width:
                 img = img.resize((new_w, new_h))
                 log(f"🖼️ Vision Resize: {original_width}x{original_height} -> {new_w}x{new_h}")
            
            # Logic Double Back Support:
            # We must remember this scale factor to map VLM coordinates back to the screen
            self.last_vision_scale = (original_width / new_w, original_height / new_h)
        
        # We need a strict JSON schema for the Action
        prompt = f"""
        User command: '{command}'
        
        You are a Browser Agent. You can see the screen.
        Analyze the UI elements and deciding on the correct action.
        
        Available Actions (JSON):
        - CLICK: {{"action": "click", "x": <int>, "y": <int>, "thought": "..."}} (Coordinates are relative to the image size: {img.width}x{img.height})
        - TYPE:  {{"action": "type", "text": "<string>", "thought": "..."}}
        - PRESS: {{"action": "press", "key": "<key_name>", "thought": "..."}} (For special keys: Enter, Backspace, Tab, Escape, Left, Right, Up, Down)
        - SCROLL: {{"action": "scroll", "direction": "down", "amount": <int>}}
        - NAVIGATE: {{"action": "navigate", "url": "<url>"}} (Only if explicitly asked/needed)
        - DONE: {{"action": "done", "thought": "Task complete"}}
        
        Return ONLY valid JSON. No preamble.
        """
        
        try:
            # We use generate_vision because we need to see the screen to know WHERE to click
            response_json = self.provider.generate_vision(prompt, img)
            log(f"Agent Plan: {response_json}")
            
            # 3. Parse and Execute
            # Sanitize JSON (sometimes models add markdown blocks)
            raw_json = response_json.replace("```json", "").replace("```", "").strip()
            
            plan = json.loads(raw_json)
            action = plan.get("action")
            thought = plan.get("thought", "")
            print(f" [Agent] 🤔 {thought}", flush=True)
            
            if action == "click":
                x = plan.get("x")
                y = plan.get("y")
                
                # Logic Double Back: Scale coordinates back to original screen size
                if hasattr(self, 'last_vision_scale'):
                    sx, sy = self.last_vision_scale
                    x = int(x * sx)
                    y = int(y * sy)
                    log(f"📍 Logic Double Back: Scaling ({plan.get('x')}, {plan.get('y')}) -> ({x}, {y})")
                
                # Try Extension first
                if self.extension.connected:
                    print(f" [Agent] 🖱️ Extension Clicking at ({x}, {y})...", flush=True)
                    self.extension.click(x, y)
                # Try CDP second (no coordinate mapping needed - uses image coords directly)
                elif self.cdp.connected:
                    print(f" [Agent] 🖱️ CDP Clicking at ({x}, {y})...", flush=True)
                    self.cdp.click(x, y)
                else:
                    # Fallback to SendInput (requires coordinate mapping)
                    sx, sy = self.input_manager.map_coordinates(x, y, img.width, img.height)
                    if sx is not None:
                        print(f" [Agent] 🖱️ SendInput Clicking at ({sx}, {sy})...", flush=True)
                        self.input_manager.click(sx, sy)
                    else:
                        print(" [Agent] ❌ Could not map coordinates.", flush=True)
                    
            elif action == "type":
                text = plan.get("text")
                print(f" [Agent] ⌨️ Typing: '{text}'", flush=True)
                
                # Try Extension first
                if self.extension.connected:
                    self.extension.type_text(text)
                elif self.cdp.connected:
                    self.cdp.type_text(text)
                else:
                    self.input_manager.type_text(text)
                
            elif action == "press":
                key = plan.get("key")
                print(f" [Agent] ⌨️ Pressing Key: '{key}'", flush=True)
                
                # Try Extension first
                if self.extension.connected:
                    self.extension.press_key(key)
                elif self.cdp.connected:
                    self.cdp.press_key(key)
                else:
                    self.input_manager.press_special_key(key)
                
            elif action == "navigate":
                url = plan.get("url")
                print(f" [Agent] 🌐 Navigating to {url}", flush=True)
                
                # Try Extension first
                if self.extension.connected:
                    self.extension.navigate(url)
                elif self.cdp.connected:
                    self.cdp.navigate(url)
                else:
                    # Fallback: Ctrl+L + type URL + Enter (not implemented yet)
                    print(" [Agent] ⚠️  Navigate requires Extension or CDP", flush=True)
                
        except Exception as e:
            log(f"Execution Error: {e}")
            print(f" [Agent] 💥 Action Failed: {e}", flush=True)

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
                
            chunk_count = 0
            with open(log_path, "r", encoding='utf-8', errors='ignore') as f:
                # Seek to end to start reading new data
                f.seek(0, 2)
                log(f"Started tailing at position {f.tell()}")
                
                while True:
                    # CRITICAL FIX: Force file stat refresh to detect growth
                    # Save current read position
                    current_pos = f.tell()
                    
                    # Seek to end to force OS to refresh file size
                    f.seek(0, 2)
                    end_pos = f.tell()
                    
                    # If file has grown, return to read position
                    if end_pos > current_pos:
                        f.seek(current_pos)
                    
                    line = f.readline()
                    if not line:
                        # No new data yet, wait briefly
                        time.sleep(0.01)
                        continue
                        
                    line = line.strip()
                    if "AUDIO_DATA:" in line:
                         parts = line.split("AUDIO_DATA:")
                         if len(parts) > 1:
                             b64_data = parts[1].strip()
                             try:
                                 audio_bytes = base64.b64decode(b64_data)
                                 self.process_audio(audio_bytes)
                                 
                                 chunk_count += 1
                                 if chunk_count == 1 or chunk_count % 50 == 0:
                                     log(f"Audio chunks processed: {chunk_count}")
                             except Exception as e:
                                 log(f"Audio Decode Error: {e}")

                    elif "AUDIO_DEBUG" in line:
                         # Keep this as a backup signal indicator
                         pass
                    
                    if line.startswith("{"): # If valid JSON appears in log (unlikely unless I put it there)
                         self.handle_message(line)
        except Exception as e:
            log(f"Log Tail Error: {e}")

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
            pass

    def update_config(self, params):
        # Update in-memory and disk
        for k, v in params.items():
            self.config[k] = v
        
        with open(os.path.join(os.path.dirname(__file__), "config.json"), "w") as f:
            json.dump(self.config, f, indent=2)
        
        self.initialize_provider() # Re-init with new keys/provider

if __name__ == "__main__":
    # Standalone Mode (Default)
    # User must run Chrome manually with: 
    # chrome.exe --enable-logging --v=1 --user-data-dir=C:\tmp\neural_chrome_profile
    NexusAgent().run()
