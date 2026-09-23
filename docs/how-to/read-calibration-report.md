# Read a capability evidence report

A task view explains one plan's progress. A capability report instead asks which
retained checks apply to an explicit measurement context. It does not infer the
capabilities required by a sample or declare that the whole sample is calibrated.

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

| Status | Meaning |
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
