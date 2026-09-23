# Preview staged calibration work

Use a stage plan to inspect prerequisites and partial progress before building a
dispatcher. Each stage represents one declared check on its exact target, setup,
parameter revision and conditions. Stages can use different contexts. The plan
does not change a session selection or combine single-target checks into a joint
measurement.

Given three `CalibrationCheckRequest` declarations prepared for the intended
checks (`q0_check`, `q1_check` and `joint_check`):

```python
from scopecat.automation.calibration_tasks import (
    CalibrationTaskPlan,
    CalibrationTaskStage,
)

plan = CalibrationTaskPlan(
    stages=(
        CalibrationTaskStage(id="q0-drive", check=q0_check),
        CalibrationTaskStage(id="q1-drive", check=q1_check),
        CalibrationTaskStage(
            id="joint-check",
            check=joint_check,
            depends_on=("q0-drive", "q1-drive"),
        ),
    )
)

progress = lab.calibration_checks.preview_task(plan)
assert progress.ready == ("q0-drive", "q1-drive")

# Explicitly bind executions already submitted through lab.procedures.
progress = lab.calibration_checks.preview_task(
    plan,
    executions={"q0-drive": q0_request.id, "q1-drive": q1_request.id},
)
for stage in progress.stages:
    print(stage.id, stage.state, stage.blocked_by)
```

The server rejects cycles, unknown or duplicate stage IDs, duplicate dependencies,
unknown execution bindings and using one execution for multiple stages. Bound
executions must have the exact check declaration specified by their stage. No
author module is loaded to answer the preview. The HTTP equivalent is
`POST /api/v1/calibration-tasks/preview` with `plan` and `executions` fields.

| Stage state | Meaning |
|---|---|
| `ready` | No execution is bound and all prerequisites passed |
| `waiting` | A prerequisite has not finished |
| `blocked` | A prerequisite was rejected, failed, cancelled, incomplete or blocked |
| `queued`, `running` | The bound procedure is pending or executing |
| `waiting_for_input`, `attention_required` | The bound procedure needs input or attention |
| `passed` | The procedure succeeded and its completed measurement has a positive check result |
| `rejected` | A completed measurement has a negative check result |
| `failed`, `cancelled` | Execution failed or was cancelled |
| `incomplete` | Execution reported success without complete check evidence |

`blocked_by` names immediate unmet prerequisites for unbound stages. Failure does
not block unrelated stages. A task becomes `complete` when every stage has a
terminal result or is blocked; `successful` requires every stage to pass. This
makes a terminal partial result distinguishable from a fully successful result.
Already bound work retains its actual execution state; a preview cannot stop or
retroactively enforce prerequisites on an independently submitted procedure.

The response is a read snapshot of selected executions and their evidence. It is
not a freshness assessment, permission to dispatch, proof that the dependent
experiment consumed predecessor outputs, or certification of combined device
readiness. Use check applicability for age/context reuse and normal server
admission for subsequent actions.

This initial API previews at most 256 stages. It does not save a task, submit work,
retry, switch setups, publish parameters or schedule parallel acquisition. Keep
the plan in author code for now. Durable task identity, stage dispatch and bounded
repair policies are the next implementation layer; existing procedure records
remain durable independently of this preview.
