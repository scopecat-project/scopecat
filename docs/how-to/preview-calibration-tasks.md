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
restore. Current development schema 98 stores task controls and indexes running
tasks; use a fresh data directory
for this format and retain older stores with their original environments.

## Final verification and publication

A task may name one registered procedure to run after **every planned check
passes**. It receives the exact adopted evidence of every stage. A rejected,
failed, cancelled or incomplete stage prevents this handoff; it never publishes
a successful subset. Add the call when creating the immutable specification:

```python
task = lab.calibration_tasks.create(
    "cooldown-7/calibrate-1",
    plan,
    calls=calls,
    finalization=task_call(finalize_calibration, final_intent, samples=samples),
)
```

`finalize_calibration` is your registered laboratory procedure. Its typed intent
must declare `calibration_task: CalibrationTaskInputs | None = None`, importing
`CalibrationTaskInputs` from `scopecat.automation.calibration_tasks`. At creation
that field must be `None`; the daemon fills it at admission. Other fields remain
unchanged. Capture the destination branch head, result revision name, composition
mode, proposal IDs, verification scope and policy in that intent **before**
starting the task. Supply the exact sample selectors needed by the final procedure.
Do not look up a newer branch head or setup to repair a conflict during replay.

For a runnable starting point, open **后台标定与最终发布** (`task-calibration`) in
[tutorial sandboxes](../tutorials/teaching-sandboxes.md). Its source contains the
fit stages, task creation and final verification/publication procedure. The three
requests show acceptance, scientific rejection and a concurrent branch conflict.

