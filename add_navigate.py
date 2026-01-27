
import os

NAVIGATE_CODE = r'''    def navigate(self, url):
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

'''

TARGET = r"c:/operation-greenfield/neural-chromium/src/glazyr/nexus_agent.py"

with open(TARGET, 'r', encoding='utf-8') as f:
    content = f.read()

# Locate CDPController
cdp_marker = "class CDPController:"
cdp_idx = content.find(cdp_marker)
if cdp_idx == -1:
    print("CDPController not found")
    exit(1)

suffix = content[cdp_idx:]

# Locate type_text
tt_marker = "    def type_text(self, text):"
tt_idx_rel = suffix.find(tt_marker)
if tt_idx_rel == -1:
    print("type_text not found in CDPController block")
    exit(1)

tt_idx_abs = cdp_idx + tt_idx_rel

# Insert before type_text
new_content = content[:tt_idx_abs] + NAVIGATE_CODE + "    " + content[tt_idx_abs:]

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Restored navigate method.")
