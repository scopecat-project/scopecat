"""Temporary bounded runner-side sampling control, no external dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path("test-diagnostics")
results = []
for mode in ("0", "1"):
    tag = f"frame-sample-{mode}"
    with (root / f"{tag}.stderr").open("w") as stderr:
        run = subprocess.run(
            [sys.executable, "scripts/diagnose_frame_probe.py"],
            env={**os.environ, "PROBE_SAMPLE": mode,
                 "PROBE_TRACE": str(root / f"{tag}.trace")},
            stdout=subprocess.PIPE, stderr=stderr, text=True, timeout=10,
        )
    result = {"mode": mode, "returncode": run.returncode, "stdout": run.stdout}
    results.append(result)
    print(json.dumps(result), flush=True)
(root / "probe-results.json").write_text(json.dumps(results, indent=2))
raise SystemExit(any(result["returncode"] != 0 for result in results))
