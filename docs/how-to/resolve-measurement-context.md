# Resolve exact measurement inputs

Use `lab.resolve_context(...)` to capture a parameter branch and setup together
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

Omitting `setup` reads the active setup in the same transaction as the parameter
branch. Pass an exact `SetupRevisionRef` to select another setup. For a registered
target, pass `target=target.ref` instead of `samples`; the current execution
binding supports one target member without target-level connections.

The result separates two responsibilities:

- `context` is a `MeasurementContext`: exact saved parameters, scientific subject,
  executable setup hash, software scenario and target-to-setup mapping.
- `receipt.branch` and `receipt.setup` record which branch generation and setup
  revision were resolved. Branch names and generations are not scientific identity.

Moving the branch or activating another setup does not change an existing context.
Resolve again when you want a fresh snapshot. With neither samples nor a target,
the context is unbound; this does not establish physical sample capability.

## Read a context from an existing measurement

```python
context = run.snapshot.measurement_context
if context is None:
    raise ValueError("This run does not use exact saved parameters without overrides")
report = lab.calibration_checks.report(context=context, profile="daily-readiness")
```

This property uses only retained evidence. Candidate configurations, temporary
parameter overrides and configurations without an independent saved parameter
revision return `None`. A context does not imply that the measurement succeeded,
that its calibration is valid, or that hardware execution is authorized.

Code that already owns an exact parameter revision and resolved binding can use
`MeasurementContext.from_binding(parameters, binding)` from
`scopecat.records.measurement_context`. It copies the complete binding evidence;
the caller must supply the parameter revision actually used without overrides.

See [calibration reports](read-calibration-report.md) for applicability policy and
[independent parameters](independent-parameters.md) for parameter branches.
