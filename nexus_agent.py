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

# --- gRPC Support ---
try:
    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    sys.path.append(os.path.join(os.path.dirname(__file__), "proto"))
    import grpc
    from concurrent import futures
    import service_pb2
    import service_pb2_grpc
    import page_state_pb2
    import action_pb2
except ImportError:
    print(" [Agent] Warning: gRPC/Protos not found. gRPC Server disabled.")
    grpc = None

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
    import pychrome
except ImportError:
    pychrome = None

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
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None
    print(" [Agent] Warning: faster-whisper not found. transcription disabled.")

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
            
            # Enable Page domain for navigation
            self.tab.call_method("Page.enable")
            self.tab.call_method("DOM.enable")
            self.tab.call_method("Runtime.enable")
            try:
                self.tab.call_method("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=False, flatten=True)
            except:
                pass # Some versions might fail
            
            self.connected = True
            log(f"CDP Connected to Chrome (tab count: {len(tabs)})")
            
            with open("agent_debug_status.txt", "a") as f:
                 f.write(f"CDP CONNECTED. Tabs: {len(tabs)}\n")
            
            return True
            
        except Exception as e:
            log(f"CDP connection failed: {e}")
            with open("agent_debug_status.txt", "a") as f:
                 f.write(f"CDP FAILED: {e}\n")
            self.connected = False
            return False
    
    def click(self, x, y):
        """Click at coordinates via CDP"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            # Use JavaScript injection with React Native Setter Hack
            js_code = f"""
            (function() {{
                const x = {x};
                const y = {y};
                let el = document.elementFromPoint(x, y);
                
                if (!el) return false;

                // Heuristic: If we clicked a label, try to find its input
                if (el.tagName === 'LABEL') {{
                    const inputId = el.getAttribute('for');
                    if (inputId) {{
                         const input = document.getElementById(inputId);
                         if (input) el = input;
                    }} else {{
                         // Maybe inside?
                         const input = el.querySelector('input');
                         if (input) el = input;
                    }}
                }}
                
                // Heuristic: If we clicked the div.view wrapper in TodoMVC, find the toggle
                if (el.classList.contains('view')) {{
                    const toggle = el.querySelector('.toggle');
                    if (toggle) el = toggle;
                }}

                // React Controlled Component Hack
                // If it's a checkbox/radio, we must bypass the value tracker to ensure React sees the change
                if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {{
                     // 1. Get Native Setter
                     const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 
                        'checked'
                     ).set;
                     
                     // 2. Toggle Value directly on prototype (bypassing React override)
                     nativeSetter.call(el, !el.checked);
                     
                     // 3. Dispatch Bubbling Click
                     const event = new MouseEvent('click', {{
                        bubbles: true,
                        cancelable: true,
                        view: window
                     }});
                     el.dispatchEvent(event);
                     
                     return true;
                }}

                // Default Fallback
                el.click();
                return true;
            }})()
            """
            
            self.tab.call_method("Runtime.evaluate", expression=js_code)
            log(f"CDP Click (Smart) at ({x}, {y})")
            return True
            
        except Exception as e:
            log(f"CDP click failed: {e}")
            return False
    
    def navigate(self, url):
        """Navigate to URL via CDP (Page.navigate + strict feedback)"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            res = self.tab.call_method("Page.navigate", url=url)
            log(f"CDP Navigate Result: {res}")
            print(f" [Agent] CDP Navigate Result: {res}", flush=True)
            if 'errorText' in res:
                print(f" [Agent] Nav Error: {res['errorText']}", flush=True)
            return True
            
        except Exception as e:
            log(f"CDP navigate failed: {e}")
            print(f" [Agent] CDP Navigate Exception: {e}", flush=True)
            return False

    def type_text(self, text, object_id=None):
        """Type text via JS Native Setter (React Compatible)"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            # Use JavaScript to insert text with React-aware native setter
            escaped_text = text.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
            
            js_function = f"""function() {{
                let el = this;
                // If 'this' is window or invalid (global context), fallback to activeElement
                try {{ if (!el || el === window || !el.tagName) el = null; }} catch(e) {{ el = null; }}
                
                if (!el) {{
                    el = document.activeElement;
                    if (!el || (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && !el.isContentEditable)) {{
                        // Fallback: Try generic selectors
                        el = document.querySelector('input[type="text"]:not([style*="display: none"])') ||
                             document.querySelector('input[type="search"]') ||
                             document.querySelector('textarea') ||
                             document.querySelector('[contenteditable="true"]') ||
                             document.querySelector('input:not([type="hidden"])');
                        
                        if (el) el.focus();
                    }}
                }}
                
                if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {{
                    // React Controlled Component Hack (Native Setter)
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    nativeSetter.call(el, '{escaped_text}');
                    
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    
                    return {{ success: true, element: el.tagName, value: el.value }};
                }} else if (el && el.isContentEditable) {{
                    el.textContent = '{escaped_text}';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    return {{ success: true, element: 'contenteditable' }};
                }}
                
                return {{ success: false, element: el ? el.tagName : 'none' }};
            }}"""
            
            if object_id:
                result = self.tab.call_method("Runtime.callFunctionOn", 
                    functionDeclaration=js_function,
                    objectId=object_id,
                    returnByValue=True
                )
            else:
                # Execute as global IIFE
                expr = f"({js_function})()"
                result = self.tab.call_method("Runtime.evaluate", expression=expr, returnByValue=True)
                
            log(f"CDP Type result: {result}")
            log(f"CDP Typed Hack: {text}")
            return True
            
        except Exception as e:
            log(f"CDP type failed: {e}")
            return False
    
    def press_key(self, key):
        """Press a key via CDP Input Domain (Trusted Events)"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            # Map common keys to Windows/CDP Virtual Key Codes
            # https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchKeyEvent
            
            key_map = {
                'Enter': {'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13, 'nativeVirtualKeyCode': 13},
                'Backspace': {'key': 'Backspace', 'code': 'Backspace', 'windowsVirtualKeyCode': 8, 'nativeVirtualKeyCode': 8},
                'Tab': {'key': 'Tab', 'code': 'Tab', 'windowsVirtualKeyCode': 9, 'nativeVirtualKeyCode': 9},
                'Escape': {'key': 'Escape', 'code': 'Escape', 'windowsVirtualKeyCode': 27, 'nativeVirtualKeyCode': 27},
            }
            
            definition = key_map.get(key, {'key': key, 'code': key, 'windowsVirtualKeyCode': 0, 'nativeVirtualKeyCode': 0})
            
            # Dispatch KeyDown
            self.tab.call_method("Input.dispatchKeyEvent", type="keyDown", **definition)
            
            # Dispatch KeyUp
            self.tab.call_method("Input.dispatchKeyEvent", type="keyUp", **definition)
            
            log(f"CDP Pressed key (Trusted): {key}")
            return True
            
        except Exception as e:
            log(f"CDP key press failed: {e}")
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
        # Seek and read
        self.mm.seek(header_size)
        pixel_data = self.mm.read(data_size)
        
        try:
            # Create Image from raw RGBA bytes
            # BGRA format from Chrome usually
            # Note: Chrome Skia output is often N32 which is BGRA on Windows/Intel.
            # We assume RGBA or BGRA. Let's try raw.
            img = Image.frombytes('RGBA', (header.width, header.height), pixel_data, 'raw', 'BGRA')
            return img
        except Exception as e:
            log(f"Image Error: {e}")
            return None

class AudioHeader(ctypes.Structure):
    _fields_ = [
        ("magic_number", ctypes.c_uint32),
        ("sample_rate", ctypes.c_uint32),
        ("channels", ctypes.c_uint32),
        ("samples_per_frame", ctypes.c_uint32),
        ("timestamp_us", ctypes.c_uint64),
        ("format", ctypes.c_uint32), # 0=PCM_16, 1=FLOAT_32
        ("frame_index", ctypes.c_uint32)
    ]

# --- Command Shared Memory Structures ---
class CommandHeader(ctypes.Structure):
    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("data_size", ctypes.c_uint32),
        ("timestamp_ms", ctypes.c_uint64),
    ]

