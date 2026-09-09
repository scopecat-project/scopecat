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

Start a fresh Python process after editing the author file. Uncommitted local
files work; no Git commit or manually computed hash is required.

```python
import scopecat as sc
from reference_lab.workflows.authored.signal import FREQUENCY, selected_mean

project = sc.open_project("examples/reference_lab")
authors = project.load_application().authors
assert authors is not None
signal = authors.get("signal")
edits = {
    "frequency": sc.axis(
        FREQUENCY.ref,
        [sc.Quantity(4.7, "GHz"), sc.Quantity(4.8, "GHz"), sc.Quantity(4.9, "GHz")],
    ),
    "gain": 2.0,
}
with project.connect(operator="alice") as lab:
    prepared = signal.prepare(lab, edits=edits)
    print(prepared.preview().initial_point_count)
    run = signal.run(lab, edits=edits)
    analysis = run.analyze(selected_mean(minimum=0.5))
    print(run.id, analysis.id)
```

`prepare` freezes the selected configuration for its preview. `signal.run`
resolves the configuration at that call, applies the same edits, and records the
admitted declaration and actual inputs. If you need to run exactly the earlier
prepared configuration, use `prepared.run(metadata=signal.provenance)` instead.
Reopen a retained run with `lab.get_run(run_id)` and call `.analyze(...)` again;
this publishes analysis over old data without acquiring or changing it.

For a scalar, use `"frequency": sc.Quantity(4.8, "GHz")`. For an explicit one-point
scan, use an axis containing that quantity. Both evaluate the same physical
point, but keep their different fixed/scanned intent. The GUI's **Fixed value**,
**Scan values** and **Scan range** controls use the same underlying `ControlSet`
operations, normalization and validation. Changing the GUI source discards stale
inactive values and invalidates the previous preview.

## Use the same declaration in the GUI

Reload the launch catalog after adding or editing an author file. Select the
experiment, edit its controls, and choose **Preview**. Check the point count,
configuration and preflight before **Start acquisition**. Submission retains a
normal single-run procedure and its exact child run; it uses the existing retry
key and configuration-generation checks. The author did not declare a procedure.
Follow **Open retained run** to inspect data, then reuse that run from Python for
independent analysis.

The daemon never imports these author modules. Existing short-lived project
workers perform discovery, preview and execution. This does not require restarting
the instrument daemon for initial discovery. An already loaded notebook collection
does not refresh automatically. Restart that Python process when changing code.

The initial fingerprint covers the experiment declaration's lexical source,
control catalog, wrapper and intent schema; the durable run request records that
declaration beside its actual inputs and point plan. It is **not** a transitive
helper/module identity or a source archive sufficient for replay. In particular,
a helper-only edit outside the decorated function is not yet a reliable revision
fence. Do not edit helpers while a run is admitted or executing. Atomic definition
refresh, helper dependency identity and retained in-flight implementations are
tracked by [#442](https://github.com/scopecat-project/scopecat/issues/442). Old results
remain readable using their retained result contract without loading new code.

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
