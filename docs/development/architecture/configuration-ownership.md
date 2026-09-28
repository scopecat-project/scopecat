# Configuration ownership and execution fences

Device maintenance, experiment setup and parameter editing have separate owners.
A resolved configuration is immutable execution evidence, not another editable
device inventory. See [device management](device-management.md) for the full
application contract.

## Maintained owners

| Owner | Mutable choice | Retained evidence |
| --- | --- | --- |
| Device | Label, availability and connection head | Exact connection revision, installed driver artifact and connection-test result |
| Setup definition | A newly saved named definition | Logical aliases, device references, routing, topology and experiment policies |
| Setup resolution | Explicit recheck against device heads | Definition hash and exact connection/driver revisions |
| Parameter branch | Compare-and-set branch head | Immutable declarations, values, provenance and publication receipts |
| Scientific selection | Per-page or per-session choice | Exact subject, parameter and setup inputs frozen at preview |
| Run | Admission and execution state | Complete configuration, scientific binding, source and operation evidence |

Device names are presentation data. Renaming one does not invalidate preparation.
Connection changes create a new immutable revision. A setup references stable
device IDs and owns experiment policies; it cannot weaken device safety policy.
Resolution composes those owners without copying connection settings into an
editable setup.

Parameter revisions can be saved without equipment or a physical sample. Branch
edits advance only the selected branch. Changing its head does not rewrite prior
previews or runs. An ordinary save records values, not scientific acceptance.

## Preparation and admission

Independent parameter launches select an explicit resolved setup. They do not
consult a global active setup when a choice is missing. The workbench requests
that choice inline; Python sessions retain it through
`session.use(parameter_branch=..., setup=...)`.

A resolved setup contains exact device revisions and installed driver identities.
Preparation checks them; admission checks their current heads again inside the
same transaction that creates resource reservations. A relevant device change
requires re-resolution and another preview. An unrelated setup selection has no
effect on these checks.

Direct registered-device sessions use a resolved device-access snapshot and the
same instrument session lease, claims, resident actor and attention mechanisms.
This snapshot is not listed among experiment setup definitions. Connection tests
use that path too; there is no unregistered temporary-binding bypass.

Device maintenance reserves the affected resource using an ordinary session.
Queued work, live ownership and quarantine block it. Idle connections must retire
successfully before a new head commits. Head replacement and maintenance-session
closure occur in one transaction; an expired lease cannot authorize the commit.
Failed connection retirement retains the old head and an attention session across
restart.

## Parameter editing and publication

The maintained editor is the independent parameter branch editor. The previous
working-point workspace, setup-rebinding editor and global-default publication
HTTP/Python commands have been removed. Their former UI editors and proposal
“Accept as default” action are also removed.

A verified candidate publishes to a reviewed parameter branch with
`publish_to_branch(...)`. Publication checks the exact destination head,
candidate base and verification evidence, then records the new immutable
revision, head and receipt together. A stale destination cannot leave a partial
publication. An exact retry returns the retained receipt.

The run results page shows proposal changes and evidence. Candidates can be
reopened in VS Code for an explicit trial; they are not promoted merely because
a fit or connection test succeeded. Device identity and matching parameter values
do not establish calibration applicability.

## Remaining combined-configuration boundary

Candidate execution inherits the baseline run's exact persisted setup reference;
calibration checks retain a separate execution setup alongside their scientific
context. Neither follows the global setup selection. The low-level combined
configuration paths still use setup-content authority, and startup bootstrap
still maintains an active setup.
Internal registry publication is still used by bootstrap and scientific-proof
tests. These are not a second supported user editing workflow. Their remaining
selection dependencies must be removed before declaring ownership cleanup
complete; deleting the user-facing writer alone does not establish that result.

Keep exact scientific subject checks, candidate verification and shared resource
ownership while replacing those consumers. Do not turn current heads into
historical evidence or use content equality as proof that hardware was untouched.

## Persistence

Development schema 100 retains device registrations, immutable connection
revisions, declared access aliases, connection-test evidence, setup definitions
and exact resolutions with the existing parameter and run records. Current-format
backup/restore covers these owners together.

No supported persistent-data baseline is designated. This refactor adds no
reader, migration or old-codec fallback for retired development formats and does
not delete historical user files. See [data compatibility](../data-compatibility.md).
