# Independent data and analysis delivery

PR 2 delivers portable scientific evidence, independent Python analysis and the
[two-window product journey](desktop-product.md). A readable recording alone is
not a complete exported run.

## Current delivery status

PR 2 closeout is complete as of 2026-10-03. The maintainer completed the four-item
Windows native checklist without an obvious problem using the corrected package.
[Native distribution acceptance](https://github.com/scopecat-project/scopecat/actions/runs/37110939966)
passed on Mac and Windows at `13f365110`;
[CI](https://github.com/scopecat-project/scopecat/actions/runs/37111184660) passed at
`9ecbd5142`, whose only additional change repairs a frontend test fixture.
The final frontend suite passed 390 tests. The Python/WebView host is retained
for this slice; see the [decision](desktop-product.md#technology-decision).
The evidence below is historical detail, not outstanding work. Final closeout
changes documentation only and must pass ordinary PR CI before squash merge.

The 2026-10-03 transition-design review adds a focused closeout batch: imported
captures now participate in ordinary preview/retry cleanup with resource-owned
bytes and per-capture run identity membership; the recording-only import library
has been retired. Recording page summaries consume raw records incrementally.
Native file operations have window-local serialization and application-owned
wait/cancel accounting, and imported/live waveform results share presentation.
These changes superseded earlier package and suite evidence for affected paths;
the relevant checks and native acceptance have since completed. The development store schema is 106;
no migration or rewriting of existing stores is performed.

Local checks for this batch: 139 related Python tests passed, followed by the new
raw-array lifetime regression and four schema/plan tests (five passed); the seven
HTTP exchange tests passed again after bounding trace reads by the series budget.
All 388 frontend tests passed. Python and frontend type checking, Ruff, frontend
lint/formatting, 11 import contracts and documentation links passed. A subsequent
13-test targeted run passed with one Windows-only open-handle test skipped on Mac;
configured Python type checking again reported no errors or warnings.
The rebuilt `30a1389b4` Mac package passed startup/stop, native import wait/quit,
cleanup and reimport observations; see the [native evidence](desktop-product.md#file-operation-and-cleanup-closeout-2026-10-03)
for the exact cancellation and tray-observation limits.

This section is the PR 2 handoff checkpoint, updated on 2026-10-03 against
`9ecbd5142`. Use the completed evidence below, not earlier planning
language or a conversation summary. Detailed evidence is linked, not a new task
list. A completed check needs repeating only when a relevant change, failure or
specific unresolved concern invalidates its evidence.

| Requirement | Current evidence | Still required before merge |
| --- | --- | --- |
| Scientific reference closure and import conflicts | Local archive/storage/HTTP tests, composed-maintenance and accepted-decision journeys; closure self-review completed | No outstanding closure implementation item |
| Independent analysis and retained provenance | Fresh public-wheel environment; native export / Python analysis / Mac native open; consumer/documentation review | Complete |
| Large-data access | 256 MiB waveform verification, independent analysis and packaged Mac browsing; bounded previews; Windows checklist accepted | Complete within tested sizes and shapes |
| Ordinary desktop interaction | Recorded Mac observations and maintainer acceptance of the four-item Windows checklist | Complete within the bounded checklist |
| Delivery and host decision | Both native distributions passed; current implementation CI passed; host reuse and self-review recorded | Documentation closeout CI and squash merge |

Completed local work:

- Independent public-wheel analysis and native export/analysis/open round trip:
  `0e5e840e8`; export and shutdown failure recovery: `b6db9c4a5`.
- Waveform memory corrections: `a29590c3f`; packaged Mac 256 MiB waveform browsing:
  `0605dfc65`. These are complete within their recorded scope, not pending
  implementations.
- User guide: `882719f91`; packaged runtime, relocation, repeat startup and
  independent author-environment acceptance: `00423b27f`.
- Latest local Python run: 3856 passed, 14 failed under macOS sandbox process
  restrictions, one skipped. All 14 failed cases passed when rerun with normal
  process permissions. This is coverage across two runs, not one all-green run.
  Frontend: 84 files / 388 tests passed. Type checking, 11 import contracts,
  Ruff, documentation links and strict documentation build passed. Subsequent
  verifier changes passed targeted Ruff and type checks; no production code
  changed after those suite results.
- A local DMG of the qualified Mac package passed image integrity, extracted
  application signature and deliberate-tamper detection. Gatekeeper rejected
  the ad-hoc-signed quarantined copy; notarization is not provided. This check
  did not attempt Finder first-open or change the user's security settings.

Completed closeout:

1. Scientific-reference closure self-review is complete: traced the single read
   transaction through typed run/input/analysis/interpretation traversal and
   payload assembly, and checked reader validation of revision hashes, proposal
   baselines, exact publication outputs and recording selections. Existing
   source-isolation, missing-reference, conflict and composed-maintenance tests
   remain the evidence; no new closure defect was found in this review.
2. Mac native closeout is complete within its recorded scope. The maintainer
   confirmed that the final window stayed hidden and restored through the menu
   bar; the rebuilt package's file-work Quit choices and cleanup/reimport journey
   passed, and the isolated test application was explicitly quit without residue.
3. The PR description describes the delivered scope. The current implementation
   CI and corrected native distributions passed, as linked above.
4. The maintainer accepted the Windows four-item native checklist. Real open-file
   cleanup recovery, cancellation and file integrity remain automated checks;
   short transfers do not require racing a manual quit action.
5. Final self-review found no remaining blocking issue: scientific-reference
   closure, consumer boundaries, cleanup ownership/retry and desktop transitions
   have recorded checks. The latest review checked all-window restoration,
   replacement navigation, Quit shortcut delegation and repaired verifier/test
   consumers. Retain the current host; broad GUI redesign and execution isolation
   remain separate work. The maintainer authorized squash merging after closeout.

Settled scope: one application/backend; no separate viewer mode; closing the
last window hides to tray on both platforms even when idle; only explicit Quit
exits. Broad GUI redesign, vendor execution isolation and real-device acceptance
are not newly added PR 2 work. Do not reopen these decisions merely because a
conversation was compacted. Private consumer pin updates remain separate from
data-only application qualification.

The detailed checks below describe their actual scope. The
[desktop evidence](desktop-product.md) distinguishes native observations from
automated checks; earlier observations are not claims that all later gates passed.

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
atomic and leaves the destination absent. This low-level recording export is
distinct from the complete scientific evidence export described below.

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
to complete plan, parameter, setup, analysis or artifact evidence, and grants no
permissions to access devices. Application import uses the scientific container
described below, not this low-level recording container alone.

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

Measurement snapshots are recording partitions used by scientific exchange, not
a second import library. Application import accepts complete scientific captures
through the path below; independent Python readers can still inspect partitions.

## Scientific evidence capture and application import

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
capture. Closure also depends on the other evidence families and reference
verification described below; these components alone do not prove it.

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
baseline recordings and explicitly referenced parameter proposals. The application
now exposes this collector through a current-run export command. Its presence does
not establish a complete-run interchange promise: the final evidence-closure
self-review remains part of this milestone.

Machine-local registrations stay outside the exchange. Historical configuration
and setup snapshots retain original connection descriptions as inert evidence so
their hashes remain verifiable. Import must not restore those descriptions into
the receiving machine's device registry or activate source/environment selections.

`import_scientific_capture` now verifies an owned copy and retains the entire
package under its capture-owned immutable object directory. Schema 105 indexes
captures and their source-qualified run memberships without inserting scheduler rows or
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

The current-run view also offers native export through `/api/v1/data/runs/{id}/file`.
It asks for a destination before collecting evidence; the server removes its
temporary archive after streaming or on collection failure. Captured analysis
attachments use the same native save and atomic transfer path as archives, with
the captured analysis record hash selecting the source. Cancellation clears
previous completion feedback, failure leaves the destination unchanged, and
success reports the saved path. Browser attachment downloads remain available
through the same data API. Local HTTP, bridge and UI tests cover these paths;
packaged Mac save, cancellation and failure recovery are recorded in the desktop
evidence. Windows native saving is included in the accepted four-item checklist.

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
and clearing stale plots on errors. The record-table response now has a distinct
presentation model and one shared 4,096-array-sample budget per page. Larger
values expose shape, unit, available count and failure reasons instead of sample
buffers or masks; small arrays remain available for point/entity charts. The UI
labels summaries and excludes them from numeric plots. This does not truncate or
rewrite the stored scientific values, and trace reads use the original records.
Partition summaries do not join buffers; unknown segment lengths remain unknown.
Decoding still reads intersecting archive chunks (bounded by the recording format),
and this sample budget is not a general byte limit for arbitrary text metadata.
Semantic cross-page projections remain incomplete.
Retained analyses reuse the existing publication view for
facts, tables, figure previews, input references and execution evidence. Source
run links navigate within the same capture. Artifact reads resolve the verified
analysis record content hash and its subject-qualified payload owner, so repeated
analysis IDs in different runs cannot select the wrong attachment. Verified
attachments are staged before streaming and temporary copies are cleaned afterward.
This reuses the existing artifact download UI; Mac native attachment saving has
been observed. The bounded preview and native command implementations above,
and external-analysis provenance below, are covered locally. Packaged waveform,
lifecycle and Windows evidence is recorded in the completed desktop gate.

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
complete desktop interaction requirements.

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

## Independent analysis and delivery boundary

External analysis writes now have a storage path: `ScientificExchange.write_analyses`
accepts ordinary prepared `AnalysisPublication` values and writes a new capture.
It preserves source run evidence, recording partitions and owned payloads, adds
independently owned analysis records, and verifies the completed evidence closure
before publishing the destination. Run-owned additions are rejected because they
would change the identity of the imported source run. This uses the existing
exchange format and application analysis records, not a second result format.
Local tests cover preserved recordings, extracted result attachments, missing
payload rejection, original/result imports coexisting, and repeated result import.
`open_capture(source, output=destination)` now supplies the ordinary
`AnalysisContext` and `analysis_function` path over a file. Each successful `save()`
atomically updates the new output owned by that handle; later failures preserve
previous saves. Existing destinations cannot be claimed or overwritten.
Local function calls retain the implementation fingerprint, interpreter
description and ordinary execution input/output bindings. The
[ordinary analysis guide](../../guides/ordinary-analysis.md) shows the author API;
authors do not construct exchange records themselves. Application and file
publication share validation, revision selection, output encoding and figure
projection. `PublishedAnalysis` consumes a transport-independent view protocol
rather than requiring a daemon response model.
Local tests cover facts, attachments, datasets, tables, figures, repeated saves,
changed-argument revisions, re-reading and preservation after interruption. The HTTP import journey now
uses an ordinary author-created result, not a hand-built publication fixture.
An independent Python-process check reopens results without importing the server
or lab adapter. Each changed file publication currently rewrites the portable
archive. A local Mac probe (2026-10-03) retained a 256 MiB binary artifact and a
four-point recording while saving three analysis revisions. Saves took 0.64,
0.73 and 0.77 seconds; process peak RSS remained 180,158,464 bytes before and
after saving. Reopening verified all three revisions and the original payload
digest and size. This demonstrates bounded copying for a large retained artifact,
not large-waveform analysis or a general latency guarantee. Saving remains O(file
size) per changed publication and needs transient space for the new archive;
many iterative saves over multi-gigabyte recordings remain a design constraint.

A separate local waveform check used 32 reversed-order acquisitions, each with
1,048,576 float64 samples (256 MiB total), and an explicit ascending logical-point
selection. A fresh public-wheel consumer environment verified every sample,
physical order and units, then ran an ordinary analysis over the logical selection,
saved its mean/count conclusion and reopened it. This exposed two memory problems:
selection pages retained up to 1000 entire waveforms, and execution-input identity
registration attempted to JSON-encode the `Dataset` dataclass before recognizing
that its internal members were not JSON values.

Selection now yields records incrementally with a 64 MiB encoded-chunk LRU budget;
decoding and consumer-retained records can add memory beyond that cache budget.
Analysis identity uses the existing measurement dataset content hash directly,
including for typed result views. Tests cover first-record streaming, eviction
and reuse without changing logical order, and reject raw dataset JSON serialization
during ordinary analysis. On this Mac, verified streaming peaked at 234,913,792
bytes, down from 455,475,200; complete materialized analysis/save/reopen peaked at
564,150,272 bytes, down from 2.1–2.7 GB. The corrected analysis journey took 4.68
seconds. These are local observations, not fixed latency or total-memory promises.
The ordinary analysis API still intentionally materializes selected measurements;
larger-than-memory analysis requires bounded record processing.

The packaged Mac application has now completed a scalar native-export / external
Python analysis / native-open round trip, displaying the unchanged measurement,
new conclusion and retained input/execution evidence. The same analysis also
passed in a fresh environment with only the public wheel and its dependencies,
outside the repository, without an installed server or adapter. The waveform check
above qualifies independent analysis at that size; the rebuilt Mac package also
displayed its array summaries, bounded waveforms and saved conclusion. Windows
desktop acceptance remains outstanding. See the
[desktop evidence](desktop-product.md) for the observed scope.

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
   choice. Implemented multi-window support still needs complete platform acceptance.

Current-format backup/restore remains a different capability: exchange selects
portable scientific evidence rather than copying an entire application store.
