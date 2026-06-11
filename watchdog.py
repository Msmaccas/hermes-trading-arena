#!/usr/bin/env python3
"""Watchdog: restart stopped profile gateways without blocking."""
import subprocess, sys

profiles = ["oneil","buffet","lynch","minervini","qullamaggie","david-ryan",
            "matt-caruso","brian-shannon","dan-zanger","nick-schmidt",
            "hormozi","samovens","kallaway"]

# Get current profile list
result = subprocess.run(["hermes", "profile", "list"], capture_output=True, text=True, timeout=30)
started = 0
for line in result.stdout.split('\n'):
    parts = line.strip().split()
    if len(parts) >= 3 and parts[0] in profiles and parts[2] == "stopped":
        subprocess.Popen(["hermes", "-p", parts[0], "gateway", "run", "--replace"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started += 1

print(f"Watchdog: restarted {started} gateways")
sys.exit(0 if started >= 0 else 1)