# Experiment input and original submission recovery

Experiment input is application-owned editing data. Its logical target is the
registered author workspace and experiment ID, not the current declaration hash
or a browser origin. The record keeps the raw request-field strings, control
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

## Storage boundary

Development schema 113 adds the experiment editing and original-request tables
under the existing application SQLite owner. Current-format backups retain them.
The [data compatibility policy](../data-compatibility.md) is unchanged: no reader
or migration for an earlier prebaseline format is introduced, and old stores are
rejected without modification. Native window/storage behavior is unchanged and
has separate acceptance in [draft recovery](draft-recovery.md).
