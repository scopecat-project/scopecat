# Core workflow evaluations

Use these journeys to evaluate whether Scopecat helps a user complete an
experiment, understand its result and choose the next step. They connect product
feedback to executable evidence; they are not a promise to preserve old examples.

The reference lab began as a user-facing requirements probe with runnable
scenarios. Its fixed q0–q3 inventory, large bootstrap and gallery wrappers are
replaceable. Removing that implementation does not cancel the underlying goals.
Teaching belongs in [Help tutorials](../tutorials/teaching-sandboxes.md), mechanism
rules in focused core tests, and a small number of combined scientific and
cross-surface journeys in integration checks. New scenarios need not enter the
reference lab first. See [retirement boundaries](reference-gallery-retirement.md)
for the current evidence owners.

## Learn, edit and reopen

**Goal:** open a Help Notebook, edit ordinary author code, explicitly run it,
inspect the same result in Runs and continue after restart without reacquisition.

**Evidence:** `scripts/verify_notebook_journey.py` exercises Help preparation and
Continue with real independent kernels. Shipped editing/grouping/calibration
lessons and author-refresh journeys check retained source, inputs and results.
[Teaching checks](test-feedback.md#teaching-and-author-entry) identify the owners.

**Boundary and questions:** all seven supplied topics share Help entry. Course
ordering and difficulty remain [#565](https://github.com/scopecat-project/scopecat/issues/565).
Browser checks substitute external editor activation and native window plumbing;
actual editor use and unfamiliar-user observation remain
[#616](https://github.com/scopecat-project/scopecat/issues/616).
Can a learner distinguish editing, preview, acquisition and reading saved results?

## Control devices and acquire meaningful data

**Goal:** inspect availability, reserve several devices, perform typed operations,
then author and preview a scan using the same capabilities. Failures should name
the device or connection involved, and cleanup should leave outputs in a known state.

**Representative evidence:**

- `examples/reference_lab/tests/test_device_sessions.py`: multi-device direct
  control, calibrated physical routes, settled readback and parked/off state.
- `test_flux_spectroscopy.py` in the same directory: bias scan, complex VNA
  spectrum, temperature, fit and review with exact parameter/setup/source inputs.
- `test_quantum_composition.py` and `unit/test_quantum_runner.py`: host bias,
  point-local routes, Ramsey/raw IQ, signed IF/LO and device compilation.

**Boundary and questions:** device semantics include complex values, units,
physical ownership, entity alignment and compiled buffers. A compute-only fixture
cannot replace all of them. Simulations with partly known answers test software
and numerical contracts, not physical scientific correctness. The current Ramsey
composition response is bias-independent; it does not demonstrate flux physics.
Can users preview resource conflicts and attribute acquisition failures without
learning compiler internals or reading worker logs?

## Analyze, compare and choose the next experiment

**Goal:** select bounded data by meaningful coordinates, inspect live or offline
groups, publish analysis, compare retained runs and carry the chosen inputs into
the next experiment without manually reconstructing identity.

**Evidence:** core dataset and Arrow pagination tests own selection/export rules.
Reference `test_grouped_analysis.py`, `test_live_group_traces.py` and
`test_comparison.py` retain grouped publication/restart and comparison-to-next-run
journeys. See [grouped analysis](../how-to/grouped-analysis.md) and
[retained-run comparison](../how-to/retained-run-comparison.md).

**Boundary and questions:** pure compute/UI cases can move to smaller fixtures.
Keep representative live/offline, source, persistence and handoff interactions
where combining them catches a distinct failure. Analysis-author growth remains
[#561](https://github.com/scopecat-project/scopecat/issues/561).
Can users discover variables and materialization costs, explain changed results,
and follow a conclusion back to its actual inputs?

## Verify, adopt and recover calibration

**Goal:** fit a candidate, acquire independent verification evidence, inspect the
scientific effect, explicitly publish to a chosen branch and use that exact
revision. Joint calibration must remeasure all requested targets under combined
values and recover without duplicate execution or publication.

**Evidence:** reference `test_typed_candidates.py` retains DRAG acquisition,
fit/report, independent verification, branch adoption and accepted-gate execution.
It also covers joint/sequential orchestration, rejection, stale destinations,
restart and lost responses. Server branch and analysis tests own atomicity,
exact identity, conflict and current-format recovery rules. See
[calibration composition](calibration-composition.md).

**Boundary and questions:** verification alone changes no branch, default or setup.
Saving parameters is not scientific acceptance. Prepared work stays pinned when
a branch advances. Unknown dependency coverage cannot authorize selective reuse;
finer applicability remains [#783](https://github.com/scopecat-project/scopecat/issues/783).
The old global-default restoration journey is retired. Does review explain the
candidate's effect and applicability, and can users distinguish rejection,
uncertainty and a publication conflict?

## Continue across the desktop and author workspace

**Goal:** choose a target, edit and explicitly adopt working parameters, preview
and submit once, inspect the exact result, then return to the next edit. Source
changes and uncertain responses must not silently change admitted work.

**Evidence:** UI `object-parameters.e2e.ts`, `parameter-working-inputs.e2e.ts` and
`application-loop.e2e.ts` cover target/setup handoff, conflicting drafts, restart,
stale previews, per-window selection and lost-response recovery. Reference author
refresh tests retain admitted source through edits and restore. The
[ordinary author entry](test-feedback.md#ordinary-settings-author-entry) checks
independent Python preparation and read-only reopening.

**Boundary and questions:** these are software journeys; installed delivery and
native interaction have [separate qualification](architecture/desktop-packaging.md).
Can users resolve submission uncertainty without acquiring twice, and understand
which source and parameter revision produced the selected run?

## Using the evaluations

Review the shortest useful journey, the executable evidence and the remaining
gap together. Look for unnecessary decisions, unfamiliar concepts, manual ID
transfer, ambiguous state, poor error attribution and broken cross-surface context.
Do not make users manage workers, leases or storage records to compensate for a
product gap.

Update these goals when the desired workflow changes. Put current gaps and
completion conditions in the linked issues, and implementation/validation in PRs.
The representative groups above do not commit to retaining all eight historical
scenario groups unchanged or mark them complete. Retirement work remains
[#773](https://github.com/scopecat-project/scopecat/issues/773), under audit umbrella
[#615](https://github.com/scopecat-project/scopecat/issues/615).
