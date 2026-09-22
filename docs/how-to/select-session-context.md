# Select an experimental context

An author session can remember the sample, experimental batch, saved working point, record collection
and operator for its next requests. This selection belongs to that client. Two
notebooks connected to the same daemon can make different selections without
changing the lab's active configuration or another client's defaults.

```python
session = sc.notebook()
collection = session.create_record_collection("Chip A · cooldown 3")
session.use(working_point=working_point_ref, collection=collection.id, operator="Li")

prepared = session.prepare(rabi())
run = prepared.run().wait().result()
session.history()
session.run(1)
```

Here `working_point_ref` is an existing exact `ConfigContextRef`; see
[parameter contexts](manage-configuration.md). Selecting it also selects its bound
sample and batch. `rabi` is an experiment imported from your project. The same `use` API is
available on `project.authoring()` and `AuthorProject` clients. Selection performs
read-only validation; it does not run an experiment, activate configuration, or
establish calibration validity.

## Change one choice without resetting the others

```python
session.use(operator="Wang")
session.use(working_point=another_working_point_ref)
selected = session.selection
```

Omitted fields remain selected. Switching working points selects the new point's
sample and batch and retains the collection and operator. Selecting a sample or
target on its own starts a new scientific scope and clears the previous working point
and batch:

```python
session.use(sample="chip-b")
session.use(target=exact_target_ref)
```

A mismatched sample/working-point pair or missing collection is rejected; a failed
update leaves all previous selections intact. `selection` is an immutable snapshot.
A later `use` call affects only future preparations; already prepared requests,
submitted jobs and retained results keep their original selection. The ordinary
server preview/admission checks remain in force if shared configuration changes.

In IPython, `sc.notebook()` reuses this kernel's session. Cell-boundary source
refresh preserves the selection. Closing and reopening starts with empty defaults;
selection is not saved as an application-wide current chip. Multiple notebooks in
one shared kernel share its session, just as they share Python variables.

## Override one preparation

Existing explicit `prepare` arguments keep their meaning:

```python
session.prepare(rabi(), parameters=other_parameters)
session.prepare(rabi(), context=another_working_point_ref, actor="Guest")
session.prepare(rabi(), context=None, record_collection=None)
```

An explicit `parameters`, `candidate`, `context` or `sample` selects the whole
scientific scope for that request. It does not combine with an inherited sample
or working point or batch. An explicitly supplied batch must agree with the
chosen working point or candidate. Changing only the batch preserves the other
scientific choices and validates their compatibility.
The selected operator and collection are inherited independently
unless explicitly overridden. `context=None` deliberately uses the ordinary active
configuration without the session's sample/working point. Clearing the collection
for one request uses the default record collection. None of these overrides changes
`session.selection`.

A saved recipe has its own frozen scientific scope.
`session.prepare_plan(ref)` uses that scope and inherits only the current operator
and collection. Both may be explicitly overridden for that execution. The plan
retains its original batch regardless of the session default. An explicit `batch`
argument to `prepare_plan` is an assertion and must match the saved scope.

## Number lookup follows the selected collection

With a collection selected, `history()`, `run(number)` and `run_number(run)` all use
that collection. Asking for another collection's run number raises a clear error.
Durable string run IDs always resolve independently of this selection.

Pass `collection=another_id` for a scoped lookup, or `collection=None` to use the
legacy store-wide number/history without changing the selection. A session with
no collection selected keeps the previous store-wide behavior. See
[record collections](record-collections.md) for stable addresses and current-format recovery.

Python/Notebook clients and the workbench support existing single-sample
working points. Declared batch/cooldown applicability is covered by
[experimental batches](experimental-batches.md). Multi-sample assemblies, setup
applicability and multiple code workspaces remain pending. A collection named
“cooldown 3” is not proof that a previous
calibration applies in that cooldown.

## Select context in the workbench

In **Experiments**, **Measurement context · this page** holds the sample and
operator. Open **Browse samples, batches and collections** to choose registered
samples, an experimental batch and a record collection. Lists have explicit
**Load more** controls. **New batch** and **New collection** create named catalog
entries; use the newly created entry when ready. Creating metadata never starts a
measurement or changes another page's selection.

These selections survive switching experiments, refreshing author code and moving
between console views. **Reset launch draft** resets experiment inputs and detaches
a saved recipe while keeping the page's sample, batch, collection and operator.
Reloading the browser or connecting to a different project starts a new selection;
separate tabs do not share mutable defaults. This is still one connected code
workspace, not a multi-workspace application selector.

Select a saved working point through **Configuration → Use for next experiment**.
Its exact sample revision and batch become the selected scope. Those fields are
read-only while bound; **Use lab default** releases the working point while keeping
sample/batch selection. When saving a working-point copy, **Choose experimental
batch** can explicitly bind the copy to a new event. Its parameter values remain
starting estimates, not new calibration evidence.

Opening a saved plan retains its sample revision and batch, while collection and
operator remain the destination page's choices. Opening a plan restores its frozen scientific scope independently of the page's
previous sample or batch. Imported analysis
suggestions retain the current operator and collection; without an explicit
working point they clear the old scientific scope.

