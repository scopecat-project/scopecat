# Everyday Python author contract

This contract guides [the implementation slices](https://github.com/scopecat-project/scopecat/issues/466).
It is contributor guidance, not a tutorial for an available high-level API.
The executable fixture below uses existing public APIs. Dictionary workspaces are
available as described in [configuration editing](../how-to/manage-configuration.md);
standard dataclass views and managed notebook sessions are also available
through their guides.

## One parameter workspace

An editable workspace starts from an existing immutable configuration/context
version and holds local changes. A saved version has stable identity and content.
Opening another workspace from that version must reproduce its values without
changing the first workspace. Saving creates a version; it does not mutate a
previous version or publish the laboratory's shared default.

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
Rebase takes an explicitly chosen current version; it rejects mismatched sample,
working point or schema. Same-cell conflicts expose base, local and current
values so an author can choose deliberately. Saving one workspace must not
silently overwrite another workspace's branch.

Preparing a reviewed run freezes the workspace's current values, including
unsaved edits. Submission uses that frozen configuration. Later edits cannot
change the reviewed request or an admitted run. A changed laboratory generation
still requires the existing conflict/review path; freezing is not permission to
bypass it. Recorded run configuration and provenance must survive reopening.

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
Selecting a sample/working-point version chooses the inputs to the next run;
publishing a shared default is a separate explicit action. Saving an estimate or
selecting a candidate does not assert that it passed scientific verification.

An ordinary analysis function may return a typed dataclass, but that object alone
is not evidence. Managed analysis must retain the input run/dataset, analysis
identity and result receipt using existing publication records. Independent
verification refers to its own retained acquisition and decision. Neither a
successful acquisition nor a successfully executed fit implies a useful or
verified calibration.

## Interface ownership and implementation order

The workspace entry point is `lab.config.workspace(context=...)`. It exposes
keyed dictionary editing, diff, discard, save, freeze and explicit rebase. Saving
advances the workspace baseline without activating the shared default. Plain IDs
can select entity-keyed rows; the declared key type supplies their identity.
Bind standard dataclass rows with `params.table(name, row_type=Drive)`; both views
share edits, and constructors supply defaults only when explicitly adding rows.
The [managed author session](../how-to/managed-author-session.md) provides ordinary
fixed/scanned inputs, frozen workspace previews, receipt-backed submission,
bounded waiting and read-only job recovery (#471).

| Producer | Contract consumed by other slices |
| --- | --- |
| [#468 workspace](https://github.com/scopecat-project/scopecat/issues/468) | Existing config/context storage, coherent cells, immutable freeze and save semantics |
| [#469 typed rows](https://github.com/scopecat-project/scopecat/issues/469) | Standard dataclass binding to an existing table; explicit unit conversion |
| [#470 unknowns and schema](https://github.com/scopecat-project/scopecat/issues/470) | Complete unknown tables and explicit creation/structure changes |
| [#471 author session](https://github.com/scopecat-project/scopecat/issues/471) | Scan/preview/submit/wait/reopen consuming the frozen workspace |
| [#472 analysis](https://github.com/scopecat-project/scopecat/issues/472) | Ordinary function adapter over existing Dataset and retained publications |
| [#473 verification](https://github.com/scopecat-project/scopecat/issues/473) | Typed candidate with evidence, independent verification, explicit selection |
| [#474 recipes](https://github.com/scopecat-project/scopecat/issues/474) | Honest symbolic input/reference types and bounded supported arithmetic |

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

`reference_lab.everyday_author` supplies reusable known/missing parameter inputs
and `acquire_everyday_author_inputs(lab)`. It uses the existing reference table,
`exploration_config`, `exploratory_signal`, real admission and retained recording.
It needs no hardware. The two acquired runs have the same five frequency points:

- The peaked response has a known center at 4.8 GHz and gain 1. Selecting values
  at least 0.5 produces one point with mean 1.
- The flat response has gain 0. Acquisition completes, but the same analysis
  rejects it because no values meet the selection. It is a deliberately useless
  scientific input, not a simulated device failure.
- The missing carrier prevents the consumer's preview. Today's fixture encodes
  it by an absent cell; this is **not** proof of the future full `None` table
  behavior. That acceptance remains with #470.

Run the current executable check from the repository root:

```sh
uv sync --locked
uv run pytest -n 0 examples/reference_lab/tests/test_everyday_author.py
```

The existing test fixture creates a fresh project and real daemon. The check
previews known/missing inputs, retains both runs, analyzes success and failure,
reopens the original data, and confirms no extra acquisition or shared-default
publication occurs during analysis. Reuse these run identities and normal
Dataset/publication APIs in later slices; do not replace them with a fake result
registry. Synthetic resonance/selection is a contract fixture, not a validated
physical calibration or a full fitting lesson.

## First usable release and novice evaluation

Release the first preview after workspace, typed rows and managed author session
are usable together: open a **preinstalled table**, edit a typed row, scan,
preview, run, save and reopen. From-zero table creation needs #470. Initially use
an existing registered analysis; user-written ordinary analysis needs #472.
Batch import/export and complete method migration do not block this first trial.

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
trial has **not** been performed by adding this fixture.
