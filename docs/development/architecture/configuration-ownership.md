# Configuration ownership and execution fences

The first executable slice of #645 separates fixed scientific selections from
unrelated changes to the lab default (#647). It does not introduce independently
maintained setup revisions or complete object-scoped calibration automation.

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

## Next: verified publication into one working point

The existing `publish_default()` path explicitly changes the shared default and
still uses global acceptance fences. It must not be described as independent
object-scoped publication. The next slice should reuse the existing workspace head:

1. Select an exact destination working-point version and verified candidate.
2. Require that destination to remain the workspace head, and require the
   candidate's complete base configuration hash to match it.
3. Check exact source sample revision, working point and batch applicability.
   Equal parameter values alone do not establish applicability.
4. Atomically retain the acceptance/verification evidence, immutable new context,
   new head and idempotent operation receipt. Do not fabricate a global activation.

A and B should then publish independently; two competing updates to A must
conflict. A failed or stale publication must leave neither an approval nor a new
head. Explicit rebasing changes the proposal and requires new independent evidence.

Further work includes maintained setup revisions, automation requirements and
qualified cross-object dependencies. Descriptive apparatus observations never
become executable authority or calibration validity implicitly. See
[apparatus history](apparatus-history.md) for that boundary and the
[prebaseline policy](../data-compatibility.md) for retained data.
