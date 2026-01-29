import sys
import os
import time
import requests

def check_env():
    print("Checking Python Environment...")
    print(f"Python Executable: {sys.executable}")
    
    # 1. Imports
    try:
        import websocket
        print("[x] websocket-client installed")
    except ImportError:
        print("[ ] ERROR: websocket-client NOT installed. Run 'pip install websocket-client'")
        
    try:
        import pychrome
        print("[x] pychrome installed")
    except ImportError:
        print("[ ] ERROR: pychrome NOT installed. Run 'pip install pychrome'")

    # 2. Chrome Port
    print("\nChecking Chrome Connection (Port 9222)...")
    try:
        resp = requests.get("http://127.0.0.1:9222/json", timeout=2)
        if resp.status_code == 200:
            print("[x] Chrome Remote Debugging (CDP) is OPEN")
            targets = resp.json()
            page_tabs = [t for t in targets if t.get('type') == 'page']
            print(f" -> Found {len(page_tabs)} Page Tabs")
            for t in page_tabs:
                print(f"    - {t.get('title')} ({t.get('url')})")
        else:
            print(f"[ ] ERROR: Chrome responded with {resp.status_code}")
    except Exception as e:
        print(f"[ ] ERROR: Could not connect to Chrome on 9222: {e}")
        print(" -> Ensure Chrome is running with --remote-debugging-port=9222")
        print(" -> Ensure no firewall is blocking localhost")

if __name__ == "__main__":
    check_env()
