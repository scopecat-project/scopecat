# Back up and restore a stopped project

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
the supported schema version, and object content hashes. Missing or corrupt
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

## Schema changes and upgrades

There is no implicit schema migration. A reader rejects every schema version
other than its own and reports both versions where available. Schema rejection
occurs before changing the original database or its SQLite sidecars, including
when an old project's last commits remain in a WAL. Snapshot verification and
restore likewise require a supported schema; use the retained matching reader
for an old snapshot.

Choose one supported path:

1. Keep the exact old reader, its environment/package artifacts and snapshot
   available to inspect the original data.
2. If a tested migration for the specific version pair becomes available, apply
   it to a separate restored copy and validate that copy before adopting it.
3. Start a new project while retaining the old snapshot and reader for historical
   measurements and analysis.

This feature supplies no general migration engine. Do not delete the old database
to make a newer reader start. Test a restore while the matching reader and
external dependencies are still available.

## Layered figure store version

Layered analysis figures use project store version 63. A version 62 project or
snapshot is rejected by this runtime without being modified; snapshot restore
does not upgrade it. Keep its pinned version 62 runtime to read or export the
retained scientific data, and preserve the original project and verified
snapshot. Create a separate version 63 project for new publications. No automatic
62-to-63 migration is supplied.
