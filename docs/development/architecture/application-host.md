# Application runtime ownership

This describes the current prototype implementation. The
[desktop product gate](desktop-product.md) separates application, windows, data
access and execution; it does not require this process or HTTP topology to remain.

One installed package provides one application runtime. The native window loads
that runtime's workbench directly; there is no manager HTTP server, service
catalog, preferred laboratory, nested workbench frame or lifecycle operation queue.

The canonical model is [one application with independent contexts](public-application.md).
Devices, drivers, source identities, setup and parameters remain independent
concepts. Registered author folders share the application's device/data authority.

## Packaged runtime

`ApplicationRuntime` opens an explicit `ApplicationInstance`, independently of
an author `scopecat.toml`. Native startup uses the Python and GUI inside the opened
package. Qualification checks that installation without loading author code,
probing author environments, or publishing scientific parameters and setups.
Registered sources supply their own capabilities and optional laboratory adapters.
Unavailable author folders do not prevent browsing data, stopping or replacing
the application.

New homes declare their layout in `scopecat.application.json`. Scientific data and
original submission receipts live in `data/`; `state/` retains deployment identity,
author registrations and installation selection alongside endpoint/worker
coordination. Materialized source copies live in `cache/`; retained execution
environments stay in `environments/`. See [installation layout](../installation-layout.md)
for the ownership and retention rules.

Recognized previous desktop homes keep `runtime/.scopecat` and their original
owner root. The application does not relocate their files or need the synthetic
empty source manifest to start. A custom old composition requires maintainer
attention rather than silently discarding its behavior. Current-format checks and
the explicit backup/reset flow use the selected layout's actual store.

Registration requires the application/deployment/data locks and a stopped owner.
The package is requalified; a pending marker fences startup until the installation
record commits. A verified current package can complete an interrupted registration
even if the previous package is unavailable. This does not rewrite scientific data.
Author interpreters remain independent.

Users quit, replace the native application and reopen it. User-owned `.venv`
clients are updated separately and are never synchronized by application updates.
There is no fallback to a previous driver identity after a qualification failure.

## Runtime and window lifecycle

The existing daemon lifecycle owns PID/creation-time verification, data locks,
startup readiness, graceful shutdown and valid stale-record reconciliation.
The application does not create a second process-state database.
Stop does not import optional adapters and remains usable after interpreter changes.

A per-home desktop lock and activation signal reuse the window. Native bridge
operations act on that home only. Preparation blocks ordinary window exit until
it completes. Window close hides the window on both macOS and Windows, preserving
experiments and the tray/menu-bar host. Explicit application Quit checks
unfinished work. An idle application exits;
active work offers stop, background retention, wait until idle or cancel. Quit
requests show progress; a failed operation leaves controls available for recovery.
Cancelling automatic quit keeps the dialog open until cancellation succeeds.
The host does not own or kill VS Code kernels.

CLI status, installation, source registration and start are headless. Only explicit
open launches a browser. Tests use temporary homes and serial real-process checks.

## Source and capability composition

Author manifests own their source declarations. Captured source pins even an
empty capability declaration. Revision workers retain the captured source root
and receive a separate execution context with the registered workspace identity,
client binding and exact revision. They do not resolve deployment paths from the
captured directory or silently substitute today's source. Source refresh and
runtime replacement are separate operations.

Capabilities can register domain-system builders by target kind using
`[lab.capabilities.domain_systems]`. No-domain tasks use the ordinary computation
system; a declared target kind selects its explicit builder. Unknown target kinds
fail with a missing-capability error rather than selecting the first provider.
A custom whole-application `experiment_system` and domain dispatch are mutually
exclusive.

## Remaining boundary

Author folders own mutable client environments. Managed execution environments
retain dependencies for prepared work and plans; source refresh selects a revision
without introducing another service. Adding dependencies creates a qualified
environment instead of mutating the application. Version checks do not establish
compatibility across arbitrary framework protocol changes. Driver dependencies
belong to the explicitly selected execution environment; vendor SDKs can use an
independent process/environment. Qualification and idle driver replacement retain
application-wide device ownership. They do not require modifying desktop Python;
see [installed laboratory adapters](public-application.md#installed-laboratory-adapters).

Help practice runs in the same service and uses ordinary data cleanup.

Separate application homes remain independent physical-device authorities.
LAN authorization, unattended OS services and cross-home hardware exclusion are
not implied by the local desktop runtime.
