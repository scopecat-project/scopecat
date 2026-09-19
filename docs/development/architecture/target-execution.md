# Frozen target selection and admission

Status: concrete implementation contract for [#626](https://github.com/scopecat-project/scopecat/issues/626),
audited at `3ddaeb27b` after target catalog PR #625. This is a design, not shipped
execution support. It refines [experiment contexts](experiment-contexts.md).
This design follows the [prebaseline data policy](../data-compatibility.md).
Current format 75 is not a compatibility baseline; no old-format reader or
migration obligation is introduced here. Coordinate shared source-side files
with workspace publication.

## What must change in the existing path

| Current seam | Observed behavior | Required replacement |
|---|---|---|
| `application/session_context.py`, `AuthorProject.use/prepare` | A sample name and separate working point/batch determine future preparation | One subject choice plus configuration choice; validate an atomic selection update |
| `records/launch_request.py` | `sample`, `sample_binding`, `context`, `configuration`, `config_source` and `batch_id` are parallel fields; request hashing has conditional branches | Separate selection input from the resolved scientific binding; remove replaced fields from the new active input model |
| `application/launch_config.py` | Working point/candidate/registry branches resolve config, then `launch_sample_selection` separately reconstructs sample intent | One resolver produces configuration and subject evidence together |
| `api/_runner.py::_plan` | A context source injects its exact sample selector; other selectors may still resolve a head later | Consume the resolved subject projection and reject conflicting caller-supplied samples |
| `daemon/wire.py::RunSubmission.intent_content_hash` | Hashes submitted config/request/plan; procedure-child identity is deliberately excluded | Include the complete new scientific binding in the new intent codec; retain existing child/parent consistency rules |
| server `services/admission.py` | Replays first, resolves samples, stages immutable objects, then commits admission/address/sample indexes together | Independently validate exact target evidence and commit its durable association in the same transaction |
| `records/experiment_plan.py`, server `services/experiment_plans.py` | Recipes retain one exact `SampleBinding` and config/context; child step checks the submission hash | Version new recipe definitions and freeze scientific binding through parent and child |
| `automation/calibrations.py` | Target has sample/context/batch but no exact sample revision or setup reference | Keep it explicitly outside new target-qualified evidence until the applicability redesign below |

`TargetCatalogStore.resolve` already rejects a foreign catalog, checks exact revision
and content hash, and reads immutable content. Use it, not `get(target_id)` followed
by whatever head is current. No target registration belongs inside `prepare()`.

## One selection model, followed by one resolved model

Use discriminated variants, not another optional `target_id` beside `sample`.
The names below specify responsibilities; final Python spelling can follow the
coordinated source-envelope implementation.

- `SubjectChoice`: `unbound`, `sample(sample_id, revision selector)` or
  `registered_target(TargetRevisionRef)`. The sample choice remains a concise
  authoring input for existing experiments, within the same model; it does not
  manufacture a registered target ID. No subject is legitimate for device-only
  work and carries no sample/target evidence.
- `ConfigurationChoice`: `registry(selection)`, `working_point(exact ref,
  overrides)` or `candidate(exact proposal source)`. A candidate cannot also carry
  independent context overrides. The existing resolved `LaunchConfigSource`
  variants remain usable as lower-level provenance during this slice.
- `ScientificSelection`: subject choice, configuration choice and explicit
  `BatchScope`. Session omission means inherit; explicit clearing produces
  `UnscopedBatch`, not an applicability wildcard. Collection and operator remain
  independent execution/attribution selections.
- `ResolvedScientificBinding`: codec version, exact resolved subject, explicit
  batch, resolved configuration provenance/hash, setup-content fingerprint and
  the deterministic runtime projection. It has no unresolved sample head, target
  head or active-config selector. The reviewed config generation remains an
  admission fence, not scientific identity.

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
intact. Preparing resolves remaining sample/config defaults once and returns a
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
6. Derive the exact `subject` binding with the selected batch. When using an existing
   working point or candidate, compare exact sample revision/hash, working-point
   identity and batch before planning; do not relabel its frozen source to fit.

Existing working points use `single_sample_applicability`, whose synthetic member
ID is the run role `subject`, whereas a registered member can be `A`. Do not compare
those two target-content hashes directly or rename catalog members to make them
match. For an existing single-sample parameter source, compare the explicit runtime
projection plus sample/batch/setup conditions; this permits using its values for
execution without retroactively asserting target-qualified calibration evidence.
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
   current authoritative inventory/domain-target and generation checks.
3. Stage immutable binding/config/request/snapshot objects. Publish their repository
   refs, indexes and address together with admission. Failed validation allocates
   no visible run/address; transaction rollback publishes no partial binding.
4. Return a read view exposing the retained binding. Do not infer a target binding
   for evidence that did not record one. Unsupported prebaseline records need no
   new-format read adapter.

Target head advancement after preparation is allowed: the old immutable revision
is still the reviewed target. Apparatus/config generation conflicts retain current
behavior. An exact retry after later head/config changes returns its original run;
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

## Setup and calibration: minimum non-optional boundaries

`setup_content_hash` currently compares execution structure conservatively for
parameter rebase. It excludes parameter definitions/values and display metadata;
that is useful identity input, not a maintained apparatus revision or live physical
state. Target execution may use that explicit fingerprint with current inventory
fencing, but must not label it calibrated setup evidence.

The following cannot be omitted from the next setup/calibration implementation:

| Boundary | Minimum contract |
|---|---|
| Setup ownership | A catalog-qualified immutable setup revision separates topology/routing/driver/connection/lifecycle content from parameter state; retain one complete resolved execution snapshot |
| Physical exclusion | Logical rename or another workspace must not acquire an already owned physical access domain; existing fencing and unknown-effect quarantine survive resolver changes |
| Working-point applicability | Exact target scientific content, batch and setup scope are checked before value composition/publication; copying values records estimates, never fresh success |
| Calibration identity | Use resolved target/member-qualified entity and applicability in new keys/freshness; store the exact target ref as provenance but exclude target label-only changes from scientific validity |
| Dependencies | Initially require matching declared scope. Single-chip evidence does not automatically satisfy joint calibration; unscoped/foreign setup evidence is not a wildcard |
| Publication | Recheck expected working-point head and applicability when publishing, and record input/result scope. A stale publication must not become valid by changing the selected target |
| Evidence boundary | No success may be assigned target/setup evidence it did not record; prebaseline keys/codecs need no new reader |

For registered targets, the new calibration key includes owning catalog, stable
target ID and member-qualified entity identity. Freshness includes target-content
hash, declared batch, setup scientific content and exact inputs/dependencies. The
full target revision ref remains provenance. Thus a label-only revision does not
expire evidence, while two different target IDs do not accidentally pool successes
just because their current contents match. Inline-sample identity is a separate
explicit key variant, not an inferred registered target.

`calibration_freshness_fingerprint` already hashes definition, target, procedure,
inputs and dependency evidence; changing its target model in place would change
the format contract. Replace current writers/readers together and give the new
format an explicit identity; retaining prebaseline validators is not required.
Do not extend `CalibrationTargetRef` with independent optional target/setup fields and
assume batch equality completes applicability.

## Delivery and ownership

1. **Pure domain work can proceed now:** extract existing registration member/hash/
   entity validation into a pure function accepting retained sample revisions and
   use it from `TargetCatalogStore`; add the deterministic single-member projection
   and topology compatibility checks with contract fixtures. The projection becomes
   an execution feature only when the next consumer lands. Avoid an unused parallel
   registry or a public "ready to execute" claim.
2. **After source publication contracts land:** one owner replaces scientific live
   selection fields, integrates the resolver and new prepared/submission identity,
   including the direct Python runner. Shared files are `author_project.py`,
   `launch_request.py`, `daemon/client.py`, HTTP transport and generated UI contracts.
3. **Same coordinated execution feature:** admission, repository, current-format
   storage, saved recipe codec and procedure-child propagation land together or behind an internal
   non-user-visible staging boundary. Do not ship a target-enabled preview that
   drops the target at admission. UI selection follows the same resolver.
4. **Next feature:** maintained setup and target-qualified working-point/calibration
   publication. Pure scope comparison tests can proceed independently; changing
   durable calibration keys before new exact inputs exist cannot.

This design does not designate a compatibility baseline, execute a target, or relax runtime binding, source qualification or resource authority.

## Focused acceptance before claiming target execution

| Scenario | Required observation |
|---|---|
| Select target revision 1; publish revision 2; submit prepared work | Run retains revision 1, its members and mapping; latest head is not substituted |
| Same target ID/hash in another catalog | Preparation and direct admission reject before address allocation |
| Single member named A, entity q0 | Run role remains subject; frozen evidence maps A/q0 to q0 and retains physical sample identity |
| Multiple members, or any target-level connection | Explicit unsupported-target error; registration remains available; no silent first-member projection |
| Missing/different sample topology or mismatched config | Preparation fails without mutating selection, working point or shared active configuration |
| Change session target, batch, code or collection after preparation | Old preparation remains frozen; new preparation uses new choices; another session is untouched |
| Exact retry after target/config head changes | Original run, address and binding returned; changed target under the same key conflicts |
| Fail binding/index commit | No admitted run/address with a missing or mismatched scientific binding |
| Candidate or working point from another sample/batch | Direct and authored paths reject; an explicit estimate copy remains distinct from evidence reuse |
| Target-bearing saved recipe produces a procedure child | Parent and child binding/hash agree; session defaults cannot override it |
| Current-format recovery and unsupported formats | Backup/restore preserves current binding/ref/address; unsupported formats are rejected without rewriting files or inferring target/setup |
| Label-only target revision; changed scientific target content | Old provenance stays exact; the future applicability comparator distinguishes metadata from scientific changes |

These are unpassed acceptance requirements for #626. Keep them as short synthetic
contract/admission journeys during the fast-CI window; installed GUI/Windows and
physical-device qualification remain separate finishing gates.


## Implemented domain foundation

`config/target_projection.py` now owns pure sample-hash and connection-entity
validation, consumed by the existing target catalog's create/revise operations.
Its internal single-member projection preserves the exact qualified target ref,
keeps member IDs separate from run role `subject`, and compares topology without
description/order sensitivity. Contract tests cover foreign catalogs, composite
targets, absent/different topology and label-only target revisions. No preparation,
admission, HTTP execution surface or calibration writer consumes the projection
yet; the execution acceptance requirements above remain open.
