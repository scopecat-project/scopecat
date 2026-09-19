# Register exact measurement targets

A measurement target records which sample revisions participate and how their
member-qualified entities connect. You can register and inspect single-sample or
assembly targets through Python. **Registered targets are not yet selectable for
execution**: the current launch, working-point and calibration paths still use
single-sample contracts. Registration does not claim an assembly is executable or
its wiring is physically verified.

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
