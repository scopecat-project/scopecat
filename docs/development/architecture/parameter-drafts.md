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
maximum wait. Internal page navigation keeps the editor mounted. Editor close,
version selection and editor replacement wait for the latest input to be acknowledged;
a failed save keeps the editor and its error visible. A retained conflict is durable
and can be closed. Service unavailability does not unmount the editor.

Page hiding flushes pending edits. With unacknowledged input, `beforeunload` requests
the browser's native leave warning and attempts a flush. Choosing to leave anyway,
a browser that suppresses this warning, or abrupt process termination can still lose
unacknowledged input. Native host Quit handling is not qualified by this browser test.
Only acknowledged writes are durable.

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
adoption, preview invalidation and one real submitted B's frozen parameter snapshot.
`project-console.e2e.ts` owns the simultaneous running A / working B / historical C
binding check; the recovery journey does not repeat A's acquisition and history
reopening. Author refresh and historical procedure navigation remain separate
contracts in `author-launch.e2e.ts` and `LaunchDraft.test.tsx`, not substitutes for
historical run binding. Decision draft domain contracts are unchanged.

The failed-save browser journey keeps the network-failure display, cancelled native
browser leave warning and successful retry/reopen. `use-parameter-draft.test.tsx`
owns save/departure acknowledgements, retry, delayed conflicts and completion;
`ConfigWorkspace.test.tsx` owns failed close/version replacement and keeping the
editor mounted during service unavailability. These focused checks retain the
failure permutations without repeating them through a browser/service fixture.
`App.navigation.test.tsx` guards the configuration editor remaining mounted across
page navigation, independently of its save state.
The browser fixtures use synthetic reference experiments. Native packaging, OS
window/store behavior, real devices and installed user upgrades require separate
acceptance.

## Object parameter editing

In Samples, resolve an explicit working branch with a registered single-member
sample target and setup, then open its working parameters beside the map. Selecting
an object shows rows directly referencing its mapped entity. Edit non-key values
there; All parameters retains key and row-structure editing. Both presentations
share one mounted draft owner and save queue. Switching objects preserves raw input.

The view reuses the existing resolved context rather than adding another set of
selection controls. It must match the displayed sample revision and the recovered
working table baseline. Inline sample association alone cannot map objects, and
same-named entities on different samples are not interchangeable. Historical run
contexts remain read-only; object selection neither adopts inputs nor changes the
launch subject. Saving and adoption retain their existing explicit boundaries.

The first slice groups existing direct-reference rows under their parameter IDs
and authored descriptions. Composite-key rows remain distinct. Shared rows list
all directly referenced entities without claiming complete dependency or impact
coverage. String-key routes, inferred dependencies, new grouping metadata, new
persistent mappings, automatic row creation and multi-member target execution are
outside this slice. Raw entity references that cannot be read remain accessible in
the full table. The object form writes back to the original raw row, never its
filtered display position.
