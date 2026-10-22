---
default: minor
---

# Reopen saved drafts and inspect submitted work independently

Saved-draft links reopen the exact editing identity and retained source revision,
with explicit errors for missing records or unavailable sources. Monitor return
links preserve that identity. Submitted work supports inline inspection without
changing the selected monitor, and read-only comparison distinguishes changed,
incomplete and unresolved-default inputs.

Retained parameter or candidate configurations can be copied into a new draft
for the current editor experiment without changing the original run or draft.
The new draft requires review and existing validation; unsupported historical
sources remain read-only. This does not recreate the original run recipe.

Native destructive close waits for confirmed experiment-input saving. Failed or
unknown saves retain the view; an aborted quit releases its editing fence for retry.
Existing background execution and last-window hiding behavior are preserved.
