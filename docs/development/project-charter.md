# Scopecat Project Charter

Scopecat is a local-first Python toolkit for laboratory experiment workflows,
from a first scan to sustained, large-scale quantum experiments. It combines an
approachable starting point with typed experiment structure, bounded execution,
live visibility, and durable results as a workflow grows.

It can coexist with notebooks and existing Python projects. Ordinary Python
remains available for composition, analysis, and adaptive orchestration, while
declarative experiment abstractions capture the structure needed for planning,
compilation, reproducibility, and scalable execution.

The selected application direction is a unified experiment workbench with
[session-scoped experimental contexts](architecture/experiment-contexts.md):
sample/assembly, batch, working point, code and operator choices have independent
ownership. Teaching belongs under Help and uses protected simulated contexts.
This is a product direction, not a claim that the current teaching host already
implements shared experimental execution.

## Current Stage

Scopecat is an early, single-user project. Its current goal is to make
structured experiments easy to adopt and increasingly valuable as they are
repeated, compared, inspected, and maintained. Simple one-off experiments may
remain clearest as ad hoc Python; Scopecat does not need to match their initial
implementation simplicity.

Adoption should be possible one workflow at a time alongside existing experiment
systems. Moving an imperative workflow into Scopecat-managed execution may
require rewriting it at the execution boundary; this is distinct from requiring
a laboratory to migrate its entire codebase at once.

Success currently means:

- a new user can run a virtual experiment quickly and control instruments
  through familiar Python APIs;
- small experiments remain concise without exposing storage, coordination, or
  compilation machinery;
- typed experiment definitions support validation, batching, durable data, and
  reuse while ordinary Python retains its natural roles;
- progress, partial measurements, failures, results, and workflow lineage remain
  easy to inspect beyond the originating notebook;
- datasets larger than notebook memory remain usable through bounded notebook
  batches and GUI previews;
- durable, time-bounded execution can be introduced for selected workflows
  without requiring a codebase-wide migration.

Features and abstractions should advance one of these adoption or growth paths.

Compatibility work should focus on explicit adoption seams rather than a broad
brownfield promise. A lower-level external-run recording path, for example,
could let legacy code retain execution ownership while Scopecat records declared
points, measurements, and optional live telemetry. Such runs must remain
distinguishable from Scopecat-managed runs: recording observations does not
imply planning, resource ownership, cleanup, or reproducibility guarantees.

## Scalability Direction

Near-term scalability focuses on a single-lab NISQ workflow. The reference
envelope includes hundreds of qubits, couplers, and control channels;
repeated-shot and structured acquisition data larger than notebook memory; and
analysis-driven workflows spanning many related runs over hours or days.

Individual runs should fit a useful hardware and stability window. Workload
volume commonly grows through shots, measured entities, and point-local sample
axes, then continues through analysis into later runs and refined point domains.
Dense spectroscopy and characterization provide an additional case with a much
larger logical point domain in one run.

Planning, execution, persistence, observation, and analysis should remain
bounded and incremental. Resource use and control-plane activity should follow
physical batches, checkpoints, and data chunks rather than individual shots or
samples. Logical point, product, and lineage identities should remain stable
across physical batching choices.

## Principles

- Optimize both adoption cost and growth cost.
- Keep user-visible complexity proportional to the workflow and expose advanced
  controls progressively.
- Use typed, declarative abstractions when a closed representation enables
  validation, batching, reproducibility, or durable interpretation; use ordinary
  Python for composition, analysis, and adaptive orchestration.
- Hide storage, coordination, compilation, and execution machinery from normal
  user code.
- Keep planning, data movement, queries, and visualization within explicit
  batch, page, chunk, or preview budgets.
- Preserve scalable semantics early and evolve their implementation through
  measured reference workloads.
- Make advanced provenance and recovery available progressively.
- Do not silently retry a hardware write when its outcome is unknown.
- Prefer simple models and decisive internal changes, judging architecture by
  the user workflows it enables.

## Current Non-Goals

- Comprehensive laboratory safety or interlock enforcement.
- Multi-user scheduling or distributed execution.
- General recovery from every interrupted hardware operation.
- Replacing plotting, analysis libraries, notebooks, or all existing drivers.
- A universal representation for every laboratory domain.
- Becoming an ELN, LIMS, data warehouse, or general automation platform.
