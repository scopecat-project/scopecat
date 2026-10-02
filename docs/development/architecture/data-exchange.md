# Independent data and analysis delivery

PR 2 starts with a recording container, then adds complete scientific reference
closure and the [two-window product journey](desktop-product.md). These are
separate acceptance steps; a readable recording is not a complete exported run.

## Implemented foundation

`scopecat.measurements.archive` writes and opens measurement snapshots without a
project, server or source loader. A snapshot retains the existing typed recording
header and Arrow append chunks; it does not invent another numeric serialization.
The manifest declares exact chunk sizes, SHA-256 checksums and acquired counts.
Chunk identities bind to the header/run and contiguous physical acquisition order.
Planned count remains distinct from acquired count for incomplete recordings.

The container is a ZIP file with fixed member names, read without extraction.
Unknown, duplicate or missing members are rejected. Manifest size is limited to
4 MiB, and each encoded chunk to 64 MiB. Export stages in the destination directory
and publishes without replacing existing files. Caller-owned data is never deleted.
Writers must supply a consistent captured append sequence, not an unbounded live
iterator over a changing recording.

Opening reads metadata only. `records(offset=..., limit=...)` verifies and decodes
only intersecting chunks, in acquisition order rather than logical point order.
Integrity of unvisited chunks is not asserted. Each selected chunk is decoded in
full; the encoded-byte limit is not an absolute decoded-memory bound. Very large
individual observations need a separate layout decision before general exchange
acceptance. Checksums detect corruption, not trustworthiness or execution consent.

```python
from pathlib import Path
from scopecat.measurements.archive import MeasurementSnapshot

with MeasurementSnapshot(Path("measurement.scopecat")) as snapshot:
    print(snapshot.header.dataset_schema)
    for record in snapshot.records(offset=0, limit=100):
        print(record.point_index, record.observables)
```

This is a development format, not a supported persistent-data baseline. No
prebaseline readers or migration chain are introduced. The snapshot has no claim
to complete plan, parameter, setup, analysis or artifact evidence, no permissions
to access devices, and no import path into the application store yet.

## Remaining PR 2 scope

1. Capture a stable recording and scientific reference closure from retained data;
   include plans, parameters, result contracts, analysis and artifact dependencies
   with explicit missing-reference failures. Keep machine-local bindings outside
   the exchange contract. Do not silently turn absent provenance into empty data.
2. Import with identity/content conflict detection and idempotent repeated import;
   expose independent Python analysis and retain external-analysis provenance.
3. Provide a data-only application entry without experiment runtime preparation.
   Split application commands from per-window navigation, drafts and selection.
4. Complete the bounded two-window journey on both platforms and record the shell
   choice. The current single-window prototype is not multi-window acceptance.

Current-format backup/restore remains a different capability: exchange selects
portable scientific evidence rather than copying an entire application store.
