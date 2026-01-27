
TARGET = r"c:/operation-greenfield/neural-chromium/src/glazyr/nexus_agent.py"

with open(TARGET, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # Look for the double indented press_key
    if line.startswith("        def press_key(self, key):"):
        print("Found bad line, fixing...")
        new_lines.append("    def press_key(self, key):\n")
    else:
        new_lines.append(line)

with open(TARGET, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Fixed indentation.")
