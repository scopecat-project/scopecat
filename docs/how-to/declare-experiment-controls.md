# Declare experiment controls once

`sc.Control` describes a maintained numeric control: its default, unit, bounds,
label, group, ownership, and provenance. Attach its `sc.ControlSet` explicitly
with `@sc.experiment(controls=controls)`. It supplies the same defaults to
immutable Python invocations and the typed launch catalog. The reference lab's
`frequency_amplitude` workflow demonstrates this with an analytic signal model.

```python
import scopecat as sc
from reference_lab.configuration import bootstrap_config
from reference_lab.workflows.frequency_amplitude import (
    CONTROLS,
    FREQUENCY,
    frequency_amplitude,
)

config = bootstrap_config()
original = frequency_amplitude()
fixed = CONTROLS.apply(
    original, config=config, edits={"frequency": sc.Quantity(4900, "MHz")}
)
scanned = CONTROLS.apply(
    fixed,
    config=config,
    edits={"frequency": sc.axis(FREQUENCY.ref, [sc.Quantity(4.9, "GHz")])},
)
restored_default = CONTROLS.apply(scanned, config=config, reset=("frequency",))
```

A scannable control has one coordinate/axis source. A scalar edit creates a
single-value `AxisSpec` with `mode="fixed"`; an explicit axis has `mode="scan"`,
even when it contains one point. Replacing that axis removes its old values.
Returning to a scalar requires a value or the declared default; the prior scalar
is not kept behind a scan. Each edit returns a new invocation. Persisted run
requests include fixed mode; historical scan records omit the default scan mode
and retain their existing JSON/content identity.

For a non-scannable control, declare an ordinary `sc.Input` of the matching type
and put its default only on the `Control`. Use `experiment.bind()` to construct
an invocation using those defaults, or bind explicit scalar inputs as usual.
The declaration rejects a mismatched input type or a duplicate function default.
Real scalar and real `Quantity` controls are supported; this is not a general
form schema or a new scan language. Forms support existing values and range
axes, without overlays or around-config axes.

## Project validation and ownership

A `ControlSet` may receive a project-owned validator taking
`sc.ControlValidationContext`. Its invocation, frozen configuration and axis
specifications describe the edited plan. The validator runs after control edits
and again in the normal planning/admission entry, including direct `.with_axis`
and `.bind` edits. Validate per-axis bounds or extrema and cross-field rules;
do not enumerate a Cartesian scan merely to validate it.

The reference model limits its own plans to 64 points and limits amplitude to
0.2 V when maximum detuning from the configured q0 carrier exceeds 0.25 GHz.
These are reference-project constraints, not global framework scan limits. The
carrier is read from the same accepted `qubits[q0]` field used by the compute
node; maximum detuning is derived from that carrier and the edited frequency
axis. Both values display their provenance. Their resolvers' values are
normalized and checked against declared units/bounds before displaying them.
They cannot be edited through the control interface.

## Use the declaration in a launch provider

`control_catalog(controls)` produces `LaunchCatalogEntry.controls`.
`edit_controls(controls, invocation, config=..., edits=request.control_edits)`
converts the typed `ControlEdit` source into the same immutable edit operations.
`control_values(...)` supplies `LaunchPreview.controls` with explicit
fixed/scanned/derived/configuration states and provenance. The reference
`control_launch` provider uses all three with the normal configuration-generation
fence and managed procedure admission.

A `ControlEdit` chooses exactly one source: `fixed` plus `value`, `scan` plus an
existing axis record, or `default` with neither. Unknown or owned fields are
rejected. The common launch worker validates nonempty edits against the project
catalog before calling its action; direct Python provider implementations must
call `validate_launch_control_edits` as the reference provider does. Empty edits
retain the existing request hash for providers without controls.

The GUI renders labels/groups, declared bounds, readonly ownership and resolved
provenance from these models. A source edit invalidates the old preview and
submission hash. Switching linear units converts the active values; nonlinear
or unknown units retain only their declared unit. The server remains the
unit/constraint authority. No device or accepted configuration is modified by
previewing the reference model.
