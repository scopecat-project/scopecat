# Plan and dispatch staged calibration work

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

Preview is read-only and supports at most 256 stages. To retain the plan and
enforce prerequisites when submitting stages, use `lab.calibration_tasks`.

## Save a task and dispatch a stage

Capture each procedure's exact definition and typed intent with `task_call`.
These intents must declare precisely the checks used in the plan above. For a
physical sample, pass its exact `SampleSelector` tuple through `samples=`, as with
ordinary procedure submission.

```python
from scopecat.api.calibration_tasks import task_call

task = lab.calibration_tasks.create(
    "cooldown-7/check-round-1",
    plan,
    calls={
        "q0-drive": task_call(check_drive, q0_intent),
        "q1-drive": task_call(check_drive, q1_intent),
        "joint-check": task_call(check_joint, joint_intent),
    },
)
task = lab.calibration_tasks.dispatch("cooldown-7/check-round-1", "q0-drive")
procedure_id = task.task.executions["q0-drive"]

# A configured procedure worker can execute the admitted stage in the background.
# For supervised notebook execution, use the existing procedure handle:
lab.procedures.get(procedure_id).resume()
task = lab.calibration_tasks.get("cooldown-7/check-round-1")
print(task.progress.ready)
```

Creating a task saves intent without admitting any procedures. Definition identity,
intent and stage dependencies cannot be edited under the same task ID. Retrying
the same create request returns the existing task; a different specification
conflicts. `list()` discovers retained tasks after reconnecting.

`dispatch(task_id, stage_id)` checks prerequisites against retained evidence,
applies normal parameter/setup/subject admission, submits the procedure and saves
its stage association in one write transaction. An interrupted transaction leaves
neither a procedure nor an association. Repeated dispatch returns the same
procedure, even after authority changes; new stages still require current
authority. No earlier unrelated execution is silently adopted. Ready independent
stages can be dispatched separately if another stage has an admission problem.

The task and associations survive daemon restarts and current-format backup and
restore. Development schema 90 introduces their storage; use a fresh data directory
for this format and retain older stores with their original environments.

This API enforces dependencies at dispatch, but does not poll and dispatch future
stages automatically. It does not switch setups, transfer predecessor outputs
into later intents, refresh parameter branches, retry rejected stages or publish
combined readiness. Calls are fixed at task creation. A repair or new observation
needs a new task specification and ID. Cancellation of an already admitted
procedure still uses the procedure API; unsubmitted stages remain unsubmitted.
Automatic task advancement, task-level controls and bounded repair policies are
the next layer above this admission primitive.
