"""Temporary bounded runner-side sampling control, no external dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path("test-diagnostics")
results = []
for mode in ("off", "old", "new"):
    tag = f"frame-sample-{mode}"
    started = time.monotonic()
    with (root / f"{tag}.stderr").open("w") as stderr:
        run = subprocess.run(
            [sys.executable, "scripts/diagnose_frame_probe.py"],
            env={**os.environ, "PROBE_SAMPLE": mode,
                 "PROBE_TRACE": str(root / f"{tag}.trace")},
            stdout=subprocess.PIPE, stderr=stderr, text=True, timeout=10,
        )
    trace = (root / f"{tag}.trace").read_text()
    result = {"mode": mode, "returncode": run.returncode, "stdout": run.stdout,
              "elapsed_seconds": time.monotonic() - started,
              "samples": trace.count("Timeout (") + trace.count("Python timer sample"),
              "invalid_frames": trace.count("<invalid frame>"),
              "trace_bytes": len(trace.encode())}
    results.append(result)
    print(json.dumps(result), flush=True)
(root / "probe-results.json").write_text(json.dumps(results, indent=2))
raise SystemExit(any(result["returncode"] != 0 for result in results))