class SetOmniboxTextCommand(ctypes.Structure):
    _fields_ = [("text", ctypes.c_wchar * 512)]

class AgentCommandClient:
    def __init__(self):
        self.map_name = "Local\\NeuralChromium_Command_V1"
        self.event_name = "Local\\NeuralChromium_Command_V1_Event"
        self.size = 4096
        self.mm = None
        self.connected = False
        self.connect()

    def connect(self):
        try:
            self.mm = mmap.mmap(-1, self.size, self.map_name)
            self.connected = True
            print(f"[Command] Connected to {self.map_name}")
        except Exception as e:
            # print(f"[Command] Connection failed: {e}")
            self.connected = False

    def signal_event(self):
        try:
            k32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            EVENT_MODIFY_STATE = 0x0002
            hEvent = k32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, self.event_name)
            if hEvent:
                k32.SetEvent(hEvent)
                k32.CloseHandle(hEvent)
            else:
                print("[Command] Failed to open event")
        except Exception as e:
            print(f"[Command] Event error: {e}")

    def send_omnibox_text(self, text):
        if not self.connected: 
            self.connect()
            if not self.connected: return

        cmd_struct = SetOmniboxTextCommand()
        cmd_struct.text = text
        
        header = CommandHeader()
        header.magic = 0x4E455552
        header.version = 1
        header.type = 2 # SET_OMNIBOX_TEXT
        header.data_size = ctypes.sizeof(cmd_struct)
        header.timestamp_ms = int(time.time() * 1000)

        self.mm.seek(0)
        self.mm.write(bytearray(header))
        self.mm.write(bytearray(cmd_struct))
        self.signal_event()

