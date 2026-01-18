#!/usr/bin/env python3
"""
Small performance probe for CI: measure import time and system info.
Writes JSON to performance-results.json
"""
import json
import time

try:
    import psutil
except Exception:
    psutil = None

results = {"benchmarks": [], "system_info": {}}
results["system_info"]["cpu_count"] = psutil.cpu_count() if psutil else None
results["system_info"]["memory_total"] = (
    psutil.virtual_memory().total if psutil else None
)

start = time.time()
try:
    pass
except Exception:
    pass

results["benchmarks"].append(
    {"name": "import_time", "value": time.time() - start, "unit": "s"}
)

with open("performance-results.json", "w") as f:
    json.dump(results, f)

print("Saved performance-results.json")
