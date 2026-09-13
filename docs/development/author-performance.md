# Author interaction performance

Measure the public notebook entry point, not only direct invocation execution:

```console
uv run --locked python -m benchmarks run author-prepare --repetitions 3
```

Run from the public workspace (the `scopecat` submodule in a private checkout).
The command copies the virtual reference project into a temporary directory,
starts its daemon, and measures first prepare, repeated prepare, input and scan
edits, source refresh, and the next two prepares, then unchanged and failed
refreshes followed by prepare. It does not submit acquisition
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
an uncached retained revision is first used or evicted, but not on every prepare. Refresh selects
the new version without changing already prepared requests. Old revisions can
be loaded independently. Unversioned projects retain their fresh-process path.

Refresh keeps its validated application in the same process. Candidate compilation,
application loading and source-identity checks still run before CAS publication.
Publication and adoption share the launch pool lock, preventing a first caller
from restoring the just-published revision again. Validation runs outside that
lock, so existing versions can still prepare. The author revision service owns
this lifecycle; HTTP does not hold a separate launch pool.

At most one candidate validates alongside the two retained launch workers. The
60-second budget includes validation queueing, startup and waiting to publish;
failure, interruption or a generation conflict closes the unpublished candidate.
An unchanged refresh still validates, but keeps an equivalent warm worker and
closes the candidate. Adoption uses the same two-slot LRU, so a third revision
may evict an idle old worker; its retained source remains available for reopening.
The analysis pool is independent, giving a transient total of five revision
processes during validation, plus the existing execution processes. Successful
validation diagnostics record compilation, application and identity durations.
A prepare served by the adopted worker has provider timings without another
revision/application initialization phase. No machine-independent latency limit
is asserted by the benchmark.

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

## Retained analysis and comparison

```console
uv run --locked python -m benchmarks run author-analysis --repetitions 2
```

This copies the virtual reference project and records three virtual input runs
outside the measured operations. It then measures the ordinary typed analysis
API, repeated and edited arguments, comparison inspection and repeated/edited
fits. Every request executes its function and uses normal publication; an
identical publication may retain its existing receipt, but calculation results
are not cached. Setup time is reported separately and no physical devices are
used. Comparison follows analysis in this workload, so its first invocation may
already have a warm retained-data worker.

Retained-data work has a separate two-revision pool from launch work: a slow fit
does not occupy the prepare queue. Together the pools retain at most four
revision workers, in addition to existing execution workers. Within each pool
calls are serialized and process/queue waits are bounded by the existing call
budget. This trades bounded retained memory for interactive latency; it does
not make CPU-heavy fitting fast or provide unrestricted analysis concurrency.

Each request opens a fresh lab connection and reads the requested retained data.
Ordinary analysis still validates its module against the selected source tree.
Comparison candidate, rejection and handoff first reopen the exact saved
run/analysis/hash and select that source's worker; editing the caller's current
model, source or arguments cannot replace the retained selection. Unversioned
comparison catalogs continue to use their fresh-process path.

`Server-Timing` uses `launch` for total server operation time, `revision` and
`application` for worker initialization, `operation` for the actual retained-data
callback and `worker` for the enclosing worker call. These nested phases must
not be added together. Both author and comparison HTTP timings are retained in
the benchmark record. Errors discard the affected worker; the next explicit
request reloads it. Timeout responses warn that publication may already have
occurred and never automatically replay an operation. Input validation errors
keep their field locations rather than returning only a Pydantic help URL.
