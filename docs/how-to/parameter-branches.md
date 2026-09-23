# Edit parameters on a branch

A revision is immutable declarations and values. A branch names an editing
history. Neither owns equipment nor establishes calibration validity.

With an initial parameter revision:

```python
lab.parameters.create_branch("chip-a/daily", revision=initial)
session.use(parameter_branch="chip-a/daily", operator="Alice")
params = session.params

# The same dictionary/typed-table editor used in the tutorials.
params["drive"]["q0"]["frequency"] = 5.15
params.diff()
version = params.save(note="Update readout calibration")
```

For editing alone, `params = lab.parameters.workspace("chip-a/daily")` needs no
setup, sample or working point. Typed row views, scalars, declarations, schema
changes, table import/export, `diff()`, `copy()` and `discard()` share the existing
parameter editor implementation.

`session.use(parameter_branch=...)` and `session.params` also work before any setup
is installed or selected. Selection checks that the referenced parameters, subject
and batch exist; it does not assert that the equipment can run them. You can save
unknown values and incomplete calibration tables. Setup compatibility, required
values and supported execution topology are checked when preparing an experiment.

After selecting an executable setup, combine the editor with a measurement context:

```python
prepared = session.prepare(
    experiment(), parameters=params, sample="chip-a", batch="cooldown-1"
)
```

These arguments apply to this preparation only; they do not replace the session's
subject or parameter branch. `target=...` selects a registered target instead of a
sample. Omit subject/batch arguments to inherit session choices. Passing
`sample=None` explicitly prepares without a sample. An editor cannot be combined
with another configuration choice (`selection` or `context`) or separate overrides.

Ordinary `params.save()` generates a revision ID and advances the current branch.
An unchanged ordinary save returns the existing version. Saving never changes
the lab default. `params.save("chip-a/trial")` forks and selects a new branch,
leaving the original branch unchanged. Inspect history with
`lab.parameters.history("chip-a/daily")`; read an exact version with
`lab.parameters.get(version.id)`.

Checkout captures an editing base. If another writer advances the branch,
saving reports a conflict without losing the local draft or saving an orphan
revision. Call `params.rebase()` to merge non-conflicting cells from the latest
head. Conflicting cells leave the entire draft unchanged. A changed catalog or
pending structure edits require explicit review rather than automatic schema
merging. Re-selecting the branch through `session.use(...)` starts a fresh checkout;
it does not merge the old editor's unsaved values.

An unchanged retry after a lost response reuses the pending save command.
Independent copies retain their editing base and do not move the original
session's selection when saved.

## Use a notebook's saved parameters in the workbench

On the experiment form, open **Choose parameter branch** in **Measurement context**.
Choose a branch, review its generation, author, note and version, then click
**Use this parameter version**. The form retains its subject, batch and operator;
any previous preview is invalidated. Preview again before acquisition.

The page holds that exact saved revision. Saving the branch in a notebook or
clicking **Refresh parameter branches** only updates the choices, not the selected
inputs or an existing preview. To adopt a newer head, explicitly use its version.
Branches are paginated with **Load more parameter branches**. A failed refresh
leaves existing inputs intact and blocks adopting a choice until a successful retry.
**Use lab parameter default** explicitly returns to the shared parameter default.

This chooser reads saved values; unsaved notebook edits remain local. Create,
edit, fork and rebase branches through the editor above. The chooser does not
activate a setup or establish calibration validity.

Python callers can browse the same heads without reading every revision:

```python
page = lab.parameters.branches(limit=100)
for branch in page.items:
    print(branch.name, branch.generation, branch.revision.revision_id)
if page.next_cursor is not None:
    page = lab.parameters.branches(after=page.next_cursor)
```

Pages are ordered by branch name and contain only the current head per branch.
They are a live catalog view; selecting a returned revision pins that version.

## Prepare without saving value edits

`session.prepare(request, parameters=params)` captures unsaved **value** changes as
run-only overrides. Previewing does not write a revision or advance a branch.
Later edits to `params` do not change an existing preview. Runs and saved plans
retain the exact base parameter/setup references and overrides.

Save **structure** changes before preparing an experiment, as with the earlier
editor. When `parameters=params` is omitted, session preparation uses the selected
saved revision; it does not silently include an editor's unsaved buffer.

## Publish a verified candidate

To combine candidates fitted from the same saved parameter revision:

```python
joint = q0_candidate.combine(q1_candidate, name="joint-drive-calibration")
prepared = session.prepare(experiment(), candidate=joint)
```