# --- Audio Client ---
class AudioCortexClient:
    def __init__(self, map_name="Local\\NeuralChromium_Audio_V1", size=4*1024*1024):
        self.map_name = map_name
        self.size = size
        self.mm = None
        self.connected = False
        self.last_ts = 0
        self.command_client = AgentCommandClient()
        self.last_text_update = 0
        
        # Whisper & VAD State
        self.openai_client = None
        self.is_speaking = False
        self.speech_buffer = None
        
        # Try OpenAI First (Cloud)
        try:
             import os
             api_key = os.environ.get("OPENAI_API_KEY")
             if api_key:
                 from openai import OpenAI
                 self.openai_client = OpenAI(api_key=api_key)
                 print(" [Audio] OpenAI Client Initialized (Cloud Whisper).")
             else:
                 print(" [Audio] OPENAI_API_KEY not found. Waiting for user...")
        except ImportError:
             print(" [Audio] openai module not found.")

        self.connect()

    def transcribe_buffer(self):
        # MOCK FALLBACK for Testing without Keys/Libs
        if not self.openai_client and not WhisperModel:
             print(" [Audio] Mock Whisper: Simulating Transcription (No keys/libs found)...")
             # We assume speech was detected by RMS trigger
             text = "Nexus test command"
             print(f" [Audio] Transcribed: '{text}' (MOCK)")
             print(f" [Agent] 🧠 WAKE WORD 'NEXUS' DETECTED! (MOCK)")
             self.command_client.send_omnibox_text(text)
             return

        if not self.openai_client or not self.speech_buffer:
            print(" [Audio] Skipped Transcription: No Client or Buffer.")
            return

        try:
            # OpenAI API requires a file-like object with a name
            self.speech_buffer.seek(0)
            self.speech_buffer.name = "audio.wav" # Generic name
            
            # Need to ensure valid WAV format from raw int16? 
            # OpenAI might accept raw if we say it's wav, but constructing a WAV header is safer.
            import wave
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wf:
                wf.setnchannels(1) # Mono
                wf.setsampwidth(2) # 16-bit
                wf.setframerate(48000) # Assumption
                self.speech_buffer.seek(0)
                wf.writeframes(self.speech_buffer.read())
            
            wav_buffer.seek(0)
            wav_buffer.name = "audio.wav"

            print(" [Audio] Transcribing...")
            transcript = self.openai_client.audio.transcriptions.create(
                model="whisper-1", 
                file=wav_buffer
            )
            
            text = transcript.text.strip()
            if text:
                print(f" [Audio] Transcribed: '{text}'")
                
                # Wake Word Logic
                if "nexus" in text.lower():
                    print(f" [Agent] 🧠 WAKE WORD 'NEXUS' DETECTED!")
                    # Optional: Strip wake word? text = text.replace("Nexus", "").strip()
                
                self.command_client.send_omnibox_text(text)
            else:
                print(" [Audio] Transcription empty.")
                self.command_client.send_omnibox_text("...")
                
        except Exception as e:
            err_msg = f"Error: {str(e)[:40]}" # Truncate for Omnibox
            print(f" [Audio] Transcription Error: {e}")
            self.command_client.send_omnibox_text(err_msg)


    def connect(self):
        try:
            # Same Security Descriptor dance as Visual Cortex
            class SECURITY_DESCRIPTOR(ctypes.Structure):
                _fields_ = [("Revision", ctypes.c_byte), ("Sbz1", ctypes.c_byte), ("Control", ctypes.c_short),
                            ("Owner", ctypes.c_void_p), ("Group", ctypes.c_void_p), ("Sacl", ctypes.c_void_p), ("Dacl", ctypes.c_void_p)]
            class SECURITY_ATTRIBUTES(ctypes.Structure):
                _fields_ = [("nLength", ctypes.c_ulong), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", ctypes.c_bool)]

            sd = ctypes.create_string_buffer(20)
            ctypes.windll.advapi32.InitializeSecurityDescriptor(sd, 1)
            ctypes.windll.advapi32.SetSecurityDescriptorDacl(sd, True, None, False)
            sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), ctypes.addressof(sd), False)

            kernel32 = ctypes.windll.kernel32
            # CreateFileMappingW with OPEN_EXISTING logic via Python mmap? 
            # In C++ we created it. In Python we just open it.
            # But VisualCortexClient used CreateFileMapping with INVALID_HANDLE to ensure it can open it even if permission tricky?
            # Or just mmap.
            
            # Let's try simple mmap first if we assume C++ made it with Null DACL.
            self.mm = mmap.mmap(-1, self.size, self.map_name) # Open existing
            self.connected = True
            log(f"[Audio] Connected to {self.map_name}")
            
        except Exception as e:
            log(f"[Audio] Connection attempt failed: {str(e)[:50]}")
            self.connected = False

    def trigger_listen(self):
        """Manually trigger listening mode (Bypass Wake Word)"""
        print(" [Audio] Manual Trigger: Listening for command...")
        self.is_speaking = True
        self.speech_buffer = io.BytesIO()
        self.command_client.send_omnibox_text("Listening (Manual)...")

    def read_audio_chunk(self):
        if not self.connected: 
            self.connect()
            if not self.connected: return None

        self.mm.seek(0)
        try:
            header_bytes = self.mm.read(ctypes.sizeof(AudioHeader))
            header = AudioHeader.from_buffer_copy(header_bytes)
        except ValueError:
            return None # Buffer empty or invalid

        if header.magic_number != 0x41554449: # "AUDI"
            return None

        # Check existing TS to avoid re-reading same packet?
        # Actually this shared memory is overwrite-mode right now (Ring buffer TODO).
        # So we only want to read if Changed?
        # or if timestamp > last_ts
        
        if header.timestamp_us <= self.last_ts:
             return None # No new data
        
        self.last_ts = header.timestamp_us
        
        # Read Data
        # Samples * Channels * BytesPerSample
        bytes_per_sample = 4 if header.format == 1 else 2
        data_len = header.samples_per_frame * header.channels * bytes_per_sample
        
        if data_len > self.size - ctypes.sizeof(AudioHeader):
            log(f"[Audio] Overflow size {data_len}")
            return None
            
        raw_data = self.mm.read(data_len)

        return {
            "rate": header.sample_rate,
            "channels": header.channels,
            "format": "FLOAT" if header.format == 1 else "INT16",
            "data": raw_data,
            "timestamp": header.timestamp_us
        }

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
        # Force ASCII for print to avoid Windows console encoding issues
        safe_msg = str(msg).encode('ascii', 'ignore').decode('ascii')
        print(f"[LOG] {safe_msg}", flush=True)
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
                prompt="Nexus agent command. Go to google. Click button.", # Prime for automation commands
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
        self.SILENCE_THRESHOLD = 200 # Lowered for quiet loopback signal
        
        self.visual_cortex = VisualCortexClient()
        self.audio_cortex = AudioCortexClient()
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

        # gRPC Server Thread
        if grpc:
            self.start_grpc_server()


        if sys.platform == 'win32':
            # import msvcrt
            # msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY) # Disable Binary Mode on Stdin (Breaks text input?)
            sys.stdout.reconfigure(encoding='utf-8')

        if self.visual_cortex.connected:
             print(" [Agent] Visual Cortex Linked [OK] + Audio Active [ON]. Listening...", flush=True)
             print(" [Agent] AUTO-BENCHMARK MODE ACTIVE: Switch tabs and scroll/play video to test FPS!", flush=True)
             print(" [Agent] To test input: Write 'debug type hello' to C:\\tmp\\neural_command.txt", flush=True)
        else:
             print(" [Agent] Audio-only mode [ON]. Listening for voice input...", flush=True)
        
        # Connect to Chrome via Extension (Non-blocking background thread)
        def try_extension_connection():
            print(" [Agent] Connecting to Chrome Extension...", flush=True)
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
        
        t_extension = threading.Thread(target=try_extension_connection, daemon=True)
        t_extension.start()

        # Connect to Chrome via CDP (Non-blocking background thread)
        def try_cdp_connection():
            print(" [Agent] Connecting to Chrome via CDP...", flush=True)
            time.sleep(1)  # Give Chrome time to start
            if self.cdp.connect():
                print(" [Agent] ✅ CDP Connected - Browser control ready!", flush=True)
            else:
                print(" [Agent] ⚠️  CDP connection failed - falling back to SendInput", flush=True)
        
        t_cdp = threading.Thread(target=try_cdp_connection, daemon=True)
        t_cdp.start()
        
        # AUTO-BENCHMARK LOOP
        frame_count = 0
        last_time = time.time()
        
        while True:
            # Poll for frames as fast as possible
            if self.visual_cortex.connected:
                header = self.visual_cortex.get_latest_frame()
                if header:
                    frame_count += 1
            
            # Poll for Audio (Shared Memory)
            # Always try to read/connect
            audio_chunk = self.audio_cortex.read_audio_chunk()
            if audio_chunk:
                # Pass raw bytes to processor
                self.process_audio(audio_chunk['data'])

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
                    pass # log(f"Silence count: {self.silence_chunks}/8")
            else:
                 # Buffer is empty. Log occasional noise floor stats
                 if self.silence_chunks % 10 == 0:
                      print(f"\r [Audio] Level: {rms:.0f}/{self.SILENCE_THRESHOLD}    ", end="", flush=True)
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

        # Amplify Audio (Digital Gain)
        try:
            count = len(self.audio_buffer) // 2
            values = struct.unpack(f"<{count}h", self.audio_buffer)
            # Find peak (avoid zero)
            max_val = max(abs(v) for v in values) if values else 0
            if max_val > 0 and max_val < 20000:
                # Target peak of 25000 (about 75% full scale)
                gain = 25000 / max_val
                gain = min(gain, 150.0) # Cap gain to avoid insane noise boost
                
                # Apply gain and clamp to int16 range
                new_values = [int(min(max(v * gain, -32768), 32767)) for v in values]
                self.audio_buffer = struct.pack(f"<{len(new_values)}h", *new_values)
                print(f" [Audio] Amplified {gain:.1f}x (Peak: {max_val} -> {int(max_val * gain)})", flush=True)
        except Exception as e:
            print(f" [Audio] Amp Error: {e}")

        # Save to WAV
        with wave.open(TEMP_WAV, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            # CRITICAL FIX: Use the actual sample rate (usually 48000 from Chrome)
            # Do NOT hardcode 16000 unless we resample (which we don't).
            # OpenAI Whisper handles 48k fine as long as header matches data.
            rate = getattr(self, 'audio_sample_rate', 48000)
            wf.setframerate(rate) 
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
        print(" [Agent] Commands: 'rec' = Force Listen, 'debug type ...' = Test Input", flush=True)
        while True:
            try:
                # Use standard input() which is thread-safe enough for this
                text = input(" > ")
                if text:
                    if text.lower() in ["rec", "listen", "record"]:
                        self.audio_cortex.trigger_listen()
                        continue
                        
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
                log(f"🧹 Input Sanitized: Detected repeated pattern. Reduced from {len(text)} to {len(sanitized)} chars.")
                text = sanitized
        
        # --- HALLUCINATION FILTER ---
        # Whisper often hallucinates these on silence/noise
        skip_phrases = [
            "MBC", "News", "Click", "Subscribe", "video", "button", 
            "Thanks for watching", "See you next time", "30", "50", "100"
        ]
        if len(text) < 50 and any(p.lower() in text.lower() for p in skip_phrases) and "nexus" not in text.lower():
             log(f"Ignoring Hallucination: '{text}'")
             return        # VERIFICATION SHORTCUT: Allow "benchmark" without wake word
        if "benchmark" in text.lower():
            self.benchmark_system()
            return

        if text.lower().startswith("go to ") or text.lower().startswith("open "):
             url = text.lower().replace("go to ", "").replace("open ", "").strip()
             # Basic URL fixer
             if not url.startswith("http"):
                  url = "https://" + url
             if "." not in url:
                  url += ".com" # extremely lazy fixer
             
             print(f" [Agent] 🧭 Direct Navigation: '{url}'", flush=True)
             if self.cdp.connected:
                 self.cdp.navigate(url)
             elif self.extension.connected:
                 self.extension.navigate(url)
             else:
                 print(" [Agent] ❌ No CDP/Extension for navigation.", flush=True)
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
                
                elif command.lower().startswith("go to ") or command.lower().startswith("open "):
                    url = command.lower().replace("go to ", "").replace("open ", "").strip()
                    # Basic URL fixer
                    if not url.startswith("http"):
                         url = "https://" + url
                    if "." not in url:
                         url += ".com" # extremely lazy fixer
                    
                    print(f" [Agent] 🧭 Direct Navigation: '{url}'", flush=True)
                    if self.cdp.connected:
                        self.cdp.navigate(url)
                    elif self.extension.connected:
                        self.extension.navigate(url)
                    else:
                        print(" [Agent] ❌ No CDP/Extension for navigation.", flush=True)
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
            print(f" [Agent] 🔇 Audio received, but ignored (No 'Nexus' wake word). Heard: '{text}'", flush=True)
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

                    elif "[NeuraAgentInput]" in line:
                         parts = line.split("[NeuraAgentInput]")
                         if len(parts) > 1:
                             input_text = parts[1].strip()
                             log(f"Received Agent Input: {input_text}")
                             print(f" [Agent] 🧠 Instructions Received: {input_text}", flush=True)
                             threading.Thread(target=self.process_text_command, args=(input_text,)).start()

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

    def start_grpc_server(self):
        try:
            server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
            service_pb2_grpc.add_NeuralServiceServicer_to_server(NeuralServiceImpl(self), server)
            server.add_insecure_port('[::]:50052')
            server.start()
            print(" [Agent] [GRPC] gRPC Server listening on port 50052", flush=True)
            self.grpc_server = server
        except Exception as e:
            log(f"Failed to start gRPC Server: {e}")



class NeuralServiceImpl(service_pb2_grpc.NeuralServiceServicer):
    def __init__(self, agent):
        self.agent = agent

    def GetState(self, request, context):
        # Flattened CDP DOM -> PageState
        
        state = page_state_pb2.PageState()
        # state.url set later
        
        if not self.agent.cdp.connected:
             self.agent.cdp.connect()
             
        if self.agent.cdp.connected:
            try:
                layout = self.agent.cdp.tab.call_method("Page.getLayoutMetrics")
                v = layout.get('cssVisualViewport', {})
                # Log Viewport dimensions for debugging capture drift
                log(f"GetState Viewport: {v.get('clientWidth')}x{v.get('clientHeight')} (scale: {v.get('zoom', 1)})")
                
                doc = self.agent.cdp.tab.call_method("DOM.getDocument", depth=-1, pierce=True)
                root = doc['root']
                state.url = root.get('documentURL', 'unknown')
                
                nodes = []
                
                nodes = []
                nodes = []
                input_nodes_to_resolve = []  # Track (nodeId, backendNodeId)
                
                def traverse(node):
                    tag = node.get('nodeName', '').lower()
                    if tag in ['style', 'script', 'head', 'meta', 'link']:
                        return
                        
                    n = page_state_pb2.Node()
                    n.id = node.get('nodeId', 0)
                    backend_id = node.get('backendNodeId', 0)
                    n.parent_id = node.get('parentId', 0)
                    
                    if n.id % 500 == 0: log(f"Traversing {n.id} tag={tag}")
                    
                    attrs = node.get('attributes', [])
                    attr_map = {}
                    for i in range(0, len(attrs), 2):
                        if i+1 < len(attrs):
                            key = attrs[i]
                            val = attrs[i+1]
                            attr_map[key] = val
                            n.attributes[key] = val
                    n.attributes['tag'] = tag
                    n.role = tag 
                    
                    # Name Heuristics:
                    candidate_name = attr_map.get('name', '') or attr_map.get('id', '')
                    if not candidate_name and 'placeholder' in attr_map:
                         candidate_name = attr_map['placeholder']
                    
                    if not candidate_name:
                         candidate_name = attr_map.get('class', '')

                    n.name = candidate_name
                    
                    # Layout Calculation (Rect) - Only for interactive or reCAPTCHA elements
                    interesting_tags = ['input', 'button', 'a', 'img', 'select', 'textarea', 'label']
                    is_rc = "rc-" in n.name.lower() or "rc-" in attr_map.get("class", "").lower()
                    
                    if tag in interesting_tags or is_rc or attr_map.get('role') in ['button', 'checkbox', 'link', 'textbox', 'image']:
                        try:
                            # Use backendNodeId for more stability with reCAPTCHA iframes
                            box = self.agent.cdp.tab.call_method("DOM.getBoxModel", backendNodeId=backend_id)
                            if box and 'model' in box:
                                quad = box['model']['content']
                                n.rect.x = int((quad[0] + quad[2] + quad[4] + quad[6]) / 4) # average X
                                n.rect.y = int((quad[1] + quad[3] + quad[5] + quad[7]) / 4) # average Y
                                n.rect.width = int(max(quad[0], quad[2], quad[4], quad[6]) - min(quad[0], quad[2], quad[4], quad[6]))
                                n.rect.height = int(max(quad[1], quad[3], quad[5], quad[7]) - min(quad[1], quad[3], quad[5], quad[7]))
                        except Exception as e:
                            # log(f"BoxModel failed for {n.id} (backend {backend_id}) {tag}: {e}")
                            # Fallback: Use JS getBoundingClientRect via resolveNode
                            try:
                                remote_obj = self.agent.cdp.tab.call_method("DOM.resolveNode", backendNodeId=backend_id)
                                obj_id = remote_obj['object']['objectId']
                                rect_result = self.agent.cdp.tab.call_method("Runtime.callFunctionOn",
                                    functionDeclaration="function() { let r = this.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; }",
                                    objectId=obj_id,
                                    returnByValue=True
                                )
                                r = rect_result.get('result', {}).get('value')
                                if r and r['w'] > 0:
                                    n.rect.x = int(r['x'])
                                    n.rect.y = int(r['y'])
                                    n.rect.width = int(r['w'])
                                    n.rect.height = int(r['h'])
                            except:
                                pass

                    # Heuristics
                    if tag == 'input':
                        t = attr_map.get('type', 'text')
                        if t in ['text', 'password', 'submit']:
                            n.role = "textbox" if t != 'submit' else "button"
                            input_nodes_to_resolve.append((n.id, backend_id))
                            n.value = attr_map.get('value', '')
                    if tag == 'button' or attr_map.get('role') == 'button':
                        n.role = 'button'
                        n.name = n.name or "Button" 
                    
                    if tag == 'a':
                        n.role = 'link'

                    if tag == '#text':
                         n.name = node.get('nodeValue', '')
                         n.role = 'text'

                    nodes.append(n)
                    
                    children = node.get('children', [])
                    for c in children:
                        c['parentId'] = n.id
                        n.children_ids.append(c.get('nodeId', 0))
                        traverse(c)
                        
                    content_doc = node.get('contentDocument')
                    if content_doc:
                        log(f"Traversing contentDocument for node {n.id}")
                        content_doc['parentId'] = n.id
                        n.children_ids.append(content_doc.get('nodeId', 0))
                        traverse(content_doc)
                        
                traverse(root)
                state.nodes.extend(nodes)
                
                # SECOND PASS: Read actual input values using backendNodeId (more stable)
                for node_id, backend_id in input_nodes_to_resolve:
                    try:
                        # Use backendNodeId for resolution
                        value_result = self.agent.cdp.tab.call_method("DOM.resolveNode", backendNodeId=backend_id)
                        obj_id = value_result['object']['objectId']
                        current_value_result = self.agent.cdp.tab.call_method("Runtime.callFunctionOn",
                            functionDeclaration="function() { return this.value; }",
                            objectId=obj_id,
                            returnByValue=True
                        )
                        actual_value = current_value_result.get('result', {}).get('value', '')
                        # Update the node in state.nodes
                        for n in state.nodes:
                            if n.id == node_id:
                                n.value = actual_value
                                break
                    except Exception as e:
                        log(f"Failed to read value for input node {node_id} (backend {backend_id}): {e}")
                # state.revision ...
                
            except Exception as e:
                log(f"gRPC GetState Error: {e}")
                
        return state

    def PerformAction(self, request, context):
        # request is AgentAction
        log(f"gRPC Action Received")
        
        if request.HasField('click'):
            target_id = request.click.element_id
            log(f"Action: CLICK -> {target_id}")
            if self.agent.cdp.connected:
                 try:
                     remote_obj = self.agent.cdp.tab.call_method("DOM.resolveNode", nodeId=target_id)
                     obj_id = remote_obj['object']['objectId']
                     
                     # Smart Click for React
                     js_function = """function() { 
                        // Heuristic: If it's a checkbox/radio, we must bypass the value tracker to ensure React sees the change
                        if (this.tagName === 'INPUT' && (this.type === 'checkbox' || this.type === 'radio')) {
                             // 1. Get Native Setter
                             const nativeSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 
                                'checked'
                             ).set;
                             
                             // 2. Toggle Value directly on prototype (bypassing React override)
                             nativeSetter.call(this, !this.checked);
                             
                             // 3. Dispatch Bubbling Click
                             const event = new MouseEvent('click', {
                                bubbles: true,
                                cancelable: true,
                                view: window
                             });
                             this.dispatchEvent(event);
                        } else {
                            this.click(); 
                            this.focus();
                        }
                     }"""
                     
                     self.agent.cdp.tab.call_method("Runtime.callFunctionOn", 
                        functionDeclaration=js_function,
                        objectId=obj_id
                     )
                 except Exception as e:
                     log(f"Click failed: {e}")

        elif request.HasField('type'):
            target_id = request.type.element_id
            text = request.type.text
            log(f"Action: TYPE -> {target_id} '{text}'")
            if self.agent.cdp.connected:
                try:
                    # simplistic type implementation using focus + Input.insertText
                    # 1. Focus
                    remote_obj = self.agent.cdp.tab.call_method("DOM.resolveNode", nodeId=target_id)
                    obj_id = remote_obj['object']['objectId']
                    self.agent.cdp.tab.call_method("Runtime.callFunctionOn", 
                       functionDeclaration="function() { this.focus(); }",
                       objectId=obj_id
                    )
                    time.sleep(0.1) # Wait for focus to settle
                    # 2. Use robust JS typing (handles replacement/events)
                    self.agent.cdp.type_text(text, object_id=obj_id)
                    
                    # 3. Handle Submit (Enter Key)
                    if hasattr(request.type, 'submit') and request.type.submit:
                        time.sleep(0.2) # Wait for React to process input
                        log(f"Action: SUBMIT (Enter Key)")
                        self.agent.cdp.press_key('Enter')
                        
                except Exception as e:
                    log(f"Type failed: {e}")
        
        elif request.HasField('navigate'):
            url = request.navigate.url
            log(f"Action: NAVIGATE -> {url}")
            if self.agent.cdp.connected:
                self.agent.cdp.navigate(url)

        return page_state_pb2.PageState()


        return page_state_pb2.PageState()

    def Navigate(self, request, context):
        url = request.url
        print(f" [Agent] [GRPC] gRPC Navigate to {url}", flush=True)
        if not self.agent.cdp.connected:
            print(" [Agent] Connecting CDP for nav...", flush=True)
            self.agent.cdp.connect()
            
        if self.agent.cdp.connected:
            print(" [Agent] CDP Connected. Executing navigate...", flush=True)
            self.agent.cdp.navigate(url)
        else:
            print(" [Agent] CDP Connection Failed.", flush=True)
        return page_state_pb2.PageState()

if __name__ == "__main__":
    # Standalone Mode (Default)
    # User must run Chrome manually with: 
    # chrome.exe --enable-logging --v=1 --user-data-dir=C:\tmp\neural_chrome_profile
    agent = NexusAgent()
    try:
        agent.run()
    except KeyboardInterrupt:
        print("\n[Agent] Shutting down...")


