
import os
import sys

TARGET = r"c:/operation-greenfield/neural-chromium/src/glazyr/nexus_agent.py"

NEW_CODE = """    def type_text(self, text):
        \"\"\"Type text via CDP Input Domain (Trusted Events)\"\"\"
        if not self.connected:
            log("CDP not connected")
            return False
            
        try:
            # 1. Ensure focus (Best effort via JS)
            # Find input if not focused
            js_focus = \"\"\"
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
            \"\"\"
            self.tab.call_method("Runtime.evaluate", expression=js_focus)
            
            # 2. Use Input.insertText (Trusted)
            self.tab.call_method("Input.insertText", text=text)
            
            log(f"CDP Typed (Trusted): {text}")
            return True
            
        except Exception as e:
            log(f"CDP type failed: {e}")
            return False"""

with open(TARGET, 'r', encoding='utf-8') as f:
    content = f.read()

# Locate Start
start_marker = '    def type_text(self, text):'
start_idx = content.find(start_marker)
if start_idx == -1:
    print("Could not find start marker")
    sys.exit(1)

# Locate End (Start of next method or end of this one)
# Finding the start of 'press_key' is safest
end_marker = '    def press_key(self, key):'
end_idx = content.find(end_marker)

if end_idx == -1:
     print("Could not find end marker")
     sys.exit(1)

# Preserve the space before 'def press_key'
# We replace from start_idx up to end_idx (exclusive of end_marker)
# But we need to keep the empty lines before press_key?
# The NEW_CODE ends with return False (indented).
# We should ensure we don't eat the lines between.

pre_content = content[:start_idx]
post_content = content[end_idx:]

# The previous content had some blank lines before press_key.
# Let's just glue them.

new_full_content = pre_content + NEW_CODE + "\n    \n" + post_content

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(new_full_content)

print("Successfully patched nexus_agent.py")
