# Resolve exact measurement inputs

Use `lab.resolve_context(...)` to capture saved parameters and setup together
without launching an experiment, changing session selection or importing author
code. Calibration reports consume this same public measurement context.

```python
from scopecat.records.sample import SampleSelector

receipt = lab.resolve_context(
    branch="daily",
    samples=(SampleSelector(sample_id="chip", revision=3),),
)
context = receipt.context
```

For a saved version without a branch, use `parameters` instead of `branch`:

```python
parameters = lab.parameters.get("initial-estimates")
receipt = lab.resolve_context(
    parameters=parameters,
    setup=lab.setup.get("bench-v1"),
    samples=(SampleSelector(sample_id="chip", revision=3),),
)
```

Pass a saved `ParameterRevision` or exact reference; a branch is neither required
nor created. The API requires exactly one of `branch` and `parameters`. Both paths
use the same revision validation and scientific binding resolver. A stale content
hash is rejected rather than replaced with the current content.

The sample workbench exposes the same choice under **Capability evidence from
saved parameters**. Select **Exact saved revision** and enter the saved version's
name; the workbench obtains its exact reference without asking you to copy a hash.

Omitting `setup` reads the active setup in the same transaction as the parameter
resolution. Pass a saved `SetupRevision` or exact reference to select another setup. For a registered
target, pass `target=target.ref` instead of `samples`; the current execution
binding supports one target member without target-level connections.

The result separates two responsibilities:

- `context` is a `MeasurementContext`: exact saved parameters, scientific subject,
  executable setup hash, software scenario and target-to-setup mapping.
- `receipt.branch` and `receipt.setup` record which branch generation and setup
  revision were resolved. Branch names and generations are not scientific identity.
  With exact parameters, `receipt.branch` is `None`; the parameter reference is
  retained in `receipt.context.parameters`.

Moving the branch or activating another setup does not change an existing context.
Resolve again when you want a fresh snapshot. With neither samples nor a target,
the context is unbound; this does not establish physical sample capability.

## Read a context from an existing measurement

For a retained candidate, resolve its original context directly:

```python
candidate = author.config.candidate(source_run_id, "coarse-then-fine")
receipt = lab.resolve_context(candidate=candidate)
report = lab.calibration_checks.report(
    context=receipt.context, profile="daily-readiness"
)
```

Do not pass a branch, saved parameters, setup or subject override with a candidate.
The server validates the exact retained proposal and resolved content, preserving
its original subject, setup hash, scenario and mapping. Both receipt selections
are `None`: no current setup or branch is substituted. The context's parameter
input is explicitly an `analysis_candidate`, not a saved parameter revision.
Checks can declare this context before running the verification measurement.
Actual admission still checks the current setup authority.

Candidate evidence applies only to that exact candidate context. Matching values
or publishing those values as a saved revision do not automatically transfer the
check's applicability to another context.

```python
context = run.snapshot.measurement_context
if context is None:
    raise ValueError("This run does not retain an exact saved or candidate input")
report = lab.calibration_checks.report(context=context, profile="daily-readiness")
```

This property uses only retained evidence, including exact candidate inputs.
Temporary parameter overrides and configurations without a saved revision or
retained candidate return `None`. A context does not imply that the measurement succeeded,
that its calibration is valid, or that hardware execution is authorized.

Code that already owns an exact parameter revision and resolved binding can use
`MeasurementContext.from_binding(parameters, binding)` from
`scopecat.records.measurement_context`. It copies the complete binding evidence;
the caller must supply the parameter revision actually used without overrides.

See [calibration reports](read-calibration-report.md) for applicability policy and
[independent parameters](independent-parameters.md) for parameter branches.
