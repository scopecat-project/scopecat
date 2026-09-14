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

## Submission to first visible data

```console
uv run --locked python -m benchmarks run author-first-data --repetitions 2
```

This benchmark uses the ordinary `prepare().run()` / `job.wait()` APIs on a
copied virtual signal experiment with three computed points. It measures initial
and repeated runs, changed input and a run after source refresh. Preparation is
reported separately; all event offsets share the same submission origin.
It is a software-path baseline, not a physical trigger/ADC latency measurement.
The existing scan-execution benchmark remains the lower-level acquisition probe.

Sparse, opt-in diagnostic events distinguish:

- Dispatch, child entry, framework imports, source restoration and application
  loading. The gap before child entry includes interpreter startup; framework
  imports begin after the lightweight timing helper is available.
- Run admission, the first completed measurement batch offered to transport,
  the first batch accepted into the daemon's live measurement store, and the
  terminal run commit. Batch readiness is not the first hardware trigger.
- The normal wait return and the first materialized result read by an independent
  ordinary client. The observer uses `job.result()` and a 0.2-second read-only
  polling interval; trace files are never used to discover its run identity.

`job.result()` currently exposes a retained step output, so this observation is
later than live data entering the daemon. It does not measure the live-preview
endpoint or GUI rendering. The observer adds HTTP traffic and up to one polling
interval of visibility delay; its read can finish before or after `job.wait()`.
These event offsets overlap and must not be summed. The benchmark retains the
run, procedure and source identities needed to interpret each sample.

The timing files use per-process JSONL and the same host's monotonic clock. The
benchmark creates and reads them automatically and embeds the selected events in
its normal result record. For a developer investigation, create a directory and
set `SCOPECAT_TIMING_DIRECTORY` before starting the daemon and clients. Unset it
when finished. Disabled timing does not write files; unavailable diagnostic
storage emits a warning and does not change an execution or publication outcome.
Do not merge timestamps from different machines. Store evidence under ignored
benchmark output, not in source control.

A local Mac sample found immediate dispatch and roughly 40ms acknowledgement,
but about 1.1s of fresh framework/application loading per execution. First
measurements entered the daemon around 1.37s after submission; the ordinary
retained-result observer read them around 1.56s. These are observations, not
thresholds or a claimed runtime speedup. A short virtual computation does not
justify removing execution isolation, sharing a live hardware worker, shortening
polling intervals, or porting startup work to a native kernel.

