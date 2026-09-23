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
