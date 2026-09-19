# Record apparatus observations without a wiring database

Use apparatus history for a physical line, cable, port group or other non-sample
object whose measurements and notes you want to find together. You do not need a
complete connection graph. These are descriptive historical records: recording a
connection note does not assert that it is the current wiring, and attaching a
room-temperature measurement does not qualify it as a cryogenic calibration.

The first delivery provides Python and HTTP APIs. Workbench browsing and selecting
an apparatus object as an executable experiment subject are separate follow-ups.
Existing sample identities, launch selections and active configuration are unchanged.
Do not register an artificial sample just to store a line's notes.

## Register and find a line

With an existing connected `LabClient` called `lab`:

```python
line = lab.apparatus.create(
    "fridge-2-line-3",
    name="Fridge 2 input line 3",
    kind="microwave-line",
    aliases=("L3", "old blue cable"),
    actor="Li",
)
found = lab.apparatus.search("blue cable")
```

Names and aliases help discovery; the catalog-qualified object reference establishes
identity. Renaming an object creates a descriptive revision. An observation retains
its original exact revision, so later changes do not rewrite its description at the
time of recording. There is no automatic physical-identity matching by name.

## Keep the original file and known conditions

```python
from pathlib import Path

slides = lab.apparatus.import_file(Path("room-temperature.pptx"))
observation = lab.apparatus.observe(
    line.ref,
    title="Room-temperature transmission measurement",
    actor="Li",
    conditions={
        "temperature": "room temperature; exact temperature not recorded",
        "connection": "VNA ports 1–2; see slide 3",
    },
    note="Reference only. Cryogenic transfer has not been established.",
    attachments=(slides,),
)
```

Import reads a file on the Python client's computer and stores its original bytes
in the connected data space, with a content hash. The server never opens a path
from the filename. Files are downloaded as attachments, not rendered or executed;
each file may contain up to 64 MiB. Owned attachment bytes are included in
[current-format recovery copies](backup-and-restore.md).

`observed_at` is an optional timezone-aware measurement time. Leave it absent if
unknown; `recorded_at` is the separately retained server recording time. Conditions
are optional human descriptions, not a validated physical-state model. Keep a
low-temperature observation as its own record with its own conditions, even when
it concerns the same line.

## Find history and relate retained measurements

```python
page = lab.apparatus.history("fridge-2-line-3", limit=50)
for item in page.items:
    print(item.id, item.draft.title, item.draft.conditions)

linked = lab.apparatus.observe(
    line.ref,
    title="Reference considered during this experiment",
    actor="Li",
    run_ids=(run.id,),
    note="Associated for review; not an assertion that this run calibrated the line.",
)
```

Object search matches the current ID, name and aliases. History uses recording
order with a cursor; it does not invent an ordering by unknown measurement dates.
Linked run IDs must already exist in this data space. Association does not alter
the run's recorded scientific subject, parameter provenance or calibration status.
A run filter can find which observations reference a retained measurement.

Records are append-only. Correct a note by creating a new observation with
`supersedes=observation.id`; the prior record stays readable and in history. A
correction must concern the same object. For uncertain network retries, choose and
reuse an explicit `observation_id`; an identical retry returns its original record,
while different content conflicts instead of overwriting it.

## Reference information is not an accepted calibration

These records are reference information only. Copying a number into an experiment
requires an explicit parameter operation and provenance appropriate to that path.
Automatic calibration reuse additionally requires a defined applicability contract
and qualified evidence. Neither step occurs when saving an apparatus observation.
The proposed separation of apparatus configuration, parameter ownership and
calibration publication is described in the [apparatus history design](../development/architecture/apparatus-history.md).
