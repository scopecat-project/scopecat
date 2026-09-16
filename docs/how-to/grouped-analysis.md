# Compose scans and analyze completed groups

Start from an imported experiment declaration bound to the current author revision
(see [refresh author code](refresh-author-code.md)). Keep the returned request;
composition methods make copies:

```python
signal = author.refresh(signal)
request = signal().sweep(frequency=[4.7, 4.8, 4.9], gain=[1, 2])
prepared = author.prepare(request)
run = prepared.run().wait().result()
```

Inputs must be declared scannable. The default `mode="cartesian"` visits every
combination (six points above). `mode="paired"` pairs equally sized explicit
scan lists; fixed controls repeat unchanged. Unequal lengths are errors. A device's
local frequency or shot array remains an array inside each point, not another
outer request axis.

To vary one consumed parameter cell without editing the saved parameter workspace:

```python
request = signal().sweep(frequency=[4.7, 4.8, 4.9], mode="paired")
request = request.sweep_parameter(
    QubitParameters.drive_carrier_frequency,
    "q0",
    [4.8, 4.9, 5.0],
    name="center",
)
```

The declared field supplies units and key types. The experiment must actually
consume that exact cell. Unknown cells and duplicate coordinate names are errors.
Preview, submission and saved plans retain the composition and bind it into their
request identity. The console currently edits Cartesian control scans; use the
Python author API for paired scans and parameter overlays. Opening such a plan in
the console reports that limitation rather than dropping its settings.

For a **completed** run, apply an ordinary analysis function independently to groups:

```python
fitted = author.analyze_groups_as(
    run.id,
    "my_lab.analysis:fit_resonance",
    ResonanceFit,
    by=("gain",),
    fitting="frequency",
    repeats="separate",
)
for group in fitted.groups:
    print(group.receipt.coordinates, group.receipt.error, group.value)
```

`by` selects point-scalar coordinates. `fitting` identifies the coordinate consumed
by the fit; it may be a local instrument axis. Each function receives the selected
Dataset with its original dimensions, units and missing-value information. The
function owns selection, fitting, scientific rejection and uncertainty estimates.

`repeats="separate"` additionally groups coordinates named `repeat` (including
qualified names ending in `/repeat`). `repeats="combine"` passes all repetitions
together **without averaging**. Other unselected coordinates also remain in the
input. Explicitly choose your scientific aggregation policy inside the function.

Each group retains its exact point indices, measurement hash, source revision,
arguments and a publication. An exception becomes that group's error receipt;
other groups continue. Scientific rejection can instead be a normal typed result
with a status and absent candidate. The parent publication retains the group
manifest. There is no online scheduling or implied complete-group inference for
an ongoing run.

After editing the analysis source, call `author.refresh()` and run the same call
with `source="current"`. The default remains the run's original source. Old
publications are immutable and can be read after restarting without executing
analysis code:

```python
old = author.read_groups_as(run.id, fitted.publication.id, ResonanceFit)
```

See [ordinary Python analysis](../guides/ordinary-analysis.md) for typed function
arguments, native result dataclasses, tables and plots.
