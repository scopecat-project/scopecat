# Command-line interface

The `scopecat` command manages one local lab project. Project lifecycle and configuration commands accept a project directory or a
path to `scopecat.toml`; the current directory is the default.

## Project lifecycle

| Command | Purpose |
| --- | --- |
| `scopecat init [PROJECT]` | Initialize a runnable project without replacing existing files. |
| `scopecat start [PROJECT]` | Start the daemon in the background. |
| `scopecat serve [PROJECT]` | Run the daemon in the foreground. |
| `scopecat status [PROJECT]` | Show recorded process identity and daemon health. |
| `scopecat open [PROJECT]` | Open the running project GUI. |
| `scopecat stop [PROJECT]` | Stop the project's recorded daemon process. |

`start` and `serve` accept `--host`, `--port`, `--static-dir`, and `--api-only`.
Only loopback hosts are accepted. Port `0`, the default, selects an available
port.

`start` waits for readiness without a default hard deadline and prints elapsed
startup time and the latest observed phase. Slow first imports are not treated as
proof of a deadlock. Ctrl+C cancels this launch and cleans up its process tree.
Automation can set an explicit hard budget, for example
`scopecat start PROJECT --api-only --startup-timeout 120`.
This budget concerns startup only; it does not alter device operation limits or
experiment wait timeouts. The Python `start_project` helper similarly accepts
`timeout=None` (default) and an `on_progress(elapsed_seconds, phase)` callback.
This is a synchronous wait; it does not yet provide a durable startup task handle
for disconnecting and resuming from another client.

## Snapshots

| Command | Purpose |
| --- | --- |
| `scopecat snapshot create PROJECT DESTINATION` | Capture a stopped current-format project in a fresh snapshot directory. |
| `scopecat snapshot verify SNAPSHOT` | Verify inventory, SQLite integrity, current schema and immutable objects. |
| `scopecat snapshot restore SNAPSHOT DESTINATION` | Restore a current-format snapshot into a fresh path without starting work. |

See [backup and restore](../how-to/backup-and-restore.md) for the source boundary,
dependency retention and explicit schema-version policy.

## Configuration

| Command | Purpose |
| --- | --- |
| `scopecat config check [PROJECT]` | Validate separate setup and optional parameter-default sources without creating state. |

The former `config diff/apply/export` commands are retired. Select and edit named
parameter branches through `session.params`, manage equipment through the
[setup API](../how-to/maintain-executable-setup.md), and preserve durable data with
[backup and restore](../how-to/backup-and-restore.md). A generated global-default
JSON file is not a complete backup.

`config check` remains read-only and also accepts equipment-only bootstrap.

## Automation

| Command | Purpose |
| --- | --- |
| `scopecat automation work [PROJECT]` | Run the project-owned resident automation worker. |
| `scopecat automation work [PROJECT] --once` | Finalize, plan, evaluate, materialize, and dispatch one bounded cycle, then exit. |

The resident worker loads the project's exact publication, calibration,
schedule, and procedure registries in its own process. It finalizes ready
calibration cohorts before config-sensitive planning, turns latest-only fixed UTC
interval occurrences into ordinary exact one-shot schedules, and executes
compatible procedures; the daemon never executes user-authored closures. A
remaining publication page temporarily blocks interval and calibration planning
but not already-frozen due or runnable work. `--once` prints publication and
procedure counters and exits nonzero for recorded deterministic failures.
`--poll-seconds` controls the idle polling interval.

Use `scopecat COMMAND --help` as the authority for all current options. See the
[configuration how-to](../how-to/manage-configuration.md) for the intended
review workflow.

## Local command diagnostics

`scopecat diagnose --output NEW_DIRECTORY -- COMMAND...` captures an explicit
command with a bounded watchdog and local evidence archive. See
[collecting diagnostics](../how-to/collect-diagnostics.md) for arguments, failure
semantics and workload instrumentation.
