
import os

TYPE_TEXT_NEW = r'''    def type_text(self, text):
        """Type text via CDP Input Domain (Trusted Events)"""
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            # 1. Ensure focus (Best effort via JS)
            # Find input if not focused
            js_focus = """
            (function() {
                let el = document.activeElement;
                if (!el || (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && !el.isContentEditable)) {
                    el = document.querySelector('input[type="text"]:not([style*="display: none"])') ||
                         document.querySelector('input[type="search"]') ||
                         document.querySelector('textarea') ||
                         document.querySelector('[contenteditable="true"]') ||
                         document.querySelector('input:not([type="hidden"])');
                    if (el) el.focus();
                }
                return el ? true : false;
            })()
            """
            self.tab.call_method("Runtime.evaluate", expression=js_focus)
            
            # 2. Use Input.insertText (Trusted)
            self.tab.call_method("Input.insertText", text=text)
            
            log(f"CDP Typed (Trusted): {text}")
            return True
            
        except Exception as e:
            log(f"CDP type failed: {e}")
            return False'''

PRESS_KEY_NEW = r'''    def press_key(self, key):
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
            return False'''

TARGET = r"c:/operation-greenfield/neural-chromium/src/glazyr/nexus_agent.py"

with open(TARGET, 'r', encoding='utf-8') as f:
    content = f.read()

# Locate CDPController
cdp_marker = "class CDPController:"
cdp_idx = content.find(cdp_marker)
if cdp_idx == -1:
    print("CDPController not found")
    exit(1)

# Look in suffix
suffix = content[cdp_idx:]

# Locate type_text
tt_marker = "    def type_text(self, text):"
tt_idx_rel = suffix.find(tt_marker)
if tt_idx_rel == -1:
    print("type_text not found in CDPController block")
    exit(1)

tt_idx_abs = cdp_idx + tt_idx_rel

# Locate press_key
pk_marker = "    def press_key(self, key):"
pk_idx_rel = suffix.find(pk_marker)
if pk_idx_rel == -1:
    print("press_key not found in CDPController block")
    exit(1)

pk_idx_abs = cdp_idx + pk_idx_rel

# Locate END (Visual Cortex section)
vc_marker = "# --- Visual Cortex"
end_idx_rel = suffix.find(vc_marker)
if end_idx_rel == -1:
    print("Visual Cortex marker not found")
    # Fallback to import mmap
    end_idx_rel = suffix.find("import mmap")
    
end_idx_abs = cdp_idx + end_idx_rel if end_idx_rel != -1 else -1

if end_idx_abs == -1:
    print("End marker not found")
    exit(1)

# Replace
# From tt_idx_abs to pk_idx_abs (Replace type_text)
# From pk_idx_abs to end_idx_abs (Replace press_key)
# We can replace the whole chunk from tt to end?
# Assuming NO other methods between them or after them in CDPController.
# Based on check, press_key is last.

new_content = content[:tt_idx_abs] + TYPE_TEXT_NEW + "\n    \n" + PRESS_KEY_NEW + "\n    \n\n\n" + content[end_idx_abs:]

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Successfully applied patch v2.")
