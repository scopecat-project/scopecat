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

An empty scientific selection is explicitly unselected. Preview rejects it with
instructions to select parameters and setup; it never resolves a shared parameter
default. Clearing the selection does not activate another configuration.
Changing the subject in a Python session retains independent parameters, setup
and unsaved branch edits, while clearing the previous subject's batch.
The launch page does not poll the global configuration registry to decide whether
a checked draft or an exact submission retry is usable. Admission validates the
retained references; unrelated global state cannot block the client-side retry.

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

The internal `ConfigService` global publisher, activation, draft preview and
operation lookup methods are retired too. Current runtime wiring no longer
constructs their operation store. Branch tests retain exact verification,
atomic revision/head/receipt rollback, replay and current-format restore.
The device-session lost-response test now changes driver availability directly,
without manufacturing an unrelated global configuration activation.

Global publication, activation and manual-draft APIs, wire commands, operation
receipts, activation history and their SQLite tables are retired. The combined
working-point writer/head, setup rebind and `parameters.bind(...)` intermediate
save are retired too. Independent preparation retains exact parameter/setup
inputs directly. No registry selector resolves a global default; named retained
snapshots are read by exact identity.

Parameter branch tests retain concurrent checkout, immutable selections, stale
save rejection, atomic revision/head/receipt rollback, replay and current-format
restore. Structure editing, unknown cells and source-address mapping have focused
branch/core coverage. The removed tests asserted global-default activation or
combined workspace writes, not an additional supported author contract.

Immutable registry entries remain retained execution/provenance data. Evidence
fixtures save entries without activation; export still rejects missing decision
evidence or changed schema identity. The source records and exact reads used by
those retained snapshots do not confer editing authority or a compatibility
promise for earlier stores. No HTTP/Python combined-snapshot writer remains.

A verified candidate publishes to a reviewed parameter branch with
`publish_to_branch(...)`. Publication checks the exact destination head,
candidate base and verification evidence, then records the new immutable
revision, head and receipt together. A stale destination cannot leave a partial
publication. An exact retry returns the retained receipt.

The run results page shows proposal changes and evidence. Candidates can be
reopened in VS Code for an explicit trial; they are not promoted merely because
a fit or connection test succeeded. Device identity and matching parameter values
do not establish calibration applicability.

## Execution and retained configuration data

Every admitted run supplies an exact execution setup, independently of parameter
and scientific provenance. Candidate runs retain their baseline's setup; calibration
checks retain a separate setup alongside the scientific context. Scientific
procedures use exact setup fences. No path falls back to a global setup selection.

Combined snapshots remain data and provenance. They grant no device access and
are not another user editing workflow. Author selections and saved plans use
independent parameters or a retained candidate. Startup atomically seeds a named
`initial` setup and optional parameter revision once, without selecting either.
The seed marker is initialization bookkeeping, never execution authority.

Exact subject checks, candidate verification and shared resource ownership remain
mandatory. Current heads are not historical evidence; content equality does not
prove that hardware was untouched.

## Persistence

Development schema 107 removes configuration activation history and combined
workspace heads, following schema 106 removal of operation receipts.
It retains device registrations, immutable connection
revisions, declared access aliases, connection-test evidence, setup definitions
and exact resolutions with the existing parameter and run records. Current-format
backup/restore covers these owners together.

No supported persistent-data baseline is designated. This refactor adds no
reader, migration or old-codec fallback for retired development formats and does
not delete historical user files. See [data compatibility](../data-compatibility.md).
