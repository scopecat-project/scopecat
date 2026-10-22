# Experiment input and original submission recovery

Experiment input is application-owned editing data. Its logical target is the
registered author workspace, experiment ID and stable draft ID, not the current
declaration hash or a browser origin. Existing records without a draft ID belong
to the default draft. Named copies use fresh IDs and the same conditional-save
authority; a name is editable display text, never the storage identity. The record keeps the raw request-field strings, control
input modes and strings, declaration, source revision, scientific selection,
operator, collection and any adopted working-table, plan or analysis handoff.
Saving input neither validates scientific values nor acquires data.

## Input lifecycle

Entering an experiment loads its saved input before editing. Intermediate invalid
text survives reload and application restart. A recovered input always requires
review and fresh preparation; a retained preview or request key is never restored as
permission to execute. Current-source changes and declaration/default changes
preserve existing edits. Removed or no-longer-editable fields remain visible as
unresolved input until the user explicitly confirms their removal. New fields
receive current defaults. Exact parameter/setup references and adopted working
input remain pinned until the user explicitly chooses replacements; the existing
preview and admission checks still enforce their validity.

Unsent edits are coalesced per target after a short quiet period. At most one
request is in flight and one latest raw input is waiting; intermediate keystrokes
are not promised historical revisions. Submission flushes the latest input.
Already persisted versions and conflict copies remain unchanged.

Writes carry an expected revision and an idempotent editing operation ID. A stale
write appends a conflict copy without replacing the head. Both copies remain
available in recovery history, including after restart. Choosing the local copy
uses the observed head as a new conditional write; another intervening edit can
conflict again. Opening a historical copy follows the same rule.

The UI distinguishes loading, saving, confirmed saving and failed saving. A
failed current save keeps its input in the window and blocks replacing that
experiment until retry. Background saves that fail after navigation retain a
window-owned retry copy and expose their target. A lost save response is retried
with the same operation ID. Input that has not been confirmed by the application
cannot be promised recoverable after process loss; the UI does not label it saved.
Browser localStorage is not an authority or fallback store.

Reset replaces the current editing input with declared defaults in a new saved
revision. Submission does not remove the form or earlier revisions. Historical,
conflicting and discarded revisions remain in application data; navigation and
window closure are not deletion policies. Explicit plan/handoff selection must
not be overwritten by an older asynchronous draft read. These are experiment
editing records, not a general-purpose draft framework.

## Parallel editing and views

Editor, Monitor and Submitted work share the existing application shell. Editor
keeps parameter, scan and source inputs separate from live execution progress.
Monitor opens the explicitly selected procedure; Submitted work lists retained
statuses, including resource waits, without imposing a new scheduling order.
Submitted work has its own inline inspection selection; inspecting task B does
not replace Monitor A. Only **Open in monitor** changes that monitor selection.
Closing an inspection leaves background work running. Uncertain submission recovery remains
visible while editing; an unknown outcome never becomes a success indication.

Copy to new draft flushes the current input before selecting a new identity.
Switching also waits for confirmed saving; save failure leaves the current input
available for retry. Names, raw input and source baselines recover through the
existing application store. Historical plans and comparison handoffs open fresh
drafts and never replace the original run. Preview state, focus, selection and
scroll positions are not persisted as part of the saved-draft promise.

### Reopen an exact saved draft

The saved-draft link carries `workspace`, `experiment` and `draft` identities.
Reopening resolves that exact saved record before selecting its experiment and
uses its retained source revision when pinned. A missing record, unavailable
source revision or missing experiment produces an explicit recovery message;
it never silently opens the default draft or substitutes the current catalog.
The current editor is preserved while another linked draft is being resolved.

Switching saved drafts updates the editing identity in the URL. In-window monitor
and return-to-preparation links preserve it alongside the selected procedure, so
returning and then reloading reopens the same saved draft. These links do not
promise recovery of unsaved input or every transient view state, and do not force
an additional window.

### Compare submitted and current inputs

The submitted-input disclosure compares the selected immutable request with the
currently selected draft. It does not claim that this draft originated that
submission. Changed fields appear first; unchanged fields can be expanded.
Different workspaces or experiments are identified as separate preparations.
Unresolved defaults and incomplete input are not reported as equal. Comparison
is read-only: it neither modifies submitted work nor replaces preview, source,
unit, setup or admission validation.

