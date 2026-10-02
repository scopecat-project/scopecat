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
Retries can make physical acquisition count exceed the logical point limit. The
limit constrains point indices, not the number of historical acquisitions.

The container is a ZIP file with fixed member names, read without extraction.
Unknown, duplicate or missing members are rejected. Manifest size is limited to
4 MiB, and each encoded chunk to 64 MiB. Export stages in the destination directory
and publishes without replacing existing files. Caller-owned data is never deleted.
Writers must supply a consistent captured append sequence, not an unbounded live
iterator over a changing recording.

The SQLite exporter captures the header, append index and analysis selection in
one read transaction. Acquisition can continue through WAL while it reads immutable
objects. Concurrent destructive cleanup may fail an export; publication remains
atomic and leaves the destination absent. This recording export is not yet the
complete scientific evidence export described below.

When supplied, the retained analysis selection is stored separately in pages of
at most 1000 logical points. `selected_records(offset=..., limit=...)` follows those
exact acquisition references in logical point order; it never substitutes the last
physical retry. A raw snapshot without a captured selection explicitly rejects this
analysis view. Missing selection and an explicitly empty selection are distinct.

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

## Independent Python use and recording import

Use an ordinary environment with the public `scopecat` package. Reading does not
import `scopecat_server`, original author code or vendor SDKs. To use the same
labeled analysis API as the application:

```python
from pathlib import Path
from scopecat.measurements.archive import MeasurementSnapshot

with MeasurementSnapshot(Path("measurement.scopecat")) as snapshot:
    data = snapshot.dataset()

values = data.to_xarray()
```

`dataset()` explicitly materializes the captured selection in memory. Large-data
processing should iterate `selected_records()` pages. The returned materialized
dataset survives closing the archive. A raw snapshot without a captured selection
cannot be converted by guessing which retries to use.

`scopecat.measurements.imports.import_measurement_snapshot(source, directory)`
copies into a caller-selected data directory, verifies the owned copy in full,
then publishes it atomically. It checks unselected historical chunks too. Equal
run identity and manifest content make a repeated import idempotent; equal run
identity with different content is a conflict, including a later partial capture.
Neither case silently replaces existing data. ZIP timestamps are not scientific
identity. This file-level import does not register execution state or complete
application-level evidence import. External analysis publication/provenance is
still part of the remaining work below.

## Evidence capture under construction

Retained evidence capture now reads the original request, accepted run snapshot,
effective configuration and content index within a caller-owned SQLite transaction.
The configuration must still hash to the accepted value. Typed parameter, setup,
plan and author references resolve to exact retained revisions, including hidden
plans and their ancestry. It does not inspect current branch heads, re-resolve a
setup using today's devices, or require the original source directory. Retained
source files are checked as bytes without extracting or executing them.

Analysis capture verifies the publication record and output index, then identifies
the precise retained artifact and dataset objects for streaming. The final writer
must verify object digests while copying; concurrent cleanup is an export failure,
not permission to omit a referenced output. Traversal across analysis inputs and
other evidence families, final container assembly and application integration are
not complete yet. These capture components alone do not prove reference closure.

Machine-local registrations stay outside the exchange. Historical configuration
and setup snapshots retain original connection descriptions as inert evidence so
their hashes remain verifiable. Import must not restore those descriptions into
the receiving machine's device registry or activate source/environment selections.

During PR 2 development, run focused checks locally; trigger CI after the complete
implementation and local self-review. The current data/evidence checks are:

```sh
uv run --locked pytest -q \
  packages/scopecat/tests/measurements/test_archive.py \
  packages/scopecat-server/tests/storage/sqlite/test_execution.py \
  packages/scopecat-server/tests/storage/sqlite/test_run_repository.py \
  packages/scopecat-server/tests/storage/sqlite/test_evidence_inputs.py \
  packages/scopecat-server/tests/storage/sqlite/test_evidence_analysis.py \
  packages/scopecat-server/tests/storage/sqlite/test_author_revision_repository.py
uv run --locked basedpyright
uv run --locked lint-imports
uv run --locked ruff check .
uv run --locked ruff format --check .
```

These are data-layer checks, not substitutes for the complete desktop journey.

## Remaining PR 2 scope

1. Extend stable recording capture to scientific reference closure from retained data;
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
