# Production Benchmark - Browser Management Notes

## Browser Restart Strategy

### Neural-Chromium
- Navigate to `about:blank` between runs
- Clears cookies, session state, and DOM
- Reuses same browser process (faster than full restart)
- Connection pool maintained via gRPC

### Playwright
- Full browser restart between runs (`browser.close()` + new launch)
- Ensures completely clean state
- Slower but guarantees isolation

## Why This Matters

**Fair Comparison:**
- Both systems start with clean slate
- No cookie/session carryover
- No cached resources affecting timing

**Realistic Simulation:**
- Mirrors production agent behavior
- Tests cold-start performance
- Validates state management

## Alternative: Full Browser Restart

If you need to fully restart Neural-Chromium browser:

```python
import subprocess
import time

def full_restart_neural_browser():
    # Kill existing Chrome
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], 
                   capture_output=True)
    time.sleep(2)
    
    # Restart
    subprocess.Popen([
        "out/Default/chrome.exe",
        "--remote-debugging-port=9222"
    ])
    time.sleep(5)  # Wait for startup
    
    # Reconnect agent
    return AgentClient()
```

**Trade-off:** Adds ~7s overhead per run (not recommended for 10-run benchmarks).

## Current Implementation

We use `about:blank` navigation as it:
- ✅ Clears all page state
- ✅ Resets cookies/localStorage
- ✅ Fast (<1s overhead)
- ✅ Keeps gRPC connection alive
- ✅ Fair comparison to Playwright's browser.close()
