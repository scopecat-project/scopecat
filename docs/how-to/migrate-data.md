# Upgrade a verified copy of retained data

The runtime uses one current storage schema: **72**. The release/package version,
storage schema number, snapshot format (**1**) and migration receipt format (**1**)
are separate identities. Downgrading a package does not downgrade scientific data.

The tested development upgrade edges are **68 → 69 → 70 → 71 → 72**. Schema 68 adds a
workspace-head index in 69; schema 69 adds research associations and run deployment
records in 70; schema 70 adds record collections and stable run addresses in 71;
schema 71 adds the experimental batch catalog and run/batch index in 72.
Old records remain unscoped: no historical batch is inferred.
The new default collection preserves each existing scheduler number and run ID.
None of these edges rewrites old scientific records. Missing historical
bench identities remain unknown, and no calibration, unit or project association
is invented. All other old/new schema versions are refused without migration.
These bounded development edges do **not** designate a released long-term data
compatibility baseline. That designation still requires an explicit release policy
and retained baseline fixtures; no promise covers every development database.

## Prepare one stopped source

Finish the attended session and stop its daemon with the normal project task or
`scopecat stop .`. Keep the original directory, environment/dependency information
and artifacts. The tools do not import or execute project code.

From the source project's directory, inspect the available upgrade:

```console
scopecat migration plan .
```

`.` means the current directory. Check that it is the intended project before
continuing. The plan shows source/target schemas and the exact edges. A running
daemon or SQLite writer must be stopped first.

Choose a **new** destination outside the source workspace, data and deployment
folders. For example, this creates a sibling folder called `upgraded-copy`:

```console
scopecat migration copy . ../upgraded-copy
```

The same command works in PowerShell; quote paths containing spaces. Replace the
destination with a location you chose, not an existing experiment directory. It
must not already exist. The command reports both output paths:

- `upgraded-copy/original`: a verified snapshot of the original schema, database,
  owned arrays/blobs, retained source, receipts and project-local files.
- `upgraded-copy/project`: the upgraded workspace and data, ready for inspection.

The tool acquires stopped-store ownership, uses SQLite's consistent backup
primitive, validates every retained object, migrates only a staging copy, then
checks integrity, foreign keys, hashes of every pre-existing table and all retained
non-database files. It publishes the destination directory only after success.
Failure or interruption leaves the original usable and no partially upgraded
public destination. Temporary staging files after a hard process/host crash are
not a completed output; retain the original and retry into a new destination.

## Inspect, then explicitly start

Open the reported upgraded workspace in your editor and use its normal start/open
tasks, or run `scopecat start ../upgraded-copy/project` and
`scopecat open ../upgraded-copy/project`. Migration itself never starts the daemon,
replays historical programs or dispatches acquisition.

The upgraded workspace retains the original bench deployment location and
ownership lock. It cannot run concurrently with the old workspace on that bench.
The data copy retains its data-space identity; treat original and upgraded copies
as alternative branches, not independently writable replicas to merge later.

Check a known run's original measurements, exact parameters/source and saved
analysis. A new analysis should be a separate publication. Unknown historical
metadata must stay unknown. Recorded package versions are inventory, not restored
environments: install compatible dependencies and external SDKs separately before
running code. Strict historical reexecution is outside this migration contract.

## Restore without pretending it is a downgrade

Use `scopecat snapshot verify ../upgraded-copy/original`, then restore to a clean
location with `scopecat snapshot restore ../upgraded-copy/original ../restored-copy`.
The restored snapshot retains its original schema. Read it with its pinned reader,
or run the same tested copy-upgrade from that restored workspace. Use an explicit
runtime binding to the intended bench before any physical use; a portable snapshot
does not silently adopt the machine's physical deployment.

Returning to the old package plus its **old restored snapshot** is software
rollback. It does not carry measurements or analyses written to the upgraded copy
back into an older schema. No automatic bidirectional migration is provided.
See [backup and restore](backup-and-restore.md) for external-file boundaries.
