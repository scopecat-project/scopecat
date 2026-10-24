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
| Narrow client operation modules | Could avoid unrelated command/view definitions in short-lived execution | `LabClient` and application composition also import broad families. A per-method import prototype shortened client import but did not improve application-ready or first-data latency; do not retain that churn. |
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


### Historical experiment and limits

The [fixed experiment record](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/capability-loading.md#matched-local-observations)
preserves host, revisions, matched observations, outlier and prototype results.
On-demand dataframe views reduced application-load cost and loaded-process RSS
in that local comparison; retained-result visibility did not improve consistently.
RSS was measured at the loaded-process boundary, not peak or aggregate memory.
First analysis included Xarray loading; the subsequent fit was not a cold-fit test.
These software observations are not Windows laboratory promises.

The per-method client-import prototype shifted work into later imports without
establishing an end-to-end improvement. The isolated slow first process was not
evidence of antivirus, a cold-start guarantee or a fix for #465.

Decision: discard the per-method import prototype. Application declarations and
execution dependencies bring nearly all of the same graph back before useful work.
An import-only speedup is not a reason to expand transport code. Keep the existing
client and capability registration APIs, retain the demonstrated dataframe-view
boundary, and finish this design investigation without introducing a descriptor
registry. Reopen a larger split only with a workload showing substantial unrelated
callback/dependency cost after simpler boundaries are exhausted.

The next performance work belongs to the existing #523 resource and provider
budgets: measure retained worker memory/revision churn and configuration/planning
CPU or allocations. Native kernels need a bounded computation bottleneck; this
import redistribution supplies no justification for one.
