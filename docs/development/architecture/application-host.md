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

`ApplicationRuntime` owns a fixed `HOME/runtime` composition and an
`installation.json` runtime registration. Native startup uses the Python and GUI
inside the currently opened package. It does not install dependencies, retain
application releases or select another interpreter from the data directory.
Qualification obtains driver metadata without connecting devices. Unavailable
author folders do not block an application update. Optional SDK imports belong
to connection, not catalog discovery.

Registration requires the application/deployment/data locks and a stopped owner.
The package is requalified, a pending marker fences startup while the composition
and runtime record are written, and the marker is removed after completion.
A verified current package can complete an interrupted registration even if the
previous package is no longer installed. This does not rewrite scientific data.
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

Author-only manifests inherit the selected application composition. Captured source
pins even an empty capability declaration, so archived code does not consult today's
registration. Source refresh and runtime replacement are separate operations.

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
still belong to the fixed application delivery.

Help practice runs in the same service and uses ordinary data cleanup.

Separate application homes remain independent physical-device authorities.
LAN authorization, unattended OS services and cross-home hardware exclusion are
not implied by the local desktop runtime.
