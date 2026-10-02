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

`export_measurement_snapshot_in_transaction` also accepts the capture layer's
existing connection. Its header, acquisition history and retained selection all
follow that earlier read boundary, even if another connection commits acquisitions
before recording export begins. The standalone convenience exporter opens its own
read transaction and delegates to the same implementation.

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

The selected dataset keeps its original scientific identity, computed from the
recording header and ordered selected record hashes. It is distinct from the
capture manifest hash, which also identifies physical acquisition history and
packaging partitions. Sealed measurement content is backed by the recording
partition, not a fictitious dataset attachment path. Full package verification
checks its ID, schema and selected-data hash against retained run content.
Host-parameter evidence uses the same normalized persisted-model hash convention
as other model content. This changes prebaseline development identities; no old
identity fallback or historical-file rewrite is introduced.

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

Retained registry and context references include their exact effective configuration
and recursively retained base entries, including manual edits. Entry hashes are
checked against the captured configuration. Capture never activates a configuration
or substitutes the current workspace/registry head.

Typed sample bindings and target members retain exact sample revisions. Target
references retain their catalog-qualified historical revision and resolve its
members transitively. Neither current sample heads nor current target heads replace
accepted evidence. Owned sample attachments resolve to content-addressed payloads
for the verified streaming writer. Missing stored files fail capture; external URLs
and unavailable local-path references remain inert metadata and are never fetched
or read as local paths.

Analysis capture verifies the publication record and output index, then identifies
the precise retained artifact and dataset objects for streaming. The final writer
must verify object digests while copying; concurrent cleanup is an export failure,
not permission to omit a referenced output. Published analysis inputs now resolve
transitively across run and project subjects, retaining each exact publication once.
Each consumed output must match its kind, target, content hash and codec, using the
same identity calculation as publication admission. Figure layers also pull in
their exact published dataset source, even without a declared analysis input;
a preview never substitutes for those dataset bytes. Missing upstream bytes fail
capture. Other evidence families and application integration are not complete yet.
These capture components alone do not prove reference closure.

Interpretation capture resolves the exact request/response hashes across retained
step attempts, rather than substituting the latest judgment. The evidence includes
the question, response, actor, timestamps, step identity and procedure context;
the response must still satisfy its retained request schema. These are inert
records, not imported scheduler work. The composed exchange models live under
`scopecat.data_exchange.models`, above records and automation, to keep dependencies
pointing inward.

The low-level `scopecat.data_exchange` container now groups scientific documents,
recording partitions and content-addressed payloads without extracting them.
Recording views borrow the package's open archive; closing a view does not close
the package. Writing checks streamed payload hashes and verifies the staged package
before atomic publication. `copy_payload(reference, destination)` saves a selected
attachment only after verifying its bytes, without replacing existing files. The
caller supplies the destination; retained filenames are never extraction paths.
Package verification also resolves run and analysis content entries through the
same canonical-reference helper used by export, rejecting absent payloads or
different scientific content identities. Owned sample attachments must match
their retained artifact references. JSON record payloads have a 64 MiB metadata
budget; artifact bytes remain streamed. These checks establish indexed-content
integrity, not complete scientific-reference closure or executable trust.
`write_captured_exchange` connects resolved evidence to stored run content, analysis
outputs, sample attachments and measurement partitions inside the caller's read
transaction. Missing content stops publication, and its temporary partitions are
removed on completion or failure. Tests exercise later acquisitions committed while
that capture remains open, then read the package after closing the original store.
`export_scientific_capture` now starts from selected run IDs and follows typed
run/analysis references, retained input revisions and interpretation records before
calling the assembly function under the same transaction. The initial end-to-end
test captures a downstream publication with its upstream analysis and run, and
rejects a missing upstream original request. A software workflow also exports a
rerun after analysis, candidate review and configuration activation, retaining its
baseline recordings and explicitly referenced parameter proposals. This is still an internal export path:
candidate/proposal identity coverage, import-side closure
validation and the application command remain to be completed before general use.
It is not yet an application export command or a complete-run interchange promise.

Machine-local registrations stay outside the exchange. Historical configuration
and setup snapshots retain original connection descriptions as inert evidence so
their hashes remain verifiable. Import must not restore those descriptions into
the receiving machine's device registry or activate source/environment selections.

`import_scientific_capture` now verifies an owned copy and retains the entire
package in the application's existing immutable object store. Schema 104 indexes
captures and source-qualified run identities without inserting scheduler rows or
local device bindings. Repacked identical archives reuse the retained capture;
overlapping exports compare their shared runs, including recording and payload
identities, before committing any import rows. Different content for an existing
source/run is an explicit conflict. Current-format backup verification includes
the imported archive references.

The unified application now exposes streamed file upload, retained-capture listing,
evidence reading and verified archive download under `/api/v1/data/captures`.
Uploads accept file bytes, not server filesystem paths; invalid files, identity
conflicts and oversized uploads produce explicit errors without adding receipts.
HTTP integration checks prohibit requesting device capabilities throughout the
import/read/download journey. The existing Data page now lists imported captures
and invokes native Open and Save dialogs through its window bridge. Transfers
stream through this same backend; a failed download leaves an existing destination
unchanged, and cancelled dialogs perform no transfer. The shared application
operation lock covers native transfers, including their file dialogs.