This saves a new proposal with exact contributing run, analysis, proposal and
content identities. The server reads retained proposals; local edits to their
Python objects are not authoritative. Sources must share the exact saved
parameter revision, setup and scientific subject/scenario, with no unsaved
overrides. Conflicting cells reject the entire composition without publication.
Supply all original candidates in one call; nested compositions are not supported.

The returned candidate has **no inherited acceptance**. Collect new measurements
using the joint candidate, then run a policy appropriate to the combined experiment.
`joint.verify(check_result)` retains every contributing baseline along with the
joint candidate data. A prior individual verification cannot publish the joint
candidate. The framework checks evidence identity and scope; the laboratory's
policy must decide whether its measurements cover the physical interactions that
matter. Composition does not schedule measurements or advance a branch.

After collecting independent candidate data and obtaining a retained positive
policy decision, publish explicitly to a captured branch head:

```python
destination = lab.parameters.checkout("chip-a/daily").head
verified = candidate.verify(check_result)
receipt = verified.publish_to_branch(
    destination,
    name="chip-a-readout-verified-1",
    note="Independent readout verification passed",
)
# Explicitly adopt the published head for future preparation.
session.use(parameter_branch="chip-a/daily")
```

`name` identifies the immutable result revision. Repeat the same destination,
name and note to recover the same receipt after a lost response, even if later
commits have advanced the branch. Different publication requests against an old
head fail without creating a revision. Re-checking out an advanced head does not
authorize reusing an old candidate: the baseline run must use that exact parameter
revision without unsaved overrides. Collect new evidence after changing the base.

The server checks retained proposal and decision records, an independent successful
candidate run and matching sample/target, execution scenario and equipment setup.
It atomically stores the result and advances only the chosen branch. Prepared
runs, other branches, the lab default and setup remain unchanged. The receipt's
`publication` records the source run, proposal and verification decision;
`previous` identifies the exact base parameter revision.

Copying values into `params` and saving remains a manual edit, not calibration
acceptance. Ordinary saves have no `publication` claim; history retains earlier
receipts. Publishing verified cells does not assert that every parameter on the
branch is calibrated or applicable to another sample. No automatic merge of
verified candidates or cohort publication is provided by this operation.

The older `publish_to(working_point=...)` and `publish_default()` still serve
legacy consumers; new branch workflows use `publish_to_branch()`.

## Publish inside a durable procedure

A registered procedure can use the same server validation and atomic branch
publication without activating a full configuration:

```python
published = ctx.publish_parameter_candidate(
    "publish-calibration",
    candidate_ref,
    proposal_id="joint-drive-calibration",
    verification=verification_ref,
    decision_output_id="decision",
    branch=intent.destination,
    name=intent.result_revision_id,
    actor="calibration-worker",
)
```

Here `candidate_ref` and `verification_ref` are exact retained analysis outputs
from earlier steps. The procedure's typed intent must capture the destination
`ParameterBranch` and result revision name at submission. Do not re-checkout a
moving branch while replaying the procedure. Joint candidates still require a
retained joint verification decision; this step does not imply target completeness.

The step stores the entire accepted branch receipt. A completed step replays that
receipt without reopening evidence or resolving today's head. If publication
commits but its step result is not saved, repeating the same command recovers the
historical publication. An unresolved transport failure requires attention;
retry with the same intent, rather than choosing a newer destination. Changing
the destination or evidence changes step identity. Analysis-only recovery into a
different procedure excludes any attempted parameter publication, just as it
excludes old configuration acceptance.

This durable output uses development schema 88. Existing development stores are
left untouched; use a fresh store rather than rewriting an older one.

For target selection, durable composition, joint verification and worker setup,
see [Automate parameter calibration](automate-parameter-calibration.md).

## Scientific context remains separate

Session branch selection and explicit branch-editor preparation preserve the
subject, batch, operator and record collection. Selecting another sample/target
drops the checkout unless a branch is also explicitly selected. Choosing a saved
parameter revision or working point exits branch mode. Branch names currently
carry no enforced sample/cooldown applicability or calibration acceptance.

The low-level `checkout(...).save(catalog=..., parameters=...)` remains available
for programmatic full-snapshot producers; ordinary authors use `params.save()`.
The [legacy combined configuration API](manage-configuration.md) remains for its
maintained working-point consumers. New author workflows use independent branches
without fabricating samples or working points.

Automatic default-branch selection and graphical branch editing remain follow-up work.
No prebaseline data migration or historical-file rewriting is introduced.
