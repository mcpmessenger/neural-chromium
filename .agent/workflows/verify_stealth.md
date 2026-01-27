---
description: Verify Neural Agent Stealth Capabilities (CAPTCHA/Bot Check)
---

This workflow runs the `test_stealth_check` scenario, navigating to a bot detection benchmark.

1. Navigate to src (if not already there)
   cd src

2. Run the Stealth Scenario
   // This runs option 4 implicitly if implemented with arguments, but for now interactive:
   echo "4" | c:\operation-greenfield\depot_tools\vpython3.bat nexus_scenarios.py
