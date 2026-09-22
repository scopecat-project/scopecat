# Save parameters before selecting execution context

Parameter declarations and values can exist before a bench, sample, batch or
working point is selected. Use a connected `lab` or author `session`:

```python
parameters = lab.parameters.save(
    name="initial-estimates",
    catalog=author_parameter_catalog,
    parameters=estimated_values,
    note="Initial estimates, not accepted calibration",
)
```

This validates the values against their declarations and saves an immutable
revision. No setup is required, no default changes, and no device is opened.
Use a new name for changed content; retrying the same name and input returns the
original revision. `lab.parameters.get(name)` and `.list()` reopen saved versions.
Saving does not assert that values are calibrated for any physical conditions.

Prepare an execution combination separately, after choosing a saved setup:

```python
prepared = lab.parameters.bind(
    parameters,
    setup=reviewed_setup_revision,
    name="bench-inputs",
    system_id="lab",
)
```

Both exact references are retained. This checks the combination and saves an
execution input, without activating setup or selecting a parameter default.
The setup need not be active while preparing inputs. Running still requires the
maintainer to have selected a compatible executable setup independently.

The current low-level runner accepts the resulting saved input:

```python
result = lab.run(experiment(), config=prepared.entry.id)
```

For an author session, use the existing `ScientificSelection.configuration` with
`SavedConfiguration(ref=PlanConfigRef(entry_id=prepared.entry.id,
content_hash=prepared.entry.content_hash))`. Subject and batch remain the existing
selection fields. Do not replace a whole selection just to change parameters;
preserve its subject and batch. Existing working-point creation can use the
prepared entry as its exact base, with its normal explicit sample binding.

These are distinct responsibilities:

| Record | Meaning |
| --- | --- |
| Parameter revision | Declarations and values, independent of equipment |
| Prepared inputs | Exact parameter/setup combination; no calibration acceptance |
| Measurement selection | Subject, batch and saved inputs or working point |
| Run evidence | Resolved inputs and scientific binding used for that run |

The prepared entry is a bridge to the current combined configuration runner.
It is not a new parameter owner or a replacement measurement context. Working
points still store combined inputs; direct resolution of independent revisions
inside the common launch resolver and simpler session selection remain open in
[#754](https://github.com/scopecat-project/scopecat/issues/754). The older
`lab.config.set_parameter_default(...)` specifically publishes a global default;
ordinary parameter authoring should not use it just to save a revision.

Current storage is development schema 85. No prebaseline migration or persistent
compatibility promise is introduced. Current-format backup/restore includes
standalone revisions even when no setup has ever been saved.
