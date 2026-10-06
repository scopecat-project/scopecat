# Application-owned Decision drafts

This bounded implementation follows the approved valuable-edits recovery principle.
Its design input is [PR #885 draft-recovery.md at 144c08b1](https://github.com/scopecat-project/scopecat/blob/144c08b1e7ebec5c34bd3d4f2297f831bc3aa556/docs/development/architecture/draft-recovery.md),
which was a draft PR, not merged main. This change does not copy that document or
include #885's native probes or #884's selection changes. Native website-store
isolation remains separate; its independent marker must still pass.

## Ownership and identity

The daemon's existing SQLiteProjectStore owns Decision editing data, under the
resolved application data home. Identity is the real procedure run, step key and
attempt. Request hash and run/step revisions are baseline metadata, never draft
lookup keys. Ports, browser origins and reviewer names do not define ownership.
There is no additional user system or browser-profile policy.

The narrow read/save/history service validates the retained interpretation target.
It never calls scientific validation, input submission, calibration or dispatch.
Typed input consists of reviewer text/kind, note, raw value text and editor mode;
invalid intermediate text is allowed, with bounded field sizes at the HTTP edge.
Numeric form input remains text until explicit form validation. Secrets are not a
new supported draft field; only the existing Decision inputs are included.

## Writes, conflicts and recovery

Each save appends a revision in one existing SQLite writer transaction. An expected
revision is compared to the latest non-conflicting revision of the same target.
A mismatch appends a conflicting copy without changing that head. The response
reports both the retained copy and current head. There is no last-writer-wins retry.
The UI preserves local input and requires an explicit conditional choice to make
that copy current. Both versions remain inspectable in paginated history.

A single editor serializes saves, debounces at 500 ms with a 2 s maximum wait, and
flushes on navigation/visibility changes. Replies acknowledge a specific input
generation; they do not replace newer input. Saved means a successful database
commit response, not a scheduled request. Network failure leaves an explicit
unsaved warning and retry. Browser/host termination before acknowledgment cannot
promise the newest keystrokes; the last confirmed commit remains recoverable.
No localStorage or fixed-port assumption participates in recovery.

Discard appends a tombstone retaining the input, conditionally and without deletion.
It resets that editor only after acknowledgment. Completion never deletes a draft.
Reads classify a changed baseline or a request no longer waiting as invalid for
recording. Recovered input does not refresh submission authority. The UI preserves
its baseline during polling, blocks recording when stale, and requires an explicit
fresh draft after inspecting the current request. History is copy-only, including
completed, stale, conflicting and discarded revisions. No automatic expiry is
introduced; history grows with confirmed edits.

## Format and verification boundary

Schema 111 adds the draft table within the existing atomic bootstrap. Per the
[prebaseline policy](../data-compatibility.md), schema 110 is rejected without
rewriting or deleting it; no migration or compatibility reader is added. Current
snapshot backup/restore copies the same application database, including all draft
states. Browser profiles are outside this change. No real home is migrated.

Backend tests cover reopening, identical target IDs in separate data homes,
concurrent CAS, stale/completed requests, discard history, backup roundtrip and
nonmutating rejection of the preceding schema. UI tests cover serialized responses,
maximum debounce wait, failed saves, navigation and discard races. The real-daemon
Playwright journey uses two pages, an invalid number, navigation, a fresh browser
context at a deliberately changed service port, a failed submission, completion
history and a second home. It does not qualify native Mac window storage, visual
menus/focus, distribution or a release.
