# Core workflow evaluations

This internal document turns user documentation and executable examples into
product design feedback. It evaluates outcomes and conceptual burden rather
than visual polish. UI layout, labels, and source-install commands are allowed
to change during internal iteration.

## Evaluation method

For each workflow, maintain three things:

1. **Target journey** states the shortest experience Scopecat should make
   natural, without accommodating current implementation accidents.
2. **Executable evidence** names the checked script or test that exercises the
   current path.
3. **Success evidence** describes what a user can observe when the product has
   delivered the intended value.

The difference between the target journey and current evidence is design
backlog. Do not close that difference by teaching ordinary users to manage
workers, leases, wire models, storage entries, generations, or daemon URLs.

Review each workflow against these questions:

| Dimension | Design question |
| --- | --- |
| First value | How many decisions and state-changing steps precede a meaningful result? |
| Concept load | Which terms must be understood before the user can continue? |
| Identity load | Which IDs must be copied or correlated manually? |
| State visibility | Can the user tell what is running, accepted, active, or failed? |
| Error attribution | Does failure identify the responsible project, device, run, analysis, or configuration? |
| Cross-surface handoff | Does CLI, Python, and GUI context carry over without re-entry? |
| Repeatability | Can the user rerun the work and explain why results or configuration differ? |
| Traceability | Can a result be followed back to its run, inputs, configuration, and evidence? |

## 1. Create a project and complete the first run

**Target journey:** initialize a project, start it, run the generated first
experiment, and find the same durable run in the project console without
supplying service locations or internal identities.

**Executable evidence:** the generated `notebooks/01_first_run.py` exercised by
`packages/scopecat-server/tests/test_lifecycle.py`, and the
[source preview quickstart](../getting-started/quickstart.md).

**Success evidence:** terminal `completed` status, one stable run ID, persistent
run history, and automatic notebook/GUI discovery of the same project daemon.

**Current design feedback:** source checkout still requires a separate UI build
and `--static-dir`. This is a distribution gap; it should disappear from the
end-user journey rather than become a permanent concept.

## 2. Inspect and directly control configured instruments

**Target journey:** see which configured instruments are available, reserve the
needed devices, perform typed operations immediately, and attribute failure to
one device or connection.

**Executable evidence:** server instrument-view and direct-control tests, with
`10_direct_control.py` temporarily retained for coupled virtual-device behavior.
The old fixed-inventory tour is retired; see the
[retirement inventory](reference-gallery-retirement.md).

**Success evidence:** inventory and availability are visible, temperature and
trace receipts succeed, coupled virtual behavior is observable, and the source
output is disabled on exit.

**Design questions:** determine whether ordinary operators must understand
provider/driver identity, and whether reservation, connection, command, and
cleanup failures remain distinguishable without reading worker logs.

## 3. Author, preview, and run an instrument experiment

**Target journey:** use the same typed capability vocabulary in an experiment,
preview points and resource requirements, run it, and receive the authored
result with no manual recording schema or execution-phase management.

**Executable evidence:** `20_flux_spectroscopy.py` and its reference-lab tests.

**Success evidence:** previewed point count matches execution, the run reaches a
terminal state, measurements retain declared coordinates and observables, and
the project console can explain the selected configuration and instruments.

**Design questions:** role and route concepts should appear when integrating or
diagnosing a lab, not as ceremony in the common experiment path. Preview should
describe conflicts in user vocabulary rather than compiler structure.

## 4. Select and export measurement data

**Target journey:** start from a run, discover its variables, select meaningful
points, and move bounded data into Xarray, Arrow, pandas, or Polars without
reconstructing the experiment or guessing schema from values.

**Executable evidence:** core measurement dataset selection/grouping/Xarray
tests, `core_integration/test_run_handle.py` for durable Arrow pagination, and
the [measurement data guide](../how-to/use-measurement-data.md). The duplicate
reference workbench script is retired.

**Success evidence:** point selections retain identity, grid projection restores
authored axes, exports agree on row counts, and paged reads remain finite and
bounded.

**Design questions:** common selection should not require durable variable IDs
when typed result handles or labels are already available. Large-data behavior
must be discoverable before accidental full materialization.

## 5. Publish analysis and review a candidate

**Target journey:** analyze a completed run with ordinary numerical Python,
publish conclusions and evidence, inspect a candidate without changing the
default, then accept it deliberately.

**Executable evidence:** `test_typed_candidates.py` covers managed receipt-backed
proposals and a real DRAG fit/candidate/verification journey using independent
parameters and setup. Analysis/publication integration tests retain legacy
publication fences. The DRAG gallery script is retired.

**Success evidence:** calibration analysis has a source run, published outputs
and report, the proposal cites evidence, a candidate run records proposal
provenance, project analysis compares the exact baseline and candidate inputs,
and verification alone changes no parameter branch, default or setup. Publication
must be a separate explicit operation against an exact destination.

**Design questions:** facts, artifacts, views, and proposals need distinct user
meaning without exposing output ontology in the common happy path. Review must
show scientific effect and scope, not just a structural configuration diff.

## 6. Publish verified parameters to an explicit branch

**Target journey:** publish verified cells to a chosen parameter branch, then
prepare production work with that exact saved revision. Retain proposal and
verification provenance without changing equipment or unrelated selections.

**Current gap:** ordinary branch saves exist, but verified candidate publication
still targets the legacy registry or working points. Neither ordinary `params.save()`
nor passing a candidate to preparation establishes calibration acceptance.

**Required evidence:** publication checks the destination generation, candidate
base and edited-cell ownership; the retained decision names exact independent
verification inputs. Advancing the branch and recording publication evidence
must be atomic and retryable. Stale heads and incompatible catalogs reject without
partial writes. A prepared run remains pinned when the branch advances.

**Design questions:** branch editing history and scientific calibration validity
are separate. Define the applicability of a verified result before expanding
cohort automation; do not carry forward the old global-default/restore workflow
as the new publication contract.

## Updating the evaluations

Change an evaluation when a supported workflow or desired product outcome
changes. A UI refactor alone does not require an update. When implementation
changes make a golden script longer, add manual identity transfer, or require a
new architectural concept, review the workflow before updating its documentation.

New detailed scenarios belong in the tested reference lab first. Promote them
to this list only when they represent a core product journey rather than a
capability demonstration or edge case.
