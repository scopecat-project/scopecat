# Collect local command diagnostics

Use `scopecat diagnose` to retain evidence from an explicitly selected command.
It requires no laboratory project, private adapter, or reference lab. For a
small public-only check, run:

```sh
scopecat diagnose --output diagnostics-first -- python -c "print('diagnostic ready')"
```

Use the Python executable from the environment you want to inspect. The output
directory must be new. `--cwd PATH` chooses the command's working directory;
`--timeout 300` limits the whole command in seconds. Arguments after `--` are
passed directly to the process, without a shell. The command itself determines
what runs, including any device access; the diagnostic wrapper does not select
an experiment or impose an offline sandbox.

The wrapper returns a nonzero exit status when the case fails. It retains the
original exit code and output, timeout state, elapsed time and observed process
survivors. Cleanup targets the launched process and observed descendants using
process identities; it does not stop unrelated laboratory services. A short
process can spawn descendants between observations, so no observed survivor is
not proof of absence of every possible leak.

Read `report.md` for the summary and `command/worker.log` for original output.
`summary.json` and `command/outcome.json` retain machine-readable evidence.
The adjacent `.zip` includes selected diagnostic files only: report/metadata,
case logs and events, timing records, startup diagnostics and kernel logs. It
excludes project directories, databases, unrelated files and symbolic links.
Nothing is uploaded. Review commands, paths and log output before sharing them.

## Add workload phases

The child receives `SCOPECAT_DIAGNOSTIC_DIRECTORY`,
`SCOPECAT_TIMING_DIRECTORY` and `SCOPECAT_STARTUP_DIAGNOSTICS`. Scopecat components
use the latter two to write their existing timing and startup evidence.
A workload can append JSON objects to `events.jsonl` in the diagnostic directory:

```python
import json
import os
import time
from pathlib import Path

path = Path(os.environ["SCOPECAT_DIAGNOSTIC_DIRECTORY"]) / "events.jsonl"
with path.open("a", encoding="utf-8") as stream:
    stream.write(
        json.dumps(
            {
                "phase": "prepare",
                "status": "passed",
                "seconds": 0.25,
                "clock_ns": time.monotonic_ns(),
            }
        )
        + "\n"
    )
```

For first-ingestion correlation, record a `submit` phase with `status="started"`
and `clock_ns`, and a `run` event carrying `run_id`. The report matches that run
to `first_measurement_ingested` in the timing logs. This measures arrival at the
daemon, not GUI rendering. Unfinished phases are not zero-duration samples;
malformed or failed events remain failures. Keep individual samples when
comparing first use and subsequent calls; a new process does not clear OS caches.

## Laboratory-specific diagnostic suites

Maintainers can reuse `scopecat_server.host_diagnostics.run_case`, `summarize`
and `archive_diagnostics` for multiple named cases. Pass the working directory
explicitly, retain each case outcome, and set `metadata["expected_cases"]` to the
planned total. Pass only the actual case names to the archive function. Keep
workload selection, adapters, offline guards and scientific acceptance in the
laboratory consumer. These helpers collect software evidence, not a scientific
qualification or a persistent-data compatibility promise.
