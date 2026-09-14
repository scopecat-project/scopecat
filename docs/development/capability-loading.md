# Capability loading boundaries

The first loading investigation in [#533](https://github.com/scopecat-project/scopecat/issues/533)
favors narrowing concrete dependency edges before introducing a new capability
registry. A fresh execution process still pays framework and application loading;
reusing preview workers does not remove that execution cost.

## Current execution path

`procedure_worker` enters before broad framework imports, then loads
`launch_worker`. The worker reads the retained procedure identity, restores its
exact author revision and loads the application from that source snapshot. The
application connects a `LabClient` and resolves the retained procedure through its
registry. These stages already appear in the `author-first-data` benchmark.

There are three distinct kinds of code in this path:

| Kind | Current owner | Loading requirement |
| --- | --- | --- |
| Wire contracts and transport | `DaemonClient`, command/view modules | Methods need their actual commands and responses; importing one broad facade currently reaches many unrelated families. |
| Capability declarations and identity | `LabApplication`, `AuthorExperiments.discover`, registries | Discovery imports authored modules, builds fingerprints and validates registration. It must expose the full catalog and fail clearly on invalid declarations. |
| Execution and data views | Project callbacks, compiler, scientific libraries, measurement adapters | Required when their operation runs; a dataset type annotation does not require constructing an Xarray view. |

The reference application registers ten procedures, including two discovered
authored experiments, alongside comparison and calibration capabilities. An
ordinary signal execution composes this entire application. Its declaration
modules also import analysis types. Consequently, moving one client import alone
can merely move the same dependency into application construction.

## First concrete boundary: measurement views

`Dataset` already retains raw measurement records and builds its canonical Xarray
representation on demand. Importing Xarray at module load defeated part of that
boundary: Xarray also loaded pandas before any data view was requested. The
reference comparison provider independently imported pandas during registration.
Moving only that provider import showed no end-to-end gain because the dataset
path still loaded both libraries.

Keep Xarray imports in the operations that actually construct or inspect Xarray
objects, and pandas in the reference provider's fit-publication path. Ordinary
Python imports provide the cache; there is no custom loader, extra registry,
background preload or new dependency fallback. Requirements remain installed as
before. Native import failures still surface at the operation requiring them.
Existing field semantics, missing-value handling, masks, projections and retained
publication formats are unchanged.

This does not make Xarray-dependent analysis free: its first call must pay the
import cost. Measure acquisition and first analysis separately, and also compare
their sum. Report loaded-process RSS separately from peak memory and total worker
memory. A smaller import timer without a better user boundary is not sufficient.

## Alternatives and decision

| Option | Benefit | Cost and decision |
| --- | --- | --- |
| Local imports at data-view or solver entry | Removes a demonstrated unrelated dependency from ordinary acquisition | First use still pays. Use where complete workloads show a benefit; preserve the public API. |
| Narrow client operation modules | Could avoid unrelated command/view definitions in short-lived execution | `LabClient` and application composition also import broad families. Next measure the transitive graph through application-ready and first data, not only `import DaemonClient`. |
| Separate capability metadata from callback loading | Could avoid entire unrelated project modules | Requires a revision-bound generated descriptor, exact callback identity, registry validation and a closure policy. Defer until simpler edges are measured and insufficient. |
| Separate hand-maintained GUI manifests | Cheap discovery | Rejected: creates a second declaration source and synchronization work for experiment authors. |
| Persistent execution workers | Amortizes imports | Outside this work: changes process isolation, lifetime and resource ownership. |

A future metadata/callback split should derive descriptors from the existing
validated source revision, rather than ask authors to declare experiments twice.
Validation must still discover all capabilities and check references before the
revision is activated. An execution loader would resolve an exact retained
procedure/version inside that revision, verify the descriptor identity, and load
its executable dependencies. Top-level functions and locally composed closures
need an explicit supported model before this becomes an API. Missing capabilities
must remain errors; there must be no fallback to a current revision.

Do not implement this larger split merely to shorten a benchmark. First establish
that remaining cost belongs to unrelated callback modules, quantify ordinary
submit-to-data and first affected capability use, and compare memory under the
existing bounded worker ownership. Catalog completeness, fingerprints, refresh,
old revision restoration and admission fences are acceptance requirements.

## Reproducing the evidence

Run the existing virtual `author-first-data` and `author-analysis` benchmarks from
[author performance](author-performance.md), serially on the same checkout,
Python environment and host. Include first/repeated submission, input edits and
refresh, then first analysis and first/repeated comparison fitting. Keep raw
records outside Git. `python -X importtime` and `cProfile` help attribute the graph;
their instrumented timings must not be substituted for ordinary latency samples.


### Matched local observations

On macOS 26.6.2 arm64, Python 3.14.7, a serial same-worktree on/off comparison
against `7214d0ff5` used the copied virtual reference project. Five ordinary
submissions per variant included first use, two repeats, an input edit and refresh.

| Boundary | Eager views | On-demand views |
| --- | --- | --- |
| Submit to first measurement ready | 1.287–1.363 s | 1.130–1.164 s |
| Application load in execution | 0.551–0.575 s | 0.398–0.409 s |
| Framework imports in execution | 0.443–0.455 s | 0.443–0.460 s |
| First prepare | 1.321 s | 1.148 s |
| Repeated prepare | 0.101–0.104 s | 0.096–0.099 s |
| First ordinary analysis, including required Xarray loading | 1.187 s | 1.177 s |
| First comparison fit after that analysis | 0.031 s | 0.034 s |
| Loaded-process RSS, three fresh processes | 213.0–213.1 MiB | 165.4–165.5 MiB |

The RSS samples import the normal launch-worker module and call the reference
project's `load_application`, then read `psutil.Process().memory_info().rss`.
Both variants retain ten procedures and two discovered authors; pandas is absent
only in the on-demand variant before a view is used. This is resident memory at
that boundary, not peak memory or aggregate worker consumption.

The first-analysis result includes loading the dependencies needed by its actual
Xarray operation. Comparison fitting here follows that analysis, so it is not a
standalone cold-fit claim. Retained-result visibility did not improve consistently:
its independent 0.2-second observer and completion boundary can conceal the earlier
first measurement. These local software observations are not Windows lab promises.
Initial cross-worktree/noisier trials did not establish a latency gain; the table
uses the serial same-worktree control rather than those exploratory timings.
