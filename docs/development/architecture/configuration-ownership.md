# Configuration ownership and execution fences

The implemented slices of #645 separate fixed scientific selections from default
changes (#647), publish verified candidates to one exact working point (#648),
and give bounded automatic calibration cohorts that same independent ownership
(#651). Independent maintained setup revisions and explicit rebinding (#653) now
separate execution authority from parameter defaults. Executable apparatus subjects
and qualified cross-object calibration dependencies remain outside this boundary.

## Existing owners

A configuration snapshot still contains executable structure and parameters.
Every admitted run retains that complete immutable snapshot and its exact
scientific binding. Mutable defaults are convenience pointers, not historical
evidence.

Working-point saves already have a separate conflict domain. Each parameter
workspace owns a head in `parameter_workspace_heads`; advancing it compares the
exact base entry inside the write transaction. Saving A does not advance B's head
or activate a global configuration. A named branch establishes a separate head.
Ordinary saves remain parameter version management; only explicit verified
publication records acceptance evidence.

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

Resource admission and direct session acquisition compare the independent setup
activation generation inside the write transaction. A parameter-only default
change cannot invalidate that authority-read/resource-claim fence. An explicit
active-parameter choice still has its separate default-generation freshness check.
Instrument removal/rekey keeps its retirement gate, queued-reservation and live-claim
drain checks, and transactional recheck. Idempotent submissions replay retained
results before stale preview checks.

## Maintained executable setup (#653)

`setup_revisions` holds immutable executable payloads without parameter declarations
or values. `setup_activations` is the current deployment authority; its generation
is separate from `config_registry_activations`, which selects the parameter default.
The setup operation ledger records exact activation intents and results.

A setup revision has an exact payload hash, including descriptive metadata. Its
execution hash uses the scientific projection above. Same executable content can
therefore remain compatible despite a new descriptive revision. Direct instrument
sessions retain the exact selected setup reference. Runs retain their complete
configuration and scientific setup hash, rather than acquiring a rewritten snapshot
when the current setup changes.

Only initial publication into a genuinely empty catalog seeds both owners in one
transaction. Parameter-default activation thereafter requires matching setup content
and never changes setup. Setup activation leaves parameter versions untouched.
Independent setup activation invokes cohort supersession only for incompatible
execution hashes; parameter-default activation does not invoke it.

`rebind_setup` explicitly composes a saved setup with an exact parameter input and
validates the result. A working-point input creates a new independent workspace,
retaining exact sample/batch identity but no publication acceptance. Source cell
origins remain provenance for estimates, not a transfer of calibration applicability.
The read-only preview and save use the same composition contract. No implicit
rebase, migration or background synchronization is provided.

One active setup is the current executable deployment authority. This slice does
not authorize simultaneously incompatible setups or infer physical connectivity.
See [the maintenance guide](../../how-to/maintain-executable-setup.md).

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
context provenance and approval survive current-format backup/restore. Explicit
rebasing changes the proposal and requires new independent evidence.

For recoverable client calls, retain and reuse `operation_id`. The shared config
operation ledger records this operation without inventing an activation generation;
an exact retry returns the original receipt before checking the now-advanced head.
A different operation cannot use an existing destination entry to bypass head CAS.

## Automatic calibration ownership (#651)

An evaluator explicitly selects one working-point workspace. It follows that
workspace's head between cycles, and freezes one exact entry, sample revision,
working point and batch within a cycle. Without that selection it can evaluate
catalog-scoped checks whose successful procedure needs no parameter publication.
It never creates a sample or chooses a writable workspace implicitly.

Logical targets acquire their owner from the planning context before observation,
status lookup and intent construction. Calibration keys include the stable workspace
identity, not a repeated display label or changing head. Two branches with identical
sample/context/batch metadata therefore have independent attempts and successes.
Publication advances the head while retaining the workspace's calibration history.
Shared fan-out limits and instrument resource claims are separate constraints;
independent ownership does not promise simultaneous access to one instrument.

A publishing cohort owns exactly one working point. Its members may calibrate
several logical entities within that scope. Admission checks the exact head,
recorded scope and current executable setup in its transaction. Parent samples
freeze exact revisions, while the baseline and candidate stages may use different
parameter snapshots. The cohort's publication proof requires both stages to match
its recorded scope and the baseline to use its exact context.

The finalizer retains the existing independent-verification and common-base merge
proofs. One transaction saves the context revision, advances its head, retains
approvals and member success records, completes finalization and writes the
idempotent receipt. No global default activation accompanies that publication.

Head changes supersede pending publications only for that workspace. A branch
save creates a different owner. A parameter-only global default change does not
supersede working-point publication; a changed executable setup can. Supersession
records whether the cause was an exact replacement head or changed setup content,
rather than representing both as a global generation. Old admitted requests and
committed publication receipts remain replayable by their exact identities.

Cross-owner and cross-batch dependencies require a separate applicability contract;
this slice rejects them. It neither promotes descriptive apparatus observations to
calibrations nor treats room-temperature measurements as suitable cold parameters.
See [durable automation](automation.md) for proofs, queues and worker behavior.

Further work includes automation requirements, concurrent deployment applicability
and qualified cross-object dependencies. Descriptive apparatus observations never
become executable authority or calibration validity implicitly. See
[apparatus history](apparatus-history.md) for that boundary and the
[prebaseline policy](../data-compatibility.md) for retained data.
