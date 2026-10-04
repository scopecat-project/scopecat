# Frozen target selection and admission

The maintained single-member execution contract originates in
[#626](https://github.com/scopecat-project/scopecat/issues/626). Direct runs,
authored/session selection, saved plans and the workbench use the same frozen
scientific binding. Configuration editing and admission fences follow
[configuration ownership](configuration-ownership.md); remaining multi-member and
apparatus work belongs to [#612](https://github.com/scopecat-project/scopecat/issues/612).
No supported persistent-data baseline is designated; the
[prebaseline data policy](../data-compatibility.md) applies.

## Subject and setup binding

`RegisteredTargetSubject` retains the exact target reference, definition and sample
evidence. It no longer owns control addresses. `TargetSetupBinding` separately
relates that target to an executable setup hash, with explicit entity and connection
maps. Both the pure topology checker and retained execution evidence use the same
mapping records. Changing a map does not change the scientific subject.

`ResolvedScientificBinding.target_binding` carries this relationship through run,
plan and procedure admission. The authority reconstructs it from retained evidence;
missing or altered mappings are rejected. Procedure children and candidate
verification preserve the relationship alongside the subject and setup.

`MeasurementContext.target_binding` also retains the relationship. Applicability
compares it independently and reports `target_binding_changed`; moving it out of
the subject must not permit reuse under a different mapping. Indexed history uses
the complete context. This is scientific applicability, separate from target identity.

Schema 94 and scientific-binding codec v3 replace the development representation.
Use a fresh development store; historical directories remain untouched. Registered
execution still supports one member with an identity mapping. The general explicit
mapping checker alone does not enable multi-member execution.

The common `records/measurement_context.py` model now serves retained run projection
and calibration. `lab.resolve_context(...)` and the read-only
`POST /api/v1/measurement-context/resolve` endpoint capture branch/setup choices;
resolution receipts remain outside scientific identity. That ownership move did
not alter saved context payloads. Development schema 94 subsequently removes
`primary_entity_id` from setup/configuration, with setup revision/content codecs v3.
Setup describes control resources; subject selection belongs to each session/page.

Further convergence should unify scientific entity addresses and author selection resolution,
then separate setup resource definitions from execution environment and target
binding. Capability prerequisite policy remains distinct from task execution order.

## Current selection consumers

| Owner | Current responsibility |
| --- | --- |
| `records/scientific_selection.py` | One subject/configuration/batch selection and a checked binding/source envelope |
| `application/session_context.py`, `AuthorProject.use/prepare` | Session-local choices and atomic selection updates; preparation freezes exact inputs |
| `application/launch_config.py::resolve_launch_config` | Resolve independent parameters/setup or a retained candidate together with subject evidence |
| `api/_runner.py`, server `services/scientific_binding.py` | Produce and independently reconstruct exact scientific evidence |
| server `services/admission.py` | Validate retained inputs and check device heads in the resource-reservation transaction |
| `records/experiment_plan.py`, server plan/procedure services | Retain binding and source identity through saved recipes, replay and child submission |

`TargetCatalogStore.resolve` already rejects a foreign catalog, checks exact revision
and content hash, and reads immutable content. Use it, not `get(target_id)` followed
by whatever head is current. No target registration belongs inside `prepare()`.

## One selection model, followed by one resolved model

`records/scientific_selection.py` defines the discriminated choices below;
`ReviewedScientificSelection` retains the resolved binding and source.

- `SubjectChoice`: `unbound`, `sample(sample_id, revision selector)` or
  `registered_target(TargetRevisionRef)`. The sample choice remains a concise
  authoring input for existing experiments, within the same model; it does not
  manufacture a registered target ID. No subject is legitimate for device-only
  work and carries no sample/target evidence.
- `ConfigurationChoice`: `unselected`, `parameters(exact ref, setup, overrides)`
  or `candidate(exact proposal source)`. A parameter draft may omit setup while
  editing, but preview requires it. A candidate retains its own exact setup and
  cannot also carry independent overrides. `LaunchConfigSource` is the resolved
  parameter or candidate source; no choice resolves a global registry default.
- `ScientificSelection`: subject choice, configuration choice and explicit
  `BatchScope`. Session omission means inherit; explicit clearing produces
  `UnscopedBatch`, not an applicability wildcard. Collection and operator remain
  independent execution/attribution selections.
- `ResolvedScientificBinding`: codec version, exact resolved subject, explicit
  batch, resolved configuration provenance/hash, setup-content fingerprint and
  the deterministic runtime projection. It has no unresolved sample head, target
  head or active-config selector. Exact parameter/setup references and current
  device heads provide admission fences; there is no global config generation.

A resolved subject is one of `unbound`, `inline_sample` or `registered_target`.
The latter retains the qualified exact target ref and its immutable content;
`inline_sample` retains the exact local sample binding and owning catalog, but
makes no claim that it was registered. These variants distinguish actual evidence
rather than maintain old and new write APIs. New submissions all use one binding
codec. A display adapter may render both as a single-chip target.

Selecting a target ID in a menu or Python convenience call resolves its current
head into a `TargetRevisionRef` when the selection is accepted. Later refresh of
code does not advance that target reference. Choosing the latest target revision
is an explicit selection change; failed selection updates leave prior defaults
intact. Preparing resolves the selected scientific inputs once and returns a
binding. Submitting that preparation never rereads a target head.

The source-qualified envelope supplied by workspace publication owns code revision,
workspace/environment identity and source freshness. The scientific binding owns
measurement conditions. Both contribute to the final prepared/submission identity;
neither resolver is allowed to replace the other's selection.

## First executable target: explicit single-member projection

Eligibility is deliberately narrower than catalog registration:

1. Require exactly one member and no declared target-level connections. Even a
   connection between two entities of one member cannot be silently discarded;
   connection composition needs the later assembly path.
2. Resolve the member's exact sample revision/hash in the owning catalog. Validate
   the target ref's catalog first, including direct low-level submission.
3. Freeze a mapping from the member ID to run role `subject`. Member `A` is not
   renamed to `subject` in target content. Existing candidate launch explicitly
   requires one `subject`; context sources also carry a role. Using member `A`
   directly as the run role would break both consumers.
4. Freeze the entity-address projection `TargetEntity(A, q0) -> runtime q0`, with
   the physical sample identity retained as the current `SampleBinding.entity_scope`.
   This passthrough is valid only for one member. It does not solve two chips both
   exposing `q0`, multi-member parameter keys or joint analysis identity.
5. Require a declared sample topology and a compatible execution topology. For the
   first implementation use exact entity `(kind, id)` and connection content
   agreement, ignoring descriptive entity metadata and declaration ordering.
   Do not merge sample topology into accepted setup automatically. A missing or
   different topology reports a preparation incompatibility; subset/composition
   policies are a later explicit extension.
6. Derive the exact `subject` binding with the selected batch. Independent parameter
   values do not manufacture scientific acceptance. A candidate retains its source
   sample, batch and setup; compare that evidence before planning, without relabeling
   its frozen source to fit a new selection.

Once a candidate's source run has registered-target evidence, preserve and check
that exact source binding as well. Selecting a new target is not permission to
relabel a saved candidate; a scope change requires an explicit estimate copy.

These checks belong in a pure projection function consumed by both preparation
and server admission. The server must still resolve retained catalog content;
a client-supplied matching hash is not permission to bypass ownership or content
checks. Registration alone never certifies configuration or calibration validity.

## Frozen evidence and transaction boundary

Use a versioned, immutable scientific-binding object associated with the admitted
run. Current snapshot/request/config records may remain the execution projection;
their sample fields are a deterministic projection of the binding, not a second caller-controlled authority. A new live submission
must supply the binding, and the server rejects disagreement with those fields.

Store its content-addressed object through the existing run repository and record
its association alongside the admission, collection address and sample indexes in
one write transaction. Reserve a dedicated repository reference and, if indexed
lookup is required, an additive association table in a separately coordinated
format change. Coordinate its schema identifier with other in-flight storage
changes; it does not designate a supported baseline. Retain the exact target
revision/content and projection, so inspection does not depend on a mutable target head or executing old code.

Admission order:

1. Match an already-admitted submission ID against its stored intent codec/hash and
   replay its retained result before evaluating current fences.
2. For a new request, validate the binding codec, catalog, immutable target/sample
   refs, config source, deterministic projection and setup fingerprint. Preserve
   authoritative inventory/domain-target checks and the transaction-local device-head fence.
3. Stage immutable binding/config/request/snapshot objects. Publish their repository
   refs, indexes and address together with admission. Failed validation allocates
   no visible run/address; transaction rollback publishes no partial binding.
4. Return a read view exposing the retained binding. Do not infer a target binding
   for evidence that did not record one. Unsupported prebaseline records need no
   new-format read adapter.

Target head advancement after preparation is allowed: the old immutable revision
is still the reviewed target. A changed device head requires setup re-resolution
for new work. An exact retry after later head changes returns its original run;
the same retry ID with another target ref/content is a content conflict. A renamed
revision may have the same target-content hash, but its exact reference differs
and cannot replace the reviewed reference under the same retry key.

## Plans and procedure children in the current contract

Selection/preparation/submission and recipe content need explicit format identities
for the new design. Current producers and consumers must agree; this does not
require preserving prebaseline codecs, missing-field defaults or legacy recipe
adapters. Changing the development format may reject earlier stores while leaving
their files intact.

- New saved recipes retain the resolved subject/config scientific binding. Reopening
  does not select the latest target or inherit a conflicting session target/batch.
- The parent procedure's admitted step intent includes the binding. Its child must
  submit that same evidence and hash; `_require_plan_child` currently checks this
  hash, so changing only direct-run submission is insufficient.
- Within the current format, exact retries retain their original scientific intent,
  source/config hashes, addresses and refs. Edits create new artifacts rather than
  modifying a retained recipe.
- A future supported baseline will define which earlier formats can be read,
  resumed or upgraded. Before it exists, no old-plan conversion, legacy-step replay
  or old-submission reader is a prerequisite for this implementation.

Historical files stay intact for owners' archival arrangements. Do not silently
reinterpret old scientific content, and do not retain replaced live request fields
or duplicate session resolvers solely for development-format compatibility.

## Setup and calibration ownership

Setup definitions and exact resolutions are maintained independently from parameter
branches. The complete executable setup hash constrains scientific admission;
matching it is not proof that hardware is unchanged or calibration remains valid.
[Configuration ownership](configuration-ownership.md) defines device-head fencing,
branch publication and retained snapshots. The former working-point editor,
global activation and intermediate parameter binding are retired.

Calibration applicability compares retained scientific conditions and declared
dependencies, separately from executable admission. Publication requires the exact
destination branch head, candidate provenance and verification evidence. Copying
values does not create a fresh success or target-qualified evidence. The remaining
scope in [#783](https://github.com/scopecat-project/scopecat/issues/783) concerns finer
dependency coverage and external inputs; do not rebuild the delivered whole-parameter
comparison or bounded check/repair pipeline. Multi-member execution and apparatus
subjects remain separate requirements in #612.

## Focused acceptance for maintained target execution

| Scenario | Required observation |
|---|---|
| Select target revision 1; publish revision 2; submit prepared work | Run retains revision 1, its members and mapping; latest head is not substituted |
| Same target ID/hash in another catalog | Preparation and direct admission reject before address allocation |
| Single member named A, entity q0 | Run role remains subject; frozen evidence maps A/q0 to q0 and retains physical sample identity |
| Multiple members, or any target-level connection | Explicit unsupported-target error; registration remains available; no silent first-member projection |
| Missing/different sample topology or mismatched config | Preparation fails without mutating the selection, parameter branch or setup |
| Change session target, batch, code or collection after preparation | Old preparation remains frozen; new preparation uses new choices; another session is untouched |
| Exact retry after target/device head changes | Original run, address and binding returned; changed target under the same key conflicts |
| Fail binding/index commit | No admitted run/address with a missing or mismatched scientific binding |
| Candidate from another sample/batch | Direct and authored paths reject; an explicit estimate copy remains distinct from evidence reuse |
| Target-bearing saved recipe produces a procedure child | Parent and child binding/hash agree; session defaults cannot override it |
| Current-format recovery and unsupported formats | Backup/restore preserves current binding/ref/address; unsupported formats are rejected without rewriting files or inferring target/setup |
| Label-only target revision; changed scientific target content | Old provenance stays exact; applicability distinguishes metadata from scientific changes |

These boundaries are exercised through the maintained producers and consumers.
Keep them as short synthetic contract/admission journeys during the fast-CI window; installed GUI/Windows and
physical-device qualification remain separate finishing gates.


## Implemented domain foundation

### Explicit multi-member topology checks

`project_target()` now validates an explicit mapping from member-local entities
and connections to a complete execution topology. It is a pure infrastructure
function: it returns a checked `TargetProjection`, not a scientific run binding
or permission to submit an assembly. The existing single-member projection uses
the same checker with identity maps and retains its previous execution boundary.

For a retained target with members `A` and `B`, each containing `q0`, `q1` and
an `edge`, and a declared `bus` connecting `A/q1` to `B/q0`:

```python
from scopecat.config.target_projection import project_target

projection = project_target(
    target_revision,
    catalog_id=owning_catalog_id,
    samples=retained_sample_revisions,
    execution_topology=setup_topology,
    entities={
        "A": {"q0": "left0", "q1": "left1"},
        "B": {"q0": "right0", "q1": "right1"},
    },
    connections={"A": {"edge": "left-edge"}, "B": {"edge": "right-edge"}},
    interconnections={"bus": "bus-edge"},
)
```

The caller supplies immutable sample revisions keyed by `(sample_id, revision)`.
Maps must cover every member entity, member connection and target interconnection
exactly once; runtime IDs must not collide, and no extra runtime entities or edges
are accepted. The checker preserves entity kinds, connection kinds, undirected
endpoints and member-connection `entity_id` through the mapping. Display metadata
and input ordering do not affect topology agreement. Target interconnections do
not declare a coupler entity, so the checker cannot invent an `entity_id` for them.

This makes the naming and topology checks reusable without inferring laboratory
routes or hardware ownership. Remaining execution work includes multi-member
scientific bindings, admission and replay consumers, explicit setup ownership of
the mapping, parameter-address alignment and resource/compiler qualification.
The capability context resolver still rejects registered multi-member/connected
targets until those contracts can be retained and validated end to end.

`config/target_projection.py` now owns pure sample-hash and connection-entity
validation, consumed by the existing target catalog's create/revise operations.
Its internal single-member projection preserves the exact qualified target ref,
keeps member IDs separate from run role `subject`, and compares topology without
description/order sensitivity. Contract tests cover foreign catalogs, composite
targets, absent/different topology and label-only target revisions. Run binding
and admission consume this projection. Public authored/session selection, saved
experiment plans and target-qualified capability reports support the same
single-member binding. Multi-member execution remains pending; catalog registration
and a successful topology check alone do not enable it.


## Run binding and admission

Every live run submission carries a versioned `ResolvedScientificBinding`. It
records an explicit unbound, catalog-qualified inline-sample, or registered-target
subject together with the accepted configuration and setup-content hashes. The
existing frozen submission envelope retains configuration provenance; the binding
does not introduce a second independently editable config source. Inline samples
preserve existing multiple-role records without claiming assembly execution.

The direct runner resolves sample revisions before submission and uses exact
selectors as its runtime projection. Resume reuses retained evidence. Procedure
admission also freezes inherited sample selectors in `resolved_samples`, while
retaining the original selection for request-key identity. Reentering a child
step therefore does not advance its inherited sample head; explicit per-step
selection and target-bearing recipes use the authored contract below. Admission
checks that evidence against local immutable catalog records, submitted config,
setup and selectors before allocating a visible run or acquisition address. A
dedicated repository reference is committed in the existing admission transaction;
current-format recovery retains the same object and identity. This adds no
migration or earlier-format reader.

The low-level registered-target variant permits only the reviewed single-member
projection. Target head movement does not replace an exact reference; foreign
catalogs, incompatible topology and altered mappings are rejected. This boundary
applies to both low-level and authored consumers; catalog registration alone does
not enable multi-member execution.


## Authored selection and saved plans

The public launch request now has one `ScientificSelection` and one checked
`ReviewedScientificSelection` envelope. Preview resolves both configuration and
subject together. Clients adopt the returned envelope before hashing, submitting
or saving; the checked fence covers exact binding and configuration provenance.
The previous flat launch sample/context/config-source fields have been removed.

Notebook sessions accept a registered single-member target, resolving a target ID
to an exact reference when selected. Refresh leaves it fixed. Saved plans contain
that exact binding and reopen independently of the session's current scientific
scope. The workbench consumes the same contract and preserves target-bearing
plans. The workbench target picker (#643) now selects exact catalog-qualified
revisions, resolves reopened references independently from the current head list,
and retains the selection through author refresh and page navigation. Batch,
record destination and operator remain separate; exact parameter/setup inputs or
candidate compatibility are checked by the same preview resolver. List refresh
never advances a selection.

Authored procedures carry the binding as a typed parent field and each durable
child is checked against it and its claimed step intent. Generic multi-stage
procedures may omit a fixed full binding because their configurations can change;
their children still pass normal scientific admission and step checks. Maintained
multi-stage reference workflows currently support inline sample selection.

Current-format recovery preserves these exact selections and bindings. Assembly
execution remains separate work; neither old development formats nor retired
configuration editing APIs are required for it.
