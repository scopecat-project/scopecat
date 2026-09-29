# Application runtime ownership

One installation home selects one application runtime. The native window loads
that runtime's workbench directly; there is no manager HTTP server, service
catalog, preferred laboratory, nested workbench frame or lifecycle operation queue.

The canonical model is [one application with independent contexts](public-application.md).
Devices, drivers, source identities, setup and parameters remain independent
concepts. Registered author folders share the application's device/data authority.

## Selected software

`ApplicationRuntime` owns a fixed `HOME/runtime` composition and an
`installation.json` selection. Candidate deliveries live at content-addressed,
retained release paths. A candidate probe checks registered sources in their selected
execution interpreters and obtains driver metadata in an isolated worker without connecting
devices. Optional SDK imports belong to connection, not catalog discovery.

Selection requires the application/deployment/data locks and a stopped owner.
The candidate is requalified, a pending marker is persisted, the composition is
updated, and then selection commits. Author interpreters remain selected independently. An interrupted
switch blocks startup and is completed by retrying the same candidate. It does
not create a new data identity or rewrite scientific data.

The stable launcher reads the same selection, including after an in-window update.
The native shell reopens to adopt changed installed software. User-owned `.venv`
clients are updated separately and are never synchronized by application updates.
There is no fallback to a previous driver identity after a qualification failure.

## Runtime and window lifecycle

The existing daemon lifecycle owns PID/creation-time verification, data locks,
startup readiness, graceful shutdown and valid stale-record reconciliation.
The application does not create a second process-state database.
Stop does not import optional adapters and remains usable after interpreter changes.

A per-home desktop lock and activation signal reuse the window. Native bridge
operations act on that home only. Preparation blocks ordinary window exit until
it completes. Closing otherwise offers explicit stop, background retention or
cancel; it does not own or kill VS Code kernels.

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
