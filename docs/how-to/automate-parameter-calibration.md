# Automate a complete parameter calibration

Use a registered durable procedure for one bounded calibration request. Capture
the requested targets, saved parameter/setup inputs, destination branch head and
result revision name in its typed intent. Pass the scientific subject when
submitting the procedure; child acquisitions inherit that retained scope.

The framework owns execution records, exact references, resource scheduling,
replay and atomic publication. Laboratory code owns what to measure, fitting,
scientific acceptance and whether coupled effects need additional measurements.

## Build the request from a captured branch

```python
destination = lab.parameters.checkout("chip-a/daily").head
initial = lab.parameters.resolve(destination.revision)

# CalibrationIntent and calibrate are registered laboratory definitions.
intent = CalibrationIntent(
    targets=("q0", "q1"),
    initial=initial,
    destination=destination,
    result_revision_id="cooldown-7-drive-001",
    actor="Alice",
)
request = lab.procedures.submit(
    calibrate, intent, request_key="cooldown-7-drive-001", sample="chip-a"
)
```

Use saved parameters without overrides. Capture inputs once at submission; do not
read today's branch head during replay. A target subset is a new explicit request,
not an automatic consequence of one member failing. Validate duplicate or empty
target lists in the laboratory intent model.

## Compose, measure and decide

Inside the procedure, use `ctx.run()` and `ctx.analyze_run()` to retain each target's
baseline and fit. Both methods return durable references. For multiple fitted
proposals, compose them with the framework step:

```python
joint_ref = ctx.combine_parameter_candidates(
    "compose",
    ((q0_fit_ref, "q0-drive"), (q1_fit_ref, "q1-drive")),
    name="joint-drive",
)
candidate = ctx.published_analysis(joint_ref).candidate_config("joint-drive")
```

Run the requested verification measurements with this exact `candidate`. Use
`ctx.analyze_project()` to retain all baseline and verification inputs and the
laboratory decision. Its arguments may contain collections of run handles; durable
identity uses their retained run IDs. Include requested, checked, rejected and
missing targets in the policy result. A positive decision requires complete
coverage of the requested scope, including relevant interactions.

Only after a positive retained decision, call
[`ctx.publish_parameter_candidate()`](parameter-branches.md#publish-inside-a-durable-procedure).
The destination branch advances atomically. Setup and shared defaults stay unchanged.
The server checks exact source/evidence identities; it cannot infer whether the
laboratory's scientific policy is adequate.

## Run and resume

For one foreground request, call `request.resume()`. A resident worker can dispatch
submitted requests without installing the old cohort evaluator or finalizer:

```python
from scopecat.api.project_worker import ProjectAutomationWorker

worker = ProjectAutomationWorker(lab.procedures)
result = worker.cycle()  # A bounded pass over due schedules and runnable requests.
```

For a process-owned loop, use `worker.run_forever(stop=stop_event)`. Keep the
connection and worker process alive. Reopen a request by its ID after restart:

```python
request = lab.procedures.get(request_id)
request.summary()
request.steps()
```

With a configured author workspace, the installed CLI starts the same worker:

```console
scopecat automation work /path/to/lab
scopecat automation work /path/to/lab --once
```

The CLI dispatches registered procedures, due schedules and registered interval
planning. It does not evaluate legacy cohort freshness or choose a working point.
Register procedures via `LabApplication(procedures=...)` or the `procedures`
list in `[lab.capabilities]`. The former `calibrations` and
`calibration_publications` application declarations and `--working-point` worker
option are retired; old declarations fail visibly instead of being ignored.

Completed steps replay their outputs. Missing steps remain pending. A scientific
rejection is a retained result and must not fall through to publishing a subset.
A stale destination requires a new request based on reviewed inputs; retrying
against a freshly checked-out head is not a rebase of old scientific evidence.

If a publication result is unknown, inspect the retained attention reason and use
`request.retry_attention()` with its unchanged intent. The same command recovers
its original publication receipt even if another writer later advanced the
branch. It does not reacquire completed measurements or publish to the newer head.

This flow does not automatically declare calibrations fresh, select stale targets,
or infer dependencies between samples. Those are explicit laboratory policies.
The maintained simulation fixture `drag_branch_calibration` tests two-target joint
remeasurement and recovery; it is developer evidence rather than a hardware recipe
or an installed tutorial sandbox.