Native File → Open dispatches to the focused window. Its page button and
Cmd/Ctrl-O share one window-owned command, with progress and error feedback even
outside the Data page. Successful opens select the capture through window URL
history; cancellation and failure retain the previous location. The native menu
dispatch and frontend history are covered separately by local tests; real native
menu/shortcut qualification on both platforms remains part of the desktop gate.
The selected run, acquisition/analysis view and page offset also belong to this
window URL. Back and recreating the view restore them; selecting another capture
clears these subordinate selections rather than reusing a same-named run from a
different source.

Local automated tests cover import feedback, duplicate receipts, cancelled saves,
failed saves and partial-transfer cleanup. They do not qualify real native dialogs
on either platform. Imported runs now expose their retained request/configuration
and paged measurements through the existing Data page. Acquisition order and the
captured analysis selection are distinct choices; a missing selection is an
explicit error rather than a last-acquisition fallback. The table renderer and
unit formatting are shared with the existing run view. Query identities include
the capture identity so identical run IDs from different sources do not share data
or selection state. Current-page point charts also reuse the run view's schema
planner, chart selector and renderer, with an explicit page-scope label. They
update with acquisition/analysis selection and pagination; they are not whole-run
summaries. Captured waveforms now use the ordinary bounded trace projector and
preview model, including min/max sampling, entity selection and unavailable-data
evidence. The UI selects a record by its acquisition or retained-selection offset
so repeated acquisitions of the same point remain separately inspectable. The
trace read never materializes the entire recording or requests device capability.
Local HTTP checks cover retry selection, a narrow peak under a sample budget,
missing values and series truncation; frontend checks cover record/entity choice
and clearing stale plots on errors. The record-table endpoint still transfers
full array values in its bounded record page; trace-response limits alone do not
qualify the whole page for large waveforms. A bounded table presentation and
semantic cross-page projections remain incomplete.
Retained analyses reuse the existing publication view for
facts, tables, figure previews, input references and execution evidence. Source
run links navigate within the same capture. Artifact reads resolve the verified
analysis record content hash and its subject-qualified payload owner, so repeated
analysis IDs in different runs cannot select the wrong attachment. Verified
attachments are staged before streaming and temporary copies are cleaned afterward.
This reuses the existing artifact download UI; native attachment-save behavior
still needs the desktop acceptance checks. Measurement chart/trace browsing,
application-wide Open commands, external-analysis provenance and full
scientific-reference validation remain outstanding; this is not the completed
desktop file journey.

Analysis-reference verification now runs in the public data layer during archive
verification as well as store capture. Published inputs and figure data sources
must resolve to the exact output kind, target, content hash and codec. Analysis
subjects and measurement/configuration inputs require their retained runs;
interpretation inputs require their exact historical judgment. Duplicate analysis
and interpretation identities are rejected. Export uses the same published-edge
resolver and validation rules instead of maintaining a second implementation.
Archive tests reject missing dependencies before publishing a destination file.
Input revision discovery is also shared between store export and archive
verification. Parameter, setup, plan, author, sample, target and configuration
references must resolve to exact retained identities. Plan ancestry and setup
definitions remain part of this closure; duplicate revision identities, altered
plan contents and damaged author source bytes are rejected without loading source
code. Tests remove individual families from a real captured plan chain and reject
missing ancestors rather than substituting current values.

Verification also reads indexed proposal records after checking their payload
identities. Proposal ownership, baseline configuration, publishing analysis and
named evidence outputs must agree. Candidate and composition references must
resolve to those proposals; direct candidate configurations are recomputed from
the retained baseline using the ordinary pure configuration resolver. Project
decision references check output existence and declared fact schema. None of this
activates a candidate. The analysis/review/activation/rerun export test verifies a
normal capture and rejects missing proposals, changed baselines/publications and
forged direct-candidate hashes. The real two-target maintenance journey now stops
its service, exports a verification run with composed proposals, and reads the
capture independently; changing a contribution hash is rejected. A separate
cross-run acceptance journey exports a run using its accepted registry entry,
retains the project decision publication, and rejects missing decisions or changed
decision schemas. These are local scientific-graph checks, not evidence of the
remaining external-analysis or desktop interaction requirements.

During PR 2 development, run focused checks locally; trigger CI after the complete
implementation and local self-review. The current data/evidence checks are:

```sh
uv run --locked pytest -q \
  packages/scopecat/tests/measurements/test_archive.py \
  packages/scopecat-server/tests/storage/sqlite/test_execution.py \
  packages/scopecat-server/tests/storage/sqlite/test_run_repository.py \
  packages/scopecat-server/tests/storage/sqlite/test_evidence_inputs.py \
  packages/scopecat-server/tests/storage/sqlite/test_evidence_analysis.py \
  packages/scopecat-server/tests/storage/sqlite/test_automation.py \
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
3. Open portable data in the unified application without preparing device or
   author execution environments. The application backend may serve both data
   access and task management; do not create a separate viewer entry mode.
   Split application commands from per-window navigation, drafts and selection.
4. Complete the bounded two-window journey on both platforms and record the shell
   choice. The current single-window prototype is not multi-window acceptance.

Current-format backup/restore remains a different capability: exchange selects
portable scientific evidence rather than copying an entire application store.
