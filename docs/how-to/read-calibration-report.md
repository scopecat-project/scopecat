# Read a capability evidence report

A task view explains one plan's progress. A capability report instead asks which
retained checks apply to an explicit measurement context. It does not infer the
capabilities required by a sample or declare that the whole sample is calibrated.

## Inspect from the workbench

Open **Calibration tasks**, select a task, and expand **Inspect applicable evidence**
under a stage. Enter a maximum evidence age in hours and click **Check evidence**.
The age starts blank because the laboratory must choose that policy. The history
limit defaults to 50 and can be increased to 200.

This queries all retained checks matching that stage's frozen context and scope,
including checks from other tasks. It does not follow a parameter branch's current
head. Each result shows its evaluation time, status, reasons and inspected count,
with links to selected measurements/analyses and unresolved procedure controls.
Changing inputs clears the old report. Checking again refreshes it; results do not
automatically update or expire on screen, and failed refreshes clear old verdicts.
This entry point inspects one declared requirement at a time; it does not certify
the task's complete scientific coverage.

## Inspect from a sample

In **Chips & samples**, select a sample revision and expand **Capability evidence
for this sample revision**. Choose a measurement from the loaded run history,
then load and select a saved capability profile. Load older runs in the sample's
run list if the measurement you need is not yet available in the selector.

This entry does not require a calibration task. It evaluates the chosen profile
using the measurement's saved parameter revision, setup, complete subject and
scenario. Joint measurements retain every sample in their subject; choosing one
sample's page does not turn joint evidence into a single-sample claim. Expand
the subject details to inspect that binding before evaluating the profile.

The measurement must use an exact saved parameter revision without overrides
and belong to the displayed sample revision. Candidate and older configuration
sources cannot supply this context. Selecting a different measurement clears
the previous report, including while the new context is loading or fails to load.
The evaluation time is current, but its requested context is historical: this
does not assess today's branch head, setup or overall sample readiness.

## Query from Python

Provide a resolved `CalibrationContext` and the requirements you want to inspect.
For example, using the exact declaration from a laboratory check intent:

```python
from datetime import timedelta

from scopecat.api.calibration_checks import CalibrationRequirement

declaration = check_intent.calibration_check
report = lab.calibration_checks.report(
    context=declaration.context,
    requirements=(
        CalibrationRequirement(
            id="readout-q0",
            scope=declaration.scope,
            max_age=timedelta(hours=4),
        ),
    ),
)
for item in report.items:
    print(item.requirement.id, item.selection.status, item.selection.reason)
    if item.selection.assessment is not None:
        print(item.selection.assessment.reasons)
    print(item.incomplete_reasons, item.unresolved_procedures)
```

Each scope names the capability, ordered targets, operating conditions and policy
version. Use separate requirements for individual and joint checks; a passing
single-target result does not establish a joint capability. All requirements in
one report share the exact parameter revision, subject, setup and execution
scenario. Query a different context separately. A branch label is not a context.

The server reads the requirements' indexed histories and resolves their evidence
within one SQLite read transaction. `observed_at` is the server's evaluation time.
Evidence selection uses measurement creation time, not analysis publication time.
No laboratory Python is imported, no experiment runs, and no parameter is written.

| `selection.status` | Meaning |
|---|---|
| `usable` | The selected check passed and meets the requested age/context policy. |
| `out_of_spec` | The selected applicable check failed its scientific criterion. |
| `recheck` | The retained check needs a new observation, for example after expiry. |
| `unknown` | Evidence is missing, ambiguous, unfinished or not fully inspected. |

A newer negative check supersedes an older passing one. The report never searches
backward for success. Exact-context filtering means another parameter revision,
setup, subject or software scenario provides no matching evidence; it is not
silently reused. An absent requirement returns `no_matching_evidence`. This is
still conservative exact-revision matching, not parameter-dependency analysis.

## Declare capability prerequisites

In a notebook, leave `report` as the last expression of a cell to display a
table of capabilities, targets, own-check results, availability and blocking
prerequisites. Expand each requirement for its evidence IDs, reasons, age policy
and unresolved executions; expand the context for exact scientific inputs.
The report retains its typed `.items`, `.context` and `.observed_at` fields.
Displaying it performs no request: call `lab.calibration_checks.report(...)`
again to obtain a fresh snapshot. A saved notebook output is a captured view
at that observation time, not a live status panel or an authoritative data record.

