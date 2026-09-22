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

A new empty catalog's startup orchestration explicitly saves and activates setup,
then publishes its parameter default from the laboratory bootstrap input.
Parameter publication itself never initializes equipment, including the old
full-snapshot `set_default` path. Subsequent source-code changes do not rewrite
initialized state or re-evaluate the bootstrap input.

If startup stops between setup activation and parameter publication, the selected
setup is retained. Restart reports incomplete initialization instead of repairing
it automatically. A maintainer must open the retained instance without the seed,
inspect setup selection and explicitly complete publication using the commands
below. Do not delete the catalog or rerun initialization over existing choices.

## Prepare and review in Python

Maintained bootstrap declarations provide independent factories:

```python
from scopecat.application import LabBootstrap


def create_bootstrap(project_root):
    return LabBootstrap(
        setup=initial_setup,  # returns ExecutableSetupSnapshot
        parameter_defaults=initial_parameters,  # returns ParameterRevisionContent
    )
```

Both factories are evaluated only for a new catalog. `parameter_defaults` is
optional: `LabBootstrap(setup=initial_setup)` initializes equipment without
creating any parameter default. Independent parameter branches can then be
created by the author. The optional default factory remains a bridge for older
execution consumers, not a parameter branch, calibration claim or save callback.
Generated starter and teaching projects declare each part directly; they do not
extract equipment from an author-owned full configuration. Routine parameter
editing does not reevaluate equipment declarations.

For ordinary parameter authoring, start with
[independent parameter revisions](independent-parameters.md): saving parameters
does not require setup. The sequence below specifically initializes a global
parameter default through the transitional registry API.

For an empty laboratory, setup can be prepared before any parameter configuration:

```python
revision = lab.setup.save(reviewed_setup, name="initial-setup")
lab.setup.activate(revision)
```

Here `reviewed_setup` is an `ExecutableSetupSnapshot` supplied by the adapter and
maintainer. Saving or selecting it does not create a parameter registry entry.
Publish parameter definitions and values against that exact saved revision:

```python
lab.config.set_parameter_default(
    name="initial-parameters",
    system_id="lab",
    setup=revision,
    catalog=author_parameter_catalog,
    parameters=reviewed_parameters,
)
```

The server loads the exact saved setup, validates the combination and records its
reference as provenance. Publishing selects the parameter default and leaves setup
selection unchanged. Select a compatible setup first; this API never bootstraps
one implicitly. `compose_configuration` remains available for pure composition,
and the full-snapshot `set_default` API remains transitional; see
[configuration ownership](../development/configuration-ownership.md).

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
