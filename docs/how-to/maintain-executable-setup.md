# Set up devices for an experiment

Open **Devices and drivers** to add, name, test or retire a device. You do not
need an experiment setup to use this page. Selecting a device reads its details;
**Connect** opens a manual session and **Test connection** runs a separate test.
Experiments and manual sessions share the same device ownership.

Keep three kinds of changes in their own places:

- **Devices and drivers:** address, transport options, installed driver and device safety.
- **Configuration → Experiment setups:** experiment aliases and their registered devices.
- **Parameter versions:** scientific inputs, estimates and saved parameter branches.

A setup is a named definition of devices, roles, channels and execution policies.
It references registered devices rather than owning another editable copy of their
connections. Each experiment page or Python session can choose its own setup.

## Start from a template

In **Configuration**, select an available template and import it. The application
saves its setup and independent parameter version. Choose **Use imported
configuration for next experiment**, then select the sample and enter experiment
inputs. No global activation is needed.

A template can reuse registered devices, but cannot overwrite their connection
settings. If an import reports a different connection, review the registered
device first. Repeated imports are not a way to change its address.

## Reuse or revise a setup

Choose a saved setup in **Configuration → Experiment setups**, adjust its device
bindings and save it under a new name. The original definition remains available.
Device safety requirements apply to every setup and cannot be relaxed by an
experiment's success or failure policy.

For topology, routing and other authored setup changes, edit the definition in
Python:

```python
definition = lab.setup.definition("bench-a").definition
revised = definition.model_copy(update={"routing": reviewed_routing})
setup = lab.setup.save(revised, name="bench-a-rewired", note="Reviewed wiring")
```

`reviewed_routing` is the routing supplied by the experiment or adapter author.
Saving a setup does not open hardware or change another page.

Before preparing an experiment, resolve its named setup and select the exact
result with the parameters:

```python
setup = session.setup.get("bench-a-rewired")
session.use(parameter_branch="daily", setup=setup)
prepared = session.prepare(experiment, parameters=session.params)
```

`setup.get(name)` resolves the definition against the current registered device
connections. The result retains their exact revisions and driver identities.
If a relevant device changes before submission, recheck the setup and preview
again. Submitted work retains its captured inputs; it never follows a later
connection head.

## Change or retire a device

Finish or cancel affected queued experiments and close manual sessions before
changing a connection. The application checks ownership and releases an idle
connection before committing the change. An unsuccessful release keeps the old
connection revision and reports the session that needs recovery.

Renaming a device changes its display name only. Retiring it retains its records
and prevents new work from resolving it. After successful retirement, its address
can be assigned to a replacement device; old experiment snapshots still refer to
the retired device.

For direct Python control without an experiment setup, see
[Control instruments](control-instruments.md). For parameter editing, see
[Parameter branches](parameter-branches.md).