Add `depends_on` to requirements when your laboratory's policy requires other
capabilities to be usable first. These are IDs in the same report:

```python
readout = CalibrationRequirement(
    id="readout", scope=readout_scope, max_age=timedelta(hours=4)
)
gate = CalibrationRequirement(
    id="gate", scope=gate_scope, max_age=timedelta(hours=2), depends_on=("readout",)
)
report = lab.calibration_checks.report(context=context, requirements=(readout, gate))
for item in report.items:
    print(item.requirement.id, item.selection.status, item.availability.status)
    print(item.availability.blocked_by)
```

`selection` always describes the requirement's own evidence. `availability`
has the same status unless a prerequisite is not usable; then it is `blocked`,
even if the requirement's own check passed. `blocked_by` lists immediate unmet
prerequisite IDs. Follow those items to inspect deeper causes. Blocking propagates
through the graph, while independent requirements remain unaffected.

All dependencies must name requirements in the request; duplicates and cycles
are rejected. Request order does not matter, and response order is preserved.
An empty dependency list preserves the individual evidence verdict. This explicit
graph describes your policy, not measured causality or parameter read dependencies.
Task stage ordering is not automatically adopted as capability policy. The
workbench's per-stage inspector submits a single requirement without dependencies.

## Save reusable requirements

Keep requirement definitions in laboratory author code, then save a named version
for use across notebooks and daemon restarts:

```python
saved = lab.calibration_checks.save_profile(
    "two-qubit-daily-v1",
    requirements=(readout, gate),
    description="Readout evidence is required before using the gate capability.",
)
lab.calibration_checks.profiles()  # bounded page; use next_cursor for older entries
lab.calibration_checks.profile("two-qubit-daily-v1")
report = lab.calibration_checks.report(context=context, profile="two-qubit-daily-v1")
report
```

A profile stores requirements, age limits and dependency edges, but no parameter
branch, setup, sample selection or task execution state. Its target addresses are
interpreted within the context supplied at evaluation. Reuse it only where those
addresses, conditions and policy meanings apply; the framework does not infer
physical equivalence across samples. There is no global active profile.

IDs use letters, digits, dots, underscores and hyphens, beginning with a letter or
digit. Saving identical content under the same ID is idempotent. Changing any
content requires a new ID, such as `two-qubit-daily-v2`; existing profiles are
immutable. The returned report includes `profile_id` and the full requirements
actually evaluated. Supply either `profile` or `requirements`, not both.

Profiles belong to the selected project data store, are available through the
HTTP API without importing author Python, and survive current-format backup and
restore. Schema 92 adds their storage; use a fresh development data directory and
retain older stores with their original environments. A saved profile is a
report policy, not an automatic maintenance schedule or complete sample policy.

In the workbench, expand a stage's evidence inspector, then **Inspect a saved
capability profile**. Load profiles, choose one, review its requirements, and
click **Check saved profile**. It evaluates every requirement in the stage's
frozen context, showing own-check status separately from availability and
blocking prerequisites. This read-only entry uses 50 checks per requirement;
use the Python report API to request a different history budget. Changing the
selection or a failed refresh clears the old result. No experiment is dispatched.

## Bounds and interpretation

`history_limit` defaults to 50 requests **per requirement**, with a range of 1–200.
A report accepts 1–32 requirements with distinct IDs, and the product of requirement
count and history limit must not exceed 2,000. A truncated history returns
`incomplete_history` with `scan_limit`. Any unresolved check in the inspected
history also prevents a usable verdict and lists its procedure ID, even when
another check passed. Open those executions for inspection; increasing a budget
does not resolve unfinished evidence.

The response is an advisory snapshot, not authorization for a later publication
or proof that the requirement list covers all scientific dependencies. It cannot
replace write-time authority checks, infer hardware independence, schedule repairs
or publish combined readiness. The workbench task view remains separate; a sample
capability panel can consume this report once the laboratory supplies its explicit
requirements and current context.
