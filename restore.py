
import os

RECOVERY_BLOCK = r'''    def type_text(self, text):
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
'''

TARGET = r"c:/operation-greenfield/neural-chromium/src/glazyr/nexus_agent.py"

with open(TARGET, 'r', encoding='utf-8') as f:
    content = f.read()

# We need to find the BROKEN block and replace it.
# The broken block starts at '    def type_text(self, text):' (The CDP one)
# And ends at '    def press_key(self, key):' (The Extension one)

# But wait, we have TWO 'def type_text'.
# 1. The broken one (InputManager).
# 2. The CDP one (CDPController).
# The Broken one is FIRST.

s_idx = content.find('    def type_text(self, text):')
if s_idx == -1:
    print("Start not found")
    exit(1)

e_idx = content.find('    def press_key(self, key):') # This targets ExtensionController.press_key (lines 280)
if e_idx == -1:
    print("End not found")
    exit(1)

# Check context
pre = content[:s_idx]
post = content[e_idx:]

# The middle is what we replace.
# Verify middle looks broken (contains self.tab)
middle = content[s_idx:e_idx]
if 'self.tab' not in middle:
    print("Warning: Middle block doesn't look like the broken CDP patch. Aborting.")
    print(middle[:200])
    exit(1)

# Replace
new_content = pre + RECOVERY_BLOCK + '\n    ' + post

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Restoration complete.")
