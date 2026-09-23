# Back up and restore a stopped project

This workflow supports the current development format only: schema **91** and
snapshot format **1**. It is not an upgrade path or compatibility baseline; see the
[data policy](../development/data-compatibility.md).

A snapshot retains the SQLite database, immutable measurement and analysis
objects, application/configuration source, and installed Python/package versions.
It is a directory that you can copy to another disk or archive using your normal
backup tools. Copy the complete snapshot, including `manifest.json` and `project/`.

Stop the daemon and any separately launched procedure or automation workers, and
finish editing the project source before capture:

```sh
scopecat stop my-lab
scopecat snapshot create my-lab backups/my-lab-2026-09-08
scopecat snapshot verify backups/my-lab-2026-09-08
scopecat snapshot restore backups/my-lab-2026-09-08 recovered-lab
```

The snapshot's parent directory must exist. Creation requires a new destination
outside the source project; restore requires a new project path and never
replaces an existing project. Creation holds the daemon's process lock and a
SQLite writer reservation for the entire capture. A running daemon or active
SQLite writer is rejected. This is a stopped-project workflow, not online backup;
it does not coordinate concurrent source-file editors or third-party writers
that bypass Scopecat's ownership contract.

## What is retained

- `.scopecat/control.sqlite3`, including committed data in a retained SQLite WAL,
  captured as one standalone database.
- All immutable objects, plus verification of every database object reference.
- Project-local source and data files, including `scopecat.toml`, `src/`, `config/`,
  notebooks, dependency declarations, and lockfiles.
- A manifest containing the snapshot format version, schema version, capture
  time, Python version, installed distribution names/versions, and SHA-256 file
  inventory. Restore also saves this manifest as `.scopecat/restore-manifest.json`.

Runtime endpoint/PID records, locks, logs, unpublished temporary objects, and GUI
procedure-dispatch state are excluded. Git internals, `.venv`, `venv`,
`node_modules`, `__pycache__`, `.pytest_cache`, and `.ruff_cache` are excluded at
all source-directory depths. Symbolic links are rejected rather than following
files outside the captured project. Files elsewhere—including separately
installed SDKs, editable packages, driver installations and hardware settings—are
not archived. Keep the original package artifacts and any required external SDK
installers/source separately. Package names and versions alone do not reproduce
an editable checkout or an unavailable proprietary dependency.

The commands do not import or execute project application/configuration code.
Verification checks file inventory/checksums, SQLite integrity and foreign keys,
the current schema version, and object content hashes. Missing or corrupt
referenced content fails capture or verification. Checksums detect damage; they
do not authenticate an unknown snapshot publisher. Treat restored application
source as executable code when choosing which snapshots to run.

## Starting a restored project

Recreate an environment using the recorded Python/package versions and retained
artifacts, install external dependencies, and inspect the restored configuration
for the target machine before explicitly starting the daemon. Snapshot restore
does not install packages, launch the daemon, import the application, or acquire
hardware.

Durable procedure records—including ready and unfinished work—are preserved.
The old GUI's management file is omitted, so starting the restored GUI does not
automatically resume those procedures. Dispatch selected procedures explicitly
when ready. Explicitly starting a resident automation worker can dispatch its
eligible work as usual. Existing recovery rules for interrupted runs and leases
still apply when the daemon starts; restore does not rewrite their history.

## Development format changes

The runtime and snapshot tools reject formats other than the current one. Rejection
does not upgrade, delete or rewrite the original database or its SQLite sidecars.
Schema 76 is a development format marker, not a promise that later releases can
read it. The former schema 68–74 migration exercises and migration CLI are retired.

Keep old stores and their original environments separately if you want an archival
reading arrangement. New Scopecat builds supply no supported read, restore or
migration path for those prebaseline formats. Start new development state in a
fresh location; do not delete the old database to make a new runtime start.

Within the current format, snapshots retain author source bundles, workspace-scoped
publication state, preparation receipts, author-job receipts and experiment plans.
Pending preparations become interrupted after restart or restore; they are not
resubmitted against today's files. External Python environments and device SDKs
still need their separately retained artifacts.

## Separately located data

A workspace can select local storage and one execution deployment with
`scopecat.runtime.toml` (see [project layout](../reference/project-layout.md)).
Snapshot locks and reads that data root, includes author-job receipts and restores
into a fresh colocated `.scopecat/` directory. It does not copy the local runtime
binding, endpoint, deployment ownership or automatic procedure dispatch intent.
The data-space identity and scientific references survive restoration. Stop the
original service before adopting a recovery copy; portable merging of independent
writable copies is not implemented.
