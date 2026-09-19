# Configuration ownership and execution fences

Two slices of #645 separate fixed scientific selections from unrelated default
changes (#647), and publish verified candidates into one exact working point
(#648). They do not introduce independently maintained setup revisions or complete
object-scoped calibration automation.

## Existing owners

A configuration snapshot still contains executable structure and parameters.
Every admitted run retains that complete immutable snapshot and its exact
scientific binding. Mutable defaults are convenience pointers, not historical
evidence.

Working-point saves already have a separate conflict domain. Each parameter
workspace owns a head in `parameter_workspace_heads`; advancing it compares the
exact base entry inside the write transaction. Saving A does not advance B's head
or activate a global configuration. A named branch establishes a separate head.
This is parameter version management, not verified calibration publication.

## Selection freshness and executable authority

| Choice | Preview/admission fence | Retained evidence |
|---|---|---|
| Active default | Exact observed activation generation | Selected entry, historical activation and scientific binding |
| Fixed saved configuration | Executable setup content | Exact saved entry and scientific binding |
| Fixed working-point version | Executable setup content | Exact context, sample/workpoint/batch and overrides |
| Analysis candidate | Executable setup content | Exact proposal, source run and scientific binding |

Procedure admission uses a typed choice of activation-generation or setup-content
fence. For fixed choices, changing only default parameter values does not invalidate
the preview. Relevant executable structure changes still reject admission. The
child run also checks its exact setup against current authority; a parent accepted
earlier cannot authorize a later child against a different setup.

`setup_content_hash` includes topology, routing, instrument driver/connection and
lifecycle declarations, and domain configuration. It excludes descriptive labels,
entity metadata, role descriptions, parameter declarations and parameter values.
This is a conservative software execution identity, not proof of physical wiring.
Logical IDs still matter because plans address them. It currently covers the
whole setup, rather than calculating a minimal dependency set for each experiment.

Content equality permits an A → B → A declaration change to return to the same
setup identity. This fence does not assert that hardware was untouched in between.
Exact request/source/code hashes and resource-scoped manual mutation cursors still
protect reviewed launches. Resource ownership, actor state and unknown-effect
quarantine remain separate runtime responsibilities.

The internal activation-generation compare used when claiming devices remains
inside the admission transaction. Even a parameter-only activation racing with
authority resolution can therefore cause that individual admission to retry or
fail. This deliberately conservative race check closes the authority-read to
resource-claim gap; it is distinct from making every outstanding fixed preview
stale. Instrument inventory migration and direct session acquisition retain their
existing protection. Idempotent submissions replay retained results before stale
preview checks so retries do not create duplicate execution.

## Verified publication into one working point (#648)

The explicit `publish_to(working_point=version, name=...)` operation reuses the
existing workspace head and returns a reusable `ParameterVersion`. The separate
`publish_default()` path still changes the shared default with global acceptance
fences. Publication to a working point follows these rules:

1. Select an exact destination working-point version and verified candidate.
2. Require that destination to remain the workspace head, and require the
   candidate's complete base configuration hash to match it.
3. Check exact source sample revision, working point and batch applicability.
   Equal parameter values alone do not establish applicability.
4. Atomically retain the acceptance/verification evidence, immutable new context,
   new head and idempotent operation receipt. Do not fabricate a global activation.

A and B publish independently; two competing updates to A conflict. A failed
or stale publication leaves neither an approval nor a new head. The receipt,
context provenance and approval survive current-format backup/restore. Explicit rebasing changes the proposal and requires new independent evidence.

For recoverable client calls, retain and reuse `operation_id`. The shared config
operation ledger records this operation without inventing an activation generation;
an exact retry returns the original receipt before checking the now-advanced head.
A different operation cannot use an existing destination entry to bypass head CAS.

The current calibration cohort planner still resolves `active`, and its cohort
merge/finalization contracts carry a global base generation. They need a separate
coordinated change before automated publication can claim independent A/B
ownership; the working-point publication API alone does not complete that change.

Further work includes maintained setup revisions, automation requirements and
qualified cross-object dependencies. Descriptive apparatus observations never
become executable authority or calibration validity implicitly. See
[apparatus history](apparatus-history.md) for that boundary and the
[prebaseline policy](../data-compatibility.md) for retained data.
