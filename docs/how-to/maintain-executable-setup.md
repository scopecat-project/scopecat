# Maintain executable setup independently

The daemon keeps one current executable setup: topology, routing, instrument
connections and lifecycle declarations, and the domain target. Saved setup
revisions contain no parameter definitions or values. Selecting a parameter
default or publishing a working-point calibration does not select another setup.

In the workbench's configuration page, **Executable setup** shows that independent
selection. Save the selected configuration's setup, or import a complete JSON
configuration snapshot and save its setup. Saving adds an immutable revision.
Review the revision and explicitly activate it to change execution authority.
The parameter default and working-point versions remain unchanged.

A new empty catalog initializes setup and parameter default together from the
laboratory's bootstrap configuration. Subsequent source-code changes do not rewrite
that state. A partly initialized catalog is rejected rather than silently repaired.

## Prepare and review in Python

For an empty laboratory, setup can be prepared before any parameter configuration:

```python
revision = lab.setup.save(reviewed_setup, name="initial-setup")
lab.setup.activate(revision)
```

Here `reviewed_setup` is an `ExecutableSetupSnapshot` supplied by the adapter and
maintainer. Saving or selecting it does not create a parameter registry entry.
To use the current full-snapshot execution API, explicitly compose parameters:

```python
from scopecat.config.resolution import compose_configuration

config = compose_configuration(
    reviewed_setup,
    id="initial-parameters",
    system_id="lab",
    catalog=author_parameter_catalog,
    parameters=reviewed_parameters,
)
lab.config.set_default(config)
```

Composition validates without persistence; publishing selects the parameter
default and leaves the already selected setup unchanged. The combined snapshot
is a transitional carrier; see [configuration ownership](../development/configuration-ownership.md).

Read the current setup and save a reviewed replacement:

```python
current = lab.setup.active()
revisions = lab.setup.list()

# reviewed_config is the complete snapshot prepared by your laboratory's
# configuration factory. Only its executable fields are saved here.
replacement = lab.setup.save(
    reviewed_config, name="rack-a-september", note="Reviewed routing"
)
assert lab.setup.active() == current
```

`save()` also accepts an `ExecutableSetupSnapshot`, available from
`scopecat.records.setup`. Names identify immutable revisions. Repeating an identical
save returns the existing revision; reusing the name for different content or
provenance fails. `get(name)` reopens that exact revision.

Prepare parameter copies before switching the live setup:

```python
preview = lab.config.preview_setup_rebind(base="chip-a-parked", setup=replacement)
rebound = lab.config.rebind_setup(
    base="chip-a-parked",
    setup=replacement,
    name="chip-a-parked-september",
    note="Copied estimates; calibration still required",
)
params = lab.config.workspace(context=rebound.entry.id)
```

Rebinding composes the saved executable fields with the original parameter catalog
and values, then validates the complete configuration. Incompatible declarations
are rejected. For a working point it creates a new workspace, retaining the exact
sample revision, working-point label and batch. It never advances the original
workspace or selects a default. The new source records both inputs, and contains
no calibration acceptance. Existing cell origins remain historical evidence for
where estimates came from; they do not establish validity on the new setup.

For a non-working-point configuration, rebinding creates a saved configuration
that can later be explicitly selected as the parameter default. Neither form is
silently recomposed when a setup changes.

## Switch the current setup

Use the generation you reviewed and retain the operation ID for a retry:

```python
selected = lab.setup.activate(
    replacement,
    expected_generation=current.activation.generation,
    operation_id="select-rack-a-september",
    note="Maintenance review complete",
)
```

A concurrent setup selection rejects the stale request. Repeating the same operation
returns its original result, including after another setup has since been selected.
Changing the intent while reusing an operation ID is rejected. Without an explicit
generation, the Python convenience method reads the current one immediately before
submission; pass the reviewed generation when the review and action are separate.

Removing or changing an instrument exclusivity key requires an explicit declaration
through `lab.setup.activate(changes=...)`. Use the typed
`InstrumentInventoryRemoval`, `InstrumentInventoryRekey` or
`InstrumentInventoryRenameRekey` from `scopecat.config.inventory`. The setup service
checks queued reservations and live claims, retires affected idle actors behind a
gate, and rechecks ownership and setup generation inside the commit transaction.
The workbench currently handles selections without these destructive declarations;
use the Python API for that maintenance case. Manual-control safeguards and
unknown-effect quarantine still apply.

After a setup change, old frozen configurations remain readable. If their executable
content differs, running them or selecting them as the parameter default fails
with a setup mismatch. Select the prepared rebound version explicitly. Direct
instrument sessions retain the exact setup revision they opened against; changing
parameter defaults does not interfere with session acquisition.

This is a software execution declaration, not proof of physical wiring or an
untouched cooldown. Runs retain complete immutable configuration and scientific
setup content hashes. Descriptive apparatus history remains separate. One active
setup does not promise concurrent execution of incompatible deployments.

See [configuration ownership](../development/architecture/configuration-ownership.md)
for the fences and [current-format backup](backup-and-restore.md) for recovery.