Ordinary authors can now use `job.progress()` and `job.preview()` to observe the
exact unsettled acquisition, including its latest received record before a flush.
See the [managed author workflow](../how-to/managed-author-session.md#observe-an-experiment-before-it-finishes).
This benchmark deliberately retains its original `job.result()` boundary. Live
preview availability is verified with an intentionally paused virtual acquisition,
not represented as a faster execution or silently substituted timing series.
Broader import/capability loading and GUI rendering costs remain separate work.

## Startup model construction

The daemon command/view families defer Pydantic validator and serializer building
until a model is used. A short-lived execution client does not need to build every
GUI, configuration and analysis response model when it imports the client module.
Field definitions, validation rules, frozen commands and JSON schemas remain the
same; first use still performs normal schema construction and validation. This
uses Pydantic's model lifecycle, without an additional application-level cache.

Measure the complete first-data workload when evaluating this change: moving
construction into the first request is not itself a gain. A same-worktree on/off
control observed a modest reduction of roughly 30–50ms in repeated submission to
first measurement, and about 6 MiB less RSS after loading the reference application.
That RSS observation is for one loaded process, not peak execution memory or the
sum of physical memory consumed by all workers. Earlier measurements under higher
host load overstated the time difference and are not the improvement claim.

The remaining startup cost includes broad client/model imports and application
registration of capabilities unrelated to the selected experiment. The
[loading-boundary investigation](capability-loading.md) records the execution
path, the concrete measurement-view boundary and alternatives. Keep the existing
registration API: a per-method client-import control only moved loading into
application construction and was discarded. Further work follows measured worker
resource and provider budgets; do not introduce a
second declaration source, execution pool or weaker source/admission checks.

## Instrument child startup evidence

For pre-health failures, set `SCOPECAT_STARTUP_DIAGNOSTICS` to an output directory
before one normal `start`. Windows CI already retains this directory. The
`daemon-startup-<pid>.log` records spawn and readiness receipt; the matching
`instrument-startup-<pid>.log` records entry, output capture, RPC imports, backend
factory loading/construction, catalog description and readiness transmission.
PID, parent PID, generation and same-host monotonic clock anchors correlate the
files without exposing environment contents or Python locals. This opt-in probe
samples one daemon stack after eight seconds and one instrument stack after five
seconds; the child cancels its sample when readiness is sent. These offsets start
at Python entry, not process launch, and do not alter the ten-second health
deadline. A stack sample alone does not mean startup failed.

A parent blocked in `Connection.poll` does not identify a slow import or driver.
Use the child's last phase and stack to locate the wait. A missing child entry
limits visibility to process bootstrap, entry-module loading or diagnostic setup;
it is not proof of an antivirus cause. The supported start launcher passes a monotonic anchor so the daemon also
records elapsed time from the launch request to its Python entry. This includes
process creation and early module setup; it does not identify their individual
costs. The health deadline starts after process creation returns, so this anchor
is not an exact deadline timestamp. Direct entry-module invocation has no anchor. Retain failed starts alongside successful starts and distinguish
fresh installations from repeated fixture runs.

A controlled backend-construction stall verifies evidence and actual child
termination at the existing endpoint startup timeout. This is diagnostic coverage,
not a reproduction or fix of intermittent Windows startup. Neither the daemon's
health deadline nor worker deadlines, retries, persistence or acquisition behavior
are changed. Broader startup reliability remains tracked in issue #465.


After readiness decoding, daemon stages distinguish driver/payload catalog
materialization, receiver startup, offline schema inspection, object-store setup,
SQLite connection/WAL/schema initialization, service composition, application
construction, configuration bootstrap and application service startup. The last
completed stage identifies the interval to investigate; an eight-second stack can
locate code still running within it. A controlled configuration-factory stall
checks this post-readiness evidence using the normal start deadline. It does not
reproduce the intermittent Windows failure. Retain the phase log even if Python
entry was too late for the sample to occur before the parent's health deadline.


## Worker residency and revision churn

```console
uv run --locked python -m benchmarks run author-residency --revisions 3
```

Run from the public source checkout on the target machine; this benchmark is not
an installed-wheel command. It uses a copied virtual reference project and makes
no physical device calls. One real retained signal run supplies analysis input.
Preparing and analyzing the same revision should reuse its worker; three or more
source revisions exercise LRU eviction in both pools. Returning to the original
revision must reconstruct its exact source in a new process. Normal shutdown is
followed by up to five seconds of observation of previously seen process identities;
this does not change production shutdown deadlines or retry shutdown.

The result records operation times and per-process PID, creation time, module,
revision and RSS at completed-operation checkpoints. Validation workers adopted
by the prepare pool count as prepare workers. Other descendants and the daemon
are recorded separately; processes that disappear during a sample are listed.
Both pools retain at most two workers. Transient validation candidates are not
captured by these settled checkpoints. RSS includes shared pages, so adding four
worker RSS values does not measure four independent physical allocations.

Use this evidence before changing pool capacity or adding idle eviction. It does
not establish a memory threshold, absence of long-session leaks, fair concurrent
scheduling or shutdown correctness during an active request. Those require bounded
workloads of their own. The current benchmark intentionally leaves scheduling,
retained-process policy, execution isolation and retry behavior unchanged.


On one Mac/Python 3.14.7, serial three- and five-revision trials kept both pools
within two workers and found no surviving observed processes after shutdown.
The five-revision trial settled near 172 MiB RSS per prepared worker and 218 MiB
per analysis worker; the three-revision trial's first workers were higher
(about 181 and 232 MiB). These are checkpoint observations, not a leak threshold.
Repeated prepare/analysis took about 0.092/0.013 seconds in the five-revision trial;
restoring the evicted original took 1.265/1.706 seconds. Shutdown took 1.8–2.0 seconds.

The initial three-revision trial spent 26.7 seconds in the virtual run operation
(`run().wait().result()`), versus 1.54 seconds in the later five-revision trial.
The first prepare was also 4.23 versus 1.24 seconds. This benchmark does not split
that run operation into startup/measurement/result phases, so it cannot attribute
or explain the outlier. Preserve it separately and use the first-data trace for
any recurrence; a later successful trial is not a startup reliability fix.

The measurements support keeping the current bounded pools while gathering
concurrent/longer-session evidence. They do not justify shrinking capacity or
adding idle eviction merely to reduce an RSS sum. Next separate queue contention,
eviction/reload cost and provider CPU/allocation work before changing ownership.
