# Copy and edit an ordinary Python experiment

Use this path when composing existing laboratory operations. You edit controls,
scientific helpers, timing and analysis; the lab maintainer configures discovery
once. There is no per-experiment catalog, service or procedure to write.

## Start with the reference author folder

The reference application discovers `reference_lab.workflows.authored`. Its
`signal.py` contains two experiments: an analytic resonance and a Ramsey sequence
using the existing virtual laboratory operations. Run the reference project as in
[the reference-lab tutorial](../tutorials/reference-lab.md), then open **Launch**.
Select **Exploratory signal** or **Editable Ramsey**. Each control supplies its
name, unit, source mode and bounds from the same Python declaration.

In `examples/reference_lab/src/reference_lab/workflows/authored/signal.py`:

- Change the frequency default or bounds on `FREQUENCY`, or the response helper.
- Change `DELAY`, the Ramsey phase or shot count to compose supported timing.
- Change `selected_mean` to choose data differently or use your own fitting code.
- To create a new experiment, copy a function or file inside the author folder and
  give the experiment function a distinct name (or an explicit distinct decorator
  ID). Imported/reexported experiments are not discovered a second time. Duplicate
  IDs are errors rather than an arbitrary selection.

Keep imported shared operations such as `quantum_capture` and `ramsey_program`
as library calls unless you intend to maintain those shared capabilities. Their
compiler, physical mapping and drivers are not needed to edit this experiment.
A missing `ControlSet`, missing required default or invalid declaration reports
the source module/function. An invalid control edit reports its field and bound.
The initial GUI adapter accepts real scalar and quantity controls; it is not an
arbitrary Python-object or structural-argument editor. A parameter that changes
Python structure must have a usable default for this initial GUI path.

## Run and analyze from Python

The reference project enables complete author revisions. Refresh after editing,
then prepare and submit through the revision-aware notebook connection. No Git
commit, manual hash or device-service restart is required.

```python
import scopecat as sc

project = sc.open_project("examples/reference_lab")
with project.authoring() as authors:
    observed = authors.state()
    authors.refresh(expected_generation=observed.generation)
    launch = authors.prepare("signal", actor="alice")
    print(launch.preview.point_count, launch.preview.code_revision)
    submitted = launch.submit(request_key="sample-a-signal-001")
    print(submitted.procedure_id)
```

The preview pins a complete code revision and exact configuration. Reuse the same
request key to retry that submission. Use the GUI's **Fixed value**, **Scan values**
and **Scan range** controls to edit the same `ControlSet`. Fixed and one-point
scanned intent remain distinct even when they evaluate the same physical point.
Changing controls invalidates the previous preview.

See [refresh author code](refresh-author-code.md) for explicit old/new revision
analysis over retained data. Direct `signal.prepare/run` and `run.analyze` remain
available for a deliberately loaded Python process, including arbitrary supported
analysis arguments. Direct imports do not hot-reload; their declaration-only
provenance must not be confused with the full revision-aware path.

## Use the same declaration in the GUI

Choose **Refresh author code** after adding or editing an author file. Select the
experiment, edit its controls, and choose **Preview**. Check the point count,
configuration and preflight before **Start acquisition**. Submission retains a
normal single-run procedure and its exact child run; it uses the existing retry
key and configuration-generation checks. The author did not declare a procedure.
Follow **Open retained run** to inspect data, then reuse that run from Python for
independent analysis.

The daemon does not import author modules. Fresh workers validate the complete
candidate and publish it atomically; another fresh worker previews or executes
that revision. A failed refresh shows the source error and leaves the previous
catalog usable. Admitted and running author procedures retain their original
helper, experiment and analysis source. Historical analysis explicitly chooses
an archived revision. See [the refresh and recovery boundary](refresh-author-code.md),
including the schema 64 store requirement and external environment limitations.

## One-time laboratory composition

The maintainer adds the author package to the existing application:

```python
LabApplication(
    build_experiment_system=build_system,
    procedures=existing_procedures,
    launch_provider=existing_provider,
    author_modules=("my_lab.authored",),
)
```

`AuthorExperiments.discover(...)` is also available from
`scopecat.application.authoring` for explicit composition and inspection. Discovery
accepts configured modules/packages, not module names supplied through HTTP. The
application combines their generated entries and procedure definitions with the
existing maintained provider. Use distinct experiment IDs across both sources.

Changing reusable laboratory semantics belongs to its shared-capability maintainer;
adding lowering, hardware mappings, device protocols or execution guarantees belongs
to the compiler/driver maintainer. The [exploration acceptance agreement](../development/pilot-work-slices.md#exploratory-work-roles-and-executable-foundation)
keeps those responsibilities distinct from ordinary experiment editing.
