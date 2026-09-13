# Author interaction performance

Measure the public notebook entry point, not only direct invocation execution:

```console
uv run --locked python -m benchmarks run author-prepare --repetitions 3
```

Run from the public workspace (the `scopecat` submodule in a private checkout).
The command copies the virtual reference project into a temporary directory,
starts its daemon, and measures first prepare, repeated prepare, input and scan
edits, source refresh, and the next two prepares. It does not submit acquisition
or connect to lab devices. The first call includes initial author validation;
this is a new-daemon baseline, not an OS filesystem-cache cold-start claim.
Record results under ignored `.benchmarks/` and compare the same host, Python,
project and workload. Windows CI correctness does not measure the lab PC.

Each record includes client operation wall times and HTTP `Server-Timing`
headers. `launch` includes active revision selection/initial validation, queue
wait, process startup and worker communication. `revision` measures restoring
and checking a pinned source/environment; `application` measures application
loading; `provider` includes the fresh connection, catalog and actual preview or
admission callback; `worker` encloses the worker phases and JSON encoding.
These are nested measurements and must not be added together. Interpreter and
framework imports precede `worker`; the residual between launch and worker is
not an import-only measurement. Initial validation and phase breakdown are also
visible in daemon diagnostics. HTTP timing ends at response headers; operation
wall time includes decoding and the complete client prepare call.

The daemon retains at most two author workers, one immutable source revision
per process, evicting the least recently used revision. Short launch calls are
serialized within the pool with bounded waiting; acquisition procedure workers
remain separate. Each call creates a fresh lab connection, obtains its catalog
and resolves current configuration. Source and application loading repeat when
a revision is first used or evicted, but not on every prepare. Refresh selects
the new version without changing already prepared requests. Old revisions can
be loaded independently. Unversioned projects retain their fresh-process path.

Author callbacks must use their request and lab connection for operation state;
module globals and the application factory are not per-request initialization
hooks. Refresh code to select a new import namespace. Restart the daemon after
changing the installed Python environment or maintained application code.
Parameters, manual-state fences and admission generations are still checked;
prepared results are not blindly reused. Worker failure discards that worker
and returns the original error. Neither failure nor timeout implicitly retries
a submission. Normal daemon shutdown closes retained workers.

The next performance priorities are first-use import/application cost, detailed
configuration/planning profiles, and the whole prepare-to-first-data path.
Retained worker RSS and concurrent-request latency also need representative
budgets before expanding this pool. Do not add more workers to hide repeated
work. Native kernels are justified by CPU/allocation profiles of bounded IR or
array workloads, not by time spent launching interpreters, importing modules or
waiting for I/O. Keep a Python reference and compare semantic output when a
kernel moves across a language boundary.
