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

## 1. Learn, edit and complete the first run

**Target journey:** open the installed application, start the parameters Notebook
from Help, edit ordinary author code, explicitly run it and inspect the same
retained result in Runs. Continue in the same folder without repeating preparation
forms or acquisition.

**Executable evidence:** `scripts/verify_notebook_journey.py` exercises Help’s
parameters and groups courses using their shipped cells in real independent
kernels, source/scan edits, application restart and read-only result reopening.
Groups checks cover 42/63 points, 2/3 groups, retained raw/analysis evidence and
returning to the parameters course’s saved inputs. Assertions are appended by
the verifier, separately from the grouping lesson. See the
[parameters lesson](../tutorials/teaching-sandboxes.md#learn-with-notebooks)
and [first experiment](../getting-started/quickstart.md).

**Success evidence:** prepared editable files and a local kernel, retained
7 × 64 → 7 × 32 → 5 × 32 results, and the same application-owned run after
restarting and continuing without reacquisition. Reading results is separate
from the explicit acquisition cell.

**Current boundary:** the native application already includes its runtime and UI;
source-checkout UI building is a contributor concern, not an ordinary user's
first-run requirement. Browser verification substitutes native editor activation
and window plumbing. Actual editor interaction and unfamiliar-user observation
remain separate acceptance work under #616. All seven topics now share Help
preparation and continuation (#913). The shipped refresh/compute and grouping
Notebooks have real-kernel evidence (#903/#904); #913 adds shared-application
restart/history checks for the remaining topics. Course ordering, difficulty and
possible grouping remain teaching-design work.

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

### Desktop continuation through retained results

**Target journey:** choose a sample/target, edit working parameters, explicitly
adopt them, preview and start an experiment, inspect that exact result, then
return to the next edit without changing either the adopted copy or recorded run.

**Executable evidence:** UI `object-parameters.e2e.ts` covers the sample map and
explicit target/setup handoff. `parameter-working-inputs.e2e.ts` reuses two windows
to check conflicting drafts, restart recovery, stale previews, separate selected
runs and continued editing. `application-loop.e2e.ts` uses one application data
space and a separate editable ordinary author folder. It loses the response after
admission, navigates away, edits the source table, checks the original submission
and reopens its result without a second acquisition. Back/forward and reopening
the procedure preserve this window's experiment inputs. The retained run still
contains the adopted scale of 2 and result of 1.6 after the working table reaches 4.

**Success evidence:** saved edits require explicit adoption; submission uncertainty
can be resolved without rerunning; exact results remain selected independently in
each window. Procedure and result links retain the current console's editable
state, while modified clicks keep normal browser behavior.

**Current boundary:** these are browser/software journeys. They do not qualify
native host plumbing, installation, unfamiliar-user operation or physical devices.

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

**Executable evidence:** `VerifiedParameterCandidate.publish_to_branch()` and
the managed-author candidate journey implement explicit single-candidate branch
publication. Server tests cover exact baseline/head checks, rejection, atomic
rollback, retry and current-format recovery. Ordinary `params.save()` and passing
a candidate to preparation still establish no calibration acceptance.

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
