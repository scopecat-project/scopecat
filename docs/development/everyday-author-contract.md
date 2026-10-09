# Everyday Python author contract

This contributor contract records the shared semantics and validation strategy for
[implementation slices A0–A8](https://github.com/scopecat-project/scopecat/issues/466),
whose APIs and consumer views are implemented. For hands-on use, start with
[configuration editing, dataclass rows and new tables](../how-to/manage-configuration.md),
[managed author sessions](../how-to/managed-author-session.md),
[ordinary analysis](../guides/ordinary-analysis.md), and
[independent candidate verification](../how-to/verify-parameter-candidates.md).
The [quantum authoring reference](../reference/python/quantum.md) covers recipe APIs.
This document explains their contract rather than replacing those user guides.

## One parameter workspace

An editable workspace starts from an existing immutable parameter
version and holds local changes. A saved version has stable identity and content.
Opening another workspace from that version must reproduce its values without
changing the first workspace. Saving creates a version; it does not mutate a
previous version or change another session's selection.

Dictionary rows and a standard dataclass view must address the same cells. An edit
through either view is visible through the other; there is no second cache of
parameter truth. Ordinary Python attribute assignment is not promised immediate
runtime validation. Save and preview validate types, units and declared bounds,
with the table, row and field identified in errors. A failed validation leaves
edits available for correction and does not create a saved version.

A diff compares current edits with the workspace's opening/saved baseline.
Discard explicitly restores that baseline. Reopen selects a saved version;
it must not silently keep unsaved changes or choose a newer shared default.
The saving implementation must state whether a successful save advances the
workspace baseline and keep diff/discard consistent with that choice.

Saved versions form explicit branches rather than a mutable “latest” pointer.
Rebase takes an explicitly chosen current branch version and checks its schema.
Same-cell conflicts expose base, local and current
values so an author can choose deliberately. Saving one workspace must not
silently overwrite another workspace's branch.

Preparing a reviewed run freezes the workspace's current values, including
unsaved edits. Submission uses that frozen configuration. Later edits cannot
change the reviewed request or an admitted run. Preparation also requires an
explicit setup reference. Changed device connections require re-resolution and
preview; unrelated global selections do not invalidate these inputs. Recorded
run configuration and provenance must survive reopening.

## Types, unknowns and origins

A standard dataclass declares familiar Python field types. Optional fields retain
`None` as unknown. Defaults apply only when the author explicitly creates a new
row, never to reading existing missing data or selecting a different working
point. Missing values should be visible in a complete table; a particular
experiment blocks only on values it actually consumes.

Unit metadata on an annotated numeric field supplies runtime conversion and
validation. It does not provide static dimensional algebra. A typed read may
present a canonical unit, but must not silently rewrite the persisted unit or
value origin. A deliberate edit is a separate operation from normalization.

Manual, estimated, imported and measured values have distinct provenance. A
manual override of a measured value must not inherit its measurement claim.
Parameter versions, subjects and setups are selected independently for the next
run. An empty selection requires parameters and setup before preview; it never
uses a global default. Changing the session's subject preserves its independent
parameters, setup and unsaved branch edits, and clears the previous batch.
Verified publication advances a reviewed parameter branch. Saving an estimate or
selecting a candidate does not assert that it passed scientific verification.

An ordinary analysis function may return a typed dataclass, but that object alone
is not evidence. Managed analysis must retain the input run/dataset, analysis
identity and result receipt using existing publication records. Independent
verification refers to its own retained acquisition and decision. Neither a
successful acquisition nor a successfully executed fit implies a useful or
verified calibration.

## Interface ownership and implementation order

The workspace entry point is `lab.parameters.workspace(branch_name)`, or
`session.params` after `session.use(parameter_branch=...)`. It exposes keyed
dictionary editing, diff, discard, save, freeze and explicit rebase. Saving
advances the selected branch with a checked generation. Plain IDs
can select entity-keyed rows; the declared key type supplies their identity.
Bind standard dataclass rows with `params.table(name, row_type=Drive)`; both views
share edits, and constructors supply defaults only when explicitly adding rows.
The [managed author session](../how-to/managed-author-session.md) provides ordinary
fixed/scanned inputs, frozen workspace previews, receipt-backed submission,
bounded waiting and read-only recovery. [Ordinary analysis](../guides/ordinary-analysis.md)
retains typed conclusions and their input/source publication.

| Producer | Contract consumed by other slices |
| --- | --- |
| [#468 workspace](https://github.com/scopecat-project/scopecat/issues/468) | Independent parameter storage, coherent cells, immutable freeze and branch save semantics |
| [#469 typed rows](https://github.com/scopecat-project/scopecat/issues/469) | Standard dataclass binding to an existing table; explicit unit conversion |
| [#470 unknowns and schema](https://github.com/scopecat-project/scopecat/issues/470) | Complete unknown tables and explicit creation/structure changes |
| [#471 author session](https://github.com/scopecat-project/scopecat/issues/471) | Scan/preview/submit/wait/reopen consuming the frozen workspace |
| [#472 analysis](https://github.com/scopecat-project/scopecat/issues/472) | Ordinary function adapter over existing Dataset and retained publications |
| [#473 verification](https://github.com/scopecat-project/scopecat/issues/473) | Typed candidate with evidence, independent verification, explicit selection |
| [#474 recipes](https://github.com/scopecat-project/scopecat/issues/474) | Honest symbolic input/reference types and bounded supported arithmetic |
| [#475 views](https://github.com/scopecat-project/scopecat/issues/475) | Live notebook representations and console drafts with units, origins and explicit save/select/publish boundaries |

No slice introduces a second configuration registry, result store or dataset
model. Producers own shared storage/wire definitions and client regeneration;
consumers rebase and use the contract. Workspace freezing is first checked using
existing low-level run APIs; the session slice owns the end-to-end new facade
check, avoiding a dependency cycle.

The managed session extends the existing revision-aware `AuthorProject` worker
path without reintroducing notebook-global project imports. A submitted job owns
its durable request key before submission; an ambiguous response is recovered
with that same identity, not a fresh key that could acquire twice. Dataset
materialization is explicit, and retained runs can be reopened through a new
connection after the submitting session closes.

## Shared executable scenario

`testing/fixtures/retained-signal` provides a device-free current author project
with an explicit center parameter and an editable synthetic response. Its server
journeys acquire a peaked and a flat response, retain ordinary analysis source
and arguments across edits/restart, and verify candidate cells using independent
measurements. Analysis reuses retained data without publishing shared defaults.

Run the current executable checks from the repository root:

```sh
uv sync --locked
uv run pytest -n 0 packages/scopecat-server/tests/author_journeys
```

Missing inputs and unknown cells are owned by
`packages/lab-tools/tests/test_unknown_parameter_authoring.py`; it covers complete
tables, visible `None`, unrelated/required consumers, frozen edits and old-run
readback after schema changes. These checks replace the old everyday/exploratory
wrappers. Synthetic resonance is a contract fixture, not physical calibration or
a full fitting lesson.

The facade coverage now also includes:

- `test_managed_author_session.py`: frozen workspace submission, fresh-process
  reopening and recovery from a lost response without duplicate acquisition.
- `author_journeys/test_analysis.py`: ordinary function results with retained source,
  arguments and restart behavior.
- `author_journeys/test_candidates.py`: cell proposals tied to analysis receipts and
  independent verification policies.
- `test_branch_parameter_editor.py` and `test_dataclass_parameters.py`: durable edits,
  unknown cells, live typed views and bounded, escaped notebook representations.
- `apps/scopecat-ui/e2e/parameter-context.e2e.ts`: independent sample/parameter selection,
  structure changes, keyboard edits retained across navigation, and a real
  Python→GUI→Python round-trip preserving units and untouched cell origins.

These tests exercise the current author boundary; passing them does not
establish novice usability or physical calibration performance.

## Implemented preview and novice evaluation

A0–A8 provide the implemented preview: edit an existing table or declare one from
zero, bind standard dataclass rows, preview and run scans, write ordinary analysis,
stage and independently verify candidates, save and reopen versions, and inspect
the same parameters in notebooks and the console. Explicit structure changes
must be saved before preparing a run. Selecting a candidate and publishing a
shared default remain separate actions.

[A9 batch import/export](https://github.com/scopecat-project/scopecat/issues/476)
is later convenience work. It does not block this preview. Automated fixtures
have exercised the implemented route; evaluation by a basic Python/NumPy user
who did not implement the APIs is still **unperformed**.

Observe at least one basic Python/NumPy user who did not implement these APIs.
Give them the task and supported documentation, then record the following for
each step without filling gaps on their behalf:

| Task | Evidence to record |
| --- | --- |
| Find and edit a parameter | Time to first edit, unfamiliar concepts, help required |
| Correct a wrong unit or missing value | Whether the message identifies a repair the user can make |
| Preview and run a small scan | Whether the user understands which edits will run |
| Change a value after preview | Whether the frozen review and next edit are distinguishable |
| Save, discard and reopen | Whether the user predicts which values return |
| Analyze peaked and flat inputs | Whether acquisition success is distinguished from useful analysis |
| Select a working point | Whether the user understands that the shared default is unchanged |

For each task record completion, stalls, exact assistance, and the artifact/run
identity. Require no Pydantic, record assembly, manual hashes, generation tokens,
`sys.path` changes or custom polling in the ordinary-user route. Report observed
friction rather than declaring success because an agent ran the test. A human
trial has **not** been performed by these implementation and automated checks.