Every selection edit invalidates the preview. **Preview** validates it through the
same launch contract used by Python; only **Start acquisition** admits a procedure.
The original request remains available if a submission response is lost, even after
subsequent edits. None of these selections publishes a new shared default config.

Declared batch and collection selection currently require an authored experiment
(the standard `@experiment` path). Legacy custom launch providers report an
unsupported-selection error at preview; the console never drops these choices to
make such a launch proceed. Their procedure adapters need explicit support before
using this scope.

## Inspect the scientific selection

`session.selection.science` contains the subject, configuration choice and explicit
batch scope. `prepared.preview.reviewed` contains the exact binding and checked
configuration source. Ordinary users can keep using the short `sample`, `target`,
`working_point` and `batch` arguments. Clients constructing requests directly use
`ScientificSelection`; do not combine it with those convenience arguments.
A preview's reviewed evidence must be carried into submit or save requests.


## Software execution scenarios

An executable setup may now declare a software scenario. Its label, model identity,
coverage, limitations, seed (when actually used) and settings form part of the setup
identity. The ordinary starter declares its virtual temperature and analytic signal
responses; laboratory adapters may declare their own bounded models. An absent
scenario means **not declared**, not proof of physical execution.

Inspect the current or selected revision in **Configuration → Executable setup**.
The preview's `prepared.preview.reviewed.binding.scenario` is the frozen choice for
that preparation; historical runs retain their own scientific binding. Changing
setup or refreshing code does not rewrite that evidence. A software declaration
rejects any non-virtual connection anywhere in the configured instrument registry.
It is a contract for trusted providers, not an operating-system sandbox for arbitrary
Python drivers, and it does not automatically emulate unsupported hardware.

This first slice uses the existing service-wide setup selection and provider.
It does not introduce a per-tab backend switch or hot-swap a laboratory adapter.
Select a supported setup explicitly using normal setup activation; saved working
points remain separate. Rebind a working point to the selected setup as a new copy
before experimenting, leaving its original branch intact. Setup identity checks
also prevent publishing a software result into a different executable setup.

Model declarations describe the adapter's actual behavior. A seed or setting is
not applied to an arbitrary driver merely by adding it to the declaration. Do not
claim a physics model or deterministic random stream that the provider does not use.


## Start from an adapter configuration template

In the workbench's configuration view, **Configuration templates** lists complete
recipes offered by the current laboratory adapter. Read the description and any
software model coverage before importing. Import saves a new immutable setup and
an independent parameter revision in one transaction; it creates no combined
configuration entry and does not select either as a global
default. A template is an initial recipe, not evidence of calibration validity.

Review and activate the imported setup, then use its parameters for a launch.
Setup activation changes the entire service, including other pages' and notebooks'
future preparations. Existing previews retain their original setup and cannot be
submitted against a different setup. The application still enforces device ownership
and requires explicit inventory changes where applicable.

Notebook clients use the same operations. With `lab` connected to the current
service and `session` its author session:

```python
templates = lab.setup.templates()
for template in templates:
    print(template.id, template.label, template.description)

chosen = templates[0]  # Choose after inspecting the available recipes.
imported = lab.setup.import_template(chosen, name="software-trial")
current = lab.setup.active()
lab.setup.activate(imported.setup, expected_generation=current.activation.generation)
session.use(selection=imported.selection)
prepared = session.prepare(experiment())
```

`imported.selection` contains the exact parameter and setup references. It does not
invent a sample or working point. To apply parameters from an existing working
point to another setup, use the explicit setup rebind workflow instead. Source
refresh preserves this selection. Importing again with the same name and intent
returns the same records; changing the template or import intent requires a new
name. Imported parameters are available through `lab.parameters.get(name)` and
`lab.parameters.list()`. To use them with the session's current setup instead,
call `session.use(parameters=imported.parameters)`; this preserves its subject
and batch. Parameter records themselves have no setup ownership.

Only templates declared by the running adapter are offered. Selecting a software
label does not replace the instrument backend or make physical connections virtual.
An adapter without templates simply has no recipes to offer. Local installation
settings remain deployment inputs, not the daily scenario selector.


## Understand a rejected preview

Expected compilation and configuration checks retain their problem codes,
messages and locations through the author worker. The workbench shows these
findings with the selected software scenario's model and limitations. An explicit
capability finding means the selected setup cannot satisfy that operation; it does
not prove the instrument family or framework can never support it. For example,
a setup without a flux route needs a matching route or a different experiment.

Notebook clients receive `AuthorLaunchRejected` with the same typed diagnostic:

```python
from scopecat.records.launch_rejection import AuthorLaunchRejected

try:
    prepared = session.prepare(experiment())
except AuthorLaunchRejected as error:
    print(error)
    for problem in error.diagnostic.problems:
        print(problem.code, problem.location, problem.details)
```

A rejected preview does not submit an acquisition. Fix the inputs or select a
suitable setup, then prepare again; an expected check failure does not require
restarting the author worker. The UI clears an earlier successful preview when a
new preview fails. Ordinary Python bugs and uncertain submission failures retain
their existing diagnostics and are not relabelled as unsupported model capabilities.
