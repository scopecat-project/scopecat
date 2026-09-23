# Register exact measurement targets

A measurement target records which sample revisions participate and how their
member-qualified entities connect. You can register and inspect single-sample or
assembly targets through Python. Notebook sessions can select an exact single-member
target for authored experiments, previews and saved plans. The workbench can select
the same exact targets. Assembly execution remains pending. Registration alone does not claim that
wiring is physically verified.

Use an existing connected `LabClient` and registered sample. A member ID such as
`A` is local to this target; `A/q0` and `B/q0` remain distinct.

```python
from scopecat.records.scientific_scope import MeasurementTarget, TargetMember
from scopecat.records.target_catalog import (
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevisionDraft,
)

sample = lab.samples.revision("chip-a", 1)
member = TargetMember(
    id="A",
    sample_id=sample.sample_id,
    revision=sample.revision,
    content_hash=sample.content_hash,
)
registered = lab.create_target(
    TargetCreateCommand(
        catalog_id=lab.health().project_id,
        target_id="chip-a-measurement",
        draft=TargetRevisionDraft(
            name="Chip A",
            content=MeasurementTarget(members=(member,)),
            actor="Li",
        ),
    )
)
exact = registered.ref
assert lab.resolve_target(exact) == registered
```

The catalog ID is the existing persistent data-space identity, currently exposed
as `health().project_id`. It survives restart, code-directory moves and supported
backup/restore. A reference from another catalog is rejected even when its local
target ID happens to match. These are local-catalog members; copying foreign sample
IDs is not an import or identity-mapping operation.

`lab.target("chip-a-measurement")` reads the latest revision.
`lab.target("chip-a-measurement", revision=1)` reads a retained local revision;
`lab.resolve_target(exact)` additionally checks catalog ownership and content hash.
`lab.targets(limit=100, before=cursor)` lists latest revisions with pagination.

To revise a target, submit the exact revision you reviewed. A stale reference
conflicts; reload and review instead of overwriting another editor's change.

```python
renamed = lab.revise_target(
    TargetReviseCommand(
        expected=registered.ref,
        draft=TargetRevisionDraft(
            name="Chip A / mounted",
            content=registered.content,
            actor="Li",
            note="Clarify the display label",
        ),
    )
)
assert renamed.ref.revision == 2
assert renamed.ref.content_hash == registered.ref.content_hash
assert lab.resolve_target(exact).name == "Chip A"
```

Every revision is immutable, including its label and audit metadata. The content
hash covers scientific members/connections, so changing only a label does not
change it. The revision number still changes and remains part of the exact reference.
Create and revise use explicit conflict semantics: after an uncertain network result,
read the target before resubmitting; a stale revision is not silently retried.

For an assembly, add another `TargetMember` and a `TargetConnection` whose endpoints
are `TargetEntity(member_id="A", entity_id="q0")` and, for example,
`TargetEntity(member_id="B", entity_id="q0")`. The service validates every sample
revision/hash and each referenced entity against its retained sample topology in
the same transaction as registration. Failed validation writes no target or revision.
No execution, calibration publication or device acquisition follows registration.

The catalog is part of the current development format.
[Current-format recovery copies](backup-and-restore.md) retain catalog identity;
independent writable clones do not gain a supported merge policy. New builds do
not promise to read or migrate prebaseline target catalogs; see the
[data policy](../development/data-compatibility.md).

## Use a target in a notebook

```python
session.use(target=exact)
prepared = session.prepare(rabi())
plan = prepared.save_plan("Target check", saved_by="Li")
run = prepared.run().wait().result()
assert run.snapshot.scientific_binding == prepared.preview.reviewed.binding
```

`rabi` is an authored experiment in the connected workspace. A target ID string
is also accepted by `use(target="chip-a-measurement")`; the client resolves it to
an exact revision at selection time. Source refresh does not advance that target.
Select it again explicitly to use a newer revision. A compatible saved working
point can be supplied alongside `target`.

Preview freezes the target reference, retained content, entity projection, sample
revision, batch and exact configuration evidence. Submit and saved plans retain
that binding. Reopening a plan does not substitute the notebook's current target.
Multi-stage maintained reference workflows that change configurations still require
a sample selection.

## Select a target in the workbench

In the experiment page, open **Browse samples, batches and collections** in
**Measurement context · this page**. The **Registered target** list shows the
latest catalog revisions. Select a single-member target and review its name,
revision, owning catalog and exact sample member. Targets with multiple members
or explicit connections are listed as unsupported for execution.

Selection pins the displayed revision. **Refresh target list**, author source
refresh and switching experiments do not advance it. To adopt a later revision,
select that revision explicitly and preview again. Reopening a saved plan keeps
its original target, even when the catalog head has changed. A foreign-catalog
reference is reported as an error; it is not converted to an ordinary sample.

A compatible working point selected from Configuration can be combined with the
target; preview checks their exact sample and batch agreement. Without a working
point, choose the experimental batch independently. The record collection and
operator remain separate choices. Changes to the scientific selection invalidate
the old preview before acquisition.

This draft remains in the open workbench when navigating between pages. Changing
the connected catalog clears it. It does not change another notebook's selection
or any already admitted measurement.

Adapter developers can validate explicit member-to-runtime topology maps with
`scopecat.config.target_projection.project_target()`. This catches collisions
such as two samples both declaring `q0` and checks intra-sample and inter-sample
connections against the supplied setup topology. See the
[projection contract](../development/architecture/target-execution.md#explicit-multi-member-topology-checks).
This check does not enable multi-member execution or change the workbench's
single-member admission boundary.
