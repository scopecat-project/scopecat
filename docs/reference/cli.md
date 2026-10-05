# Command-line interface

The installed `scopecat` command is provided by `scopecat-lab-tools`, which
composes project/server commands with application, Notebook and practice entry.
For server-only development, use `python -m scopecat_server.cli`; that module
has no application/teaching commands or `init --topic` option.

Ordinary users start in the desktop application. These commands support optional
maintenance, automation and retained development/teaching callers; project lifecycle
commands are not a requirement to open data, use Help or author against the app.
See the [entry map](../development/architecture/public-application.md#user-journeys-and-entry-ownership).

## Entry ownership and migration

| Installation | Entry | Commands |
| --- | --- | --- |
| Complete application / `scopecat-lab-tools` | `scopecat` or `python -m lab_tools.public_cli` | Project commands below plus `app`, `notebook`, `teach`, `init --topic` |
| Server-only / retained server pilot | `python -m scopecat_server.cli` | Project commands below, without application/teaching commands |
| Existing teaching tools | `scopecat-lab` | Existing generated-project / VS Code lifecycle; unchanged |

The server wheel no longer owns the `scopecat` console script. Server-only scripts
and users replace `scopecat COMMAND` with `python -m scopecat_server.cli COMMAND`
using their installed environment's interpreter. Application/teaching callers
that previously used the server module instead use `scopecat` or the application
module. There is one console-script owner, with no optional provider discovery.
Generated server projects print the module commands, which also work in a complete
application environment. This does not change project files, data or runtime
ownership; no migration of retained stores is implied.

Project lifecycle and configuration commands accept a project directory or a
path to `scopecat.toml`; the current directory is the default. In the following
tables, server-only installations substitute the module entry for `scopecat`.

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
| `scopecat automation work [PROJECT] --once` | Plan intervals, materialize due schedules and dispatch runnable procedures for one bounded cycle, then exit. |

The resident worker uses the project's procedure registry and interval planner in
its own process. It plans latest-only fixed UTC intervals, materializes due one-shot
schedules and dispatches compatible runnable procedures. The daemon does not
execute user-authored closures. `--once` reports interval, schedule and procedure
outcomes, including failures, drift and conflicts, and exits nonzero when the cycle
needs review. `--poll-seconds` controls the idle polling interval. This command does
not perform the retired calibration-cohort/publication planning cycle.

Use `scopecat COMMAND --help` as the authority for all current options. See the
[configuration how-to](../how-to/manage-configuration.md) for the intended
review workflow.

## Local command diagnostics

`scopecat diagnose --output NEW_DIRECTORY -- COMMAND...` captures an explicit
command with a bounded watchdog and local evidence archive. See
[collecting diagnostics](../how-to/collect-diagnostics.md) for arguments, failure
semantics and workload instrumentation.