### Reuse a retained configuration

In a run's **Frozen execution inputs**, **Use this configuration in a new draft**
copies its retained configuration into a new copy of the current editor
experiment. Choose that experiment first. The new draft keeps the current
experiment's inputs and subject, with the historical parameter revision, setup
and overrides, or the exact retained analysis-candidate source. This is
configuration reuse, not reconstruction of the original source code or run recipe.
Legacy and unknown configuration sources remain read-only; current defaults are
never substituted for missing historical evidence.

The new identity is saved before it can replace the selected editor. A failed
save preserves the current draft; retrying a lost response reuses the same save
operation and target. If the editor changes during saving, the new draft remains
saved separately and can be opened explicitly. Neither the original run nor the
existing draft is modified. The new draft clears stale plan/handoff and preview
state, requires review, and still passes the existing compatibility and submission
checks before execution. Creating it does not submit work.

A run window reuses an already-open exact run, including while its view loads.
If that window has navigated elsewhere or closed, opening the run creates a view.
Closing a view keeps the existing background execution semantics.

## Original submission lifecycle

Before sending a submit request, the UI waits for confirmed input persistence and
retains the complete original request with the preview's exact procedure
`definition` in the application database. The receipt is immutable and keyed by
`definition_id + request_key`; replay is allowed only with identical contents.
Failure or uncertainty while retaining it prevents the submit call. A synchronous
submission guard rejects overlapping preparation. After each persistence wait,
the UI rechecks the selected input and submission intent before sending acquisition;
changing receipt, choosing a new run, editing or leaving the provider cancels that
pending send. A retained but unsent receipt can still be inspected without retrying
acquisition.

After a lost response or restart, recovery only queries the original task. It
checks the complete definition reference, request key, request hash, retained
source workspace/revision, scientific binding, configuration source, manual
preview fence and plan reference. A missing or mismatched task remains
unconfirmed. Recovery does not compile current code, dispatch work or resubmit.
An old query response cannot overwrite a subsequently selected receipt or new-run
choice.

“Prepare a new run” is separate from recovering/opening the original task. It
invalidates the preview and request key, requiring fresh preparation on explicit
Start. Preview can inspect that preparation without submitting. Original receipts
remain in history regardless of task completion; one new run does not erase evidence
of an earlier uncertain submission.

## Start and optional preview

Start runs the existing preview and validity checks internally when there is no
matching retained preview. The returned evidence is passed directly to submission;
React rendering is not the handoff. Both paths preserve source, scientific context,
request hash and manual-operation fences. The displayed catalog source is pinned
for preparation. Known task effects and declared review instructions appear before
Start; generating a candidate is not branch publication, and declared review may
be automated. Starting does not answer a waiting interpretation request.

One synchronous preparation guard covers checking through submission. Input edits,
source/experiment changes, navigation and Cancel preparation revoke pending intent.
Each asynchronous boundary checks that intent before proceeding. The original-request
persistence path repeats the check after saving input and after retaining the receipt.
A cancelled retained-but-unsent attempt is historical evidence, not acquisition.
After submit is sent, the UI reports possible acquisition and uses task controls or
original-receipt recovery; it does not call this preparation cancellation.

## Native close and unsaved experiment input

A destructive native-window close checks that its experiment input is saved before
destroying the view. Application quit checks all open views, including hidden ones,
through the existing lifecycle owner. Input edits and unsent preparation are fenced
while the check runs. Failed saves, conflicts, missing acknowledgments and page
changes keep the affected view available; an aborted quit releases the edit fence
so the user can resolve the problem and retry. Deferred quit checks again when it
actually closes. The last-window hide behavior and existing execution-stop choices
are retained.

This guard covers experiment launch-input sessions, including unfinished sessions
retained after changing project. It does not promise to restore every transient UI
state or certify other editors. Browser unload prompts, mocked host tests and native
Windows interaction remain distinct qualification boundaries.

## Storage boundary

Development schema 113 adds the experiment editing and original-request tables
under the existing application SQLite owner. Current-format backups retain them.
The [data compatibility policy](../data-compatibility.md) is unchanged: no reader
or migration for an earlier prebaseline format is introduced, and old stores are
rejected without modification. Native window/storage qualification remains separate
from browser evidence; see [draft recovery](draft-recovery.md).
