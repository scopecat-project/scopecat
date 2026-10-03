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
This includes declarative quantum gate/measurement recipe queries selected with
`with_recipes(...)`: a literal operand and an unconditional operation can consume
a cell even when the experiment has no explicit `parameter_ref` for it. Queries
may use aliases, arithmetic and cross-table keys. A cell whose lookup key is also
overlaid is rejected because that exact cell is not guaranteed across the scan.
The selector cell itself can be scanned when its own key stays fixed.

Consumption checks do not build pulses or expand scan points/repetitions. They
remain conservative for arbitrary Python resolvers, dynamic operands, conditional
branches, entity-set maps, unexpanded dynamic fragments and scoped candidate
gates. Scoped gates use `with_recipe_parameters(...)`; measurements keep baseline
semantics even inside a scope. These limits do not require fake parameter reads
in an experiment. Domain compilers must consume the effective point snapshots in
`DomainBatchRequest.parameters` for recipe-only overlays to reach actual waveforms.

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

A context-based `@sc.analysis_step` can use the same grouping call:
`context.measurements()` returns only the selected group. Explicitly reading a
reference run with `context.measurements(reference_run)` reads that reference's
measurements without applying the primary run's point selection. Ordinary and
context functions share typed argument decoding and retained-source defaults.
Invalid invocation arguments fail before group execution; errors inside an
individual group remain isolated to that group's receipt.

`repeats="separate"` additionally groups coordinates named `repeat` (including
qualified names ending in `/repeat`). `repeats="combine"` passes all repetitions
together **without averaging**. Other unselected coordinates also remain in the
input. Explicitly choose your scientific aggregation policy inside the function.

Each group retains its exact point indices, measurement hash, source revision,
arguments and a publication. An exception becomes that group's error receipt;
other groups continue. Scientific rejection can instead be a normal typed result
with a status and absent candidate. The parent publication retains the group
manifest.

For a fixed Cartesian scan, follow complete groups while acquisition continues:

```python
follow = author.follow_groups_as(
    run.id,
    "my_lab.analysis:fit_resonance",
    ResonanceFit,
    by=("gain",),
    fitting="frequency",
)
follow_id = follow.id  # retain this alongside your run id
cursor = 0

# Repeat when you want the next results; each request is bounded.
page = follow.poll(after=cursor)
for group in follow.results(page):
    print(group.receipt.coordinates, group.receipt.error, group.value)
cursor = page.next_cursor
print(page.follow.state, page.follow.finished_count, page.follow.group_count)
```

Drain further pages while `page.has_more` is true, even after the follow completes.
An empty page while `state == "running"` simply means no new result is available yet.

The same ordinary or context function receives the same group structure as the
offline call. A group becomes eligible only when every expected logical point has
a committed acquisition. Received data and execution recovery groups alone do not
prove this. Instrument-returned frequency arrays remain inside their point; repeated
T1 groups retain all planned delays. Paired, point-cloud and adaptive domains do not
yet have a live completion contract and report an explicit error; completed-run
offline analysis remains available.

Each group fixes a small manifest of exact acquisition identities and hashes,
referencing the original arrays without copying them. Source revision and arguments
are fixed when the follow starts. Later data and source edits cannot change an
existing input or result. Exported `.scopecat` files retain these slices, readable
with `capture.measurements(run_id, selector=slice_id)`.

The application executes one background group at a time through its existing
bounded author workers. Defaults limit a follow to 1,000 groups, 4,096 logical points
per group, 64 MiB of encoded input chunks per group and 60 seconds per invocation.
These budgets are configurable in `follow_groups_as`; at most eight follows run
concurrently. Planning reads at most 65,536 values of each grouping axis, including
duplicates. Groups that fail scientifically remain isolated. Missing points after
acquisition ends are marked incomplete. `completed` means processing finished;
inspect `failed_count` and individual receipts before using results.

Closing Python does not cancel acquisition or analysis. Reconnect without rerunning:

```python
follow = author.reopen_group_follow_as(follow_id, ResonanceFit)
page = follow.poll(after=cursor)
# To stop analysis only:
follow.stop()
```

Stopping an active invocation requests worker cancellation; poll until it has
settled. An application interruption during publication is marked `attention` with
an uncertain result, never silently repeated. Inspect retained publications before
starting another follow. The run view shows progress and new publications as groups
finish. Input cleanup is blocked until active analysis stops. These results do not
automatically gate later acquisition; feedback requires an explicit execution
dependency. Stateful streaming reducers are a separate capability.

The reference lab's [ordinary trace examples](../../examples/reference_lab/src/reference_lab_authors/authored/group_traces.py)
provide `power_trace` / `locate_resonance` and `synthetic_t1` / `fit_decay`.
Use `by=("power",), fitting="trace/frequency"` for the first, and
`by=(), fitting="delay", repeats="separate"` for the second. The virtual instrument
returns each full spectrum; the T1 example deliberately uses a known, zero-baseline
synthetic decay. Its scientific assumptions are visible in the ordinary functions,
not built into the group scheduler. These software checks do not qualify hardware
or a physical fitting policy.

After editing the analysis source, call `author.refresh()` and run the same call
with `source="current"`. The default remains the run's original source. Old
publications are immutable and can be read after restarting without executing
analysis code:

```python
old = author.read_groups_as(run.id, fitted.publication.id, ResonanceFit)
```

See [ordinary Python analysis](../guides/ordinary-analysis.md) for typed function
arguments, native result dataclasses, tables and plots.
