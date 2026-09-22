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

For a notebook or author session, select the parameter version directly:

```python
session.use(parameters=parameters)  # a saved name or exact reference also works
```

This preserves the selected subject, batch, operator and record collection.
Independent parameters replace the configuration choice, not the whole context;
they do not inherit a working point's calibration publication ownership. Choose
either `parameters` or `working_point` in one `use` call. Other sessions are
unaffected. Selection and editing require no executable setup; the maintainer
must select one before previewing or running an experiment. For repeated editing,
use [a parameter branch and `session.params`](parameter-branches.md).

Preview resolves the parameter revision and current setup through the common
measurement resolver without creating a configuration-registry entry. Reviewed
requests retain both exact references; a later setup selection or session edit
cannot silently replace them. An incompatible active setup blocks admission.
Saved experiment plans pin both references and reuse the same resolver.

The low-level runner can use the same read-only resolution:

```python
inputs = lab.parameters.resolve(parameters)
result = lab.run(experiment(), config=inputs)
```

Advanced callers can supply an exact `setup=` to `resolve`, or choose
`ParameterConfiguration(ref=..., setup=...)` inside an existing scientific
selection. Neither form activates that setup. `parameters.bind(...)` remains a
bridge for callers needing a named combined entry as an old working-point base;
normal independent-parameter launches do not need it.

These are distinct responsibilities:

| Record | Meaning |
| --- | --- |
| Parameter revision | Declarations and values, independent of equipment |
| Prepared inputs | Exact parameter/setup combination; no calibration acceptance |
| Measurement selection | Subject, batch and saved inputs or working point |
| Run evidence | Resolved inputs and scientific binding used for that run |

Compilation still consumes a combined snapshot, while run provenance retains
the independent input references. Adapter templates also save independent
parameter revisions and setup revisions atomically, without selecting defaults.
Working points and old bootstrap
consumers still need ownership cleanup, tracked in
[#754](https://github.com/scopecat-project/scopecat/issues/754). The older
`lab.config.set_parameter_default(...)` specifically publishes a global default;
ordinary parameter authoring should not use it just to save a revision.

Current storage is development schema 86. No prebaseline migration or persistent
compatibility promise is introduced. Current-format backup/restore includes
standalone revisions even when no setup has ever been saved.

For ongoing edits without naming every revision, use a
[parameter branch](parameter-branches.md). Session checkout preserves an editing
base and saves advance only that branch.
