# Parameter working tables

Parameter editing has its own application-owned draft service and append-only
SQLite history. It does not reuse Decision run identities or a generic draft
framework. A working table belongs to an explicit existing branch; independent
copies belong to their exact immutable parameter baseline. The active branch table
is recovered even when another writer advances the branch.

Raw text, units, table rows and metadata are saved before scientific validation.
Writes use the expected draft revision. Stale writes append a retained conflict
without replacing the current head. Explicit discard and successful completion
retain history; close merely hides the editor. Copies can select an exact history
revision. Per-editor requests are serialized, debounced at 500 ms with a two-second
maximum wait, and flushed on navigation/page hiding. Only acknowledged writes are
durable; abrupt termination can lose unacknowledged input.

## Scientific actions stay explicit

Draft persistence does not create a parameter revision, advance a branch or run an
experiment. Freeze validates the exact current draft and reviewed branch generation,
then returns the existing parameter configuration shape: an immutable baseline plus
replacement overrides. Whole-parameter removal cannot be represented by this shape
and requires a checkpoint. Existing override limits still apply.

Launch keeps the adopted draft identity and revision only as UI provenance. It
checks for source changes, requires explicit adoption and a fresh preview, and
checks again before submission. The scientific request retains captured values,
not a live draft reference. Subsequent edits cannot change queued/running/history
requests. Opening a saved plan or comparison handoff starts from that captured
configuration without attaching the previous editor's working source.

A changed branch retains input but blocks freeze/checkpoint until its latest head
has been reviewed. Keeping the table acknowledges that generation without automatic
cell merging or replacing its original baseline. The work-table branch identity
cannot be reassigned through raw draft saves.

Checkpoint creation and draft completion share one SQLite transaction with the
existing configuration service. Retrying a completed expected revision returns the
same immutable result. Failed validation, branch conflicts or failed completion
writes leave the draft active and publish no revision. A completed working table
allows the next opening to start from the new branch head.

## Storage and evidence

Schema 112 adds the parameter-draft table. Current-format snapshots include raw,
conflicting and completed history. Unsupported development schemas are rejected
without migration or rewriting historical files, as required by the prebaseline
policy. Draft input is bounded to 1 MiB per record; automatic retention has no
purge policy. Drafts do not add supported credential or secret fields.

`test_parameter_drafts.py` exercises recovery, conflicts, exact-history copies,
atomic completion/retry, branch fencing, validation and backup/restore.
`parameter-working-inputs.e2e.ts` exercises raw invalid input, navigation, concurrent
editors, a fresh browser context after full service restart on another port, explicit
A/B adoption and unchanged historical A. Its fixture uses synthetic reference
experiments. Native packaging, OS window/store behavior, real devices and installed
user upgrades require separate acceptance.
