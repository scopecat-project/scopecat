# Save and reopen experiment plans

A named plan saves immutable experiment inputs for later work. Saving, opening,
copying and deleting a plan do not acquire data, reserve a device or change the
lab's default configuration. Every execution uses a fresh normal launch preview.

In **Experiments**, edit the experiment inputs and preview them. Enter a plan
name and choose **Save plan**. The revision records the definition, supported
author code revision, scalar/scan controls, exact configuration entry or named
parameter context and overrides, and the exact sample revision when a sample is
selected. Experiments without a sample can use the current default configuration;
saving does not require creating a working point. Later changes to the default
never silently replace the saved configuration.

Use **Saved plans** to open a revision. The saved name and revision, experiment,
sample and source result appear before the optional exact-reference details.
The current execution operator is separate from the person who saved the plan.
Opening does not reuse that person's operator identity or a previous submission
key. Review the selected configuration and obtain a new preview before starting.

Edit inputs and preview again to **Save new revision**, or use **Save as copy**
for another named plan. Revision comparison shows the actual changed fields.
Existing revisions and acquired runs remain unchanged. **Delete** hides a named
head; exact references from historical runs still read the retained revision.
Resetting a launch draft only resets its editable UI state. A pending unknown
submission retains its separate original-request recovery entry.

## Start from a retained analysis

From a retained comparison result, import the suggested inputs into Experiments.
Review the configuration and sample, preview, then save a plan. The plan retains
the source run, exact analysis publication and content identity. The admitted
procedure and each child run retain the same immutable plan reference, so their
**Source analysis** link remains available after closing the console. Importing
suggestions alone remains a session draft; saving establishes the durable bridge.

## Python

The author client uses the same launch, plan and preview records as the GUI:

```python
from scopecat.application.author_project import AuthorProject

with AuthorProject(endpoint) as author:
    prepared = author.prepare("frequency-amplitude", actor="alice")
    saved = prepared.save_plan("Frequency check", saved_by="alice")

    # A new preview selects the saved configuration and code, not today's default.
    reopened = author.prepare_plan(saved.ref, actor="bob")
    receipt = reopened.submit(request_key="frequency-check-2026-09-09")
```

Use `lab.plans.list()`, `lab.plans.get(ref)`, `lab.plans.save(command)` and
`lab.plans.delete(ref)` for typed revision management. `ExperimentPlanSave.previous`
selects an optimistic edit; `copied_from` creates a distinct plan identity. Pass
`PlanAnalysisSource` to `prepared.save_plan(..., source=...)` when deriving inputs
from a retained analysis. The daemon checks that publication's exact identity.
The server rejects stale edits rather than choosing a winner.

The shared configuration resolver supports active and explicit named contexts,
and exact non-active registry entries selected by a plan. Admission checks the
stored plan against the actual launch request and the server's checked-preview
facts in the same transaction. An exact request-key replay still reopens its
original procedure before current admission fences are evaluated.

## Storage and supported source boundary

Plans use project schema **65**, immutable owned objects and small revision/head
tables. A schema 64 or older project is rejected before modification; retain its
matching runtime to read/export it and use a separate schema 65 project. No
implicit migration is supplied. Snapshot/restore includes all plan revisions,
including hidden heads, and preserves referenced author bundles and sample content.

Supported author revisions use the existing complete-source manifest and
matching environment contract. Maintained definitions retain their declaration
fingerprint; a missing or changed definition requires an explicit revision and
fresh validation. Plans do not archive external SDK installations or turn an
unsupported source/environment into an executable historical environment.