Inside the procedure, each `intent.calibration_task.checks[stage_id]` contains the
adopted measurement and analysis identity, scientific binding, scope and result.
Use `intent.calibration_task.measurement(stage_id)` and
`intent.calibration_task.analysis(stage_id)` for the durable references consumed
by `ctx.run_handle()` and `ctx.published_analysis()`. Choose the proposal
from that exact analysis according to the captured intent, then use
[`ctx.combine_parameter_candidates()`](automate-parameter-calibration.md#compose-measure-and-decide)
for parallel or sequential composition. Measure the aggregate, retain all source
and verification inputs in the laboratory decision, and only then call
`ctx.publish_parameter_candidate()` against the captured branch head. This uses
the ordinary fenced publication and durable-step recovery path. The task does
not invent an acceptance policy or authorize publication from check success.

Start the task to enable finalization admission. Check evidence binding and the
new procedure association commit in one transaction; interrupted admission leaves
neither behind. Restart and backup/restore retain the same association. The
configured worker executes it even after the notebook disconnects. Admission
errors appear in `task.task.finalization_error`; correct the cause and start again
to retry. Procedure attention/input and unknown publication outcomes use existing
procedure controls, without submitting a second finalizer.

`task.progress` describes the check stages only. Inspect `task.finalization` for
the final procedure's state and closure, and its retained publication output for
the actual branch receipt. `mode="finished"` means advancement ended, not that
scientific verification passed or parameters were published. The workbench shows
the final procedure separately and links to its evidence and controls. Pause or
cancel prevents new finalization admission; it cannot undo an already admitted
procedure or committed publication.

## Run without keeping a notebook open

The workbench's **Calibration tasks** section lists retained plans. Open a task to
inspect each stage's check, target, frozen context, prerequisites and admission
errors. **Open execution** leads to the existing procedure view for worker/resource
status, retained measurements, review input and execution cancellation. The task URL
can be bookmarked and reopened after a browser restart.

Start/resume, pause and cancel controls require an operator and reason. A conflicting
control refreshes the view and displays the error without automatically retrying
your action. Task progress describes this plan; it is not a sample-health or combined
capability assessment. Plan creation currently remains in author Python code.

Once the task specification is ready, ask the daemon to advance it:

```python
task = lab.calibration_tasks.start(task, actor="alice", reason="check this cooldown")
# The notebook may now disconnect. Read current state after reconnecting:
task = lab.calibration_tasks.get("cooldown-7/check-round-1")
print(task.task.mode, task.task.dispatch_errors)
print(task.progress)
```

The daemon polls running tasks and admits at most one stage per task at a time.
Existing procedure workers load the configured application/author workspace and
execute the retained definition. Its identity must still be available there;
defining a procedure only in a notebook does not make it importable by a worker.
Procedure leases and resource admission continue to apply. Different tasks may
execute concurrently within the worker limit; distinct targets do not prove that
their hardware is independent.

A rejected check blocks its descendants; other independent stages may continue.
An admission failure is retained in `task.task.dispatch_errors` and is not retried
on every poll. Correct the cause and call `start` with the latest task view to
clear admission errors and try again. A failed worker process stays paused and
requires explicit procedure dispatch after inspection; starting the task does
not reset that worker pause. Waiting input or attention also stops sequential
advancement until resolved through the procedure's existing controls.

An unavailable author workspace or worker-start failure pauses that execution;
other tasks can still reach their workers. Open its execution to see the retained
worker diagnostic and exact log path, restore its code workspace or environment, then use
**Dispatch existing procedure**. This retries the admitted execution rather than
creating a replacement check.

Expand **Recent worker output** in execution details to read the latest 16 KiB
directly in the workbench. Use **Refresh worker output** for new messages. The view
marks truncated output and distinguishes an absent log from an empty one; use the
displayed file path when you need the complete log. Output is shown as plain text,
separately from the retained measurement and procedure outcomes.

```python
task = lab.calibration_tasks.pause(task, actor="alice", reason="inspect equipment")
task = lab.calibration_tasks.start(task, actor="alice", reason="inspection complete")
task = lab.calibration_tasks.cancel(
    task, actor="alice", reason="abandon remaining checks"
)
```

Pause and cancellation stop **new stage admission**, including manual dispatch.
Already admitted procedures may continue; use their procedure/run controls to
request cancellation and inspect the actual outcome. Task cancellation is final
and does not mean hardware has stopped or that the plan completed. Its progress
still reports the retained stage results. A finished task likewise cannot restart;
`progress.successful` distinguishes complete success from terminal partial results.

Control commands carry the revision from the supplied view, actor and reason.
An exact retry is safe; a stale view cannot silently undo a newer control. Reload
the task before issuing a different command after another operator changes it.
The daemon recovers running tasks and admitted worker handoffs after restart;
paused/cancelled tasks remain so. There is no automatic retry of scientific failures.

Tasks do not switch setups, refresh branches or publish combined readiness.
A repair or new observation needs a new task specification and ID. Bounded repair
and scheduling policy remain separate work.

## Bind a prerequisite's candidate output

Use an explicit candidate edge when the next check must measure parameters fitted
by a preceding stage:

```python
from scopecat.automation.calibration_tasks import (
    CalibrationTaskStage,
    StageCandidateOutput,
)

verification_stage = CalibrationTaskStage(
    id="verify",
    check=verification_template,
    depends_on=("fit",),
    candidate_from=StageCandidateOutput(stage_id="fit", proposal_id="frequency"),
)
```

`verification_template` is the usual `CalibrationCheckRequest`. Its scope, subject,
setup, scenario, mapping and result-step addresses remain fixed. `candidate_from`
explicitly replaces its parameter input at admission; until then that input is a
template, not a resolved context. The matching call still contains the template
under `calibration_check` at task creation.

The source must be an explicit prerequisite and pass. Its adopted check analysis
must publish the named parameter proposal alongside the standard check result.
Another analysis of the same run, a latest-analysis lookup or a different stage's
result cannot substitute for that output. A failed or rejected source blocks the
dependent stage; this feature is not a repair-on-failure loop.

At dispatch the server validates the output and scientific binding, then replaces
only `intent.calibration_check.context.parameters` with the exact candidate source.
Procedure code must consume this incoming declaration; parameter copies in other
intent fields are not rewritten. The child measurement is checked against the
admitted candidate, so code that accidentally keeps the template input is rejected.

The resolved check and execution association commit together. `task.task.resolved_checks`
retains the inputs; `task.task.resolved_plan` overlays them on the immutable
specification for Python inspection and previews. Undispatched candidate stages
remain templates. Restart/retry reuses the admitted check and procedure. An admission
failure retains no partial binding; automatic advancement records the error until
explicit resume, while independent work can proceed.

The workbench shows the source stage/proposal while binding is pending and exposes
the frozen candidate context and evidence query only after admission. Binding does
not update the daily branch or certify the proposed values; verification and
optimistically fenced publication remain explicit operations.
